import copy
from loguru import logger
import pandas as pd
import numpy as np
import os
from pulp import LpProblem, LpVariable, lpSum, LpMaximize, PULP_CBC_CMD
import sys
from datetime import timedelta
import matplotlib.lines as mlines
import matplotlib.colors as mcolors
from matplotlib.patches import Circle, RegularPolygon
from matplotlib.collections import PatchCollection
import matplotlib.pyplot as plt
from tools.db_connector import DBConnector


# Get the current directory of the executing script (folder1)
current_dir = os.path.dirname(os.path.abspath(__file__))

# Construct the path to folder2
parallel_folder = os.path.join(current_dir, "..", "tools")

# Add folder2 to sys.path so we can import from it
sys.path.append(parallel_folder)

# START_OF_DAY = pd.Timestamp('2022-01-02 00:00:00', tz='Europe/Berlin')
# END_OF_DAY = pd.Timestamp('2022-01-03 00:00:00', tz='Europe/Berlin')
START_OF_DAY = pd.Timestamp("2024-01-01 00:00:00")
END_OF_DAY = pd.Timestamp("2024-12-31 23:59:00")
EXECUTION_TIME_START = (START_OF_DAY - timedelta(days=1)).replace(hour=16, minute=0)
EXECUTION_TIME_END = START_OF_DAY.replace(hour=23, minute=44, second=59)

BUCKET_SIZE = 1
C_RATE = 0.5
RTO = 0.95
MAX_CYCLES = 2
MIN_TRADES = 1


def get_prices_day(df, execution_time_start, day):
    """
    Filters and processes price data for a specific day.

    Args:
        df (pd.DataFrame): DataFrame containing price data.
        execution_time_start (datetime): Start time for execution.
        day (str): The day for which prices are being processed.

    Returns:
        pd.DataFrame: Processed DataFrame with prices for the specified day.
    """
    # set start_of_day to day at 00:00:00
    start_of_day = pd.to_datetime(day)

    # set hour and minute to 0 (europe/berlin time)
    start_of_day = start_of_day.replace(hour=0, minute=0)

    end_of_day = start_of_day

    end_of_day = end_of_day.replace(hour=23, minute=45)

    filtered_df = df[df["execution_time_start"] == execution_time_start]

    # filter so product is <= end_of_day
    filtered_df = filtered_df[filtered_df["product"] <= end_of_day]

    # remove column execution_time_start
    filtered_df = filtered_df.drop(columns=["execution_time_start"])

    # set index to product
    filtered_df.set_index("product", inplace=True)

    # set index to be all 15 minute intervals from start_of_day to end_of_day, filling missing values with NaN
    filtered_df = filtered_df.reindex(
        pd.date_range(start_of_day, end_of_day, freq="15min")
    )

    return filtered_df


def calculate_discounted_price(price, current_time, delivery_time, discount_rate):
    """
    Calculates the discounted price based on the time difference and discount rate.

    Args:
        price (float): Original price.
        current_time (datetime): Current time.
        delivery_time (datetime): Delivery time.
        discount_rate (float): Discount rate in percentage.

    Returns:
        float: Discounted price.
    """
    time_difference = (
        delivery_time - current_time
    ).total_seconds() / 3600  # difference in hours

    if time_difference <= 1:  # if less than one hour, return the original price
        return price

    if price < 0:
        discount_factor = np.exp((discount_rate / 100) * time_difference)
    else:
        discount_factor = np.exp(-(discount_rate / 100) * time_difference)

    return price * discount_factor


