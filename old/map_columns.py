
import pandas as pd
import numpy as np

file_path = r'c:/Users/owner/Desktop/專題/test4.0/phase1/maintenance_data_v2.xlsx'

try:
    df = pd.read_excel(file_path)
    print(f"Total rows: {len(df)}")
    
    # Check column content types and stats
    for i, col in enumerate(df.columns):
        non_null = df[col].count()
        sample = df[col].dropna().iloc[0] if non_null > 0 else "None"
        dtype = df[col].dtype
        
        # Check if float and range for Lat/Lon
        is_lat = False
        is_lon = False
        if np.issubdtype(dtype, np.number):
             min_val = df[col].min()
             max_val = df[col].max()
             if 21 <= min_val <= 26: is_lat = True
             if 119 <= min_val <= 122: is_lon = True
             
        print(f"Index {i}: Used={non_null}, Type={dtype}, Range=({df[col].min() if np.issubdtype(dtype, np.number) else 'N/A'}, {df[col].max() if np.issubdtype(dtype, np.number) else 'N/A'}), Sample={sample}, Lat?={is_lat}, Lon?={is_lon}")

except Exception as e:
    print(f"Error: {e}")
