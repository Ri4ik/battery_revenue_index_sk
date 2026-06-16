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
    resolve_result_folder_multi,
)
from tools.validate_marketdata import validate_required_marketdata


def _parse_workers(unknown_args):
    auto_workers = max(1, (os.cpu_count() or 2) - 1)
    workers = auto_workers
    for i, token in enumerate(unknown_args):
        if token == "--workers" and i + 1 < len(unknown_args):
            try:
                workers = max(1, int(unknown_args[i + 1]))
            except ValueError:
                workers = auto_workers
    return workers


def _parse_only_configs(unknown_args):
    """Optional filter: --only-configs 1_2,2_1,2_2"""
    for i, token in enumerate(unknown_args):
        if token == "--only-configs" and i + 1 < len(unknown_args):
            out = set()
            for part in unknown_args[i + 1].split(","):
                part = part.strip()
                if "_" not in part:
                    continue
                e_s, c_s = part.split("_", 1)
                out.add((int(e_s), int(c_s)))
            return out or None
    return None


def _run_day(day, battery_config, market_config, result_folder, market_list):
    cfg = copy.deepcopy(battery_config)
    sm.process_day(day, cfg, market_config, result_folder, market_list)
    return day


def _process_days(day_list, battery_config, market_config, result_folder, market_list, workers):
    pending_days = list(day_list)
    if workers <= 1:
        for day in pending_days:
            try:
                _run_day(day, battery_config, market_config, result_folder, market_list)
                print(f"Done: {day}")
            except Exception as e:
                print(f"Error for day {day}")
                print(e)
                print(traceback.format_exc())
        return

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
                print(f"Error for day {day}")
                print(e)
                print(traceback.format_exc())


if __name__ == "__main__":
    args, unknown = parse_slovakia_run_args(
        "Slovakia single-market benchmark (DA, IDM15, IDM60, IMB, FCR, aFRR)"
    )
    workers = _parse_workers(unknown)
    only_configs = _parse_only_configs(unknown)
    if args.resume and args.result_folder and args.resume_parent:
        raise SystemExit("Use either --result-folder OR --resume-parent with --resume, not both.")
    if args.resume and not args.result_folder and not args.resume_parent:
        raise SystemExit("--resume requires --resume-parent <base> (paths ..._E_C per combo).")
    if args.result_folder and not args.resume:
        raise SystemExit("--result-folder is only used together with --resume.")

    start_day = args.start_day or "2025-03-01"
    end_day = args.end_day or "2026-03-01"

    day_list = (
        pd.date_range(start=start_day, end=end_day, freq="D")
        .strftime("%Y-%m-%d")
        .tolist()
    )
    validate_required_marketdata(
        workspace=".",
        day_list=day_list,
        markets=["DA", "IDM15", "IDM60", "IMB", "aFRR"],
    )

    current_date = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M")

    # Slovak benchmark:
    # DA + IDM15 + IDM60 + IMB + FCR + aFRR Capacity + aFRR Energy.
    # aFRR Energy is sourced from SEPS/Damas "Regulačná elektrina (denná)" exports.
    for energy in [1, 2]:
        for cycles in [1, 2]:
            if only_configs and (energy, cycles) not in only_configs:
                continue
            parallel = False
            market_list = ["DA", "IDM15", "IDM60", "IMB", "FCR", "aFRR"]

            battery_config = {
                "energy": energy,
                "power": 1,
                "cycle_limit": cycles,
                "service_life": 10,
                "efficiency": 0.95,
                "maxSOC": 1,
                "minSOC": 0,
                "startSOC": 0.5,
                "DoD": 1,
                "costs": 250,
            }

            market_config = {
                "DA": {
                    "t_delivery": 1,
                    "power_share": 1,
                    "capture_rate": 1,
                    "capacity_share": 1,
                },
                "IDM15": {
                    "t_delivery": 0.25,
                    "power_share": 1,
                    "capture_rate": 1,
                    "capacity_share": 1,
                },
                "IDM60": {
                    "t_delivery": 0.25,
                    "power_share": 1,
                    "capture_rate": 1,
                    "capacity_share": 1,
                },
                "IMB": {
                    "t_delivery": 0.25,
                    "power_share": 1,
                    "capture_rate": 1,
                    "capacity_share": 1,
                },
                "FCR": {
                    "t_delivery": 4,
                    "power_share": 1,
                    "capture_rate": 1,
                    "capacity_share": 1,
                    "cycle_share": 0.5,
                },
                "aFRR Capacity": {
                    "t_delivery": 4,
                    "power_share": min(1, energy / (battery_config["power"] * 4)),
                    "capture_rate": 1,
                    "capacity_share": 1,
                },
                "aFRR Energy": {
                    "t_delivery": 0.25,
                    "power_share": min(1, energy / (battery_config["power"] * 4)),
                    "capture_rate": 1,
                    "capacity_share": 1,
                    "init_position": 0.05,
                    "cycle_share": 1,
                    "source": "seps_damas",
                },
            }

            aging_costs = (
                battery_config["costs"]
                * 1000
                / (
                    battery_config["service_life"]
                    * battery_config["cycle_limit"]
                    * 365
                )
            )
            battery_config["aging_costs"] = aging_costs

            result_folder = resolve_result_folder_multi(
                energy=energy,
                cycles=cycles,
                current_date=current_date,
                resume=args.resume,
                resume_parent=args.resume_parent,
            )
            os.makedirs(result_folder, exist_ok=True)

            if parallel:
                raise NotImplementedError("Parallel mode is disabled in this config.")

            pending_days = []
            for day in day_list:
                if args.resume and day_outputs_complete(result_folder, day, market_list):
                    print(f"Skip {energy}h/{cycles}c (cached): {day}")
                    continue
                pending_days.append(day)

            if pending_days:
                print(
                    f"Config {energy}h / {cycles}c -> {result_folder} "
                    f"({len(pending_days)} days, workers={workers})"
                )
                _process_days(
                    pending_days,
                    battery_config,
                    market_config,
                    result_folder,
                    market_list,
                    workers,
                )
