
import json
import pandas as pd
import utils
import collections
import numpy as np
import os

# JSON Encoder
class NpEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, np.integer):
            return int(obj)
        if isinstance(obj, np.floating):
            return float(obj)
        if isinstance(obj, np.ndarray):
            return obj.tolist()
        return super(NpEncoder, self).default(obj)

def strict_fix():
    print("Running Strict 540m Fix (Final Report Mode)...")
    
    # Load Schedule
    if not os.path.exists('schedule_output_fixed.json'):
        print("schedule_output_fixed.json not found")
        input_file = 'schedule_output_fixed.json'
        if os.path.exists('schedule_output.json'):
             input_file = 'schedule_output.json'
        else:
             return
    else:
        input_file = 'schedule_output_fixed.json'
        
    with open(input_file, 'r', encoding='utf-8') as f:
        schedule = json.load(f)

    df = pd.read_excel('maintenance_data_v2.xlsx', sheet_name='Wcrp53311b水肥車每日維護週期表')
    
    unassigned_tasks = [] 
    
    # 1. Prune Overloaded Routes (Daily Aggregation)
    print("\n--- Pruning Routes > 540m (Daily Limit) ---")
    
    def get_zip(tid):
        if tid in df.index:
            return df.loc[tid]['郵遞區號3碼  ']
        return ''

    # Organize by Driver/Day
    driver_day_entries = collections.defaultdict(list)
    for entry in schedule:
        if entry['Driver'] == 'Unassigned': continue
        driver_day_entries[(entry['Driver'], entry['Day'])].append(entry)
        
    for (driver, day), entries in driver_day_entries.items():
        while True:
            # Recalculate totals for all entries on this day
            daily_total = 0
            
            def update_entry(e):
                tasks_list = e['Details']['tasks']
                if not tasks_list:
                    e['Details'].update({'duration_s':0, 'distance_m':0, 'geometry':None, 'total_time_min':0})
                    return 0
                
                # Build coords
                depot = utils.PZ_DEPOT
                if 'WG' in entry['Driver']: depot = utils.WG_DEPOT
                coords = [(depot['lon'], depot['lat'])]
                clean_sum = 0
                for i, t in enumerate(tasks_list):
                    clean_sum += t['clean_time']
                    if i > 0:
                        prev = tasks_list[i-1]
                        p_zip = prev.get('zipcode') or get_zip(prev['id'])
                        t_zip = t.get('zipcode') or get_zip(t['id'])
                        if utils.is_cross_county(p_zip, t_zip): coords.append((depot['lon'], depot['lat']))
                    coords.append((t['lon'], t['lat']))
                coords.append((depot['lon'], depot['lat']))
                
                r = utils.get_osrm_route(coords)
                if r:
                    dur_min = r['duration'] / 60
                    tot = dur_min + clean_sum
                    
                    e['Details']['duration_s'] = r['duration']
                    e['Details']['distance_m'] = r['distance']
                    e['Details']['geometry'] = r['geometry']
                    e['Details']['total_time_min'] = tot
                    
                    # Update legs/acc_time
                    legs = r.get('legs', [])
                    acc_time_s = 0
                    leg_ptr = 0
                    for i, t in enumerate(tasks_list):
                        is_cross = False
                        if i > 0:
                             prev = tasks_list[i-1]
                             p_zip = prev.get('zipcode') or get_zip(prev['id'])
                             t_zip = t.get('zipcode') or get_zip(t['id'])
                             if utils.is_cross_county(p_zip, t_zip): is_cross = True
                        
                        if is_cross:
                            if leg_ptr < len(legs): acc_time_s += legs[leg_ptr]['duration']; leg_ptr += 1
                            if leg_ptr < len(legs): acc_time_s += legs[leg_ptr]['duration']; leg_ptr += 1
                        else:
                            if leg_ptr < len(legs): acc_time_s += legs[leg_ptr]['duration']; leg_ptr += 1
                        
                        arrival_min = acc_time_s / 60
                        t['arrival_time'] = round(arrival_min, 1)
                        acc_time_s += (t['clean_time'] * 60)
                        t['departure_time'] = round(acc_time_s / 60, 1)
                        t['acc_time_str'] = f"{round(arrival_min + t['clean_time'], 1)} min"

                    return tot
                else:
                    prev_tot = e['Details'].get('total_time_min', 0)
                    if prev_tot > 0: return prev_tot
                    return 9999 
            
            current_totals = []
            for e in entries:
                t = update_entry(e)
                current_totals.append((t, e))
                
            daily_total = sum(x[0] for x in current_totals)
            
            if daily_total > 540:
                current_totals.sort(key=lambda x: x[0], reverse=True)
                largest_entry_time, largest_entry = current_totals[0]
                
                tasks = largest_entry['Details']['tasks']
                if tasks:
                    excess = daily_total - 540
                    total_dur = largest_entry['Details'].get('duration_s', 0) / 60
                    avg_drive = total_dur / len(tasks) if len(tasks) > 0 else 5
                    
                    removed_count = 0
                    while excess > 0 and tasks:
                        last_task = tasks[-1]
                        savings_est = last_task['clean_time'] + avg_drive
                        
                        removed = tasks.pop()
                        
                        unassigned_tasks.append({
                            'id': removed['id'],
                            'sheet': largest_entry['Sheet'],
                            'from_day': largest_entry['Day']
                        })
                        
                        excess -= savings_est
                        removed_count += 1
                    
                    largest_entry['Tasks'] = [t['id'] for t in tasks] 
                    print(f"  {driver} {day}: Batch Pruned {removed_count} Tasks (Excess {daily_total - 540:.1f} -> Est {excess:.1f})")
                else:
                    break
            else:
                break

    # 2. Try to Reassign Unassigned Tasks (Strictly Best-Feasible)
    print(f"\n--- Attempting to Reassign {len(unassigned_tasks)} Tasks (Best-Feasible Pass) ---")
    
    reassigned_count = 0
    final_unassigned = []
    reassignment_report = []
    
    # Sort unassigned by duration to try consistent order
    # unassigned_tasks.sort(key=lambda x: x['id']) 
    
    for i, item in enumerate(unassigned_tasks):
        if i % 100 == 0: print(f"Processing {i}/{len(unassigned_tasks)}...")
        
        tid = item['id']
        sheet = item['sheet']
        
        if tid not in df.index:
             final_unassigned.append(item)
             reassignment_report.append(f"Task {tid}: ID Missing")
             continue
             
        row = df.loc[tid]
        if isinstance(row, pd.DataFrame):
            row = row.iloc[0]
            
        task_time = 15
        if pd.notnull(row['維護時間']): task_time = row['維護時間']
        task_zip = row['郵遞區號3碼  ']
        
        assigned = False
        best_fail_reason = "No candidates check passed"
        min_deficit = 9999
        
        for entry in schedule:
            if entry['Driver'] == 'Unassigned': continue
            if entry['Sheet'] != sheet: continue 
            
            all_day_entries = [e for e in schedule if e['Driver'] == entry['Driver'] and e['Day'] == entry['Day']]
            total_existing_time = 0
            current_entry_time = 0
            for e in all_day_entries:
                d_dur = e['Details']['duration_s'] / 60
                d_cln = sum(t['clean_time'] for t in e['Details']['tasks'])
                total_existing_time += (d_dur + d_cln)
                if e is entry:
                    current_entry_time = (d_dur + d_cln)
            
            if total_existing_time + task_time + 5 > 540: 
                deficit = (total_existing_time + task_time + 5) - 540
                if deficit < min_deficit:
                    min_deficit = deficit
                    best_fail_reason = f"Capacity Full (Deficit {deficit:.1f}m)"
                continue 
            
            # Distance Heuristic
            current_tasks = entry['Details']['tasks']
            if current_tasks:
                last_task = current_tasks[-1]
                l_lat = last_task['lat']
                l_lon = last_task['lon']
                t_lat = row['緯度']
                t_lon = row['經度']
                dist_km = utils.haversine_distance((l_lat, l_lon), (t_lat, t_lon))
                if dist_km > 5: 
                    best_fail_reason = "Too Far (>5km)"
                    continue
            
            # Build Coords
            depot = utils.PZ_DEPOT
            if 'WG' in entry['Driver']: depot = utils.WG_DEPOT
            temp_tasks = current_tasks + [{
                'id': tid,
                'lat': row['緯度'],
                'lon': row['經度'],
                'clean_time': task_time,
                'zipcode': task_zip
            }]
            
            coords = [(depot['lon'], depot['lat'])]
            for idx, t in enumerate(temp_tasks):
                if idx > 0:
                    prev = temp_tasks[idx-1]
                    p_zip = prev.get('zipcode') or get_zip(prev['id'])
                    c_zip = t.get('zipcode') or get_zip(t['id'])
                    if utils.is_cross_county(p_zip, c_zip):
                        coords.append((depot['lon'], depot['lat']))
                coords.append((t['lon'], t['lat']))
            coords.append((depot['lon'], depot['lat']))
            
            route_info = utils.get_osrm_route(coords)
            if route_info:
                new_dur = route_info['duration'] / 60
                clean = sum(t['clean_time'] for t in entry['Details']['tasks']) 
                new_clean = clean + task_time
                
                other_sheet_time = total_existing_time - current_entry_time
                if other_sheet_time + (new_dur + new_clean) <= 540:
                    # Success
                    entry['Details']['tasks'] = temp_tasks
                    entry['Tasks'].append(tid)
                    entry['Details']['duration_s'] = route_info['duration']
                    entry['Details']['distance_m'] = route_info['distance']
                    entry['Details']['geometry'] = route_info['geometry']
                    entry['Details']['tasks'][-1]['name'] = row['客戶名稱']
                    entry['Details']['tasks'][-1]['address'] = row['服務地點 ']
                    
                    assigned = True
                    reassigned_count += 1
                    print(f"  Reassigned {tid} to {entry['Driver']} {entry['Day']}")
                    break
                else:
                    best_fail_reason = f"OSRM Over Limit (Excess {(other_sheet_time + new_dur + new_clean) - 540:.1f}m)"
            else:
                 best_fail_reason = "OSRM Error"
        
        if not assigned:
            final_unassigned.append(tid)
            reassignment_report.append(f"Task {tid}: {best_fail_reason}")
            
    print(f"\nDone. Reassigned: {reassigned_count}. Final Unassigned: {len(final_unassigned)}")
    
    # Save Final Schedule
    with open('schedule_output_strict.json', 'w', encoding='utf-8') as f:
        json.dump(schedule, f, ensure_ascii=False, indent=2, cls=NpEncoder)
        
    # Write Report
    with open('best_feasible_report.txt', 'w', encoding='utf-8') as f:
        f.write("Strict Schedule - Unassigned Tasks Analysis\n")
        f.write("===========================================\n")
        f.write(f"Total Unassigned: {len(final_unassigned)}\n\n")
        for line in reassignment_report:
            f.write(line + "\n")

import os
if __name__ == "__main__":
    strict_fix()
