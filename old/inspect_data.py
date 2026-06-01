
import pandas as pd

file_path = r'c:/Users/owner/Desktop/專題/test4.0/phase1/maintenance_data_v2.xlsx'

try:
    xls = pd.ExcelFile(file_path)
    print(f"Sheet names: {xls.sheet_names}")
    
    for sheet_name in xls.sheet_names:
        if sheet_name in ['週清1', '週清2']:
            df = pd.read_excel(xls, sheet_name=sheet_name, nrows=5)
            print(f"\n--- {sheet_name} Columns ---")
            print(df.columns.tolist())
            print(f"\n--- {sheet_name} Head ---")
            print(df.head())
except Exception as e:
    print(f"Error reading file: {e}")
