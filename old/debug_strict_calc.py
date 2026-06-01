
import json
import pandas as pd
import utils
import collections
import numpy as np

def update_entry(e, df):
    tasks_list = e['Details']['tasks']
    if not tasks_list:
        return 0
    
    # Recalc clean sum
    clean_sum = 0
    for t in tasks_list:
        clean_sum += t.get('clean_time', 0)
    
    # Simulated OSRM (don't call API to speed up, just use existing duration if non-zero, or estimate)
    # Actually we want to see what strict_fix sees.
    # strict_fix calls utils.get_osrm_route. 
    # Let's call it.
    
    depot = utils.PZ_DEPOT
    if 'WG' in e['Driver']: depot = utils.WG_DEPOT
    coords = [(depot['lon'], depot['lat'])]
    for i, t in enumerate(tasks_list):
        if i > 0:
            prev = tasks_list[i-1]
            p_zip = prev.get('zipcode') or df.loc[prev['id']]['郵遞區號3碼  ']
            t_zip = t.get('zipcode') or df.loc[t['id']]['郵遞區號3碼  ']
            is_cross = utils.is_cross_county(p_zip, t_zip)
            if is_cross: 
                coords.append((depot['lon'], depot['lat']))
                print(f"  CROSS: {p_zip} -> {t_zip} (Task {prev['id']} -> {t['id']})")
        coords.append((t['lon'], t['lat']))
    coords.append((depot['lon'], depot['lat']))
    
    r = utils.get_osrm_route(coords)
    if r:
        dur_min = r['duration'] / 60
        tot = dur_min + clean_sum
        print(f"DEBUG ENTRY: Sheet={e.get('Sheet')} Tasks={len(tasks_list)} Clean={clean_sum} Dur={dur_min:.1f} Tot={tot:.1f}")
        return tot
    else:
        print(f"DEBUG ENTRY: OSRM FAILED for {e.get('Sheet')}")
        return 0

def debug():
    with open('schedule_output_strict.json', 'r') as f:
        schedule = json.load(f)
    print("Loaded schedule.")
    
    df = pd.read_excel('maintenance_data_v2.xlsx', sheet_name='Wcrp53311b水肥車每日維護週期表')
    # Use default index as ID
    
    # Filter WG_01 Mon
    entries = [e for e in schedule if e['Driver'] == 'Driver_WG_01' and e['Day'] == 'Mon']
    print(f"Found {len(entries)} entries for WG_01 Mon.")
    
    total = 0
    for e in entries:
        t = update_entry(e, df)
        print(f"Sheet {e.get('Sheet')}: {t:.1f}")
        total += t
    
    print(f"Daily Total: {total:.1f}")

if __name__ == "__main__":
    debug()
