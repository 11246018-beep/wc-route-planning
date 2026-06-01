
import pandas as pd

file_path = r'c:/Users/owner/Desktop/專題/test4.0/phase1/maintenance_data_v2.xlsx'

try:
    df = pd.read_excel(file_path)
    print("Columns:", df.columns.tolist())
    
    print("\nFirst row values:")
    print(df.iloc[0].values)
    
    print("\nChecking if we can decode headers by guessing Big5:")
    new_cols = []
    for col in df.columns:
        try:
            # Sometimes headers are read as string but bytes were Big5?
            # It's rare for read_excel.
            # Maybe the file was created by saving a Big5 CSV as XLSX without wizard?
            new_cols.append(col)
        except:
            new_cols.append(col)
    
except Exception as e:
    print(f"Error: {e}")
