import pandas as pd
import datetime
import os
import warnings
import json
import numpy as np
from markets.wholesale_market import WholesaleMarket
from markets.aFRR_market import aFRRmarket
from markets.FCR_market import FCRmarket
from tools.db_connector import DBConnector
import traceback
import concurrent.futures
import functools
from markets.id_rolling_intrinsic import simulate_period

# Suppress the specific FutureWarning
warnings.filterwarnings("ignore", category=FutureWarning)
warnings.filterwarnings("ignore", category=UserWarning, module="openpyxl")
try:
    from pandas.errors import SettingWithCopyWarning  # pandas >= 1.x
except Exception:
    try:
        from pandas.core.common import SettingWithCopyWarning  # older pandas fallback
    except Exception:
        SettingWithCopyWarning = None

if SettingWithCopyWarning is not None:
    warnings.filterwarnings("ignore", category=SettingWithCopyWarning)

"""
Module for analyzing battery revenue potential in various electricity markets individually,
including Day-Ahead, Intraday, FCR, and aFRR markets.
"""


def create_index(day, resolution):
    """
    Creates a datetime index for the specified day and resolution.

    Args:
        day (str): The day for which to create the index in 'YYYY-MM-DD' format.
        resolution (str): Time resolution, either 'h' for hourly or '15min' for 15-minute intervals.

    Returns:
        pd.DatetimeIndex: Timezone-aware datetime index for Berlin timezone.
    """
    # use the resolution input ('h' or '15min') to create the index
    if resolution == "h":
        index = pd.date_range(start=day + " 00:00", periods=24, freq="H").strftime(
            "%Y-%m-%d %H:%M"
        )
    elif resolution == "15min":
        index = pd.date_range(start=day + " 00:00", periods=96, freq="15min").strftime(
            "%Y-%m-%d %H:%M"
        )

    datetime_index = pd.DatetimeIndex(index)

    # make it berlin timezone; handle DST transition days robustly
    datetime_index = _tz_localize_berlin(datetime_index)

    return datetime_index


def _tz_localize_berlin(index_like):
    """
    Localize naive datetime index to Europe/Berlin with DST-safe defaults.
    """
    dt_index = pd.DatetimeIndex(index_like)
    return dt_index.tz_localize(
        "Europe/Berlin",
        ambiguous=False,
        nonexistent="shift_forward",
    )


def compare_blocks(day, afrr=None, fcr=None):
    """
    Compares revenue potential between aFRR and FCR markets for each 4-hour block.

    Args:
        day (str): The day for which to compare market blocks.
        afrr (aFRRmarket, optional): aFRR market object with pricing data. Defaults to None.
        fcr (FCRmarket, optional): FCR market object with pricing data. Defaults to None.

    Returns:
        pd.DataFrame: Comparison of profits for each market per block, with the selected market per block.
    """
    if fcr is None:
        block_profits_fcr = afrr.capacity_revenue * 0
        block_profits_afrr = afrr.capacity_revenue
    elif afrr is None:
        block_profits_fcr = fcr.revenue
        block_profits_afrr = fcr.revenue * 0
    else:
        block_profits_afrr = afrr.capacity_revenue
        block_profits_fcr = fcr.revenue

    # Compare the block profits of aFRR and FCR
    comparison = pd.DataFrame(
        {
            "block": range(6),
            "aFRR Profit": block_profits_afrr.values,
            "FCR Profit": block_profits_fcr.values,
            "Market": None,
            "Lost Revenue": None,
            "Wholesale Profit": None,
        }
    )

    # take the max profit from every blcok
    comparison["Max Profit"] = comparison.apply(
        lambda row: max(row["aFRR Profit"], row["FCR Profit"]), axis=1
    )

    afrr_blocks = []
    fcr_blocks = []
    for block in comparison.index:
        if comparison["Max Profit"][block] == comparison["aFRR Profit"][block]:
            comparison.loc[block, "Market"] = "aFRR"
            afrr_blocks.append(block)
            if afrr is not None:
                afrr.capacity_revenue[block] = comparison["aFRR Profit"][block]
            if fcr is not None:
                fcr.revenue[block] = 0
        else:
            comparison.loc[block, "Market"] = "FCR"
            fcr_blocks.append(block)
            if fcr is not None:
                fcr.revenue[block] = comparison["FCR Profit"][block]
            if afrr is not None:
                afrr.capacity_revenue[block] = 0

    # add two columns with timestamps in 4h blcoks
    # the first block starts at 00:00
    # the second block starts at 04:00
    # the third block starts at 08:00
    # the fourth block starts at 12:00
    # the fifth block starts at 16:00
    #
    # the sixth block starts at 20:00
    comparison["t_start"] = pd.date_range(start=day + " 00:00", periods=6, freq="4H")
    comparison["t_end"] = pd.date_range(start=day + " 04:00", periods=6, freq="4H")

    if afrr is not None:
        afrr.activated_blocks = afrr_blocks
    if fcr is not None:
        fcr.activated_blocks = fcr_blocks

    return comparison


