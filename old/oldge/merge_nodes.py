import pandas as pd
import shutil
import os

# Define file paths
current_dir = os.path.dirname(os.path.abspath(__file__))
# Input is now the Excel file
input_excel = os.path.join(current_dir, 'maintenance_data_v2.xlsx')
# Output is the CSV file
output_csv = os.path.join(current_dir, 'processed_nodes_phase1.csv')

# Column mapping from Excel headers to internal names (consistent with phase1.py)
COL_MAPPING = {
    '客戶名稱': 'Client_ID',
    '維護時間': 'S_Time',
    '服務地點': 'Address',
    '週清1': 'Freq_1x',
    '週清2': 'Freq_2x',
    '緯度': 'Lat',
    '經度': 'Lon',
    '出租單號': 'ordercode',
    '排程總序號': 'serialno',
    '樓層': 'floor',
    '倉庫別': 'depstock'
}

def merge_duplicates():
    # Check if input file exists
    if not os.path.exists(input_excel):
        print(f"Error: File not found at {input_excel}")
        return

    print("Reading Excel file...")
    try:
        # Read Excel using openpyxl
        df = pd.read_excel(input_excel, engine='openpyxl')
        print("File loaded successfully.")
    except Exception as e:
        print(f"Error reading Excel: {e}")
        return

    # Clean column names (strip whitespace)
    df.columns = df.columns.str.strip()

    # Rename columns based on mapping
    # We only rename columns that exist in the mapping
    df = df.rename(columns=COL_MAPPING)

    # Required columns for merging
    required_columns = ['Address', 'floor']
    missing_cols = [col for col in required_columns if col not in df.columns]
    if missing_cols:
        print(f"Error: Missing required columns: {missing_cols}")
        print(f"Current columns: {df.columns.tolist()}")
        return

    # Data Cleaning for Merge Keys
    # Ensure they are strings and strip whitespace to ensure strict exact match works expectedly
    # (e.g. "1F" vs "1F ")
    for col in required_columns:
        df[col] = df[col].astype(str).str.strip()

    # Calculate '間數' (Count)
    # Group by Address and floor, then count the occurrences
    print("Calculating room counts...")
    df['間數'] = df.groupby(['Address', 'floor'])['Address'].transform('count')

    # Drop duplicates, keeping the first occurrence
    # This retains the first row's data for other columns (like Lat/Lon, Client_ID etc.)
    df_merged = df.drop_duplicates(subset=['Address', 'floor'], keep='first')

    # Save validation info
    original_count = len(df)
    merged_count = len(df_merged)
    print(f"Original row count: {original_count}")
    print(f"Merged row count: {merged_count}")
    print(f"Rows removed: {original_count - merged_count}")

    # Save to CSV
    try:
        df_merged.to_csv(output_csv, index=False, encoding='utf-8-sig')
        print(f"Successfully saved merged data to {output_csv}")
    except Exception as e:
        print(f"Error saving CSV: {e}")

if __name__ == "__main__":
    merge_duplicates()
