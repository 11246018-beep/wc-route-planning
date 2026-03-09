import pandas as pd
df = pd.read_excel('c:/Users/v4122/OneDrive/桌面/ma3是他/ma 2/output/Weekly_Schedule_Summary_CrossCounty_Compact.xlsx')
with open('c:/Users/v4122/OneDrive/桌面/ma3是他/ma 2/output/cross_county_report.txt', 'w', encoding='utf-8') as f:
    for (driver, day), group in df.groupby(['driver', 'day']):
        counties = group['county'].unique()
        if len(counties) > 1:
            counties_str = ', '.join(counties)
            f.write(f'司機 {driver} 在第 {day} 天有跨縣市路線，涵蓋縣市: {counties_str}\n')
            for _, row in group.iterrows():
                f.write(f'  - [{row["county"]}] {row["address"]} (任務: {row["task_id"]})\n')
            f.write('-'*40 + '\n')
