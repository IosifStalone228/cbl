import dash
from dash import html
import sqlite3
import pandas as pd
import folium
import fiona
from shapely.geometry import shape, Point
from pyproj import Transformer
from rtree import index
import os

# Paths
shapefile_path = "/Users/mateilaslau/Desktop/everything/UNI Documents/CBL/Mapping Files/London-wards-2018/London-wards-2018_ESRI/London_Ward_CityMerged.shp"
db_path = "crime_data.db"
output_path = os.path.join('assets', 'interactive_crime_map_2022_03.html')
os.makedirs('assets', exist_ok=True)

# Transformer: British National Grid -> WGS84
transformer = Transformer.from_crs("EPSG:27700", "EPSG:4326", always_xy=True)

def reproject_geometry(geom_dict):
    if geom_dict["type"] == "Polygon":
        return {
            "type": "Polygon",
            "coordinates": [
                [transformer.transform(x, y) for x, y in ring]
                for ring in geom_dict["coordinates"]
            ]
        }
    elif geom_dict["type"] == "MultiPolygon":
        return {
            "type": "MultiPolygon",
            "coordinates": [
                [[transformer.transform(x, y) for x, y in ring] for ring in part]
                for part in geom_dict["coordinates"]
            ]
        }
    else:
        raise ValueError("Unsupported geometry type.")

def generate_map(db_path):
    # Load crime data
    conn = sqlite3.connect(db_path)
    df = pd.read_sql_query("""
        SELECT crimeID, Month, Longitude, Latitude, Type, Outcome
        FROM crime
        WHERE Month = '2023-07' AND Longitude IS NOT NULL AND Latitude IS NOT NULL
    """, conn)
    conn.close()

    # Load shapefile and reproject geometries
    wards = []
    spatial_index = index.Index()
    with fiona.open(shapefile_path) as shp:
        for i, feature in enumerate(shp):
            geom = shape(reproject_geometry(feature["geometry"]))
            ward_code = feature["properties"]["GSS_CODE"]
            wards.append((ward_code, geom))
            spatial_index.insert(i, geom.bounds)

    # Count crimes and store points
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

    # Create map
    m = folium.Map(location=[51.5074, -0.1278], zoom_start=10, tiles="OpenStreetMap")

    # Add ward outlines
    # Add ward outlines with clearer styling
    for ward_code, geom in wards:
        count = ward_crime_counts[ward_code]
        folium.GeoJson(
            data=geom.__geo_interface__,
            tooltip=f"Ward: {ward_code}<br>Crimes: {count}",
            style_function=lambda feature: {
                'color': 'blue',  # Outline color
                'weight': 2,  # Thickness of line
                'opacity': 1,  # Line opacity
                'fillOpacity': 0.1  # Slightly visible fill for visual contrast
            }
        ).add_to(m)

    # Add crime dots
    for lat, lon, crime_type, outcome in crime_points:
        folium.CircleMarker(
            location=[lat, lon],
            radius=2,
            color='red',
            fill=True,
            fill_opacity=0.7,
            tooltip=f"{crime_type} ({outcome})"
        ).add_to(m)

    m.save(output_path)

# Generate the map
generate_map(db_path)

# Dash app
app = dash.Dash(__name__)
app.layout = html.Div([
    html.H1("London Crime Map - July 2023"),
    html.Iframe(src="/assets/interactive_crime_map_2022_03.html",
                style={"height": "700px", "width": "100%", "border": "none"})
])

if __name__ == "__main__":
    app.run(debug=True)