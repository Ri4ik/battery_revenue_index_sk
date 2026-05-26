import copy
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


if __name__ == "__main__":
    args, _ = parse_slovakia_run_args(
        "Slovakia single-market benchmark (DA, IDM15, IDM60, IMB, FCR, aFRR)"
    )
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
                    "cycle_share": 0,
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

            for day in day_list:
                if args.resume and day_outputs_complete(result_folder, day, market_list):
                    print(f"Skip {energy}h/{cycles}c (cached): {day}")
                    continue
                battery_config_copy = copy.deepcopy(battery_config)
                try:
                    sm.process_day(
                        day,
                        battery_config_copy,
                        market_config,
                        result_folder,
                        market_list,
                    )
                except Exception as e:
                    print(f"Error for day {day}")
                    print(e)
                    print(traceback.format_exc())
                    continue
