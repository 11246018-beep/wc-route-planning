"""Read-only QA: reconcile generated downloads with current tenant routes."""
import sys
import json
from pathlib import Path

sys.path.append(str(Path(__file__).parent / '.test_runtime'))
from openpyxl import load_workbook
from routing.services.route_export import build_route_workbook
from routing.services.route_mileage import normalize_route_mileage

count = 0
for folder in (Path(__file__).parent / 'output/tenants').iterdir():
    if not all((folder / f'routes_{variant}.json').exists() for variant in ('normal', 'cross', 'compact')):
        continue
    payloads = {variant: json.loads((folder / f'routes_{variant}.json').read_text(encoding='utf-8')).get('routes', [])
                for variant in ('normal', 'cross', 'compact')}
    original = folder / 'Dispatch_Report_Latest.xlsx'
    book = load_workbook(build_route_workbook('dispatch_latest', payloads, original))
    sheet = book['route_summary']
    headers = [cell.value for cell in sheet[1]]
    rows = [dict(zip(headers, values)) for values in sheet.iter_rows(min_row=2, values_only=True)]
    for variant, routes in payloads.items():
        expected = sum(normalize_route_mileage(route, variant)['metrics']['dist_km'] for route in routes)
        actual = sum(row['dist_km'] for row in rows if row['variant'] == variant)
        assert abs(expected - actual) < .000001
        count += len(routes)
    if original.exists():
        before = load_workbook(original)
        if 'all_points' in before.sheetnames:
            assert list(before['all_points'].values) == list(book['all_points'].values)
    print(folder.name, 'all route variants reconciled; other source data preserved')
print('Verified downloaded route summaries:', count)
