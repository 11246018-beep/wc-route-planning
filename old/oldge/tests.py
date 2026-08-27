from django.test import SimpleTestCase, TestCase
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

from .services.incremental_scheduler import low_workload_routes, move_route_stops, write_payload_atomic
from .ai.planner import TaskPlanner, validate_plan
from .ai.tool_registry import get_tool, list_tools
from .ai.orchestrator import is_conversation_question, is_system_related_question, out_of_scope_response

# Create your tests here.


class FakeCostProvider:
    def warm_costs(self, coords):
        return None

    def route_cost(self, coords):
        distance = sum(abs(a[0] - b[0]) + abs(a[1] - b[1]) for a, b in zip(coords, coords[1:]))
        return {"duration": distance * 10, "distance": distance, "used_fallback": False}

    def get_cost(self, start, end):
        distance = abs(start[0] - end[0]) + abs(start[1] - end[1])
        return {"duration": distance * 10, "distance": distance}

    def route_geometry(self, coords):
        cost = self.route_cost(coords)
        return {"coordinates": [[lat, lon] for lat, lon in coords],
                "duration": cost["duration"], "distance": cost["distance"],
                "used_fallback": False, "source": "fake OSRM"}


class LowWorkloadMergeTests(SimpleTestCase):
    def payload(self):
        depot = {"code": "Wugu", "lat": 0, "lon": 0}
        stop_a = {"node_id": "A", "county": "桃園市", "address": "A", "lat": 1, "lon": 0, "service_min": 10}
        stop_b = {"node_id": "B", "county": "桃園市", "address": "B", "lat": 2, "lon": 0, "service_min": 20}
        return {"ok": True, "meta": {"variant": "normal"}, "routes": [
            {"route_id": "SHORT", "driver": "D1", "day": 1, "depot": depot, "counties": ["桃園市"],
             "metrics": {"service_min": 10, "drive_min": 10, "total_min": 20}, "stops": [stop_a]},
            {"route_id": "TARGET", "driver": "D2", "day": 2, "depot": depot, "counties": ["桃園市"],
             "metrics": {"service_min": 20, "drive_min": 20, "total_min": 40}, "stops": [stop_b]},
        ]}

    def test_lists_short_route_and_compatible_target(self):
        items = low_workload_routes(self.payload(), threshold_minutes=30, max_minutes=100)
        self.assertEqual(items[0]["route_id"], "SHORT")
        self.assertEqual(items[0]["target_routes"][0]["route_id"], "TARGET")

    def test_moves_stop_and_removes_empty_source_route(self):
        updated, summary = move_route_stops(self.payload(), "SHORT", "TARGET", 100, FakeCostProvider())
        self.assertEqual(summary["moved_stop_count"], 1)
        self.assertEqual([r["route_id"] for r in updated["routes"]], ["TARGET"])
        self.assertEqual({s["node_id"] for s in updated["routes"][0]["stops"]}, {"A", "B"})

    def test_rejects_cross_county_merge_in_normal_mode(self):
        payload = self.payload()
        payload["routes"][1]["counties"] = ["新北市"]
        payload["routes"][1]["stops"][0]["county"] = "新北市"
        with self.assertRaisesMessage(ValueError, "不同縣市"):
            move_route_stops(payload, "SHORT", "TARGET", 100, FakeCostProvider())

    def test_atomic_write_creates_restorable_backup(self):
        with TemporaryDirectory() as folder:
            path = Path(folder) / "routes.json"
            path.write_text('{"version": 1}', encoding="utf-8")
            backup = write_payload_atomic(path, {"version": 2})
            self.assertIsNotNone(backup)
            self.assertEqual(backup.read_text(encoding="utf-8"), '{"version": 1}')
            self.assertEqual(__import__("json").loads(path.read_text(encoding="utf-8"))["version"], 2)

    def test_incremental_insert_can_allow_cross_county(self):
        from .services.incremental_scheduler import insert_candidates
        payload = self.payload()
        payload["routes"][0]["stops"] = []
        payload["routes"][0]["counties"] = ["新北市"]
        payload["routes"][1]["stops"][0]["county"] = "新北市"
        payload["routes"][1]["counties"] = ["新北市"]
        candidate = {"candidate_key": "C", "node_id": "C", "county": "桃園市", "lat": 1.5, "lon": 0, "service_min": 10}
        _, inserted, _ = insert_candidates(payload, [candidate], 1, 100, FakeCostProvider(), allow_cross_county=True)
        self.assertEqual(len(inserted), 1)

    def test_incremental_insert_never_exceeds_exact_osrm_limit(self):
        from .services.incremental_scheduler import insert_candidates

        class UnderestimatingProvider(FakeCostProvider):
            def route_geometry(self, coords):
                return {"coordinates": list(coords), "duration": 95, "distance": 10,
                        "used_fallback": False, "source": "fake OSRM"}

        payload = self.payload()
        candidate = {"candidate_key": "C", "node_id": "C", "county": payload["routes"][0]["counties"][0],
                     "lat": 1.5, "lon": 0, "service_min": 10}
        _, inserted, unassigned = insert_candidates(payload, [candidate], 1, 100, UnderestimatingProvider())
        self.assertEqual(inserted, [])
        self.assertEqual(len(unassigned), 1)


