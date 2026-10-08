#!/usr/bin/env python
"""檢查週清二點是否在同一天被訪問"""

import pandas as pd
import os

weekly_summary_path = 'routing/output/Weekly_Schedule_Summary.xlsx'
if os.path.exists(weekly_summary_path):
    schedule_df = pd.read_excel(weekly_summary_path)

    # 檢查同一天同節點的違規
    violations = []
    for node_id in schedule_df['node_id'].unique():
        node_tasks = schedule_df[schedule_df['node_id'] == node_id]
        day_counts = node_tasks.groupby('day').size()
        if (day_counts > 1).any():
            violations.append(node_id)

    if violations:
        print(f'❌ 發現 {len(violations)} 個節點在同一天被訪問多次')
        print('前5個:', violations[:5])
    else:
        print('✅ 所有節點都正確地在不同天被訪問！')

    # 統計多訪問節點
    node_counts = schedule_df.groupby('node_id').size()
    multi_visit = (node_counts > 1).sum()
    print(f'多訪問節點數量: {multi_visit}')

    # 顯示一個樣本
    if multi_visit > 0:
        sample_node = node_counts[node_counts > 1].index[0]
        sample_tasks = schedule_df[schedule_df['node_id'] == sample_node]
        print(f'\n樣本 {sample_node}:')
        for _, t in sample_tasks.iterrows():
            print(f'  第{t["day"]}天: {t["service_time_min"]:.1f}分鐘')

else:
    print('找不到 Weekly_Schedule_Summary.xlsx 文件')