def run_qh_optimization(
    prices_qh,
    execution_time,
    cap,
    min_soc,
    max_soc,
    soc_diff_afrr,
    c_rate,
    roundtrip_eff,
    max_cycles,
    threshold,
    threshold_abs_min,
    discount_rate,
    prev_net_trades=pd.DataFrame(
        columns=["sum_buy", "sum_sell", "net_buy", "net_sell", "product"]
    ),
):
    """
    Runs quarter-hourly optimization for battery trading.

    Args:
        prices_qh (pd.DataFrame): Quarter-hourly price data.
        execution_time (datetime): Execution time.
        cap (float): Battery capacity.
        min_soc (float): Minimum state of charge.
        max_soc (float): Maximum state of charge.
        soc_diff_afrr (float): SOC difference for aFRR.
        c_rate (float): Charge/discharge rate.
        roundtrip_eff (float): Roundtrip efficiency.
        max_cycles (int): Maximum allowed cycles.
        threshold (float): Threshold for price adjustments.
        threshold_abs_min (float): Minimum absolute threshold.
        discount_rate (float): Discount rate in percentage.
        prev_net_trades (pd.DataFrame, optional): Previous net trades. Defaults to an empty DataFrame.

    Returns:
        tuple: Results DataFrame, trades DataFrame, and objective value.
    """
    # All code is initially based on the rolling intrinsic algorithm developed at the KIT

    # copy prices_qh
    prices_qh_adj = prices_qh.copy()

    # loop through prices_qh and adjust prices
    for i in prices_qh_adj.index:
        if not pd.isna(prices_qh_adj.loc[i, "price"]):
            prices_qh_adj.loc[i, "price"] = calculate_discounted_price(
                prices_qh_adj.loc[i, "price"], execution_time, i, discount_rate
            )

            # round prices to 2 decimals
            prices_qh_adj.loc[i, "price"] = round(prices_qh_adj.loc[i, "price"], 2)

    # copy prices_qh
    prices_qh_adj_buy = prices_qh.copy()

    # loop through prices_qh and adjust prices
    for i in prices_qh_adj_buy.index:
        if not pd.isna(prices_qh_adj_buy.loc[i, "price"]):
            prices_qh_adj_buy.loc[i, "price"] = calculate_discounted_price(
                prices_qh_adj_buy.loc[i, "price"], execution_time, i, -discount_rate
            )

            # round prices to 2 decimals
            prices_qh_adj_buy.loc[i, "price"] = round(
                prices_qh_adj_buy.loc[i, "price"], 2
            )

    # Round prices to 2 decimals
    prices_qh["price"] = round(prices_qh_adj["price"], 2)

    # Setup optimization problem
    m_battery = LpProblem("battery", LpMaximize)

    c_rate.index = prices_qh.index

    # check if prices_qh is a float
    if isinstance(prices_qh, float):
        print("prices_qh is a float")

    # Setup variables
    current_buy_qh = LpVariable.dicts("current_buy_qh", prices_qh.index, lowBound=0)
    current_sell_qh = LpVariable.dicts("current_sell_qh", prices_qh.index, lowBound=0)
    battery_soc = LpVariable.dicts("battery_soc", prices_qh.index, lowBound=0)
    net_buy = LpVariable.dicts("net_buy", prices_qh.index, lowBound=0)
    net_sell = LpVariable.dicts("net_sell", prices_qh.index, lowBound=0)
    charge_sign = LpVariable.dicts(
        "charge_sign", prices_qh.index, lowBound=0, cat="Binary"
    )

    # Auxiliary variables
    z = LpVariable.dicts("z", prices_qh.index, lowBound=0)
    w = LpVariable.dicts("w", prices_qh.index, lowBound=0)

    M = 100
    e = 0.01

    # Objective functions
    # Adjusted objective component or cases where previous trades < e
    adjusted_obj = [
        (
            (
                current_sell_qh[i]
                * (
                    prices_qh_adj.loc[i, "price"]
                    - max(
                        abs((threshold / 100) * abs(prices_qh.loc[i, "price"])),
                        threshold_abs_min,
                    )
                    / 2
                    - e
                )
            )
            - (
                current_buy_qh[i]
                * (
                    prices_qh_adj_buy.loc[i, "price"]
                    + max(
                        abs((threshold / 100) * abs(prices_qh.loc[i, "price"])),
                        threshold_abs_min,
                    )
                    / 2
                    + e
                )
            )
        )
        * 1.0
        / 4.0
        for i in prices_qh.index
        if not pd.isna(prices_qh.loc[i, "price"])
        and (
            prev_net_trades.loc[i, "net_buy"] < e
            and prev_net_trades.loc[i, "net_sell"] < e
        )
    ]

    # Original objective for cases where previous trades >= e
    original_obj = [
        (
            current_sell_qh[i] * (prices_qh.loc[i, "price"] - e)
            - current_buy_qh[i] * prices_qh.loc[i, "price"]
        )
        * 1.0
        / 4.0
        for i in prices_qh.index
        if not pd.isna(prices_qh.loc[i, "price"])
        and (
            prev_net_trades.loc[i, "net_buy"] >= e
            or prev_net_trades.loc[i, "net_sell"] >= e
        )
    ]

    # Combine the two vectors and set as objective
    m_battery += lpSum(original_obj + adjusted_obj)
    # m_battery += lpSum(original_obj)

    # Constraints
    previous_index = prices_qh.index[0]

    efficiency = roundtrip_eff**0.5

    for i in prices_qh.index[1:]:
        m_battery += (
            battery_soc[i]
            == (
                battery_soc[previous_index]
                + (net_buy[previous_index] * efficiency * (1 / cap) * (1.0 / 4.0))
                - (
                    net_sell[previous_index]
                    * (1 / efficiency)
                    * (1 / cap)
                    * (1.0 / 4.0)
                )
            ),
            f"BatteryBalance_{i}",
        )
        previous_index = i

    m_battery += (
        (
            battery_soc[prices_qh.index[-1]]
            + (net_buy[prices_qh.index[-1]] * efficiency * (1 / cap) * (1.0 / 4.0))
            - (
                net_sell[prices_qh.index[-1]]
                * (1 / efficiency)
                * (1 / cap)
                * (1.0 / 4.0)
            )
        )
        == (max_soc + min_soc) / 2 + soc_diff_afrr,
        "BatteryBalanceFinal",
    )

    m_battery += (
        battery_soc[prices_qh.index[0]] == (max_soc + min_soc) / 2,
        "InitialBatterySOC",
    )
    m_battery += (
        battery_soc[prices_qh.index[-1]] == (max_soc + min_soc) / 2,
        "EndBatterySOC",
    )

    for i in prices_qh.index:
        # Handling NaN values by setting buy and sell quantities to 0
        if pd.isna(prices_qh.loc[i, "price"]):
            m_battery += current_buy_qh[i] == 0, f"NaNBuy_{i}"
            m_battery += current_sell_qh[i] == 0, f"NaNSell_{i}"

        m_battery += battery_soc[i] <= max_soc, f"UpperCap_{i}"
        m_battery += battery_soc[i] >= min_soc, f"LowerCap_{i}"
        m_battery += net_buy[i] <= cap * c_rate[i], f"BuyRate_{i}"
        m_battery += net_sell[i] <= cap * c_rate[i], f"SellRate_{i}"
        m_battery += (
            net_sell[i] * 1.0 / efficiency / 4.0 <= battery_soc[i],
            f"SellVsSOC_{i}",
        )

        # big M constraints for net buy and sell
        m_battery += net_buy[i] <= M * charge_sign[i], f"NetBuyBigM_{i}"
        m_battery += net_sell[i] <= M * (1 - charge_sign[i]), f"NetSellBigM_{i}"

        m_battery += z[i] <= charge_sign[i] * M, f"ZUpper_{i}"
        m_battery += z[i] <= net_buy[i], f"ZNetBuy_{i}"
        m_battery += z[i] >= net_buy[i] - (1 - charge_sign[i]) * M, f"ZLower_{i}"
        m_battery += z[i] >= 0, f"ZNonNeg_{i}"

        m_battery += w[i] <= (1 - charge_sign[i]) * M, f"WUpper_{i}"
        m_battery += w[i] <= net_sell[i], f"WNetSell_{i}"
        m_battery += w[i] >= net_sell[i] - charge_sign[i] * M, f"WLower_{i}"
        m_battery += w[i] >= 0, f"WNonNeg_{i}"

        m_battery += (
            z[i] - w[i]
            == current_buy_qh[i]
            + prev_net_trades.loc[i, "net_buy"]
            - current_sell_qh[i]
            - prev_net_trades.loc[i, "net_sell"],
            f"Netting_{i}",
        )

    m_battery += (
        lpSum(
            net_buy[i] * efficiency * 1.0 / 4.0
            + net_sell[i] * 1 / efficiency * 1.0 / 4.0
            for i in prices_qh.index
        )
        <= max_cycles * (2 * cap),
        "MaxCycles",
    )

    # Solve the problem
    # m_battery.solve(GUROBI(msg=0))

    # Solve the problem
    solver = PULP_CBC_CMD(msg=0, timeLimit=10)
    m_battery.solve(solver)

    # write lp file
    # m_battery.writeLP("battery.lp")

    results = pd.DataFrame(
        columns=["current_buy_qh", "current_sell_qh", "battery_soc"],
        index=prices_qh.index,
    )

    trades = pd.DataFrame(
        columns=["execution_time", "side", "quantity", "price", "product", "profit"]
    )

    for i in prices_qh.index:
        if current_buy_qh[i].value() and current_buy_qh[i].value() > 0:
            # create buy trade
            new_trade = {
                "execution_time": [execution_time],
                "side": ["buy"],
                "quantity": [current_buy_qh[i].value()],
                "price": [prices_qh.loc[i, "price"]],
                "product": [i],
                "profit": [-current_buy_qh[i].value() * prices_qh.loc[i, "price"] / 4],
            }
            # append new trade using concat
            if len(trades) > 0:
                trades = pd.concat([trades, pd.DataFrame(new_trade)], ignore_index=True)
            else:
                trades = pd.DataFrame(new_trade)

        if current_sell_qh[i].value() and current_sell_qh[i].value() > 0:
            # create sell trade
            new_trade = {
                "execution_time": [execution_time],
                "side": ["sell"],
                "quantity": [current_sell_qh[i].value()],
                "price": [prices_qh.loc[i, "price"]],
                "product": [i],
                "profit": [current_sell_qh[i].value() * prices_qh.loc[i, "price"] / 4],
            }
            # append new trade using concat
            if len(trades) > 0:
                trades = pd.concat([trades, pd.DataFrame(new_trade)], ignore_index=True)
            else:
                trades = pd.DataFrame(new_trade)

    for i in prices_qh.index:
        results.loc[i, "current_buy_qh"] = current_buy_qh[i].value()
        results.loc[i, "current_sell_qh"] = current_sell_qh[i].value()
        results.loc[i, "net_buy"] = net_buy[i].value()
        results.loc[i, "net_sell"] = net_sell[i].value()
        results.loc[i, "charge_sign"] = charge_sign[i].value()
        results.loc[i, "battery_soc"] = battery_soc[i].value()

    return results, trades, m_battery.objective.value()


