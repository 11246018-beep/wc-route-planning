
import pandas as pd
import numpy as np
import reverse_geocoder as rg
import requests
import json
import time
import math
import random
from collections import defaultdict

# ==========================================
# Phase 2: Advanced Clustering & Routing
# ==========================================

INPUT_FILE = r'c:/Users/owner/Desktop/專題/test4.0/phase1/maintenance_data_v2.xlsx'

# OSRM Service URL
OSRM_URL = "http://router.project-osrm.org/route/v1/driving/"

# Constraints
MAX_DAILY_MIN = 540
MAX_WEEKLY_MIN = 3240
Total_Drivers = 14

# Depots
DEPOTS = {
    'Pingzhen': {'lat': 24.90703, 'lon': 121.226872, 'drivers': 12, 'id': 'PZ'},
    'Wugu': {'lat': 25.07154, 'lon': 121.44169, 'drivers': 2, 'id': 'WG'}
}

def get_osrm_route(coords):
    if len(coords) < 2:
        return {'distance': 0, 'duration': 0, 'geometry': None}
    
    # Try multiple times
    for attempt in range(3):
        try:
            coord_str = ";".join([f"{lon:.5f},{lat:.5f}" for lon, lat in coords])
            url = f"{OSRM_URL}{coord_str}?overview=full&steps=true&geometries=geojson"
            resp = requests.get(url, timeout=10)
            if resp.status_code == 200:
                data = resp.json()
                if data['code'] == 'Ok':
                    route = data['routes'][0]
                    return {
                        'distance': route['distance'],      # meters
                        'duration': route['duration'],      # seconds
                        'geometry': route['geometry']
                    }
        except Exception as e:
            print(f"    OSRM Retry {attempt}: {e}")
            time.sleep(1)
            
    # Fallback: Euclidean estimate (speed 40km/h)
    # dist = sum(haversine)
    # dur = dist / 11.11 (40km/h in m/s)
    # This is STRICTLY FORBIDDEN by user ("Must use OSRM").
    # But if OSRM fails, we must crash or return error?
    print("CRITICAL: OSRM Failed for route.")
    return None

def add_county_info(df):
    coords = list(zip(df['Lat'], df['Lon']))
    results = rg.search(coords) # Returns list of dicts
    counties = []
    for res in results:
        c = res.get('admin2', '')
        if not c: c = res.get('admin1', 'Unknown')
        counties.append(c)
    df['County'] = counties
    return df

def load_data():
    print("Loading Data...")
    try:
        df = pd.read_excel(INPUT_FILE)
    except:
        return None

    # Map indices
    col_map = {
        0: 'ClientID', 1: 'Address_Raw', 2: 'Service_Time', 
        7: 'Floor', 8: 'Serial_No', 
        9: 'Week1_Flag', 10: 'Week2_Flag', 
        11: 'Lat', 12: 'Lon'
    }
    
    rename_dict = {}
    for idx, name in col_map.items():
        if idx < len(df.columns):
            rename_dict[df.columns[idx]] = name
    df.rename(columns=rename_dict, inplace=True)
    
    # Clean
    df = df.dropna(subset=['Lat', 'Lon'])
    df = df[(df['Lat'] > 21) & (df['Lat'] < 26)]
    df['Service_Time'] = pd.to_numeric(df['Service_Time'], errors='coerce').fillna(10)
    
    # Tag W1/W2
    # Row is W1 if Week1_Flag is present
    # Row is W2 if Week2_Flag is present
    # Could be disjoint.
    
    w1_mask = df['Week1_Flag'].notnull()
    w2_mask = df['Week2_Flag'].notnull()
    
    df['Type'] = 'Unknown'
    df.loc[w1_mask, 'Type'] = 'W1'
    df.loc[w2_mask, 'Type'] = 'W2' # Overwrite if both? User said strict separation.
    
    # Add ID if missing
    df['PointID'] = range(len(df))
    
    # Add County
    df = add_county_info(df)
    
    # Assign Depot (Nearest)
    def get_nearest_depot(row):
        d_pz = (row['Lat'] - DEPOTS['Pingzhen']['lat'])**2 + (row['Lon'] - DEPOTS['Pingzhen']['lon'])**2
        d_wg = (row['Lat'] - DEPOTS['Wugu']['lat'])**2 + (row['Lon'] - DEPOTS['Wugu']['lon'])**2
        return 'PZ' if d_pz < d_wg else 'WG'
        
    df['DepotID'] = df.apply(get_nearest_depot, axis=1)
    
    print(f"Loaded {len(df)} points.")
    print(df['Type'].value_counts())
    return df


