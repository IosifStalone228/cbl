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

# ── Paths ─────────────────────────────────────────────────────────────────────
db_path = "crime_data.db"

# Ward shapefile (with NAME and GSS_CODE in its properties)
shapefile_path = "LSOA_and_Ward_files/London-wards-2018/London-wards-2018_ESRI/London_Ward_CityMerged.shp"

# LSOA shapefile (with LSOA21CD and LSOA21NM in its properties)
lsoa_shapefile_path = "LSOA_and_Ward_files/England_LSOA_2021/LSOA_2021_EW_BSC_V4.shp"

assets_folder = "assets"
default_map_path = os.path.join(assets_folder, "default_map.html")
map_output_path = os.path.join(assets_folder, "temp_crime_map.html")

# Ensure assets folder exists
os.makedirs(assets_folder, exist_ok=True)

# Clean up assets folder on startup except for default_map.html
for file in os.listdir(assets_folder):
    if file != "default_map.html":
        os.remove(os.path.join(assets_folder, file))

# Transformer for British National Grid → WGS84
transformer = Transformer.from_crs("EPSG:27700", "EPSG:4326", always_xy=True)

# ── Helper to reproject any Polygon or MultiPolygon from EPSG:27700 → EPSG:4326 ──
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
                [[
                    transformer.transform(x, y)
                    for x, y in ring
                ] for ring in part]
                for part in geom_dict["coordinates"]
            ],
        }
    else:
        raise ValueError("Unsupported geometry type.")

# ── List of all London borough names for filtering LSOA21NM ────────────────────
LONDON_BOROUGHS = [
    "Barking and Dagenham", "Barnet", "Bexley", "Brent", "Bromley", "Camden",
    "Croydon", "Ealing", "Enfield", "Greenwich", "Hackney", "Hammersmith and Fulham",
    "Haringey", "Harrow", "Havering", "Hillingdon", "Hounslow", "Islington",
    "Kensington and Chelsea", "Kingston upon Thames", "Lambeth", "Lewisham",
    "Merton", "Newham", "Redbridge", "Richmond upon Thames", "Southwark",
    "Sutton", "Tower Hamlets", "Waltham Forest", "Wandsworth", "Westminster"
]

# ── Set of LSOA codes to drop entirely ────────────────────────────────────────
dropped_lsoas = {
    'E01021443', 'E01021468', 'E01021473', 'E01021446', 'E01021445', 'E01021435',
    'E01021474', 'E01021475', 'E01021472', 'E01033063', 'E01021438', 'E01021464',
    'E01021461', 'E01021431', 'E01021434', 'E01034359', 'E01034361', 'E01033064',
    'E01034360', 'E01034358', 'E010214__', 'E01021432', 'E01021433', 'E01021466',
    'E01021456', 'E01021454', 'E01021437', 'E01021447', 'E01021449', 'E01021448',
    'E01021450', 'E01021451', 'E01021453', 'E01021455', 'E01021465', 'E01021467',
    'E01021452', 'E01021460', 'E01021462', 'E01021463', 'E01021444', 'E01021442',
    'E01021469', 'E01021471', 'E01021441', 'E01021470', 'E01021457', 'E01021458',
    'E01021459'
}

# ── Load months present in the SQLite “crime” table ────────────────────────────
def get_available_months():
    if not os.path.exists(db_path):
        return []
    conn = sqlite3.connect(db_path)
    df = pd.read_sql_query("SELECT DISTINCT Month FROM crime ORDER BY Month", conn)
    conn.close()
    return df["Month"].tolist()

