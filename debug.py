import fiona
from shapely.geometry import shape
from pyproj import Transformer

# Path to the original shapefile (in EPSG:27700)
shapefile_path = "/Users/mateilaslau/Desktop/everything/UNI Documents/CBL/Mapping Files/London-wards-2018/London-wards-2018_ESRI/London_Ward_CityMerged.shp"

# Set up the transformer from EPSG:27700 (British National Grid) to EPSG:4326 (lat/lon)
transformer = Transformer.from_crs("EPSG:27700", "EPSG:4326", always_xy=True)

# Function to reproject geometry coordinates
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
                [[transformer.transform(x, y) for x, y in ring] for ring in polygon]
                for polygon in geom_dict["coordinates"]
            ]
        }
    else:
        raise ValueError(f"Unsupported geometry type: {geom_dict['type']}")

# Load shapefile and reproject the first feature
with fiona.open(shapefile_path) as shp:
    first = shp[0]
    print("Original (EPSG:27700):", first["geometry"])
    reprojected_geom = reproject_geometry(first["geometry"])
    print("Reprojected (EPSG:4326):", reprojected_geom)