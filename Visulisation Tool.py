import dash
from dash import html, dcc, Output, Input, State
import os
import sqlite3
import pandas as pd
from datetime import datetime, timezone
import folium
from folium.plugins import MarkerCluster
import fiona
from shapely.geometry import shape, Point
from pyproj import Transformer
from rtree import index
import dash.exceptions

# Paths
db_path = "crime_data.db"
shapefile_path = "LSOA_and_Ward_files/London-wards-2018/London-wards-2018_ESRI/London_Ward_CityMerged.shp"
assets_folder = "assets"
default_map_path = os.path.join(assets_folder, "default_map.html")
map_output_path = os.path.join(assets_folder, "temp_crime_map.html")

# Ensure assets folder exists
os.makedirs(assets_folder, exist_ok=True)

# Clean up assets folder on startup except for default_map.html
for file in os.listdir(assets_folder):
    if file != "default_map.html":
        os.remove(os.path.join(assets_folder, file))

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

def get_available_months():
    if not os.path.exists(db_path):
        return []
    conn = sqlite3.connect(db_path)
    df = pd.read_sql_query("SELECT DISTINCT Month FROM crime ORDER BY Month", conn)
    conn.close()
    return df["Month"].tolist()

def generate_map(start_month, end_month, output_path):
    conn = sqlite3.connect(db_path)
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
    conn.close()

    wards = []
    spatial_index = index.Index()
    with fiona.open(shapefile_path) as shp:
        for i, feature in enumerate(shp):
            geom = shape(reproject_geometry(feature["geometry"]))
            ward_code = feature["properties"]["GSS_CODE"]
            wards.append((ward_code, geom))
            spatial_index.insert(i, geom.bounds)

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

    m.save(output_path)
    print(f"[INFO] Generated map for period: {start_month} to {end_month}")

def load_special_ops_text():
    try:
        with open("special_ops.txt", "r", encoding="utf-8") as f:
            return f.read()
    except Exception as e:
        return f"Could not load special_ops.txt: {e}"

# Dash app
app = dash.Dash(__name__)

available_months = get_available_months()
month_to_index = {m: i for i, m in enumerate(available_months)}
index_to_month = {i: m for i, m in enumerate(available_months)}

app.layout = html.Div([
    html.H1("London Burglary Map - Select Time Period"),
    dcc.RangeSlider(
        id="month-slider",
        min=0,
        max=len(available_months) - 1 if available_months else 0,
        value=[0, len(available_months) - 1] if available_months else [0, 0],
        marks={i: m for i, m in enumerate(available_months)},
        step=None,
        allowCross=False,
        tooltip={"placement": "bottom", "always_visible": True},
    ),
    html.Button("Submit", id="submit-button", n_clicks=0, style={"marginTop": "20px"}),
    dcc.Upload(
        id='upload-db',
        children=html.Button('Upload .db File', style={"marginTop": "20px"}),
        accept='.db',
        multiple=False,
    ),
    html.Div(id='upload-status', style={'marginTop': '10px', 'color': 'green'}),
    html.Button("Special Operation description", id="toggle-description", n_clicks=0, style={"marginTop": "20px"}),
    html.Div(id="special-description", style={"marginTop": "10px", "whiteSpace": "pre-wrap", "display": "none"}),
    html.Div(id="map-time-period", style={"marginTop": "20px", "fontWeight": "bold"}),
    dcc.Loading(
        id="loading-spinner",
        type="circle",
        children=[
            html.Div(id="loading-output"),
            html.Iframe(
                id="crime-map",
                src="/assets/default_map.html",
                style={"height": "700px", "width": "100%", "border": "none"},
            )
        ]
    )
])