def get_net_trades(trades, end_date):
    """
    Calculates net trades for each product based on executed trades.

    Args:
        trades (pd.DataFrame): DataFrame containing executed trades.
        end_date (datetime): End date for the trades.

    Returns:
        pd.DataFrame: DataFrame with net trades for each product.
    """
    # create a new empty dataframe with the columns "net_buy" and "net_sell"
    net_trades = pd.DataFrame(
        columns=["sum_buy", "sum_sell", "net_buy", "net_sell", "product"]
    )

    # based on trades, calculate the net buy and net sell for each product
    for product in trades["product"].unique():
        product_trades = trades[trades["product"] == product]
        sum_buy = product_trades[product_trades["side"] == "buy"]["quantity"].sum()
        sum_sell = product_trades[product_trades["side"] == "sell"]["quantity"].sum()
        if len(net_trades) > 0:
            net_trades = pd.concat(
                [
                    net_trades,
                    pd.DataFrame(
                        [[sum_buy, sum_sell, product]],
                        columns=["sum_buy", "sum_sell", "product"],
                    ),
                ],
                ignore_index=True,
            )
        else:
            net_trades = pd.DataFrame(
                [[sum_buy, sum_sell, product]],
                columns=["sum_buy", "sum_sell", "product"],
            )

    # add the columns "net_buy" and "net_sell" to net_trades, net_buy = sum_buy - sum_sell (if > 0), net_sell = sum_sell - sum_buy (if > 0)
    net_trades["net_buy"] = net_trades["sum_buy"] - net_trades["sum_sell"]
    net_trades["net_sell"] = net_trades["sum_sell"] - net_trades["sum_buy"]

    # remove values < 0 for net_buy and net_sell
    net_trades.loc[net_trades["net_buy"] < 0, "net_buy"] = 0
    net_trades.loc[net_trades["net_sell"] < 0, "net_sell"] = 0

    # set column product to index
    net_trades = net_trades.set_index("product")

    # set start_of_day to end_date minus 1 day
    start_of_day = pd.to_datetime(end_date) - pd.Timedelta(hours=2)

    # set hour and minute to 0 (europe/berlin time)
    start_of_day = start_of_day.replace(hour=0, minute=0)
    end_of_day = start_of_day
    end_of_day = end_of_day.replace(hour=23, minute=45)

    net_trades = net_trades.reindex(
        pd.date_range(start_of_day, end_of_day, freq="15min")
    )

    # fill NaN values with 0
    net_trades = net_trades.fillna(0)

    # set index to datetime
    net_trades.index = pd.to_datetime(net_trades.index)

    # return the net_trades dataframe
    return net_trades


