#used interpreter python 3.9 (3.11 can have issues with the libraries 3.10 probably fine)
#pip install geopandas pandas matplotlib shapely
#(import sqlite3 (in powershell, in case of import error))
#cd C:\Users\20234289\Documents\GitHub\cbl (directory)
#python Crime_per_ward.py (to run in terminal)

import os
import sqlite3
import geopandas as gpd
import pandas as pd
import matplotlib.pyplot as plt
from shapely.geometry import Point

# Path to database
db_path = r"C:\Users\20234289\OneDrive - TU Eindhoven\Q8\Data Challenge 2\crime_data.db"
# Path to shape file of ward
shapefile_path = r"C:\Users\20234289\OneDrive - TU Eindhoven\Q8\Data Challenge 2\Mapping files\London-wards-2018\London-wards-2018_ESRI\London_Ward.shp"
# Where output will be stored
output_dir = "maps_wards"
map_output_file = os.path.join(output_dir, "crime_map_2022_03.png")
csv_output_file = os.path.join(output_dir, "crime_counts_by_ward.csv")

# Create output directory if it doesn't exist
os.makedirs(output_dir, exist_ok=True)

# Step 1: Load crime data
conn = sqlite3.connect(db_path)
# Only March 2022 as sample
# To get map of only Burglary: AND type = 'Burglary'
query = """
SELECT crimeID, Month, Longitude, Latitude, Type, Outcome
FROM crime
WHERE Month = '2022-03' AND Longitude IS NOT NULL AND Latitude IS NOT NULL
"""
df_crime = pd.read_sql_query(query, conn)
conn.close()

# Step 2: Convert crime data to GeoDataFrame
geometry = [Point(xy) for xy in zip(df_crime["Longitude"], df_crime["Latitude"])]
gdf_crime = gpd.GeoDataFrame(df_crime, geometry=geometry, crs="EPSG:4326")

# Step 3: Load London wards shapefile
gdf_wards = gpd.read_file(shapefile_path)
gdf_wards = gdf_wards.to_crs(epsg=4326)

# Step 4: Spatial join crime points to ward polygons
gdf_joined = gpd.sjoin(gdf_crime, gdf_wards, how="inner", predicate="within")

# Step 5: Count crimes per ward
crime_counts = gdf_joined.groupby(gdf_joined.index_right).size()
gdf_wards["crime_count"] = gdf_wards.index.map(crime_counts).fillna(0).astype(int)

# Step 6: Export CSV with ward names and crime counts
ward_crime_summary = gdf_wards[["NAME", "crime_count"]].sort_values(by="crime_count", ascending=False)
ward_crime_summary.to_csv(csv_output_file, index=False)

# Step 7: Plotting
fig, ax = plt.subplots(1, 1, figsize=(12, 12))
gdf_wards.plot(column="crime_count", ax=ax, legend=True,
               legend_kwds={'label': "Number of Crimes (March 2022)"},
               cmap="OrRd", edgecolor="black")
ax.set_title("Number of Crimes per Ward in London (March 2022)", fontsize=15)
ax.axis('off')

# Step 8: Save map
plt.savefig(map_output_file, dpi=300, bbox_inches="tight")
plt.close()

print(f"Map saved to: {map_output_file}")
print(f"CSV saved to: {csv_output_file}")
