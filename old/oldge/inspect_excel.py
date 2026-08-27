import pandas as pd

try:
    df = pd.read_excel('maintenance_data_v2.xlsx')
    print("Columns:", df.columns.tolist())
    print("First few rows:\n", df.head())
except Exception as e:
    print(f"Error reading file: {e}")
