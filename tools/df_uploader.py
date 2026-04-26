import json
import os
from datetime import datetime
import pandas as pd
import sqlalchemy
import sys


if os.path.exists("ZugangsdatenBRI.json"):
    with open("ZugangsdatenBRI.json") as f:
        zugang = json.load(f)
        host_name = zugang["host"]
        port_num = zugang["port"]
        user_name = zugang["user"]
        pw = zugang["password"]
        database = zugang["database"]
        last_update = zugang["last_update"]


def process_files_for_fcr_afrr_idc():
    cols = ["day", "batt_config", "batt_config_id", "mkt_config_id", "total_revenue",
            "afrrc_revenue", "afrre_revenue", "soc_balancing_revenue", "fcr_revenue", "idc_revenue"]
    df = pd.DataFrame(columns=cols)

    with open("batt_config.json", 'r') as file:
        batt_config = json.load(file)
    with open("mkt_config.json", 'r') as file:
        mkt_config = json.load(file)

    folder_path = "../results/"
    market_files = [f for f in os.listdir(folder_path) if f.endswith('.json')]
    market_files.sort()

    for json_file in market_files:
        file_path = os.path.join(folder_path, json_file)
        with open(file_path, "r") as file:
            json_data = json.load(file)

        batt_config_of_new_file = json_data["battery_config"]
        keys_to_remove = ["aging_costs", "marketable_power"]
        for key in keys_to_remove:
            batt_config_of_new_file.pop(key, None)
        try:
            batt_config_id = [key for key, value in batt_config.items() if value == batt_config_of_new_file][0]
        except IndexError:
            print("This battery configuration does not exist in the json")
            sys.exit()

        market_config_temp = {}
        for key, value in json_data["market_config"].items():
            temp_value = value.copy()
            temp_value.pop("marketable_power", None)
            for sub_key, sub_value in temp_value.items():
                if sub_value == 1.0:
                    temp_value[sub_key] = 1
                elif sub_value == 0.0:
                    temp_value[sub_key] = 0
            market_config_temp[key] = temp_value
        try:
            mkt_config_id = [key for key, value in mkt_config.items() if value == market_config_temp][0]
        except IndexError:
            print("This market configuration does not exist in the json")
            sys.exit()

        date = datetime.strptime(file_path.split('/')[-1][:10], "%Y-%m-%d").date()
        tuple = (date, f"{json_data['battery_config']['energy']}h/{json_data['battery_config']['cycle_limit']}c",
                 batt_config_id, mkt_config_id, json_data["results"]["daily_revenue"]["Total"],
                 json_data["results"]["daily_revenue"]["aFRR_Capacity"], json_data["results"]["daily_revenue"]["aFRR_Energy"],
                 None, json_data["results"]["daily_revenue"]["FCR"], json_data["results"]["daily_revenue"]["RI"])
        df.loc[len(df)] = tuple

    engine = sqlalchemy.create_engine(f"mysql+pymysql://{user_name}:{pw}@{host_name}:{port_num}/{database}")
    df.to_sql("fcr_afrr_idc", con=engine, if_exists="append", index=False)
    print(f"{len(df)} new records uploaded for battery config {df['batt_config'].iat[0]} and day {df['day'].iat[0]} to the table fcr_afrr_idc")

    return


process_files_for_fcr_afrr_idc()