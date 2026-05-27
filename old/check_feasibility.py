import pandas as pd

def check_feasibility():
    input_file = 'maintenance_data_aggregated.csv'
    try:
        df = pd.read_csv(input_file)
    except FileNotFoundError:
        print("File not found.")
        return

    # User request: "先不用考慮每周兩次的次數 都先當作每周清理一次來計算"
    # This means we treat count as 1 for every location.
    # We need to get the per-visit maintenance time. 
    # '維護時間' is currently the SUM. So we divide by count.
    # '行車距離' is currently AVERAGE (distance / count).
    
    # Cost per visit = (Sum Maintenance / Count) + Avg Travel Distance
    df['per_visit_maint'] = df['維護時間'] / df['count']
    df['cost_per_visit'] = df['per_visit_maint'] + df['行車距離']
    
    total_cost_all = df['cost_per_visit'].sum()
    print(f"Total Cost (Count=1 assumption): {total_cost_all}")
    
    limit_per_zone = 540
    max_zones = 14
    total_capacity = limit_per_zone * max_zones
    
    print(f"Max Capacity ({max_zones} zones * {limit_per_zone}): {total_capacity}")
    
    if total_cost_all > total_capacity:
        print("RESULT: IMPOSSIBLE")
        print(f"Ratio: {total_cost_all / total_capacity:.2f}")
        print(f"Required Zones: {total_cost_all / limit_per_zone:.2f}")
    else:
        print("RESULT: FEASIBLE")

if __name__ == "__main__":
    check_feasibility()
