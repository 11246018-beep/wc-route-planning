import pandas as pd
import folium
from sklearn.cluster import KMeans
import numpy as np

INPUT="maintenance_data_v2_processed.xlsx"
MAX=3240

DEPOTS=[
{"name":"五股","lat":25.07154,"lon":121.44169,"drivers":2},
{"name":"平鎮","lat":24.90703,"lon":121.226872,"drivers":12}
]

df=pd.read_excel(INPUT)
df["清潔時間"]=df["清潔時間"].fillna(30)

# ======================
# assign nearest depot
# ======================

def nearest(row):
    d=[]
    for dep in DEPOTS:
        d.append((row["緯度"]-dep["lat"])**2+(row["經度"]-dep["lon"])**2)
    return DEPOTS[d.index(min(d))]["name"]

df["depot"]=df.apply(nearest,axis=1)

# ======================
# cluster per depot
# ======================

df["cluster"]=-1
cluster_id=0

for dep in DEPOTS:

    sub=df[df["depot"]==dep["name"]]
    k=dep["drivers"]

    coords=sub[["緯度","經度"]]

    labels=KMeans(n_clusters=k,random_state=0).fit_predict(coords)

    df.loc[sub.index,"cluster"]=labels+cluster_id

    cluster_id+=k

TOTAL=cluster_id

# ======================
# rebalance inside depot
# ======================

def workload(c):
    return df[df["cluster"]==c]["清潔時間"].sum()

for dep in DEPOTS:

    clusters=df[df["depot"]==dep["name"]]["cluster"].unique()

    for _ in range(50):

        loads={c:workload(c) for c in clusters}
        bad=[c for c,v in loads.items() if v>MAX]

        if not bad: break

        c=bad[0]

        cand=df[df["cluster"]==c].sort_values("清潔時間",ascending=False)

        for idx,row in cand.iterrows():

            for t in clusters:

                if t==c: continue
                if loads[t]+row["清潔時間"]<MAX:

                    df.at[idx,"cluster"]=t
                    loads[t]+=row["清潔時間"]
                    loads[c]-=row["清潔時間"]
                    break

            if loads[c]<=MAX: break

# ======================
# stats
# ======================

print("\n==== Final clusters ====\n")

for c in sorted(df["cluster"].unique()):
    total=int(workload(c))
    flag=" ⚠" if total>MAX else ""
    depot=df[df["cluster"]==c]["depot"].iloc[0]
    print(f"{depot} Cluster {c}: {total} min{flag}")

# ======================
# map
# ======================

m=folium.Map(location=[25,121],zoom_start=9)

colors=["red","blue","green","purple","orange","darkred","cadetblue","black","pink","gray","lightblue","lightgreen","beige","brown"]


# ======================
# Export all drivers to single Excel
# ======================

print("\nExporting drivers.xlsx ...")

writer = pd.ExcelWriter("drivers.xlsx", engine="openpyxl")

driver_no = 1

for c in sorted(df["cluster"].unique()):

    sub = df[df["cluster"] == c].copy()
    depot = sub["depot"].iloc[0]

    sheet = f"{depot}_司機{driver_no:02d}"

    cols = [
        "depot",
        "服務地點",
        "樓層",
        "間數",
        "清潔時間",
        "週清1",
        "週清2",
        "緯度",
        "經度"
    ]

    cols = [x for x in cols if x in sub.columns]

    sub[cols].sort_values(["服務地點","樓層"]).to_excel(
        writer,
        sheet_name=sheet,
        index=False
    )

    print("sheet:", sheet)
    driver_no += 1

writer.close()

print("\n✅ drivers.xlsx created")