class AiAssistantGroundingTests(SimpleTestCase):
    def test_assistant_meta_question_is_treated_as_conversation(self):
        self.assertTrue(is_conversation_question("你可以接續我問過你的問題回答嗎？", []))

    @patch("routing.ai.orchestrator.GeminiProvider.complete", return_value="可以，你可以直接接著問。")
    def test_conversation_question_uses_model_without_route_tools(self, complete):
        from .ai.orchestrator import AssistantOrchestrator
        result = AssistantOrchestrator(
            "你可以接續我問過你的問題回答嗎？",
            {"conversation_history": [{"question": "哪些路線工時最低？", "answer": "..."}]},
            api_key="test-key", model="gemini-3.1-flash-lite",
        ).run()
        self.assertEqual(result["action"], "conversation")
        self.assertEqual(result["plan"]["steps"], [])
        self.assertFalse(result["mock"])
        complete.assert_called_once()

    def test_custom_gemini_model_name_is_allowed_but_unsafe_path_is_rejected(self):
        from .views import normalize_gemini_model
        self.assertEqual(normalize_gemini_model("gemini-4-flash-preview"), "gemini-4-flash-preview")
        self.assertEqual(normalize_gemini_model("models/gemini-3.5-flash"), "gemini-3.5-flash")
        self.assertEqual(normalize_gemini_model("../other-provider/model"), "gemini-3.5-flash")

    @patch("routing.ai.orchestrator.requests.post")
    def test_busy_flash_model_falls_back_to_flash_lite(self, post):
        from .ai.orchestrator import GeminiProvider
        busy = Mock(ok=False, status_code=503, reason="Unavailable")
        busy.json.return_value = {"error": {"message": "high demand"}}
        success = Mock(ok=True, status_code=200)
        success.json.return_value = {"candidates": [{"content": {"parts": [{"text": "完成"}]}}]}
        post.side_effect = [busy, success]

        provider = GeminiProvider(api_key="test-key", model="gemini-3.5-flash")
        self.assertEqual(provider.complete("test"), "完成")
        self.assertEqual(provider.model, "gemini-3.1-flash-lite")
        self.assertIn("自動改用", provider.last_notice)

    def test_page_model_overrides_deprecated_environment_default(self):
        from .ai.orchestrator import AssistantOrchestrator
        assistant = AssistantOrchestrator("查詢路線", {}, api_key="test-key", model="gemini-3.5-flash")
        self.assertEqual(assistant.provider.model, "gemini-3.5-flash")

    def test_unrelated_question_is_stopped_before_route_tools(self):
        question = "路上有人稱讚我漂亮，他是不是喜歡我？"
        self.assertFalse(is_system_related_question(question, []))
        result = out_of_scope_response(question)
        self.assertEqual(result["action"], "out_of_scope")
        self.assertEqual(result["plan"]["steps"], [])

    def test_system_follow_up_uses_recent_conversation(self):
        history = [{"question": "請找出工時最低的五條路線"}]
        self.assertTrue(is_system_related_question("那其中前三個呢？", history))

    def test_ai_assistant_exposes_read_only_tools(self):
        tools = list_tools()
        self.assertNotIn("simulate_route_change", {item["name"] for item in tools})
        valid, errors = validate_plan({
            "steps": [{"step_id": "change", "tool": "simulate_route_change",
                       "arguments": {}, "depends_on": []}],
        })
        self.assertFalse(valid)
        self.assertTrue(errors)

    def test_taipei_area_question_uses_area_coverage_tool(self):
        question = "可以告訴我清理區域在臺北市的路線嗎？"
        plan = TaskPlanner().plan(question, {"conversation_history": []})

        self.assertEqual(plan["steps"][0]["tool"], "analyze_area_coverage")
        self.assertEqual(plan["steps"][0]["arguments"]["county"], "臺北市")

    def test_area_coverage_groups_routes_and_districts(self):
        routes = [
            {
                "route_id": "P01-D01", "driver": "P01", "day": 1,
                "metrics": {"total_min": 120, "dist_km": 20},
                "stops": [
                    {"node_id": "A", "address": "臺北市士林區中山北路一段1號"},
                    {"node_id": "B", "address": "臺北市南港區市民大道八段2號"},
                ],
            },
            {
                "route_id": "W01-D01", "driver": "W01", "day": 1,
                "metrics": {"total_min": 100, "dist_km": 18},
                "stops": [{"node_id": "C", "address": "新北市五股區成泰路1號"}],
            },
        ]
        context = {
            "data": {
                "requested_variant": "normal",
                "variants": {"normal": {"routes": routes}},
            }
        }

        result = get_tool("analyze_area_coverage").execute(
            context, {"county": "臺北市", "variant": "normal"}, {}
        )

        self.assertTrue(result["success"])
        self.assertEqual(result["data"]["route_count"], 1)
        self.assertEqual(result["data"]["matched_stop_count"], 2)
        self.assertEqual(
            result["data"]["districts"],
            [{"district": "士林區", "stop_count": 1}, {"district": "南港區", "stop_count": 1}],
        )


class CompanyManagementTests(SimpleTestCase):
    def test_custom_industry_is_preserved(self):
        from .views import normalize_company_industry
        self.assertEqual(normalize_company_industry("other", "冷鏈物流"), "冷鏈物流")
        self.assertEqual(normalize_company_industry("other", ""), "generic_dispatch")
        self.assertEqual(normalize_company_industry("unexpected", "ignored"), "generic_dispatch")
