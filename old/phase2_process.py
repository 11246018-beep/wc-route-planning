
import pandas as pd
import numpy as np
import reverse_geocoder as rg
import requests
import json
import time
import math
from datetime import datetime, timedelta

# ==========================================
# Phase 2: Advanced Clustering & Routing
# ==========================================

INPUT_FILE = r'c:/Users/owner/Desktop/專題/test4.0/phase1/maintenance_data_v2.xlsx'

# OSRM Service URL
OSRM_URL = "http://router.project-osrm.org/route/v1/driving/"

# Constraints
MAX_DAILY_MIN = 540
MAX_WEEKLY_MIN = 3240
Drivers_PZ = 12
Drivers_WG = 2
Total_Drivers = 14

# Depots
DEPOTS = {
    'Pingzhen': {'lat': 24.90703, 'lon': 121.226872, 'drivers': 12, 'id': 'PZ'},
    'Wugu': {'lat': 25.07154, 'lon': 121.44169, 'drivers': 2, 'id': 'WG'}
}

def get_osrm_route(coords):
    """
    Get OSRM route for a list of coordinates [(lon, lat), ...].
    Returns dict with distance(m), duration(s), geometry(geojson).
    """
    if len(coords) < 2:
        return {'distance': 0, 'duration': 0, 'geometry': None}
    
    # Check for too many coordinates - OSRM public server might reject > 100
    # But usually fine for standard daily routes (20-30 stops)
    
    coord_str = ";".join([f"{lon},{lat}" for lon, lat in coords])
    url = f"{OSRM_URL}{coord_str}?overview=full&steps=true&geometries=geojson"
    try:
        resp = requests.get(url, timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            if data['code'] == 'Ok':
                route = data['routes'][0]
                return {
                    'distance': route['distance'],
                    'duration': route['duration'],
                    'geometry': route['geometry']
                }
    except Exception as e:
        print(f"OSRM Error: {e}")
        time.sleep(1)
    
    return None

def add_county_info(df):
    """Add County column using reverse_geocoder"""
    coords = list(zip(df['Lat'], df['Lon']))
    results = rg.search(coords) # Returns list of dicts
    
    counties = []
    for res in results:
        # Taiwan admin 2 is usually County/City name in English
        # e.g. 'Taoyuan City', 'New Taipei'
        county = res.get('admin2', '')
        if not county:
            county = res.get('admin1', 'Unknown')
        counties.append(county)
    
    df['County'] = counties
    return df

def load_data():
    """Load and clean data based on index mapping"""
    print("Loading Excel...")
    try:
        df = pd.read_excel(INPUT_FILE)
    except Exception as e:
        print(f"Failed to load Excel: {e}")
        return None

    # Map columns by index
    # 0: ClientID, 1: Addr, 2: S_Time, 7: Floor, 8: Serial, 9: W1, 10: W2, 11: Lat, 12: Lon
    # Note: Indices might shift if user edits file. Assuming fixed based on verified checks.
    
    # Rename columns
    new_cols = df.columns.tolist() # Copy
    # We only care about specific indices
    col_map = {
        0: 'ClientID',
        1: 'Address_Raw',
        2: 'Service_Time',
        7: 'Floor',
        8: 'Serial_No',
        9: 'Week1',
        10: 'Week2',
        11: 'Lat',
        12: 'Lon',
        13: 'Unknown_ID'
    }
    
    rename_dict = {}
    for idx, name in col_map.items():
        if idx < len(df.columns):
            rename_dict[df.columns[idx]] = name
            
    df.rename(columns=rename_dict, inplace=True)
    
    # Filter valid coordinates
    df = df.dropna(subset=['Lat', 'Lon'])
    df = df[(df['Lat'] > 20) & (df['Lat'] < 26) & (df['Lon'] > 118) & (df['Lon'] < 123)]
    
    # Fill Service Time
    df['Service_Time'] = pd.to_numeric(df['Service_Time'], errors='coerce').fillna(10)
    
    # Process lat/lon
    df['Lat'] = pd.to_numeric(df['Lat'])
    df['Lon'] = pd.to_numeric(df['Lon'])

    # Add County
    print("Adding County info...")
    df = add_county_info(df)
    
    print("Data loaded. Head:")
    print(df[['ClientID', 'Lat', 'Lon', 'Service_Time', 'County']].head())
    
    return df

if __name__ == "__main__":
    df = load_data()
    if df is not None:
        # Save intermediate
        df.to_csv('phase2_cleaned.csv', index=False, encoding='utf-8-sig')
        print("Standardized data saved to phase2_cleaned.csv")
