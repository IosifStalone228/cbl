# Python 3.9 (or 3.10)

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
import json

# ── Paths ─────────────────────────────────────────────────────────────────────
db_path = "crime_data.db"

# Ward shapefile (with NAME and GSS_CODE in its properties)
shapefile_path = "LSOA_and_Ward_files/London-wards-2018/London-wards-2018_ESRI/London_Ward_CityMerged.shp"

# LSOA shapefile (with LSOA21CD and LSOA21NM in its properties)
lsoa_shapefile_path = "LSOA_and_Ward_files/England_LSOA_2021/LSOA_2021_EW_BSC_V4.shp"

assets_folder = "assets"
default_map_template = "map_{start}_to_{end}_{flag}.html"   # how we name each generated map
lsoa_json_filename = "lsoa_mapping.json"                    # always stored under assets/
lsoa_json_path = os.path.join(assets_folder, lsoa_json_filename)

# Ensure assets folder exists
os.makedirs(assets_folder, exist_ok=True)

# Transformer for British National Grid → WGS84
transformer = Transformer.from_crs("EPSG:27700", "EPSG:4326", always_xy=True)

# ── Helper: Reproject a GeoJSON‐style geometry dict from EPSG:27700 → EPSG:4326 ──
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

# ── Load months present in the SQLite “crime” table ────────────────────────────
def get_available_months():
    if not os.path.exists(db_path):
        return []
    conn = sqlite3.connect(db_path)
    df = pd.read_sql_query("SELECT DISTINCT Month FROM crime ORDER BY Month", conn)
    conn.close()
    return df["Month"].tolist()

# ── Build a map‐filename given parameters ──────────────────────────────────────
def build_map_filename(start_month, end_month, include_lsoa):
    """
    Returns a filename like:
      map_2022-03_to_2022-04_withLSOA.html
      or
      map_2022-03_to_2022-04_noLSOA.html
    """
    flag = "withLSOA" if include_lsoa else "noLSOA"
    filename = default_map_template.format(start=start_month, end=end_month, flag=flag)
    return os.path.join(assets_folder, filename)

