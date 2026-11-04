"""Daily pipeline: local inbox / OneDrive -> APIs -> calculation -> publication -> report.

    python -m bri.pipeline                         # scheduled run (window: recent days)
    python -m bri.pipeline --from 2025-03-01       # backfill a period
    python -m bri.pipeline --skip-api --skip-onedrive   # only recompute what changed

Only one pipeline runs at a time (PostgreSQL advisory lock, valid across containers).
Each step is independent: a failing source is reported and the others still run.
"""

import argparse
import datetime as dt
import sys
import traceback
from pathlib import Path

import pandas as pd

from bri import calc, db, local_inbox, onedrive, readiness, settings
from bri.importers import entsoe, okte
from bri.timeutil import days as day_list, yesterday_local

EVAL_ORDER = ("final", "monthly", "daily")
EVAL_RANK = {"daily": 1, "monthly": 2, "final": 3}


def log(msg):
    print("[{}] {}".format(dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"), msg), flush=True)


def _status_days(conn, market, component, statuses, days):
    with conn.cursor() as cur:
        cur.execute("""SELECT d.delivery_day FROM market_day d JOIN market m USING (market_id)
                       WHERE m.code = %s AND d.component = %s AND d.status = ANY(%s)
                         AND d.delivery_day = ANY(%s) ORDER BY 1""",
                    (market, component, list(statuses), list(days)))
        out = [r[0] for r in cur.fetchall()]
    conn.commit()
    return out


def fetch_okte(conn, days, recent, messages):
    """DA / IDM15 / IDM60 for days that are not complete or are recent; IMB best evaluation."""
    touched = set()
    for market in ("DA", "IDM15", "IDM60"):
        complete = set(_status_days(conn, market, "prices", ("complete",), days))
        for day in days:
            if day in complete and day not in recent:
                continue
            try:
                res = okte.import_da(conn, day) if market == "DA" else \
                    okte.import_idm(conn, day, 15 if market == "IDM15" else 60)
                if res and res["rows"]:
                    touched.add(day)
            except Exception as exc:
                messages.append("OKTE {} {}: {}".format(market, day, exc))
    lookback_from = yesterday_local() - dt.timedelta(days=settings.IMB_FINAL_LOOKBACK_DAYS)
    for day in days:
        best = okte.best_imb_evaluation(conn, day)
        if best == EVAL_RANK["final"]:
            continue
        if best is not None and day < lookback_from:
            continue  # old day with a non-final value: checked by an explicit backfill only
        for evaluation in EVAL_ORDER:
            if best is not None and EVAL_RANK[evaluation] <= best:
                break
            try:
                res = okte.import_imb(conn, day, evaluation)
            except Exception as exc:
                messages.append("OKTE IMB {} {}: {}".format(evaluation, day, exc))
                break
            if res:
                if res["rows"]:
                    touched.add(day)
                break
    return touched


def fetch_entsoe(conn, days, recent, messages):
    need = set(_status_days(conn, "AFRR", "capacity", ("missing", "incomplete", "invalid"), days)) | set(recent)
    known = set(_status_days(conn, "AFRR", "capacity", ("complete", "missing", "incomplete", "invalid"), days))
    need |= set(days) - known
    if not need:
        return set()
    if not settings.ENTSOE_API_KEY:
        messages.append("ENTSO-E: ENTSOE_API_KEY nie je nastavený, aFRR kapacita sa nesťahuje")
        return set()
    try:
        entsoe.import_range(conn, min(need), max(need))
        return need
    except Exception as exc:
        messages.append("ENTSO-E aFRR kapacita {}..{}: {}".format(min(need), max(need), exc))
        return set()


def status_text(conn, run_started, messages, onedrive_ok):
    lines = ["Stav Battery Revenue Index SK",
             "Posledný pokus o aktualizáciu: {}".format(run_started.strftime("%d. %m. %Y %H:%M"))]
    with conn.cursor() as cur:
        cur.execute("SELECT max(published_at) FROM result_publication")
        last = cur.fetchone()[0]
        lines.append("Posledné zverejnené výsledky: {}".format(
            pd.Timestamp(last).tz_convert(settings.TZ).strftime("%d. %m. %Y %H:%M") if last else "-"))
        cur.execute("""SELECT d.delivery_day FROM market_day d JOIN market m USING (market_id)
                       WHERE m.code = 'AFRR' AND d.component = 'energy' AND d.status <> 'complete'
                         AND d.delivery_day >= %s ORDER BY 1""", (settings.FIRST_DAY,))
        missing = [r[0] for r in cur.fetchall()]
        cur.execute("SELECT count(*) FROM inbox_file WHERE state = 'archive_pending'")
        pending = cur.fetchone()[0]
    conn.commit()
    lines.append("")
    if missing:
        lines.append("TREBA NAHRAŤ (aFRR energia, SEPS/Damas 'Regulačná elektrina (denná)', XLSX do 1_Nahrať):")
        lines.append("  " + _ranges(missing))
    else:
        lines.append("aFRR energia: nič nechýba.")
    if pending:
        lines.append("Súbory čakajúce na presun do 2_Spracované: {}".format(pending))
    if not onedrive_ok:
        lines.append("OneDrive: posledný beh sa k priečinku nepripojil (pozri hlásenia).")
    if messages:
        lines.append("")
        lines.append("Hlásenia posledného behu:")
        lines += ["  - " + m for m in messages[-40:]]
    return "\n".join(lines) + "\n"


def _ranges(days):
    out, start, prev = [], None, None
    for d in sorted(days):
        if prev and d == prev + dt.timedelta(days=1):
            prev = d
            continue
        if start:
            out.append(str(start) if start == prev else "{} – {}".format(start, prev))
        start = prev = d
    if start:
        out.append(str(start) if start == prev else "{} – {}".format(start, prev))
    return ", ".join(out)


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--from", dest="date_from")
    p.add_argument("--to", dest="date_to")
    p.add_argument("--trigger", default="manual", choices=["cron", "manual", "backfill", "test"])
    p.add_argument("--skip-onedrive", action="store_true")
    p.add_argument("--skip-api", action="store_true")
    p.add_argument("--force-calc", action="store_true", help="recompute even unchanged days")
    p.add_argument("--source", choices=("local", "onedrive", "none"), default=settings.FILE_SOURCE,
                   help="file transport (APIs are controlled separately by --skip-api)")
    p.add_argument("--inbox", help="local inbox directory (default BRI_LOCAL_INBOX)")
    p.add_argument("--report-dir", help="persistent report directory (default BRI_REPORT_DIR)")
    cleanup = p.add_mutually_exclusive_group()
    cleanup.add_argument("--delete-after-success", dest="delete_after_success", action="store_true",
                         help="remove claimed local inputs/raw copies after verified publication and report")
    cleanup.add_argument("--keep-files", dest="delete_after_success", action="store_false")
    p.set_defaults(delete_after_success=settings.DELETE_AFTER_SUCCESS)
    args = p.parse_args(argv)
    if args.inbox:
        settings.LOCAL_INBOX = Path(args.inbox)
    if args.report_dir:
        settings.REPORT_DIR = Path(args.report_dir)

    started = pd.Timestamp.now(tz=settings.TZ)
    conn = db.connect()
    applied = db.migrate(conn)
    if applied:
        log("schema migrations applied: {}".format(", ".join(applied)))
    calc.seed_configs(conn)
    if not db.try_pipeline_lock(conn):
        log("another pipeline is running - exit")
        conn.close()
        return 2
    messages, status, local_run = [], "success", None
    with conn.cursor() as cur:
        cur.execute("INSERT INTO pipeline_run (trigger, status) VALUES (%s, 'running') RETURNING pipeline_run_id",
                    (args.trigger,))
        prun = cur.fetchone()[0]
    conn.commit()
    try:
        from bri import artifacts
        artifacts.mark_stale_batches(conn)
        calc.mark_stale_runs(conn)

        last = pd.Timestamp(args.date_to).date() if args.date_to else yesterday_local()
        first = pd.Timestamp(args.date_from).date() if args.date_from else \
            last - dt.timedelta(days=settings.IMB_FINAL_LOOKBACK_DAYS)
        first = max(first, pd.Timestamp(settings.FIRST_DAY).date())
        window = day_list(first, last)
        # older days whose download failed earlier (e.g. API timeout) are retried too
        with conn.cursor() as cur:
            cur.execute("""SELECT DISTINCT delivery_day FROM market_day
                           WHERE component = 'prices' AND status IN ('missing', 'invalid')
                             AND delivery_day >= %s AND delivery_day < %s""", (settings.FIRST_DAY, first))
            window = sorted(set(window) | {r[0] for r in cur.fetchall()})
        conn.commit()
        recent = set(day_list(last - dt.timedelta(days=settings.RECENT_DAYS - 1), last))
        log("pipeline {} .. {} ({} days)".format(first, last, len(window)))

        touched = set()
        onedrive_ok = True
        if args.source == "local":
            local_run = local_inbox.LocalInboxRun(conn, prun, args.delete_after_success, allow_api=not args.skip_api)
            touched |= local_run.intake()
            file_days = {d for d in touched if str(d) >= settings.FIRST_DAY}
            # A manual upload updates its own period. Scheduled cron still refreshes recent markets.
            if args.trigger != "cron" and not args.date_from and not args.date_to:
                window = sorted(file_days)
            else:
                window = sorted(set(window) | file_days)
            recent &= set(window)
        if args.source == "onedrive" and settings.ONEDRIVE_REMOTE and not args.skip_onedrive:
            try:
                touched |= onedrive.intake(conn, messages)
            except onedrive.RcloneError as exc:
                onedrive_ok = False
                messages.append("OneDrive {}: {}".format("autorizácia" if exc.auth else "nedostupný", exc))
        if not args.skip_api and window:
            readiness.refresh(conn, window)
            touched |= fetch_okte(conn, window, recent, messages)
            touched |= fetch_entsoe(conn, window, recent, messages)
        calc_days = sorted(set(window) | {d for d in touched if str(d) >= settings.FIRST_DAY})
        readiness.refresh(conn, calc_days)
        tasks = calc.select_tasks(conn, calc_days, force=args.force_calc)
        log("calculation tasks: {}".format(len(tasks)))
        run_id, n_done, errors = calc.run(conn, tasks, trigger=args.trigger, log=log)
        messages += errors
        if tasks:
            messages.append("Výpočet: {} dní/trhov zverejnených (run {})".format(n_done, run_id))
        if messages and any(not m.startswith(("Výpočet", "OneDrive: ")) for m in messages):
            status = "partial"
        if args.source == "onedrive" and settings.ONEDRIVE_REMOTE and not args.skip_onedrive:
            text = status_text(conn, started, messages, onedrive_ok)
            try:
                onedrive.write_status(text)
            except onedrive.RcloneError as exc:
                status = "partial"
                messages.append("OneDrive: STAV.txt sa nepodarilo zapísať ({})".format(exc))
        if local_run:
            if not local_run.finish(messages):
                status = "partial"
                messages.append("Local inbox: some files are waiting, rejected or require cleanup (see report)")
            for entry in local_run.entries:
                if entry.get("error"):
                    messages.append("Local inbox {} [{}]: {}".format(entry['original_name'], entry['status'], entry['error']))
            log("Local report JSON: {}".format(local_run.json_path))
            log("Local report TXT: {}".format(local_run.txt_path))
        for m in messages:
            log(m)
    except Exception:
        status = "failed"
        messages.append(traceback.format_exc())
        log(messages[-1])
        if local_run:
            try:
                local_run.fail(messages[-1])
            except Exception:
                log("Could not persist the failure report: " + traceback.format_exc())
    finally:
        conn.rollback()
        with conn.cursor() as cur:
            cur.execute("""UPDATE pipeline_run SET finished_at = now(), status = %s, messages = %s
                           WHERE pipeline_run_id = %s""", (status, "\n".join(messages)[:20000] or None, prun))
        conn.commit()
        db.release_pipeline_lock(conn)
        conn.close()
    log("pipeline finished: {}".format(status))
    return 0 if status == "success" or (status == "partial" and args.source != "local") else 1


if __name__ == "__main__":
    sys.exit(main())
