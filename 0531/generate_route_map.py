import pandas as pd
import folium
from folium.plugins import BeautifyIcon
import json
import googlemaps
import polyline
import os
import time

# Config
OUTPUT_FILE = 'Weekly_Schedule.xlsx'
MAP_FILE = 'Weekly_Schedule_Map.html'
CACHE_FILE = 'route_cache.json'

# --- API KEY CONFIGURATION ---
# Please replace 'YOUR_API_KEY_HERE' with your actual Google Maps API Key
GOOGLE_MAPS_API_KEY = 'AIzaSyCJbc6sUbEMxNao23NM3V1W9iKw1V7Xc3Q' 

# High Saturation Colors (User Request)
COLORS = [
    '#FF0000', '#00FF00', '#0000FF', '#FFFF00', '#FF00FF', '#00FFFF', 
    '#FF8000', '#800080', '#008000', '#000080', '#800000', '#808000',
    '#008080', '#FF0080'
]

BASE_COORDS = {'PZ': (24.90679, 121.22683), 'WG': (25.07055, 121.44141)}
BASE_NAMES = {'PZ': '平鎮倉', 'WG': '五股倉'}
BASE_COLORS = {'PZ': 'darkblue', 'WG': 'darkred'}
STAFF_TEAM_MAP = {
    'S01': 'PZ', 'S02': 'PZ', 'S03': 'PZ', 'S04': 'PZ', 'S05': 'PZ', 'S06': 'PZ', 'S07': 'PZ', 
    'S08': 'PZ', 'S09': 'PZ', 'S10': 'PZ', 'S11': 'PZ', 'S12': 'PZ', 'S13': 'WG', 'S14': 'WG'
}

# Initialize Google Maps Client
gmaps = None
if GOOGLE_MAPS_API_KEY != 'YOUR_API_KEY_HERE':
    try:
        gmaps = googlemaps.Client(key=GOOGLE_MAPS_API_KEY)
        print("Google Maps Client initialized.")
    except Exception as e:
        print(f"Error initializing Google Maps Client: {e}")
        gmaps = None
else:
    print("Warning: Google Maps API Key not set. Using straight lines.")

# Load Route Cache
route_cache = {}
if os.path.exists(CACHE_FILE):
    try:
        with open(CACHE_FILE, 'r', encoding='utf-8') as f:
            route_cache = json.load(f)
        print(f"Loaded {len(route_cache)} routes from cache.")
    except Exception as e:
        print(f"Error loading cache: {e}")

def save_cache():
    try:
        with open(CACHE_FILE, 'w', encoding='utf-8') as f:
            json.dump(route_cache, f, ensure_ascii=False, indent=2)
        print("Cache saved.")
    except Exception as e:
        print(f"Error saving cache: {e}")

def get_route_polyline(start_coords, end_coords):
    """
    Get route polyline from cache or Google Directions API.
    start_coords: (lat, lon) tuple
    end_coords: (lat, lon) tuple
    Returns: List of (lat, lon) tuples for the route
    """
    if not gmaps:
        return [start_coords, end_coords]

    # Create a unique key for the route
    key = f"{start_coords[0]},{start_coords[1]}|{end_coords[0]},{end_coords[1]}"
    
    if key in route_cache:
        return route_cache[key]
    
    try:
        # Request directions
        # mode='driving' is default
        directions_result = gmaps.directions(
            origin=start_coords,
            destination=end_coords,
            mode="driving"
        )
        
        if directions_result:
            # Decode the polyline from the first route's overview_polyline
            encoded_polyline = directions_result[0]['overview_polyline']['points']
            decoded_points = polyline.decode(encoded_polyline)
            
            # Cache the result
            route_cache[key] = decoded_points
            return decoded_points
        else:
            print(f"No directions found for {key}")
            return [start_coords, end_coords]
            
    except Exception as e:
        print(f"Error getting directions for {key}: {e}")
        return [start_coords, end_coords]

