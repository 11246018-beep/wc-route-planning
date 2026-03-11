#!/usr/bin/env python
"""
One-command execution of complete workflow: Phase1 + Phase2

Usage:
    python run_all.py
"""

import os
import sys
import django
import time
import threading
import subprocess

# 配置 Django
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "route_system.settings")
django.setup()

from routing.services.phase1 import (
    load_from_database,
    load_and_process_data,
    generate_html_map,
    OUTPUT_CSV_NAME,
    OUTPUT_MAP_NAME,
)


def print_section(title):
    """打印分隔符和標題"""
    print("\n" + "=" * 80)
    print(f"  {title}")
    print("=" * 80 + "\n")


def run_phase1():
    """執行 Phase 1"""
    print_section("STEP 1: Running Phase1 (Data Processing & Node Aggregation)")

    try:
        # 載入資料
        print("Loading data...")
        raw_df = load_from_database()

        # 處理資料
        print("Processing data...")
        df_nodes = load_and_process_data(raw_df)

        # 設定輸出資料夾
        current_dir = os.path.dirname(os.path.abspath(__file__))
        output_dir = os.path.join(current_dir, "output")
        os.makedirs(output_dir, exist_ok=True)

        # 輸出 CSV
        output_csv_path = os.path.join(output_dir, OUTPUT_CSV_NAME)
        df_nodes.to_csv(output_csv_path, index=False, encoding="utf-8-sig")
        print(f"[OK] CSV exported: {output_csv_path}")

        # 生成地圖
        print("\nGenerating map (this may take a few minutes with many nodes)...")

        map_generated = False
        map_error = None

        def generate_map_thread():
            nonlocal map_generated, map_error
            try:
                output_map_path = os.path.join(output_dir, OUTPUT_MAP_NAME)
                generate_html_map(df_nodes, output_map_path)
                map_generated = True
                print(f"[OK] Map generated: {output_map_path}")
            except Exception as e:
                map_error = e

        map_thread = threading.Thread(target=generate_map_thread, daemon=True)
        map_thread.start()

        # 等待最多 20 分鐘
        map_thread.join(timeout=1200)

        if map_thread.is_alive():
            print("[WARNING] Map generation timeout (>20 minutes), skipping to Phase2")
        elif map_error:
            print(f"[WARNING] Map generation failed: {map_error}")
            print("[INFO] Continuing to Phase2...")
        elif not map_generated:
            print("[WARNING] Map generation did not complete")
            print("[INFO] Continuing to Phase2...")

        # 統計資訊
        print("\n[OK] Phase1 Statistics:")
        print(f"  - Original orders: {len(raw_df)} items")
        print(f"  - Aggregated nodes: {len(df_nodes)} nodes")
        print(f"  - Compression rate: {(1 - len(df_nodes) / len(raw_df)) * 100:.1f}%")
        print(f"  - Total service time: {df_nodes['Service_Time'].sum():.1f} minutes")
        print(f"  - Weekly 2x nodes: {(df_nodes['Freq'] == '2x').sum()} nodes")

        return True

    except Exception as e:
        print(f"\n[ERROR] Phase1 failed: {e}")
        import traceback

        traceback.print_exc()
        return False


def run_phase2():
    """執行 Phase 2"""
    print_section("STEP 2: Running Phase2 (Route Scheduling & Optimization)")

    try:
        current_dir = os.path.dirname(os.path.abspath(__file__))
        phase2_script = os.path.join(
            current_dir, "routing/services/phase2_scheduler.py"
        )

        result = subprocess.run(
            [sys.executable, phase2_script],
            cwd=current_dir,
            capture_output=True,
            text=True,
            timeout=3600,
        )

        if result.returncode == 0:
            print("[OK] Phase2 completed successfully")

            if result.stdout:
                lines = result.stdout.split("\n")
                for line in lines[-20:]:
                    if line.strip():
                        print(" ", line)

            return True

        else:
            print("\n[ERROR] Phase2 execution failed")
            if result.stderr:
                print("Error output:", result.stderr[:500])
            return False

    except subprocess.TimeoutExpired:
        print("\n[ERROR] Phase2 exceeded 1 hour timeout")
        return False

    except Exception as e:
        print(f"\n[ERROR] Phase2 execution failed: {e}")
        import traceback

        traceback.print_exc()
        return False


def main():
    """主程式"""

    print_section("Beginning full workflow execution")

    start_time = time.time()

    # Phase 1
    phase1_success = run_phase1()

    if not phase1_success:
        print_section("Phase1 critical failure")
        print("Phase1 failed to generate CSV. Stopping.")
        sys.exit(1)

    print("\n[OK] Phase1 completed")
    print("[INFO] Proceeding to Phase2...\n")

    # Phase 2
    phase2_success = run_phase2()

    elapsed_time = time.time() - start_time

    print_section("Workflow execution completed")

    if phase2_success:
        print("[OK] All phases completed successfully")
    else:
        print("[WARNING] Phase2 encountered issues")

    print(f"Total execution time: {elapsed_time:.1f} seconds\n")

    # 檢查輸出檔案
    print("Output files:")

    current_dir = os.path.dirname(os.path.abspath(__file__))
    output_dir = os.path.join(current_dir, "output")

    output_files = {
        "Node data": "processed_nodes_phase1.csv",
        "Interactive map": "maintenance_map_phase1.html",
        "Weekly schedule": "Weekly_Schedule_Summary.xlsx",
        "Daily summary": "Daily_Route_Summary.xlsx",
        "Weekly routing map": "Weekly_Routing_Map.html",
    }

    found_count = 0

    for description, filename in output_files.items():

        filepath = os.path.join(output_dir, filename)

        if os.path.exists(filepath):

            size_mb = os.path.getsize(filepath) / (1024 * 1024)

            print(f"  [OK] {description}: {filename} ({size_mb:.2f} MB)")

            found_count += 1

        else:

            print(f"  [--] {description}: {filename} (not found)")

    print(f"\n{found_count}/{len(output_files)} output files generated")

    print("\n" + "=" * 80)
    print("Workflow complete!")
    print("=" * 80 + "\n")


if __name__ == "__main__":
    main()