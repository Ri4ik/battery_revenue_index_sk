import os

import numpy as np
import pandas as pd

# Set BRI_VERBOSE_TRADES=1 to print each accepted trade and spread stop messages.
_VERBOSE_TRADES = os.environ.get("BRI_VERBOSE_TRADES", "").lower() in ("1", "true", "yes")


"""
Class for managing wholesale electricity market operations including day-ahead and intraday markets.
Handles price retrieval, power calculation, and trading optimization.
"""


class WholesaleMarket:
    """
    Represents wholesale electricity markets (Day-Ahead and Intraday) with methods for
    retrieving prices, calculating marketable power, and executing trades.
    """

    def __init__(self, day, db_connector):
        """
        Initializes the WholesaleMarket with price data for a specific day.

        Args:
            day (str): The day for which market data is initialized in 'YYYY-MM-DD' format.
            db_connector (object): Database connector object for retrieving market data.
        """
        self.prices = {"DA": None, "IDA1": None, "ID1": None}

        raw_data_path = "marketdata"
        market_path = "ID1"
        folder_path = os.path.join(raw_data_path, market_path)

        self.prices["ID1"] = self.get_id1_prices(folder_path, day, db_connector)
        self.prices["IDA"] = self.get_ida_prices(day, db_connector)

        # transoform day format from yyyy-mm-dd to datetime
        # day = pd.to_datetime(day)

        # comp_prices = self.get_id1_prices(folder_path, day)

    def set_marketable_power_id1(self, battery_config, market_config):
        """
        Sets the marketable power for Intraday 1 market based on battery configuration and market constraints.

        Args:
            battery_config (dict): Battery configuration with energy, power, DoD and SOC limits.
            market_config (dict): Market configuration with capacity share, delivery time and power share.
        """
        # This method ensures the Day Ahead Trading Power is not higher than our energy per trading timestep. This can be the case from the following situations:
        # 1) We limit the available capacity for DA and the aFRR restrictions additioanlly limit our usable energy for ID1
        # 2) DoD Limit from the battery configuration
        # 3) SOC Limit from the battery configuration (may have been adjusted by aFRR configuratiuon)
        # 4) Power Limit from the battery configuration
        max_power_market = (
            battery_config["energy"]
            * market_config["capacity_share"]
            / 2
            / market_config["t_delivery"]
        )  # if we would trade the whole energy in every trade
        max_power_with_dod = (
            max_power_market * battery_config["DoD"]
        )  # if we market full allowed DoD at every trade
        max_power_with_soc = (
            battery_config["energy"]
            * (battery_config["maxSOC"] - battery_config["minSOC"])
            / market_config["t_delivery"]
        )  # if we trade full allowed SOC at every trade
        max_power_battery = (
            battery_config["power"] * market_config["power_share"]
        )  # if we trade full available battery power at every trade
        power_limit_id1 = min(max_power_with_dod, max_power_with_soc, max_power_battery)

        # update the marketable power for ID1
        market_config["marketable_power"] = power_limit_id1

    def set_marketable_power_da(self, battery_config, market_config):
        """
        Sets the marketable power for Day-Ahead market based on battery configuration and market constraints.

        Args:
            battery_config (dict): Battery configuration with energy, power, DoD and SOC limits.
            market_config (dict): Market configuration with capacity share, delivery time and power share.
        """
        # This method ensures the Day Ahead Trading Power is not higher than our energy per trading timestep. This can be the case from the following situations:
        # 1) We limit the available capacity for DA and the aFRR restrictions additioanlly limit our usable energy for DA
        # 2) DoD Limit from the battery configuration
        # 3) SOC Limit from the battery configuration (may have been adjusted by aFRR configuratiuon)
        # 4) Power Limit from the battery configuration
        max_power_market = (
            battery_config["energy"]
            * market_config["capacity_share"]
            / 2
            / market_config["t_delivery"]
        )  #
        max_power_with_dod = (
            battery_config["energy"]
            * battery_config["DoD"]
            / market_config["t_delivery"]
        )  # if we market full allowed DoD at every trade
        max_power_with_soc = (
            battery_config["energy"]
            * (battery_config["maxSOC"] - battery_config["minSOC"])
            / market_config["t_delivery"]
        )  # if we trade full allowed SOC at every trade
        max_power_battery = (
            battery_config["power"] * market_config["power_share"]
        )  # if we trade full available battery power at every trade
        power_limit_da = min(
            max_power_market, max_power_with_dod, max_power_with_soc, max_power_battery
        )

        # update the marketable power for DA
        market_config["marketable_power"] = power_limit_da

    def get_ida_prices(self, day, db=None):
        """
        Retrieves Intraday Auction (IDA1) prices from a local file for the given day.

        Args:
            folder_path (str): Path to the folder containing IDA1 price files.
            day (str): The day for which IDA1 prices are retrieved.

        Returns:
            pd.Series: IDA1 prices for the specified day with Berlin timezone.
        """
        raw_data_path = "marketdata"
        market_path = "IDA1"
        folder_path = os.path.join(raw_data_path, market_path)
        file_path = os.path.join(folder_path, rf"IDA1 {day}.csv")

        try:
            data = pd.read_csv(file_path, parse_dates=False, index_col=0)

            # make it berlin timezone
            data.index = pd.date_range(start=day, periods=96, freq="15T")

            ida_prices = data

        except FileNotFoundError:
            print(f"File {file_path} not found.")
            print("Trying to download the data from the database instead...")
            try:
                ida_prices = db.get_ida_prices_from_db(day)

                try:
                    os.makedirs(folder_path, exist_ok=True)
                except OSError as e:
                    print(f"Error creating directory {folder_path}: {e}")
                    raise
                ida_prices.to_csv(file_path)
            except:
                #raise Exception(f"Failed to get ida prices for {day}.")
                ida_prices = None

        return ida_prices

    def get_id1_prices(self, folder_path, day, db=None):
        file_path = os.path.join(folder_path, f"ID1_{day}.csv")
        try:
            id1_prices = pd.read_csv(file_path, index_col=0, parse_dates=True)
        except:
            print(f"File {file_path} not found.")
            print("Trying to download the data from the database instead...")
            try:
                id1_db_data = db.get_id1_prices_from_db(day)
                id1_prices = self.convert_id1_prices(id1_db_data)
                os.makedirs(folder_path, exist_ok=True)
                id1_prices.to_csv(file_path)
            except:
                #raise Exception(f"Failed to get ida1 data for {day}.")
                id1_prices = None

        return id1_prices

    def convert_id1_prices(self, id1_prices):
        # Keep only prices and timestamps
        reduced_prices = id1_prices[["IndexPrice", "DeliveryStart"]]
        # Remove duplicates
        duplicate_prices = reduced_prices.drop_duplicates("DeliveryStart")
        # Rename the columns
        duplicate_prices.columns = ["price", "timestamp"]
        # Set timestamp column as index
        reindexed_prices = duplicate_prices.set_index("timestamp")
        return reindexed_prices

    def get_da_prices(self, folder_path, day, db=None):
        file_path = os.path.join(folder_path, f"DA_{day}.csv")
        try:
            prices = pd.read_csv(file_path, index_col=0, parse_dates=True)
        except FileNotFoundError:
            print(f"File {file_path} not found.")
            print("Trying to download the data from the database instead...")
            try:
                prices = db.get_da_prices(day)
                # make column 'timestamp' the index
                prices.index = pd.to_datetime(
                    prices["timestamp"], format="%Y-%m-%d %H:%M:%S"
                )

                prices.drop(columns=["timestamp"], inplace=True)
                os.makedirs(folder_path, exist_ok=True)
                prices.to_csv(file_path)
            except:
                raise Exception(f"Failed to get fcr data for {day}.")

        self.prices["DA"] = prices
        return prices

    def get_imb_prices(self, folder_path, day):
        """
        Retrieves imbalance prices from a local file for the given day.

        Expected file format:
            timestamp,price
            2025-03-01 00:00:00,120.5
            ...

        Args:
            folder_path (str): Path to the folder containing imbalance files.
            day (str): The day for which prices are retrieved.

        Returns:
            pd.DataFrame: Quarter-hourly imbalance prices indexed by timestamp.
        """
        file_path = os.path.join(folder_path, f"IMB_{day}.csv")
        try:
            prices = pd.read_csv(file_path, index_col=0, parse_dates=True)
        except FileNotFoundError as exc:
            raise FileNotFoundError(
                f"Imbalance file not found: {file_path}. "
                "Create it with tools/slovakia_data_adapter.py"
            ) from exc

        self.prices["IMB"] = prices
        return prices

    def trade_energy(self, market, time, energy):
        """
        Calculates revenue from trading energy in a specific market at a specific time.

        Args:
            market (str): Market identifier ('DA', 'IDA1', or 'ID1').
            time (datetime): Time at which the energy is traded.
            energy (float): Amount of energy traded (negative for buying, positive for selling).

        Returns:
            float: Revenue from the trade (negative for costs).
        """
        # buy energy at a certain time
        # energy as input is negative if we have to buy electricity to recharge
        #

        revenue = energy * self.prices[market].loc[time]

        return revenue

    def calculate_market_trades(
        self, prices, battery_config, market_config, soc_input=None, blocked_times=None
    ):
        """
        Calculates optimal trades in wholesale markets based on price spreads and battery constraints.

        Args:
            prices (pd.DataFrame): Price data for the market.
            battery_config (dict): Battery configuration with SOC, power, energy, and efficiency parameters.
            market_config (dict): Market configuration with resolution and power share.
            soc_input (pd.Series, optional): Pre-defined SOC profile to respect. Defaults to None.
            blocked_times (list, optional): List of time periods blocked for other services. Defaults to None.

        Returns:
            tuple: Executed trades DataFrame, daily profit, and results DataFrame with energy, revenue and SOC.
        """
        # rescale soc_input timeseries to market_config['t_delivery']
        if soc_input is not None:
            soc_given = True
            # Resample and interpolate
            new_resolution = str(market_config["t_delivery"]) + "h"
            soc_input = soc_input.resample(
                new_resolution
            ).asfreq()  # Resample to new resolution
            soc_input = soc_input.interpolate(method="linear")  # Linear interpolation

            # flatten the timeseries
            soc_input = soc_input.squeeze()

        # 1) SETUP PARAMETERS -----------------------------------------------------------------------------------
        if "marketable_power" not in market_config:
            available_power_from_config = min(
                battery_config["energy"]
                * battery_config["DoD"]
                / market_config["t_delivery"],
                battery_config["power"] * market_config["power_share"],
            )  # 50% of the battery capacity is marketable
            available_power_fom_capa = (
                battery_config["energy"]
                * market_config["capacity_share"]
                / 2
                / market_config["t_delivery"]
            )
            market_config["marketable_power"] = min(
                available_power_from_config, available_power_fom_capa
            )
        else:
            battery_config["marketable_power"] = market_config["marketable_power"]

        # trade volumes including efficiency losses. We always charge or discharge with full power (1 MW)
        trade_volume_buy = (
            market_config["t_delivery"]
            * market_config["marketable_power"]
            / battery_config["efficiency"]
        )
        trade_volume_sell = (
            market_config["t_delivery"]
            * market_config["marketable_power"]
            * battery_config["efficiency"]
        )

        # soc changes in the battery when charging or discharging with full power (1 MW)
        soc_change_buy = (
            market_config["t_delivery"]
            * battery_config["marketable_power"]
            / battery_config["energy"]
        )
        soc_change_sell = (
            market_config["t_delivery"]
            * battery_config["marketable_power"]
            / battery_config["energy"]
        )

        # marginal costs are the minimal costs that have to be earned per trade to be accepted
        # this is especially relevant for wholesale. We will see how to apply for FCR and aFRR later
        marginal_cost_per_trade = (
            battery_config["aging_costs"]
            * market_config["t_delivery"]
            * market_config["marketable_power"]
        )  # in €

        # soc ranges for the battery
        if soc_input is None:
            start_soc = battery_config["startSOC"]
        else:
            start_soc = soc_input[0]

        max_soc = battery_config["maxSOC"]
        min_soc = battery_config["minSOC"]

        if "capacity_share" in market_config:
            max_soc_da = 0.5 + market_config["capacity_share"] / 2
            min_soc_da = 0.5 - market_config["capacity_share"] / 2

            max_soc = min(max_soc, max_soc_da)
            min_soc = max(min_soc, min_soc_da)

        max_power = battery_config["marketable_power"]

        # create a time dependent energy series for selling and buying energy
        energy_sell = pd.Series(index=prices.index, data=trade_volume_sell)
        energy_buy = pd.Series(index=prices.index, data=trade_volume_buy)

        if soc_input is not None:
            for t in prices.index:
                e_soc_sell = (
                    battery_config["energy"]
                    * soc_input.loc[t]
                    * battery_config["efficiency"]
                )
                e_soc_buy = (
                    battery_config["energy"]
                    * (1 - soc_input.loc[t])
                    / battery_config["efficiency"]
                )

                energy_sell.loc[t] = min(energy_sell.loc[t], e_soc_sell)
                energy_buy.loc[t] = min(energy_buy.loc[t], e_soc_buy)

        # 2) CALCULATE THE PRICE SPREADS -----------------------------------------------------------------------

        # # turn the prices into a series
        prices = prices.squeeze()

        # Price spreads: p_sell - p_buy for all (sell, buy) pairs (same ordering as legacy loops).
        price_idx = prices.index
        p = np.asarray(prices.values, dtype=np.float64).reshape(-1)
        n = len(p)
        spread_mat = p.reshape(-1, 1) - p.reshape(1, -1)
        ii, jj = np.meshgrid(np.arange(n), np.arange(n), indexing="ij")
        price_spreads = pd.DataFrame(
            {
                "spread": spread_mat.ravel(),
                "t_sell": price_idx[ii.ravel()],
                "t_buy": price_idx[jj.ravel()],
                "p_sell": p[ii.ravel()],
                "p_buy": p[jj.ravel()],
            }
        )
        price_spreads = price_spreads.sort_values(by="spread", ascending=False).reset_index(
            drop=True
        )

        # 3) FIND THE BEST PRICE SPREADS THAT MEET CERTAIN CONDITIONS -------------------------------------------

        # find the best price spreads that meet certain conditions
        executed_rows = []

        # Extend the index to include the first timestep of the next day
        last_time = prices.index[-1]
        next_day_first_timestep = last_time + pd.Timedelta(
            hours=market_config["t_delivery"]
        )

        # Create a new index that includes all original times plus the first hour of the next day
        extended_index = pd.DatetimeIndex(
            list(prices.index) + [next_day_first_timestep]
        )

        if soc_input is None:
            soc = pd.Series(index=extended_index, data=start_soc)
        else:
            soc = pd.Series(index=extended_index, data=soc_input.values)

        daily_profit = 0
        for i in range(len(price_spreads)):
            soc_temp = soc.copy()

            # Contition A) check if price spread is higher than marginal costs
            if price_spreads.iloc[i]["spread"] < marginal_cost_per_trade:
                if _VERBOSE_TRADES and i > 0:
                    print(
                        "No profitable trades left. Last spread nr "
                        f"{i - 1} with value {price_spreads.iloc[i - 1]['spread']}"
                    )
                break

            t_buy = price_spreads.iloc[i]["t_buy"]
            t_sell = price_spreads.iloc[i]["t_sell"]

            # Condition 0) check if trades are within block borders
            if blocked_times is not None:
                in_reserved_block = False
                for blocked_time in blocked_times:
                    if t_buy in blocked_time or t_sell in blocked_time:
                        in_reserved_block = True
                        break
                if in_reserved_block:
                    continue

            # Calculate the new SOC curve when that trade would be executed
            for t in soc.index:
                previous_hour = t - pd.Timedelta(hours=market_config["t_delivery"])
                if previous_hour in prices.index and previous_hour in soc.index:
                    previous_soc = soc[t]
                    previous_soc_temp = soc_temp[t]
                    next_time = t + pd.Timedelta(hours=market_config["t_delivery"])
                    if next_time in soc_temp.index:
                        if t == t_buy:
                            soc_temp.loc[next_time] = (
                                soc_temp[next_time]
                                - previous_soc
                                + previous_soc_temp
                                + soc_change_buy
                            )
                        elif t == t_sell:
                            soc_temp.loc[next_time] = (
                                soc_temp.loc[next_time]
                                - previous_soc
                                + previous_soc_temp
                                - soc_change_sell
                            )
                        else:
                            soc_temp.loc[next_time] = (
                                soc_temp.loc[next_time]
                                - previous_soc
                                + previous_soc_temp
                            )

            # Condition B) check if the SOC changes are within the bounds
            if soc_temp.max() > max_soc or soc_temp.min() < min_soc:
                continue

            # Condition C) check if the SOC changes (DoD) are within the bounds -
            # this should alreardy be feasible through the marketable power calculation
            if (
                soc_temp.diff().max() > battery_config["DoD"]
                or abs(soc_temp[0] - battery_config["startSOC"]) > battery_config["DoD"]
            ):
                continue

            # Contition D) check if soc at the start and at the end are always the same
            start_soc = soc[0]
            end_soc = round(soc_temp.iloc[-1], 4)
            if start_soc != end_soc:
                continue

            # calculate the sum of all positive soc changes
            pos_soc_changes = soc_temp.diff()[soc_temp.diff() > 0].sum()
            if soc_temp[0] > start_soc:
                pos_soc_changes += soc_temp[0] - start_soc

            # CONDITION E) check if SOC changes (cycles) stay within cycle limi
            if pos_soc_changes > battery_config["cycle_limit"]:
                continue

            # check if the positive and negative soc chnages are the same
            neg_soc_changes = soc_temp.diff()[soc_temp.diff() < 0].sum()
            if soc_temp[0] < start_soc:
                neg_soc_changes -= start_soc - soc_temp[0]
            if pos_soc_changes - neg_soc_changes * -1 > 0.01 and _VERBOSE_TRADES:
                print(
                    f"Number of positive soc changes: {pos_soc_changes} and negative soc changes: {neg_soc_changes}"
                )

            diff_energy_per_t = soc_temp.diff() * battery_config["energy"]
            diff_energy_per_t[0] = (
                soc_temp[0] * battery_config["energy"]
                - start_soc * battery_config["energy"]
            )

            charged_energy_per_t = diff_energy_per_t[diff_energy_per_t > 0]
            discharged_energy_per_t = diff_energy_per_t[diff_energy_per_t < 0] * -1

            charged_power = charged_energy_per_t / market_config["t_delivery"]
            discharged_power = discharged_energy_per_t / market_config["t_delivery"]

            # CONDITION F) check if the used power is within the limits
            if (
                charged_power.max() > max_power * 1.001
                or discharged_power.max() > max_power * 1.001
            ):  # 0.1% safety margin for rounding issues
                continue

            # Consition D) Check if SOC at the start of every blocked period is 0.5
            if blocked_times is not None:
                wrong_soc = False
                for blocked_time in blocked_times:
                    if soc_temp[blocked_time[0]] != 0.5:
                        wrong_soc = True
                        break
                if wrong_soc:
                    continue

            # execute the trade
            soc = soc_temp.copy()

            # calculate how many consecutive "buy" trades there are in executed_trades
            revenue = trade_volume_sell * price_spreads.iloc[i]["p_sell"]
            costs = trade_volume_buy * price_spreads.iloc[i]["p_buy"]
            profit = revenue - costs
            daily_profit += profit

            executed_rows.append(
                {
                    "spread": price_spreads.iloc[i]["spread"],
                    "t_buy": t_buy,
                    "t_sell": t_sell,
                    "p_buy": price_spreads.iloc[i]["p_buy"],
                    "p_sell": price_spreads.iloc[i]["p_sell"],
                    "revenue": revenue,
                    "costs": costs,
                    "profit": profit,
                }
            )

            if _VERBOSE_TRADES:
                print(f"Trade executed at BUY: {t_buy} and SELL: {t_sell}")

        executed_trades = (
            pd.DataFrame(executed_rows)
            if executed_rows
            else pd.DataFrame(
                columns=[
                    "spread",
                    "t_buy",
                    "t_sell",
                    "p_buy",
                    "p_sell",
                    "costs",
                    "revenue",
                    "profit",
                ]
            )
        )

        result_df = self.get_buy_sell_data(
            prices.index, executed_trades, trade_volume_sell, trade_volume_buy
        )
        result_df["soc"] = soc.values[1 : len(prices) + 1]

        return executed_trades, daily_profit, result_df

    def get_buy_sell_data(self, begin_of_hour_index, executed_trades, e_sell, e_buy):
        """
        Converts executed trades into time series data of energy, revenue, and SOC.

        Args:
            begin_of_hour_index (pd.DatetimeIndex): Index for the resulting DataFrame.
            executed_trades (pd.DataFrame): DataFrame containing executed trades.
            e_sell (float): Energy volume for sell trades.
            e_buy (float): Energy volume for buy trades.

        Returns:
            pd.DataFrame: DataFrame with energy, revenue and SOC columns for each time step.
        """
        result_df = pd.DataFrame(
            columns=["energy", "revenue", "soc"], index=begin_of_hour_index, data=0
        )
        for i in range(len(executed_trades)):
            result_df["energy"].loc[executed_trades.iloc[i]["t_buy"]] += e_sell
            result_df["energy"].loc[executed_trades.iloc[i]["t_sell"]] -= e_buy
            result_df["revenue"].loc[executed_trades.iloc[i]["t_sell"]] += (
                executed_trades.iloc[i]["revenue"]
            )
            result_df["revenue"].loc[executed_trades.iloc[i]["t_buy"]] -= (
                executed_trades.iloc[i]["costs"]
            )

        return result_df

    def transform_into_qh_df(self, new_resolution, df):
        """
        Transforms a DataFrame from hourly to quarter-hourly resolution.

        Args:
            new_resolution (str): Target resolution (e.g., '15min').
            df (pd.DataFrame): DataFrame to transform with hourly resolution.

        Returns:
            pd.DataFrame: Transformed DataFrame with quarter-hourly resolution.
        """
        # transform the dataframe into a quarter hour resolution
        # take the values from the innit_resolution and devide them evenly to the new resolution
        # e.g. 1h to 15min

        # add one more timestap to the back of the dataframe only containing zeros
        init_resolution = df.index[1] - df.index[0]
        last_value = df.iloc[-1]
        df.loc[df.index[-1] + init_resolution] = 0
        df.loc[df.index[-1], "soc"] = last_value["soc"]

        # resample the dataframe to the new resolution
        df_extended = df.resample(new_resolution).asfreq()

        df_shifted = df_extended.shift(3)

        new_resolution = pd.to_timedelta(new_resolution)
        # add a value in the beginning to interpolate to the beginning
        df_shifted.loc[df.index[0] - new_resolution] = 0
        df_shifted.loc[df.index[0], "soc"] = 0.5

        # sort the df based on datetime index
        df_shifted = df_shifted.sort_index()
        # interpolate the values
        df_shifted["soc"] = df_shifted["soc"].interpolate(method="linear")

        soc_copy = df_shifted["soc"].copy()
        # distribute the values to the new resolution evenly
        df_shifted = df_shifted.fillna(method="bfill")  # forward fill the first value

        # divide all values but column soc by the number of new resolution steps
        df_shifted = df_shifted / 4

        # attach soc copy
        df_shifted["soc"] = soc_copy
        # delete first row again
        df_shifted = df_shifted.drop(df_shifted.index[0])
        # delete last row from df
        df_shifted = df_shifted.drop(df_shifted.index[-1])

        return df_shifted
