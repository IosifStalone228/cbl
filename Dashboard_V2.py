import os
import json
import sqlite3
import pandas as pd
import folium
import fiona
from shapely.geometry import shape, Point
from pyproj import Transformer
from rtree import index
import dash
from dash import html

# ─── CONFIG ───────────────────────────────────────────────────────
ward_shp    = "LSOA_and_Ward_files/London-wards-2018/London-wards-2018_ESRI/London_Ward_CityMerged.shp"
lsoa_shp    = "LSOA_and_Ward_files/England_LSOA_2021/LSOA_2021_EW_BSC_V4.shp"
crime_db    = "data_burglary.db"
alloc_db    = "police_allocation.db"
output_path = os.path.join("assets", "interactive_crime_map.html")
os.makedirs("assets", exist_ok=True)

# ─── PROJECTION ──────────────────────────────────────────────────
TRANS = Transformer.from_crs("EPSG:27700", "EPSG:4326", always_xy=True)
def reproject_geom(g):
    if g["type"] == "Polygon":
        return {
            "type":"Polygon",
            "coordinates":[
                [[*TRANS.transform(x,y)] for x,y in ring]
                for ring in g["coordinates"]
            ]
        }
    else:
        return {
            "type":"MultiPolygon",
            "coordinates":[
                [[[ *TRANS.transform(x,y)] for x,y in ring] for ring in part]
                for part in g["coordinates"]
            ]
        }

# ─── LOAD DATA ───────────────────────────────────────────────────
def load_crime():
    with sqlite3.connect(crime_db) as conn:
        return pd.read_sql_query(
            "SELECT Month, Longitude, Latitude FROM crime "
            "WHERE Longitude IS NOT NULL AND Latitude IS NOT NULL",
            conn
        )

def load_allocations():
    with sqlite3.connect(alloc_db) as conn:
        return pd.read_sql_query(
            "SELECT WD24CD AS WardCode, Longitude, Latitude, "
            "allocated_hours, reinforcement_hours FROM allocation "
            "WHERE Longitude IS NOT NULL AND Latitude IS NOT NULL",
            conn
        )

# ─── LOAD & INDEX SHAPES ─────────────────────────────────────────
def load_shapes(path, code_field):
    items, idx = [], index.Index()
    with fiona.open(path) as src:
        for i, feat in enumerate(src):
            code = feat["properties"][code_field]
            geom = shape(reproject_geom(feat["geometry"])).simplify(0.0005, preserve_topology=True)
            items.append((code, geom))
            idx.insert(i, geom.bounds)
    return items, idx

# ─── PRECOMPUTE MAP DATA ─────────────────────────────────────────
crime_df    = load_crime()
alloc_df    = load_allocations()
wards, widx = load_shapes(ward_shp, "GSS_CODE")
lsoas, lidx = load_shapes(lsoa_shp, "LSOA21CD")

# group burglary points by ward & month
crime_by_ward = {}
for m, lon, lat in zip(crime_df.Month, crime_df.Longitude, crime_df.Latitude):
    pt = Point(lon, lat)
    for j in widx.intersection((lon, lat, lon, lat)):
        wcode, wgeom = wards[j]
        if wgeom.contains(pt):
            crime_by_ward.setdefault(wcode, {}).setdefault(m, []).append([lat, lon])
            break

# group allocation points by ward (with hours & reinforcement)
alloc_by_ward = {}
for wcode, lon, lat, hrs, rin in zip(
    alloc_df.WardCode, alloc_df.Longitude, alloc_df.Latitude,
    alloc_df.allocated_hours, alloc_df.reinforcement_hours):
    pt = Point(lon, lat)
    for j in widx.intersection((lon, lat, lon, lat)):
        wc, wg = wards[j]
        if wg.contains(pt):
            alloc_by_ward.setdefault(wc, []).append({
                "lat": lat, "lon": lon,
                "hours": hrs, "reinf": rin
            })
            break

