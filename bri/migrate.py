"""One-time migration of the file-based project into the database.

    python -m bri.migrate --marketdata /app/marketdata --results /app/results

Steps (each idempotent - safe to run again):
  1. schema + battery configurations
  2. legacy inputs: aFRR capacity CSV, original SEPS/Damas XLSX
  3. legacy results (JSON) as calc_run 'legacy-json-v1', published
  4. pipeline backfill from BRI_FIRST_DAY: DA/IDM/IMB re-downloaded from the OKTE API with
     native intervals, readiness, native calculation; native results replace legacy ones
     wherever inputs are complete
"""

import argparse
import sys
from pathlib import Path

from bri import calc, db, legacy, pipeline, settings


def main(argv=None):
    p = argparse.ArgumentParser()
    p.add_argument("--marketdata", default=str(settings.REPO / "marketdata"))
    p.add_argument("--results", default=str(settings.REPO / "results"))
    p.add_argument("--skip-backfill", action="store_true")
    args = p.parse_args(argv)

    conn = db.connect()
    pipeline.log("schema: {}".format(db.migrate(conn) or "up to date"))
    calc.seed_configs(conn)
    md = Path(args.marketdata)
    legacy.import_afrr_capacity_csv(conn, md / "aFRR_capacity", log=pipeline.log)
    legacy.import_seps_raw(conn, md / "SepsDamasEnergy" / "raw", log=pipeline.log)
    legacy.import_results(conn, args.results, log=pipeline.log)
    conn.close()
    if args.skip_backfill:
        return 0
    return pipeline.main(["--from", settings.FIRST_DAY, "--trigger", "backfill", "--skip-onedrive"])


if __name__ == "__main__":
    sys.exit(main())