def create_map():
    print("Loading schedule...")
    try:
        df = pd.read_excel(OUTPUT_FILE)
    except FileNotFoundError:
        print(f"Error: {OUTPUT_FILE} not found.")
        return

    m = folium.Map(location=[24.95, 121.3], zoom_start=11, tiles='OpenStreetMap')
    
    staff_ids = sorted(list(set(df['員工代號'])))
    color_map = {s_id: COLORS[i % len(COLORS)] for i, s_id in enumerate(staff_ids)}

    # Add Warehouses (Always Visible)
    for team, (lat, lon) in BASE_COORDS.items():
        folium.Marker(
            location=[lat, lon],
            popup=BASE_NAMES[team],
            icon=folium.Icon(color=BASE_COLORS[team], icon='home', prefix='fa')
        ).add_to(m)

    # Dictionary to store layer IDs for JS
    # Structure: {'S01': {'1': layer_id, '2': layer_id...}, ...}
    layer_map = {} 
    
    # Track API calls to avoid hitting rate limits or spamming
    api_calls_count = 0 

    grouped = df.groupby(['員工代號', '日程(Day)'])
    
    for (staff, day), group in grouped:
        fg = folium.FeatureGroup(name=f"{staff}_Day{day}", show=False)
        m.add_child(fg)
        
        # Add to layer map for JS
        if staff not in layer_map: layer_map[staff] = {}
        layer_map[staff][str(day)] = fg.get_name()
        
        group = group.sort_values('順序').reset_index(drop=True)
        color = color_map[staff]
        
        team = STAFF_TEAM_MAP.get(staff, 'PZ')
        base_lat, base_lon = BASE_COORDS[team]
        
        # Start at Base
        prev_lat, prev_lon = base_lat, base_lon
        
        for i in range(len(group)):
            row = group.iloc[i]
            lat, lon = float(row['緯度']), float(row['經度'])
            seq = int(row['順序'])
            is_support = ('Support' in str(row['任務屬性']))
            is_via_base = ('Via Base' in str(row['任務屬性']))
            
            # --- Line Logic ---
            if is_via_base:
                # 1. Prev -> Base (Dashed)
                route_pts_1 = get_route_polyline((prev_lat, prev_lon), (base_lat, base_lon))
                folium.PolyLine(
                    route_pts_1,
                    color=color, weight=3, dash_array='10, 10', opacity=0.6,
                    tooltip="返回倉庫 (跨區中轉)"
                ).add_to(fg)
                
                # 2. Base -> Curr (Dashed)
                route_pts_2 = get_route_polyline((base_lat, base_lon), (lat, lon))
                folium.PolyLine(
                    route_pts_2,
                    color=color, weight=3, dash_array='10, 10', opacity=0.6,
                    tooltip="前往任務 (跨區中轉)"
                ).add_to(fg)
            else:
                # Direct (Solid)
                # Fetch route from prev to current
                route_pts = get_route_polyline((prev_lat, prev_lon), (lat, lon))
                
                folium.PolyLine(
                    route_pts,
                    color=color, weight=4, opacity=0.9
                ).add_to(fg)
            
            # Update Prev
            prev_lat, prev_lon = lat, lon
            
            # --- Marker Logic ---
            if i < len(group) - 1:
                next_addr = group.iloc[i+1]['地址']
                next_stop = next_addr
            else:
                next_stop = f"返回{BASE_NAMES[team]}"

            popup_html = f"""
            <div style="font-family: sans-serif; font-size: 14px; width: 240px;">
                <div style="font-weight:bold; border-bottom:2px solid {color}; padding-bottom:5px; margin-bottom:10px;">
                    {staff} | 週{day} | 第{seq}站
                </div>
                <b>地點:</b> {row['地址']}<br>
                <b>區域:</b> {row['工作區域']}<br>
                <b>維護:</b> {int(row['維護時間(分)'])} 分<br>
                <b>車程:</b> {row['預估車程(分)']} 分 (累計: {int(row['累計工時(分)'])} 分)<br>
                <div style="margin-top:8px; padding-top:5px; border-top:1px dashed #ccc; color:#666;">
                    Next: {next_stop}
                </div>
                {f'<div style="color:red; font-weight:bold; margin-top:5px;">[跨區支援任務]</div>' if is_support else ''}
                {f'<div style="color:blue; font-weight:bold; margin-top:5px;">[需經由倉庫]</div>' if is_via_base else ''}
            </div>
            """
            
            icon_obj = BeautifyIcon(
                icon_shape='circle',
                number=seq,
                border_color='white',
                text_color='white',
                background_color=color, # Filled high sat color
                inner_icon_style='font-size:12px; font-weight:bold;'
            )

            folium.Marker(
                location=[lat, lon],
                popup=folium.Popup(popup_html, max_width=300),
                tooltip=f"{staff} #{seq}",
                icon=icon_obj
            ).add_to(fg)

        # Final Return to Base (Solid, end of day)
        route_pts_return = get_route_polyline((prev_lat, prev_lon), (base_lat, base_lon))
        folium.PolyLine(
            route_pts_return,
            color=color, weight=2, opacity=0.5, dash_array='5,5'
        ).add_to(fg)
    
    # Save cache before finishing
    save_cache()

    # --- Sidebar & Logic ---
    staff_options = "".join([f'<option value="{s}">{s}</option>' for s in staff_ids])
    day_options = "".join([f'<option value="{d}">Day {d}</option>' for d in range(1, 7)])
    layer_json = json.dumps(layer_map)

    # Mimic the sidebar style
    sidebar_html = f"""
    <style>
        .map-sidebar {{
            position: fixed; top: 10px; left: 50px; width: 300px; 
            z-index: 9999; background: white; padding: 15px; 
            box-shadow: 0 0 15px rgba(0,0,0,0.2); border-radius: 8px;
            font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
        }}
        .map-sidebar h3 {{ margin-top:0; margin-bottom:15px; color:#333; }}
        .form-group {{ margin-bottom: 15px; }}
        .form-group label {{ display: block; margin-bottom: 5px; font-weight: 600; color: #555; }}
        .form-control {{ width: 100%; padding: 8px; border: 1px solid #ddd; border-radius: 4px; }}
        .btn-primary {{ 
            width: 100%; padding: 10px; background-color: #007bff; color: white; 
            border: none; border-radius: 4px; cursor: pointer; font-weight: bold;
        }}
        .btn-primary:hover {{ background-color: #0056b3; }}
        .info-box {{ margin-top: 15px; padding: 10px; background: #f8f9fa; border-radius: 4px; font-size: 12px; color: #666; }}
    </style>
    
    <div class="map-sidebar">
        <h3>排程路線篩選</h3>
        <div class="form-group">
            <label>員工 (Driver)</label>
            <select id="staffSelect" class="form-control">
                {staff_options}
            </select>
        </div>
        <div class="form-group">
            <label>日程 (Day)</label>
            <select id="daySelect" class="form-control">
                {day_options}
            </select>
        </div>
        <button class="btn-primary" onclick="updateMap()">顯示路線</button>
        
        <div class="info-box">
            <b>說明：</b><br>
            - 實線：一般移動<br>
            - 虛線：跨區返回倉庫 / 每日返倉<br>
            - 支援任務：延續該員當日序號
        </div>
    </div>
    
    <script>
        var layerMap = {layer_json};
        
        // Find Leaflet Map Instance
        function getMapInstance() {{
            for(var key in window) {{
                if (key.startsWith('map_')) return window[key];
            }}
            return null;
        }}

        function updateMap() {{
            var staff = document.getElementById('staffSelect').value;
            var day = document.getElementById('daySelect').value;
            var map = getMapInstance();
            
            if (!map) {{ alert("Map initializing..."); return; }}
            
            // 1. Hide ALL staff layers
            for (var s in layerMap) {{
                for (var d in layerMap[s]) {{
                    var layerId = layerMap[s][d];
                    // Access variable logic directly using the ID
                    var layerObj = window[layerId];
                    if (layerObj && map.hasLayer(layerObj)) {{
                        map.removeLayer(layerObj);
                    }}
                }}
            }}
            
            // 2. Show Selected
            if (layerMap[staff] && layerMap[staff][day]) {{
                var showId = layerMap[staff][day];
                var layerObj = window[showId];
                if (layerObj) {{
                    map.addLayer(layerObj);
                    // Fit bounds if layer has bounds (FeatureGroup usually does)
                    if (layerObj.getBounds && layerObj.getBounds().isValid()) {{
                        map.fitBounds(layerObj.getBounds(), {{padding: [50, 50]}});
                    }}
                }}
            }}
        }}
        
        // init
        setTimeout(updateMap, 1000); // Auto-load first selection
    </script>
    """
    
    m.get_root().html.add_child(folium.Element(sidebar_html))
    
    print(f"Saving to {MAP_FILE}...")
    m.save(MAP_FILE)
    print("Done.")

if __name__ == "__main__":
    create_map()