# ── Main map‐generation routine (now with an extra flag `include_lsoa`) ─────────
def generate_map(start_month, end_month, output_path, include_lsoa=False):
    """
    Creates a Folium map, saving to output_path. Always draws ward boundaries and burglary points.
    If include_lsoa=True, it also draws filtered LSOA polygons on top.
    """
    # 1. Pull burglary crimes in the date range
    conn = sqlite3.connect(db_path)
    df = pd.read_sql_query(
        """
        SELECT crimeID, Month, Longitude, Latitude, Type, Outcome
        FROM crime
        WHERE Longitude IS NOT NULL
          AND Latitude IS NOT NULL
          AND Type = 'Burglary'
          AND Month >= ? AND Month <= ?
        """,
        conn,
        params=(start_month, end_month),
    )
    conn.close()

    # 2. Load and index wards (now storing both code and name)
    wards = []
    ward_index = index.Index()
    with fiona.open(shapefile_path) as shp:
        for i, feature in enumerate(shp):
            props = feature["properties"]
            ward_code = props["GSS_CODE"]
            ward_name = props["NAME"]           # <— grab the NAME field
            geom = shape(reproject_geometry(feature["geometry"]))
            wards.append((ward_code, ward_name, geom))
            ward_index.insert(i, geom.bounds)

    # Initialize a count dict for ward‐level burglary counts (keyed by code as before)
    ward_crime_counts = {code: 0 for code, _, _ in wards}
    crime_points = []  # will hold (lat, lon, Type, Outcome) for all burglary points

    # 3. If include_lsoa=True, prepare LSOA structures (fixed index logic, storing code+name)
    lsoas = []
    lsoa_index = None
    lsoa_crime_counts = {}
    if include_lsoa:
        lsoas = []
        lsoa_index = index.Index()
        kept_idx = 0  # This counter matches positions in `lsoas`

        with fiona.open(lsoa_shapefile_path) as shp_lsoa:
            for feature in shp_lsoa:
                props = feature["properties"]
                lsoa_code = props["LSOA21CD"]
                lsoa_name = props["LSOA21NM"]

                # a) Skip any LSOA whose code is in dropped_lsoas
                if lsoa_code in dropped_lsoas:
                    continue

                # b) Keep only those LSOAs whose LSOA21NM string contains a London borough
                if not any(borough in lsoa_name for borough in LONDON_BOROUGHS):
                    continue

                # c) Reproject geometry and append (code, name, geom)
                geom = shape(reproject_geometry(feature["geometry"]))
                lsoas.append((lsoa_code, lsoa_name, geom))

                # d) Insert into the R‐tree under the index = kept_idx
                lsoa_index.insert(kept_idx, geom.bounds)
                kept_idx += 1

        # Initialize counts for each kept LSOA
        lsoa_crime_counts = {code: 0 for code, _, _ in lsoas}

    # 4. Loop through burglary records, assign each point to its ward (and LSOA, if toggled)
    for _, row in df.iterrows():
        pt = Point(row["Longitude"], row["Latitude"])
        assigned = False

        # a) First assign to ward
        for idx in ward_index.intersection((pt.x, pt.y, pt.x, pt.y)):
            ward_code, ward_name, ward_geom = wards[idx]
            if ward_geom.contains(pt):
                ward_crime_counts[ward_code] += 1
                assigned = True
                break

        # b) Then, if we also want LSOA‐level counts
        if include_lsoa:
            for idx in lsoa_index.intersection((pt.x, pt.y, pt.x, pt.y)):
                lsoa_code, lsoa_name, lsoa_geom = lsoas[idx]
                if lsoa_geom.contains(pt):
                    lsoa_crime_counts[lsoa_code] += 1
                    break

        # Regardless, we want to plot a circle marker for each burglary
        if assigned:
            crime_points.append((row["Latitude"], row["Longitude"], row["Type"], row["Outcome"]))

    # 5. Create a base Folium map centered on London
    m = folium.Map(location=[51.5074, -0.1278], zoom_start=10, tiles="CartoDB positron")

    # 6. Draw ward boundaries (using ward_name in tooltip, crime count from ward_code)
    for ward_code, ward_name, ward_geom in wards:
        count = ward_crime_counts.get(ward_code, 0)
        folium.GeoJson(
            data=ward_geom.__geo_interface__,
            tooltip=f"Ward: {ward_name}<br>Crimes: {count}",  # <— use ward_name here
            style_function=lambda feature: {
                'color': 'blue',
                'weight': 2,
                'opacity': 1,
                'fillOpacity': 0.05
            }
        ).add_to(m)

    # 7. If include_lsoa=True, draw LSOA polygons (using lsoa_name in tooltip)
    if include_lsoa:
        for lsoa_code, lsoa_name, lsoa_geom in lsoas:
            count = lsoa_crime_counts.get(lsoa_code, 0)
            folium.GeoJson(
                data=lsoa_geom.__geo_interface__,
                tooltip=f"LSOA: {lsoa_name}<br>Crimes: {count}",  # <— use lsoa_name here
                style_function=lambda feature: {
                    'color': 'green',
                    'weight': 1.5,
                    'opacity': 0.7,
                    'fillOpacity': 0.1
                }
            ).add_to(m)

    # 8. Plot all burglary points as red circles (same as before)
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

    # 9. Save to the specified output_path
    m.save(output_path)
    print(f"[INFO] Generated map {('with LSOA' if include_lsoa else '')} for: {start_month} → {end_month}")


