import dash
from dash import html
import sqlite3
import pandas as pd
import folium
import fiona
from shapely.geometry import shape
from pyproj import Transformer
from rtree import index
import os
import json

# Paths
ward_shp = "/Users/mateilaslau/Desktop/everything/UNI Documents/CBL/Mapping Files/London-wards-2018/London-wards-2018_ESRI/London_Ward_CityMerged.shp"
lsoa_shp = "/Users/mateilaslau/Desktop/everything/UNI Documents/CBL/Mapping Files/England LSOA 2021/LSOA_2021_EW_BSC_V4.shp"
db_path = "/Users/mateilaslau/Desktop/everything/UNI Documents/CBL/crime_data.db"
output_path = os.path.join("assets", "interactive_crime_map.html")
os.makedirs("assets", exist_ok=True)

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

def load_crime():
    conn = sqlite3.connect(db_path)
    df = pd.read_sql_query("""
        SELECT crimeID, Month, Longitude, Latitude, Type, Outcome
        FROM crime
        WHERE Month = '2023-07' AND Type = 'Burglary'
        AND Longitude IS NOT NULL AND Latitude IS NOT NULL
        AND Longitude BETWEEN -0.5103 AND 0.334
        AND Latitude BETWEEN 51.2868 AND 51.6919
    """, conn)
    conn.close()
    print(f"{len(df)} crime records loaded.")
    return df

def load_shapes(path, name_field, code_field):
    shapes = []
    with fiona.open(path) as src:
        for feat in src:
            props = feat['properties']
            name = props.get(name_field)
            code = props.get(code_field)
            geom = shape(reproject_geometry(feat["geometry"])).simplify(0.0005, preserve_topology=True)
            shapes.append((code, name, geom))
    return shapes

def generate_map():
    crime_df = load_crime()
    wards = load_shapes(ward_shp, 'NAME', 'GSS_CODE')
    lsoas = load_shapes(lsoa_shp, 'LSOA21NM', 'LSOA21CD')

    m = folium.Map(location=[51.5074, -0.1278], zoom_start=10, tiles="CartoDB dark_matter")

    # Spatial index for LSOAs
    lsoa_idx = index.Index()
    lsoa_geoms = []
    for i, (code, name, geom) in enumerate(lsoas):
        lsoa_geoms.append((code, name, geom))
        lsoa_idx.insert(i, geom.bounds)

    # LSOAs per ward
    lsoa_by_ward = {}
    for ward_code, _, ward_geom in wards:
        included = []
        candidates = list(lsoa_idx.intersection(ward_geom.bounds))
        for i in candidates:
            lsoa_code, _, lsoa_geom = lsoa_geoms[i]
            if ward_geom.intersects(lsoa_geom):
                inter_area = ward_geom.intersection(lsoa_geom).area
                ratio = inter_area / lsoa_geom.area
                if ratio > 0.3:
                    included.append({
                        "type": "Feature",
                        "geometry": lsoa_geom.__geo_interface__,
                        "properties": {"LSOA21CD": lsoa_code}
                    })
        lsoa_by_ward[ward_code] = included

    with open("assets/lsoa_mapping.json", "w") as f:
        json.dump(lsoa_by_ward, f)

    for ward_code, _, ward_geom in wards:
        gj = folium.GeoJson(
            data={
                "type": "Feature",
                "geometry": ward_geom.__geo_interface__,
                "properties": {"ward": ward_code}
            },
            name=ward_code,
            tooltip=None,
            style_function=lambda feat: {
                "color": "deeppink",
                "weight": 2,
                "fillOpacity": 0.01
            },
            highlight_function=lambda feat: {
                "color": "white",
                "weight": 3,
                "fillOpacity": 0.1
            }
        )
        gj.add_to(m)

    for _, row in crime_df.iterrows():
        folium.CircleMarker(
            location=(row["Latitude"], row["Longitude"]),
            radius=2,
            color="red",
            fill=True,
            fill_opacity=0.6,
            tooltip=f"{row['Type']} - {row['Outcome']}"
        ).add_to(m)

    m.get_root().html.add_child(folium.Element("""
<script>
let currentWard = null;
let currentLayer = null;

function toggleLSOAs(wardCode) {
    fetch('/assets/lsoa_mapping.json')
        .then(res => res.json())
        .then(data => {
            if (!data[wardCode]) return;

            if (currentWard === wardCode && currentLayer) {
                window.map.removeLayer(currentLayer);
                currentWard = null;
                currentLayer = null;
            } else {
                if (currentLayer) {
                    window.map.removeLayer(currentLayer);
                }
                const group = L.layerGroup();
                data[wardCode].forEach(feature => {
                    L.geoJSON(feature, {
                        style: {
                            color: 'violet',
                            weight: 1,
                            fillOpacity: 0
                        }
                    }).addTo(group);
                });
                group.addTo(window.map);
                currentWard = wardCode;
                currentLayer = group;
            }
        });
}

document.addEventListener('DOMContentLoaded', function () {
    for (let key in window) {
        if (window[key] instanceof L.Map) {
            window.map = window[key];
            break;
        }
    }

    window.map.eachLayer(layer => {
        if (layer.feature && layer.feature.properties && layer.feature.properties.ward) {
            layer.on('click', function () {
                toggleLSOAs(layer.feature.properties.ward);
            });
        }
        if (layer.getPopup) {
            layer.unbindPopup();
        }
    });
});
</script>
    """))

    m.save(output_path)

generate_map()

app = dash.Dash(__name__)
app.title = "London Crime Map"
app.layout = html.Div([
    html.Iframe(
        src="/assets/interactive_crime_map.html",
        style={"height": "100vh", "width": "100%", "border": "none"}
    )
])

if __name__ == "__main__":
    app.run(debug=True)