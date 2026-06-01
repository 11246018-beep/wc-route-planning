
import json

with open('schedule_output_strict.json', 'r') as f:
    data = json.load(f)

for entry in data:
    if entry.get('Driver') == 'Driver_WG_01' and entry.get('Day') == 'Mon':
        print(f"Sheet: {entry.get('Sheet')}")
        details = entry.get('Details', {})
        print(f"  Duration: {details.get('duration_s', 0)/60:.1f} min")
        print(f"  Distance: {details.get('distance_m', 0)/1000:.1f} km")
        tasks = details.get('tasks', [])
        clean_time = sum(t['clean_time'] for t in tasks)
        print(f"  Clean Time: {clean_time} min")
        print(f"  Tasks Count: {len(tasks)}")
        print(f"  Total Time (calc): {(details.get('duration_s', 0)/60 + clean_time):.1f} min")
        print(f"  Stored Total: {details.get('total_time_min')}")
