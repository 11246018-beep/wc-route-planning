import numpy as np
from sklearn.cluster import KMeans
from scipy.spatial import distance

def feature_based_constrained_kmeans(
    features, 
    service_times, 
    n_clusters, 
    max_cap, 
    stem_cost_func=None,
    avg_speed_kmh=40.0, 
    max_iter=30
):
    """
    Capacity-Constrained K-Means on Feature Space (e.g. Anchor Coordinates)
    
    Args:
        features (np.array): (N, 2) 特徵座標 (這裡是 Anchor Lat/Lon)
        service_times (np.array): (N,) 每個節點的服務時間 (min)
        n_clusters (int): 分群數量
        max_cap (float): 容量上限 (min)
        stem_cost_func (callable): function(center_coords) -> min. 
                                   計算從倉庫到該中心的 Commute Cost (Round Trip * 6 days)
        avg_speed_kmh (float): 平均時速 (km/h)
        max_iter (int): 最大迭代次數
        
    Returns:
        labels (np.array): 分群結果
        centers (np.array): 最終質心座標
    """
    n_samples = len(features)
    
    print(f"    [Algo] 啟動 Feature-Based Constrained K-Means (k={n_clusters}, Cap={max_cap})")

    # 1. 初始重心 (K-Means++)
    kmeans_init = KMeans(n_clusters=n_clusters, random_state=42, n_init=10)
    kmeans_init.fit(features)
    centers = kmeans_init.cluster_centers_
    
    # 用來檢查是否收斂
    labels = np.full(n_samples, -1)
    
    for iteration in range(max_iter):
        # ------------------------------------------------
        # 1. 計算成本矩陣 (Cost Matrix)
        # ------------------------------------------------
        # 距離矩陣: Node Feature -> Center (km)
        # 假設 Features 是 Lat/Lon
        dists_deg = distance.cdist(features, centers, 'euclidean')
        dists_km = dists_deg * 111
        
        # Intra-Cluster Travel Time (min)
        # 這是單程 Center -> Node
        # 估計每個任務的行車成本: 
        # (Center -> Node) + (Node -> Center) ? 
        # 或者是把這些點串起來? 
        # 簡單估計: 每個點貢獻 (Dist_to_Center / Speed * 60)
        # 這裡假設 Star-Shape 輻射狀，實際 TSP 會比較短，但這裡用作保守估計
        travel_costs = (dists_km / avg_speed_kmh) * 60
        
        # Stem Cost (Depot -> Center)
        # 這是一個 Cluster-Level 的固定成本，但會隨 Center 移動而變
        # 我們把它平均分攤到每個點? 或者是作為 Cluster 的 Base Load.
        # 為了指派，我們需要檢查: Sum(Service + Travel) + Stem <= Cap
        # => Sum(Service + Travel) <= Cap - Stem
        
        cluster_stem_costs = np.zeros(n_clusters)
        if stem_cost_func:
            for k in range(n_clusters):
                cluster_stem_costs[k] = stem_cost_func(centers[k])
        
        # 剩餘可用容量 (Effective Capacity)
        effective_caps = max_cap - cluster_stem_costs
        
        # 若 Stem Cost 太大導致負容量，修正為極小值
        effective_caps = np.maximum(effective_caps, 0)
        
        # ------------------------------------------------
        # 2. 指派 (Assignment with Regret)
        # ------------------------------------------------
        # 計算每個點對每個中心的總成本 (Service + Travel)
        # cost[i, k]
        node_costs = travel_costs + service_times[:, np.newaxis]
        
        # 排序候選中心
        sorted_indices = np.argsort(node_costs, axis=1)
        
        # 計算 Regret
        regrets = np.zeros(n_samples)
        for i in range(n_samples):
            c1 = sorted_indices[i, 0]
            c2 = sorted_indices[i, 1] if n_clusters > 1 else c1
            regrets[i] = node_costs[i, c2] - node_costs[i, c1]
            
        # 依照 Regret 排序 (大者優先)
        priority_idx = np.argsort(regrets)[::-1]
        
        new_labels = np.full(n_samples, -1)
        cluster_loads = np.zeros(n_clusters)
        forced_count = 0
        
        for i in priority_idx:
            assigned = False
            for k in sorted_indices[i]:
                cost = node_costs[i, k]
                
                if cluster_loads[k] + cost <= effective_caps[k]:
                    new_labels[i] = k
                    cluster_loads[k] += cost
                    assigned = True
                    break
            
            if not assigned:
                # Force assign based on min-overflow
                # 找一個溢出最小的
                overflows = []
                for k in range(n_clusters):
                    current_overflow = max(0, cluster_loads[k] - effective_caps[k])
                    new_overflow = max(0, cluster_loads[k] + node_costs[i, k] - effective_caps[k])
                    overflows.append(new_overflow - current_overflow)
                
                best_k = np.argmin(overflows)
                new_labels[i] = best_k
                cluster_loads[best_k] += node_costs[i, best_k]
                forced_count += 1

        # ------------------------------------------------
        # 3. 更新重心 (Update Centers)
        # ------------------------------------------------
        new_centers = np.zeros_like(centers)
        shift = 0.0
        
        for k in range(n_clusters):
            mask = (new_labels == k)
            if np.any(mask):
                new_centers[k] = np.mean(features[mask], axis=0)
            else:
                new_centers[k] = centers[k]
                
        shift = np.linalg.norm(new_centers - centers)
        
        # Check changes
        n_changed = np.sum(new_labels != labels)
        
        # msg = f"    Iter {iteration+1:02d}: Shift={shift:.4f}, Changed={n_changed}, Forced={forced_count}"
        # print(msg)
        
        centers = new_centers
        labels = new_labels
        
        if n_changed == 0 and shift < 1e-4:
            break
            
    return labels, centers

# Alias for backward compatibility / different naming convention
capacity_constrained_kmeans = feature_based_constrained_kmeans
