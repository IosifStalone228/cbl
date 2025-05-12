import dash
from dash import html
import sqlite3
import pandas as pd
import folium
import fiona
from shapely.geometry import shape, Point
from collections import defaultdict
import os

# Paths
shapefile_path = "/Users/mateilaslau/Desktop/everything/UNI Documents/CBL/Mapping Files/London-wards-2018/London-wards-2018_ESRI/London_Ward_CityMerged.shp"
db_path = "crime_data.db"  # Adjust if the DB file is elsewhere
output_path = os.path.join('assets', 'interactive_crime_map_2022_03.html')

# Ensure assets folder exists
os.makedirs('assets', exist_ok=True)

def generate_map(db_path):
    # Load crime data
    conn = sqlite3.connect(db_path)
    crime_df = pd.read_sql_query("""
        SELECT crimeID, Month, Longitude, Latitude, Type, Outcome
        FROM crime
        WHERE Month = '2023-07' AND Longitude IS NOT NULL AND Latitude IS NOT NULL
    """, conn)
    conn.close()

    # Load shapefile geometries
    ward_shapes = []
    with fiona.open(shapefile_path) as shp:
        for feature in shp:
            geometry = shape(feature["geometry"])
            ward_code = feature["properties"]["GSS_CODE"]
            ward_shapes.append((ward_code, geometry))

    # Count crimes per ward
    crime_counts = defaultdict(int)
    for _, row in crime_df.iterrows():
        point = Point(row["Longitude"], row["Latitude"])
        for ward_code, geometry in ward_shapes:
            if geometry.contains(point):
                crime_counts[ward_code] += 1
                break

    # Build folium map
    m = folium.Map(location=[51.5074, -0.1278], zoom_start=10, tiles='OpenStreetMap')
    for ward_code, geometry in ward_shapes:
        count = crime_counts.get(ward_code, 0)
        folium.GeoJson(
            data=geometry.__geo_interface__,
            tooltip=f"Ward: {ward_code}<br>Crimes: {count}"
        ).add_to(m)

    m.save(output_path)

# Generate map at startup
generate_map(db_path)

# Setup Dash app
app = dash.Dash(__name__, external_stylesheets=['https://stackpath.bootstrapcdn.com/bootstrap/4.5.2/css/bootstrap.min.css'])
app.layout = html.Div([
    html.H1("London Crime Map - July 2023"),
    html.Iframe(
        src="/assets/interactive_crime_map_2022_03.html",
        style={"height": "700px", "width": "100%", "border": "none"}
    )
])

if __name__ == "__main__":
    app.run_server(debug=True)