import copy
import datetime
import os
import traceback

import pandas as pd

import analysismodes.single_market_analysis as sm


if __name__ == "__main__":
    # Simplified Slovak benchmark:
    # DA + IDA1 + ID1 + IMB + FCR + aFRR Capacity
    # (aFRR Energy and IDC are intentionally excluded in this first phase)
    for energy in [1, 2]:
        for cycles in [1, 2]:
            parallel = False
            start_day = "2025-03-01"
            end_day = "2025-03-01"
            market_list = ["DA", "IDA1", "ID1", "IMB", "FCR", "aFRR"]

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
                "IDA1": {
                    "t_delivery": 0.25,
                    "power_share": 1,
                    "capture_rate": 1,
                    "capacity_share": 1,
                },
                "ID1": {
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
                # For the simplified model, we keep aFRR energy logic switched off
                # by setting near-zero marketable participation.
                "aFRR Energy": {
                    "t_delivery": 0.25,
                    "power_share": 0.0001,
                    "capture_rate": 0,
                    "capacity_share": 0,
                    "init_position": 0.05,
                    "cycle_share": 0.0001,
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

            day_list = (
                pd.date_range(start=start_day, end=end_day, freq="D")
                .strftime("%Y-%m-%d")
                .tolist()
            )

            current_date = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M")
            result_folder = os.path.join(
                "results",
                f"Slovakia_SingleMarket_results_{current_date}_{energy}_{cycles}",
            )
            os.makedirs(result_folder, exist_ok=True)

            if parallel:
                raise NotImplementedError("Parallel mode is disabled in this config.")
            else:
                for day in day_list:
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
