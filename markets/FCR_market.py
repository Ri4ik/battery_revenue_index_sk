import pandas as pd
import os


class FCRmarket:
    def __init__(self, market_config, battery_config, day, db):
        """
        Initializes the FCRmarket class with the given configurations and database connection.

        Args:
            market_config (dict): Configuration for the FCR market.
            battery_config (dict): Configuration for the battery, including energy and power limits.
            day (str): The day for which the market is being initialized.
            db (object): Database connection object for retrieving market data.
        """
        raw_data_path = "marketdata"
        market_path = "FCR"
        folder_path = os.path.join(raw_data_path, market_path)
        self.prices = self.get_fcr_prices(day, folder_path, db)
        self.market_config = market_config
        self.battery_config = battery_config
        self.set_marketable_power(battery_config, market_config)
        _, self.revenue = self.calculate_fcr_revenue(
            self.prices, market_config, battery_config
        )

    def get_fcr_prices(self, day, folder_path=None, db=None):
        """
        Retrieves FCR prices from a local file for the given day.

        Args:
            file_path (str): Path to the file containing FCR prices.
            day (str): The day for which FCR prices are retrieved.

        Returns:
            pd.Series: FCR prices for the specified day.
        """
        file_path = os.path.join(folder_path, f"FCR_{day}.csv")
        try:
            fcr_data = pd.read_csv(file_path, index_col=0, parse_dates=True)
            fcr_prices_ger = fcr_data["GERMANY_SETTLEMENTCAPACITY_PRICE_[EUR/MW]"]
        except FileNotFoundError:
            print(f"File {file_path} not found.")
            print("Trying to download the data from the database instead...")
            try:
                fcr_data = db.get_fcr_prices(day)
                fcr_prices_ger = fcr_data["GERMANY_SETTLEMENTCAPACITY_PRICE_[EUR/MW]"]
                fcr_prices_ger.index = pd.to_datetime(fcr_prices_ger.index)
                os.makedirs(folder_path, exist_ok=True)
                fcr_prices_ger.to_csv(file_path)
            except:
                raise Exception(f"Failed to get fcr data for {day}.")

        return fcr_prices_ger

    def get_fcr_prices_from_db(self, day, db):
        """
        Retrieves FCR prices from the database for the given day.

        Args:
            day (str): The day for which FCR prices are retrieved.
            db (object): Database connection object.

        Returns:
            pd.Series: FCR prices for the specified day.
        """
        # read xlsx file as downloaded from website
        fcr_data = db.get_fcr_prices(day)
        # fcr_data = all_fcr_data[all_fcr_data['TENDER_NUMBER']==1]
        fcr_prices_ger = fcr_data["GERMANY_SETTLEMENTCAPACITY_PRICE_[EUR/MW]"]

        # convert day to datetime
        day = pd.to_datetime(day)
        # convert index to datetime
        fcr_prices_ger.index = pd.to_datetime(fcr_prices_ger.index)

        return fcr_prices_ger

    def calculate_fcr_revenue(self, fcr_prices, market_config, battery_config):
        """
        Calculates the revenue for FCR based on prices, market configuration, and battery configuration.

        Args:
            fcr_prices (pd.Series): FCR prices for the day.
            market_config (dict): Market configuration with power share.
            battery_config (dict): Battery configuration with power and energy limits.

        Returns:
            pd.Series: Revenue for each 4-hour block of the day.
        """
        # Calculate the revenue for FCR based on the prices
        fcr_revenue = fcr_prices * self.marketable_power  # in € per 4 hour slot

        daily_revenue = fcr_revenue.sum()  # total revenue for the day

        block_revenue = pd.Series(index=range(6), data=0.0)
        for i in range(6):
            block_revenue[i] = fcr_revenue[i]

        return daily_revenue, block_revenue

    def set_marketable_power(self, battery_config, market_config):
        """
        Sets the marketable power and SOC boundaries based on battery and market configurations.

        Args:
            battery_config (dict): Battery configuration with power and energy limits.
            market_config (dict): Market configuration with power share.

        Notes:
            - Marketable power is calculated based on PQ-regulations formula 3.9.
            - SOC boundaries are calculated based on PQ-regulations formulas 3.7 and 3.8.
        """
        # What we can can max from aFRR regulations and power splitting
        self.marketable_power = (
            battery_config["power"] * market_config["power_share"] / 1.25
        )  # based on PQ-regulations formula 3.9
        self.reserved_power = battery_config["power"] * market_config["power_share"]

        self.lower_soc_boundary = (
            0.25 * self.marketable_power / battery_config["energy"]
        )  # based on PQ-regulations formula 3.8
        self.upper_soc_boundary = (
            1 - 0.25 * self.marketable_power / battery_config["energy"]
        )  # based on PQ-regulations formula 3.7
