import requests
import time
from typing import List, Tuple, Optional

# OSRM Public Demo Server
# NOTE: This has usage limits. For production, host your own OSRM instance.
OSRM_BASE_URL = "http://router.project-osrm.org"

def get_travel_times(
    sources: List[Tuple[float, float]], 
    destination: Tuple[float, float],
    chunk_size: int = 50
) -> List[Optional[float]]:
    """
    Get driving travel times (in seconds) from multiple source coordinates to a single destination.
    
    Args:
        sources: List of (lat, lon) for source locations.
        destination: (lat, lon) for the destination (cluster center).
        chunk_size: Number of sources to process in one request (to avoid URL too long).
        
    Returns:
        List of travel times in seconds. Returns None for failed requests or unreachable paths.
    """
    results = []
    dest_lat, dest_lon = destination
    
    # Process in chunks
    for i in range(0, len(sources), chunk_size):
        chunk = sources[i : i + chunk_size]
        
        # OSRM expects coordinates as "lon,lat"
        # We construct the coordinate string: src1;src2;...;center
        coords_list = [f"{lon},{lat}" for lat, lon in chunk]
        coords_list.append(f"{dest_lon},{dest_lat}") # Add destination at the end
        
        coords_str = ";".join(coords_list)
        
        # Sources indices: 0 to len(chunk)-1
        src_indices = ";".join(map(str, range(len(chunk))))
        # Destination index: len(chunk) (the last one)
        dest_index = str(len(chunk))
        
        url = f"{OSRM_BASE_URL}/table/v1/driving/{coords_str}"
        params = {
            "sources": src_indices,
            "destinations": dest_index,
            # "annotations": "duration" # Default is duration
        }
        
        try:
            # Add a small delay to be polite to the public server
            if i > 0:
                time.sleep(0.1)
                
            response = requests.get(url, params=params, timeout=10)
            data = response.json()
            
            if response.status_code == 200 and data.get("code") == "Ok":
                durations = data.get("durations", [])
                # durations is a list of lists (sources x destinations)
                # Since we have 1 destination, it's [[time], [time], ...] or similar depending on row/col major
                # OSRM Table: "durations array of arrays... durations[i][j] gives time from i-th source to j-th destination"
                # Here sources are rows, destinations are cols.
                
                chunk_times = []
                for row in durations:
                    if row and row[0] is not None:
                        chunk_times.append(row[0])
                    else:
                        chunk_times.append(None) # Unreachable or error
                
                results.extend(chunk_times)
            else:
                print(f"[OSRM Error] Status: {response.status_code}, Msg: {data.get('message', 'Unknown')}")
                results.extend([None] * len(chunk))
                
        except Exception as e:
            print(f"[OSRM Exception] {e}")
            results.extend([None] * len(chunk))
            
    return results

if __name__ == "__main__":
    # Test cases
    print("Testing OSRM Client...")
    
    # Wugu Depot (25.07154, 121.44169)
    center = (25.07154, 121.44169)
    
    # Some nearby points
    # 1. Nearby: New Taipei Industrial Park (25.06, 121.45)
    # 2. Far: Taipei 101 (25.033, 121.565)
    test_sources = [
        (25.06000, 121.45000), 
        (25.03300, 121.56500)
    ]
    
    times = get_travel_times(test_sources, center)
    print(f"Sources: {test_sources}")
    print(f"Destination: {center}")
    print(f"Travel Times (seconds): {times}")
    print(f"Travel Times (minutes): {[t/60 if t else None for t in times]}")
