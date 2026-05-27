import pandas as pd
import os

def process_data(input_file, output_file):
    print(f"Reading {input_file}...")
    try:
        df = pd.read_csv(input_file)
    except Exception as e:
        print(f"Error reading CSV: {e}")
        # Try reading with different encoding if default utf-8 fails, though usually pandas handles it or defaults to system
        # If the file has chinese characters, it might be Big5 or cp950 (common on Windows)
        try:
            df = pd.read_csv(input_file, encoding='cp950')
        except:
            df = pd.read_csv(input_file, encoding='big5')

    # Clean column names (strip whitespace)
    df.columns = df.columns.str.strip()
    
    # Ensure numeric columns are numeric
    df['維護時間'] = pd.to_numeric(df['維護時間'], errors='coerce').fillna(0)
    # The user asked to sum "Travel Time", but the column is "行車距離". 
    # Assuming "Travel Time" meant "Travel Distance" as per plan.
    if '行車距離' in df.columns:
        df['行車距離'] = pd.to_numeric(df['行車距離'], errors='coerce').fillna(0)
    
    print("Grouping by location and aggregating...")
    
    # Define aggregation rules
    # Sum '維護時間' and '行車距離'
    # Keep first for others
    # Count occurrences
    
    group_col = '服務地點'
    
    if group_col not in df.columns:
        print(f"Error: Column {group_col} not found in input file.")
        return

    # Create aggregation dictionary
    agg_funcs = {}
    for col in df.columns:
        if col == group_col:
            continue
        elif col == '維護時間':
            agg_funcs[col] = 'sum'
        elif col == '行車距離':
            agg_funcs[col] = 'sum'
        else:
            agg_funcs[col] = 'first'
            
    # Perform aggregation
    df_aggregated = df.groupby(group_col).agg(agg_funcs).reset_index()
    
    # Calculate count
    count_series = df.groupby(group_col).size().reset_index(name='count')
    
    # Merge count
    df_final = pd.merge(df_aggregated, count_series, on=group_col)
    
    # User requested to divide '行車距離' by 'count'
    if '行車距離' in df_final.columns and 'count' in df_final.columns:
        df_final['行車距離'] = df_final['行車距離'] / df_final['count']
    
    # Reorder columns to put 'count' after '服務地點' or at the end? 
    # Usually appending is fine, but let's try to verify if user has preference. 
    # "新增一個欄位count" - imply adding it.
    
    print(f"Saving to {output_file}...")
    df_final.to_csv(output_file, index=False, encoding='utf-8-sig') # use utf-8-sig for excel compatibility
    print("Done!")

if __name__ == "__main__":
    input_path = "maintenance_data_v2.csv"
    output_path = "maintenance_data_aggregated.csv"
    
    if os.path.exists(input_path):
        process_data(input_path, output_path)
    else:
        print(f"File {input_path} not found.")
