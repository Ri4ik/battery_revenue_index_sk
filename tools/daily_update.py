"""
Daily update of the Slovak battery revenue index (meant to run from cron).

1. Imports manually supplied SEPS/Damas XLSX files dropped into inbox/.
2. Downloads new market data from public APIs:
   DA, IDM15, IDM60, IMB (OKTE) and aFRR Capacity (ENTSO-E, needs ENTSOE_API_KEY).
   IMB is refreshed until OKTE publishes the final evaluation (~2-3 months later).
3. Recomputes revenues for every day/market whose input data are new or changed,
   for all 4 battery configs, into the existing result folders.
4. Writes the new results into the dashboard database (if DATABASE_URL is set).
5. Writes inbox/MISSING_MANUAL_DATA.txt with the data that must be added manually.

Usage (from repository root):
    python tools/daily_update.py                       # last --lookback days up to yesterday
    python tools/daily_update.py --from 2026-03-02     # backfill from a given day
"""

import argparse
import concurrent.futures
import contextlib
import copy
import datetime
import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import time
import traceback
from pathlib import Path
from typing import Dict, List, Set, Tuple

_REPO = Path(__file__).resolve().parent.parent
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

import pandas as pd

from calculation_config_slovakia import build_configs
from tools import import_okte_exports as okte
from tools import import_seps_damas_energy as seps
from tools.slovakia_revenue_dashboard import DEFAULT_RESULTS_BASE, parse_result_file
from tools.validate_marketdata import validate_required_marketdata

TZ = "Europe/Bratislava"
DATASET = DEFAULT_RESULTS_BASE
DATASET_START = "2025-03-01"
COMBOS = [(1, 1), (1, 2), (2, 1), (2, 2)]
MARKETS = ["DA", "IDM15", "IDM60", "IMB", "aFRR"]
SEPS_DAILY = "SepsDamasEnergy/daily/seps_damas_afrr_energy_{d}.csv"
INPUTS = {
    "DA": ["DA/DA_{d}.csv"],
    "IDM15": ["IDM15/IDM15_{d}.csv"],
    "IDM60": ["IDM60/IDM60_{d}.csv"],
    "IMB": ["IMB/IMB_{d}.csv"],
    "aFRR": ["aFRR_capacity/afrr_capacity_{d}.csv", SEPS_DAILY],
}
# Already downloaded days are re-downloaded this many days back (late corrections).
RECENT_REFRESH_DAYS = 3
IMB_RANK = {"daily": 0, "monthly": 1, "final": 2}
IMB_STATUS_FILE = "IMB/_evaluation_status.json"
LOCK_MAX_AGE_S = 12 * 3600

MARKETDATA = _REPO / "marketdata"
RESULTS = _REPO / "results"
INBOX = _REPO / "inbox"