# ── Main map‐generation routine ────────────────────────────────────────────────
def generate_map(start_month, end_month, output_path, include_lsoa=False):
    """
    Creates a Folium map, saving to output_path. Always draws:
      1. Ward boundaries (clickable → toggles that ward’s LSOAs).
      2. Burglary‐point markers (clustered).
      3. (Optional) A single GeoJson overlay of ALL London LSOAs (with tooltips),
         if include_lsoa=True.  **Limited to only those LSOAs intersecting wards.**
      4. JavaScript for per‐ward LSOA toggling (no tooltips on per‐ward).
    """

    # 1. Pull burglary crimes in the date range
    conn = sqlite3.connect(db_path)
    df = pd.read_sql_query(
        """
        SELECT crimeID, Month, Longitude, Latitude, Type, Outcome
        FROM crime
        WHERE Longitude BETWEEN -0.5103 AND 0.334
          AND Latitude BETWEEN 51.2868 AND 51.6919
          AND Type = 'Burglary'
          AND Month >= ? AND Month <= ?
        """,
        conn,
        params=(start_month, end_month),
    )
    conn.close()

    # 2. Load + index all wards (keep ward_code, ward_name, shapely‐geom)
    wards = []
    ward_index = index.Index()
    # Also build ward_code_to_name for reverse lookup
    ward_code_to_name = {}
    with fiona.open(shapefile_path) as shp:
        for i, feature in enumerate(shp):
            props = feature["properties"]
            ward_code = props["GSS_CODE"]
            ward_name = props["NAME"]
            geom = shape(reproject_geometry(feature["geometry"]))
            wards.append((ward_code, ward_name, geom))
            ward_code_to_name[ward_code] = ward_name
            ward_index.insert(i, geom.bounds)

    # 3. Load all LSOAs (we’ll later only keep those overlapping London wards)
    lsoas = []
    lsoa_index = index.Index()
    with fiona.open(lsoa_shapefile_path) as shp_lsoa:
        for i, feature in enumerate(shp_lsoa):
            props = feature["properties"]
            lsoa_code = props["LSOA21CD"]
            lsoa_name = props["LSOA21NM"]
            geom = shape(reproject_geometry(feature["geometry"]))
            lsoas.append((lsoa_code, lsoa_name, geom))
            lsoa_index.insert(i, geom.bounds)

    # 4. Build lsoa_by_ward: ward_code → list of GeoJSON‐style features (only LSOAs overlapping that ward by ≥ 30% of LSOA area)
    lsoa_by_ward = {}
    # Also build lsoa_code_to_ward_name: assign each LSOA to the first ward encountered
    lsoa_code_to_ward_name = {}
    for ward_code, ward_name, ward_geom in wards:
        included = []
        candidates = list(lsoa_index.intersection(ward_geom.bounds))
        for i in candidates:
            lsoa_code, lsoa_name, lsoa_geom = lsoas[i]
            if ward_geom.intersects(lsoa_geom):
                inter_area = ward_geom.intersection(lsoa_geom).area
                ratio = inter_area / lsoa_geom.area
                if ratio > 0.3:
                    included.append({
                        "type": "Feature",
                        "geometry": lsoa_geom.__geo_interface__,
                        "properties": {
                            "LSOA21CD": lsoa_code,
                            "LSOA21NM": lsoa_name
                        }
                    })
                    # If we haven't yet assigned this LSOA to any ward, do so now
                    if lsoa_code not in lsoa_code_to_ward_name:
                        lsoa_code_to_ward_name[lsoa_code] = ward_name
        lsoa_by_ward[ward_code] = included

    # 5. Save lsoa_by_ward → assets/lsoa_mapping.json
    with open(lsoa_json_path, "w") as f:
        json.dump(lsoa_by_ward, f)

    # 6. Compute crime‐counts per LSOA (for tooltips).
    #    We'll only assign crimes to LSOAs that actually intersect London:
    lsoa_crime_counts = {code: 0 for code, _, _ in lsoas}
    for _, row in df.iterrows():
        pt = Point(row["Longitude"], row["Latitude"])
        for idx in lsoa_index.intersection((pt.x, pt.y, pt.x, pt.y)):
            lsoa_code, lsoa_name, lsoa_geom = lsoas[idx]
            if lsoa_geom.contains(pt):
                lsoa_crime_counts[lsoa_code] += 1
                break

    # 7. Create base Folium map centered on London
    m = folium.Map(location=[51.5074, -0.1278], zoom_start=10, tiles="CartoDB positron")

    # 8. Assign each burglary record to its ward (for ward tooltips):
    ward_crime_counts = {code: 0 for code, _, _ in wards}
    crime_points_for_markers = []
    for _, row in df.iterrows():
        pt = Point(row["Longitude"], row["Latitude"])
        assigned = False
        for idx in ward_index.intersection((pt.x, pt.y, pt.x, pt.y)):
            ward_code, ward_name, ward_geom = wards[idx]
            if ward_geom.contains(pt):
                ward_crime_counts[ward_code] += 1
                assigned = True
                break
        if assigned:
            crime_points_for_markers.append((row["Latitude"], row["Longitude"], row["Type"], row["Outcome"]))

    # 9. Draw ward boundaries as clickable GeoJson (with a tooltip showing ward_name + burglary‐count)
    for ward_code, ward_name, ward_geom in wards:
        count = ward_crime_counts.get(ward_code, 0)
        gj = folium.GeoJson(
            data={
                "type": "Feature",
                "geometry": ward_geom.__geo_interface__,
                "properties": {
                    "ward": ward_code,
                    "ward_name": ward_name,
                    "count": count
                }
            },
            name=ward_code,
            tooltip=f"Ward: {ward_name}<br>Crimes: {count}",
            style_function=lambda feature: {
                'color': 'blue',
                'weight': 2,
                'opacity': 1,
                'fillOpacity': 0.05
            },
            highlight_function=lambda feature: {
                'color': 'white',
                'weight': 3,
                'fillOpacity': 0.1
            }
        )
        gj.add_to(m)

    # 10. (Optional) Static overlay: draw ALL London‐LSOAs (deduplicated) with tooltips
    if include_lsoa:
        seen = set()
        all_lsoa_features = []
        for ward_feats in lsoa_by_ward.values():
            for feat in ward_feats:
                code = feat["properties"]["LSOA21CD"]
                if code not in seen:
                    seen.add(code)
                    name = feat["properties"]["LSOA21NM"]
                    geometry = feat["geometry"]
                    count = lsoa_crime_counts.get(code, 0)
                    ward_name = lsoa_code_to_ward_name.get(code, "Unknown ward")
                    all_lsoa_features.append({
                        "type": "Feature",
                        "geometry": geometry,
                        "properties": {
                            "LSOA21CD": code,
                            "LSOA21NM": name,
                            "ward_name": ward_name,
                            "count": count
                        }
                    })

        if all_lsoa_features:
            collection = {
                "type": "FeatureCollection",
                "features": all_lsoa_features
            }
            geo = folium.GeoJson(
                data=collection,
                name="all_lsoas",
                style_function=lambda feature: {
                    'color': 'purple',
                    'weight': 1,
                    'fillOpacity': 0
                }
            )
            # Attach a tooltip that shows "LSOA21NM", "ward_name", and "count"
            tooltip = folium.features.GeoJsonTooltip(
                fields=["LSOA21NM", "ward_name", "count"],
                aliases=["LSOA name:", "Ward:", "Burglary count:"],
                localize=True,
                sticky=True
            )
            geo.add_child(tooltip)
            geo.add_to(m)

    # 11. Plot burglary points as red CircleMarkers (clustered)
    cluster = MarkerCluster().add_to(m)
    for lat, lon, crime_type, outcome in crime_points_for_markers:
        folium.CircleMarker(
            location=[lat, lon],
            radius=2,
            color='red',
            fill=True,
            fill_opacity=0.7,
            tooltip=f"{crime_type} ({outcome})"
        ).add_to(cluster)

    # 12. Inject JS for per‐ward LSOA toggling (no tooltips on per‐ward polygons)
    m.get_root().html.add_child(folium.Element(f"""
<script>
    let currentWard = null;
    let currentLayer = null;

    function toggleLSOAs(wardCode) {{
        fetch('/assets/{lsoa_json_filename}')
            .then(res => res.json())
            .then(data => {{
                if (!data[wardCode]) return;

                // If same ward clicked, remove its LSOA layer
                if (currentWard === wardCode && currentLayer) {{
                    window.map.removeLayer(currentLayer);
                    currentWard = null;
                    currentLayer = null;
                }} else {{
                    if (currentLayer) {{
                        window.map.removeLayer(currentLayer);
                    }}
                    const group = L.layerGroup();
                    data[wardCode].forEach(feature => {{
                        L.geoJSON(feature, {{
                            style: {{ color: 'violet', weight: 1, fillOpacity: 0 }}
                        }}).addTo(group);
                    }});
                    group.addTo(window.map);
                    currentWard = wardCode;
                    currentLayer = group;
                }}
            }});
    }}

    document.addEventListener('DOMContentLoaded', () => {{
        for (let key in window) {{
            if (window[key] instanceof L.Map) {{
                window.map = window[key];
                break;
            }}
        }}
        window.map.eachLayer(layer => {{
            if (layer.feature && layer.feature.properties && layer.feature.properties.ward) {{
                layer.on('click', () => {{
                    toggleLSOAs(layer.feature.properties.ward);
                }});
            }}
            if (layer.getPopup) {{
                layer.unbindPopup();
            }}
        }});
    }});
</script>
    """))

    # 13. Save to disk
    m.save(output_path)
    print(f"[INFO] Generated map ({'with' if include_lsoa else 'without'} static LSOA overlay) for: "
          f"{start_month} → {end_month}  →  {os.path.basename(output_path)}")

