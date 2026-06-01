
import pandas as pd
df = pd.read_excel('maintenance_data_v2.xlsx', sheet_name='Wcrp53311b水肥車每日維護週期表')
print(df.columns.tolist())
