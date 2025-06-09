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
db_path = "data_burglary.db"  # <-- Use the filtered database
shapefile_paths = {
   'wards': "LSOA_and_Ward_files/London-wards-2018/London-wards-2018_ESRI/London_Ward.shp",
   'lsoa': "LSOA_and_Ward_files/England_LSOA_2021/LSOA_2021_EW_BSC_V4.shp"
}
assets_folder = "assets"
default_map_path = os.path.join(assets_folder, "default_map.html")
map_output_path = os.path.join(assets_folder, "temp_crime_map.html")

# Ensure assets folder exists
os.makedirs(assets_folder, exist_ok=True)

def clean_assets_folder():
    """Remove all files in assets except for default_map.html."""
    for file in os.listdir(assets_folder):
        if file != "default_map.html":
            os.remove(os.path.join(assets_folder, file))

# Borough list for filtering LSOAs
london_boroughs = [
    'Barking and Dagenham', 'Barnet', 'Bexley', 'Brent', 'Bromley', 'Camden', 'Croydon', 'Ealing',
    'Enfield', 'Greenwich', 'Hackney', 'Hammersmith and Fulham', 'Haringey', 'Harrow', 'Havering',
    'Hillingdon', 'Hounslow', 'Islington', 'Kensington and Chelsea', 'Kingston upon Thames', 'Lambeth',
    'Lewisham', 'Merton', 'Newham', 'Redbridge', 'Richmond upon Thames', 'Southwark', 'Sutton',
    'Tower Hamlets', 'Waltham Forest', 'Wandsworth', 'Westminster'
]

# List of LSOA codes to exclude
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

# Clean up assets folder at startup
clean_assets_folder()

# Transformer: British National Grid -> WGS84
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

