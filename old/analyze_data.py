
import pandas as pd

# Load the Excel file
file_path = 'maintenance_data_v2.xlsx'

try:
    df = pd.read_excel(file_path, sheet_name='Wcrp53311b水肥車每日維護週期表')
    
    # Filter Week 1 and Week 2
    # Assuming Week 1 is marked with 1.0 (or non-null)
    # Week 2 is marked with 1.0
    
    w1_mask = df['週清1'].notnull()
    w2_mask = df['週清2'].notnull()
    
    df_w1 = df[w1_mask]
    df_w2 = df[w2_mask]
    
    # Calculate Clean Time
    # Week 1: Clean once
    total_clean_time_w1 = df_w1['維護時間'].sum()
    
    # Week 2: Clean TWICE
    total_clean_time_w2 = df_w2['維護時間'].sum() * 2
    
    total_clean_time = total_clean_time_w1 + total_clean_time_w2
    
    print(f"Total Week 1 Clean Time: {total_clean_time_w1} min")
    print(f"Total Week 2 Clean Time: {total_clean_time_w2} min")
    print(f"Total Clean Time: {total_clean_time} min")
    
    # Limits
    # 14 drivers
    # 3240 min/driver/week
    total_capacity = 14 * 3240
    print(f"Total Capacity (14 drivers * 3240): {total_capacity} min")
    
    if total_clean_time > total_capacity:
        print("WARNING: Total Clean Time EXCEEDS Capacity!")
    else:
        remaining = total_capacity - total_clean_time
        print(f"Remaining for Travel: {remaining} min")
        print(f"Avg Travel Time per Driver per Week: {remaining / 14} min")

except Exception as e:
    print(f"Error: {e}")