# ── (Unchanged) Load “special_ops.txt” for toggling description ─────────────────
def load_special_ops_text():
    try:
        with open("special_ops.txt", "r", encoding="utf-8") as f:
            return f.read()
    except Exception as e:
        return f"Could not load special_ops.txt: {e}"


# ── Initialize Dash app ───────────────────────────────────────────────────────
app = dash.Dash(__name__)

available_months = get_available_months()
month_to_index = {m: i for i, m in enumerate(available_months)}
index_to_month = {i: m for i, m in enumerate(available_months)}

# ── Layout ─────────────────────────────────────────────────────────────────────
app.layout = html.Div([
    html.H1("London Burglary Map - Select Time Period"),

    # 1) The date‐range slider
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

    # 2) “Show all LSOAs” checkbox
    dcc.Checklist(
        id="show-all-lsoa",
        options=[{"label": "Show all LSOAs", "value": "show"}],
        value=[],
        style={"marginTop": "20px"}
    ),

    html.Button("Submit", id="submit-button", n_clicks=0, style={"marginTop": "20px"}),

    # 3) Upload .db file
    dcc.Upload(
        id='upload-db',
        children=html.Button('Upload .db File', style={"marginTop": "20px"}),
        accept='.db',
        multiple=False,
    ),
    html.Div(id='upload-status', style={'marginTop': '10px', 'color': 'green'}),

    # 4) Special operation description toggle
    html.Button("Special Operation description", id="toggle-description", n_clicks=0, style={"marginTop": "20px"}),
    html.Div(id="special-description", style={"marginTop": "10px", "whiteSpace": "pre-wrap", "display": "none"}),

    # 5) Map time‐period text + the actual IFrame for Folium map
    html.Div(id="map-time-period", style={"marginTop": "20px", "fontWeight": "bold"}),
    dcc.Loading(
        id="loading-spinner",
        type="circle",
        children=[
            html.Div(id="loading-output"),
            html.Iframe(
                id="crime-map",
                src="",  # set by callback
                style={"height": "700px", "width": "100%", "border": "none"},
            )
        ]
    )
])

