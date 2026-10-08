import unittest

from routing.services.incremental_scheduler import insert_candidates


class FakeCostProvider:
    def warm_costs(self, coords):
        return True

    def get_cost(self, origin, dest, persist_fallback=True):
        distance = abs(origin[0] - dest[0]) + abs(origin[1] - dest[1])
        return {"duration": distance * 10, "distance": distance, "source": "test", "used_fallback": False}

    def route_cost(self, coords):
        legs = [self.get_cost(coords[index], coords[index + 1]) for index in range(len(coords) - 1)]
        return {
            "duration": sum(item["duration"] for item in legs),
            "distance": sum(item["distance"] for item in legs),
            "source": "test",
            "used_fallback": False,
        }


def route(route_id, driver, day, stop_lon):
    return {
        "route_id": route_id,
        "driver": driver,
        "day": day,
        "depot": {"lat": 0, "lon": 0},
        "metrics": {"service_min": 10, "drive_min": stop_lon * 10, "dist_km": stop_lon, "total_min": 10 + stop_lon * 10},
        "stops": [{"seq": 1, "node_id": f"old-{day}", "address": f"old-{day}", "lat": 0, "lon": stop_lon, "county": "A", "service_min": 10}],
    }


class IncrementalSchedulerTests(unittest.TestCase):
    def test_preserves_days_before_start_and_inserts_into_future_route(self):
        payload = {"meta": {}, "routes": [route("r1", "P01", 1, 1), route("r2", "P02", 2, 2)]}
        original_day_one = payload["routes"][0].copy()
        candidate = {"candidate_key": "point:9", "node_id": "SP_9", "address": "new", "lat": 0, "lon": 1.5, "county": "A", "service_min": 10}

        updated, inserted, unassigned = insert_candidates(payload, [candidate], start_day=2, max_minutes=540, provider=FakeCostProvider())

        self.assertEqual([], unassigned)
        self.assertEqual(1, len(inserted))
        self.assertEqual("r2", inserted[0]["route_id"])
        self.assertEqual(original_day_one, updated["routes"][0])
        self.assertEqual(2, updated["routes"][1]["stop_count"])

    def test_does_not_force_point_into_overtime_route(self):
        payload = {"meta": {}, "routes": [route("r1", "P01", 2, 20)]}
        candidate = {"candidate_key": "point:9", "node_id": "SP_9", "address": "new", "lat": 0, "lon": 30, "county": "A", "service_min": 400}

        updated, inserted, unassigned = insert_candidates(payload, [candidate], start_day=2, max_minutes=100, provider=FakeCostProvider())

        self.assertEqual([], inserted)
        self.assertEqual(1, len(unassigned))
        self.assertEqual(1, len(updated["routes"][0]["stops"]))

    def test_inserts_after_locked_completed_prefix(self):
        payload = {"meta": {}, "routes": [route("r1", "P01", 2, 2)]}
        payload["routes"][0]["stops"].append({"seq": 2, "node_id": "locked-2", "address": "locked", "lat": 0, "lon": 3, "county": "A", "service_min": 10})
        candidate = {"candidate_key": "point:9", "node_id": "SP_9", "address": "new", "lat": 0, "lon": 0.5, "county": "A", "service_min": 10}

        updated, inserted, _ = insert_candidates(payload, [candidate], start_day=2, max_minutes=540, provider=FakeCostProvider(), locked_prefixes={"r1": 2})

        self.assertEqual(3, inserted[0]["position"])
        self.assertEqual(["old-2", "locked-2", "SP_9"], [stop["node_id"] for stop in updated["routes"][0]["stops"]])

    def test_normal_variant_does_not_cross_counties(self):
        payload = {"meta": {"variant": "normal"}, "routes": [route("r1", "P01", 2, 2)]}
        payload["routes"][0]["counties"] = ["A"]
        candidate = {"candidate_key": "point:9", "node_id": "SP_9", "address": "new", "lat": 0, "lon": 1, "county": "B", "service_min": 10}

        _, inserted, unassigned = insert_candidates(payload, [candidate], start_day=2, max_minutes=540, provider=FakeCostProvider())

        self.assertEqual([], inserted)
        self.assertEqual(1, len(unassigned))

    def test_skipped_point_is_only_inserted_after_original_day(self):
        payload = {"meta": {"variant": "normal"}, "routes": [route("r2", "P01", 2, 2), route("r3", "P01", 3, 3)]}
        candidate = {"candidate_key": "skip:1", "source": "skipped", "original_day": 2, "node_id": "SP_9", "address": "new", "lat": 0, "lon": 1, "county": "A", "service_min": 10}

        _, inserted, _ = insert_candidates(payload, [candidate], start_day=1, max_minutes=540, provider=FakeCostProvider())

        self.assertEqual(3, inserted[0]["day"])


if __name__ == "__main__":
    unittest.main()
