
import json
import os

def generate_html_map(json_file, output_html):
    with open(json_file, 'r', encoding='utf-8') as f:
        data = json.load(f)
        
    # Prepare data for JS
    # We need: list of routes, each with: driver, day, sheet, geometry, tasks (points)
    
    # Check if map_template.html exists
    if not os.path.exists('map_template.html'):
        print("Error: map_template.html not found.")
        return

    with open('map_template.html', 'r', encoding='utf-8') as f:
        template = f.read()
        
    # Inject data
    json_str = json.dumps(data, ensure_ascii=False)
    
    final_html = template.replace('{{SCHEDULE_DATA}}', json_str)
    
    with open(output_html, 'w', encoding='utf-8') as f:
        f.write(final_html)
        
    print(f"Map generated at {output_html}")

if __name__ == "__main__":
    input_file = 'schedule_output_strict.json' if os.path.exists('schedule_output_strict.json') else ('schedule_output_fixed.json' if os.path.exists('schedule_output_fixed.json') else 'schedule_output.json')
    generate_html_map(input_file, 'Weekly_Schedule_Map.html')
