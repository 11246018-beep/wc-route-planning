#!/usr/bin/env python
"""
?堆?桃?蹓???1. Phase1?城????冽???????? processed_nodes_phase1.csv
2. Phase2-Normal?垢???行?3. Phase2-Cross?城?駁??質??4. Phase2-Compact?奕璆???????5. Dashboard Assets?垮?????????Dispatch_Report
"""

from pathlib import Path
import argparse
import os
import subprocess
import sys
import time
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed

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
from routing.tenant import resolve_company_key


BASE_DIR = Path(__file__).resolve().parent
OUTPUT_DIR = BASE_DIR / "output"


def print_section(title):
    print("\n" + "=" * 80)
    print(f"  {title}")
    print("=" * 80 + "\n")


def run_phase1(company_key):
    print_section("STEP 1: Running Phase1")

    raw_df = load_from_database(company_key=company_key)
    if raw_df.empty:
        print(f"[ERROR] company_key={company_key} 沒有可排程的有效點位資料。")
        return False
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


def run_phase2_script(label, script_path, log_sections, company_key):
    print_section(f"STEP 2-{label}: Running {script_path.name}")

    process = subprocess.Popen(
        [sys.executable, str(script_path)],
        cwd=str(BASE_DIR),
        env={**os.environ, "DISPATCH_COMPANY_KEY": str(company_key or "")},
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="ignore",
        bufsize=1,
    )

    stdout_lines = []
    stderr_lines = []

    def stream_pipe(pipe, prefix, collector):
        try:
            for line in iter(pipe.readline, ""):
                collector.append(line)
                print(f"{prefix}{line}", end="", flush=True)
        finally:
            pipe.close()

    stdout_thread = threading.Thread(
        target=stream_pipe,
        args=(process.stdout, "", stdout_lines),
        daemon=True,
    )
    stderr_thread = threading.Thread(
        target=stream_pipe,
        args=(process.stderr, "[stderr] ", stderr_lines),
        daemon=True,
    )
    stdout_thread.start()
    stderr_thread.start()

    try:
        returncode = process.wait(timeout=3600)
    except subprocess.TimeoutExpired:
        process.kill()
        stdout_thread.join(timeout=5)
        stderr_thread.join(timeout=5)
        log_sections.append(f"\n===== {label} | {script_path.name} =====\n")
        log_sections.append("Return code: TIMEOUT\n")
        log_sections.append("\n=== STDOUT ===\n")
        log_sections.append("".join(stdout_lines))
        log_sections.append("\n\n=== STDERR ===\n")
        log_sections.append("".join(stderr_lines))
        print(f"[ERROR] {script_path.name} timeout after 3600 seconds")
        return False

    stdout_thread.join(timeout=5)
    stderr_thread.join(timeout=5)

    log_sections.append(f"\n===== {label} | {script_path.name} =====\n")
    log_sections.append(f"Return code: {returncode}\n")
    log_sections.append("\n=== STDOUT ===\n")
    log_sections.append("".join(stdout_lines))
    log_sections.append("\n\n=== STDERR ===\n")
    log_sections.append("".join(stderr_lines))

    if returncode != 0:
        print(f"[ERROR] {script_path.name} failed")
        return False

    print(f"[OK] {script_path.name} completed successfully")
    if stdout_lines:
        tail = [line for line in "".join(stdout_lines).splitlines() if line.strip()][-12:]
        for line in tail:
            print(" ", line)
    return True


def run_dashboard_assets():
    print_section("STEP 3: Export Dashboard JSON / Old Routes / Dispatch Report")
    info = export_dashboard_assets(BASE_DIR, output_dir=OUTPUT_DIR)

    print("[OK] Dashboard assets completed")
    print(f"  - normal routes: {info['normal_routes_count']}")
    print(f"  - cross routes: {info['cross_routes_count']}")
    print(f"  - compact routes: {info['compact_routes_count']}")
    print(f"  - old routes: {info['old_routes_count']}")
    print(f"  - latest report: {info['latest_report']}")
    print(f"  - stamped report: {info['stamped_report']}")
    return True


def main():
    global OUTPUT_DIR
    parser = argparse.ArgumentParser(description="Run Dispatch Nav scheduling workflow.")
    parser.add_argument("--company-key", default="", help="Company key to schedule, e.g. toilet_demo or generic_demo.")
    parser.add_argument("--output-dir", default="", help="Optional isolated output directory for this run.")
    args = parser.parse_args()

    start = time.time()
    company_key, company_source = resolve_company_key(args.company_key)
    requested_output = str(args.output_dir or os.environ.get("DISPATCH_OUTPUT_DIR") or "").strip()
    OUTPUT_DIR = Path(requested_output).resolve() if requested_output else BASE_DIR / "output" / "tenants" / company_key
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    os.environ["DISPATCH_COMPANY_KEY"] = company_key
    os.environ["DISPATCH_OUTPUT_DIR"] = str(OUTPUT_DIR)
    print(f"[RUN_ALL] company_key={company_key}")
    print(f"[RUN_ALL] company_key ???={company_source}")
    print(f"[RUN_ALL] output_dir={OUTPUT_DIR}")

    if not run_phase1(company_key):
        print("[FAILED] Phase1 failed")
        sys.exit(1)

    log_sections = []

    phase2_scripts = [
        ("NORMAL", BASE_DIR / "routing" / "services" / "phase2_scheduler.py"),
        ("CROSS", BASE_DIR / "routing" / "services" / "phase2_scheduler_cross_county.py"),
        ("COMPACT", BASE_DIR / "routing" / "services" / "phase2_scheduler_cross_county_compact.py"),
    ]

    max_workers = min(len(phase2_scripts), max(int(os.environ.get("DISPATCH_PHASE2_WORKERS", "3")), 1))
    results = {}
    with ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="phase2") as executor:
        futures = {
            executor.submit(run_phase2_script, label, script_path, log_sections, company_key): label
            for label, script_path in phase2_scripts
        }
        for future in as_completed(futures):
            label = futures[future]
            try:
                results[label] = bool(future.result())
            except Exception as exc:
                results[label] = False
                log_sections.append(f"\n===== {label} =====\nUnhandled error: {exc}\n")

    if not all(results.get(label, False) for label, _ in phase2_scripts):
        (OUTPUT_DIR / "run_all_last.log").write_text("".join(log_sections), encoding="utf-8")
        print(f"[INFO] Phase2 failure log: {OUTPUT_DIR / 'run_all_last.log'}")
        sys.exit(1)
    (OUTPUT_DIR / "run_all_last.log").write_text("".join(log_sections), encoding="utf-8")

    if not run_dashboard_assets():
        print("[FAILED] Dashboard assets export failed")
        sys.exit(1)

    elapsed = time.time() - start
    print_section("ALL DONE")
    print(f"Total elapsed time: {elapsed:.1f} seconds")
    print("[OK] 所有模式已完成並更新 Dashboard。")


if __name__ == "__main__":
    main()
