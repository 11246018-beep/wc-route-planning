
import json
import pandas as pd

with open('schedule_output_strict.json', 'r') as f:
    data = json.load(f)

# Get first task ID
tid = data[0]['Details']['tasks'][0]['id']
print(f"Task ID from JSON: {tid} (Type: {type(tid)})")

df = pd.read_excel('maintenance_data_v2.xlsx', sheet_name='Wcrp53311b水肥車每日維護週期表')
print(f"Columns: {df.columns.tolist()}")

if '排程總序號' in df.columns:
    print(f"First 5 排程總序號: {df['排程總序號'].head().tolist()}")
    if tid in df['排程總序號'].values:
        print("ID found in column.")
    else:
        print("ID NOT found in column.")
else:
    print("Column missing.")
