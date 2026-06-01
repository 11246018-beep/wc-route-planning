
import pandas as pd

file_path = r'c:/Users/owner/Desktop/專題/test4.0/phase1/maintenance_data_v2.xlsx'

try:
    xls = pd.ExcelFile(file_path, engine='openpyxl')
    sheet_name = xls.sheet_names[0]
    df = pd.read_excel(xls, sheet_name=sheet_name)
    
    # Identify columns
    # Based on phase1.py mapping: '週清1', '週清2'
    if '週清1' in df.columns and '週清2' in df.columns:
        w1_notnull = df['週清1'].notnull()
        w2_notnull = df['週清2'].notnull()
        
        w1_count = w1_notnull.sum()
        w2_count = w2_notnull.sum()
        overlap = (w1_notnull & w2_notnull).sum()
        
        print(f"Total rows: {len(df)}")
        print(f"Week 1 (Non-null): {w1_count}")
        print(f"Week 2 (Non-null): {w2_count}")
        print(f"Overlap: {overlap}")
        
        if overlap > 0:
            print("WARNING: Some rows are marked for BOTH Week 1 and Week 2.")
            print(df[w1_notnull & w2_notnull][['週清1', '週清2']].head())
    else:
        print("Columns '週清1' or '週清2' not found.")
        print("Columns found:", df.columns.tolist())

except Exception as e:
    print(f"Error: {e}")
