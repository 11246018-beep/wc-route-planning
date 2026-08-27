import json

from django.test import SimpleTestCase

from .ai.executor import PlanExecutor
from .ai.planner import TaskPlanner, validate_plan
from .ai.state import get_state, new_execution_state
from .ai.tool_registry import get_tool, list_tools
from .views import parse_ai_query_constraints, requested_list_limit


class FakeProvider:
    available = True

    def __init__(self, response):
        self.response = response

    def complete(self, prompt, timeout=20):
        return self.response


class AgentFoundationTests(SimpleTestCase):
    def context(self):
        return {
            "question_type": "dispatch",
            "common": {
                "company": {"key": "company_a", "name": "A"},
                "counts": {"drivers": 2},
                "schedule_settings": {"daily_work_minutes": 540},
            },
            "data": {
                "driver_rankings": {
                    "highest_weekly_total": [{"driver": "A01", "total": 100}],
                    "lowest_weekly_total": [],
                    "highest_daily_total": [],
                },
                "variants": {"normal": {"route_summary": {"totals": {"route_count": 2}}}},
                "requested_variant": "normal",
            },
        }

    def test_registry_has_metadata_and_no_unknown_tool(self):
        tools = list_tools()
        self.assertTrue(tools)
        self.assertTrue(all(item["name"] and item["category"] and item["input_schema"] for item in tools))
        self.assertIsNone(get_tool("invented_tool"))

    def test_planner_rejects_unknown_tool_and_falls_back(self):
        invalid = json.dumps({
            "goal": "test", "task_type": "query", "steps": [
                {"step_id": "s1", "tool": "invented_tool", "arguments": {}, "depends_on": []}
            ]
        })
        plan = TaskPlanner(FakeProvider(invalid)).plan("查詢公司資料", self.context())
        self.assertNotEqual(plan["steps"][0]["tool"], "invented_tool")
        self.assertTrue(plan.get("planner_errors"))

    def test_executor_runs_dependencies_and_normalizes_output(self):
        context = self.context()
        state = new_execution_state(company_key="company_a", user_id="u1", question="查詢", question_type="dispatch")
        plan = {
            "goal": "查詢公司與路線摘要", "task_type": "analysis", "steps": [
                {"step_id": "s1", "tool": "get_company_overview", "arguments": {}, "depends_on": []},
                {"step_id": "s2", "tool": "get_route_details", "arguments": {"variant": "normal"}, "depends_on": ["s1"]},
            ]
        }
        result = PlanExecutor().execute(plan, context, state)
        self.assertTrue(result["success"])
        self.assertEqual(result["completed_steps"], ["s1", "s2"])
        self.assertIn("source", result["step_results"]["s2"])
        self.assertIn("metrics", result["step_results"]["s2"])

    def test_unseen_composite_goal_can_be_planned_without_question_mapping(self):
        response = json.dumps({
            "goal": "先取得公司，再比較司機工作量",
            "task_type": "analysis",
            "requires_confirmation": False,
            "steps": [
                {"step_id": "company", "tool": "get_company_overview", "arguments": {}, "depends_on": []},
                {"step_id": "drivers", "tool": "get_driver_summary", "arguments": {}, "depends_on": ["company"]},
            ],
        })
        plan = TaskPlanner(FakeProvider(response)).plan("先取得公司，再比較司機工作量", self.context())
        state = new_execution_state(company_key="company_a", user_id="u1", question="先取得公司，再比較司機工作量", question_type="dispatch")
        result = PlanExecutor().execute(plan, self.context(), state)
        self.assertTrue(result["success"])
        self.assertEqual(result["completed_steps"], ["company", "drivers"])

    def test_invalid_argument_stops_before_tool(self):
        state = new_execution_state(company_key="company_a", user_id="u1", question="排名", question_type="carbon")
        plan = {"goal": "排名", "task_type": "analysis", "steps": [
            {"step_id": "s1", "tool": "rank_carbon_routes", "arguments": {"limit": "three"}, "depends_on": []}
        ]}
        result = PlanExecutor().execute(plan, {"question_type": "carbon", "data": {}}, state)
        self.assertFalse(result["success"])
        self.assertEqual(result["completed_steps"], [])
        self.assertTrue(result["errors"])

    def test_state_is_scoped_by_company_and_user(self):
        state = new_execution_state(company_key="company_a", user_id="u1", question="查詢", question_type="dispatch")
        self.assertIsNotNone(get_state(state["request_id"], company_key="company_a", user_id="u1"))
        self.assertIsNone(get_state(state["request_id"], company_key="company_b", user_id="u1"))
        self.assertIsNone(get_state(state["request_id"], company_key="company_a", user_id="u2"))

    def test_query_slots_keep_driver_and_scope_separate(self):
        slots = parse_ai_query_constraints("我該如何降低 P04 司機一週總工時？")
        self.assertEqual(slots["driver_ids"], ["P04"])
        self.assertEqual(slots["scope"], "weekly")
        self.assertEqual(slots["metric"], "work_time")
        self.assertEqual(requested_list_limit("請列出前三條路線"), 3)

    def test_combined_carbon_and_variant_goal_composes_two_tools(self):
        context = self.context()
        context["question_type"] = "carbon"
        context["data"]["query_constraints"] = {
            "metric": "carbon", "limit": 3, "driver_ids": [], "route_ids": [], "scope": "unspecified"
        }
        planner = TaskPlanner()
        plan = planner.plan("找出碳排最高前三條路線並比較三種路線模式的工時與碳排", context)
        self.assertEqual([step["tool"] for step in plan["steps"]], ["rank_carbon_routes", "compare_route_variants"])

    def test_imbalance_question_uses_calculated_imbalance_tool(self):
        context = self.context()
        context["data"]["query_constraints"] = {
            "metric": "work_time", "limit": 3, "driver_ids": [], "route_ids": [], "scope": "unspecified"
        }
        plan = TaskPlanner().plan("哪些司機工作量最不平均？", context)
        self.assertEqual(plan["steps"][0]["tool"], "rank_driver_imbalance")

    def test_carbon_reduction_question_uses_baseline_aware_tool(self):
        context = self.context()
        context["question_type"] = "carbon"
        context["data"]["query_constraints"] = {
            "metric": "carbon", "limit": 1, "driver_ids": [], "route_ids": [], "scope": "unspecified"
        }
        plan = TaskPlanner().plan("哪一條路線碳排減少最多？", context)
        self.assertEqual(plan["steps"][0]["tool"], "calculate_route_carbon_reduction")

    def test_backup_question_uses_candidate_assessment_tool(self):
        context = self.context()
        context["data"]["query_constraints"] = {
            "metric": "work_time", "limit": 10, "driver_ids": [], "route_ids": [], "scope": "unspecified"
        }
        plan = TaskPlanner().plan("目前哪一位司機的工時最少？建議讓他成為備用人員嗎？", context)
        self.assertEqual(plan["steps"][0]["tool"], "assess_backup_driver_candidates")
