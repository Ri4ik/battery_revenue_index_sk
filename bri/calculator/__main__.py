"""Battery Calculator command line.

  python -m bri.calculator run <scenario_id>            compute a stored scenario (used by the web page)
  python -m bri.calculator new power_mw=2 duration_h=2 strategy=BEST ...
                                                        create and compute a scenario, print the KPIs
  python -m bri.calculator warm --durations 1,2 --cycles 1,2 [--from YYYY-MM-DD --to YYYY-MM-DD]
                                                        precompute the revenue cache for common batteries
"""

import argparse
import sys

from bri import db
from bri.calculator import params as P, scenario, simulation


def main(argv=None):
    parser = argparse.ArgumentParser(prog="python -m bri.calculator")
    sub = parser.add_subparsers(dest="cmd")
    p_run = sub.add_parser("run")
    p_run.add_argument("scenario_id", type=int)
    p_new = sub.add_parser("new")
    p_new.add_argument("values", nargs="*", help="name=value")
    p_warm = sub.add_parser("warm")
    p_warm.add_argument("--durations", default="1,2")
    p_warm.add_argument("--cycles", default="1,2")
    p_warm.add_argument("--from", dest="start")
    p_warm.add_argument("--to", dest="end")
    args = parser.parse_args(argv)

    conn = db.connect()
    db.migrate(conn)
    if args.cmd == "run":
        return 0 if scenario.run(conn, args.scenario_id) == "success" else 1
    if args.cmd == "new":
        raw = dict(v.split("=", 1) for v in args.values)
        sid = scenario.create(conn, raw)
        status = scenario.run(conn, sid)
        s, _, years = scenario.load(conn, sid)
        print("scenario {}: {}".format(sid, status))
        if status == "success":
            print(scenario.dumps(s["summary"]["kpi"]))
            print(years.round(1).to_string(index=False))
        else:
            print(s["message"])
        return 0 if status == "success" else 1
    if args.cmd == "warm":
        first, last = simulation.data_range(conn)
        start, end = args.start or first, args.end or last
        for duration in [float(x) for x in args.durations.split(",")]:
            for cycles in [float(x) for x in args.cycles.split(",")]:
                p = P.validate({"duration_h": duration, "cycles_per_day": cycles,
                                "period_start": str(start), "period_end": str(end)}, first, last)
                frame = simulation.simulate(conn, P.tech(p), P.MARKETS, start, end)
                print("{} h, {} cycles: {} market-days".format(duration, cycles, len(frame)))
        return 0
    parser.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
