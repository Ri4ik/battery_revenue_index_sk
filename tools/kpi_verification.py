import os
import pandas as pd
import json
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import seaborn as sns

def check_cycle_limit(soc_series, limit):
    abs_changes = soc_series.diff().abs()  # Calculate absolute changes between consecutive SOC values.
    total_abs_change_sum = abs_changes.sum()  # Sum of all absolute changes.

    return total_abs_change_sum / (2 * 1)  # Divide by 2 to get cycle count.

def check_max_soc(soc_series, limit):
    max_soc = soc_series.max()
    return max_soc

def check_min_soc(soc_series, limit):
    min_soc = soc_series.min()
    return min_soc

def check_power(soc_series, cap, limit):
    power = soc_series.diff().abs() * cap * 4
    power = power.fillna(0).max()
    return power

def get_energy_throughput(soc_series, battery_capacity):
    #soc_series = soc_series.reset_index(drop=True)
    energy_throughput = soc_series.diff().abs().sum() * battery_capacity  # Calculate absolute changes between consecutive SOC values.
    return energy_throughput

def get_revenue_per_throughput(energy_throughput, revenue):
    return revenue / energy_throughput


soc_df = pd.DataFrame()

# Define the folder path containing your JSON files
foldername = 'results_2025-03-25_16-38'
folder_path = os.path.join('results',
                           foldername)  # Replace with your actual folder path

afrr_df = pd.DataFrame(columns=['Date', 'energy_throughput', 'revenue', 'revenue_per_throughput'])
# Iterate through each file in the folder
for filename in os.listdir(folder_path):
    if filename.endswith('_results_aFRR.json'):
        # Construct the full file path
        file_path = os.path.join(folder_path, filename)

        # Extract the day from the filename (assuming format is YYYY-MM-DD)
        day = filename.split('_')[0]  # Get '2024-09-01' part
        

        # Open and read the JSON file
        with open(file_path, 'r') as file:
            data = json.load(file)

            battery_config = data.get("battery_config", {})
            soc_data = data.get("market_results", {}).get("SOC", {})
            
            afrr_energy_revenue = data.get("results", {}).get("daily_revenue", {}).get("aFRR_Energy", 0)
            afrr_capacity_revenue = data.get("results", {}).get("daily_revenue", {}).get("aFRR_Capacity", 0)
            

            if isinstance(soc_data, dict) and len(soc_data) > 0:
                # Convert SOC dictionary into a DataFrame
                temp_df = pd.DataFrame(list(soc_data.items()), columns=['Datetime', 'SOC'])

                # Convert Datetime column to datetime type and set it as index
                temp_df['Datetime'] = pd.to_datetime(temp_df['Datetime'])

                # Add a column for Date (the date part only)
                temp_df['Date'] = temp_df['Datetime'].dt.date

                # Append to main DataFrame with Date as index (optional)
                soc_df = pd.concat([soc_df, temp_df], ignore_index=True)
                
                energy_throughput = get_energy_throughput(temp_df['SOC'], battery_config['energy'])
                revenue_per_throughput = get_revenue_per_throughput(energy_throughput, afrr_energy_revenue)
                temp_df_afrr = pd.DataFrame({'Date': [temp_df['Datetime'].dt.date[0]], 'energy_throughput': [energy_throughput], 'revenue': [afrr_energy_revenue], 'revenue_per_throughput': [revenue_per_throughput]})
                afrr_df = pd.concat([afrr_df, temp_df_afrr], ignore_index=True)
                print(f"SOC data extracted successfully for {filename}.")
            else:
                print(f"No valid SOC data found for {filename}. Skipping.")

if not soc_df.empty:
    cycle_limit_df = soc_df.groupby('Date')['SOC'].apply(lambda x: check_cycle_limit(x,battery_config['cycle_limit'])).reset_index()
    cycle_limit_df.columns = ['Date', 'cycle_limit']
    max_soc_df = soc_df.groupby('Date')['SOC'].apply(lambda x: check_max_soc(x, battery_config['maxSOC'])).reset_index()
    max_soc_df.columns = ['Date', 'max_soc']
    min_soc_df = soc_df.groupby('Date')['SOC'].apply(lambda x: check_min_soc(x, battery_config['minSOC'])).reset_index()
    min_soc_df.columns = ['Date', 'min_soc']
    power_df = soc_df.groupby('Date')['SOC'].apply(lambda x: check_power(x, battery_config['energy'], battery_config['power'])).reset_index()
    power_df.columns = ['Date', 'power']
    kpi_df = pd.concat([cycle_limit_df, max_soc_df, min_soc_df, power_df, afrr_df], axis=1)
    kpi_df = kpi_df.loc[:, ~kpi_df.columns.duplicated()]
    
    kpi_df.to_csv('kpi_verification_afrr_2h2c.csv', index=False, sep=';')

    fig, [ax1, ax2, ax3, ax4] = plt.subplots(4, 1, figsize=(14, 6), sharex=True, tight_layout=True)
    sns.barplot(x='Date', y='cycle_limit', data=kpi_df, ax=ax1, hue=kpi_df['cycle_limit'] > battery_config['cycle_limit'])
    for i, row in kpi_df.iterrows():
        if row['cycle_limit'] > battery_config['cycle_limit']:
            ax1.text(
                str(row['Date']),
                row['cycle_limit'] + 0.01,
                row['Date'].strftime('%m-%d'),
                ha='center', va='bottom', fontsize=6, color='red', rotation=90
            )

    ax1.hlines(battery_config['cycle_limit'],str(kpi_df['Date'].min()), str(kpi_df['Date'].max()), color='red')
    sns.barplot(x='Date', y='max_soc', data=kpi_df, ax=ax2, hue=kpi_df['max_soc'] > battery_config['maxSOC'])

    for i, row in kpi_df.iterrows():
        if row['max_soc'] > battery_config['maxSOC']:
            ax2.text(
                str(row['Date']),
                row['max_soc'] + 0.01,
                row['Date'].strftime('%m-%d'),
                ha='center', va='bottom', fontsize=6, color='red', rotation=90
            )

    ax2.hlines(battery_config['maxSOC'], str(kpi_df['Date'].min()), str(kpi_df['Date'].max()), color='red')
    sns.barplot(x='Date', y='min_soc', data=kpi_df, ax=ax3, hue=kpi_df['min_soc'] < battery_config['minSOC'])

    for i, row in kpi_df.iterrows():
        if row['min_soc'] < battery_config['minSOC']:
            ax3.text(
                str(row['Date']),
                row['min_soc'] + 0.01,
                row['Date'].strftime('%m-%d'),
                ha='center', va='bottom', fontsize=6, color='red', rotation=90
            )

    ax3.hlines(battery_config['minSOC'], str(kpi_df['Date'].min()), str(kpi_df['Date'].max()), color='red')

    sns.barplot(x='Date', y='power', data=kpi_df, ax=ax4, hue=kpi_df['power'] > battery_config['power'])

    for i, row in kpi_df.iterrows():
        if row['power'] > battery_config['power']:
            ax4.text(
                str(row['Date']),
                row['power'] + 0.01,
                row['Date'].strftime('%m-%d'),
                ha='center', va='bottom', fontsize=6, color='red', rotation=90
            )

    ax4.hlines(battery_config['power'], str(kpi_df['Date'].min()), str(kpi_df['Date'].max()), color='red')
    ax4.xaxis.set_major_locator(mdates.MonthLocator())
    ax4.xaxis.set_major_formatter(mdates.DateFormatter('%b'))

    plt.xticks(rotation=45)
    plt.show()

print('done')