import pandas as pd

# Load data
df_res = pd.read_excel('Weekly_Schedule.xlsx')
try:
    df_una = pd.read_csv('unassigned_tasks.csv')
except:
    df_una = pd.DataFrame()

# 1. Analyze Unassigned distribution
print("--- Unassigned Tasks by Zone ---")
if not df_una.empty:
    print(df_una['Zone'].value_counts())
else:
    print("No unassigned_tasks.csv found or empty.")

# 2. Analyze Staff Time Usage
print("\n--- Staff Average Daily Usage (Current 540 Limit) ---")
# Get last '累計工時(分)' for each staff, each day
usage = df_res.groupby(['員工代號', '日程(Day)'])['累計工時(分)'].max().reset_index()

# Note: The '累計工時(分)' includes work + travel between tasks. 
# We should also consider the return trip home which is NOT in the row but checked in code.
# The code check: total_time_at_finish + return_home_time <= DAILY_LIMIT_MINS

staff_summary = usage.groupby('員工代號')['累計工時(分)'].agg(['mean', 'max', 'count']).round(1)
print(staff_summary)

# Identify regions with spare capacity
print("\n--- Spare Capacity (minutes per day) ---")
# 540 - mean
staff_summary['Spare'] = (540 - staff_summary['mean']).round(1)
print(staff_summary[['mean', 'Spare']])
