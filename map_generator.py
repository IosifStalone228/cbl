import sqlite3
import pandas as pd
import folium
from folium.plugins import MarkerCluster
import fiona
from shapely.geometry import shape, Point
from pyproj import Transformer
from rtree import index
import os

# Constants
shapefile_path = "LSOA_and_Ward_files/London-wards-2018/London-wards-2018_ESRI/London_Ward_CityMerged.shp"
os.makedirs("assets", exist_ok=True)

# Coordinate transformer: British National Grid → WGS84
transformer = Transformer.from_crs("EPSG:27700", "EPSG:4326", always_xy=True)

def reproject_geometry(geom_dict):
    if geom_dict["type"] == "Polygon":
        return {
            "type": "Polygon",
            "coordinates": [
                [transformer.transform(x, y) for x, y in ring]
                for ring in geom_dict["coordinates"]
            ],
        }
    elif geom_dict["type"] == "MultiPolygon":
        return {
            "type": "MultiPolygon",
            "coordinates": [
                [[transformer.transform(x, y) for x, y in ring] for ring in part]
                for part in geom_dict["coordinates"]
            ],
        }
    else:
        raise ValueError("Unsupported geometry type.")

def get_available_months(conn):
    df = pd.read_sql_query("SELECT DISTINCT Month FROM crime ORDER BY Month", conn)
    return df["Month"].tolist()

def generate_map(start_month, end_month, conn, output_map_path):
    print(f"Generating map from {start_month} to {end_month}...")

    df = pd.read_sql_query(
        """
        SELECT crimeID, Month, Longitude, Latitude, Type, Outcome
        FROM crime
        WHERE Longitude IS NOT NULL AND Latitude IS NOT NULL AND Type = 'Burglary'
        AND Month >= ? AND Month <= ?
        """,
        conn,
        params=(start_month, end_month),
    )

    # Load and reproject ward geometries
    wards = []
    spatial_index = index.Index()
    with fiona.open(shapefile_path) as shp:
        for i, feature in enumerate(shp):
            geom = shape(reproject_geometry(feature["geometry"]))
            ward_code = feature["properties"]["GSS_CODE"]
            wards.append((ward_code, geom))
            spatial_index.insert(i, geom.bounds)

    # Crime counts per ward
    ward_crime_counts = {code: 0 for code, _ in wards}
    crime_points = []

    for _, row in df.iterrows():
        point = Point(row["Longitude"], row["Latitude"])
        for idx in spatial_index.intersection((point.x, point.y, point.x, point.y)):
            ward_code, geom = wards[idx]
            if geom.contains(point):
                ward_crime_counts[ward_code] += 1
                crime_points.append((point.y, point.x, row["Type"], row["Outcome"]))
                break

    m = folium.Map(location=[51.5074, -0.1278], zoom_start=10, tiles="CartoDB positron")

    # Add ward outlines
    for ward_code, geom in wards:
        count = ward_crime_counts[ward_code]
        folium.GeoJson(
            data=geom.__geo_interface__,
            tooltip=f"Ward: {ward_code}<br>Crimes: {count}",
            style_function=lambda feature: {
                'color': 'blue',
                'weight': 2,
                'opacity': 1,
                'fillOpacity': 0.1
            }
        ).add_to(m)

    # Add clustered crime markers
    cluster = MarkerCluster().add_to(m)
    for lat, lon, crime_type, outcome in crime_points:
        folium.CircleMarker(
            location=[lat, lon],
            radius=2,
            color='red',
            fill=True,
            fill_opacity=0.7,
            tooltip=f"{crime_type} ({outcome})"
        ).add_to(cluster)

    m.save(output_map_path)
    print(f"Map saved to {output_map_path}")

if __name__ == "__main__":
    while True:
        db_path = input("Enter path to .db file (e.g. 'crime_data.db'): ").strip()

        if not os.path.exists(db_path):
            print(f"File not found: {db_path}")
            continue

        try:
            conn = sqlite3.connect(db_path)
            months = get_available_months(conn)
            if not months:
                print("No valid months found in the database.")
                conn.close()
                continue

            output_map_path = os.path.join("assets", "default_map.html")

            generate_map(months[0], months[0], conn, output_map_path)
            conn.close()
            break

        except Exception as e:
            print(f"Error: {e}")