def extract_local_trades(
    df_in, execution_time_start, execution_time_end, end_date, min_trades
):
    """
    Extracts and processes local trades based on execution time and minimum trade count.

    Args:
        df_in (pd.DataFrame): Input DataFrame with trade data.
        execution_time_start (datetime): Start time for execution.
        execution_time_end (datetime): End time for execution.
        end_date (datetime): End date for the trades.
        min_trades (int): Minimum number of trades required.

    Returns:
        pd.DataFrame: Processed DataFrame with local trades.
    """
    # set start_of_day to end_date minus 1 day
    start_of_day = pd.to_datetime(end_date) - pd.Timedelta(hours=2)

    # set hour and minute to 0 (europe/berlin time)
    start_of_day = start_of_day.replace(hour=0, minute=0)

    end_of_day = start_of_day

    end_of_day = end_of_day.replace(hour=23, minute=45)

    # Filter by Executiontime
    df = copy.deepcopy(df_in)
    df_filtered = df[
        (df["ExecutionTime"] >= execution_time_start)
        & (df["ExecutionTime"] <= execution_time_end)
    ]
    df_cleaned = df_filtered.drop("ExecutionTime", axis=1)
    df_renamed = df_cleaned.rename({"DeliveryStart": "product"}, axis=1)
    # Remove all groups with fewer entries than min_trades
    groups = df_renamed.groupby("product").filter(lambda x: len(x) >= min_trades)
    # Calculate weighted average
    result = groups.groupby("product").apply(
        lambda x: (x["Price"] * x["Volume"]).sum() / (x["Volume"]).sum(),
        include_groups=False,
    )
    # Cast to dataframe and set column names
    if result.empty:
        df_out = pd.DataFrame(
            0,
            index=pd.date_range(start_of_day, end_of_day, freq="15min"),
            columns=["price"],
        )
    else:
        df_out = result.to_frame()
        df_out.columns = ["price"]
        df_out = df_out.reindex(pd.date_range(start_of_day, end_of_day, freq="15min"))

    return df_out


