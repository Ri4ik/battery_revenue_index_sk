import os
from datetime import datetime, timedelta

import numpy as np
import pandas as pd

from markets.price_column_names import (
    AFRR_CAPACITY,
    AFRR_CAPACITY_LEGACY,
    AFRR_SETPOINT,
    AFRR_SETPOINT_LEGACY,
    series_from_columns,
)


class aFRRmarket:
    def __init__(
        self, day, market_config_cap, market_config_energy, battery_config, db
    ):
        """
        Initializes the aFRRmarket class with the given configurations and database connection.

        Args:
            day (str): The day for which the market is being initialized.
            market_config_cap (dict): Configuration for aFRR capacity market.
            market_config_energy (dict): Configuration for aFRR energy market.
            battery_config (dict): Configuration for the battery, including energy and power limits.
            db (object): Database connection object for retrieving market data.
        """
        raw_data_path = "marketdata"
        market_path = "aFRR_capacity"
        folder_path = os.path.join(raw_data_path, market_path)
        self.get_affr_capacity_prices(day, folder_path=folder_path, db=db)
        self.market_config_capacity = market_config_cap
        self.market_config_energy = market_config_energy
        self.battery_config = battery_config
        self.set_marketable_power_afrr_capacity(battery_config, market_config_cap)
        self.capacity_revenue = self.calculate_afrr_capacity_revenue(market_config_cap)
        self.cycle_limit = (
            battery_config["cycle_limit"] * market_config_energy["cycle_share"]
        )

    def set_marketable_power_afrr_capacity(self, battery_config, market_config):
        """
        Sets the marketable power for aFRR (automatic Frequency Restoration Reserve) capacity
        based on the battery configuration and market configuration.
        Args:
            battery_config (dict): A dictionary containing battery parameters:
                - 'energy' (float): The total energy capacity of the battery.
                - 'power' (float): The maximum power output of the battery.
            market_config (dict): A dictionary containing market parameters:
                - 'power_share' (float): The fraction of the battery's power that can be used for aFRR.
        Returns:
            dict: Updated market configuration dictionary with the calculated marketable power
            for aFRR capacity added under the key 'marketable_power'.
        Notes:
            - The marketable power for symmetric aFRR bids is calculated as the minimum of
            half the battery's energy capacity and the product of the battery's power and
            the market's power share.
            - The calculated marketable power is stored in both the `market_config` dictionary
            and as an instance attribute `self.afrr_power`.
        """
        # What we can can max from aFRR regulations and power splitting
        afrr_power_symmetric = min(
            battery_config["energy"] / 2,
            battery_config["power"] * market_config["power_share"],
        )  # for symmetric aFRR bids
        afrr_power_share = afrr_power_symmetric  # * market_config['power_share']  # share of the battery power that is used for aFRR

        market_config["marketable_power"] = afrr_power_share

        self.market_config = market_config

        self.afrr_power = afrr_power_share

        return market_config

    def set_marketable_power_afrr_energy(self, battery_config, fcr=None):
        """
        Sets the marketable power for aFRR energy based on battery configuration and FCR.

        Args:
            battery_config (dict): Battery configuration with energy and power limits.
            fcr (object, optional): FCR object with reserved power. Defaults to None.
        """
        # calculate the power used in the market before
        power_limit = pd.Series(index=range(1, 97), data=0)

        afrr_power_share = (
            battery_config["power"] * self.market_config_energy["power_share"]
        )  # share of the battery power that is used for aFRR

        for block in range(6):
            if block in self.activated_blocks:
                power_limit[block * 16 : (block + 1) * 16] = afrr_power_share
            else:
                power_limit[block * 16 : (block + 1) * 16] = min(
                    afrr_power_share,
                    (battery_config["power"] - fcr.reserved_power)
                    * self.market_config_energy["power_share"],
                )

        self.market_config_energy["marketable_power"] = power_limit

    def set_marketable_soc_afrr_energy(self, battery_config, fcr=None, ri_config=None):
        """
        Sets the marketable SOC (State of Charge) limits for aFRR energy.

        Args:
            battery_config (dict): Battery configuration with SOC limits.
            fcr (object, optional): FCR object with SOC boundaries. Defaults to None.
            ri_config (dict, optional): RI configuration with capacity share. Defaults to None.
        """
        # calculate theoretical soc limits that we must no exceed with afrr activation (including SOC management)
        max_soc = pd.Series(index=range(1, 97), data=0)
        min_soc = pd.Series(index=range(1, 97), data=0)
        max_soc_marketable = pd.Series(index=range(1, 97), data=0)
        min_soc_marketable = pd.Series(index=range(1, 97), data=0)

        for block in range(6):
            if ri_config is None:
                max_soc.loc[block * 16 : (block + 1) * 16] = battery_config["maxSOC"]
                min_soc.loc[block * 16 : (block + 1) * 16] = battery_config["minSOC"]
            else:
                max_soc.loc[block * 16 : (block + 1) * 16] = (
                    battery_config["maxSOC"] - ri_config["capacity_share"] / 2
                )
                min_soc.loc[block * 16 : (block + 1) * 16] = (
                    battery_config["minSOC"] + ri_config["capacity_share"] / 2
                )

            if block in self.activated_blocks:
                max_soc_marketable.loc[block * 16 : (block + 1) * 16] = (
                    max_soc - self.afrr_power / battery_config["energy"]
                )
                min_soc_marketable.loc[block * 16 : (block + 1) * 16] = (
                    min_soc + self.afrr_power / battery_config["energy"]
                )

            else:
                if ri_config is None:
                    max_soc_marketable.loc[block * 16 : (block + 1) * 16] = min(
                        battery_config["maxSOC"], fcr.upper_soc_boundary
                    )
                    min_soc_marketable.loc[block * 16 : (block + 1) * 16] = max(
                        battery_config["minSOC"], fcr.lower_soc_boundary
                    )
                else:
                    max_soc.loc[block * 16 : (block + 1) * 16] = (
                        min(battery_config["maxSOC"], fcr.upper_soc_boundary)
                        - ri_config["capacity_share"] / 2
                    )
                    min_soc.loc[block * 16 : (block + 1) * 16] = (
                        max(battery_config["minSOC"], fcr.lower_soc_boundary)
                        + ri_config["capacity_share"] / 2
                    )
                    max_soc_marketable.loc[block * 16 : (block + 1) * 16] = max_soc
                    min_soc_marketable.loc[block * 16 : (block + 1) * 16] = min_soc

        self.max_soc = max_soc
        self.min_soc = min_soc
        self.max_soc_marketable = max_soc_marketable
        self.min_soc_marketable = min_soc_marketable

    def get_affr_capacity_prices(self, day, folder_path=None, db=None):
        """
        Retrieves aFRR capacity prices from the database for the given day.

        Args:
            day (str): The day for which capacity prices are retrieved.
            db (object): Database connection object.
        """
        # read xlsx file as downloaded from website

        file_path = os.path.join(folder_path, f"afrr_capacity_{day}.csv")
        try:
            all_afrr_data = pd.read_csv(file_path, header=0, index_col=0)
        except FileNotFoundError:
            print(f"File {file_path} not found.")
            print("Trying to download the data from the database instead...")
            all_afrr_data = db.get_afrr_capacity_prices(day)
            os.makedirs(folder_path, exist_ok=True)
            all_afrr_data.to_csv(file_path)
        _cap_col = next(
            c for c in (AFRR_CAPACITY, AFRR_CAPACITY_LEGACY) if c in all_afrr_data.columns
        )

        # convert index to datetime
        all_afrr_data.index = pd.to_datetime(all_afrr_data.index)

        self.capacity_prices = pd.DataFrame(index=range(6), columns=["POS", "NEG"])
        self.capacity_prices["POS"] = all_afrr_data[
            all_afrr_data["PRODUCT"].str.contains("POS")
        ][_cap_col].values
        self.capacity_prices["NEG"] = all_afrr_data[
            all_afrr_data["PRODUCT"].str.contains("NEG")
        ][_cap_col].values

    def calculate_afrr_capacity_revenue(self, market_config):
        """
        Calculates the revenue for aFRR capacity based on market configuration.

        Args:
            market_config (dict): Market configuration with delivery time.

        Returns:
            float: Total revenue for the day.
        """
        # Calculate the revenue for aFRR based on the prices
        # calculate marketable power for aFRR. This is due to the assumption that we always bid in both directions.
        # So if there is 1 MW of battery power. We only can bis 0.5 MW in both direction.
        # This will reduce the potential revenue by the factor of 2.
        # battery_config['marketable_power'] = min(battery_config['energy'] * battery_config['DoD'] / market_config['t_delivery'], battery_config['power'])  # 50% of the battery capacity is marketable

        afrr_revenue_per_hour_pos = (
            self.capacity_prices["POS"] * self.afrr_power
        )  # in € per hour
        afrr_revenue_per_hour_neg = self.capacity_prices["NEG"] * self.afrr_power

        afrr_revenue = (
            afrr_revenue_per_hour_pos + afrr_revenue_per_hour_neg
        ) * market_config["t_delivery"]  # scale up to 4 hour blocks

        daily_revenue = afrr_revenue.sum()  # total revenue for the day

        return afrr_revenue

    def adjust_soc_boundaries(self, battery_config, market_config):
        """
        Adjusts the SOC boundaries based on aFRR capacity requirements.

        Args:
            battery_config (dict): Battery configuration with SOC limits.
            market_config (dict): Market configuration with aFRR capacity.
        """
        # energy reserved for aFRR Capacity following PQ 2.7
        energy_afrr = (
            market_config["aFRR Capacity"]["marketable_power"] * 1
        )  # we have to reserve 1 hour
        # max_soc_after_afrr = (battery_config['energy'] - energy_afrr)/ battery_config['energy']
        # min_soc_after_afrr = energy_afrr/ battery_config['energy']

        battery_config["maxSOC"] = (
            battery_config["maxSOC"] - energy_afrr / battery_config["energy"]
        )
        battery_config["minSOC"] = (
            battery_config["minSOC"] + energy_afrr / battery_config["energy"]
        )

    def read_merit_order(self, db_connector, day):
        """
        Reads the merit order data for aFRR energy from the database.

        Args:
            db_connector (object): Database connection object.
            day (str): The day for which merit order data is retrieved.
        """
        # merit order path
        meritorderpath = os.path.join("marketdata", "aFRR_energy")
        filename = "aFRR_merit_order_" + day + ".csv"
        file_path = os.path.join(meritorderpath, filename)

        try:
            all_afrr_energy_data = pd.read_csv(
                file_path, header=0, index_col=0
            )  # index is date
        except FileNotFoundError:
            all_afrr_energy_data = db_connector.get_afrr_merit_order_from_db(day)
            os.makedirs(meritorderpath, exist_ok=True)
            all_afrr_energy_data.to_csv(file_path)

        product_column = "PRODUCT"
        afrr_energy_products = all_afrr_energy_data[product_column]
        neg_mask = afrr_energy_products.str.contains("NEG")
        pos_mask = afrr_energy_products.str.contains("POS")

        # Create a dictionary to hold the grouped DataFrames
        grouped_dfs = {"NEG": {}, "POS": {}}

        # Group the data by quarter hour
        for quarter_hour in range(1, 97):
            quarter_hour_str = f"{quarter_hour:03d}"
            time_mask = afrr_energy_products.str.contains(quarter_hour_str)

            grouped_dfs["NEG"][quarter_hour] = all_afrr_energy_data[
                neg_mask & time_mask
            ]
            grouped_dfs["POS"][quarter_hour] = all_afrr_energy_data[
                pos_mask & time_mask
            ]

            # correct the sign in case of payment direction is grid-->provider because this will be costs to us
            payment_direction = grouped_dfs["NEG"][quarter_hour][
                "ENERGY_PRICE_PAYMENT_DIRECTION"
            ]
            grouped_dfs["NEG"][quarter_hour].loc[:, "ENERGY_PRICE_[EUR/MWh]"] = (
                np.where(payment_direction == "PROVIDER_TO_GRID", -1, 1)
                * grouped_dfs["NEG"][quarter_hour]["ENERGY_PRICE_[EUR/MWh]"]
            )

            payment_direction = grouped_dfs["POS"][quarter_hour][
                "ENERGY_PRICE_PAYMENT_DIRECTION"
            ]
            grouped_dfs["POS"][quarter_hour].loc[:, "ENERGY_PRICE_[EUR/MWh]"] = (
                np.where(payment_direction == "PROVIDER_TO_GRID", -1, 1)
                * grouped_dfs["POS"][quarter_hour]["ENERGY_PRICE_[EUR/MWh]"]
            )

            # drop columns COUNTRY, NOTE, and TYPE_OF_RESERVES
            for direction in ["NEG", "POS"]:
                grouped_dfs[direction][quarter_hour] = grouped_dfs[direction][
                    quarter_hour
                ].drop(columns=["COUNTRY", "NOTE", "TYPE_OF_RESERVES"])

                # sort with asecending prices and add a column with cummulated capacity
                grouped_dfs[direction][quarter_hour] = grouped_dfs[direction][
                    quarter_hour
                ].sort_values(by="ENERGY_PRICE_[EUR/MWh]")

                # add a column with cummulated capacity
                grouped_dfs[direction][quarter_hour] = grouped_dfs[direction][
                    quarter_hour
                ].assign(
                    CAPACITY_CUMSUM_MW=grouped_dfs[direction][quarter_hour][
                        "ALLOCATED_CAPACITY_[MW]"
                    ].cumsum()
                )

        self.merit_orders = grouped_dfs

    def calculate_clearing_prices(self):
        """
        Calculates the clearing prices for aFRR energy based on activated power and merit orders.
        """
        # we have merit orders for each quarter hour of one day in self.merit_orders
        # we have secondly data of activated power in self.activated_power

        # 1) Reindex the secondly data to one value per 4 seconds and take the mean
        # activated_power_4s = self.activated_power.resample('4S').mean()
        activated_power = self.activated_power.copy()

        # Create an empty DataFrame for results
        result_df = pd.DataFrame(
            index=activated_power.index,
            columns=["clearing_price", "setpoints_power", "activated_power", "revenue"],
        )

        self.setpoints_power_qh = {}

        for quarter_hour in range(1, 97):
            # Get the activated power for this quarter hour
            start_time = (
                (quarter_hour - 1) * 15 * 60 // 1
            )  # Convert minutes to intervals of 4 seconds
            end_time = quarter_hour * 15 * 60 // 1  # Same conversion

            activated_power_qh = activated_power.iloc[start_time:end_time]
            self.setpoints_power_qh[quarter_hour] = activated_power_qh

            current_pos_merit_order = self.merit_orders["POS"][quarter_hour]
            current_neg_merit_order = self.merit_orders["NEG"][quarter_hour]

            # Use vectorized operation instead of apply() inside loop
            clearing_prices_values = []

            for x in activated_power_qh:
                setpoint_power = abs(x)
                merit_order = current_pos_merit_order if x > 0 else current_neg_merit_order
                filtered_df = merit_order[merit_order["CAPACITY_CUMSUM_MW"] <= setpoint_power]
                clearing_price_s = filtered_df.iloc[-1]["ENERGY_PRICE_[EUR/MWh]"] if not filtered_df.empty else 0
                # clearing_prices_values.append(clearing_price_s)
                # capacity_cumsum_mw = abs(x)
                # clearing_price = merit_order_indices[quarter_hour - 1][merit_order_indices[quarter_hour - 1]['CAPACITY_CUMSUM_MW'] >= capacity_cumsum_mw].iloc[0]['ENERGY_PRICE_[EUR/MWh]']
                clearing_prices_values.append(clearing_price_s)

            result_df.loc[activated_power_qh.index, "clearing_price"] = (
                clearing_prices_values
            )

        result_df["setpoints_power"] = activated_power
        result_df["activated_power"] = 0
        result_df["offered_power"] = 0
        result_df["revenue"] = 0
        result_df["threshold_power_pos"] = 0
        result_df["threshold_power_neg"] = 0
        result_df["BalancingPower"] = 0
        result_df["SOC"] = 0.5

        self.clearing_data = result_df

    def calculate_threshold_power(self, qh, bid_price_negative, bid_price_positive):
        """
        Calculates the threshold power for a given quarter-hour and bid.

        Args:
            qh (int): Quarter-hour index.
            bid_price_negative (float): Bid price for negative aFRR.
            bid_price_positive (float): Bid price for positive aFRR.
        Returns:
            dict: Threshold power matrix for POS and NEG directions.
        """

        threshold_power_matrix = {"POS": {}, "NEG": {}}

        # POSITIVE aFRR THRESHOLD

        merit_order = self.merit_orders["POS"][qh]
        existing_lower_bids = merit_order[
            merit_order["ENERGY_PRICE_[EUR/MWh]"] <= bid_price_positive
        ]
        if existing_lower_bids.empty:
            # if there are no lower bids, set the threshold power to 0
            threshold_power_qh = 0
        else:
            # get the last cumsum power in the merit order where the price is lower than our price
            threshold_power_qh = existing_lower_bids.iloc[-1]["CAPACITY_CUMSUM_MW"]
        threshold_power_matrix["POS"][bid_price_positive] = threshold_power_qh

        # NEGATIVE aFRR THRESHOLD

        merit_order = self.merit_orders["NEG"][qh]
        existing_lower_bids = merit_order[
            merit_order["ENERGY_PRICE_[EUR/MWh]"] <= bid_price_negative
        ]
        if existing_lower_bids.empty:
            # if there are no lower bids, set the threshold power to 0
            threshold_power_qh = 0
        else:
            # get the last cumsum power in the merit order where the price is lower than our price
            threshold_power_qh = existing_lower_bids.iloc[-1]["CAPACITY_CUMSUM_MW"]
        threshold_power_matrix["NEG"][bid_price_negative] = threshold_power_qh

        return threshold_power_matrix

    def calculate_energy_to_deliver(
        self,
        battery_config,
        threshold_power_matrix,
        qh,
        power_limit_pos,
        power_limit_neg,
        bid_price_negative,
        bid_price_positive,
        p_balancing=0,
        potential_calc=False,
    ):
        """
        Calculates the energy to deliver for aFRR activation in a given quarter-hour.

        Args:
            battery_config (dict): Battery configuration with efficiency and energy limits.
            threshold_power_matrix (dict): Threshold power matrix for POS and NEG directions.
            qh (int): Quarter-hour index.
            power_limit_pos (float): Positive power limit.
            power_limit_neg (float): Negative power limit.
            bid_price_negative (float): Bid price for negative aFRR.
            bid_price_positive (float): Bid price for positive aFRR.
            p_balancing (float, optional): Balancing power. Defaults to 0.
            potential_calc (bool, optional): Whether to calculate potential revenues. Defaults to False.

        Returns:
            tuple: Energy delivered and revenue for the quarter-hour.
        """
        # p_balancing has posotve values for charging and negative values for discharging
        # this power has to be considered when calculating the available power for delivering aFRR
        # when we discharge to balance the SOC, we can actually use that power to deliver negative aFRR

        # whenever absolute value of self.clearing_data['activated_power'] is higher than the threshold power
        setpoint_power = self.setpoints_power_qh[qh]
        eff = battery_config["efficiency"]
        power_limit_afrr_pos = min(
            self.market_config_energy["marketable_power"][qh] - p_balancing,
            power_limit_pos,
        )
        power_limit_afrr_neg = min(
            self.market_config_energy["marketable_power"][qh] + p_balancing,
            power_limit_neg,
        )

        threshold_power_pos = threshold_power_matrix["POS"][bid_price_positive]
        threshold_power_neg = threshold_power_matrix["NEG"][bid_price_negative]

        # calculate the energy to deliver
        power_request = setpoint_power * 0
        power_request[setpoint_power > threshold_power_pos] = setpoint_power[
            setpoint_power > threshold_power_pos
        ]
        power_request[setpoint_power < threshold_power_neg * -1] = setpoint_power[
            setpoint_power < threshold_power_neg * -1
        ]

        # set power request to marketable power where the power request is higher than the marketable power
        # positive afrr --> dicharge --> we have lower offered power thaan activated
        # negative afrr --> charge --> we have higher offered power than activated
        activated_power = power_request.copy()
        activated_power[power_request > power_limit_afrr_pos] = power_limit_afrr_pos
        activated_power[power_request < power_limit_afrr_neg * -1] = (
            -1 * power_limit_afrr_neg * eff
        )

        offered_power = np.where(
            power_request > 0, activated_power * eff, activated_power / eff
        )

        revenue_ts = setpoint_power * 0
        revenue_ts[setpoint_power > threshold_power_pos] = (
            self.clearing_data.loc[setpoint_power.index, "clearing_price"][
                setpoint_power > threshold_power_pos
            ]
            * offered_power[setpoint_power > threshold_power_pos]
            / 3600
        )
        revenue_ts[setpoint_power < threshold_power_neg * -1] = (
            self.clearing_data.loc[setpoint_power.index, "clearing_price"][
                setpoint_power < threshold_power_neg * -1
            ]
            * offered_power[setpoint_power < threshold_power_neg * -1]
            * -1
            / 3600
        )

        if not potential_calc:
            # set objects variables only if we do NOT calculate potential revenues
            if qh == 1:
                last_soc = 0.5
            else:
                last_soc = self.clearing_data.loc[
                    self.setpoints_power_qh[qh - 1].index[-1], "SOC"
                ]

            self.clearing_data.loc[setpoint_power.index, "activated_power"] = (
                activated_power
            )
            self.clearing_data.loc[setpoint_power.index, "offered_power"] = (
                offered_power
            )
            self.clearing_data.loc[setpoint_power.index, "revenue"] = revenue_ts
            self.clearing_data.loc[setpoint_power.index, "threshold_power_pos"] = (
                threshold_power_pos
            )
            self.clearing_data.loc[setpoint_power.index, "threshold_power_neg"] = (
                threshold_power_neg
            )
            self.clearing_data.loc[setpoint_power.index, "bid_pos"] = bid_price_positive
            self.clearing_data.loc[setpoint_power.index, "bid_neg"] = bid_price_negative
            self.clearing_data.loc[setpoint_power.index, "BalancingPower"] = p_balancing

            delivered_power = activated_power + p_balancing
            self.clearing_data.loc[setpoint_power.index, "SOC"] = (
                last_soc - delivered_power.cumsum() / (battery_config["energy"] * 3600)
            )

            last_index = setpoint_power.index[-1]
            last_index_df = self.clearing_data.index[-1]

            # fill all values in the column SOC from last_index to last_index_df
            self.clearing_data.loc[last_index:last_index_df, "SOC"] = (
                self.clearing_data.loc[last_index, "SOC"]
            )

        # negative sign to show what we actually do with the storage (power request is positive in the case of POS aFRR --> we have to discharge)
        energy_delivered_qh = activated_power.sum() / 3600  # in MWh;
        revenue_qh = revenue_ts.sum()

        return energy_delivered_qh, revenue_qh

    def create_index(self, day, resolution):
        """
        Creates a datetime index for the given day and resolution.

        Args:
            day (str): The day for which the index is created.
            resolution (str): Resolution of the index ('h' for hourly, '15min' for 15-minute intervals).

        Returns:
            pd.DatetimeIndex: Datetime index with Berlin timezone.
        """
        # use the resolution input ('h' or '15min') to create the index
        if resolution == "h":
            index = pd.date_range(start=day + " 00:00", periods=24, freq="H").strftime(
                "%Y-%m-%d %H:%M"
            )
        elif resolution == "15min":
            index = pd.date_range(
                start=day + " 00:00", periods=96, freq="15min"
            ).strftime("%Y-%m-%d %H:%M")

        datetime_index = pd.DatetimeIndex(index)

        # make it berlin timezone
        datetime_index = datetime_index.tz_localize("Europe/Berlin")

        return datetime_index

    def read_activation_data(self, day, db):
        """
        Reads aFRR activation data for the given day from the database.

        Args:
            day (str): The day for which activation data is retrieved.
            db (object): Database connection object.
        """
        # read activation data
        activation_path = os.path.join("marketdata", "aFRR_energy")

        filename = "aFRR_activation_" + day + ".csv"

        filepath = os.path.join(activation_path, filename)

        if not os.path.exists(filepath):
            # all_afrr_activation_data = pd.read_csv(file_path, header=0)
            all_afrr_activation_data = db.get_afrr_activation_data(day)

            # combine 0 and 1 column to a datetime string as index
            all_afrr_activation_data["datetime"] = all_afrr_activation_data.apply(
                lambda row: pd.to_datetime(row["DATE"]) + row["TIME"], axis=1
            )

            all_afrr_activation_data.index = pd.to_datetime(
                all_afrr_activation_data["datetime"], format="%Y-%m-%d %H:%M:%S"
            )

            activated_power = series_from_columns(
                all_afrr_activation_data, AFRR_SETPOINT, AFRR_SETPOINT_LEGACY
            )

            # save locally so we don't have to call db every time
            os.makedirs(activation_path, exist_ok=True)
            activated_power.to_csv(filepath)

            self.activated_power = activated_power

        else:
            activated_power = pd.read_csv(filepath, header=0)
            activated_power.index = pd.to_datetime(
                activated_power["datetime"], format="%Y-%m-%d %H:%M:%S"
            )

            self.activated_power = series_from_columns(
                activated_power, AFRR_SETPOINT, AFRR_SETPOINT_LEGACY
            )

    def read_seps_damas_energy_data(self, day):
        path = os.path.join(
            "marketdata",
            "SepsDamasEnergy",
            "daily",
            "seps_damas_afrr_energy_{}.csv".format(day),
        )
        if not os.path.exists(path):
            raise FileNotFoundError(path)
        data = pd.read_csv(path)
        data["timestamp_local"] = pd.to_datetime(data["timestamp_local"])
        return data

    def calculate_seps_damas_energy_revenue(self, day):
        """
        Calculate aFRR energy revenue from SEPS/Damas activated aFRR energy.

        SEPS/Damas publishes actual system aFRR activation volume and standard
        prices per quarter-hour. For a battery benchmark we follow the net
        activation direction per quarter-hour and cap delivery by marketable
        aFRR power, SOC headroom, and the configured daily cycle budget.
        """
        data = self.read_seps_damas_energy_data(day)
        index = pd.date_range(start=day + " 00:00", periods=96, freq="15min")
        start_soc = float(self.battery_config.get("startSOC", 0.5))
        min_soc = float(self.battery_config.get("minSOC", 0.0))
        max_soc = float(self.battery_config.get("maxSOC", 1.0))
        battery_energy = float(self.battery_config.get("energy", 1.0))
        cycle_share = float(self.market_config_energy.get("cycle_share", 1.0))
        cycle_limit = float(self.battery_config.get("cycle_limit", 1.0)) * cycle_share
        max_throughput_mwh = max(0.0, 2.0 * battery_energy * cycle_limit)
        soc = start_soc
        throughput_mwh = 0.0
        result = pd.DataFrame(
            index=range(1, 97),
            data={
                "afrr_revenue": 0.0,
                "balancing_revenue": 0.0,
                "soc": start_soc,
                "up_volume_mwh": 0.0,
                "down_volume_mwh": 0.0,
                "battery_up_mwh": 0.0,
                "battery_down_mwh": 0.0,
            },
        )

        if "marketable_power" in self.market_config_energy:
            power_limit = self.market_config_energy["marketable_power"]
        else:
            power_limit = pd.Series(index=range(1, 97), data=self.afrr_power)

        for qh in range(1, 97):
            rows_qh = data[data["quarter_hour"] == qh]
            if rows_qh.empty:
                result.loc[qh, "soc"] = soc
                continue

            max_energy_mwh = float(power_limit.loc[qh]) * 0.25
            remaining_throughput = max_throughput_mwh - throughput_mwh
            if remaining_throughput <= 0 or max_energy_mwh <= 0:
                result.loc[qh, "soc"] = soc
                continue

            up_volume = rows_qh["up_volume_mwh"].clip(lower=0).sum()
            down_volume = rows_qh["down_volume_mwh"].clip(lower=0).sum()
            up_revenue = (
                rows_qh["up_volume_mwh"].clip(lower=0) * rows_qh["up_price_eur_mwh"]
            ).sum()
            down_revenue = (
                rows_qh["down_volume_mwh"].clip(lower=0)
                * rows_qh["down_price_eur_mwh"]
            ).sum()
            up_price = up_revenue / up_volume if up_volume else 0.0
            down_price = down_revenue / down_volume if down_volume else 0.0
            net_volume = up_volume - down_volume

            battery_up = 0.0
            battery_down = 0.0
            revenue = 0.0
            if net_volume > 0:
                soc_headroom_mwh = max(0.0, (soc - min_soc) * battery_energy)
                battery_up = min(
                    net_volume,
                    max_energy_mwh,
                    remaining_throughput,
                    soc_headroom_mwh,
                )
                revenue = battery_up * up_price
                soc -= battery_up / battery_energy
                throughput_mwh += battery_up
            elif net_volume < 0:
                soc_headroom_mwh = max(0.0, (max_soc - soc) * battery_energy)
                battery_down = min(
                    -net_volume,
                    max_energy_mwh,
                    remaining_throughput,
                    soc_headroom_mwh,
                )
                revenue = battery_down * down_price
                soc += battery_down / battery_energy
                throughput_mwh += battery_down

            result.loc[qh, "afrr_revenue"] = revenue
            result.loc[qh, "up_volume_mwh"] = up_volume
            result.loc[qh, "down_volume_mwh"] = down_volume
            result.loc[qh, "battery_up_mwh"] = battery_up
            result.loc[qh, "battery_down_mwh"] = battery_down
            result.loc[qh, "soc"] = soc

        result.index = index
        return result

    def calculate_afrr_energy_revenue(
        self, day, daily_results, battery_config, market_config
    ):
        """
        Calculates the revenue for aFRR energy based on daily results and battery configuration.

        Args:
            day (str): The day for which revenue is calculated.
            daily_results (dict): Daily results for each quarter-hour.
            battery_config (dict): Battery configuration with cycle limits.
            market_config (dict): Market configuration with delivery time.

        Returns:
            pd.DataFrame: Revenue and energy data for each quarter-hour.
        """
        max_cycles = battery_config["cycle_limit"]
        capacity = battery_config["energy"]

        max_energy_throughput = max_cycles * capacity  # in MWh

        q1_results = daily_results["Q1"].sort_values(by="revenue", ascending=False)
        q2_results = daily_results["Q2"].sort_values(by="revenue", ascending=False)
        q3_results = daily_results["Q3"].sort_values(by="revenue", ascending=False)

        # calculate how much energy from the battery is needed to fullfill frr request
        q1_requests = q1_results["energy"]
        q2_requests = q2_results["energy"]
        q3_requests = q3_results["energy"]

        q1_battery_activity = q1_requests.copy()
        q1_battery_activity = (
            q1_battery_activity[q1_battery_activity < 0] / battery_config["efficiency"]
        )
        q1_battery_activity = (
            q1_battery_activity[q1_battery_activity > 0] * battery_config["efficiency"]
        )

        q2_battery_activity = q2_requests.copy()
        q2_battery_activity = (
            q2_battery_activity[q2_battery_activity < 0] / battery_config["efficiency"]
        )
        q2_battery_activity = (
            q2_battery_activity[q2_battery_activity > 0] * battery_config["efficiency"]
        )

        q3_battery_activity = q3_requests.copy()
        q3_battery_activity = (
            q3_battery_activity[q1_battery_activity < 0] / battery_config["efficiency"]
        )
        q3_battery_activity = (
            q3_battery_activity[q1_battery_activity > 0] * battery_config["efficiency"]
        )

        # filter the trades until we reach the cycle limitation
        q1_usage = q1_battery_activity[
            abs(q1_battery_activity).cumsum() < max_energy_throughput
        ]
        q2_usage = q2_battery_activity[
            abs(q2_battery_activity).cumsum() < max_energy_throughput
        ]
        q3_usage = q3_battery_activity[
            abs(q3_battery_activity).cumsum() < max_energy_throughput
        ]

        q1_revenue = q1_usage["revenue"].sum()
        q2_revenue = q2_usage["revenue"].sum()
        q3_revenue = q3_usage["revenue"].sum()

        # create df for q1, q2,q3 with the index beeing the quarter hours of the day in datetime format
        timestamp_of_15min = pd.date_range(
            start=day + " 00:00", periods=96, freq="15min"
        )
        timestamp_of_15min = timestamp_of_15min.tz_localize("Europe/Berlin")

        q1_df = pd.DataFrame(index=timestamp_of_15min, columns=["energy", "revenue"])
        q2_df = pd.DataFrame(index=timestamp_of_15min, columns=["energy", "revenue"])
        q3_df = pd.DataFrame(index=timestamp_of_15min, columns=["energy", "revenue"])

        for quarter_hour in range(1, 97):
            # Parse the date
            start_of_day = datetime.strptime(day, "%Y-%m-%d")

            # Calculate the starting time of the quarter hour
            minutes = (quarter_hour - 1) * 15
            timestamp = start_of_day + timedelta(minutes=minutes)

            # Convert the timestamp to Berlin timezone
            t = (
                pd.Timestamp(timestamp)
                .tz_localize("UTC")
                .tz_convert("Europe/Berlin")
                .strftime("%Y-%m-%d %H:%M:%S")
            )

            if quarter_hour in q1_usage.index:
                q1_df.loc[t]["energy"] = q1_usage.loc[quarter_hour]["energy"]
                q1_df.loc[t]["revenue"] = q1_usage.loc[quarter_hour]["revenue"]
            if quarter_hour in q2_usage.index:
                q2_df.loc[t]["energy"] = q2_usage.loc[quarter_hour]["energy"]
                q2_df.loc[t]["revenue"] = q2_usage.loc[quarter_hour]["revenue"]
            if quarter_hour in q3_usage.index:
                q3_df.loc[t]["energy"] = q3_usage.loc[quarter_hour]["energy"]
                q3_df.loc[t]["revenue"] = q3_usage.loc[quarter_hour]["revenue"]

        revenue_range = [q1_revenue, q2_revenue, q3_revenue]

        return q2_df

    def calculate_revenue(self, delivered_energy, qh):
        """
        Calculates the revenue based on delivered energy and clearing price for a specific quarter-hour.

        Args:
            delivered_energy (float): Delivered energy in MWh.
            qh (int): Quarter-hour index.

        Returns:
            float: Revenue for the quarter-hour.
        """
        # get the clearing price of that qh
        start_time = (
            (qh - 1) * 15 * 60 // 1
        )  # Convert minutes to intervals of 4 seconds
        end_time = qh * 15 * 60 // 1  # Same conversion
        clearing_price = self.clearing_data.iloc[start_time:end_time][
            "clearing_price"
        ].sum()
        revenue = delivered_energy * clearing_price

        return revenue

    def calculate_daily_revenue(
        self, soc_marge, id1_prices, ida_prices, battery_config
    ):
        """
        Calculates daily results for aFRR energy, including SOC management and revenue.

        Args:
            soc_marge (float): SOC margin for balancing.
            id1_prices (pd.DataFrame): Intraday prices for balancing energy.
            ida_prices (pd.DataFrame): Intraday prices for aFRR activation bidding.
            battery_config (dict): Battery configuration with SOC and energy limits.
        Returns:
            pd.DataFrame: Daily results with energy, revenue, and SOC data.
        """
        result_afrr_energy = pd.DataFrame(
            columns=[
                "afrr_energy_pos",
                "afrr_energy_neg",
                "afrr_revenue",
                "balancing_energy_sell",
                "balancing_energy_buy",
                "balancing_revenue",
                "soc",
                "total_bat_power",
            ],
            index=range(1, 97),
            data=0,
        )
        cycle_check = True
        power_limit_afrr = self.market_config_energy["marketable_power"]

        soc = pd.Series(index=range(1, 97), data=0.5)

        cycle_counter = 0

        for qh in range(1, 97):
            ### ---------------------------------------------
            ### GET CURRENT VALUES
            ### ---------------------------------------------

            current_power_limit = power_limit_afrr[qh]
            if qh == 1:
                current_soc = soc[qh]
            else:
                current_soc = soc[qh - 1]
            # adjust soc marge if upper and lower soc boundary are too close
            if self.max_soc_marketable[qh] - self.min_soc_marketable[qh] < soc_marge:
                soc_marge = (
                    self.max_soc_marketable[qh] - self.min_soc_marketable[qh]
                ) / 2

            ### ---------------------------------------------
            ### SOC MANAGEMENT
            ### ---------------------------------------------
            curent_id1_price = id1_prices.loc[
                id1_prices["QuarterHour"] == qh, "price"
            ].values[0]
            p_balancing = 0

            if qh > 88:
                self.max_soc_marketable[qh] = 0.5
                self.min_soc_marketable[qh] = 0.5
                soc_marge = 0

            if current_soc > self.max_soc_marketable[qh] - soc_marge:
                surplus_energy = (
                    current_soc - (self.max_soc_marketable[qh] - soc_marge)
                ) * battery_config["energy"]  # positive values for selling electricity
                trade_energy = min(
                    current_power_limit * battery_config["efficiency"] * 0.25,
                    surplus_energy * battery_config["efficiency"],
                )  # in MWh  --> negative values for buying electricity
                p_balancing = (
                    trade_energy / battery_config["efficiency"] / 0.25
                )  # power to be dischaged every second in the coming 15 minutes
                discharged_energy = (
                    p_balancing * 0.25
                )  # energy to be actually discharged in the coming 15 minutes

                result_afrr_energy.loc[qh, "balancing_energy_sell"] = 1 * trade_energy
                result_afrr_energy.loc[qh, "balancing_energy_buy"] = 0
                result_afrr_energy.loc[qh, "balancing_revenue"] = (
                    curent_id1_price * trade_energy
                )
                print(
                    f"Charge management in qh:{qh}: discharged energy: {trade_energy}; revenue {curent_id1_price * trade_energy} EUR"
                )

                current_soc -= (
                    discharged_energy / battery_config["energy"]
                )  # trade_energy has positive sign / we sell energy
                # result_afrr_energy.loc[qh, 'soc'] = soc[qh]

            elif current_soc < self.min_soc_marketable[qh] + soc_marge:
                surplus_energy = (
                    current_soc - (self.min_soc_marketable[qh] + soc_marge)
                ) * battery_config["energy"]
                trade_energy = max(
                    -1 * current_power_limit / battery_config["efficiency"] * 0.25,
                    surplus_energy / battery_config["efficiency"],
                )  # in MWh  --> negative values for buying electricity
                p_balancing = (
                    trade_energy * battery_config["efficiency"] / 0.25
                )  # power to be actually charged every second in the coming 15 minutes
                charged_energy = (
                    p_balancing * 0.25
                )  # energy to be actually charged in the coming 15 minutes

                result_afrr_energy.loc[qh, "balancing_energy_buy"] = (
                    -1 * trade_energy
                )  # will show positive values in the csv
                result_afrr_energy.loc[qh, "balancing_energy_sell"] = 0
                result_afrr_energy.loc[qh, "balancing_revenue"] = (
                    curent_id1_price * trade_energy
                )
                print(
                    f"Charge management in qh:{qh}: charged energy {charged_energy} MWh; revenue {curent_id1_price * trade_energy} EUR"
                )

                current_soc += (
                    -1 * charged_energy / battery_config["energy"]
                )  # trade_energy has negative sign / we buy energy

            ### ---------------------------------------------
            ### aFRR ACTIVATION
            ### ---------------------------------------------

            current_ida_price = ida_prices.loc[
                id1_prices["QuarterHour"] == qh, "0"
            ].values[0]

            if not pd.isna(current_ida_price):
                bid_price_negative = current_ida_price - abs(current_ida_price) * 0.5
                bid_price_positive = current_ida_price + abs(current_ida_price) * 0.5

            # if cycle limit is reached, we don't participate in bidding
            if cycle_check:
                # calculate the threshold power for each qh for both directions
                threshold_power_matrix = self.calculate_threshold_power(
                    qh, bid_price_negative, bid_price_positive
                )

                # max power we can offer to not exceed soc limits
                if qh > 88:  # For the last 8 qh of the day
                    soc_limit_neg = max(0.5 - current_soc, 0)
                    soc_limit_pos = max(current_soc - 0.5, 0)
                else:
                    soc_limit_neg = self.max_soc[qh] - current_soc
                    soc_limit_pos = current_soc - self.min_soc[qh]

                power_limit_pos = soc_limit_pos * battery_config["energy"] / 0.25
                power_limit_neg = soc_limit_neg * battery_config["energy"] / 0.25

                # calculate how much energy we have to deliver in this qh
                energy_delivered_qh, revenue_qh = self.calculate_energy_to_deliver(
                    battery_config,
                    threshold_power_matrix,
                    qh,
                    power_limit_pos,
                    power_limit_neg,
                    bid_price_negative,
                    bid_price_positive,
                    p_balancing,
                )  # positive value for charging (positive aFRR)

                print(
                    f"aFRR activation in qh {qh} is {energy_delivered_qh} MWh with revenue {revenue_qh} EUR"
                )
                soc[qh] = current_soc - energy_delivered_qh / battery_config["energy"]
            else:
                energy_delivered_qh = 0
                revenue_qh = 0
                soc[qh] = current_soc
                print(f"No participation in qh {qh}")

            ### ---------------------------------------------
            ### SAVE DATA
            ### ---------------------------------------------

            if energy_delivered_qh > 0:  # positive afrr
                result_afrr_energy.loc[qh, "afrr_energy_pos"] = energy_delivered_qh
                result_afrr_energy.loc[qh, "afrr_energy_neg"] = 0
            else:  # negative afrr
                result_afrr_energy.loc[qh, "afrr_energy_pos"] = 0
                result_afrr_energy.loc[qh, "afrr_energy_neg"] = -1 * energy_delivered_qh
            result_afrr_energy.loc[qh, "afrr_revenue"] = revenue_qh
            result_afrr_energy.loc[qh, "soc"] = soc[qh]
            result_afrr_energy.loc[qh, "total_bat_power"] = (
                p_balancing + energy_delivered_qh / 0.25
            )

            ### ---------------------------------------------
            ### CYCLE CHECK
            ### ---------------------------------------------
            if qh > 1:
                cycle_counter += abs(soc[qh] - soc[qh - 1]) / 2
            else:
                cycle_counter += abs(soc[qh] - 0.5) / 2

            if cycle_counter > self.cycle_limit:
                cycle_check = False
                print(f"Cycle limit reached in qh {qh}")

        return result_afrr_energy

    def save_clearing_data(self, day, folderpath):
        """
        Saves the clearing data to a CSV file.

        Args:
            day (str): The day for which clearing data is saved.
            folderpath (str): Path to the folder where the file is saved.
        """
        # save the clearing data to a csv file
        file_name = f"{day}_aFRR_clearing_data.csv"
        path = os.path.join(folderpath, file_name)
        self.clearing_data.to_csv(path, sep=";")
