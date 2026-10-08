#!/usr/bin/env python
"""測試週清二點的排程邏輯"""

import os
import django
import pandas as pd

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'route_system.settings')
django.setup()

from routing.services.phase1 import load_from_database, load_and_process_data

print("\n" + "="*80)
print("測試週清二點的排程邏輯")
print("="*80)

# 載入 Phase1 數據
raw_df = load_from_database()
df_nodes = load_and_process_data(raw_df)

# 找到一個週清二的點（weekly_1=1, weekly_2=1）
weekly_2_nodes = df_nodes[(df_nodes['weekly_1'] == 1) & (df_nodes['weekly_2'] == 1)]
if len(weekly_2_nodes) > 0:
    sample_node = weekly_2_nodes.iloc[0]
    print(f"\n【樣本週清二節點】")
    print(f"  Node_ID: {sample_node['Node_ID']}")
    print(f"  weekly_1: {sample_node['weekly_1']}")
    print(f"  weekly_2: {sample_node['weekly_2']}")
    print(f"  visits: {sample_node['weekly_1'] + sample_node['weekly_2']}")
    print(f"  Service_Time: {sample_node['Service_Time']:.1f} 分鐘")
    print(f"  每次訪問時間: {sample_node['Service_Time'] / (sample_node['weekly_1'] + sample_node['weekly_2']):.1f} 分鐘")

    # 檢查 Phase2 輸出
    current_dir = os.path.dirname(os.path.abspath(__file__))
    weekly_summary_path = os.path.join(current_dir, 'routing/output/Weekly_Schedule_Summary.xlsx')

    if os.path.exists(weekly_summary_path):
        schedule_df = pd.read_excel(weekly_summary_path)

        # 找到這個節點的所有任務
        node_tasks = schedule_df[schedule_df['node_id'] == sample_node['Node_ID']]
        print(f"\n【Phase2 排程結果】")
        print(f"  任務數量: {len(node_tasks)}")
        print(f"  預期任務數: {sample_node['weekly_1'] + sample_node['weekly_2']}")

        if len(node_tasks) > 0:
            print(f"\n  任務詳情:")
            for _, task in node_tasks.iterrows():
                print(f"    任務 {task['task_id']}: 天數 {task['day']}, 服務時間 {task['service_time_min']:.1f} 分鐘")

            # 檢查是否在不同天
            days = sorted(node_tasks['day'].unique())
            print(f"\n  訪問天數: {days}")
            print(f"  是否在不同天: {'✅' if len(days) == len(node_tasks) else '❌'}")

            # 檢查總服務時間
            total_service = node_tasks['service_time_min'].sum()
            expected_total = sample_node['Service_Time']
            print(f"\n  總服務時間: {total_service:.1f} 分鐘")
            print(f"  預期總時間: {expected_total:.1f} 分鐘")
            print(f"  是否相等: {'✅' if abs(total_service - expected_total) < 0.1 else '❌'}")

    else:
        print("❌ 找不到 Weekly_Schedule_Summary.xlsx 文件")

else:
    print("❌ 沒有找到週清二的節點")

print("\n" + "="*80)