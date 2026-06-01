
import pandas as pd
import numpy as np
from sklearn.cluster import KMeans
import json
import os
import utils
from datetime import timedelta
from schedule_generator import load_data, assign_drivers_to_clusters, schedule_driver_tasks

def allocate_clusters_to_counties(df, total_clusters=14):
    """
    Allocates 14 clusters to counties based on task count.
    """
    df['county'] = df['zipcode'].apply(utils.get_county_from_zip)
    county_counts = df['county'].value_counts()
    
    # Calculate proportions
    total_tasks = len(df)
    allocations = (county_counts / total_tasks * total_clusters).round().astype(int)
    
    # Adjust to sum to 14
    diff = total_clusters - allocations.sum()
    if diff != 0:
        # Add/Subtract from largest
        target = allocations.index[0]
        allocations[target] += diff
        
    print("Cluster Allocations by County:", allocations)
    return allocations

def cluster_by_county(df, allocations):
    """
    Cluster within each county.
    """
    df['cluster'] = -1
    
    cluster_offset = 0
    for county, n_clus in allocations.items():
        if n_clus <= 0:
            # Assign explicitly to nearest? Or just include in 'Others'?
            # For simplicity, force at least 1 if count > 0?
            if len(df[df['county'] == county]) > 0:
                 n_clus = 1
                 # Adjust global sum? This is a heuristic script.
        
        county_tasks = df[df['county'] == county]
        if n_clus == 1:
            df.loc[county_tasks.index, 'cluster'] = cluster_offset
        else:
            kmeans = KMeans(n_clusters=n_clus, random_state=42, n_init=10)
            labels = kmeans.fit_predict(county_tasks[['lat', 'lon']])
            df.loc[county_tasks.index, 'cluster'] = labels + cluster_offset
            
        cluster_offset += n_clus
        
    return df

def main_v2():
    # Reuse load_data from v1
    w1_df, w2_df = load_data()
    
    print("\n--- Week 1 County Clustering ---")
    allocs_w1 = allocate_clusters_to_counties(w1_df, 14)
    w1_df = cluster_by_county(w1_df, allocs_w1)
    
    # Verify cluster count
    n_w1_clusters = w1_df['cluster'].nunique()
    print(f"Total W1 Clusters: {n_w1_clusters}")
    
    # ... Assignment Logic ...
    # Reuse assign_drivers_to_clusters?
    # Yes, it assigns based on centroid distance to depot.
    w1_clusters = assign_drivers_to_clusters(w1_df)
    
    final_output = []
    
    print("\nProcessing Week 1 (County Based)...")
    for cluster_id, info in w1_clusters.items():
        driver = info['driver']
        depot = info['depot']
        tasks = w1_df[w1_df['cluster'] == cluster_id]
        print(f"  Scheduling {driver} (Cluster {cluster_id}, {len(tasks)} tasks)...")
        # Reuse scheduling
        schedule = schedule_driver_tasks(driver, depot, tasks, is_week2=False)
        
        for day, data in schedule.items():
            if not data: continue
            entry = {
                'Sheet': '週清1',
                'Driver': driver,
                'Depot': depot['name'],
                'Day': day,
                'Tasks': [t['id'] for t in data['tasks']],
                'Details': data
            }
            final_output.append(entry)

    # Week 2
    print("\n--- Week 2 County Clustering ---")
    allocs_w2 = allocate_clusters_to_counties(w2_df, 14)
    w2_df = cluster_by_county(w2_df, allocs_w2)
    
    w2_clusters = assign_drivers_to_clusters(w2_df)
    
    print("\nProcessing Week 2 (County Based)...")
    for cluster_id, info in w2_clusters.items():
        driver = info['driver']
        depot = info['depot']
        tasks = w2_df[w2_df['cluster'] == cluster_id]
        print(f"  Scheduling {driver} (Cluster {cluster_id}, {len(tasks)} tasks)...")
        
        schedule = schedule_driver_tasks(driver, depot, tasks, is_week2=True)
         
        for day, data in schedule.items():
            if not data: continue
            entry = {
                'Sheet': '週清2',
                'Driver': driver,
                'Depot': depot['name'],
                'Day': day,
                'Tasks': [t['id'] for t in data['tasks']],
                'Details': data
            }
            final_output.append(entry)
            
    # Save output
    with open('schedule_output.json', 'w', encoding='utf-8') as f:
        json.dump(final_output, f, ensure_ascii=False, indent=2)
        
    print("Schedule generation v2 complete. Saved to schedule_output.json")

if __name__ == "__main__":
    main_v2()