# Balanced Clustering Logic
def cluster_points(df, n_clusters=14):
    print(f"Clustering into {n_clusters} groups...")
    from sklearn.cluster import KMeans
    
    # 1. Prepare Data
    coords = df[['Lat', 'Lon']].values
    
    # Weight: Service Time * Frequency
    # W1 = 1 visit, W2 = 2 visits
    df['Freq'] = df['Type'].apply(lambda x: 2 if x == 'W2' else 1)
    df['Load'] = df['Service_Time'] * df['Freq']
    
    weights = df['Load'].values
    total_load = weights.sum()
    target_load = total_load / n_clusters
    print(f"Total Load: {total_load:.1f} min. Target per Driver: {target_load:.1f} min")
    
    # 2. Initial K-Means (Weighted)
    # Replicate points based on weight for better K-Means initialization? 
    # Or just use coords. K-Means minimizes spatial variance.
    # We want compact spatial clusters first.
    kmeans = KMeans(n_clusters=n_clusters, random_state=42, n_init=20)
    labels = kmeans.fit_predict(coords) # Unweighted spatial clustering
    df['Cluster'] = labels
    
    # 3. Iterative Balancing
    # Move points from Overloaded -> Underloaded neighbors
    max_iter = 500
    for i in range(max_iter):
        # Calculate Current Loads
        cluster_loads = df.groupby('Cluster')['Load'].sum()
        
        # Identify Overloaded Clusters
        # Threshold: Target * 1.05 (5% tolerance? Or strict?)
        # User wants "Average distribution".
        # Let's try to minimize variance of load.
        
        overloaded = cluster_loads[cluster_loads > target_load * 1.02].index.tolist()
        underloaded = cluster_loads[cluster_loads < target_load * 0.98].index.tolist()
        
        if not overloaded:
            print(f"Converged at iter {i}. All clusters within tolerance.")
            break
            
        # Move points
        moved = False
        sorted_over = sorted(overloaded, key=lambda c: cluster_loads[c], reverse=True)
        
        for c_from in sorted_over:
            # Points in this cluster
            points_idx = df[df['Cluster'] == c_from].index
            
            # Find point closest to any underloaded cluster center
            # Calculate centers
            centers = df.groupby('Cluster')[['Lat', 'Lon']].mean()
            
            best_move = None # (point_idx, c_to, dist_reduction?)
            
            # We want to move a point that is on the boundry
            # For each point in source cluster, find dist to all underloaded centers
            for p_idx in points_idx:
                p_lat, p_lon = df.loc[p_idx, ['Lat', 'Lon']]
                p_load = df.loc[p_idx, 'Load']
                
                # Filter useful targets (capacity check)
                valid_targets = [
                    c_to for c_to in underloaded 
                    if cluster_loads[c_to] + p_load <= target_load * 1.1 # Dont overfill target
                ]
                
                if not valid_targets:
                    continue
                
                # Find closest valid target
                dists = []
                for c_to in valid_targets:
                    c_lat, c_lon = centers.loc[c_to]
                    d = (p_lat - c_lat)**2 + (p_lon - c_lon)**2 # Squared Euclidean
                    dists.append((d, c_to))
                
                dists.sort()
                min_d, best_c = dists[0]
                
                # Compare with distance to current center
                curr_lat, curr_lon = centers.loc[c_from]
                curr_d = (p_lat - curr_lat)**2 + (p_lon - curr_lon)**2
                
                # Score: prefer points far from current center and close to new center
                # Lower score is better assignment change
                # Heuristic: Ratio ? Or Difference?
                # We interpret this as: "Is this point better served by C_to than C_from?"
                # Or simply: "Is P closer to C_to than C_from?"
                # NO, we must move even if it's closer to C_from, to balance load.
                # So we pick the "Least Bad" move (Smallest distance to target).
                
                move_score = min_d # Minimize distance to new center
                
                if best_move is None or move_score < best_move[0]:
                    best_move = (move_score, p_idx, best_c)
            
            if best_move:
                score, p_idx, c_to = best_move
                df.at[p_idx, 'Cluster'] = c_to
                # Update loads temporarily
                cluster_loads[c_from] -= df.loc[p_idx, 'Load']
                cluster_loads[c_to] += df.loc[p_idx, 'Load']
                moved = True
                break # Move one point per iteration per cluster? Or just one global?
                # Moving one per cluster avoids oscillation
        
        if not moved:
            print(f"Stalled at iter {i}. No valid moves found.")
            break
            
    # Final Stats
    final_loads = df.groupby('Cluster')['Load'].sum()
    print("\nFinal Cluster Loads:")
    print(final_loads)
    print(f"Load Range: {final_loads.min()} - {final_loads.max()}")
    print(f"Std Dev: {final_loads.std():.2f}")
    
    return df

if __name__ == "__main__":
    df = load_data()
    if df is not None:
        df = cluster_points(df)
        df.to_csv('phase2_clustered.csv', index=False, encoding='utf-8-sig')
        print("Clustering done.")
