"""
This module implements value stacking strategies combining FCR, aFRR, and Intraday Continuous (IDC) markets
for battery revenue optimization.
"""

import pandas as pd
import os
import datetime
import traceback
import concurrent.futures
import functools
import json

from markets.wholesale_market import WholesaleMarket
from markets.aFRR_market import aFRRmarket
from markets.FCR_market import FCRmarket
from markets.id_rolling_intrinsic import simulate_period
from tools.db_connector import DBConnector


def save_results(results_df, result_folder, name):
    """
    Saves results dataframe to a CSV file in the specified folder.

    Args:
        results_df (pd.DataFrame): Results dataframe to save.
        result_folder (str): Path to the folder where results should be saved.
        name (str): Base filename for the CSV file.
    """
    results_df.to_csv(os.path.join(result_folder, f"{name}.csv"))


def compare_blocks(day, afrr, fcr):
    """
    Compares revenue potential between aFRR and FCR markets for each 4-hour block.

    Args:
        day (str): The day for which to compare market blocks.
        afrr (aFRRmarket): aFRR market object with pricing data.
        fcr (FCRmarket): FCR market object with pricing data.

    Returns:
        pd.DataFrame: Comparison of profits for each market per block, with the selected market per block.
    """
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
            afrr.capacity_revenue[block] = comparison["aFRR Profit"][block]
            fcr.revenue[block] = 0
        else:
            comparison.loc[block, "Market"] = "FCR"
            fcr_blocks.append(block)
            fcr.revenue[block] = comparison["FCR Profit"][block]
            afrr.capacity_revenue[block] = 0

    comparison["t_start"] = pd.date_range(start=day + " 00:00", periods=6, freq="4H")
    comparison["t_end"] = pd.date_range(start=day + " 04:00", periods=6, freq="4H")

    fcr.activated_blocks = fcr_blocks
    afrr.activated_blocks = afrr_blocks

    return comparison


