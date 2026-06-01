
import pandas as pd

file_path = r'c:/Users/owner/Desktop/專題/test4.0/phase1/maintenance_data_v2.xlsx'

try:
    print("Attempting to read as CSV with Big5 encoding...")
    df = pd.read_csv(file_path, encoding='big5')
    print("Success!")
    print("Columns:", df.columns.tolist())
    print("First row:", df.iloc[0].values)
except Exception as e:
    print(f"Failed to read as CSV: {e}")

try:
    print("\nAttempting to read as CSV with cp950 encoding...")
    df = pd.read_csv(file_path, encoding='cp950')
    print("Success!")
    print("Columns:", df.columns.tolist())
except Exception as e:
    print(f"Failed to read as cp950: {e}")
