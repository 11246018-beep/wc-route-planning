
import pandas as pd
import os

file_path = r'c:/Users/owner/Desktop/專題/test4.0/phase1/maintenance_data_v2.xlsx'

if os.path.exists(file_path):
    print(f"File exists. Size: {os.path.getsize(file_path)}")
else:
    print("File does not exist.")

try:
    df = pd.read_excel(file_path, sheet_name=None, engine='openpyxl')
    print("Sheet names found:", list(df.keys()))
except Exception as e:
    print(f"Error: {e}")
