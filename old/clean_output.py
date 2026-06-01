
import json
import os

def clean_schedule():
    file_path = 'schedule_output_fixed.json'
    if not os.path.exists(file_path):
        print("File not found.")
        return

    with open(file_path, 'r', encoding='utf-8') as f:
        data = json.load(f)
    
    # Filter out entries where Driver is 'Unassigned'
    cleaned_data = [entry for entry in data if entry['Driver'] != 'Unassigned']
    
    removed_count = len(data) - len(cleaned_data)
    print(f"Removed {removed_count} 'Unassigned' entries.")
    
    with open(file_path, 'w', encoding='utf-8') as f:
        json.dump(cleaned_data, f, ensure_ascii=False, indent=2)
    print("Saved cleaned schedule.")

if __name__ == "__main__":
    clean_schedule()
