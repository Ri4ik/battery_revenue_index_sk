import os
import json
import pandas as pd
import matplotlib.pyplot as plt

# Define the folder path containing your JSON files
foldername = 'results_2025-03-07_15-35'
folder_path = os.path.join('../results',
                           foldername)  # Replace with your actual folder path

# Create an empty DataFrame to store the results
results_df = pd.DataFrame()

# Iterate through each file in the folder
for filename in os.listdir(folder_path):
    if filename.endswith('_results_aFRR_ID1_RI.json'):
        # Construct the full file path
        file_path = os.path.join(folder_path, filename)

        # Extract the day from the filename (assuming format is YYYY-MM-DD)
        day = filename.split('_')[0]  # Get '2024-09-01' part

        # Open and read the JSON file
        with open(file_path, 'r') as file:
            data = json.load(file)

            # Extract daily_revenue dict if it exists
            daily_revenue = data.get("results", {}).get("daily_revenue", {})

            # Convert daily_revenue dictionary into a DataFrame and set index as day
            temp_df = pd.DataFrame(daily_revenue, index=[day])

            # Append to results_df
            results_df = pd.concat([results_df, temp_df])

# Reset index if needed or keep it as is based on your requirements.
# results_df.reset_index(inplace=True)

# Convert index to datetime format
results_df.index = pd.to_datetime(results_df.index)

# Plotting Daily Stacked Bar Plot with adjusted x-ticks for every 14th day
plt.figure(figsize=(12, 6))
daily_result = results_df[['FCR', 'aFRR_Capacity', 'aFRR_Energy', 'RI']] * 365
ax1 = daily_result.plot(kind='bar', stacked=True, ax=plt.gca())
plt.grid(axis='y', linestyle='--', alpha=0.6)
plt.title('Daily aFRR Capacity and Energy')
plt.xlabel('Date')
plt.ylabel('Annualized Revenue in €/MW/a')

# Set x-ticks to show every 14th day
ax1.set_xticks(range(0, len(results_df), 14))
ax1.set_xticklabels(results_df.index[::14].date, rotation=45)  # Show date labels

plt.legend(title='Legend')
plt.tight_layout()
# Save daily plot as high-resolution PNG in same directory as script
daily_plot_path = os.path.join(os.path.dirname(__file__), f'daily_{foldername}.png')
plt.savefig(daily_plot_path, dpi=300)  # Adjust dpi for higher resolution if needed.
plt.close()  # Close figure after saving

# Resampling for Monthly Aggregation (sum values for each month)
monthly_results_df = results_df.resample('ME').sum() * 12

# Plotting Monthly Stacked Bar Plot with month names on x-axis only
plt.figure(figsize=(12, 6))
ax2 = monthly_results_df[['FCR', 'aFRR_Capacity', 'aFRR_Energy', 'RI']].plot(kind='bar', stacked=True, ax=plt.gca())
plt.grid(axis='y', linestyle='--', alpha=0.6)
plt.title('Monthly aFRR Capacity and Energy')
plt.xlabel('Month')
plt.ylabel('Annualized Revenue in €/MW/a')

# Set x-tick labels to show only month names.
ax2.set_xticklabels(monthly_results_df.index.strftime('%B'), rotation=45)

plt.legend(title='Legend')
plt.tight_layout()
# Save monthly plot as high-resolution PNG
monthly_plot_path = os.path.join(os.path.dirname(__file__), f'monthly_{foldername}.png')
plt.savefig(monthly_plot_path, dpi=300)  # Adjust dpi for higher resolution if needed.
plt.close()  # Close figure after saving