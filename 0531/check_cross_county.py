import pandas as pd
import os

# Configuration
INPUT_FILE = 'Weekly_Schedule_Updated.xlsx'
OUTPUT_REPORT = 'cross_county_report.txt'

def get_county(address):
    if not isinstance(address, str) or len(address) < 3:
        return 'Unknown'
    
    county = address[:3]
    # Handle potentially ambiguous cases or special requirements
    # For now, strictly first 3 chars (e.g., "桃園市", "新竹縣", "新竹市")
    # Note: "新竹市" and "新竹縣" might be considered same "area" by some clients, 
    # but strictly they are different administrative divisions. 
    # User asked for "different county/city" (跨縣市).
    # Common practice in Taiwan logistics: Taipei/New Taipei are often grouped, but strictly different.
    # Let's report ALL strict differences first.
    return county

def check_cross_county():
    print("Loading schedule...", flush=True)
    try:
        # Load the updated file if exists, else original
        file_to_load = INPUT_FILE if os.path.exists(INPUT_FILE) else 'Weekly_Schedule.xlsx'
        df = pd.read_excel(file_to_load)
        print(f"Loaded {file_to_load}", flush=True)
    except Exception as e:
        print(f"Error loading file: {e}", flush=True)
        return

    print("Checking for cross-county travel...", flush=True)
    
    report_lines = []
    report_lines.append("跨縣市移動檢查報表")
    report_lines.append("=============================================")
    report_lines.append(f"檢查檔案: {file_to_load}")
    report_lines.append("說明：列出同一天內出現在兩個以上不同縣市(前三個字)的員工")
    report_lines.append("---------------------------------------------")
    
    # Group by Staff and Day
    grouped = df.groupby(['員工代號', '日程(Day)'])
    sorted_keys = sorted(grouped.groups.keys())
    
    found_any = False
    
    for (staff_id, day) in sorted_keys:
        group = grouped.get_group((staff_id, day)).sort_values('順序')
        
        visited_counties = set()
        details = []
        
        for idx, row in group.iterrows():
            addr = str(row['地址'])
            county = get_county(addr)
            if county != 'Unknown':
                visited_counties.add(county)
                details.append(f"{county}({addr})")
        
        # Check if more than 1 unique county
        if len(visited_counties) > 1:
            found_any = True
            counties_str = ", ".join(sorted(list(visited_counties)))
            line = f"員工: {staff_id} | Day: {day} | 跨越縣市: {counties_str}"
            print(line, flush=True)
            report_lines.append(line)
            
            # Optional: Detailed path
            # report_lines.append(f"  路徑: {' -> '.join(details)}")
            
    if not found_any:
        msg = "未發現任何跨縣市移動的情況。"
        print(msg, flush=True)
        report_lines.append(msg)
        
    # Write Report
    with open(OUTPUT_REPORT, 'w', encoding='utf-8') as f:
        f.write("\n".join(report_lines))
    
    print(f"Report generated: {OUTPUT_REPORT}", flush=True)

if __name__ == "__main__":
    check_cross_county()