# ── (Unchanged) Load “special_ops.txt” for toggling description ─────────────────
def load_special_ops_text():
    try:
        with open("special_ops.txt", "r", encoding="utf-8") as f:
            return f.read()
    except Exception as e:
        return f"Could not load special_ops.txt: {e}"

# ── Initialize Dash app ────────────────────────────────────────────────────────
app = dash.Dash(__name__)

available_months = get_available_months()
month_to_index = {m: i for i, m in enumerate(available_months)}
index_to_month = {i: m for i, m in enumerate(available_months)}

# ── Layout ─────────────────────────────────────────────────────────────────────
app.layout = html.Div([
    html.H1("London Burglary Map - Select Time Period"),

    # 1) The date‐range slider (same as before)
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

    # 2) Upload .db file (unchanged)
    dcc.Upload(
        id='upload-db',
        children=html.Button('Upload .db File', style={"marginTop": "20px"}),
        accept='.db',
        multiple=False,
    ),
    html.Div(id='upload-status', style={'marginTop': '10px', 'color': 'green'}),

    # ── LSOA Toggle ─────────────────────────────────────────────────────────────
    html.Div(
        [
            dcc.Checklist(
                id="lsoa-toggle",
                options=[{"label": "Show LSOA‐level", "value": "show"}],
                value=[],  # start with LSOA off
                style={"marginTop": "20px", "marginBottom": "10px"}
            ),
            html.Small("Toggle on to overlay LSOA boundaries (filtered to London boroughs)."),
        ],
        style={"marginTop": "10px"}
    ),

    # 3) Special operation description toggle (unchanged)
    html.Button("Special Operation description", id="toggle-description", n_clicks=0, style={"marginTop": "20px"}),
    html.Div(id="special-description", style={"marginTop": "10px", "whiteSpace": "pre-wrap", "display": "none"}),

    # 4) Map time‐period text + the actual IFrame for Folium map (unchanged)
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


# ── Callback: Handle upload, slider, and LSOA‐toggle ─────────────────────────────
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
    State('month-slider', 'value'),
    Input('lsoa-toggle', 'value'),  # ← LSOA on/off toggle
)
def handle_all(upload_contents, upload_filename, submit_n_clicks, slider_range, lsoa_toggle):
    """
    Responds to:
      - Uploading a new SQLite crime DB
      - Clicking “Submit” to regenerate
      - Toggling the LSOA checkbox
    """
    ctx = dash.callback_context
    trigger_id = ctx.triggered[0]['prop_id'].split('.')[0] if ctx.triggered else None

    global available_months, month_to_index, index_to_month

    # Default values (for marks, slider bounds, etc.)
    upload_msg = ""
    loading_msg = ""
    marks = {i: m for i, m in enumerate(available_months)} if available_months else {}
    min_val = 0
    max_val = len(available_months) - 1 if available_months else 0

    # Determine whether LSOA‐toggle is “on”
    include_lsoa = ("show" in lsoa_toggle)

    # ── Case 1: A new .db is uploaded ───────────────────────────────────────────
    if trigger_id == 'upload-db' and upload_contents is not None:
        import base64
        if ',' not in upload_contents:
            return (
                "Upload failed: Invalid file contents.",
                dash.no_update, dash.no_update, dash.no_update, dash.no_update,
                dash.no_update, dash.no_update, dash.no_update
            )
        content_type, content_string = upload_contents.split(',', 1)
        try:
            decoded = base64.b64decode(content_string)
        except Exception as e:
            return (
                f"Upload failed: Decoding error: {str(e)}",
                dash.no_update, dash.no_update, dash.no_update, dash.no_update,
                dash.no_update, dash.no_update, dash.no_update
            )

        # Overwrite local crime_data.db
        with open(db_path, 'wb') as f:
            f.write(decoded)

        # Refresh available months
        months = get_available_months()
        if not months:
            return (
                "Upload failed: No valid data in file.",
                dash.no_update, dash.no_update, dash.no_update, dash.no_update,
                dash.no_update, dash.no_update, dash.no_update
            )

        available_months = months
        month_to_index = {m: i for i, m in enumerate(available_months)}
        index_to_month = {i: m for i, m in enumerate(available_months)}

        min_val = 0
        max_val = len(available_months) - 1
        slider_val = [min_val, max_val]
        marks = {i: m for i, m in enumerate(available_months)}

        # Generate a “default” map for the first month in the new DB (no LSOA overlay by default)
        generate_map(available_months[0], available_months[0], default_map_path, include_lsoa=False)
        map_src = f"/assets/default_map.html?ts={int(datetime.now(timezone.utc).timestamp())}"
        period_text = f"Showing: {available_months[0]} to {available_months[0]}"

        upload_msg = f"Uploaded '{upload_filename}' successfully."
        return (
            upload_msg, marks, min_val, max_val, slider_val,
            map_src, period_text, loading_msg
        )

    # ── Case 2: User clicked “Submit” OR toggled LSOA checkbox ───────────────────
    elif (trigger_id == 'submit-button' and submit_n_clicks > 0) or (trigger_id == 'lsoa-toggle'):
        if not available_months:
            return (
                dash.no_update, dash.no_update, dash.no_update, dash.no_update,
                dash.no_update, dash.no_update, "No data loaded.", dash.no_update
            )

        # Clean out old temporary map files (except default_map.html)
        for file in os.listdir(assets_folder):
            if file != "default_map.html":
                os.remove(os.path.join(assets_folder, file))

        # Figure out which months to draw
        start_month = index_to_month[slider_range[0]]
        end_month = index_to_month[slider_range[1]]

        # Re‐generate the map, passing include_lsoa based on the toggle
        generate_map(start_month, end_month, map_output_path, include_lsoa=include_lsoa)

        timestamp = int(datetime.now(timezone.utc).timestamp())
        map_src = f"/assets/temp_crime_map.html?ts={timestamp}"
        period_text = f"Showing: {start_month} to {end_month} " + \
                      ("(LSOA ON)" if include_lsoa else "(LSOA OFF)")

        return (
            dash.no_update, dash.no_update, dash.no_update, dash.no_update,
            slider_range, map_src, period_text, ""
        )

    # ── Fallback: just display the default map (first‐month, no overlay) ─────────
    else:
        slider_val = [0, len(available_months) - 1] if available_months else [0, 0]
        map_src = f"/assets/default_map.html?ts={int(datetime.now(timezone.utc).timestamp())}" \
                  if os.path.exists(default_map_path) else ""
        period_text = f"Showing: {available_months[0]} to {available_months[0]}" if available_months else "No data loaded."
        return (
            dash.no_update, dash.no_update, dash.no_update, dash.no_update,
            slider_val, map_src, period_text, ""
        )


# ── Callback: Toggle description for “special_operations.txt” (unchanged) ─────
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


# ── Run the app ────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    # On first start, if default_map.html doesn’t exist but we have months, create it
    if not os.path.exists(default_map_path) and available_months:
        generate_map(available_months[0], available_months[0], default_map_path, include_lsoa=False)
    app.run(debug=True, dev_tools_hot_reload=False)
