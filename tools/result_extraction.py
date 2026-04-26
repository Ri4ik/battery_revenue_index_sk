import glob
import os
import json
import pandas as pd

def result_to_df(result_folder: str, battery_configs:[dict], market_configs:[dict]):
    result_files = glob.glob(os.path.join(result_folder, "*.json"))
    results_df = pd.DataFrame()
    print(len(result_files))
    for result_file in result_files:
        with open(result_file) as f:
            data = json.load(f)
            date = result_file.split("/")[-1].split("_")[0]
            results_dict = {}
            results_dict['bat_config'] = list(battery_configs.keys())[list(battery_configs.values()).index(data['battery_config'])]
            results_dict['mkt_config'] = list(market_configs.keys())[list(market_configs.values()).index(data['market_config'])]

            results_dict.update(data['results']['daily_revenue'])

            tmp_df = pd.DataFrame(results_dict, index=[date])

            results_df = pd.concat([results_df, tmp_df], ignore_index=True)

    return results_df

test_con = {"energy": 1,
        "power": 1,
        "cycle_limit": 1,
        "service_life": 10,
        "efficiency": 0.95,
        "maxSOC": 1,
        "minSOC": 0,
        "startSOC": 0.5,
        "DoD": 1,
        "costs": 250,
        "aging_costs": 68.4931506849315}
test_bat_config = {'1': test_con}
test_mkt = {
        "FCR": {
            "t_delivery": 4,
            "power_share": 0.5,
            "capture_rate": 1.0,
            "capacity_share": 1
        },
        "aFRR Capacity": {
            "t_delivery": 4,
            "power_share": 0.25,
            "capture_rate": 1.0,
            "capacity_share": 1,
            "marketable_power": 0.25
        },
        "aFRR Energy": {
            "t_delivery": 0.25,
            "power_share": 0.5,
            "capture_rate": 1.0,
            "capacity_share": 0.5,
            "init_position": 0.05,
            "cycle_share": 0.5,
            "marketable_power": "1     0.5\n2     0.5\n3     0.5\n4     0.5\n5     0.5\n     ... \n92    0.5\n93    0.5\n94    0.5\n95    0.5\n96    0.5\nLength: 96, dtype: float64"
        },
        "RI": {
            "power_share": 0.5,
            "capacity_share": 0.5,
            "capture_rate": 0.8,
            "cycle_share": 0.5
        }
    }

test_mkt_config = {1: test_mkt}
test = result_to_df('../results/'+'results_2025-03-10_14-14', test_bat_config, test_mkt_config)
print(test.head())