def simulate_period(
    start_day,
    end_day,
    threshold,
    threshold_abs_min,
    discount_rate,
    bucket_size,
    size,
    c_rate,
    roundtrip_eff,
    max_cycles,
    min_trades,
    min_soc,
    max_soc,
    soc_diff_afrr,
    result_path,
):
    """
    Simulates the rolling intrinsic trading strategy over a specified period.

    Args:
        start_day (datetime): Start day of the simulation.
        end_day (datetime): End day of the simulation.
        threshold (float): Threshold for price adjustments.
        threshold_abs_min (float): Minimum absolute threshold.
        discount_rate (float): Discount rate in percentage.
        bucket_size (int): Size of the trading bucket in minutes.
        size (float): Battery capacity.
        c_rate (float): Charge/discharge rate.
        roundtrip_eff (float): Roundtrip efficiency.
        max_cycles (int): Maximum allowed cycles.
        min_trades (int): Minimum number of trades required.
        min_soc (float): Minimum state of charge.
        max_soc (float): Maximum state of charge.
        soc_diff_afrr (float): SOC difference for aFRR.
        result_path (str): Path to save the simulation results.

    Returns:
        tuple: Total profit, profits DataFrame, and per-product data.
    """
    log_message = (
        "Running Rolling intrinsic QH with the following parameters:\n"
        "Start Day: {start_day}\n"
        "End Day: {end_day}\n"
        "Threshold: {threshold}\n"
        "Threshold Absolute Minimum: {threshold_abs_min}\n"
        "Discount Rate: {discount_rate}\n"
        "Bucket Size: {bucket_size}\n"
        "C Rate: {c_rate}\n"
        "Roundtrip Efficiency: {roundtrip_eff}\n"
        "Max Cycles: {max_cycles}\n"
        "Min Trades: {min_trades}"
    ).format(
        start_day=start_day,
        end_day=end_day,
        threshold=threshold,
        threshold_abs_min=threshold_abs_min,
        discount_rate=discount_rate,
        bucket_size=bucket_size,
        c_rate=c_rate,
        roundtrip_eff=roundtrip_eff,
        max_cycles=max_cycles,
        min_trades=min_trades,
    )

    logger.info(log_message)

    db = DBConnector()

    path = os.path.join(
        result_path,
        "quarterhourly",
        "bs"
        + str(bucket_size)
        + "cr"
        + str(c_rate.iloc[0])
        + "rto"
        + str(roundtrip_eff)
        + "mc"
        + str(max_cycles)
        + "mt"
        + str(min_trades),
    )

    tradepath = os.path.join(path, "trades")

    # create directory if it doesn't exist
    if not os.path.exists(path):
        os.makedirs(path)

    if not os.path.exists(tradepath):
        os.makedirs(tradepath)

    transaction_path = os.path.join("marketdata", "IDC")

    # create directory if it doesn't exist
    if not os.path.exists(transaction_path):
        os.makedirs(transaction_path)

    # initialize data for current day
    profits = pd.DataFrame(columns=["day", "profit", "cycles"])
    total_profit = 0
    current_day = start_day
    current_cycles = 0
    net_trades = pd.DataFrame(
        columns=["sum_buy", "sum_sell", "net_buy", "net_sell", "product"]
    )

    while current_day < end_day:
        current_day = current_day.replace(hour=0, minute=0, second=0, microsecond=0)

        print("current_day: ", current_day)

        all_trades = pd.DataFrame(
            columns=["execution_time", "side", "quantity", "price", "product", "profit"]
        )

        # set trading_start to current_day minus 3 hours
        trading_start = current_day - pd.Timedelta(hours=8)
        # set trading_end to current_day plus 1 day
        trading_end = current_day + pd.Timedelta(days=1)

        print("trading_start: ", trading_start)
        print("trading_end: ", trading_end)

        # set execution_time_start to trading_start
        execution_time_start = trading_start
        # set execution_time_end to trading_start plus 15 minutes
        execution_time_end = trading_start + pd.Timedelta(minutes=bucket_size)

        # calculate number of days until end_day
        days_left = (end_day - current_day).days

        allowed_cycles = max_cycles / 365 + (
            (max_cycles / 365 * (365 - days_left)) - current_cycles
        )

        # allowed_cycles = (500 - current_cycles) / days_left

        print("Days left: ", days_left)
        print("Current cycles: ", current_cycles)
        print("Allowed cycles: ", allowed_cycles)

        # access db only if csv file for this day does not exist
        if not os.path.exists(
            os.path.join(
                transaction_path,
                "transactions_" + start_day.strftime("%Y-%m-%d") + ".csv",
            )
        ):
            daily_trades = db.get_transaction_data("BUY", trading_start, trading_end)
            daily_trades.to_csv(
                os.path.join(
                    transaction_path,
                    "transactions_" + start_day.strftime("%Y-%m-%d") + ".csv",
                ),
                index=False,
            )
        else:
            daily_trades = pd.read_csv(
                os.path.join(
                    transaction_path,
                    "transactions_" + start_day.strftime("%Y-%m-%d") + ".csv",
                )
            )
            # format columns "ExecutionTime" and "DeliveryStart" to datetime
            daily_trades["ExecutionTime"] = pd.to_datetime(
                daily_trades["ExecutionTime"]
            )
            daily_trades["DeliveryStart"] = pd.to_datetime(
                daily_trades["DeliveryStart"]
            )

        while execution_time_end < trading_end:
            print(f"End of current bucket: {execution_time_end}")
            # get average price for BUY orders
            vwap = extract_local_trades(
                copy.deepcopy(daily_trades),
                execution_time_start,
                execution_time_end,
                trading_end,
                min_trades,
            )
            # min_soc = pd.DataFrame({'level':np.ones(96) * min_cap}, index=vwap.index)
            # max_soc = pd.DataFrame({'level':np.ones(96) * max_cap}, index=vwap.index)
            # vwap = get_closest_prices(execution_time_start, trading_end)

            net_trades = get_net_trades(all_trades, trading_end)

            # if all vwap["price"] are NaN
            if vwap["price"].isnull().all():
                print("No trades in this quarter hour")
                execution_time_start = execution_time_end
                execution_time_end = execution_time_start + pd.Timedelta(
                    minutes=bucket_size
                )
                continue
            else:
                try:
                    results, trades, profit = run_qh_optimization(
                        vwap,
                        execution_time_start,
                        size,
                        min_soc,
                        max_soc,
                        soc_diff_afrr,
                        c_rate,
                        roundtrip_eff,
                        max_cycles,
                        threshold,
                        threshold_abs_min,
                        discount_rate,
                        net_trades,
                    )
                    # append trades to all_trades using concat
                    if len(all_trades.index) > 0 and len(trades.index) > 0:
                        all_trades = pd.concat([all_trades, trades])
                    elif len(trades.index) > 0:
                        all_trades = trades
                except ValueError:
                    print("Error in optimization")
                    print("execution_time_start: ", execution_time_start)
                    execution_time_start = execution_time_end
                    execution_time_end = execution_time_start + pd.Timedelta(
                        minutes=bucket_size
                    )

                    continue

            execution_time_start = execution_time_end
            execution_time_end = execution_time_start + pd.Timedelta(
                minutes=bucket_size
            )

        # calculate daily_profit as sum of all_trades["profit"]
        daily_profit = all_trades["profit"].sum()

        current_cycles += net_trades["net_buy"].sum() / 4.0 * roundtrip_eff**0.5

        # save trades
        # all_trades.to_csv(
        #    os.path.join(tradepath,
        #    "trades_" + current_day.strftime("%Y-%m-%d") + ".csv"),
        #    index=False,
        # )
        # append daily_profit to profits.csv using concat
        if len(profits) > 0:
            profits = pd.concat(
                [
                    profits,
                    pd.DataFrame(
                        [[current_day, daily_profit, current_cycles]],
                        columns=["day", "profit", "cycles"],
                    ),
                ]
            )
        else:
            profits = pd.DataFrame(
                [[current_day, daily_profit, current_cycles]],
                columns=["day", "profit", "cycles"],
            )

        profits_db = pd.DataFrame(
            [
                [
                    current_day,
                    daily_profit,
                    net_trades["net_buy"].sum() / 4.0 * roundtrip_eff**0.5,
                ]
            ],
            columns=["day", "profit", "cycles"],
        )

        # add column threshold, threshold_abs and discount_rate to profits_db
        profits_db["type_freq"] = "QH"
        profits_db["max_cycles"] = max_cycles
        profits_db["bucket_size"] = bucket_size
        profits_db["rto"] = roundtrip_eff
        profits_db["c_rate"] = c_rate
        profits_db["min_trades"] = min_trades

        # save profits.csv
        # profits.to_csv(os.path.join(path, "profit.csv"), index=False)

        daily_trades["searchkey"] = list(zip(daily_trades["DeliveryStart"]))
        trades_by_product = daily_trades.groupby("searchkey")

        trades_rib = all_trades.copy()
        trades_rib.round(2).head()
        per_product_data = derive_soc_from_trades(
            trades_rib,
            roundtrip_eff**0.5,
            current_day,
            execution_time_end,
            (max_soc + min_soc) / 2,
            cap=size,
        )
        products = pd.Series(
            pd.to_datetime(trades_rib["product"].unique())
        ).sort_values()
        trades_rib_by_product = trades_rib.groupby("product")

        # save per product data
        # per_product_data.to_csv(
        #    os.path.join(tradepath,
        #    "RI_product_data_" + current_day.strftime("%Y-%m-%d") + ".csv"),
        #    index=True,
        #    sep=';'
        # )

        (
            plotting_prices,
            plotting_sides,
            background,
            prices,
            plotting_prices_backup,
            prices_and_sides,
        ) = derive_plotting_data(
            trades_by_product,
            trades_rib_by_product,
            current_day,
            execution_time_end,
            trading_start,
        )

        # plot_results(plotting_prices, plotting_sides, background, prices, plotting_prices_backup, prices_and_sides, per_product_data, daily_profit, current_day, tradepath)

        # set current day to current_day plus 1 day
        current_day = current_day + pd.Timedelta(days=1) + pd.Timedelta(hours=2)

        total_profit += daily_profit

    return total_profit, profits_db, per_product_data


