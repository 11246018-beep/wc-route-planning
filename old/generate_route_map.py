
import pandas as pd
import folium
from folium.plugins import GroupedLayerControl, BeautifyIcon

# Config
OUTPUT_FILE = 'Weekly_Schedule.xlsx'
MAP_FILE = 'Weekly_Schedule_Map.html'

# Saturated Colors (Tab10)
COLORS = [
    '#E31A1C', '#1F78B4', '#33A02C', '#6A3D9A', '#FF7F00', '#B15928', 
    '#A6CEE3', '#B2DF8A', '#FB9A99', '#FDBF6F', '#CAB2D6', '#FFFF99', 
    '#000000', '#808080'
]

BASE_COORDS = {'PZ': (24.90679, 121.22683), 'WG': (25.07055, 121.44141)}
BASE_NAMES = {'PZ': '平鎮倉', 'WG': '五股倉'}
BASE_COLORS = {'PZ': 'darkblue', 'WG': 'darkred'}
# Folium Icon colors are limited for standard markers.
# 'darkblue', 'darkred' are valid for folium.Icon

STAFF_TEAM_MAP = {
    'S01': 'PZ', 'S02': 'PZ', 'S03': 'PZ', 'S04': 'PZ', 'S05': 'PZ', 'S06': 'PZ', 'S07': 'PZ', 
    'S08': 'PZ', 'S09': 'PZ', 'S10': 'PZ', 'S11': 'PZ', 'S12': 'PZ', 'S13': 'WG', 'S14': 'WG'
}

def create_map():
    print("Loading schedule...")
    df = pd.read_excel(OUTPUT_FILE)
    
    # Use OpenStreetMap for better Chinese labels support
    m = folium.Map(location=[24.95, 121.3], zoom_start=11, tiles='OpenStreetMap')
    
    staff_ids = sorted(list(set(df['員工代號'])))
    color_map = {s_id: COLORS[i % len(COLORS)] for i, s_id in enumerate(staff_ids)}

    # 1. Compact Legend (No Emojis, Small Font)
    legend_items = ""
    for s_id in staff_ids:
        c = color_map[s_id]
        legend_items += f'''
        <div style="margin-bottom:2px; display:flex; align-items:center;">
            <span style="background-color:{c}; width:10px; height:10px; border-radius:50%; margin-right:6px; display:inline-block;"></span>
            <span style="color:#333;">{s_id}</span>
        </div>'''

    legend_html = f'''
     <div style="position: fixed; 
     bottom: 20px; right: 20px; width: 140px; max-height: 400px;
     background-color:rgba(255, 255, 255, 0.9); padding: 8px; border-radius: 4px; 
     border: 1px solid #ccc; font-size:11px; font-family: sans-serif; z-index:9999;">
     <div style="border-bottom:1px solid #ddd; margin-bottom:5px; padding-bottom:2px; font-weight:bold;">圖例</div>
     <div style="margin-bottom:2px;"><i class="fa fa-home" style="color:darkblue"></i> 平鎮倉</div>
     <div style="margin-bottom:2px;"><i class="fa fa-home" style="color:darkred"></i> 五股倉</div>
     <div style="margin-bottom:5px;"><i class="fa fa-plus-circle" style="color:red"></i> 支援</div>
     {legend_items}
     </div>
     '''
    m.get_root().html.add_child(folium.Element(legend_html))

    # 2. Add Warehouses (Standard Map Markers)
    for team, (lat, lon) in BASE_COORDS.items():
        folium.Marker(
            location=[lat, lon],
            popup=BASE_NAMES[team],
            icon=folium.Icon(color=BASE_COLORS[team], icon='home', prefix='fa')
        ).add_to(m)

    # 3. Process Routes
    day_layers = {}
    for day in range(1, 7):
        fg = folium.FeatureGroup(name=f"Day {day}", show=(day==1))
        day_layers[day] = fg
        m.add_child(fg)

    grouped = df.groupby(['日程(Day)', '員工代號'])
    
    for (day, staff), group in grouped:
        layer = day_layers[day]
        group = group.sort_values('順序').reset_index(drop=True)
        color = color_map[staff]
        
        team = STAFF_TEAM_MAP.get(staff, 'PZ')
        base_lat, base_lon = BASE_COORDS[team]
        path_coords = [(base_lat, base_lon)]
        
        for i in range(len(group)):
            row = group.iloc[i]
            lat, lon = float(row['緯度']), float(row['經度'])
            seq = int(row['順序'])
            is_support = (row['任務屬性'] == 'Support')
            
            # Next Stop
            if i < len(group) - 1:
                next_addr = group.iloc[i+1]['地址']
                next_stop = next_addr
            else:
                next_stop = f"返回{BASE_NAMES[team]}"

            # Clean Popup (No Emojis, Standard Text)
            popup_html = f"""
            <div style="font-family: sans-serif; font-size: 12px; width: 220px;">
                <div style="font-weight:bold; border-bottom:1px solid #ccc; padding-bottom:3px; margin-bottom:5px;">
                    {staff} ({row['工作區域']}) - 週{day} 第{seq}站
                </div>
                地點: {row['地址']}<br>
                本站: {int(row['維護時間(分)'])} 分<br>
                累計: {int(row['累計工時(分)'])} 分<br>
                <div style="margin-top:5px; padding-top:3px; border-top:1px dashed #eee; color:#555;">
                    下一站: {next_stop}
                </div>
                {f'<div style="color:red; font-weight:bold; margin-top:5px;">[跨區支援]</div>' if is_support else ''}
            </div>
            """
            
            path_coords.append((lat, lon))
            
            # Marker Style: Numbered Circle (User Request)
            # Normal: Numbered Circle
            # Support: Simple Small Icon (Plus)
            
            if is_support:
                icon_obj = BeautifyIcon(
                    icon='plus', # Simple small icon
                    icon_shape='circle',
                    border_color='#b30000', # Darker red border
                    text_color='white',
                    background_color='#d32f2f', # Red background
                    inner_icon_style='font-size:12px; margin-top:2px;'
                )
            else:
                icon_obj = BeautifyIcon(
                    icon_shape='circle',
                    number=seq, # Number on point
                    border_color=color,
                    text_color='white',
                    background_color=color,
                    inner_icon_style='font-size:11px; font-weight:bold; margin-top:-2px;'
                )

            folium.Marker(
                location=[lat, lon],
                popup=folium.Popup(popup_html, max_width=300),
                tooltip=f"{staff} #{seq} | {row['地址']}",
                icon=icon_obj
            ).add_to(layer)

        # Return to base
        path_coords.append((base_lat, base_lon))
        
        # Route Line
        folium.PolyLine(
            path_coords,
            color=color,
            weight=2,
            opacity=0.7
        ).add_to(layer)

    folium.LayerControl(collapsed=False).add_to(m)
    print(f"Saving to {MAP_FILE}...")
    m.save(MAP_FILE)
    print("Done.")

if __name__ == "__main__":
    create_map()
