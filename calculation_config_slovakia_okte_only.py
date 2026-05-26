import copy
import concurrent.futures
import datetime
import os
import traceback

import pandas as pd

import analysismodes.single_market_analysis as sm
from tools.slovakia_run_common import (
    day_outputs_complete,
    parse_slovakia_run_args,
    resolve_result_folder_single,
)
from tools.validate_marketdata import validate_required_marketdata


def _parse_workers(unknown_args):
    auto_workers = max(1, min(8, (os.cpu_count() or 2) - 1))
    workers = auto_workers
    for i, token in enumerate(unknown_args):
        if token == "--workers" and i + 1 < len(unknown_args):
            try:
                workers = max(1, int(unknown_args[i + 1]))
            except ValueError:
                workers = auto_workers
    return workers


def _run_day(day, battery_config, market_config, result_folder, market_list):
    cfg = copy.deepcopy(battery_config)
    sm.process_day(day, cfg, market_config, result_folder, market_list, use_db=False)
    return day


if __name__ == "__main__":
    args, unknown = parse_slovakia_run_args(
        "Slovakia OKTE-only benchmark (DA, IDM15, IDM60, IMB)"
    )
    workers = _parse_workers(unknown)
    if args.resume and args.resume_parent:
        raise SystemExit("For this script use --resume with --result-folder (full path), not --resume-parent.")
    if args.resume and not args.result_folder:
        raise SystemExit("--resume requires --result-folder <path>")

    start_day = args.start_day or "2025-03-01"
    end_day = args.end_day or "2026-03-01"
    market_list = ["DA", "IDM15", "IDM60", "IMB"]

    battery_config = {
        "energy": 1,
        "power": 1,
        "cycle_limit": 1,
        "service_life": 10,
        "efficiency": 0.95,
        "maxSOC": 1,
        "minSOC": 0,
        "startSOC": 0.5,
        "DoD": 1,
        "costs": 250,
    }

    market_config = {
        "DA": {"t_delivery": 1, "power_share": 1, "capture_rate": 1, "capacity_share": 1},
        "IDM15": {"t_delivery": 0.25, "power_share": 1, "capture_rate": 1, "capacity_share": 1},
        "IDM60": {"t_delivery": 0.25, "power_share": 1, "capture_rate": 1, "capacity_share": 1},
        "IMB": {"t_delivery": 0.25, "power_share": 1, "capture_rate": 1, "capacity_share": 1},
    }

    battery_config["aging_costs"] = (
        battery_config["costs"] * 1000
        / (battery_config["service_life"] * battery_config["cycle_limit"] * 365)
    )

    day_list = (
        pd.date_range(start=start_day, end=end_day, freq="D").strftime("%Y-%m-%d").tolist()
    )
    validate_required_marketdata(
        workspace=".",
        day_list=day_list,
        markets=["DA", "IDM15", "IDM60", "IMB"],
    )

    current_date = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M")
    result_folder = resolve_result_folder_single(
        default_timestamped_name=f"Slovakia_OKTE_only_{current_date}",
        resume=args.resume,
        result_folder=args.result_folder,
    )
    os.makedirs(result_folder, exist_ok=True)

    pending_days = []
    for day in day_list:
        if args.resume and day_outputs_complete(result_folder, day, market_list):
            print(f"Skip (cached): {day}")
            continue
        pending_days.append(day)

    if workers <= 1:
        for day in pending_days:
            cfg = copy.deepcopy(battery_config)
            try:
                sm.process_day(
                    day, cfg, market_config, result_folder, market_list, use_db=False
                )
                print(f"Done: {day}")
            except Exception as e:
                print(f"Error for day {day}: {e}")
                print(traceback.format_exc())
    else:
        print(f"Running in parallel: workers={workers}, days={len(pending_days)}")
        with concurrent.futures.ProcessPoolExecutor(max_workers=workers) as ex:
            futures = {
                ex.submit(
                    _run_day,
                    day,
                    battery_config,
                    market_config,
                    result_folder,
                    market_list,
                ): day
                for day in pending_days
            }
            for fut in concurrent.futures.as_completed(futures):
                day = futures[fut]
                try:
                    fut.result()
                    print(f"Done: {day}")
                except Exception as e:
                    print(f"Error for day {day}: {e}")
                    print(traceback.format_exc())