@app.callback(
    Output('upload-status', 'children'),
    Output('month-slider', 'marks'),
    Output('month-slider', 'min'),
    Output('month-slider', 'max'),
    Output('month-slider', 'value'),
    Output('crime-map', 'src'),
    Output('map-time-period', 'children'),
    Output('loading-output', 'children'),
    Input('upload-db', 'contents'),
    State('upload-db', 'filename'),
    Input('submit-button', 'n_clicks'),
    State('month-slider', 'value')
)
def handle_all(upload_contents, upload_filename, submit_n_clicks, slider_range):
    ctx = dash.callback_context
    trigger_id = ctx.triggered[0]['prop_id'].split('.')[0] if ctx.triggered else None

    global available_months, month_to_index, index_to_month

    # Defaults
    upload_msg = ""
    loading_msg = ""
    marks = {i: m for i, m in enumerate(available_months)} if available_months else {}
    min_val = 0
    max_val = len(available_months) - 1 if available_months else 0

    if trigger_id == 'upload-db' and upload_contents is not None:
        import base64
        if ',' not in upload_contents:
            return "Upload failed: Invalid file contents.", dash.no_update, dash.no_update, dash.no_update, dash.no_update, dash.no_update, dash.no_update, dash.no_update
        content_type, content_string = upload_contents.split(',', 1)
        try:
            decoded = base64.b64decode(content_string)
        except Exception as e:
            return f"Upload failed: Decoding error: {str(e)}", dash.no_update, dash.no_update, dash.no_update, dash.no_update, dash.no_update, dash.no_update, dash.no_update

        with open(db_path, 'wb') as f:
            f.write(decoded)

        months = get_available_months()
        if not months:
            return "Upload failed: No valid data in file.", dash.no_update, dash.no_update, dash.no_update, dash.no_update, dash.no_update, dash.no_update, dash.no_update

        available_months = months
        month_to_index = {m: i for i, m in enumerate(available_months)}
        index_to_month = {i: m for i, m in enumerate(available_months)}

        min_val = 0
        max_val = len(available_months) - 1
        slider_val = [min_val, max_val]
        marks = {i: m for i, m in enumerate(available_months)}

        generate_map(available_months[0], available_months[0], default_map_path)
        map_src = f"/assets/default_map.html?ts={int(datetime.now(timezone.utc).timestamp())}"
        period_text = f"Showing: {available_months[0]} to {available_months[0]}"

        upload_msg = f"Uploaded '{upload_filename}' successfully."
        return upload_msg, marks, min_val, max_val, slider_val, map_src, period_text, loading_msg

    elif trigger_id == 'submit-button' and submit_n_clicks > 0:
        if not available_months:
            return dash.no_update, dash.no_update, dash.no_update, dash.no_update, dash.no_update, dash.no_update, "No data loaded.", dash.no_update

        for file in os.listdir(assets_folder):
            if file != "default_map.html":
                os.remove(os.path.join(assets_folder, file))

        start_month = index_to_month[slider_range[0]]
        end_month = index_to_month[slider_range[1]]

        generate_map(start_month, end_month, map_output_path)

        timestamp = int(datetime.now(timezone.utc).timestamp())
        map_src = f"/assets/temp_crime_map.html?ts={timestamp}"
        period_text = f"Showing: {start_month} to {end_month}"

        print(f"New map generated for period: {start_month} to {end_month}")

        return dash.no_update, dash.no_update, dash.no_update, dash.no_update, slider_range, map_src, period_text, ""

    else:
        slider_val = [0, len(available_months) - 1] if available_months else [0, 0]
        map_src = f"/assets/default_map.html?ts={int(datetime.now(timezone.utc).timestamp())}" if os.path.exists(default_map_path) else ""
        period_text = f"Showing: {available_months[0]} to {available_months[0]}" if available_months else "No data loaded."

        return dash.no_update, dash.no_update, dash.no_update, dash.no_update, slider_val, map_src, period_text, ""

@app.callback(
    Output("special-description", "style"),
    Output("special-description", "children"),
    Input("toggle-description", "n_clicks"),
    prevent_initial_call="initial_duplicate"
)
def toggle_special_description(n_clicks):
    if n_clicks % 2 == 1:
        return {"marginTop": "10px", "whiteSpace": "pre-wrap", "display": "block"}, load_special_ops_text()
    else:
        return {"display": "none"}, ""

if __name__ == "__main__":
    if not os.path.exists(default_map_path) and available_months:
        generate_map(available_months[0], available_months[0], default_map_path)
    app.run(debug=True, dev_tools_hot_reload=False)