# ── Callback: Handle upload, slider, “show all LSOA” checkbox, and generate/load map ─────────────────────────
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
    Input('show-all-lsoa', 'value'),
    Input('submit-button', 'n_clicks'),
    State('upload-db', 'filename'),
    State('month-slider', 'value'),
)
def handle_all(upload_contents, show_all_lsoa_value, submit_n_clicks, upload_filename, slider_range):
    ctx = dash.callback_context
    trigger_id = ctx.triggered[0]['prop_id'].split('.')[0] if ctx.triggered else None

    global available_months, month_to_index, index_to_month

    include_lsoa = ("show" in show_all_lsoa_value)

    # Default outputs
    upload_msg = ""
    loading_msg = ""
    marks = {i: m for i, m in enumerate(available_months)} if available_months else {}
    min_val = 0
    max_val = len(available_months) - 1 if available_months else 0

    # ── Case 1: new .db uploaded ─────────────────────────────────────────────────
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

        # Build (or load) the default map for the first month
        first = available_months[0]
        default_filename = build_map_filename(first, first, include_lsoa)
        if not os.path.exists(default_filename):
            generate_map(first, first, default_filename, include_lsoa=include_lsoa)

        map_src = f"/assets/{os.path.basename(default_filename)}?ts={int(datetime.now(timezone.utc).timestamp())}"
        period_text = f"Showing: {first} to {first}"
        upload_msg = f"Uploaded '{upload_filename}' successfully."

        return (
            upload_msg, marks, min_val, max_val, slider_val,
            map_src, period_text, loading_msg
        )

    # ── Case 2: Submit clicked OR “Show all LSOA” toggled ─────────────────────────
    elif trigger_id in ('submit-button', 'show-all-lsoa') and submit_n_clicks >= 0:
        if not available_months:
            return (
                dash.no_update, dash.no_update, dash.no_update, dash.no_update,
                dash.no_update, dash.no_update, "No data loaded.", dash.no_update
            )

        # Determine which months to draw
        start_month = index_to_month[slider_range[0]]
        end_month = index_to_month[slider_range[1]]

        # Build the desired filename
        target_filename = build_map_filename(start_month, end_month, include_lsoa)

        # If it does not exist, generate it now
        if not os.path.exists(target_filename):
            generate_map(start_month, end_month, target_filename, include_lsoa=include_lsoa)

        # Serve from assets/ with timestamp to bust cache
        timestamp = int(datetime.now(timezone.utc).timestamp())
        map_src = f"/assets/{os.path.basename(target_filename)}?ts={timestamp}"
        period_text = f"Showing: {start_month} to {end_month}"

        return (
            dash.no_update, dash.no_update, dash.no_update, dash.no_update,
            slider_range, map_src, period_text, ""
        )

    # ── Fallback: show default (first‐month, no LSOA) ─────────────────────────────
    else:
        if available_months:
            first = available_months[0]
            default_filename = build_map_filename(first, first, include_lsoa=False)
            if not os.path.exists(default_filename):
                generate_map(first, first, default_filename, include_lsoa=False)
            map_src = f"/assets/{os.path.basename(default_filename)}?ts={int(datetime.now(timezone.utc).timestamp())}"
            period_text = f"Showing: {first} to {first}"
            slider_val = [0, len(available_months) - 1]
        else:
            map_src = ""
            period_text = "No data loaded."
            slider_val = [0, 0]

        return (
            dash.no_update, dash.no_update, dash.no_update, dash.no_update,
            slider_val, map_src, period_text, ""
        )

# ── Callback: Toggle “special_operations.txt” description (unchanged) ─────────────
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
    # On first start, if we have months but no default map, generate it
    if available_months:
        first = available_months[0]
        default_filename = build_map_filename(first, first, include_lsoa=False)
        if not os.path.exists(default_filename):
            generate_map(first, first, default_filename, include_lsoa=False)
    app.run(debug=True, dev_tools_hot_reload=False)
