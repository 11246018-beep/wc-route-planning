import pandas as pd
import folium
import os

# File paths
input_file = 'maintenance_data_zoned.csv'
output_file = 'try1.html'

def get_color(zone_id):
    # distinct colors for 14 zones
    colors = [
        'red', 'blue', 'green', 'purple', 'orange', 'darkred',
        'lightred', 'beige', 'darkblue', 'darkgreen', 'cadetblue',
        'darkpurple', 'white', 'pink', 'lightblue', 'lightgreen',
        'gray', 'black', 'lightgray'
    ]
    return colors[int(zone_id) % len(colors)]

DEPOTS = {
    '台北分公司': {
        'Lat': 25.07154, 
        'Lon': 121.44169, 
        'Color': 'red',
        'Address': '新北市五股區成泰路一段98巷16號之七號'
    },
    '總公司': {
        'Lat': 24.90703, 
        'Lon': 121.226872, 
        'Color': 'blue',
        'Address': '桃園市平鎮區東勢里30鄰東豐路735巷123弄85之1號'
    }
}

def generate_map():
    print(f"Reading {input_file}...")
    try:
        df = pd.read_csv(input_file, encoding='utf-8-sig')
    except FileNotFoundError:
        print(f"Error: {input_file} not found. Please run partition_zones.py first.")
        return
    except Exception as e:
        print(f"Error reading CSV: {e}")
        return

    if 'zone_id' not in df.columns:
        print("Error: 'zone_id' column missing. Please run partition_zones.py.")
        return

    # Check for latitude and longitude columns
    if '緯度' not in df.columns or '經度' not in df.columns:
        print("Error: '緯度' (Latitude) or '經度' (Longitude) columns missing.")
        return

    # Filter out rows with missing coordinates
    df_coords = df.dropna(subset=['緯度', '經度'])
    
    if df_coords.empty:
        print("No valid coordinates found to plot.")
        return

    # Calculate center of the map
    center_lat = df_coords['緯度'].mean()
    center_lon = df_coords['經度'].mean()

    print(f"Creating map centered at {center_lat}, {center_lon}...")
    m = folium.Map(location=[center_lat, center_lon], zoom_start=13)

    # Add Depots
    for name, info in DEPOTS.items():
        folium.Marker(
            location=[info['Lat'], info['Lon']],
            popup=folium.Popup(f"<b>{name}</b><br>{info['Address']}", max_width=300),
            tooltip=name,
            icon=folium.Icon(color=info['Color'], icon='home', prefix='glyphicon')
        ).add_to(m)

    # Add markers
    for index, row in df_coords.iterrows():
        lat = row['緯度']
        lon = row['經度']
        location_name = row.get('服務地點', 'Unknown')
        customer_name = row.get('客戶名稱', 'Unknown')
        maintenance_time = row.get('維護時間', 0)
        travel_distance = row.get('行車距離', 0)
        count = row.get('count', 0)
        zone_id = row.get('zone_id', -1)
        
        # Color based on zone
        color = get_color(zone_id)

        # Create popup content
        popup_html = f"""
        <b>Zone ID:</b> {zone_id}<br>
        <b>服務地點:</b> {location_name}<br>
        <b>客戶名稱:</b> {customer_name}<br>
        <b>維護時間 (Sum):</b> {maintenance_time}<br>
        <b>行車距離 (Avg):</b> {travel_distance:.2f}<br>
        <b>次數 (Count):</b> {count}
        """
        
        folium.CircleMarker(
            location=[lat, lon],
            radius=6,
            popup=folium.Popup(popup_html, max_width=300),
            tooltip=f"Zone {zone_id}: {location_name}",
            color=color,
            fill=True,
            fill_color=color,
            fill_opacity=0.7
        ).add_to(m)

    # Save to HTML
    print(f"Saving map to {output_file}...")
    m.save(output_file)
    print("Done!")

if __name__ == "__main__":
    generate_map()