def process_day(day, battery_config, market_config, result_folder_path):
    """
    Processes a single day with the value stacking strategy, calculating revenues from FCR, aFRR, and IDC markets.

    Args:
        day (str): The day to process in 'YYYY-MM-DD' format.
        battery_config (dict): Battery configuration with energy, power, and SOC parameters.
        market_config (dict): Configuration for each market with power share and capacity share.
        result_folder_path (str): Path to the folder where results should be saved.

    Returns:
        pd.DataFrame or None: Results dataframe if successful, None on error.
    """
    db = DBConnector()

    afrr = aFRRmarket(
        day,
        market_config["aFRR Capacity"],
        market_config["aFRR Energy"],
        battery_config,
        db,
    )
    fcr = FCRmarket(market_config["FCR"], battery_config, day, db)
    wholesale = WholesaleMarket(day, db)

    try:
        results_df_day = pd.DataFrame()
        timestamp_of_15min = pd.date_range(
            start=day + " 00:00", periods=96, freq="15min"
        )
        results_df_day = pd.DataFrame(
            columns=["FCR", "aFRR Capacity", "aFRR Energy", "IDC", "SOC"],
            index=timestamp_of_15min,
        )

        # turn index in ttz aware for europe
        results_df_day.index = results_df_day.index.tz_localize("Europe/Berlin")

        # -----------------------------------------------------------------------------------------------------------------
        # 0) t ID1 prices for SOC management
        # -----------------------------------------------------------------------------------------------------------------
        raw_data_path = "marketdata"
        market_path = "ID1"
        folder_path = os.path.join(raw_data_path, market_path)

        id1_prices = wholesale.get_id1_prices(folder_path, day, db)
        id1_prices["QuarterHour"] = range(1, len(id1_prices) + 1)

        ida_prices = wholesale.get_ida_prices(day, db)
        ida_prices["QuarterHour"] = range(1, len(ida_prices) + 1)

        # -----------------------------------------------------------------------------------------------------------------
        # 1) COMPARE FCR vs aFRR Capacit
        # -----------------------------------------------------------------------------------------------------------------
        comparison = compare_blocks(day, afrr, fcr)

        # adjust battery SOC boundaries for rest services as we have to reserve it for aFRR Capacity
        # adjust_soc_boundaries(comparison, battery_config, afrr, fcr)

        # -----------------------------------------------------------------------------------------------------------------
        # 2) CALCULATE aFRR ENERGY REVENUES
        # -----------------------------------------------------------------------------------------------------------------

        afrr.set_marketable_power_afrr_energy(battery_config, fcr)
        afrr.set_marketable_soc_afrr_energy(battery_config, fcr, market_config["IDC"])
        # preprocessing 1: read merit order data
        afrr.read_merit_order(db, day)

        # preprocessing 2: calculate clearing prices for each 4s block

        # read second by second activation data
        afrr.read_activation_data(day, db)

        # calculate clearing prices and activated mean power for 4s intervalls
        afrr.calculate_clearing_prices()

        soc_marge = 0.05
        result_afrr_energy = afrr.calculate_daily_revenue(
            soc_marge, id1_prices, ida_prices, battery_config
        )

        last_soc_afrr = result_afrr_energy["soc"].iloc[-1]
        soc_diff_afrr = battery_config["startSOC"] - last_soc_afrr
        afrr_cycles = result_afrr_energy["soc"].diff().abs().sum() / 2
        # -----------------------------------------------------------------------------------------------------------------
        # 3) CALCULATE RI REVENUES
        # -----------------------------------------------------------------------------------------------------------------
        RI_config = market_config["IDC"]
        start_of_day = pd.Timestamp(day + " 00:00")
        end_of_day = start_of_day + pd.Timedelta(days=1)
        bucket_size = 15
        rto = battery_config["efficiency"] ** 2
        max_cycles = max(battery_config["cycle_limit"] - afrr_cycles, battery_config["cycle_limit"] * RI_config["cycle_share"])
        min_trades = 1
        min_soc = battery_config["startSOC"] - RI_config["capacity_share"] / 2
        max_soc = battery_config["startSOC"] + RI_config["capacity_share"] / 2
        prereserved_power = afrr.market_config_energy["marketable_power"]
        c_rate = (battery_config["power"] - prereserved_power) / battery_config[
            "energy"
        ]

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
            soc_diff_afrr=soc_diff_afrr,
            result_path=result_folder_path,
        )

        print(f"RI profit: {profit_ri}")
        results_df_day["IDC"] = ri_trades["profit"]

        result_afrr_energy["TotalRevenue"] = (
            result_afrr_energy["afrr_revenue"].values
            + result_afrr_energy["balancing_revenue"].values
        )
        daily_profit = result_afrr_energy["TotalRevenue"].sum()

        print(f"Daily profit for aFRR Energy on {day} is {daily_profit} EUR")

        # afrr.save_clearing_data(day, result_folder_path)

        # -----------------------------------------------------------------------------------------------------------------
        # CREATE REULT DATAFRAME
        # -----------------------------------------------------------------------------------------------------------------

        # replace 'soc' by 'soc_da' in result_da_qh as column header
        result_afrr_energy = result_afrr_energy.rename(columns={"soc": "soc_afrr"})
        result_afrr_energy.index = create_index(day, "15min")

        extracted_ri_results = product_data[
            ["net_charge", "net_discharge", "profit", "soc"]
        ]
        extracted_ri_results = extracted_ri_results.rename(columns={"soc": "soc_ri"})
        extracted_ri_results.index = create_index(day, "15min")

        total_result = pd.concat([result_afrr_energy, extracted_ri_results], axis=1)
        total_result["soc"] = (
            total_result["soc_afrr"]
            + total_result["soc_ri"]
            - battery_config["startSOC"]
        )

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
                results_df_day.loc[t, "FCR"] = (
                    fcr.revenue[0] / 16 * market_config["FCR"]["capture_rate"]
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
                results_df_day.loc[t, "FCR"] = (
                    fcr.revenue[1] / 16 * market_config["FCR"]["capture_rate"]
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
                results_df_day.loc[t, "FCR"] = (
                    fcr.revenue[2] / 16 * market_config["FCR"]["capture_rate"]
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
                results_df_day.loc[t, "FCR"] = (
                    fcr.revenue[3] / 16 * market_config["FCR"]["capture_rate"]
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
                results_df_day.loc[t, "FCR"] = (
                    fcr.revenue[4] / 16 * market_config["FCR"]["capture_rate"]
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
                results_df_day.loc[t, "FCR"] = (
                    fcr.revenue[5] / 16 * market_config["FCR"]["capture_rate"]
                )

        results_df_day["aFRR Energy"] = (
            result_afrr_energy["afrr_revenue"].values
            + result_afrr_energy["balancing_revenue"].values
        ) * market_config["aFRR Energy"]["capture_rate"]
        results_df_day["SOC"] = total_result["soc"].values
        results_df_day["IDC"] = total_result["profit"].values

        # -----------------------------------------------------------------------------------------------------------------
        # CREATE JSON
        # -----------------------------------------------------------------------------------------------------------------

        # Convert dataframe to nested dictionary
        market_results = {}
        results_df_day.index = results_df_day.index.astype(str)
        for column in results_df_day.columns:
            market_results[column] = results_df_day[column].to_dict()

        # create a table with the daily revenues for all markets
        daily_results = pd.DataFrame(
            index=["FCR", "aFRR_Capacity", "aFRR_Energy", "Total"],
            columns=["daily_revenue"],
            data=0,
        )
        daily_results.loc["IDC"] = profit_ri * market_config["IDC"]["capture_rate"]
        daily_results.loc["aFRR_Capacity"] = results_df_day["aFRR Capacity"].sum()
        daily_results.loc["aFRR_Energy"] = (
            results_df_day["aFRR Energy"].sum()
            * market_config["aFRR Energy"]["capture_rate"]
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

        filename = f"{day}_results_cross_market.json"
        full_path = os.path.join(result_folder_path, filename)
        # Write to a JSON file
        with open(full_path, "w") as f:
            json.dump(combined_data, f, indent=4, default=str)

    except Exception as e:
        print(f"Error in day {day}: {e}")
        traceback.print_exc()
        return None


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

    # make it berlin timezone
    datetime_index = datetime_index.tz_localize("Europe/Berlin")

    return datetime_index
