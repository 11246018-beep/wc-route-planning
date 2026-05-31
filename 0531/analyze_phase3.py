import pandas as pd

# Load data
df_res = pd.read_excel('Weekly_Schedule.xlsx')
try:
    df_una = pd.read_csv('unassigned_tasks.csv')
except:
    df_una = pd.DataFrame()

# 1. Analyze Unassigned
print("--- Unassigned Tasks ---")
if not df_una.empty:
    print(df_una[['ID_List', 'Address', 'Work_Mins', 'Zone']])
    print("\nBy Zone:")
    print(df_una['Zone'].value_counts())
else:
    print("Zero unassigned tasks!")

# 2. Analyze Wugu Staff (S13, S14) usage
print("\n--- Wugu Staff Usage (Target for these tasks) ---")
wugu_usage = df_res[df_res['員工代號'].isin(['S13', 'S14'])].groupby(['員工代號', '日程(Day)'])
print(wugu_usage['累計工時(分)'].max())

# 3. Analyze PZ-A Staff Usage
print("\n--- PZ-A Staff Usage (S01-S04) ---")
pza_usage = df_res[df_res['員工代號'].isin(['S01', 'S02', 'S03', 'S04'])].groupby(['員工代號', '日程(Day)'])
print(pza_usage['累計工時(分)'].max())