def log(msg: str) -> None:
    print("[{}] {}".format(datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"), msg), flush=True)


def day_range(start: str, end: str) -> List[str]:
    if start > end:
        return []
    return pd.date_range(start=start, end=end, freq="D").strftime("%Y-%m-%d").tolist()


def contiguous_ranges(days) -> List[Tuple[str, str]]:
    """Sorted day strings -> [(first, last), ...] of consecutive runs."""
    out: List[Tuple[str, str]] = []
    for d in sorted(days):
        if out and pd.Timestamp(d) - pd.Timestamp(out[-1][1]) == pd.Timedelta(days=1):
            out[-1] = (out[-1][0], d)
        else:
            out.append((d, d))
    return out


def fmt_ranges(days) -> str:
    return ", ".join(a if a == b else "{} .. {}".format(a, b) for a, b in contiguous_ranges(days))


def input_paths(market: str, day: str) -> List[Path]:
    return [MARKETDATA / p.format(d=day) for p in INPUTS[market]]


def result_folder(energy: int, cycles: int) -> Path:
    # 1 h / 1 cycle lives in the legacy folder without suffix (see dashboard/importer).
    if (energy, cycles) == (1, 1):
        return RESULTS / DATASET
    return RESULTS / "{}_{}_{}".format(DATASET, energy, cycles)


def result_path(folder: Path, day: str, market: str) -> Path:
    return folder / "{}_results_{}.json".format(day, market)


# ---------------------------------------------------------------------------
# Locking and change detection
# ---------------------------------------------------------------------------

@contextlib.contextmanager
def run_lock():
    lock = MARKETDATA / ".daily_update.lock"
    if lock.exists() and time.time() - lock.stat().st_mtime > LOCK_MAX_AGE_S:
        lock.unlink()
    try:
        fd = os.open(str(lock), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        raise SystemExit("Another daily update is running ({} exists).".format(lock))
    try:
        os.write(fd, str(os.getpid()).encode())
        os.close(fd)
        yield
    finally:
        lock.unlink()


def file_hash(path: Path) -> str:
    return hashlib.md5(path.read_bytes()).hexdigest()


def snapshot(paths) -> Dict[Path, Tuple[str, int, int]]:
    snap = {}
    for p in paths:
        if p.is_file():
            st = p.stat()
            snap[p] = (file_hash(p), st.st_atime_ns, st.st_mtime_ns)
    return snap


def restore_unchanged_mtimes(snap) -> None:
    """Importers rewrite files even when nothing changed; keep old mtime then,
    so that unchanged inputs do not trigger a recalculation."""
    for p, (digest, atime, mtime) in snap.items():
        if p.is_file() and file_hash(p) == digest:
            os.utime(str(p), ns=(atime, mtime))


# ---------------------------------------------------------------------------
# Data import
# ---------------------------------------------------------------------------

def import_inbox() -> Set[str]:
    """Import SEPS/Damas XLSX files from inbox/, return the days written."""
    INBOX.mkdir(exist_ok=True)
    files = sorted(p for p in INBOX.iterdir() if p.is_file() and p.suffix.lower() == ".xlsx")
    if not files:
        return set()
    daily_dir = MARKETDATA / "SepsDamasEnergy" / "daily"
    before = {p.name: p.stat().st_mtime_ns for p in daily_dir.glob("*.csv")} if daily_dir.is_dir() else {}
    log("Importing {} SEPS/Damas file(s) from inbox: {}".format(len(files), [f.name for f in files]))
    rc = seps.main(["--workspace", str(_REPO)] + [str(f) for f in files])
    if rc != 0:
        log("SEPS/Damas import failed (exit {}); files stay in inbox/.".format(rc))
        return set()
    processed = INBOX / "processed"
    processed.mkdir(exist_ok=True)
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    for f in files:
        shutil.move(str(f), str(processed / "{}_{}".format(stamp, f.name)))
    days = set()
    for p in daily_dir.glob("seps_damas_afrr_energy_*.csv"):
        if before.get(p.name) != p.stat().st_mtime_ns:
            days.add(p.stem.rsplit("_", 1)[-1])
    log("SEPS/Damas data written for {} day(s).".format(len(days)))
    return days


def days_to_download(market: str, days: List[str], end: str) -> List[str]:
    """Days without a usable file, plus the most recent days (late corrections)."""
    recent_from = (pd.Timestamp(end) - pd.Timedelta(days=RECENT_REFRESH_DAYS - 1)).strftime("%Y-%m-%d")
    out = []
    for d in days:
        path = input_paths(market, d)[0]
        # aFRR validity also depends on the manual SEPS file, so only check the downloaded file exists.
        usable = path.is_file() if market == "aFRR" else inputs_valid(market, d)
        if d >= recent_from or not usable:
            out.append(d)
    return out


def guarded(label: str, fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except Exception as exc:
        log("{} failed: {}".format(label, exc))
        return None


def download_okte_prices(days: List[str], end: str) -> None:
    for a, b in contiguous_ranges(days_to_download("DA", days, end)):
        guarded("DA {}..{}".format(a, b), okte.import_dam_results_api, str(_REPO), a, b)
    for product_type, market in ((15, "IDM15"), (60, "IDM60")):
        for a, b in contiguous_ranges(days_to_download(market, days, end)):
            guarded(
                "{} {}..{}".format(market, a, b),
                okte.import_idm_results_api,
                workspace=str(_REPO),
                date_from=a,
                date_to=b,
                product_type=product_type,
                market_name=market,
            )


def download_imb(days: List[str]) -> None:
    """Fetch best available IMB evaluation: final > monthly > daily (preliminary)."""
    status_path = MARKETDATA / IMB_STATUS_FILE
    status = json.loads(status_path.read_text()) if status_path.is_file() else {}
    for eval_type in ("final", "monthly", "daily"):
        rank = IMB_RANK[eval_type]
        # A day is re-fetched with this type only if it does not have this or a better one yet.
        eligible = [d for d in days if IMB_RANK.get(status.get(d), -1) < rank]
        for a, b in contiguous_ranges(eligible):
            try:
                got = okte.import_system_imbalance_api(str(_REPO), a, b, eval_type)
            except ValueError:
                got = []  # this evaluation is not published yet for these days
            except Exception as exc:
                log("IMB {} {}..{} failed: {}".format(eval_type, a, b, exc))
                got = []
            for d in got:
                status[d] = eval_type
        status_path.parent.mkdir(parents=True, exist_ok=True)
        status_path.write_text(json.dumps(status, indent=1, sort_keys=True))
    pending = [d for d in days if status.get(d) != "final"]
    if pending:
        log("IMB not final yet (will be refreshed later): {}".format(fmt_ranges(pending)))


def download_afrr_capacity(days: List[str], end: str) -> None:
    need = days_to_download("aFRR", days, end)
    has_key = os.environ.get("ENTSOE_API_KEY") or (_REPO / "api_checks" / "entsoe" / ".env").is_file()
    if not need:
        return
    if not has_key:
        log("ENTSOE_API_KEY not set - aFRR Capacity not downloaded.")
        return
    for a, b in contiguous_ranges(need):
        cmd = [
            sys.executable, str(_REPO / "tools" / "importaFRR.py"),
            "--workspace", str(_REPO), "--date-from", a, "--date-to", b,
            "--datasets", "procured_balancing_capacity", "--skip-merit-order",
        ]
        rc = subprocess.call(cmd, cwd=str(_REPO))
        if rc != 0:
            log("aFRR Capacity {}..{} failed (exit {}).".format(a, b, rc))
    # importaFRR.py exits 0 even on network errors; check what actually arrived.
    missing = [d for d in need if not input_paths("aFRR", d)[0].is_file()]
    if missing:
        log("aFRR Capacity not available from ENTSO-E for: {} "
            "(see marketdata/ENTSOE/aFRR/import_report_*.txt)".format(fmt_ranges(missing)))


# ---------------------------------------------------------------------------
# Calculation
# ---------------------------------------------------------------------------

def inputs_valid(market: str, day: str) -> bool:
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            validate_required_marketdata(str(_REPO), [day], [market])
        return True
    except ValueError:
        return False


def markets_to_compute(folder: Path, day: str, valid: Dict[Tuple[str, str], bool]) -> List[str]:
    out = []
    for market in MARKETS:
        if not valid[(market, day)]:
            continue
        res = result_path(folder, day, market)
        if not res.is_file():
            out.append(market)
            continue
        newest_input = max(p.stat().st_mtime for p in input_paths(market, day))
        if newest_input > res.stat().st_mtime:
            out.append(market)
    return out


def _compute(task):
    energy, cycles, day, markets = task
    import analysismodes.single_market_analysis as sm

    _, battery_config, market_config = build_configs(energy, cycles)
    folder = result_folder(energy, cycles)
    folder.mkdir(parents=True, exist_ok=True)
    with contextlib.redirect_stdout(io.StringIO()) as out:
        sm.process_day(day, copy.deepcopy(battery_config), copy.deepcopy(market_config), str(folder), markets)
    errors = [line for line in out.getvalue().splitlines() if line.startswith("Error in day")]
    return task, errors


def run_calculations(tasks, workers: int) -> None:
    if not tasks:
        log("Nothing to recalculate.")
        return
    log("Recalculating {} day/config task(s) with {} worker(s)...".format(len(tasks), workers))
    done = 0
    with concurrent.futures.ProcessPoolExecutor(max_workers=workers) as ex:
        futures = [ex.submit(_compute, t) for t in tasks]
        for fut in concurrent.futures.as_completed(futures):
            done += 1
            try:
                (energy, cycles, day, markets), errors = fut.result()
                for e in errors:
                    log("  {} {}h/{}c: {}".format(day, energy, cycles, e))
            except Exception:
                log("  task failed:\n" + traceback.format_exc())
            if done % 50 == 0 or done == len(tasks):
                log("  {}/{} done".format(done, len(tasks)))


# ---------------------------------------------------------------------------
# Database and report
# ---------------------------------------------------------------------------

def push_to_db(tasks) -> None:
    from tools import results_db

    if not results_db.database_url():
        log("DATABASE_URL not set - skipping database update.")
        return
    engine = results_db.get_engine()
    results_db.init_schema(engine)
    total = 0
    for energy, cycles in COMBOS:
        folder = result_folder(energy, cycles)
        rows = []
        for e, c, day, markets in tasks:
            if (e, c) != (energy, cycles):
                continue
            for market in markets:
                p = result_path(folder, day, market)
                row = parse_result_file(p) if p.is_file() else None
                if row:
                    rows.append(row)
        if rows:
            total += results_db.upsert_rows(engine, DATASET, energy, cycles, pd.DataFrame(rows))
    log("Database updated: {} row(s).".format(total))


def write_missing_report(end: str, valid_days: List[str], valid: Dict[Tuple[str, str], bool]) -> None:
    seps_missing = [d for d in day_range(DATASET_START, end) if not (MARKETDATA / SEPS_DAILY.format(d=d)).is_file()]
    lines = [
        "Stav k {} (posledny pocitany den: {})".format(datetime.datetime.now().strftime("%Y-%m-%d %H:%M"), end),
        "",
    ]
    if seps_missing:
        lines += [
            "RUCNE DOPLNIT - SEPS/Damas 'Regulacna elektrina (denna)' (aFRR Energy):",
            "  chybajuce dni: " + fmt_ranges(seps_missing),
            "  XLSX subor(y) za tieto dni vlozte do priecinka inbox/.",
            "  Najblizsie spustenie ich naimportuje a dopocita aFRR.",
            "",
        ]
    else:
        lines += ["SEPS/Damas aFRR Energy: kompletne, nic netreba doplnat.", ""]
    auto_missing = {
        m: [d for d in valid_days if not valid[(m, d)]] for m in MARKETS if m != "aFRR"
    }
    auto_missing["aFRR Capacity"] = [d for d in valid_days if not input_paths("aFRR", d)[0].is_file()]
    auto_missing = {m: ds for m, ds in auto_missing.items() if ds}
    if auto_missing:
        lines.append("Automaticke zdroje este nepublikovali (doplni sa samo):")
        for m, ds in auto_missing.items():
            lines.append("  {}: {}".format(m, fmt_ranges(ds)))
    INBOX.mkdir(exist_ok=True)
    (INBOX / "MISSING_MANUAL_DATA.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    for line in lines:
        if line:
            log(line)


# ---------------------------------------------------------------------------

def main():
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--from", dest="date_from", help="First day (YYYY-MM-DD). Default: today - lookback.")
    p.add_argument("--to", dest="date_to", help="Last day (YYYY-MM-DD). Default: yesterday.")
    p.add_argument("--lookback", type=int, default=120,
                   help="Days back to keep refreshing (covers late final IMB prices).")
    p.add_argument("--workers", type=int, default=max(1, (os.cpu_count() or 2) - 1))
    p.add_argument("--skip-download", action="store_true", help="Only recalculate and update DB.")
    args = p.parse_args()

    os.chdir(str(_REPO))  # calculation code uses relative 'marketdata/' paths
    today = pd.Timestamp.now(tz=TZ).normalize().tz_localize(None)
    end = args.date_to or (today - pd.Timedelta(days=1)).strftime("%Y-%m-%d")
    start = args.date_from or (today - pd.Timedelta(days=args.lookback)).strftime("%Y-%m-%d")
    days = day_range(start, end)
    log("Daily update {} .. {} ({} days)".format(start, end, len(days)))

    with run_lock():
        new_seps_days = import_inbox()

        if not args.skip_download:
            watched = [p for d in days for m in MARKETS for p in input_paths(m, d)]
            snap = snapshot(watched)
            download_okte_prices(days, end)
            download_imb(days)
            download_afrr_capacity(days, end)
            restore_unchanged_mtimes(snap)

        # Days to check: the window plus any older day that just got SEPS/Damas data.
        calc_days = sorted(set(days) | {d for d in new_seps_days if DATASET_START <= d <= end})
        valid = {(m, d): inputs_valid(m, d) for d in calc_days for m in MARKETS}
        tasks = []
        for energy, cycles in COMBOS:
            folder = result_folder(energy, cycles)
            for d in calc_days:
                markets = markets_to_compute(folder, d, valid)
                if markets:
                    tasks.append((energy, cycles, d, markets))
        run_calculations(tasks, args.workers)
        push_to_db(tasks)
        write_missing_report(end, days, valid)
    log("Daily update finished.")


if __name__ == "__main__":
    main()