def plot_results(
    plotting_prices,
    plotting_sides,
    background,
    prices,
    plotting_prices_backup,
    prices_and_sides,
    per_product_data,
    profit,
    current_day,
    result_path,
):
    """
    Plots the results of the rolling intrinsic trading strategy.

    Args:
        plotting_prices (pd.DataFrame): DataFrame with plotting prices.
        plotting_sides (pd.DataFrame): DataFrame with plotting sides.
        background (pd.DataFrame): Background data for the plot.
        prices (list): List of prices.
        plotting_prices_backup (pd.DataFrame): Backup of plotting prices.
        prices_and_sides (dict): Dictionary with prices and sides.
        per_product_data (pd.DataFrame): DataFrame with per-product data.
        profit (float): Profit for the current day.
        current_day (datetime): Current day of the simulation.
        result_path (str): Path to save the plot.
    """
    # Plot heatmap
    start = pd.to_datetime(START_OF_DAY, utc=True)
    end = pd.to_datetime(END_OF_DAY, utc=True)
    exec_start = pd.to_datetime(EXECUTION_TIME_START, utc=True)
    exec_end = pd.to_datetime(EXECUTION_TIME_END, utc=True)

    figure = plt.figure(figsize=(15.5, 10))
    ax1 = figure.add_subplot(1, 100, (1, 91))
    ax2 = figure.add_subplot(1, 100, (93, 97), sharey=ax1)
    ax3 = figure.add_subplot(1, 100, (99, 100))

    bg = ax1
    ax1.set_ylim([-0.5, background.shape[0] - 1 + 0.5])
    ax1.grid(axis="y", linestyle="dashed", zorder=2.5)
    ax1.set_ylabel("Products", fontsize=15)
    ax1.set_xticks(
        np.arange(0, len(background.columns), 1)[::4],
        [
            x.time().strftime("%H:%M")
            if x > start
            else "D-1  " + x.time().strftime("%H:%M")
            for x in background.columns
        ][::4],
        rotation=45,
        ha="right",
    )
    ax1.set_yticks(
        background.index,
        [
            f"{x.time().strftime('%H:%M')} - {(x + timedelta(minutes=15)).time().strftime('%H:%M')}"
            for x in plotting_prices_backup.index
        ],
    )
    ax1.tick_params(axis="x", which="major", labelsize=10)
    ax1.tick_params(axis="y", which="major", labelsize=5)
    ax1.set_xlim([-1, background.shape[1] + 1])

    patches = []
    for key in prices_and_sides:
        if prices_and_sides[key]["side"] == "buy":
            circ = Circle((key[1], key[0]), 0.45, edgecolor="black", zorder=10)
            patches.append(circ)
        else:
            poly = RegularPolygon(
                xy=(key[1], key[0]), numVertices=4, radius=0.5, color="black", zorder=10
            )
            patches.append(poly)

    colors = prices

    num_buckets = 10

    color_steps = [
        "#0000ff",
        "#3333ff",
        "#6666ff",
        "#9999ff",
        "#ccccff",
        "#ffcccc",
        "#ff9999",
        "#ff6666",
        "#ff3333",
        "#ff0000",
    ]

    cmap = mcolors.LinearSegmentedColormap.from_list(
        "custom_colormap", color_steps, N=num_buckets
    )

    p = PatchCollection(patches, cmap=cmap, alpha=0.7, edgecolors="black")

    colors = np.array(colors)
    p.set_array(colors)
    ax1.add_collection(p)
    ax1.invert_yaxis()

    ax2.step(per_product_data["soc"] * 100, background.index, color="black")
    ax2.fill_between(
        per_product_data["soc"] * 100,
        background.index,
        step="pre",
        alpha=0.4,
        facecolor="black",
    )

    ax2.set_xlabel("State of\nCharge in %", fontsize=15)
    ax2.set_xticks(np.arange(0, 101, 50), np.arange(0, 101, 50), rotation=45)
    ax2.tick_params(axis="both", which="major", labelsize=12)
    ax2.yaxis.set_tick_params(labelleft=False)

    plt.subplots_adjust(left=0.0, bottom=0.0, right=1, top=1, wspace=0.0, hspace=0.0)

    cbar = figure.colorbar(p, cax=ax3)
    ax3.set_ylabel("Prices in Eur/MWh", fontsize=15)
    figure.subplots_adjust(wspace=0.5)

    buy_trade_marker = mlines.Line2D(
        [],
        [],
        markeredgecolor="black",
        markerfacecolor="white",
        marker="o",
        linestyle="None",
        markersize=9,
        label="Buy Trades",
    )
    sell_trade_marker = mlines.Line2D(
        [],
        [],
        markeredgecolor="black",
        markerfacecolor="white",
        marker="D",
        linestyle="None",
        markersize=7,
        label="Sell Trades",
    )

    ax1.legend(
        handles=[buy_trade_marker, sell_trade_marker],
        loc="upper right",
        fontsize=12,
        framealpha=1,
    )

    # add a text displaying the profit
    annualized_profit = profit * 365 / C_RATE
    ax1.text(
        0.5,
        1.02,
        f"Annualized Profit: {annualized_profit:.2f} €/MW/a",
        horizontalalignment="center",
        verticalalignment="center",
        transform=ax1.transAxes,
        fontsize=15,
        fontweight="bold",
    )

    output_dir = os.path.join(result_path, "plots")
    if not os.path.exists(output_dir):
        os.makedirs(output_dir)

    # save figure
    day_str = current_day.strftime("%Y-%m-%d")
    figure.savefig(
        os.path.join(output_dir, f"rolling_intrinsic_{day_str}_quarter_hourly.png"),
        dpi=300,
        bbox_inches="tight",
    )


