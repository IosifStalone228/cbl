import os
import sqlite3
import geopandas as gpd
import pandas as pd
import matplotlib.pyplot as plt
from shapely.geometry import Point

# Paths
db_path = r"C:\Users\20234289\OneDrive - TU Eindhoven\Q8\Data Challenge 2\crime_data.db"
shapefile_path = r"C:\Users\20234289\OneDrive - TU Eindhoven\Q8\Data Challenge 2\Mapping files\London-wards-2018\London-wards-2018_ESRI\London_Ward.shp"
output_dir = "maps_wards"
os.makedirs(output_dir, exist_ok=True)

map_output_file = os.path.join(output_dir, "burglary_unsolved_percentage_map.png")
csv_output_file = os.path.join(output_dir, "burglary_unsolved_percentage_by_ward.csv")

# Step 1: Load crime data
conn = sqlite3.connect(db_path)
query = """
SELECT crimeID, Month, Longitude, Latitude, Type, Outcome
FROM crime
WHERE Longitude IS NOT NULL AND Latitude IS NOT NULL
"""
df_crime = pd.read_sql_query(query, conn)
conn.close()

# Step 2: Filter for burglary crimes
df_burglary = df_crime[df_crime["Type"] == "Burglary"].copy()

# Step 3: Convert to GeoDataFrame
geometry = [Point(xy) for xy in zip(df_burglary["Longitude"], df_burglary["Latitude"])]
gdf_burglary = gpd.GeoDataFrame(df_burglary, geometry=geometry, crs="EPSG:4326")

# Step 4: Load ward shapefile
gdf_wards = gpd.read_file(shapefile_path)
gdf_wards = gdf_wards.to_crs(epsg=4326)

# Step 5: Spatial join
gdf_joined = gpd.sjoin(gdf_burglary, gdf_wards, how="inner", predicate="within")

# Step 6: Calculate totals and matched outcomes
total_burglaries = gdf_joined.groupby(gdf_joined.index_right).size()
matched_outcomes = gdf_joined[
    gdf_joined["Outcome"] == "Investigation complete; no suspect identified"
].groupby(gdf_joined.index_right).size()

# Step 7: Merge into wards
gdf_wards["total_burglaries"] = gdf_wards.index.map(total_burglaries).fillna(0)
gdf_wards["matched_outcomes"] = gdf_wards.index.map(matched_outcomes).fillna(0)
gdf_wards["burglary_unsolved_pct"] = (
    100 * gdf_wards["matched_outcomes"] / gdf_wards["total_burglaries"]
).fillna(0)

# Step 8: Save CSV
gdf_wards[["NAME", "total_burglaries", "matched_outcomes", "burglary_unsolved_pct"]].to_csv(csv_output_file, index=False)

# Step 9: Plot map
fig, ax = plt.subplots(1, 1, figsize=(12, 12))
gdf_wards.plot(column="burglary_unsolved_pct", ax=ax, legend=True,
               legend_kwds={'label': "% of Burglaries Unsolved"},
               cmap="Blues", edgecolor="black")
ax.set_title("Burglary Cases with No Suspect Identified (2022-04 - 2025-03)", fontsize=15)
ax.axis('off')

# Step 10: Save map
plt.savefig(map_output_file, dpi=300, bbox_inches="tight")
plt.close()

print(f"Map saved to: {map_output_file}")
print(f"CSV saved to: {csv_output_file}")