# compute LSOA outlines per ward
lsoa_by_ward = {}
for wcode, wgeom in wards:
    feats = []
    for j in lidx.intersection(wgeom.bounds):
        lcode, lgeom = lsoas[j]
        if wgeom.intersects(lgeom) and (wgeom.intersection(lgeom).area / lgeom.area) > 0.3:
            feats.append({
                "type":"Feature",
                "geometry": lgeom._geo_interface_,
                "properties":{"LSOA21CD":lcode}
            })
    lsoa_by_ward[wcode] = feats

# ─── BUILD BASE MAP ─────────────────────────────────────────────
m = folium.Map([51.5074, -0.1278], zoom_start=10, tiles="CartoDB dark_matter")
wards_geojson = {
    "type":"FeatureCollection",
    "features":[
        {"type":"Feature","properties":{"ward":wcode},"geometry":wgeom._geo_interface_}
        for wcode, wgeom in wards
    ]
}

# serialize JSON
wards_json = json.dumps(wards_geojson)
lsoa_json  = json.dumps(lsoa_by_ward)
crime_json = json.dumps(crime_by_ward)
alloc_json = json.dumps(alloc_by_ward)

# inject JS + CSS
js = f"""
<script>
window.onload = function() {{
  const map = Object.values(window).find(v=>v instanceof L.Map);
  if(!map) return;

  const wardsData   = {wards_json},
        lsoaByWard  = {lsoa_json},
        crimeByWard = {crime_json},
        allocByWard = {alloc_json};
  let lsoaLayers={{}}, pointLayers={{}}, mode='crime', lsoaAll, allocAllLayer;

  // control panel
  const ctrl = L.DomUtil.create('div','month-range-control leaflet-bar');
  ctrl.innerHTML = `
    <label>From:<input type="month" id="fromMonth" min="2021-01" value="2023-07"></label>
    <label>To:<input type="month" id="toMonth"   min="2021-01" value="2023-08"></label>
    <button id="toggleAll"  class="clear-btn">Toggle</button>
    <button id="switchMode" class="clear-btn">Show Alloc</button>
    <button id="infoBtn"    class="clear-btn">Info</button>
  `;
  L.DomEvent.disableClickPropagation(ctrl);
  Object.assign(ctrl.style,{{position:'absolute',top:'10px',left:'40px',zIndex:1000}});
  map.getContainer().appendChild(ctrl);

  const toggleAllBtn = document.getElementById('toggleAll'),
        switchModeBtn= document.getElementById('switchMode'),
        infoBtn      = document.getElementById('infoBtn');

  toggleAllBtn.onclick = () => {{
    if(!Object.keys(lsoaLayers).length)
      wardsData.features.forEach(f=>toggleWard(f.properties.ward));
    else
      Object.keys(lsoaLayers).forEach(c=>clearWard(c));
  }};

  switchModeBtn.onclick = function() {{
    mode = (mode==='crime'?'alloc':'crime');
    this.textContent = (mode==='crime'?'Show Alloc':'Show Crime');

    if(mode==='alloc') {{
      Object.keys(pointLayers).forEach(c=>clearWard(c));
      lsoaAll = L.geoJSON(
        {{type:'FeatureCollection',features:[].concat(...Object.values(lsoaByWard))}},
        {{style:{{color:'white',weight:1.5,fillOpacity:0.05}}}}
      ).addTo(map);
      allocAllLayer = L.layerGroup();
      Object.values(allocByWard).flat().forEach(p=>{{
        const marker = L.circleMarker([p.lat,p.lon],{{radius:5,color:'blue',fillOpacity:0.6}}).addTo(allocAllLayer);
        marker.bindTooltip(A:${{p.hours}} hrs, R:${{p.reinf}} hrs, {{direction:'top',offset:[0,-2],className:'alloc-tooltip'}});
        marker.on('mouseover',()=>marker.openTooltip());
        marker.on('mouseout',()=>marker.closeTooltip());
      }});
      allocAllLayer.addTo(map);
      wardsLayer.off('click');
    }} else {{
      if(lsoaAll) map.removeLayer(lsoaAll);
      if(allocAllLayer) map.removeLayer(allocAllLayer);
      Object.keys(pointLayers).forEach(c=>clearWard(c));
      wardsLayer.eachLayer(layer=>{{
        const c = layer.feature.properties.ward;
        layer.off();
        layer.bindTooltip(c,{{sticky:true,className:'ward-tooltip'}});
        layer.on('click',()=>toggleWard(c));
        layer.on('mouseover',()=>layer.setStyle({{color:'white',weight:3,fillOpacity:0.1}}));
        layer.on('mouseout',()=>layer.setStyle({{color:'deeppink',weight:2,fillOpacity:0.01}}));
      }});
    }}
  }};

  // Info sidebar
  const sidebar = L.DomUtil.create('div','info-sidebar leaflet-bar');
  sidebar.innerHTML = `
    <div class="info-pages">
      <div class="page active" data-page="0">Visible Police Patrol Operations:Operations in the form of aggressive deployment of numerous uniformed officers to patrol a high-crime ward for an entire day. The primary goal is to maintain a high-level visible police presence that serves as a direct deterrent against possible offenders. Visibility by police has been shown to reduce crime levels based on fear of being caught. These activities are especially effective during peak activity times of burglary risk, such as times of seasonal peaks or hot spot neighborhoods.</div>
      <div class="page" data-page="1">Surveillance Operations: hese are designed to monitor burglary hotspots less explicitly. They include the use of covert police officers, mobile CCTV cameras, and unmarked vehicles to gather intelligence, watch out for suspicious activity, and apprehend burglars in the act. These operations are particularly worthwhile in areas where there are multiple instances of burglaries or where the offenders are seen to be operating on unique patterns. Surveillance operations are also utilized to gather useful intelligence for the purposes of investigation and ensuing risk assessment..</div>
      <div class="page" data-page="2">Burglary Prevention Operations: Prevention-focused operations go proactive by engaging directly with residents of high-risk areas. Police officers go door-to-door, offering home security surveys, installing simple alarms or locks, and offering crime prevention packs. Such operations aim to enable residents to make their homes secure against burglary and instill a community watch mindset. Additionally, they serve to boost police-community relations, and this can contribute to enhanced cooperation and reporting.</div>
      <div class="page" data-page="3">Burglary Root Operations: These operations address the root social and economic causes of burglary. Police collaborate with housing departments in the local area, youth services, educational authorities, and social services to address vulnerable individuals and families. The focus is on prevention over the long term, addressing factors like disengagement of young people, drug abuse, unemployment, and poor housing. Intervening early and supplementing social services, these operations work towards preventing the circumstances causing burglary.  </div>
    </div>
    <button id="sidebarClose" class="clear-btn">Close</button>
    <button id="sidebarPrev"  class="clear-btn">Prev</button>
    <button id="sidebarNext"  class="clear-btn">Next</button>
  `;
  Object.assign(sidebar.style, {{
    position:'absolute', top:'60px', right:'10px',
    width:'300px', maxHeight:'400px', overflowY:'auto',
    background:'rgba(0,0,0,0.8)', color:'#fff', padding:'10px',
    borderRadius:'5px', zIndex:1100, display:'none'
  }});
  map.getContainer().appendChild(sidebar);
  const pages = sidebar.querySelectorAll('.page');
  let currentPage = 0;
  function showPage(i) {{
    pages.forEach((p,j)=>p.style.display = (i===j?'block':'none'));
    currentPage = i;
  }}
  document.getElementById('sidebarClose').onclick = ()=> sidebar.style.display='none';
  document.getElementById('sidebarPrev').onclick  = ()=> showPage((currentPage-1+pages.length)%pages.length);
  document.getElementById('sidebarNext').onclick  = ()=> showPage((currentPage+1)%pages.length);
  infoBtn.onclick = ()=> {{
    sidebar.style.display = (sidebar.style.display==='none'?'block':'none');
    showPage(currentPage);
  }};
  showPage(0);

  ['fromMonth','toMonth'].forEach(id=>
    document.getElementById(id).addEventListener('change',()=>
      Object.keys(pointLayers).forEach(c=>clearWard(c)||toggleWard(c))
    )
  );

  function clearWard(code) {{
    if(lsoaLayers[code]) map.removeLayer(lsoaLayers[code]);
    if(pointLayers[code]) map.removeLayer(pointLayers[code]);
    delete lsoaLayers[code]; delete pointLayers[code];
  }}

  function toggleWard(code) {{
    if(lsoaLayers[code]) return clearWard(code);
    lsoaLayers[code] = L.geoJSON(
      {{type:'FeatureCollection',features:lsoaByWard[code]||[]}},
      {{style:{{color:'white',weight:1.5,fillOpacity:0.05}}}}
    ).addTo(map);
    let pts = [];
    if(mode==='alloc') pts = allocByWard[code]||[];
    else {{
      const from = document.getElementById('fromMonth').value,
            to   = document.getElementById('toMonth').value;
      Object.entries(crimeByWard[code]||{{}}).forEach(([m,arr])=>{{
        if(m>=from&&m<=to) pts.push(...arr);
      }});
    }}
    const grp = L.layerGroup();
    pts.forEach(p=>{{
      if(mode==='alloc') {{
        const marker=L.circleMarker([p.lat,p.lon],{{radius:5,color:'blue',fillOpacity:0.6}}).addTo(grp);
        marker.bindTooltip(A:${{p.hours}} hrs, R:${{p.reinf}} hrs,{{direction:'top',offset:[0,-2],className:'alloc-tooltip'}});
        marker.on('mouseover',()=>marker.openTooltip());
        marker.on('mouseout',()=>marker.closeTooltip());
      }} else {{
        L.circleMarker(p,{{radius:3,color:'red',fillOpacity:0.6}}).addTo(grp);
      }}
    }});
    grp.addTo(map);
    pointLayers[code] = grp;
    wardsLayer.bringToFront();
  }}

  const wardsLayer = L.geoJSON(wardsData,{{
    style:()=>({{color:'deeppink',weight:2,fillOpacity:0.01}}),
    onEachFeature:(f, lyr)=>{{
      const c = f.properties.ward;
      lyr.bindTooltip(c,{{sticky:true,className:'ward-tooltip'}})
         .on('click',()=>toggleWard(c))
         .on('mouseover',()=>lyr.setStyle({{color:'white',weight:3,fillOpacity:0.1}}))
         .on('mouseout',()=>lyr.setStyle({{color:'deeppink',weight:2,fillOpacity:0.01}}));
    }}
  }}).addTo(map);
}};
</script>

<style>
.month-range-control {{
  display:flex;align-items:center;background:rgba(0,0,0,0.75);
  padding:6px 10px;border-radius:4px;font-family:Segoe UI,sans-serif;
}}
.month-range-control label{{margin-right:12px;color:#fff;font-size:14px}}
.month-range-control input[type="month"]{{
  margin-left:4px;padding:2px 6px;border:1px solid #555;
  border-radius:3px;background:#222;color:#fff;font-size:14px;
}}
.clear-btn {{
  margin-left:12px;padding:2px 8px;background:#444;color:#fff;
  border:none;border-radius:3px;cursor:pointer;font-size:14px;
}}
.clear-btn:hover {{background:#555}}
.ward-tooltip {{
  background:transparent!important;border:none!important;
  box-shadow:none!important;color:#fff!important;font-weight:bold;
}}
.alloc-tooltip {{font-size:16px;padding:4px 8px}}
.info-sidebar {{font-family:Segoe UI,sans-serif}}
.info-sidebar .page {{display:none}}
.info-sidebar .page.active {{display:block}}
</style>
"""
m.get_root().html.add_child(folium.Element(js))
m.save(output_path)

# ─── DASH APP ───────────────────────────────────────────────────
app = dash.Dash(_name_)
app.layout = html.Div([
    html.Iframe(
        src=f"/assets/{os.path.basename(output_path)}",
        style={"height":"100vh","width":"100%","border":"none"}
    )
])
if _name_ == "_main_":
    app.run(debug=False, dev_tools_ui=False, dev_tools_props_check=False)