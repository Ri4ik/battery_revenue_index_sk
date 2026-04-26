import pandas as pd
import os
import sqlalchemy
import json

class DBConnector:

    def __init__(self):
        # get directory of the file
        dir_path = os.path.dirname(os.path.realpath(__file__))
        # get the path of the access_info_db.json file
        access_info_path = os.path.join(dir_path, '..', "access_info_db.json")
        try:
            with open(access_info_path) as f:
                access_data = json.load(f)
                self.host_name = access_data["host"]
                self.port_num = access_data["port"]
                self.user_name = access_data["user"]
                self.pw = access_data["password"]
                self.database = access_data["database"]
                self.last_update = access_data["last_update"]
        except FileNotFoundError:
            print(f"Error: The file {access_info_path} was not found.")
            print("Using local files.")
    
    def get_data_from_db(self, table, query):
        engine = sqlalchemy.create_engine(
            f"mysql+pymysql://{self.user_name}:{self.pw}@{self.host_name}:{self.port_num}/{self.database}")
        data = pd.read_sql_query(query, con=engine)
        engine.dispose()
        return data
    
    def get_afrr_merit_order_from_db(self, day):
        engine = sqlalchemy.create_engine(
            f"mysql+pymysql://{self.user_name}:{self.pw}@{self.host_name}:{self.port_num}/{'Marketprices'}")
        
        query = f"""
        SELECT * 
        FROM afrr_energy_bids 
        WHERE DATE(DELIVERY_DATE) = '{day}'
        """

        # Execute the query and load data into a DataFrame
        merit_order_afrr = pd.read_sql_query(query, con=engine)
        engine.dispose()

        return merit_order_afrr
    
    def get_afrr_activation_data(self, day):
        
        engine = sqlalchemy.create_engine(
            f"mysql+pymysql://{self.user_name}:{self.pw}@{self.host_name}:{self.port_num}/{'Marketprices'}")
        #TIMESTAMP(DATE + INTERVAL TIME SECOND) AS datetime
        
        query = f"""
            SELECT 
            *, 
            TIMESTAMP(DATE + INTERVAL TIME SECOND) AS datetime
            FROM afrr_activation
            WHERE DATE(DATE) = '{day}'
            """

        # Execute the query and load data into a DataFrame
        activation_data = pd.read_sql_query(query, con=engine)
        engine.dispose()

        return activation_data

    def get_fcr_prices(self, day):
        engine = sqlalchemy.create_engine(
            f"mysql+pymysql://{self.user_name}:{self.pw}@{self.host_name}:{self.port_num}/{'Marketprices'}")
        
        query = f"""
            SELECT *
            FROM fcr
            WHERE DATE(DATE_FROM) = '{day}' AND TENDER_NUMBER = 1
            """
        
        finalizer_fcr = pd.read_sql_query(query, con=engine)
        engine.dispose()
        
        # make column DATE_FROM to datetime_index
        finalizer_fcr['DATE_FROM'] = pd.to_datetime(finalizer_fcr['DATE_FROM'])
        finalizer_fcr.set_index('DATE_FROM', inplace=True)
        
        # capacity_prices = pd.DataFrame(index = range(6), columns = ['POS', 'NEG'])
        
        # for i in range(6):
        #     capacity_prices.loc[i]['POS'] = finalizer_fcr.iloc[i]
        #     capacity_prices.loc[i]['NEG'] = finalizer_fcr.iloc[i+6]

        return finalizer_fcr
    
    def get_fcr_prices_range(self, start_date, end_date):
        engine = sqlalchemy.create_engine(
            f"mysql+pymysql://{self.user_name}:{self.pw}@{self.host_name}:{self.port_num}/{'Marketprices'}")
        
        query = f"""
            SELECT *
            FROM fcr
            WHERE DATE(DATE_FROM) >= '{start_date}' AND DATE(DATE_FROM) <= '{end_date}' AND TENDER_NUMBER = 1
            """
        
        finalizer_fcr = pd.read_sql_query(query, con=engine)
        engine.dispose()
        
        # make column DATE_FROM to datetime_index
        finalizer_fcr['DATE_FROM'] = pd.to_datetime(finalizer_fcr['DATE_FROM'])
        finalizer_fcr.set_index('DATE_FROM', inplace=True)
        
        # capacity_prices = pd.DataFrame(index = range(6), columns = ['POS', 'NEG'])
        
        # for i in range(6):
        #     capacity_prices.loc[i]['POS'] = finalizer_fcr.iloc[i]
        #     capacity_prices.loc[i]['NEG'] = finalizer_fcr.iloc[i+6]

        return finalizer_fcr
    
    def get_afrr_capacity_prices(self, day):
        engine = sqlalchemy.create_engine(
            f"mysql+pymysql://{self.user_name}:{self.pw}@{self.host_name}:{self.port_num}/{'Marketprices'}")
        
        query = f"""
            SELECT 
            *
            FROM afrr_capacity
            WHERE DATE(DATE_FROM) = '{day}'
            """

        # Execute the query and load data into a DataFrame
        finalizer_afrr = pd.read_sql_query(query, con=engine)
        engine.dispose()

        return finalizer_afrr
    
    def get_afrr_capacity_prices_range(self, start_date, end_date):
        engine = sqlalchemy.create_engine(
            f"mysql+pymysql://{self.user_name}:{self.pw}@{self.host_name}:{self.port_num}/{'Marketprices'}")
        
        query = f"""
            SELECT 
            *
            FROM afrr_capacity
            WHERE DATE(DATE_FROM) >= '{start_date}' AND DATE(DATE_FROM) <= '{end_date}'
            """

        # Execute the query and load data into a DataFrame
        finalizer_afrr = pd.read_sql_query(query, con=engine)
        engine.dispose()

        return finalizer_afrr
        
        
        
    def get_id1_prices_from_db(self, day):
        engine = sqlalchemy.create_engine(
            f"mysql+pymysql://{self.user_name}:{self.pw}@{self.host_name}:{self.port_num}/{'Intraday_Continuous'}")
        
        # Convert input day (German time) to UTC for database query
        day_dt = pd.to_datetime(day)
        
        # Determine if day is in DST or standard time
        is_dst = bool(pd.Timestamp(day_dt).tz_localize('Europe/Berlin').dst())
        utc_offset = 2 if is_dst else 1  # Hours to subtract to get UTC
        
        # Create day boundary times in UTC - following the same approach as get_da_prices
        start_time = day_dt.replace(hour=0, minute=0, second=0) - pd.Timedelta(hours=utc_offset)
        end_time = start_time + pd.Timedelta(days=1) - pd.Timedelta(seconds=1)
        
        query = f"""
        SELECT * 
        FROM idc_index 
        WHERE TimeResolution = '15min' 
        AND IndexName = 'ID1' 
        AND DeliveryStart >= '{start_time}' AND DeliveryStart < '{end_time}'
        """

        # Execute the query and load data into a DataFrame
        filtered_ID1 = pd.read_sql_query(query, con=engine)
        engine.dispose()

        # Convert DeliveryStart to German time
        filtered_ID1['DeliveryStart'] = pd.to_datetime(filtered_ID1['DeliveryStart']) + pd.Timedelta(hours=utc_offset)
        filtered_ID1['date'] = filtered_ID1['DeliveryStart'].dt.date
        filtered_ID1['QuarterHour'] = range(1, len(filtered_ID1) + 1)

        return filtered_ID1

    def get_id1_prices_range(self, start_date, end_date):
        engine = sqlalchemy.create_engine(
            f"mysql+pymysql://{self.user_name}:{self.pw}@{self.host_name}:{self.port_num}/{'Intraday_Continuous'}")
        
        # Convert input dates (German time) to UTC for database query
        start_dt = pd.to_datetime(start_date)
        end_dt = pd.to_datetime(end_date)
        
        # Determine if dates are in DST
        start_is_dst = bool(pd.Timestamp(start_dt).tz_localize('Europe/Berlin').dst())
        end_is_dst = bool(pd.Timestamp(end_dt).tz_localize('Europe/Berlin').dst())
        
        start_utc_offset = 2 if start_is_dst else 1
        end_utc_offset = 2 if end_is_dst else 1
        
        # Create boundary times in UTC
        utc_start = start_dt.replace(hour=0, minute=0, second=0) - pd.Timedelta(hours=start_utc_offset)
        utc_end = end_dt.replace(hour=23, minute=59, second=59) - pd.Timedelta(hours=end_utc_offset)
        
        # Format dates for query
        utc_start_date = utc_start.strftime('%Y-%m-%d')
        utc_end_date = utc_end.strftime('%Y-%m-%d')
        
        query = f"""
        SELECT * 
        FROM idc_index 
        WHERE TimeResolution = '15min' 
        AND IndexName = 'ID1' 
        AND DATE(DeliveryStart) >= '{utc_start_date}' AND DATE(DeliveryStart) <= '{utc_end_date}'
        """

        # Execute the query and load data into a DataFrame
        filtered_ID1 = pd.read_sql_query(query, con=engine)
        engine.dispose()

        # Convert DeliveryStart to German time - use appropriate offset for each row
        # This is more complex as different days might have different DST settings
        filtered_ID1['DeliveryStart'] = filtered_ID1.apply(
            lambda row: pd.to_datetime(row['DeliveryStart']) + pd.Timedelta(
                hours=(2 if bool(pd.Timestamp(row['DeliveryStart']).tz_localize('UTC').tz_convert('Europe/Berlin').dst()) else 1)
            ), 
            axis=1
        )
        
        filtered_ID1['date'] = filtered_ID1['DeliveryStart'].dt.date
        filtered_ID1['QuarterHour'] = range(1, len(filtered_ID1) + 1)

        return filtered_ID1

    def get_ida_prices_from_db(self,day):
        engine = sqlalchemy.create_engine(
            f"mysql+pymysql://{self.user_name}:{self.pw}@{self.host_name}:{self.port_num}/{'Marketprices'}")
           
        query = f"""
        SELECT * 
        FROM IDA1_prices 
        """

        # Execute the query and load data into a DataFrame
        filtered_IDA1 = pd.read_sql_query(query, con=engine)
        engine.dispose()
        
        # filter the day
        filtered_IDA1["Delivery day"] = pd.to_datetime(filtered_IDA1["Delivery day"]).dt.date
        day = pd.to_datetime(day).date()
        filtered_IDA1 = filtered_IDA1.loc[filtered_IDA1["Delivery day"] == day]
        
        # index for filtered_df with len
        filtered_IDA1 = filtered_IDA1.reset_index(drop=True)
        # delete columns "Hour 3B Q1" to "Hour 3B Q4"
        #filtered_IDA1 = filtered_IDA1.drop(columns=["Hour 3B Q1", "Hour 3B Q2", "Hour 3B Q3", "Hour 3B Q4"])
        prices = pd.Series(index=range(1, 97), dtype=float)
        for i in range(1,97):
            prices.loc[i] = filtered_IDA1.iloc[0, i] 
            
        # add an index with the quarter hours of the day
        prices.index = pd.date_range(start=day, periods=96, freq='15T')

        return prices
            
    def get_da_prices(self, day):
        engine = sqlalchemy.create_engine(
            f"mysql+pymysql://{self.user_name}:{self.pw}@{self.host_name}:{self.port_num}/{'Marketprices'}")
        
        # Convert input day (German time) to UTC for database query
        day_dt = pd.to_datetime(day)
        
        # Determine if day is in DST or standard time
        is_dst = bool(pd.Timestamp(day_dt).tz_localize('Europe/Berlin').dst())
        utc_offset = 2 if is_dst else 1  # Hours to subtract to get UTC
        
        # Create day boundary times in UTC
        start_time = day_dt.replace(hour=0, minute=0, second=0) - pd.Timedelta(hours=utc_offset)
        end_time = start_time + pd.Timedelta(days=1) - pd.Timedelta(seconds=1)
        
        query = f"""
            SELECT price, timestamp
            FROM day_ahead
            WHERE timestamp >= '{start_time}' AND timestamp < '{end_time}'
            ORDER BY timestamp
            """

        # Execute the query and load data into a DataFrame
        finalizer_da = pd.read_sql_query(query, con=engine)
        engine.dispose()

        # Convert timestamps back to German time for consistency in your application
        finalizer_da['timestamp'] = finalizer_da['timestamp'] + pd.Timedelta(hours=utc_offset)
        
        return finalizer_da
    
    def get_da_prices_range(self, start_date, end_date):
        engine = sqlalchemy.create_engine(
            f"mysql+pymysql://{self.user_name}:{self.pw}@{self.host_name}:{self.port_num}/{'Marketprices'}")
        
        # Convert input dates (German time) to UTC for database query
        start_dt = pd.to_datetime(start_date)
        end_dt = pd.to_datetime(end_date)
        
        # Determine if dates are in DST
        start_is_dst = bool(pd.Timestamp(start_dt).tz_localize('Europe/Berlin').dst())
        end_is_dst = bool(pd.Timestamp(end_dt).tz_localize('Europe/Berlin').dst())
        
        start_utc_offset = 2 if start_is_dst else 1
        end_utc_offset = 2 if end_is_dst else 1
        
        # Create boundary times in UTC
        utc_start = start_dt.replace(hour=0, minute=0, second=0) - pd.Timedelta(hours=start_utc_offset)
        utc_end = end_dt.replace(hour=23, minute=59, second=59) - pd.Timedelta(hours=end_utc_offset)
        
        query = f"""
            SELECT price, timestamp
            FROM day_ahead
            WHERE timestamp >= '{utc_start}' AND timestamp <= '{utc_end}'
            ORDER BY timestamp
            """

        # Execute the query and load data into a DataFrame
        finalizer_da = pd.read_sql_query(query, con=engine)
        engine.dispose()
        
        # You might want to convert timestamps back to German time here
        # depending on your application needs
        
        return finalizer_da


    def get_transaction_data(self, side, start_date, end_date):
        # Convert input dates (German time) to UTC for database query
        start_dt = pd.to_datetime(start_date)
        end_dt = pd.to_datetime(end_date)
        
        # Determine if dates are in DST
        start_is_dst = bool(pd.Timestamp(start_dt).tz_localize('Europe/Berlin').dst())
        end_is_dst = bool(pd.Timestamp(end_dt).tz_localize('Europe/Berlin').dst())
        
        start_utc_offset = 2 if start_is_dst else 1
        end_utc_offset = 2 if end_is_dst else 1
        
        # Convert to UTC
        utc_start_date = start_dt - pd.Timedelta(hours=start_utc_offset)
        utc_end_date = end_dt - pd.Timedelta(hours=end_utc_offset)
        
        # Create day boundary times for the query
        utc_start_of_day = utc_end_date.replace(hour=0, minute=0, second=0)
        utc_end_of_day = utc_start_of_day.replace(hour=23, minute=45, second=0)
        
        current_year = pd.to_datetime(end_date).year
        
        engine = sqlalchemy.create_engine(
            f"mysql+pymysql://{self.user_name}:{self.pw}@{self.host_name}:{self.port_num}/{'Intraday_Continuous'}")

        if current_year == 2024:
            query = f""" 
                SELECT
                ExecutionTime,
                DeliveryStart,
                Price, 
                Volume
                FROM
                idc_transaction_2024
                WHERE
                (ExecutionTime BETWEEN '{utc_start_date}' AND '{utc_end_of_day}')
                AND (Product ='XBID_Quarter_Hour_Power' or Product = 'Intraday_Quarter_Hour_Power') 
                AND Side='{side}' 
                AND DeliveryStart < '{utc_end_date}' 
                AND DeliveryStart >= '{utc_start_of_day}'
                """
        elif current_year == 2025:
            query = f""" 
                SELECT
                ExecutionTime,
                DeliveryStart,
                Price, 
                Volume
                FROM
                idc_transaction_2025
                WHERE
                (ExecutionTime BETWEEN '{utc_start_date}' AND '{utc_end_of_day}')
                AND (Product ='XBID_Quarter_Hour_Power' or Product = 'Intraday_Quarter_Hour_Power') 
                AND Side='{side}' 
                AND DeliveryStart < '{utc_end_date}' 
                AND DeliveryStart >= '{utc_start_of_day}'
                """

        df = pd.read_sql_query(query, con=engine)
        engine.dispose()
        
        # Convert timestamps back to German time
        if not df.empty:
            if 'ExecutionTime' in df.columns:
                df['ExecutionTime'] = pd.to_datetime(df['ExecutionTime']) + pd.Timedelta(hours=end_utc_offset)
            if 'DeliveryStart' in df.columns:
                df['DeliveryStart'] = pd.to_datetime(df['DeliveryStart']) + pd.Timedelta(hours=end_utc_offset)

        return df
    
    def get_order_book_data(self, start_date, end_date):
        
        start_of_day = pd.to_datetime(end_date) - pd.Timedelta(hours=2)
        start_of_day = start_of_day.replace(hour=0, minute=0).tz_localize(None)

        end_of_day = start_of_day
        end_of_day = end_of_day.replace(hour=23, minute=45)
        
        year = pd.to_datetime(end_date).year
        month = pd.to_datetime(end_date).month
        
        # format month to mm
        if month < 10:
            month = '0' + str(month)
            
        table_name = f"idc_orders_{year}_{month}"

        engine = sqlalchemy.create_engine(
            f"mysql+pymysql://{self.user_name}:{self.pw}@{self.host_name}:{self.port_num}/{'Intraday_Continuous'}")

        query = f""" 
                *
                FROM
                {table_name}
                WHERE
                (ExecutionTime BETWEEN '{start_date}' AND '{end_of_day}')
                AND (Product ='XBID_Quarter_Hour_Power' or Product = 'Intraday_Quarter_Hour_Power') AND deliverystart < '{end_date}' AND deliverystart >= '{start_of_day}'
                """

        df = pd.read_sql_query(query, con=engine)
        engine.dispose()

        return df

    def get_transaction_trades(self, execution_time_start, execution_time_end, start_of_day, end_of_day):
        # Convert input times (German time) to UTC for database query
        execution_start_dt = pd.to_datetime(execution_time_start)
        execution_end_dt = pd.to_datetime(execution_time_end)
        start_day_dt = pd.to_datetime(start_of_day)
        end_day_dt = pd.to_datetime(end_of_day)
        
        # Determine if dates are in DST
        is_start_dst = bool(pd.Timestamp(execution_start_dt).tz_localize('Europe/Berlin').dst())
        is_end_dst = bool(pd.Timestamp(execution_end_dt).tz_localize('Europe/Berlin').dst())
        
        start_utc_offset = 2 if is_start_dst else 1
        end_utc_offset = 2 if is_end_dst else 1
        
        # Convert to UTC
        utc_execution_start = execution_start_dt - pd.Timedelta(hours=start_utc_offset)
        utc_execution_end = execution_end_dt - pd.Timedelta(hours=end_utc_offset)
        utc_start_day = start_day_dt - pd.Timedelta(hours=start_utc_offset)
        utc_end_day = end_day_dt - pd.Timedelta(hours=end_utc_offset)

        engine = sqlalchemy.create_engine(
            f"mysql+pymysql://{self.user_name}:{self.pw}@{self.host_name}:{self.port_num}/{'Intraday_Continuous'}")

        query = f"""
                SELECT 
                *
                FROM 
                idc_transaction
                WHERE 
                (executiontime BETWEEN '{utc_execution_start}' AND '{utc_execution_end}')
                AND (product ='XBID_Quarter_Hour_Power' or product = 'Intraday_Quarter_Hour_Power')  
                AND deliverystart < '{utc_end_day}' AND deliverystart >= '{utc_start_day}'
                AND side = 'BUY'
                """

        df = pd.read_sql_query(query, con=engine)
        engine.dispose()
        
        # Convert timestamps back to German time
        if not df.empty:
            if 'executiontime' in df.columns:
                df['executiontime'] = pd.to_datetime(df['executiontime']) + pd.Timedelta(hours=end_utc_offset)
            if 'deliverystart' in df.columns:
                df['deliverystart'] = pd.to_datetime(df['deliverystart']) + pd.Timedelta(hours=end_utc_offset)

        return df