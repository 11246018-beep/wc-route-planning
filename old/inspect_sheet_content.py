
import pandas as pd

file_path = r'c:/Users/owner/Desktop/專題/test4.0/phase1/maintenance_data_v2.xlsx'

try:
    xls = pd.ExcelFile(file_path, engine='openpyxl')
    sheet_name = xls.sheet_names[0]
    print(f"Reading sheet: {sheet_name}")
    df = pd.read_excel(xls, sheet_name=sheet_name)
    print("Columns:", df.columns.tolist())
    print("First 5 rows:")
    print(df.head())
except Exception as e:
    print(f"Error: {e}")
