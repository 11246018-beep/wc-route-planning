import pandas as pd
import folium
import re

print("Loading data...")
file_path = '/Users/CherryWu/Desktop/phase1/maintenance_data_v2.xlsx'
df = pd.read_excel(file_path)

addr_col = None
for col in df.columns:
    if '服務地點' in col:
        addr_col = col
        break

if not addr_col:
    print('Error: Columns are', df.columns)
    exit(1)

# Drop rows where '緯度' or '經度' or Address might be NaN
df_valid = df.dropna(subset=[addr_col, '緯度', '經度']).copy()

print(f"Loaded {len(df_valid)} valid rows with coordinates.")

pattern = r'(基隆市|台北市|臺北市|新北市|桃園市|桃園縣|新竹市|新竹縣|苗栗縣|台中市|臺中市|彰化縣|南投縣|雲林縣|嘉義市|嘉義縣|台南市|臺南市|高雄市|屏東縣|宜蘭縣|花蓮縣|台東縣|臺東縣|澎湖縣|金門縣|連江縣)'

def parse_county(addr):
    addr = str(addr).strip()
    match = re.search(pattern, addr)
    if match:
        return match.group(1).replace('臺', '台')
    if len(addr) >= 3 and addr[2] in ['縣', '市']:
        return addr[:3].replace('臺', '台')
    return 'Unknown'

df_valid['County'] = df_valid[addr_col].apply(parse_county)

# Colors mapping
unique_counties = df_valid['County'].unique().tolist()
colors = ['red', 'blue', 'green', 'purple', 'orange', 'darkred', 'cadetblue', 'darkgreen', 'darkblue', 'pink', 'lightgreen', 'black', 'gray']
color_map = {county: colors[i % len(colors)] for i, county in enumerate(unique_counties)}

print("Generating map...")
# Center map around northern Taiwan
m = folium.Map(location=[24.9, 121.2], zoom_start=9)

# Grouping marker additions to map for speed
for idx, row in df_valid.iterrows():
    county = row['County']
    color = color_map.get(county, 'gray')
    popup_text = f"<b>縣市:</b> {county}<br><b>地點:</b> {row[addr_col]}"
    folium.CircleMarker(
        location=[row['緯度'], row['經度']],
        radius=6,
        popup=folium.Popup(popup_text, max_width=300),
        color=color,
        fill=True,
        fill_color=color,
        fill_opacity=0.7,
        tooltip=county
    ).add_to(m)

# Add Warehouse markers
warehouses = [
    {"name": "平鎮倉", "lat": 24.9046004, "lon": 121.2265770, "addr": "桃園市平鎮區東勢里30鄰東豐路735巷123弄85之1號"},
    {"name": "五股倉", "lat": 25.0783391, "lon": 121.4357565, "addr": "新北市五股區成泰路一段98巷16號之七號"}
]

for wh in warehouses:
    folium.Marker(
        location=[wh['lat'], wh['lon']],
        popup=folium.Popup(f"<b>{wh['name']}</b><br>地點: {wh['addr']}", max_width=300),
        tooltip=wh['name'],
        icon=folium.Icon(color='black', icon='home', prefix='fa')
    ).add_to(m)

# Add Legend
legend_html = '''
{% macro html(this, kwargs) %}
<div style="
    position: fixed; 
    bottom: 50px; left: 50px; width: 140px; height: auto; 
    border:2px solid grey; z-index:9999; font-size:14px;
    background-color:white; padding: 10px;
    ">
    <h4 style="margin-top: 0;">縣市圖例</h4>
'''
for county, color in color_map.items():
    legend_html += f'    <p style="margin: 0;"><i style="background:{color};width:12px;height:12px;float:left;margin-right:8px;margin-top:4px;"></i>{county}</p>\n'
legend_html += '''</div>
{% endmacro %}
'''

macro = folium.MacroElement()
macro._template = folium.branca.element.Template(legend_html)
m.get_root().add_child(macro)

output_path = '/Users/CherryWu/Desktop/phase1/maintenance_locations_map.html'
m.save(output_path)
print(f"Map successfully saved to {output_path}")
