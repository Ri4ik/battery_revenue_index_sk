"""
Load daily revenue JSON results from results/ into the dashboard database.

Folder naming follows the calculation scripts:
    results/<dataset>_<system_type>_<cycle_limit>/   -> that battery combo
    results/<dataset>/                               -> legacy folder, counts as 1 h / 1 cycle

Usage (from repository root, DATABASE_URL must be set):
    python tools/import_results_to_db.py            # upsert the published annual dataset
    python tools/import_results_to_db.py --dataset NAME --force

Modified 2026-10-20: strict validation, selection, and transactional upserts.
"""

import argparse
import sys
import time
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from sqlalchemy.exc import OperationalError

from tools import results_db
from tools.result_files import (
    BATTERY_SUFFIX_RE,
    folder_has_results,
    parse_result_files,
)


def folder_to_combo(folder: Path):
    m = BATTERY_SUFFIX_RE.match(folder.name)
    if m:
        return m.group(1), int(m.group(2)), int(m.group(3))
    return folder.name, 1, 1


def wait_for_engine(retries: int = 30, delay: float = 2.0):
    engine = results_db.get_engine()
    for attempt in range(1, retries + 1):
        try:
            with engine.connect():
                return engine
        except OperationalError:
            print(f"Database not ready yet ({attempt}/{retries}), waiting...")
            time.sleep(delay)
    raise SystemExit("Database is not reachable.")


def import_results(engine, results_dir, selected, force=False):
    results_dir = Path(results_dir)
    if not results_dir.is_dir():
        raise ValueError(f"Results folder not found: {results_dir}")
    found, count = set(), 0
    for folder in sorted(p for p in results_dir.iterdir() if p.is_dir()):
        dataset, system_type, cycle_limit = folder_to_combo(folder)
        if dataset not in selected or not folder_has_results(folder):
            continue
        if folder.name == dataset and folder_has_results(results_dir / f"{dataset}_1_1"):
            continue
        df = parse_result_files(folder, system_type, cycle_limit)
        n = results_db.write_combo(engine, dataset, system_type, cycle_limit, df, replace=force)
        found.add(dataset)
        count += n
        print(f"{'Replaced' if force else 'Upserted'} {n} rows: {dataset} [{system_type} h / {cycle_limit} c]")
    if missing := set(selected) - found:
        raise ValueError(f"Selected datasets have no result files: {sorted(missing)}")
    return count


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--results-dir", default=str(_REPO / "results"))
    p.add_argument("--dataset", action="append", help="Dataset base name; repeatable. Default: published annual dataset.")
    p.add_argument("--force", action="store_true", help="Replace selected combos, including removing obsolete rows.")
    args = p.parse_args()

    results_dir = Path(args.results_dir)
    if not results_dir.is_dir():
        raise SystemExit(f"Results folder not found: {results_dir}")

    engine = wait_for_engine()
    results_db.init_schema(engine)

    import_results(engine, results_dir, args.dataset or [results_db.DEFAULT_DATASET], args.force)
    print("Import finished.")


if __name__ == "__main__":
    main()
