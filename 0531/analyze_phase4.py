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
    print(df_una['Zone'].value_counts())
else:
    print("Zero unassigned tasks!")

# 2. Analyze Staff Usage
print("\n--- Staff Daily Usage ---")
usage = df_res.groupby(['員工代號', '日程(Day)'])['累計工時(分)'].max().groupby('員工代號').agg(['mean', 'max', 'count']).round(1)
print(usage)

# 3. Check specific job types for S01-S04 (Did they take Guanyin?)
print("\n--- S01-S04 Job Types in Guanyin (PZ-A) ---")
guanyin_tasks = df_res[
    (df_res['員工代號'].isin(['S01', 'S02', 'S03', 'S04'])) & 
    (df_res['工作區域'] == 'PZ-A')
]
print(f"S01-S04 PZ-A Tasks Count: {len(guanyin_tasks)}")
print(guanyin_tasks[['員工代號', '地址']].head())

# 4. Check S13-S14 Usage (WG)
print("\n--- S13-S14 Usage ---")
print(usage.loc[['S13', 'S14']])
