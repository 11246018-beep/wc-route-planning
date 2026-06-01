
import pandas as pd

file_path = r'c:/Users/owner/Desktop/專題/test4.0/phase1/maintenance_data_v2.xlsx'

try:
    df = pd.read_excel(file_path)
    print("Original Columns:", df.columns.tolist())
    
    fixed_cols = []
    for col in df.columns:
        try:
            # Try encoding as latin-1 (common culprit for moji-bake) and decoding as big5
            fixed = col.encode('latin-1').decode('big5')
            fixed_cols.append(fixed)
        except Exception as e:
            # If failed, try cp1252
            try:
                fixed = col.encode('cp1252').decode('big5')
                fixed_cols.append(fixed)
            except:
                fixed_cols.append(col)
                
    print("\nFixed Columns:", fixed_cols)
    
    # Try to fix content as well
    print("\nFirst row original:", df.iloc[0].values)
    
    first_row_fixed = []
    for val in df.iloc[0]:
        if isinstance(val, str):
            try:
                fixed = val.encode('latin-1').decode('big5')
                first_row_fixed.append(fixed)
            except:
                 first_row_fixed.append(val)
        else:
             first_row_fixed.append(val)
             
    print("First row fixed:", first_row_fixed)

except Exception as e:
    print(f"Error: {e}")
