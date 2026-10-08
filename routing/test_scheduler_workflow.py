import ast
import json
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from django.test import SimpleTestCase
from openpyxl import load_workbook

from . import views
from .services.dashboard_assets import export_dashboard_assets


class SchedulerWorkflowTests(SimpleTestCase):
    def tearDown(self):
        views._set_run_state(running=False, finished=False, success=False)

    def test_pending_thread_clears_previous_success_and_prevents_duplicate(self):
        views._set_run_state(running=False, finished=True, success=True)
        self.assertTrue(views._reserve_scheduler_run('compact'))
        snapshot = views._snapshot_run_state('compact')
        self.assertTrue(snapshot['running'])
        self.assertFalse(snapshot['finished'])
        self.assertFalse(snapshot['success'])
        self.assertFalse(views._reserve_scheduler_run('normal'))

    def test_failure_explains_child_process_error(self):
        result = SimpleNamespace(returncode=1, stdout='', stderr='Traceback:\nModuleNotFoundError: missing dependency')
        self.assertIn('missing dependency', views._scheduler_failure_message(result))
        self.assertIn('結束碼 1', views._scheduler_failure_message(result))

    def test_scheduler_includes_all_supported_variants(self):
        tree = ast.parse((Path(__file__).resolve().parents[1] / 'run_all.py').read_text(encoding='utf-8'))
        assignment = next(node for node in tree.body if isinstance(node, ast.Assign)
                          and any(isinstance(t, ast.Name) and t.id == 'PHASE2_SCRIPTS' for t in node.targets))
        scripts = ast.literal_eval(assignment.value)
        self.assertEqual({label for label, _ in scripts}, {'NORMAL', 'CROSS', 'COMPACT'})
        for _, filename in scripts:
            self.assertTrue((Path(__file__).parent / 'services' / filename).exists())

    def test_export_preserves_uploaded_old_routes_and_includes_compact(self):
        route = {'driver': 'A', 'day': 1, 'return_to_depot': False,
                 'stops': [{'seq': 1, 'address': 'a', 'travel_dist_km': 2}],
                 'metrics': {'dist_km': 2, 'drive_min': 3, 'service_min': 5}}
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder)
            original = json.dumps({'meta': {'source': 'uploaded_excel'}, 'routes': [route]})
            (output / 'old_routes.json').write_text(original, encoding='utf-8')
            (output / 'routes_compact.json').write_text(json.dumps({'routes': [route]}), encoding='utf-8')
            with patch('routing.services.dashboard_assets.load_normal_routes_and_refresh', return_value=([], {}, 'normal')), \
                 patch('routing.services.dashboard_assets.parse_old_routes_from_map_html', side_effect=AssertionError('must preserve uploaded baseline')):
                info = export_dashboard_assets(output, output)
            self.assertEqual((output / 'old_routes.json').read_text(encoding='utf-8'), original)
            self.assertEqual(info['compact_routes_count'], 1)
            book = load_workbook(info['latest_report'])
            rows = list(book['route_summary'].values)
            index = rows[0].index('variant')
            self.assertEqual({row[index] for row in rows[1:]}, {'old', 'compact'})
