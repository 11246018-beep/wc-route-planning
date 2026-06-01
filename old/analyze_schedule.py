
import json
import pandas as pd
import collections

def analyze_schedule(json_file, input_excel):
    print(f"Analyzing {json_file}...")
    
    with open(json_file, 'r', encoding='utf-8') as f:
        schedule = json.load(f)
        
    df = pd.read_excel(input_excel, sheet_name='Wcrp53311b水肥車每日維護週期表')
    total_tasks_w1 = df[df['週清1'].notnull()].shape[0]
    total_tasks_w2 = df[df['週清2'].notnull()].shape[0] * 2 # Count each instance?
    # Actually, in Input, Week 2 task is one row, but needs 2 visits.
    # Schedule output contains "Task IDs".
    
    # Collect scheduled tasks
    scheduled_tasks_w1 = set()
    scheduled_tasks_w2 = [] # list because unique tasks appear twice
    
    driver_stats = collections.defaultdict(lambda: {'time': 0, 'days': collections.defaultdict(int)})
    
    for entry in schedule:
        driver = entry['Driver']
        day = entry['Day']
        sheet = entry['Sheet']
        duration = (entry['Details']['duration_s'] / 60) # mins driving
        clean_time = sum(t['clean_time'] for t in entry['Details']['tasks'])
        total_time = duration + clean_time
        
        driver_stats[driver]['time'] += total_time
        driver_stats[driver]['days'][day] += total_time
        
        task_ids = entry['Tasks']
        if sheet == '週清1':
            scheduled_tasks_w1.update(task_ids)
        else:
            scheduled_tasks_w2.extend(task_ids)
            
    # Verification
    print("\n--- Workload Stats ---")
    for driver, stats in sorted(driver_stats.items()):
        print(f"{driver}: {stats['time']:.1f} min/week")
        for day, t in stats['days'].items():
            if t > 540:
                print(f"  WARNING: {day} over limit! ({t:.1f} min)")
    
    print("\n--- Task Coverage ---")
    
    # Week 1
    missed_w1 = total_tasks_w1 - len(scheduled_tasks_w1)
    print(f"Week 1: Scheduled {len(scheduled_tasks_w1)} / {total_tasks_w1}. Missed: {missed_w1}")
    
    # Week 2
    # Week 2 tasks need to be done TWICE.
    # Check simple count
    # unique tasks in W2
    w2_input_ids = df[df['週清2'].notnull()].index.tolist()
    
    # Count occurrences in schedule
    w2_counts = collections.Counter(scheduled_tasks_w2)
    
    missed_w2 = 0
    under_scheduled_w2 = 0
    for tid in w2_input_ids:
        if w2_counts[tid] == 0:
            missed_w2 += 1
        elif w2_counts[tid] < 2:
            under_scheduled_w2 += 1
            
    print(f"Week 2: Missed Entirely: {missed_w2}. Scheduled Once (Need Twice): {under_scheduled_w2}")
    
    return missed_w1 + missed_w2 + under_scheduled_w2

if __name__ == "__main__":
    import os
    input_file = 'schedule_output_strict.json' if os.path.exists('schedule_output_strict.json') else ('schedule_output_fixed.json' if os.path.exists('schedule_output_fixed.json') else 'schedule_output.json')
    analyze_schedule(input_file, 'maintenance_data_v2.xlsx')
