import pandas as pd
import datetime
import os
import analysismodes.single_market_analysis as sm
import analysismodes.cross_market_analysis as cm
import traceback
import concurrent.futures
import functools
import copy


if __name__ == "__main__":
    for energy in [1, 2]:
        for cycles in [1, 2]:
            for analysismode in ["Single-Market", "Cross-Market"]:
                # configs
                parallel = False
                start_day = "2025-03-01"
                end_day = "2025-03-01"
                market_list = ["FCR", "aFRR", "DA", "IDA1", "ID1", "IDC"]
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
                }  ## in future rt 90%; costs in €/kWh

                if analysismode == "Single-Market":
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
                        "FCR": {
                            "t_delivery": 4,
                            "power_share": 1,
                            "capture_rate": 6 / 6,
                            "capacity_share": 1,
                            "cycle_share": 0.5,
                        },
                        "aFRR Capacity": {
                            "t_delivery": 4,
                            "power_share": min(1, energy / (battery_config["power"] * 4)),
                            "capture_rate": 6 / 6,
                            "capacity_share": 1,
                        },
                        "aFRR Energy": {
                            "t_delivery": 0.25,
                            "power_share": 1,
                            "capture_rate": 1,
                            "capacity_share": 1,
                            "init_position": 0.05,
                            "cycle_share": 1,
                        },
                        "IDC": {
                            "power_share": 1,
                            "capacity_share": 1,
                            "capture_rate": 0.8,
                            "cycle_share": 1,
                        },
                    }
                elif analysismode == "Cross-Market":
                    market_config = {
                        "FCR": {
                            "t_delivery": 4,
                            "power_share": 0.5,
                            "capture_rate": 6 / 6,
                            "capacity_share": 1,
                        },
                        "aFRR Capacity": {
                            "t_delivery": 4,
                            "power_share": min(1, energy / (battery_config["power"] * 4)),
                            "capture_rate": 6 / 6,
                            "capacity_share": 1,
                        },
                        "aFRR Energy": {
                            "t_delivery": 0.25,
                            "power_share": 0.5,
                            "capture_rate": 1,
                            "capacity_share": 0.5,
                            "init_position": 0.05,
                            "cycle_share": 0.5,
                        },
                        "IDC": {
                            "power_share": 0.5,
                            "capacity_share": 0.5,
                            "capture_rate": 0.8,
                            "cycle_share": 0.5,
                        },
                    }

                # NO CHANgES FROM HERE NECESSARY

                # calculate additional battery parameters. Aging costs are based on initial invest
                aging_costs = (
                    battery_config["costs"]
                    * 1000
                    / (
                        battery_config["service_life"]
                        * battery_config["cycle_limit"]
                        * 365
                    )
                )  # in €/MWh
                battery_config["aging_costs"] = aging_costs

                # make a list of strings with all days between start_day and end_day
                day_list = (
                    pd.date_range(start=start_day, end=end_day, freq="D")
                    .strftime("%Y-%m-%d")
                    .tolist()
                )

                # check if the result folder already exists
                current_date = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M")
                result_folder = os.path.join(
                    "results",
                    f"{analysismode}_results_{current_date}_{energy}_{cycles}",
                )

                if not os.path.exists("results"):
                    os.mkdir("results")

                if not os.path.exists(result_folder):
                    os.mkdir(result_folder)

                results_df_total = pd.DataFrame()

                if parallel:
                    battery_config_copy = copy.deepcopy(battery_config)
                    results = []
                    # Using ThreadPoolExecutor for parallelization
                    with concurrent.futures.ProcessPoolExecutor(
                        max_workers=80
                    ) as executor:
                        # Create a partial function with static arguments
                        if analysismode == "Single-Market":
                            partial_func = functools.partial(
                                sm.process_day,
                                battery_config=battery_config_copy,
                                market_config=market_config,
                                result_folder_path=result_folder,
                                market_list=market_list,
                            )
                        elif analysismode == "Cross-Market":
                            partial_func = functools.partial(
                                cm.process_day,
                                battery_config=battery_config_copy,
                                market_config=market_config,
                                result_folder_path=result_folder,
                            )
                        futures = []
                        for day in day_list:
                            # Submit each combination to the pool
                            futures.append(executor.submit(partial_func, day))

                else:
                    for day in day_list:
                        battery_config_copy = copy.deepcopy(battery_config)
                        try:
                            if analysismode == "Single-Market":
                                sm.process_day(
                                    day,
                                    battery_config_copy,
                                    market_config,
                                    result_folder,
                                    market_list,
                                )
                            elif analysismode == "Cross-Market":
                                cm.process_day(
                                    day,
                                    battery_config_copy,
                                    market_config,
                                    result_folder,
                                )
                        except Exception as e:
                            print(f"Error for day {day}")
                            print(e)
                            print(traceback.format_exc())
                            continue
