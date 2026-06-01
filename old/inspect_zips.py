
import pandas as pd

# Load the Excel file
file_path = 'maintenance_data_v2.xlsx'

try:
    df = pd.read_excel(file_path, sheet_name='Wcrp53311b水肥車每日維護週期表')
    
    # Check column names
    print("Columns:", df.columns.tolist())
    
    # Identify zipcode column
    zip_col = [c for c in df.columns if '郵遞區號3碼' in c][0]
    print(f"Zipcode Column: '{zip_col}'")
    
    unique_zips = df[zip_col].unique()
    print("Unique Zipcodes:", unique_zips)
    
    # Also check '服務地點' (Address) for first 3 chars to guess county
    # Sample address
    print("Sample Addresses:", df['服務地點 '].head(10))

except Exception as e:
    print(f"Error reading excel file: {e}")
