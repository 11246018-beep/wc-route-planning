#!/usr/bin/env python
"""
完整流程：
1. Phase1：從資料庫讀資料，整理成 processed_nodes_phase1.csv
2. Phase2-Normal：不跨縣市
3. Phase2-Cross：可跨縣市
4. Phase2-Compact：跨縣市精簡版
5. Dashboard Assets：整合舊路線與 Dispatch_Report
"""

from pathlib import Path
import os
import subprocess
import sys
import time

import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "route_system.settings")
django.setup()

from routing.services.phase1 import (
    load_from_database,
    load_and_process_data,
    generate_html_map,
    OUTPUT_CSV_NAME,
    OUTPUT_MAP_NAME,
)
from routing.services.dashboard_assets import export_dashboard_assets


BASE_DIR = Path(__file__).resolve().parent
OUTPUT_DIR = BASE_DIR / "output"


def print_section(title):
    print("\n" + "=" * 80)
    print(f"  {title}")
    print("=" * 80 + "\n")


def run_phase1():
    print_section("STEP 1: Running Phase1")

    raw_df = load_from_database()
    df_nodes = load_and_process_data(raw_df)

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    output_csv_path = OUTPUT_DIR / OUTPUT_CSV_NAME
    df_nodes.to_csv(output_csv_path, index=False, encoding="utf-8-sig")
    print(f"[OK] CSV exported: {output_csv_path}")

    try:
        output_map_path = OUTPUT_DIR / OUTPUT_MAP_NAME
        generate_html_map(df_nodes, str(output_map_path))
        print(f"[OK] Phase1 map exported: {output_map_path}")
    except Exception as e:
        print(f"[WARNING] Phase1 HTML map failed, but workflow continues: {e}")

    print("[OK] Phase1 statistics:")
    print(f"  - Original orders: {len(raw_df)}")
    print(f"  - Aggregated nodes: {len(df_nodes)}")
    print(f"  - Total service time: {df_nodes['Service_Time'].sum():.1f} minutes")
    return True


def run_phase2_script(label, script_path, log_sections):
    print_section(f"STEP 2-{label}: Running {script_path.name}")

    result = subprocess.run(
        [sys.executable, str(script_path)],
        cwd=str(BASE_DIR),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="ignore",
        timeout=3600,
    )

    log_sections.append(f"\n===== {label} | {script_path.name} =====\n")
    log_sections.append(f"Return code: {result.returncode}\n")
    log_sections.append("\n=== STDOUT ===\n")
    log_sections.append(result.stdout or "")
    log_sections.append("\n\n=== STDERR ===\n")
    log_sections.append(result.stderr or "")

    if result.returncode != 0:
        print(f"[ERROR] {script_path.name} failed")
        return False

    print(f"[OK] {script_path.name} completed successfully")
    if result.stdout:
        tail = [line for line in result.stdout.splitlines() if line.strip()][-12:]
        for line in tail:
            print(" ", line)
    return True


def run_dashboard_assets():
    print_section("STEP 3: Export Dashboard JSON / Old Routes / Dispatch Report")
    info = export_dashboard_assets(BASE_DIR)

    print("[OK] Dashboard assets completed")
    print(f"  - normal routes: {info['normal_routes_count']}")
    print(f"  - cross routes: {info['cross_routes_count']}")
    print(f"  - compact routes: {info['compact_routes_count']}")
    print(f"  - old routes: {info['old_routes_count']}")
    print(f"  - latest report: {info['latest_report']}")
    print(f"  - stamped report: {info['stamped_report']}")
    return True


def main():
    start = time.time()
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    if not run_phase1():
        print("[FAILED] Phase1 failed")
        sys.exit(1)

    log_sections = []

    phase2_scripts = [
        ("NORMAL", BASE_DIR / "routing" / "services" / "phase2_scheduler.py"),
        ("CROSS", BASE_DIR / "routing" / "services" / "phase2_scheduler_cross_county.py"),
        ("COMPACT", BASE_DIR / "routing" / "services" / "phase2_scheduler_cross_county_compact.py"),
    ]

    for label, script_path in phase2_scripts:
        ok = run_phase2_script(label, script_path, log_sections)
        if not ok:
            (OUTPUT_DIR / "run_all_last.log").write_text("".join(log_sections), encoding="utf-8")
            print(f"[INFO] 詳細錯誤請看: {OUTPUT_DIR / 'run_all_last.log'}")
            sys.exit(1)

    (OUTPUT_DIR / "run_all_last.log").write_text("".join(log_sections), encoding="utf-8")

    if not run_dashboard_assets():
        print("[FAILED] Dashboard assets export failed")
        sys.exit(1)

    elapsed = time.time() - start
    print_section("ALL DONE")
    print(f"Total elapsed time: {elapsed:.1f} seconds")
    print("[OK] 你現在可以回首頁重新整理 Dashboard。")


if __name__ == "__main__":
    main()