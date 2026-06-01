
import requests
import time
import json
import math

# Configuration
PZ_DEPOT = {'lat': 24.90703, 'lon': 121.226872, 'name': 'Pingzhen Depot', 'id': 'PZ_DEPOT'}
WG_DEPOT = {'lat': 25.07154, 'lon': 121.44169, 'name': 'Wugu Depot', 'id': 'WG_DEPOT'}

# 12 drivers at PZ, 2 at WG
DRIVERS_PZ = [f'Driver_PZ_{i+1:02d}' for i in range(12)]
DRIVERS_WG = [f'Driver_WG_{i+1:02d}' for i in range(2)]

ALL_DRIVERS = DRIVERS_PZ + DRIVERS_WG

MAX_DAILY_WORK_MIN = 540
MAX_WEEKLY_WORK_MIN = 3240

def get_osrm_route(coords):
    """
    Get route from OSRM.
    coords: list of (lon, lat) tuples
    Returns: dictionary with distance (meters), duration (seconds), geometry (polyline/geojson)
    """
    if len(coords) < 2:
        return {'distance': 0, 'duration': 0, 'geometry': None}
    
    # Format coordinates for OSRM: lon,lat;lon,lat
    coord_str = ";".join([f"{lon},{lat}" for lon, lat in coords])
    
    url = f"http://router.project-osrm.org/route/v1/driving/{coord_str}?overview=full&steps=true&geometries=geojson"
    
    try:
        response = requests.get(url, timeout=10)
        if response.status_code == 200:
            data = response.json()
            if data['code'] == 'Ok':
                route = data['routes'][0]
                return {
                    'distance': route['distance'],      # meters
                    'duration': route['duration'],      # seconds
                    'geometry': route['geometry'],      # GeoJSON
                    'legs': route['legs']               # Detailed legs
                }
    except Exception as e:
        print(f"OSRM Request Error: {e}")
        time.sleep(1) # Simple retry wait
    
    return None

def calculate_group_centroid(df):
    """Calculate centroid of a group of points"""
    if df.empty:
        return None
    return df[['經度', '緯度']].mean().values

def haversine_distance(coord1, coord2):
    """
    Calculate the great circle distance between two points 
    on the earth (specified in decimal degrees)
    coord: (lat, lon)
    """
    lat1, lon1 = coord1
    lat2, lon2 = coord2
    
    R = 6371  # Radius of earth in km
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat/2) * math.sin(dlat/2) + \
        math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * \
        math.sin(dlon/2) * math.sin(dlon/2)
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1-a))
    d = R * c # Distance in km
    return d

def get_county_from_zip(zipcode):
    """
    Map 3-digit zipcode to County/City Name.
    Based on Taiwan Zipcode rules (simplified for relevant areas).
    """
    try:
        z = int(zipcode)
    except:
        return "Unknown"
        
    if 100 <= z <= 116: return "Taipei City"
    if 200 <= z <= 208: return "Keelung City"
    if 220 <= z <= 253: return "New Taipei City"
    if 300 == z: return "Hsinchu City"
    if 302 <= z <= 315: return "Hsinchu County" # 308 is Baoshan, etc.
    if 320 <= z <= 338: return "Taoyuan City"
    if 350 <= z <= 369: return "Miaoli County"
    
    return "Others"

def is_cross_county(zip1, zip2):
    c1 = get_county_from_zip(zip1)
    c2 = get_county_from_zip(zip2)
    return c1 != c2
