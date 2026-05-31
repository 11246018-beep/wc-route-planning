import pandas as pd
import os

OUTPUT_FILE = 'Weekly_Schedule.xlsx'

def verify():
    if not os.path.exists(OUTPUT_FILE):
        print(f"Error: {OUTPUT_FILE} not found.")
        return

    df = pd.read_excel(OUTPUT_FILE)
    print(f"Loaded Schedule: {len(df)} rows")
    
    # 1. 540 Min Limit Check
    over_limit = df[df['累計工時(分)'] > 540]
    if not over_limit.empty:
        print(f"FAIL: {len(over_limit)} rows exceed 540 mins!")
        print(over_limit[['日程(Day)', '員工代號', '累計工時(分)', '任務屬性']].head())
    else:
        print("PASS: All rows within 540 mins.")
        
    # 2. Cross County / Via Base Check
    via_base = df[df['任務屬性'].str.contains('Via Base', na=False)]
    print(f"Info: Found {len(via_base)} tasks routed 'Via Base'.")
    if not via_base.empty:
        print(via_base[['日程(Day)', '員工代號', '任務屬性', '預估車程(分)']].head())
        
    # 3. Check Travel Times > 30 mins (likely heavy traffic or long distance)
    long_travel = df[df['預估車程(分)'] > 30]
    print(f"Info: Found {len(long_travel)} tasks with > 30 mins travel.")
    
    # 4. Check Unassigned Coverage
    # Not easy from just this file, but meaningful enough.
    
    print("Verification Done.")

if __name__ == "__main__":
    verify()
