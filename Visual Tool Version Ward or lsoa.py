import dash
from dash import html, dcc
from dash.dependencies import Input, Output
import sqlite3
import pandas as pd
import folium
import fiona
from shapely.geometry import shape, Point
from pyproj import Transformer
from rtree import index
import os

#pip install dash pandas folium fiona shapely pyproj rtree numpy branca Jinja2

# Paths
db_path = r"C:\Users\20234289\OneDrive - TU Eindhoven\Q8\Data Challenge 2\crime_data.db"
shapefile_paths = {
    'wards': r"C:\Users\20234289\PycharmProjects\cbl\LSOA_and_Ward_files\London-wards-2018\London-wards-2018_ESRI\London_Ward.shp",
    'lsoa': r"C:\Users\20234289\PycharmProjects\cbl\LSOA_and_Ward_files\England_LSOA_2021\LSOA_2021_EW_BSC_V4.shp"
}

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


def load_crime_data(month="2023-07", crime_type="Burglary"):
    conn = sqlite3.connect(db_path)
    df = pd.read_sql_query(
        f"""
        SELECT crimeID, Month, Longitude, Latitude, Type, Outcome
        FROM crime
        WHERE Type = '{crime_type}' AND Month = '{month}'
          AND Longitude IS NOT NULL AND Latitude IS NOT NULL
        """, conn)
    conn.close()
    return df


def generate_map(mode='wards'):
    df = load_crime_data()
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
                crime_points.append((row['Latitude'], row['Longitude'], row['Type'], row['Outcome']))
                break

    m = folium.Map(location=[51.5074, -0.1278], zoom_start=10)
    for code, geom in features:
        count = counts.get(code, 0)
        folium.GeoJson(
            data=geom.__geo_interface__,
            tooltip=f"{mode.title()[:-1]}: {code}<br>Crimes: {count}",
            style_function=lambda feat: {'color': 'blue', 'weight': 2, 'opacity': 1, 'fillOpacity': 0.1}
        ).add_to(m)
    for lat, lon, ctype, outcome in crime_points:
        folium.CircleMarker(
            location=[lat, lon], radius=2, color='red', fill=True, fill_opacity=0.7,
            tooltip=f"{ctype} ({outcome})"
        ).add_to(m)
    return m

app = dash.Dash(__name__)
app.layout = html.Div([
    html.H1("London Crime Map - Burglary, July 2023"),
    dcc.Dropdown(
        id='mode-select',
        options=[
            {'label': 'Wards', 'value': 'wards'},
            {'label': 'LSOAs', 'value': 'lsoa'}
        ],
        value='wards',
        clearable=False
    ),
    html.Div(id='map-container')
])

@app.callback(
    Output('map-container', 'children'),
    Input('mode-select', 'value')
)
def update_map(mode):
    m = generate_map(mode)
    return html.Iframe(
        srcDoc=m.get_root().render(),
        style={'width': '100%', 'height': '700px', 'border': 'none'}
    )

if __name__ == '__main__':
    app.run(debug=True)