def derive_soc_from_trades(
    trade_df: pd.DataFrame, efficiency: float, start_of_day, end_of_day, startSOC, cap
):
    """
    Derives the state of charge (SOC) from executed trades.

    Args:
        trade_df (pd.DataFrame): DataFrame with trade data.
        efficiency (float): Roundtrip efficiency.
        start_of_day (datetime): Start of the day.
        end_of_day (datetime): End of the day.
        startSOC (float): Initial state of charge.
        cap (float): Battery capacity.

    Returns:
        pd.DataFrame: DataFrame with SOC data for each product.
    """
    trade_df = trade_df.copy()
    trade_df["product"] = pd.to_datetime(trade_df["product"])
    grouped_by_product = trade_df.groupby("product")

    per_product_data = {}

    for idx, product_df in grouped_by_product:
        if product_df.empty:
            continue
        product = product_df["product"].iloc[0]
        sells = product_df[product_df["side"] == "sell"]
        sell_volume = sells["quantity"].sum()
        buys = product_df[product_df["side"] == "buy"]
        buy_volume = buys["quantity"].sum()

        net_position = (-1) * sell_volume + buy_volume

        if sell_volume > 0:
            vwap_sell = (sells["quantity"] * sells["price"]).sum() / sells[
                "quantity"
            ].sum()
        else:
            vwap_sell = 0

        if buy_volume > 0:
            vwap_buy = (buys["quantity"] * buys["price"]).sum() / buys["quantity"].sum()
        else:
            vwap_buy = 0

        pnl = buy_volume * (-1) * vwap_buy + sell_volume * vwap_sell

        profit = product_df["profit"].sum()

        net_charge = 0
        net_discharge = 0
        if net_position > 0:
            net_charge = net_position * efficiency
        else:
            net_discharge = (-1) * net_position * (1 / efficiency)

        per_product_data.update(
            {
                product: {
                    "sell_volume": sell_volume,
                    "buy_volume": buy_volume,
                    "net_position": net_position,
                    "net_discharge": net_discharge,
                    "net_charge": net_charge,
                    "vwap_sell": vwap_sell,
                    "vwap_buy": vwap_buy,
                    "pnl": pnl,
                    "profit": profit,
                }
            }
        )

    per_product_data_df = pd.DataFrame.from_dict(per_product_data).T
    per_product_data_df = per_product_data_df.reindex(
        pd.date_range(start_of_day, end_of_day - timedelta(minutes=15), freq="15min"),
        fill_value=0.0,
    )
    per_product_data_df["soc"] = (
        per_product_data_df["net_charge"] * (1 / 4) * (1 / cap)
        + (-1) * per_product_data_df["net_discharge"] * (1 / 4) * (1 / cap)
    ).cumsum() + startSOC
    return per_product_data_df


