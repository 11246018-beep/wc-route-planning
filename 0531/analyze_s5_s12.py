import pandas as pd

# Load data
df_res = pd.read_excel('Weekly_Schedule.xlsx')

# Analyze Staff Usage (S05-S14)
print("\n--- Staff S05-S14 Average Daily Usage ---")
target_staff = ['S05', 'S06', 'S07', 'S08', 'S09', 'S10', 'S11', 'S12']
usage = df_res[df_res['員工代號'].isin(target_staff)].groupby(['員工代號', '日程(Day)'])
stats = usage['累計工時(分)'].max().groupby('員工代號').agg(['mean', 'max', 'count']).round(1)
stats['Spare'] = (540 - stats['mean']).round(1)
print(stats)
