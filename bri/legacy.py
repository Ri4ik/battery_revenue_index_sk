"""One-time import of the file-based project state (legacy) into the database.

* results/*.json  -> one calc_run with model_version 'legacy-json-v1' (published until a
  native run replaces each day); DA after 01.10.2025 in these files is hourly averaged.
* marketdata/aFRR_capacity/*.csv (normalized ENTSO-E data) -> afrr_capacity.
* marketdata/SepsDamasEnergy/raw/*.xlsx (original SEPS exports) -> afrr_energy.
"""

import csv
import json
import re
from pathlib import Path

import pandas as pd

from bri import artifacts, inputs, settings
from bri.db import execute_values
from bri.importers import entsoe, seps

LEGACY_MODEL = "legacy-json-v1"
RESULT_FOLDERS = {  # folder name -> battery_config.code
    "Slovakia_2025-2026 (01.03)": "1h_1c",
    "Slovakia_2025-2026 (01.03)_1_2": "1h_2c",
    "Slovakia_2025-2026 (01.03)_2_1": "2h_1c",
    "Slovakia_2025-2026 (01.03)_2_2": "2h_2c",
}
FILE_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})_results_(DA|IDM15|IDM60|IMB|aFRR)\.json$")


def import_afrr_capacity_csv(conn, folder, log=print):
    files = sorted(Path(folder).glob("afrr_capacity_*.csv"))
    imported = 0
    for path in files:
        content = path.read_bytes()
        records = list(csv.DictReader(content.decode("utf-8").splitlines()))
        if not records:
            continue
        artifact_id = artifacts.store_artifact(
            conn, "ENTSOE", "legacy", content, "csv", original_name=path.name,
            metadata={"note": "normalized by tools/importaFRR.py (procured_balancing_capacity)"},
        )
        _, n, skipped = artifacts.import_artifact(
            conn, artifact_id, "legacy-afrr-capacity-csv-v1",
            lambda cur, b: inputs.write_afrr_capacity_rows(cur, entsoe.rows_from_legacy(records), b),
        )
        imported += 0 if skipped else 1
    log("aFRR capacity CSV: {} files, {} imported".format(len(files), imported))


def import_seps_raw(conn, folder, log=print):
    for path in sorted(Path(folder).glob("*.xlsx")):
        try:
            res = seps.import_xlsx(conn, path.read_bytes(), path.name, "legacy",
                                   metadata={"legacy_path": str(path.name)})
            log("SEPS {}: {} rows ({}..{}){}".format(path.name, res["rows"], res["first_day"], res["last_day"],
                                                     " already imported" if res["skipped"] else ""))
        except seps.FormatError as exc:
            log("SEPS {}: rejected - {}".format(path.name, exc))


def _interval_rows(run_id, cid, market_id, component, series):
    rows = []
    for ts, value in series.items():
        start = pd.Timestamp(ts).tz_convert("UTC")
        rows.append((run_id, cid, market_id, component, start.to_pydatetime(),
                     (start + pd.Timedelta(minutes=15)).to_pydatetime(), float(value or 0.0)))
    return rows


def import_results(conn, results_dir, log=print):
    """Load legacy JSON results as one published calc_run."""
    if not any(FILE_RE.match(p.name) for folder in RESULT_FOLDERS
               for p in (Path(results_dir) / folder).glob("*.json")):
        log("legacy results: no JSON files in {}".format(results_dir))
        return None
    with conn.cursor() as cur:
        cur.execute("SELECT run_id FROM calc_run WHERE model_version = %s", (LEGACY_MODEL,))
        if cur.fetchone():
            conn.commit()
            log("legacy results already imported")
            return None
        cur.execute("""INSERT INTO calc_run (status, trigger, code_version, model_version)
                       VALUES ('running', 'migration', 'legacy-files', %s) RETURNING run_id""", (LEGACY_MODEL,))
        run_id = cur.fetchone()[0]
        cur.execute("SELECT code, config_id FROM battery_config")
        config_ids = dict(cur.fetchall())
        cur.execute("SELECT code, market_id FROM market")
        market_ids = dict(cur.fetchall())
    conn.commit()

    daily, published = [], set()
    count = 0
    for folder, code in RESULT_FOLDERS.items():
        cid = config_ids[code]
        for path in sorted((Path(results_dir) / folder).glob("*.json")):
            m = FILE_RE.match(path.name)
            if not m:
                continue
            day, name = m.group(1), m.group(2)
            market = "AFRR" if name == "aFRR" else name
            payload = json.loads(path.read_text(encoding="utf-8"))
            dr = payload["results"]["daily_revenue"]
            mr = payload.get("market_results", {})
            intervals = []
            if market == "AFRR":
                daily += [(run_id, cid, market_ids[market], day, "capacity", float(dr.get("aFRR_Capacity", 0))),
                          (run_id, cid, market_ids[market], day, "energy", float(dr.get("aFRR_Energy", 0))),
                          (run_id, cid, market_ids[market], day, "total", float(dr["Total"]))]
                cap = pd.Series(mr.get("aFRR Capacity", {}), dtype=float)
                en = pd.Series(mr.get("aFRR Energy", {}), dtype=float)
                intervals += _interval_rows(run_id, cid, market_ids[market], "capacity", cap)
                intervals += _interval_rows(run_id, cid, market_ids[market], "energy", en)
                total = cap.add(en, fill_value=0)
            else:
                daily.append((run_id, cid, market_ids[market], day, "total", float(dr["Total"])))
                total = pd.Series(mr.get(name, {}), dtype=float)
            soc = {pd.Timestamp(k).tz_convert("UTC").to_pydatetime(): v for k, v in mr.get("SOC", {}).items()}
            for row in _interval_rows(run_id, cid, market_ids[market], "total", total):
                value = soc.get(row[4])
                intervals.append(row + (float(value) if value is not None and pd.notna(value) else None,))
            intervals = [r if len(r) == 8 else r + (None,) for r in intervals]
            with conn.cursor() as cur:
                execute_values(cur, """INSERT INTO result_interval (run_id, config_id, market_id, component,
                                           interval_start, interval_end, revenue_eur, soc) VALUES %s
                                       ON CONFLICT DO NOTHING""", intervals, page_size=5000)
            conn.commit()
            published.add((cid, market_ids[market], day))
            count += 1
            if count % 1000 == 0:
                log("  legacy results {} files".format(count))
    with conn.cursor() as cur:
        execute_values(cur, """INSERT INTO result_daily (run_id, config_id, market_id, delivery_day, component,
                                   revenue_eur) VALUES %s""", daily)
        execute_values(cur, """INSERT INTO result_publication (config_id, market_id, delivery_day, run_id)
                               VALUES %s ON CONFLICT DO NOTHING""",
                       [(c, mk, d, run_id) for c, mk, d in published])
        cur.execute("UPDATE calc_run SET status = 'success', finished_at = now() WHERE run_id = %s", (run_id,))
    conn.commit()
    log("legacy results: {} files -> run {}".format(count, run_id))
    return run_id