def generate_map(start_month, end_month, output_path, mode='wards'):
   conn = sqlite3.connect(db_path)
   df = pd.read_sql_query(
      """
      SELECT Month, Longitude, Latitude, Outcome
      FROM crime
      WHERE Longitude IS NOT NULL AND Latitude IS NOT NULL
      AND Month >= ? AND Month <= ?
      """,
      conn,
      params=(start_month, end_month),
   )
   conn.close()

   shp_path = shapefile_paths[mode]
   features = []
   spatial_idx = index.Index()
   feat_idx = 0
   with fiona.open(shp_path) as shp:
      for feat in shp:
         props = feat['properties']
         if mode == 'lsoa':
               name = props.get('LSOA21NM', '')
               code = props.get('LSOA21CD') or props.get('GSS_CODE')
               # filter by borough and drop unwanted LSOAs
               if not any(boro in name for boro in london_boroughs) or code in dropped_lsoas:
                  continue
         else:
               code = props.get('GSS_CODE')
         geom = shape(reproject_geometry(feat['geometry']))
         features.append((code, geom))
         spatial_idx.insert(feat_idx, geom.bounds)
         feat_idx += 1

   counts = {code: 0 for code, _ in features}
   crime_points = []
   for _, row in df.iterrows():
      pt = Point(row['Longitude'], row['Latitude'])
      for idx in spatial_idx.intersection((pt.x, pt.y, pt.x, pt.y)):
         code, geom = features[idx]
         if geom.contains(pt):
               counts[code] += 1
               crime_points.append((row['Latitude'], row['Longitude'], row['Outcome']))
               break

   m = folium.Map(location=[51.5074, -0.1278], zoom_start=10)
   for code, geom in features:
      count = counts.get(code, 0)
      display_name = {"wards": "Ward", "lsoa": "LSOA"}[mode]
      folium.GeoJson(
         data=geom.__geo_interface__,
         tooltip=f"{mode.title()[:-1]}: {code}<br>Crimes: {count}",
         style_function=lambda feat: {'color': 'blue', 'weight': 2, 'opacity': 1, 'fillOpacity': 0.1}
      ).add_to(m)
   #return m

   # Add marker cluster for crime points
   cluster = MarkerCluster().add_to(m)
   for lat, lon, outcome in crime_points:
      folium.CircleMarker(
         location=[lat, lon],
         radius=2,
         color='red',
         fill=True,
         fill_opacity=0.7,
         tooltip=f"Burglary ({outcome})"
      ).add_to(cluster)

   m.save(output_path)
   print(f"[Generated] map for period: {start_month} to {end_month} (mode: {mode})")
   return m

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
   # Month Slider
   dcc.RangeSlider(
      id="month-slider",
      min=0,
      max=len(available_months) - 1 if available_months else 0,
      value=[0, 0] if available_months else [0, 0],  # <-- Only the first month selected
      marks={i: m for i, m in enumerate(available_months)},
      step=None,
      allowCross=False,
      tooltip={"placement": "bottom", "always_visible": True},
   ),
   html.Button("Submit", id="submit-button", n_clicks=0, style={"marginTop": "20px"}),
   # Select map mode
   dcc.Dropdown(
      id='mode-select',
      options=[
         {'label': 'Wards', 'value': 'wards'},
         {'label': 'LSOAs', 'value': 'lsoa'}
      ],
      value='wards',
      clearable=False
   ),
   # Upload .db file
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
   Input('mode-select', 'value'),  # FIXED property name
   Input('upload-db', 'contents'),
   State('upload-db', 'filename'),
   Input('submit-button', 'n_clicks'),
   State('month-slider', 'value')
)
def handle_all(mode_value, upload_contents, upload_filename, submit_n_clicks, slider_range):
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

      generate_map(available_months[0], available_months[0], default_map_path, mode=mode_value)
      map_src = f"/assets/default_map.html?ts={int(datetime.now(timezone.utc).timestamp())}"
      period_text = f"Showing: {available_months[0]} to {available_months[0]}"

      upload_msg = f"Uploaded '{upload_filename}' successfully."
      return upload_msg, marks, min_val, max_val, slider_val, map_src, period_text, loading_msg

   elif trigger_id == 'submit-button' and submit_n_clicks > 0:
      if not available_months:
         return dash.no_update, dash.no_update, dash.no_update, dash.no_update, dash.no_update, dash.no_update, "No data loaded.", dash.no_update

      clean_assets_folder()  # <-- Clean up before generating a new map

      start_month = index_to_month[slider_range[0]]
      end_month = index_to_month[slider_range[1]]

      print(f"[Generating] map for period: {start_month} to {end_month} (mode: {mode_value})")  # <-- Print before generating

      generate_map(start_month, end_month, map_output_path, mode=mode_value)

      timestamp = int(datetime.now(timezone.utc).timestamp())
      map_src = f"/assets/temp_crime_map.html?ts={timestamp}"
      period_text = f"Showing: {start_month} to {end_month}"

      return dash.no_update, dash.no_update, dash.no_update, dash.no_update, slider_range, map_src, period_text, ""

   elif trigger_id == 'mode-select':
      if not available_months:
         return dash.no_update, dash.no_update, dash.no_update, dash.no_update, dash.no_update, dash.no_update, "No data loaded.", dash.no_update

      clean_assets_folder()  # Clean up before generating a new map

      start_month = index_to_month[slider_range[0]]
      end_month = index_to_month[slider_range[1]]

      print(f"[Generating] map for period: {start_month} to {end_month} (mode: {mode_value})")  # <-- Print before generating

      generate_map(start_month, end_month, map_output_path, mode=mode_value)  # <-- Use temp_crime_map.html
      map_src = f"/assets/temp_crime_map.html?ts={int(datetime.now(timezone.utc).timestamp())}"
      period_text = f"Showing: {start_month} to {end_month}"

      # Return exactly 8 values
      return dash.no_update, dash.no_update, dash.no_update, dash.no_update, slider_range, map_src, period_text, ""

   else:
      slider_val = [0, 0] if available_months else [0, 0]  # <-- Only the first month selected
      map_src = f"/assets/default_map.html?ts={int(datetime.now(timezone.utc).timestamp())}" if os.path.exists(default_map_path) else ""
      period_text = f"Showing: {available_months[0]} to {available_months[0]}" if available_months else "No data loaded."

      # Return exactly 8 values
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










