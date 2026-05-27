import pandas as pd

try:
    df = pd.read_csv('maintenance_data_zoned.csv')
    print("Unique values in '週清1':")
    print(df['週清1'].unique())
    print("\nUnique values in '週清2':")
    print(df['週清2'].unique())
    
    print("\nRows where '週清2' is not null:")
    print(df[df['週清2'].notna()].head())

except Exception as e:
    print(f"Error: {e}")
