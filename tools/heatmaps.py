import os
import pytz
import json
import numpy as np
import pandas as pd
import seaborn as sns
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt


# mention the folder path containing the result json files here
folder_path = r"C:\Users\Yash\Documents\PyCharm-Projects\battery_revenue_index-main\results\Cross-Market_results_2025-04-17_1_1"


# this section creates a aggregated df from all the json files
soc_df = pd.DataFrame(columns=["timestamp", "soc"])
for filename in os.listdir(folder_path):
    if filename.endswith('.json'):
        file_path = os.path.join(folder_path, filename)
        with open(file_path, 'r') as file:
            data = json.load(file)
        data = data['market_results']['SOC']
        df = pd.DataFrame(list(data.items()), columns=['timestamp', 'soc'])
        soc_df = pd.concat([soc_df, df], ignore_index=True)
#soc_df.to_csv('soc_1h1c.csv', index=False)


# this section fills the missing time entries with 0.5 SoC
# soc_df = pd.read_csv('soc_1h1c.csv')
soc_df['timestamp'] = pd.to_datetime(soc_df['timestamp'])
soc_df['date'] = soc_df['timestamp'].apply(lambda x: x.strftime('%Y-%m-%d'))
# manually mention the start and end time range that you expect in the data here
full_date_range = pd.date_range('2024-01-01', '2025-03-31').date
full_date_range_str = [date.strftime('%Y-%m-%d') for date in full_date_range]
our_dates = set(soc_df['date'])
missing_days = set(full_date_range_str) - set(soc_df['date'])
missing_days = sorted(list(missing_days))
missing_intervals = []
german_time_zone = pytz.timezone('Europe/Berlin')
for day in missing_days:
    time_range = pd.date_range(f'{day} 00:00', f'{day} 23:45', freq='15T')
    localized_time_range = [german_time_zone.localize(t) for t in time_range]
    missing_intervals.extend(localized_time_range)
df_miss = pd.DataFrame({
    'timestamp': missing_intervals,
    'soc': [0.5] * len(missing_intervals)
})
df_new = pd.concat([soc_df, df_miss], ignore_index=True)
df_new = df_new[['timestamp', 'soc']]
sorted_df = df_new.sort_values(by=['timestamp'], ascending=True)
df_filtered = sorted_df.drop_duplicates(subset='timestamp', keep='first')
# df_filtered.to_csv('soc_full_1h1c.csv', index=False)


# the remaining code section creates the heatmap
# df = pd.read_csv('soc_full_1h1c.csv')
df = df_filtered.copy()
df['soc'] = np.where(df['soc'] < 0, 0, df['soc'])
df['timestamp'] = pd.to_datetime(df['timestamp'], utc=True)
df['date'] = df['timestamp'].apply(lambda x: x.date())
df['hour'] = df['timestamp'].apply(lambda x: x.hour)

heatmap_data = df.groupby(['date', 'hour'])['soc'].mean().unstack()
heatmap_data_percent = heatmap_data * 100

fig = plt.figure(figsize=(16, 8))
gs = fig.add_gridspec(2, 2, width_ratios=(20, 1), height_ratios=(1, 20), wspace=0.05, hspace=0.05)

ax_top = fig.add_subplot(gs[0, 0])
daily_energy = df.groupby('date')['soc'].sum()
ax_top.plot(daily_energy.index, daily_energy.values, '.', alpha=0.3)
ax_top.fill_between(daily_energy.index, daily_energy.values, alpha=0.3, color='gray')
ax_top.set_xticks([])
ax_top.set_ylabel("Daily Traded\nEnergy [MWh]")
ax_top.set_xlim(heatmap_data.index.min(), heatmap_data.index.max())

ax_right = fig.add_subplot(gs[1, 1])
hourly_mean = heatmap_data_percent.mean(axis=0)
ax_right.plot(hourly_mean.values, hourly_mean.index, color='black', alpha=0.3)
ax_right.set_yticks([])
ax_right.set_xlabel("Mean SoC\nProfile [%]")
ax_right.set_xlim(0, 100)
ax_right.set_ylim(heatmap_data_percent.columns.min(), heatmap_data_percent.columns.max())

ax_main = fig.add_subplot(gs[1, 0])
cbar_ax = fig.add_axes([0.92, 0.15, 0.015, 0.7])
sns.heatmap(heatmap_data_percent.T, cmap="RdBu_r", cbar=True, ax=ax_main,
            cbar_ax=cbar_ax, cbar_kws={'label': 'SoC [%]'})
ax_main.set_xlabel("Date")
ax_main.set_ylabel("Hour of the Day")

date_ticks = pd.date_range(start=df['timestamp'].min(), end=df['timestamp'].max(), freq='MS').date
ax_main.set_xticks([heatmap_data.index.get_loc(d) for d in date_ticks if d in heatmap_data.index])
ax_main.set_xticklabels([pd.to_datetime(d).strftime('%b \'%y') for d in date_ticks if d in heatmap_data.index], rotation=45)

plt.tight_layout(rect=[0, 0, 0.9, 0.98])

# change the file name according to the battery configuration
plt.savefig("heatmap_1h1c.png", dpi=600)

