
import json
import os
import random

INPUT_JSON = r'c:/Users/owner/Desktop/專題/test4.0/phase1/schedule_output.json'
OUTPUT_HTML = r'c:/Users/owner/Desktop/專題/test4.0/phase1/maintenance_route_map.html'

def generate_map():
    if not os.path.exists(INPUT_JSON):
        print("Schedule output not found.")
        return

    with open(INPUT_JSON, 'r', encoding='utf-8') as f:
        data = json.load(f)
        
    # Process Data for Map
    # We need:
    # 1. Routes: List of {driver, day, geometry, color, stats}
    # 2. Points: List of {lat, lon, popup, icon}
    
    routes = {} # key: (driver, day) -> {coords: [], info: ...}
    points = []
    
    # Colors for 14 drivers
    colors = [
        '#e6194b', '#3cb44b', '#ffe119', '#4363d8', '#f58231', 
        '#911eb4', '#46f0f0', '#f032e6', '#bcf60c', '#fabebe', 
        '#008080', '#e6beff', '#9a6324', '#fffac8'
    ]
    
    for row in data:
        did = row['DriverID']
        day = row['Day']
        
        # Route
        key = (did, day)
        if key not in routes:
            # Parse geometry
            geom = row.get('Route_Geometry')
            if geom:
                routes[key] = {
                    'driver': did,
                    'day': day,
                    'geometry': geom,
                    'color': colors[did % 14],
                    'dist': row['Distance_Meters'],
                    'time': row['Total_Daily_Time']
                }
        
        # Point
        points.append({
            'lat': row['Lat'],
            'lon': row['Lon'],
            'driver': did,
            'day': day,
            'seq': row['Sequence'],
            'id': row['ClientID'],
            'addr': row['Address'],
            'type': 'W2' if row['Week2'] else 'W1'
        })

    # Convert routes to list
    route_list = list(routes.values())
    
    # HTML Template
    html_content = f"""
<!DOCTYPE html>
<html>
<head>
    <title>Maintenance Route Map</title>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <link rel="stylesheet" href="https://unpkg.com/leaflet@1.7.1/dist/leaflet.css" />
    <script src="https://unpkg.com/leaflet@1.7.1/dist/leaflet.js"></script>
    <style>
        body {{ margin: 0; padding: 0; }}
        #map {{ width: 100%; height: 100vh; }}
        #controls {{
            position: absolute; top: 10px; right: 10px; z-index: 1000;
            background: white; padding: 10px; border-radius: 5px;
            box-shadow: 0 0 5px rgba(0,0,0,0.3);
        }}
        select {{ margin: 5px; padding: 5px; }}
        .info-panel {{ margin-top: 10px; font-size: 12px; }}
    </style>
</head>
<body>
    <div id="controls">
        <h3>Route Filter</h3>
        <label>Driver:</label>
        <select id="driverSelect" onchange="updateMap()">
            <option value="all">All Drivers</option>
            {''.join([f'<option value="{i}">Driver {i}</option>' for i in range(14)])}
        </select>
        <br>
        <label>Day:</label>
        <select id="daySelect" onchange="updateMap()">
            <option value="all">All Days</option>
            {''.join([f'<option value="{i}">Day {i}</option>' for i in range(1, 7)])}
        </select>
        <div id="stats" class="info-panel"></div>
    </div>
    <div id="map"></div>

    <script>
        var map = L.map('map').setView([25.0, 121.3], 10);
        L.tileLayer('https://{{s}}.tile.openstreetmap.org/{{z}}/{{x}}/{{y}}.png', {{
            maxZoom: 19,
            attribution: '© OpenStreetMap inputs'
        }}).addTo(map);

        var routeData = {json.dumps(route_list)};
        var pointData = {json.dumps(points)};
        
        var routeLayer = L.layerGroup().addTo(map);
        var pointLayer = L.layerGroup().addTo(map);

        function updateMap() {{
            routeLayer.clearLayers();
            pointLayer.clearLayers();
            
            var selDriver = document.getElementById('driverSelect').value;
            var selDay = document.getElementById('daySelect').value;
            
            var totalTime = 0;
            var totalDist = 0;
            
            // Draw Routes
            routeData.forEach(function(r) {{
                if ((selDriver === 'all' || r.driver == selDriver) && 
                    (selDay === 'all' || r.day == selDay)) {{
                    
                    var style = {{ color: r.color, weight: 4, opacity: 0.7 }};
                    if (selDay !== 'all') {{ style.weight = 6; style.opacity = 0.9; }}
                    
                    L.geoJSON(r.geometry, {{ style: style }})
                     .bindPopup('Driver: ' + r.driver + '<br>Day: ' + r.day + '<br>Time: ' + r.time.toFixed(1) + ' min')
                     .addTo(routeLayer);
                     
                    totalTime += r.time;
                    totalDist += r.dist;
                }}
            }});
            
            // Draw Points (Only if specific filter to avoid clutter?)
            // Or show all if zoomed in?
            // Let's show points if Driver is selected or Day is selected, or both.
            // If "All" and "All", maybe too many (3000)?
            // Yes. Only show points if filter is active.
            
            if (selDriver !== 'all' || selDay !== 'all') {{
                pointData.forEach(function(p) {{
                    if ((selDriver === 'all' || p.driver == selDriver) && 
                        (selDay === 'all' || p.day == selDay)) {{
                        
                        var markerColor = (p.type === 'W2') ? 'red' : 'blue';
                        
                        L.circleMarker([p.lat, p.lon], {{
                            radius: 5,
                            fillColor: markerColor,
                            color: '#000',
                            weight: 1,
                            opacity: 1,
                            fillOpacity: 0.8
                        }}).bindPopup(
                            '<b>' + p.id + '</b><br>' + 
                            p.addr + '<br>' + 
                            'Seq: ' + p.seq + '<br>' +
                            'Type: ' + p.type
                        ).addTo(pointLayer);
                    }}
                }});
            }}
            
            document.getElementById('stats').innerHTML = 
                'Total Time: ' + (totalTime).toFixed(1) + ' min<br>' +
                'Total Dist: ' + (totalDist/1000).toFixed(1) + ' km';
        }}
        
        updateMap();
    </script>
</body>
</html>
    """
    
    with open(OUTPUT_HTML, 'w', encoding='utf-8') as f:
        f.write(html_content)
        
    print(f"Map generated at {OUTPUT_HTML}")

if __name__ == "__main__":
    generate_map()
