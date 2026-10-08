#!/usr/bin/env python
"""分析 Weekly_Schedule_Summary 为什么总维护时间不是 27954.5"""

import os
import django
import pandas as pd

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'route_system.settings')
django.setup()

from routing.services.phase1 import load_from_database, load_and_process_data

print("\n" + "="*80)
print("分析 Weekly_Schedule_Summary 维护时间差异")
print("="*80)

# 第一步：Phase1 数据
print("\n【Phase1 输出】")
raw_df = load_from_database()
df_nodes = load_and_process_data(raw_df)

phase1_total_service_time = df_nodes['Service_Time'].sum()
print(f"  原始数据: {len(raw_df)} 行")
print(f"  聚合后节点: {len(df_nodes)} 个")
print(f"  Phase1 总服务时间: {phase1_total_service_time:.1f} 分钟")

# 查看 weekly_1 和 weekly_2 聚合后的情况
print(f"\n  weekly_1 统计:")
print(f"    - 总和: {int(df_nodes['weekly_1'].sum())}")
print(f"    - 最大值: {df_nodes['weekly_1'].max()}")
print(f"    - 平均值: {df_nodes['weekly_1'].mean():.2f}")

print(f"\n  weekly_2 统计:")
print(f"    - 总和: {int(df_nodes['weekly_2'].sum())}")
print(f"    - 最大值: {df_nodes['weekly_2'].max()}")
print(f"    - > 0 的数量: {(df_nodes['weekly_2'] > 0).sum()}")

# 查看 Freq 分布
print(f"\n  Freq 分布:")
print(f"    - 1x 节点: {(df_nodes['Freq'] == '1x').sum()}")
print(f"    - 2x 节点: {(df_nodes['Freq'] == '2x').sum()}")

# 第二步：读取 Phase2 输出（如果存在）
current_dir = os.path.dirname(os.path.abspath(__file__))
weekly_summary_path = os.path.join(current_dir, 'routing/output/Weekly_Schedule_Summary.xlsx')

if os.path.exists(weekly_summary_path):
    print("\n【Phase2 输出 - Weekly_Schedule_Summary.xlsx】")
    schedule_df = pd.read_excel(weekly_summary_path)
    
    total_tasks = len(schedule_df)
    total_service_time_phase2 = schedule_df['service_time_min'].sum() if 'service_time_min' in schedule_df.columns else 0
    
    print(f"  总任务数: {total_tasks}")
    print(f"  总服务时间: {total_service_time_phase2:.1f} 分钟")
    print(f"  与 Phase1 的差异: {total_service_time_phase2 - phase1_total_service_time:.1f} 分钟")
    print(f"  差异原因: {total_tasks - len(df_nodes)} 个额外任务" + 
          ("（来自2x节点展开）" if total_tasks > len(df_nodes) else ""))
    
    # 分析任务分布
    if 'freq' in schedule_df.columns:
        print(f"\n  任务频率分布:")
        print(f"    - 1x 任务: {(schedule_df['freq'] == '1x').sum()}")
        print(f"    - 2x 任务: {(schedule_df['freq'] == '2x').sum()}")

# 第三步：理论计算
print("\n【理论分析】")
count_1x = (df_nodes['Freq'] == '1x').sum()
count_2x = (df_nodes['Freq'] == '2x').sum()

theoretical_tasks = count_1x * 1 + count_2x * 2
print(f"  理论任务数 (基于 Freq 字段):")
print(f"    - 1x 节点产生任务: {count_1x} × 1 = {count_1x}")
print(f"    - 2x 节点产生任务: {count_2x} × 2 = {count_2x * 2}")
print(f"    - 总计: {theoretical_tasks}")

print("\n【关键问题分析】")

# 问题 1: weekly_1 + weekly_2 vs Freq
print(f"\n  问题 1: weekly 聚合逻辑")
print(f"    - weekly_1 总和 (sum 聚合): {int(df_nodes['weekly_1'].sum())}")
print(f"    - weekly_2 总和 (sum 聚合): {int(df_nodes['weekly_2'].sum())}")
print(f"    - 理论总访问次数: {int(df_nodes['weekly_1'].sum() + df_nodes['weekly_2'].sum())}")
print(f"    - 实际生成任务数: {theoretical_tasks} (基于 Freq 字段)")

# 问题 2: 是否所有任务都被分配
if os.path.exists(weekly_summary_path):
    if len(schedule_df) != theoretical_tasks:
        print(f"\n  问题 2: 任务分配不完整")
        print(f"    - 期望任务数: {theoretical_tasks}")
        print(f"    - 实际分配任务: {len(schedule_df)}")
        print(f"    - 未分配任务: {theoretical_tasks - len(schedule_df)}")
        print(f"    ⚠️ 这说明某些任务在排程过程中未被分配!")

print("\n" + "="*80)