def get_ri_trades():
    """
    Retrieves rolling intrinsic trades from the output directory.

    Returns:
        pd.DataFrame: DataFrame with rolling intrinsic trades.
    """
    path = os.path.join(
        "output",
        "quarterhourly",
        "bs"
        + str(BUCKET_SIZE)
        + "cr"
        + str(C_RATE)
        + "rto"
        + str(RTO)
        + "mc"
        + str(MAX_CYCLES)
        + "mt"
        + str(MIN_TRADES),
    )

    trade_path = os.path.join(
        path, "trades", "trades_" + START_OF_DAY.date().isoformat() + ".csv"
    )
    trades_rib = pd.read_csv(trade_path, index_col=0, parse_dates=True, header=0)
    trades_rib.reset_index(inplace=True)
    return trades_rib


def derive_plotting_data(
    trades_by_product, trades_rib_by_product, start_of_day, end_of_day, tradingstart
):
    """
    Derives data for plotting trades and prices.

    Args:
        trades_by_product (pd.DataFrame): Trades grouped by product.
        trades_rib_by_product (pd.DataFrame): Rolling intrinsic trades grouped by product.
        start_of_day (datetime): Start of the day.
        end_of_day (datetime): End of the day.
        tradingstart (datetime): Start of the trading period.

    Returns:
        tuple: DataFrames for plotting prices, sides, background, and other plotting data.
    """
    tradingend = start_of_day.replace(hour=23, minute=44, second=59)
    products = pd.date_range(start=start_of_day, end=end_of_day, freq="15min")
    buckets = pd.date_range(start=tradingstart, end=tradingend, freq="15min")
    plotting_prices = pd.DataFrame(index=products, columns=buckets.astype(str))
    plotting_sides = pd.DataFrame(index=products, columns=buckets.astype(str))

    for idx, (_, df) in enumerate(trades_by_product):
        product = pd.to_datetime(df.DeliveryStart.values[0]).tz_localize(None)
        df.sort_values(by="ExecutionTime", inplace=True)
        try:
            trades = trades_rib_by_product.get_group(product)
        except KeyError:
            continue

        trades.copy().sort_values(by="execution_time", inplace=True)

        for idx, row in trades.iterrows():
            product = pd.to_datetime(trades["product"], utc=False)
            plotting_prices.loc[product.iloc[0], str(row["execution_time"])] = row[
                "price"
            ]
            plotting_sides.loc[product.iloc[0], str(row["execution_time"])] = row[
                "side"
            ]

    plotting_prices.index = pd.to_datetime(plotting_prices.index, utc=True)
    plotting_sides.index = pd.to_datetime(plotting_sides.index, utc=True)
    plotting_prices.columns = pd.to_datetime(plotting_prices.columns, utc=True)
    plotting_sides.columns = pd.to_datetime(plotting_sides.columns, utc=True)

    plotting_prices.sort_index(axis=1, inplace=True)
    plotting_sides.sort_index(axis=1, inplace=True)
    start = pd.to_datetime(start_of_day, utc=True)
    end = pd.to_datetime(end_of_day, utc=True)
    exec_start = pd.to_datetime(tradingstart, utc=True)
    exec_end = pd.to_datetime(tradingend, utc=True)
    plotting_prices = plotting_prices.reindex(
        pd.date_range(start, end - timedelta(minutes=15), freq="15min"), axis=0
    )
    plotting_sides = plotting_sides.reindex(
        pd.date_range(start, end - timedelta(minutes=15), freq="15min"), axis=0
    )
    plotting_prices = plotting_prices.reindex(
        pd.date_range(exec_start, exec_end + timedelta(hours=1), freq="15min"), axis=1
    )
    plotting_sides = plotting_sides.reindex(
        pd.date_range(exec_start, exec_end + timedelta(hours=1), freq="15min"), axis=1
    )

    plotting_prices_backup = plotting_prices.copy()

    plotting_prices.reset_index(inplace=True, drop=True)
    plotting_prices.index = plotting_prices.index

    plotting_sides.reset_index(inplace=True, drop=True)
    plotting_sides.index = plotting_sides.index

    plotting_sides.columns = np.arange(len(plotting_prices.columns))
    plotting_prices.columns = np.arange(len(plotting_prices.columns))

    background = pd.DataFrame(
        index=np.arange(0, len(plotting_prices.index), 1),
        data=np.ones(plotting_prices.shape),
    )
    background.columns = pd.to_datetime(
        plotting_prices_backup.columns, utc=True
    )  # .tz_convert("Europe/Berlin")

    prices_and_sides = {}
    prices = []
    for row_idx, row in plotting_prices.iterrows():
        for col_idx, val in row.items():
            if not np.isnan(val):
                prices_and_sides.update(
                    {
                        (row_idx, col_idx): {
                            "price": val,
                            "side": plotting_sides.loc[row_idx, col_idx],
                        }
                    }
                )
                prices.append(val)

    return (
        plotting_prices,
        plotting_sides,
        background,
        prices,
        plotting_prices_backup,
        prices_and_sides,
    )