def process_day(day, battery_config, market_config, result_folder_path, market_list, use_db=True):
    """
    Processes a single day, calculating potential revenues in each specified market.

    Args:
        day (str): The day to process in 'YYYY-MM-DD' format.
        battery_config (dict): Battery configuration with energy, power, and SOC parameters.
        market_config (dict): Configuration for each market with power share and capture rates.
        result_folder_path (str): Path to the folder where results should be saved.
        market_list (list): List of markets to analyze (e.g., 'DA', 'ID1', 'FCR', 'aFRR Capacity').

    Returns:
        pd.DataFrame or None: Results dataframe if successful, None on error.
    """
    db = DBConnector() if use_db else None
    raw_data_path = 'marketdata'


    results_df_day = pd.DataFrame()
    timestamp_of_15min = pd.date_range(start=day + ' 00:00', periods=96, freq='15min')
    results_df_day = pd.DataFrame(columns=market_list, index=timestamp_of_15min)
    
    #turn index in ttz aware for europe
    results_df_day.index = _tz_localize_berlin(results_df_day.index)
                
    wholesale = None
    for market in market_list:
        try:
                            
            if market == 'DA':
                if wholesale is None:
                    wholesale = WholesaleMarket(day, db)
                raw_data_path = 'marketdata'
                market_path = 'DA'
                folder_path = os.path.join(raw_data_path, market_path)

                prices = wholesale.get_da_prices(folder_path, day, db)

                wholesale.set_marketable_power_da(battery_config, market_config["DA"])
                executed_trades, daily_profit, results = (
                    wholesale.calculate_market_trades(
                        prices, battery_config, market_config["DA"]
                    )
                )

                # Extend the DataFrame to 15-minute resolution
                results = results.reindex(timestamp_of_15min)

                # Interpolate SOC values linearly between hourly SOC values
                results["soc"] = results["soc"].interpolate(method="linear")

                # Fill missing values for other columns using forward fill
                results = results.ffill()

                # fill with missing 3 values
                results = results.reindex(timestamp_of_15min, method="ffill")

                # divide the revnue by 4 to adjust now to quarter hour revenues
                results["revenue"] = results["revenue"] / 4

                results_df_day = pd.DataFrame()
                timestamp_of_15min = pd.date_range(
                    start=day + " 00:00", periods=96, freq="15min"
                )
                results_df_day = pd.DataFrame(
                    columns=["DA", "SOC"], index=timestamp_of_15min
                )

                # turn index in ttz aware for europe
                results_df_day.index = _tz_localize_berlin(results_df_day.index)

                results_df_day["SOC"] = results["soc"].values
                results_df_day["DA"] = (
                    results["revenue"].values * market_config["DA"]["capture_rate"]
                )

                # Convert dataframe to nested dictionary
                market_results = {}
                results_df_day.index = results_df_day.index.astype(str)
                for column in results_df_day.columns:
                    market_results[column] = results_df_day[column].to_dict()

                # create a table with the daily revenues for all markets
                daily_results = pd.DataFrame(
                    index=["DA", "Total"], columns=["daily_revenue"], data=0
                )
                daily_results.loc["DA"] = results_df_day["DA"].sum()
                daily_results.loc["Total"] = daily_results.sum()

                # Combine everything into a single JSON structure
                combined_data = {
                    "results": daily_results.to_dict(),
                    "battery_config": battery_config,
                    "market_config": market_config,
                    "market_results": market_results,
                }
                filename = f"{day}_results_DA.json"
                full_path = os.path.join(result_folder_path, filename)
                # Write to a JSON file
                with open(full_path, "w") as f:
                    json.dump(combined_data, f, indent=4, default=str)

            elif market in ("IDM15", "IDM60"):
                if wholesale is None:
                    wholesale = WholesaleMarket(day, db)

                wholesale.set_marketable_power_id1(battery_config, market_config[market])
                prices = wholesale.get_intraday_prices(market, day, db)
                executed_trades, daily_profit, results = (
                    wholesale.calculate_market_trades_fast(
                        prices, battery_config, market_config[market]
                    )
                )

                results_df_day = pd.DataFrame()
                timestamp_of_15min = pd.date_range(
                    start=day + " 00:00", periods=96, freq="15min"
                )
                results_df_day = pd.DataFrame(
                    columns=[market, "SOC"], index=timestamp_of_15min
                )

                results_df_day.index = _tz_localize_berlin(results_df_day.index)

                results_df_day["SOC"] = results["soc"].values
                results_df_day[market] = (
                    results["revenue"].values * market_config[market]["capture_rate"]
                )

                market_results = {}
                results_df_day.index = results_df_day.index.astype(str)
                for column in results_df_day.columns:
                    market_results[column] = results_df_day[column].to_dict()

                daily_results = pd.DataFrame(
                    index=[market, "Total"], columns=["daily_revenue"], data=0
                )
                daily_results.loc[market] = results_df_day[market].sum()
                daily_results.loc["Total"] = daily_results.sum()

                combined_data = {
                    "results": daily_results.to_dict(),
                    "battery_config": battery_config,
                    "market_config": market_config,
                    "market_results": market_results,
                }
                filename = f"{day}_results_{market}.json"
                full_path = os.path.join(result_folder_path, filename)
                with open(full_path, "w") as f:
                    json.dump(combined_data, f, indent=4, default=str)

            elif market == 'ID1':
                if wholesale is None:
                    wholesale = WholesaleMarket(day, db)
                
                raw_data_path = 'marketdata'
                market_path = 'ID1'
                folder_path = os.path.join(raw_data_path, market_path)

                wholesale.set_marketable_power_id1(battery_config, market_config["ID1"])
                prices = wholesale.get_id1_prices(folder_path, day, db)
                executed_trades, daily_profit, results = (
                    wholesale.calculate_market_trades(
                        prices, battery_config, market_config["ID1"]
                    )
                )

                results_df_day = pd.DataFrame()
                timestamp_of_15min = pd.date_range(
                    start=day + " 00:00", periods=96, freq="15min"
                )
                results_df_day = pd.DataFrame(
                    columns=["ID1", "SOC"], index=timestamp_of_15min
                )

                # turn index in ttz aware for europe
                results_df_day.index = _tz_localize_berlin(results_df_day.index)

                results_df_day["SOC"] = results["soc"].values
                results_df_day["ID1"] = (
                    results["revenue"].values * market_config["ID1"]["capture_rate"]
                )

                # Convert dataframe to nested dictionary
                market_results = {}
                results_df_day.index = results_df_day.index.astype(str)
                for column in results_df_day.columns:
                    market_results[column] = results_df_day[column].to_dict()

                # create a table with the daily revenues for all markets
                daily_results = pd.DataFrame(
                    index=["ID1", "Total"], columns=["daily_revenue"], data=0
                )
                daily_results.loc["ID1"] = results_df_day["ID1"].sum()
                daily_results.loc["Total"] = daily_results.sum()

                # Combine everything into a single JSON structure
                combined_data = {
                    "results": daily_results.to_dict(),
                    "battery_config": battery_config,
                    "market_config": market_config,
                    "market_results": market_results,
                }
                filename = f"{day}_results_ID1.json"
                full_path = os.path.join(result_folder_path, filename)
                # Write to a JSON file
                with open(full_path, "w") as f:
                    json.dump(combined_data, f, indent=4, default=str)

            elif market == 'IDA1':
                if wholesale is None:
                    wholesale = WholesaleMarket(day, db)
                
                wholesale.set_marketable_power_id1(battery_config, market_config['IDA1'])

                prices = wholesale.get_ida_prices(day, db)
                executed_trades, daily_profit, results = (
                    wholesale.calculate_market_trades(
                        prices, battery_config, market_config["IDA1"]
                    )
                )

                results_df_day = pd.DataFrame()
                timestamp_of_15min = pd.date_range(
                    start=day + " 00:00", periods=96, freq="15min"
                )
                results_df_day = pd.DataFrame(
                    columns=["IDA1", "SOC"], index=timestamp_of_15min
                )

                # turn index in ttz aware for europe
                results_df_day.index = _tz_localize_berlin(results_df_day.index)

                results_df_day["SOC"] = results["soc"].values
                results_df_day["IDA1"] = (
                    results["revenue"].values * market_config["IDA1"]["capture_rate"]
                )

                # Convert dataframe to nested dictionary
                market_results = {}
                results_df_day.index = results_df_day.index.astype(str)
                for column in results_df_day.columns:
                    market_results[column] = results_df_day[column].to_dict()

                # create a table with the daily revenues for all markets
                daily_results = pd.DataFrame(
                    index=["IDA1", "Total"], columns=["daily_revenue"], data=0
                )
                daily_results.loc["IDA1"] = results_df_day["IDA1"].sum()
                daily_results.loc["Total"] = daily_results.sum()

                # Combine everything into a single JSON structure
                combined_data = {
                    "results": daily_results.to_dict(),
                    "battery_config": battery_config,
                    "market_config": market_config,
                    "market_results": market_results,
                }
                filename = f"{day}_results_IDA1.json"
                full_path = os.path.join(result_folder_path, filename)
                # Write to a JSON file
                with open(full_path, "w") as f:
                    json.dump(combined_data, f, indent=4, default=str)

            elif market == 'FCR':
                fcr = FCRmarket(market_config['FCR'], battery_config, day, db)
                
                results_df_day = pd.DataFrame()
                timestamp_of_15min = pd.date_range(
                    start=day + " 00:00", periods=96, freq="15min"
                )
                results_df_day = pd.DataFrame(
                    columns=["FCR", "SOC"], index=timestamp_of_15min
                )

                # turn index in ttz aware for europe
                results_df_day.index = _tz_localize_berlin(results_df_day.index)

                # fcr_prices = get_fcr_prices(rf'marketdata\fcr_{day}.xlsx')
                raw_data_path = "marketdata"
                market_path = "FCR"
                folder_path = os.path.join(raw_data_path, market_path)
                fcr_prices = fcr.get_fcr_prices(day, folder_path, db)
                # calculate revenues for the whole day
                daily_profit, block_revenues = fcr.calculate_fcr_revenue(
                    fcr_prices,
                    market_config=market_config["FCR"],
                    battery_config=battery_config,
                )

                for t in results_df_day.index:
                    if t in pd.date_range(
                        start=pd.Timestamp(day + " 00:00", tz="Europe/Berlin"),
                        end=pd.Timestamp(day + " 03:45", tz="Europe/Berlin"),
                        freq="15Min",
                    ):
                        results_df_day.loc[t, "FCR"] = (
                            block_revenues[0]
                            / 16
                            * market_config["FCR"]["capture_rate"]
                        )
                    elif t in pd.date_range(
                        start=pd.Timestamp(day + " 04:00", tz="Europe/Berlin"),
                        end=pd.Timestamp(day + " 07:45", tz="Europe/Berlin"),
                        freq="15Min",
                    ):
                        results_df_day.loc[t, "FCR"] = (
                            block_revenues[1]
                            / 16
                            * market_config["FCR"]["capture_rate"]
                        )
                    elif t in pd.date_range(
                        start=pd.Timestamp(day + " 08:00", tz="Europe/Berlin"),
                        end=pd.Timestamp(day + " 11:45", tz="Europe/Berlin"),
                        freq="15Min",
                    ):
                        results_df_day.loc[t, "FCR"] = (
                            block_revenues[2]
                            / 16
                            * market_config["FCR"]["capture_rate"]
                        )
                    elif t in pd.date_range(
                        start=pd.Timestamp(day + " 12:00", tz="Europe/Berlin"),
                        end=pd.Timestamp(day + " 15:45", tz="Europe/Berlin"),
                        freq="15Min",
                    ):
                        results_df_day.loc[t, "FCR"] = (
                            block_revenues[3]
                            / 16
                            * market_config["FCR"]["capture_rate"]
                        )
                    elif t in pd.date_range(
                        start=pd.Timestamp(day + " 16:00", tz="Europe/Berlin"),
                        end=pd.Timestamp(day + " 19:45", tz="Europe/Berlin"),
                        freq="15Min",
                    ):
                        results_df_day.loc[t, "FCR"] = (
                            block_revenues[4]
                            / 16
                            * market_config["FCR"]["capture_rate"]
                        )
                    elif t in pd.date_range(
                        start=pd.Timestamp(day + " 20:00", tz="Europe/Berlin"),
                        end=pd.Timestamp(day + " 23:45", tz="Europe/Berlin"),
                        freq="15Min",
                    ):
                        results_df_day.loc[t, "FCR"] = (
                            block_revenues[5]
                            / 16
                            * market_config["FCR"]["capture_rate"]
                        )

                    results_df_day["SOC"] = 0.5

                # Convert dataframe to nested dictionary
                market_results = {}
                results_df_day.index = results_df_day.index.astype(str)
                for column in results_df_day.columns:
                    market_results[column] = results_df_day[column].to_dict()

                # create a table with the daily revenues for all markets
                daily_results = pd.DataFrame(
                    index=["FCR", "Total"], columns=["daily_revenue"], data=0
                )
                daily_results.loc["FCR"] = results_df_day["FCR"].sum()
                daily_results.loc["Total"] = daily_results.sum()

                # Combine everything into a single JSON structure
                combined_data = {
                    "results": daily_results.to_dict(),
                    "battery_config": battery_config,
                    "market_config": market_config,
                    "market_results": market_results,
                }
                filename = f"{day}_results_FCR.json"
                full_path = os.path.join(result_folder_path, filename)
                # Write to a JSON file
                with open(full_path, "w") as f:
                    json.dump(combined_data, f, indent=4, default=str)

            elif market == "IDC":
                results_df_day = pd.DataFrame()
                timestamp_of_15min = pd.date_range(
                    start=day + " 00:00", periods=96, freq="15min"
                )
                results_df_day = pd.DataFrame(
                    columns=["IDC", "SOC"], index=timestamp_of_15min
                )

                # turn index in ttz aware for europe
                results_df_day.index = _tz_localize_berlin(results_df_day.index)

                RI_config = market_config["IDC"]
                start_of_day = pd.Timestamp(day + " 00:00")
                end_of_day = start_of_day + pd.Timedelta(days=1)
                bucket_size = 15
                rto = battery_config["efficiency"] ** 2
                max_cycles = battery_config["cycle_limit"] * RI_config["cycle_share"]
                min_trades = 1
                min_soc = battery_config["startSOC"] - RI_config["capacity_share"] / 2
                max_soc = battery_config["startSOC"] + RI_config["capacity_share"] / 2
                c_rate = pd.Series(
                    index=results_df_day.index,
                    data=(RI_config["power_share"] * battery_config["power"])
                    / battery_config["energy"],
                )

                profit_ri, ri_trades, product_data = simulate_period(
                    start_of_day,
                    end_of_day,
                    threshold=0,
                    threshold_abs_min=0,
                    discount_rate=0,
                    bucket_size=bucket_size,
                    size=battery_config["energy"],
                    c_rate=c_rate,
                    roundtrip_eff=round(rto, 2),
                    max_cycles=max_cycles,
                    min_trades=min_trades,
                    min_soc=min_soc,
                    max_soc=max_soc,
                    soc_diff_afrr=0,
                    result_path=result_folder_path,
                )

                print(f"RI profit: {profit_ri}")
                # results_df_day['IDC'] = ri_trades['profit']

                extracted_ri_results = product_data[
                    ["net_charge", "net_discharge", "profit", "soc"]
                ]
                extracted_ri_results = extracted_ri_results.rename(
                    columns={"soc": "soc_ri"}
                )
                extracted_ri_results.index = create_index(day, "15min")

                total_result = pd.concat([extracted_ri_results], axis=1)
                total_result["soc"] = total_result["soc_ri"]

                results_df_day["SOC"] = total_result["soc"].values
                results_df_day["IDC"] = (
                    total_result["profit"].values * market_config["IDC"]["capture_rate"]
                )

                # Convert dataframe to nested dictionary
                market_results = {}
                results_df_day.index = results_df_day.index.astype(str)
                for column in results_df_day.columns:
                    market_results[column] = results_df_day[column].to_dict()

                # create a table with the daily revenues for all markets
                daily_results = pd.DataFrame(
                    index=["IDC", "Total"], columns=["daily_revenue"], data=0
                )
                daily_results.loc["IDC"] = results_df_day["IDC"].sum()
                daily_results.loc["Total"] = daily_results.sum()

                # Combine everything into a single JSON structure
                combined_data = {
                    "results": daily_results.to_dict(),
                    "battery_config": battery_config,
                    "market_config": market_config,
                    "market_results": market_results,
                }
                filename = f"{day}_results_IDC.json"
                full_path = os.path.join(result_folder_path, filename)
                # Write to a JSON file
                with open(full_path, "w") as f:
                    json.dump(combined_data, f, indent=4, default=str)

            elif market == "IMB":
                if wholesale is None:
                    wholesale = WholesaleMarket(day, db)

                raw_data_path = "marketdata"
                market_path = "IMB"
                folder_path = os.path.join(raw_data_path, market_path)

                wholesale.set_marketable_power_id1(battery_config, market_config["IMB"])
                prices = wholesale.get_imb_prices(folder_path, day)
                executed_trades, daily_profit, results = (
                    wholesale.calculate_market_trades(
                        prices, battery_config, market_config["IMB"]
                    )
                )

                results_df_day = pd.DataFrame()
                timestamp_of_15min = pd.date_range(
                    start=day + " 00:00", periods=96, freq="15min"
                )
                results_df_day = pd.DataFrame(
                    columns=["IMB", "SOC"], index=timestamp_of_15min
                )
                results_df_day.index = _tz_localize_berlin(results_df_day.index)

                results_df_day["SOC"] = results["soc"].values
                results_df_day["IMB"] = (
                    results["revenue"].values * market_config["IMB"]["capture_rate"]
                )

                market_results = {}
                results_df_day.index = results_df_day.index.astype(str)
                for column in results_df_day.columns:
                    market_results[column] = results_df_day[column].to_dict()

                daily_results = pd.DataFrame(
                    index=["IMB", "Total"], columns=["daily_revenue"], data=0
                )
                daily_results.loc["IMB"] = results_df_day["IMB"].sum()
                daily_results.loc["Total"] = daily_results.sum()

                combined_data = {
                    "results": daily_results.to_dict(),
                    "battery_config": battery_config,
                    "market_config": market_config,
                    "market_results": market_results,
                }
                filename = f"{day}_results_IMB.json"
                full_path = os.path.join(result_folder_path, filename)
                with open(full_path, "w") as f:
                    json.dump(combined_data, f, indent=4, default=str)

            elif market == 'aFRR':
                if use_db and db is None:
                    db = DBConnector()
                wholesale = WholesaleMarket(day, db)
                afrr = aFRRmarket(day, market_config['aFRR Capacity'], market_config['aFRR Energy'], battery_config, db)

                try:
                    results_df_day = pd.DataFrame()
                    timestamp_of_15min = pd.date_range(
                        start=day + " 00:00", periods=96, freq="15min"
                    )
                    results_df_day = pd.DataFrame(
                        columns=["aFRR Capacity", "aFRR Energy", "SOC"],
                        index=timestamp_of_15min,
                    )

                    # turn index in ttz aware for europe
                    results_df_day.index = _tz_localize_berlin(results_df_day.index)

                    # -----------------------------------------------------------------------------------------------------------------
                    # 0) t ID1 prices for SOC management
                    # -----------------------------------------------------------------------------------------------------------------
                    raw_data_path = "marketdata"
                    market_path = "ID1"
                    folder_path = os.path.join(raw_data_path, market_path)

                    # -----------------------------------------------------------------------------------------------------------------
                    # 1) COMPARE FCR vs aFRR Capacit
                    # -----------------------------------------------------------------------------------------------------------------
                    comparison = compare_blocks(day, afrr)
                    # -----------------------------------------------------------------------------------------------------------------
                    # 2) CALCULATE aFRR ENERGY REVENUES
                    # -----------------------------------------------------------------------------------------------------------------
                    energy_cfg = market_config.get("aFRR Energy", {})
                    run_afrr_energy = (
                        energy_cfg.get("capture_rate", 0) > 0
                        and energy_cfg.get("power_share", 0) > 0
                        and energy_cfg.get("capacity_share", 0) > 0
                    )
                    if run_afrr_energy:
                        afrr.set_marketable_power_afrr_energy(battery_config)
                        if energy_cfg.get("source") == "seps_damas":
                            result_afrr_energy = afrr.calculate_seps_damas_energy_revenue(day)
                        else:
                            id1_prices = wholesale.get_id1_prices(folder_path, day, db)
                            id1_prices["QuarterHour"] = range(1, len(id1_prices) + 1)

                            ida_prices = wholesale.get_ida_prices(day, db)
                            ida_prices["QuarterHour"] = range(1, len(ida_prices) + 1)

                            afrr.set_marketable_soc_afrr_energy(battery_config)
                            afrr.read_merit_order(db, day)
                            afrr.read_activation_data(day, db)
                            afrr.calculate_clearing_prices()

                            soc_marge = 0.05
                            result_afrr_energy = afrr.calculate_daily_revenue(
                                soc_marge, id1_prices, ida_prices, battery_config
                            )

                            result_afrr_energy["TotalRevenue"] = (
                                result_afrr_energy["afrr_revenue"].values
                                + result_afrr_energy["balancing_revenue"].values
                            )
                    else:
                        result_afrr_energy = pd.DataFrame(
                            index=range(1, 97),
                            data={
                                "afrr_revenue": 0.0,
                                "balancing_revenue": 0.0,
                                "soc": battery_config.get("startSOC", 0.5),
                            },
                        )

                    #afrr.save_clearing_data(day, result_folder_path)

                    # -----------------------------------------------------------------------------------------------------------------
                    # CREATE REULT DATAFRAME
                    # -----------------------------------------------------------------------------------------------------------------

                    # replace 'soc' by 'soc_da' in result_da_qh as column header
                    result_afrr_energy = result_afrr_energy.rename(
                        columns={"soc": "soc_afrr"}
                    )
                    result_afrr_energy.index = create_index(day, "15min")

                    total_result = pd.concat([result_afrr_energy], axis=1)
                    total_result["soc"] = total_result["soc_afrr"]

                    # total_result.to_csv(
                    #     os.path.join(result_folder_path, f"{day}_results.csv"), sep=";"
                    # )

                    for t in results_df_day.index:
                        if t in pd.date_range(
                            start=pd.Timestamp(day + " 00:00", tz="Europe/Berlin"),
                            end=pd.Timestamp(day + " 03:45", tz="Europe/Berlin"),
                            freq="15Min",
                        ):
                            results_df_day.loc[t, "aFRR Capacity"] = (
                                afrr.capacity_revenue[0]
                                / 16
                                * market_config["aFRR Capacity"]["capture_rate"]
                            )
                        elif t in pd.date_range(
                            start=pd.Timestamp(day + " 04:00", tz="Europe/Berlin"),
                            end=pd.Timestamp(day + " 07:45", tz="Europe/Berlin"),
                            freq="15Min",
                        ):
                            results_df_day.loc[t, "aFRR Capacity"] = (
                                afrr.capacity_revenue[1]
                                / 16
                                * market_config["aFRR Capacity"]["capture_rate"]
                            )
                        elif t in pd.date_range(
                            start=pd.Timestamp(day + " 08:00", tz="Europe/Berlin"),
                            end=pd.Timestamp(day + " 11:45", tz="Europe/Berlin"),
                            freq="15Min",
                        ):
                            results_df_day.loc[t, "aFRR Capacity"] = (
                                afrr.capacity_revenue[2]
                                / 16
                                * market_config["aFRR Capacity"]["capture_rate"]
                            )
                        elif t in pd.date_range(
                            start=pd.Timestamp(day + " 12:00", tz="Europe/Berlin"),
                            end=pd.Timestamp(day + " 15:45", tz="Europe/Berlin"),
                            freq="15Min",
                        ):
                            results_df_day.loc[t, "aFRR Capacity"] = (
                                afrr.capacity_revenue[3]
                                / 16
                                * market_config["aFRR Capacity"]["capture_rate"]
                            )
                        elif t in pd.date_range(
                            start=pd.Timestamp(day + " 16:00", tz="Europe/Berlin"),
                            end=pd.Timestamp(day + " 19:45", tz="Europe/Berlin"),
                            freq="15Min",
                        ):
                            results_df_day.loc[t, "aFRR Capacity"] = (
                                afrr.capacity_revenue[4]
                                / 16
                                * market_config["aFRR Capacity"]["capture_rate"]
                            )
                        elif t in pd.date_range(
                            start=pd.Timestamp(day + " 20:00", tz="Europe/Berlin"),
                            end=pd.Timestamp(day + " 23:45", tz="Europe/Berlin"),
                            freq="15Min",
                        ):
                            results_df_day.loc[t, "aFRR Capacity"] = (
                                afrr.capacity_revenue[5]
                                / 16
                                * market_config["aFRR Capacity"]["capture_rate"]
                            )

                    results_df_day["aFRR Energy"] = (
                        result_afrr_energy["afrr_revenue"].values
                        + result_afrr_energy["balancing_revenue"].values
                    ) * market_config["aFRR Energy"]["capture_rate"]
                    results_df_day["SOC"] = total_result["soc"].values

                    # Convert dataframe to nested dictionary
                    market_results = {}
                    results_df_day.index = results_df_day.index.astype(str)
                    for column in results_df_day.columns:
                        market_results[column] = results_df_day[column].to_dict()

                    # create a table with the daily revenues for all markets
                    daily_results = pd.DataFrame(
                        index=["aFRR_Capacity", "aFRR_Energy", "Total"],
                        columns=["daily_revenue"],
                        data=0,
                    )
                    daily_results.loc["aFRR_Capacity"] = results_df_day[
                        "aFRR Capacity"
                    ].sum()
                    daily_results.loc["aFRR_Energy"] = results_df_day[
                        "aFRR Energy"
                    ].sum()
                    daily_results.loc["Total"] = daily_results.sum()

                    # Combine everything into a single JSON structure
                    combined_data = {
                        "results": daily_results.to_dict(),
                        "battery_config": battery_config,
                        "market_config": market_config,
                        "market_results": market_results,
                    }
                    filename = f"{day}_results_aFRR.json"
                    full_path = os.path.join(result_folder_path, filename)
                    # Write to a JSON file
                    with open(full_path, "w") as f:
                        json.dump(combined_data, f, indent=4, default=str)

                except Exception as e:
                    print(f"Error in day {day}: {e}")
                    traceback.print_exc()
                          
         
        except Exception as e:
            print(f"Error in day {day} and market {market}: {e}")
            traceback.print_exc()
