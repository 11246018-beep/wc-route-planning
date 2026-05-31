import requests
import time

def test_osrm():
    coords = [
        (121.22683, 24.90679, 121.23, 24.91), # Short
        (121.22683, 24.90679, 121.44141, 25.07055), # Long (PZ->WG)
        (121.22683, 24.90679, 121.22683, 24.90679) # Zero
    ]
    
    url_base = 'http://router.project-osrm.org/route/v1/driving'
    
    print("Testing OSRM Latency...")
    for lat1, lon1, lat2, lon2 in coords:
        start = time.time()
        try:
            url = f"{url_base}/{lat1},{lon1};{lat2},{lon2}?overview=false"
            resp = requests.get(url, timeout=2)
            dur = time.time() - start
            print(f"Call: {lat1},{lon1} -> {lat2},{lon2} | Status: {resp.status_code} | Time: {dur:.4f}s")
        except Exception as e:
            print(f"Call Failed: {e}")

if __name__ == "__main__":
    test_osrm()
