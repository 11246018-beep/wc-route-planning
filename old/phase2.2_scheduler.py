import pandas as pd
import numpy as np
import folium
import os
import json

def main():
    project_dir = os.getcwd()
    # 請確保資料夾內有這兩個檔案
    input_csv = os.path.join(project_dir, 'output', 'processed_nodes_phase1.csv')
    summary_csv = os.path.join(project_dir, 'output', 'Daily_Route_Summary.xlsx - Sheet1.csv')
    output_path = os.path.join(project_dir, 'output', 'Weekly_Routing_Map.html')

    if not os.path.exists(input_csv):
        print(f"錯誤：找不到 {input_csv}")
        return

    # 1. 準備節點數據
    df = pd.read_csv(input_csv)
    if 'Lat' in df.columns: df = df.rename(columns={'Lat': 'lat', 'Lon': 'lon'})
    df['address'] = df.get('Address', df.get('address', '未知地址'))
    
    if 'driver' not in df.columns:
        df['driver'] = np.where(df.index % 2 == 0, '新路線-司機A', '舊路線-司機B')
    if 'day' not in df.columns:
        df['day'] = (df.index // 20) % 5 + 1

    # 建立新舊分類 (範例：假設 P 開頭為新，其餘為舊，或根據您的欄位定義)
    df['route_type'] = df['driver'].apply(lambda x: '新路線' if str(x).startswith('P') else '舊路線')

    # 2. 準備摘要數據 (里程、工時)
    summary_data = {}
    if os.path.exists(summary_csv):
        sdf = pd.read_csv(summary_csv)
        for _, row in sdf.iterrows():
            key = f"{row['司機']}_{row['天數']}"
            summary_data[key] = {
                'dist': round(row['總里程_km'], 1),
                'time': int(row['總工時_分']),
                'stops': int(row['總站數'])
            }

    # 轉換 JSON
    nodes_json = df.to_json(orient='records', force_ascii=False)
    summary_json = json.dumps(summary_data, ensure_ascii=False)

    # 3. 建立地圖
    m = folium.Map(location=[25.04, 121.50], zoom_start=11, tiles='cartodbpositron')

    # 4. 注入專業 CSS
    custom_css = """
    <style>
        body, html { height: 100%; margin: 0; font-family: "Microsoft JhengHei", sans-serif; }
        #wrap { display: flex; height: 100vh; width: 100vw; }
        #panel { width: 380px; height: 100%; overflow-y: auto; background: #fff; border-right: 1px solid #ddd; padding: 15px; box-sizing: border-box; }
        #map_container { flex: 1; height: 100%; position: relative; }
        .folium-map { width: 100% !important; height: 100% !important; }
        .sec { background: #fcfcfc; padding: 12px; border: 1px solid #eee; border-radius: 10px; margin-bottom: 15px; }
        .sec h5 { margin: 0 0 10px 0; color: #2e86de; border-left: 4px solid #2e86de; padding-left: 8px; }
        .stat-box { display: flex; justify-content: space-around; background: #eef5ff; padding: 8px; border-radius: 8px; margin: 10px 0; font-size: 12px; font-weight: bold; }
        .row-ctrl { display: flex; align-items: center; gap: 8px; margin-bottom: 8px; font-size: 13px; }
        .row-ctrl label { width: 80px; flex-shrink: 0; }
        select, button { width: 100%; padding: 8px; border-radius: 6px; border: 1px solid #ccc; font-size: 13px; }
        #btnDraw { background: #2e86de; color: white; border: none; cursor: pointer; font-weight: bold; margin-top: 10px; }
        .node-card { border-bottom: 1px solid #eee; padding: 10px; cursor: pointer; font-size: 12px; }
        .badge { padding: 2px 6px; border-radius: 4px; font-size: 10px; color: white; margin-left: 4px; }
        .bg-orange { background: #e67e22; } .bg-blue { background: #3498db; }
    </style>
    """

    # 5. 注入 HTML (修正：移除分頁，改用選單過濾)
    sidebar_html = """
    <div id="wrap">
        <div id="panel">
            <h3 style="margin-top:0;">路徑規劃系統 v4</h3>
            
            <div class="sec">
                <h5>1. 快速篩選</h5>
                <select id="geoFilter" onchange="renderList()">
                    <option value="all">顯示所有地點</option>
                    <option value="cross">僅顯示：跨縣市</option>
                    <option value="local">僅顯示：非跨縣市</option>
                </select>
            </div>

            <div class="sec">
                <h5>2. 路線比較 (新 vs 舊)</h5>
                <div style="font-weight:bold; color:#d35400; margin-bottom:5px;">[新路線配置]</div>
                <div class="row-ctrl"><label>新司機</label><select id="newDrv" onchange="updateDays('new')"></select></div>
                <div class="row-ctrl"><label>新日期</label><select id="newDay" onchange="showStats('new')"></select></div>
                <div id="newStats" class="stat-box">里程: -- | 工時: -- | 站數: --</div>
                
                <hr>
                
                <div style="font-weight:bold; color:#7f8c8d; margin-bottom:5px;">[舊路線配置]</div>
                <div class="row-ctrl"><label>舊司機</label><select id="oldDrv" onchange="updateDays('old')"></select></div>
                <div class="row-ctrl"><label>舊日期</label><select id="oldDay" onchange="showStats('old')"></select></div>
                <div id="oldStats" class="stat-box">里程: -- | 工時: -- | 站數: --</div>
                
                <button id="btnDraw" onclick="drawRoutes()">同步繪製比較圖</button>
            </div>

            <h5>3. 節點清單 (隨選單連動)</h5>
            <div id="pointList"></div>
        </div>
        <div id="map_container"></div>
    </div>
    """

    # 6. 注入 JavaScript (核心修正：資料連動與統計顯示)
    custom_js = f"""
    <script>
        var rawData = {nodes_json};
        var statsData = {summary_json};
        var markerLayer = L.layerGroup();
        var lineLayer = L.layerGroup();

        function init() {{
            var mapDiv = document.querySelector('.folium-map');
            document.getElementById('map_container').appendChild(mapDiv);
            markerLayer.addTo(map); lineLayer.addTo(map);

            // 分離新舊司機
            var newDrivers = [...new Set(rawData.filter(d => d.route_type === '新路線' || String(d.driver).startsWith('新')).map(d => d.driver))];
            var oldDrivers = [...new Set(rawData.filter(d => d.route_type === '舊路線' || String(d.driver).startsWith('舊')).map(d => d.driver))];

            if (newDrivers.length === 0) newDrivers = [...new Set(rawData.map(d => d.driver))];
            if (oldDrivers.length === 0) oldDrivers = [...new Set(rawData.map(d => d.driver))];

            populate('newDrv', newDrivers);
            populate('oldDrv', oldDrivers);

            updateDays('new'); updateDays('old');
            renderList();
        }}

        function populate(id, list) {{
            var s = document.getElementById(id);
            list.forEach(i => {{ var o = document.createElement('option'); o.value=i; o.text=i; s.appendChild(o); }});
        }}

        function updateDays(type) {{
            var drv = document.getElementById(type + 'Drv').value;
            var daySel = document.getElementById(type + 'Day');
            daySel.innerHTML = "";
            var days = [...new Set(rawData.filter(r => String(r.driver) === String(drv)).map(r => r.day))].sort((a,b)=>a-b);
            days.forEach(d => {{ var o = document.createElement('option'); o.value=d; o.text="第 "+d+" 天"; daySel.appendChild(o); }});
            showStats(type);
            renderList(); // 同步更新下方清單
        }}

        function showStats(type) {{
            var drv = document.getElementById(type + 'Drv').value;
            var day = document.getElementById(type + 'Day').value;
            var key = drv + "_" + day;
            var info = statsData[key] || {{dist:'--', time:'--', stops:'--'}};
            document.getElementById(type + 'Stats').innerHTML = `里程: ${{info.dist}}km | 工時: ${{info.time}}m | 站數: ${{info.stops}}`;
        }}

        function drawRoutes() {{
            markerLayer.clearLayers(); lineLayer.clearLayers();
            var configs = [
                {{ type: 'new', color: '#2e86de', dash: null }},
                {{ type: 'old', color: '#95a5a6', dash: '10, 10' }}
            ];

            configs.forEach(c => {{
                var drv = document.getElementById(c.type + 'Drv').value;
                var day = document.getElementById(c.type + 'Day').value;
                var pts = rawData.filter(r => r.driver === drv && String(r.day) === String(day));
                
                if(pts.length > 0) {{
                    var coords = pts.map(p => [p.lat, p.lon]);
                    L.polyline(coords, {{color: c.color, weight: 5, dashArray: c.dash}}).addTo(lineLayer);
                    pts.forEach(p => {{
                        L.marker([p.lat, p.lon], {{icon: L.AwesomeMarkers.icon({{icon:'info-sign', markerColor: c.type==='new'?'blue':'orange', prefix:'glyphicon'}})}})
                        .bindPopup(p.address).addTo(markerLayer);
                    }});
                    map.fitBounds(lineLayer.getBounds());
                }}
            }});
        }}

        function renderList() {{
            var drv = document.getElementById('newDrv').value; // 以新路線選擇的司機作為清單顯示基準
            var day = document.getElementById('newDay').value;
            var geo = document.getElementById('geoFilter').value;
            
            var filtered = rawData.filter(d => String(d.driver) === String(drv) && String(d.day) === String(day));
            if(geo === 'cross') filtered = filtered.filter(d => d.is_cross_county);
            if(geo === 'local') filtered = filtered.filter(d => !d.is_cross_county);

            var html = "";
            filtered.forEach(d => {{
                html += `<div class='node-card' onclick='map.panTo([${{d.lat}}, ${{d.lon}}])'>
                    <b>${{d.driver}}</b> ${{d.is_cross_county ? '<span class="badge bg-orange">跨縣市</span>' : '<span class="badge bg-blue">本區域</span>'}}<br>
                    <small>${{d.address}}</small>
                </div>`;
            }});
            document.getElementById('pointList').innerHTML = html || "<div style='padding:10px;'>此篩選條件下無資料</div>";
        }}

        setTimeout(init, 1000);
    </script>
    """

    m.get_root().header.add_child(folium.Element(custom_css))
    m.get_root().html.add_child(folium.Element(sidebar_html))
    m.get_root().html.add_child(folium.Element(custom_js))

    m.save(output_path)
    print(f"成功產出地圖！數據已連動，請開啟：{output_path}")

if __name__ == "__main__":
    main()