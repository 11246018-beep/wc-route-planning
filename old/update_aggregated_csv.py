import pandas as pd

def update_csv():
    zoned_file = 'maintenance_data_zoned.csv'
    target_file = 'maintenance_data_aggregated.csv'
    
    print(f"Reading {zoned_file}...")
    try:
        df = pd.read_csv(zoned_file)
    except FileNotFoundError:
        print("Error: Zoned file not found.")
        return

    # Rename zone_id to zone
    if 'zone_id' in df.columns:
        df = df.rename(columns={'zone_id': 'zone'})
        print("Renamed 'zone_id' to 'zone'.")
    
    # Save to target file
    print(f"Saving to {target_file}...")
    df.to_csv(target_file, index=False, encoding='utf-8-sig')
    print("Done!")

if __name__ == "__main__":
    update_csv()
