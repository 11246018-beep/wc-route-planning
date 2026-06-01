
import requests
import json

# Test OSRM API
# Pingzhen Depot to Wugu Depot
url = "http://router.project-osrm.org/route/v1/driving/121.226872,24.90703;121.44169,25.07154?overview=full&steps=true"

try:
    response = requests.get(url, timeout=10)
    if response.status_code == 200:
        data = response.json()
        print("OSRM Success!")
        print("Duration:", data['routes'][0]['duration'])
        print("Distance:", data['routes'][0]['distance'])
    else:
        print(f"OSRM Failed: {response.status_code}")
except Exception as e:
    print(f"OSRM Error: {e}")
