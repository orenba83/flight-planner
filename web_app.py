import math
import time
import numpy as np
import streamlit as st
import folium
from folium.plugins import Draw
from branca.element import Element
from streamlit_folium import st_folium
import srtm

st.set_page_config(page_title="Flight Planner", page_icon="✈️", layout="wide", initial_sidebar_state="expanded")
st.markdown("""
<style>
html, body, [data-testid="stAppViewContainer"] { overflow: hidden !important; }
[data-testid="stMainBlockContainer"] { padding-top:.25rem !important; padding-bottom:.1rem !important; }
section[data-testid="stSidebar"] { min-width:340px !important; max-width:340px !important; }
</style>
""", unsafe_allow_html=True)

DEFAULT_GAIN=[(30,-35.0),(60,-24.5),(90,-12.5),(500,-1.0),(1000,3.0),(2000,3.0),(3000,2.0),(4500,2.0),(5000,2.0),(6000,4.0)]
EARTH_RADIUS_M=6371000.0
C=299792458.0
NOISE_BW_MHZ=0.025
DEFAULT_NF_DB=6.0

def interp_gain(freq):
    if freq<=DEFAULT_GAIN[0][0]: return DEFAULT_GAIN[0][1]
    if freq>=DEFAULT_GAIN[-1][0]: return DEFAULT_GAIN[-1][1]
    for i in range(1,len(DEFAULT_GAIN)):
        f0,g0=DEFAULT_GAIN[i-1]; f1,g1=DEFAULT_GAIN[i]
        if freq<=f1:
            t=(freq-f0)/(f1-f0); return g0+t*(g1-g0)
    return DEFAULT_GAIN[-1][1]

def haversine_m(lat1,lon1,lat2,lon2):
    p=math.pi/180.0
    a=math.sin((lat2-lat1)*p/2)**2+math.cos(lat1*p)*math.cos(lat2*p)*math.sin((lon2-lon1)*p/2)**2
    return 2*EARTH_RADIUS_M*math.asin(math.sqrt(a))

def fspl_db(d,f):
    return 0.0 if d<=0 else 20*math.log10(4*math.pi*d*f*1e6/C)

def point_in_polygon(lat,lon,polygon):
    inside=False; j=len(polygon)-1
    for i in range(len(polygon)):
        yi,xi=polygon[i]; yj,xj=polygon[j]
        if ((yi>lat)!=(yj>lat)):
            x=(xj-xi)*(lat-yi)/((yj-yi) or 1e-15)+xi
            if lon<x: inside=not inside
        j=i
    return inside

def resample_path(points,n):
    if len(points)<=1:return points
    n=max(2,int(n)); cum=[0.0]
    for a,b in zip(points[:-1],points[1:]):cum.append(cum[-1]+haversine_m(a[0],a[1],b[0],b[1]))
    total=cum[-1]
    if total<=0:return [points[0]]
    out=[]
    for k in range(n):
        target=total*k/(n-1); seg=1
        while seg<len(cum)-1 and cum[seg]<target:seg+=1
        a=points[seg-1]; b=points[seg]; length=cum[seg]-cum[seg-1]
        t=(target-cum[seg-1])/length if length else 0
        out.append([a[0]+t*(b[0]-a[0]),a[1]+t*(b[1]-a[1])])
    return out

def color_rgb(v,vmin,vmax):
    if not math.isfinite(v):return (120,120,120)
    x=max(0,min(1,(v-vmin)/max(vmax-vmin,1e-9)))
    stops=[(0,(213,43,30)),(.4,(247,148,30)),(.7,(255,221,51)),(1,(46,204,64))]
    for i in range(len(stops)-1):
        x0,c0=stops[i]; x1,c1=stops[i+1]
        if x<=x1:
            t=(x-x0)/(x1-x0)
            return tuple(int(c0[k]+t*(c1[k]-c0[k])) for k in range(3))
    return stops[-1][1]

@st.cache_resource(show_spinner=False)
def get_srtm():return srtm.get_data(srtm1=False,srtm3=True)

def terrain_elevation(provider,lat,lon,cache):
    key=(round(float(lat),5),round(float(lon),5))
    if key in cache:return cache[key]
    try:
        v=provider.get_elevation(lat,lon,approximate=True); v=0.0 if v is None else float(v)
    except Exception:v=0.0
    cache[key]=v; return v

def los_clear(provider,alat,alon,aalt,glat,glon,gelev,cache,samples=40):
    horizontal=haversine_m(alat,alon,glat,glon)
    if horizontal<2:return True
    count=max(10,int(samples))
    for k in range(1,count):
        t=k/count; lat=alat+(glat-alat)*t; lon=alon+(glon-alon)*t
        terrain=terrain_elevation(provider,lat,lon,cache); ray=aalt+(gelev-aalt)*t
        if terrain>=ray-2.0:return False
    return True

def base_map(center,zoom):return folium.Map(location=list(center),zoom_start=zoom,control_scale=True,tiles="OpenStreetMap")

def add_auto_draw(m,mode):
    opts={"polyline":{"shapeOptions":{"color":"#ff3333","weight":4}} if mode=="path" else False,"polygon":False,"rectangle":{"shapeOptions":{"color":"#3388ff","weight":3,"fillOpacity":0.12}} if mode=="aoi" else False,"circle":False,"marker":False,"circlemarker":False}
    Draw(export=False,position="topleft",draw_options=opts,edit_options=False).add_to(m)
    map_name=m.get_name()
    handler_class="L.Draw.Polyline" if mode=="path" else "L.Draw.Rectangle"
    handler_options="{shapeOptions:{color:'#ff3333',weight:4}}" if mode=="path" else "{shapeOptions:{color:'#3388ff',weight:3,fillOpacity:0.12}}"
    js=f"""
    <style>.leaflet-draw {{ display:none !important; }}</style>
    <script>
    (function() {{
      var attempts=0;
      function startDrawing() {{
        attempts++;
        try {{
          var map={map_name};
          if (map && window.L && L.Draw && L.Draw.Polyline && L.Draw.Rectangle) {{
            var handler=new {handler_class}(map,{handler_options});
            handler.enable();
            return;
          }}
        }} catch(e) {{ console.log('draw init',e); }}
        if(attempts<20) setTimeout(startDrawing,250);
      }}
      setTimeout(startDrawing,100);
    }})();
    </script>
    """
    m.get_root().html.add_child(Element(js))

def add_geometry(m):
    if st.session_state.path:
        folium.PolyLine(st.session_state.path,color="red",weight=5,tooltip="Flight Path").add_to(m)
        folium.Marker(st.session_state.path[0],tooltip="Aircraft start").add_to(m)
    if st.session_state.aoi:folium.Polygon(st.session_state.aoi,color="blue",weight=3,fill=True,fill_opacity=.10,tooltip="AOI").add_to(m)

def add_heat_cells(m,grid,details,bounds,vmin,vmax,freq_idx="all"):
    rows,cols=grid.shape; minlat,minlon=bounds[0]; maxlat,maxlon=bounds[1]; dlat=(maxlat-minlat)/rows; dlon=(maxlon-minlon)/cols
    for iy in range(rows):
        for ix in range(cols):
            detail=details[iy][ix]
            if detail is None:continue
            north=maxlat-iy*dlat; south=maxlat-(iy+1)*dlat; west=minlon+ix*dlon; east=minlon+(ix+1)*dlon; center_lat=(south+north)/2; center_lon=(west+east)/2
            use=detail
            if freq_idx!="all" and not detail.get("blocked") and detail.get("per_freq") and freq_idx in detail["per_freq"]:
                use=detail["per_freq"][freq_idx]
            if detail.get("blocked") or use is None:
                fill="#707070"; tip="LOS BLOCKED"
                popup_html=f"<div style='min-width:230px'><b>LOS BLOCKED</b><br>Lat: {center_lat:.5f}<br>Lon: {center_lon:.5f}<hr>Terrain blocks the line of sight.<br>DTM elevation: {detail.get('ground_elev',0):.1f} m</div>"
            else:
                v=use["snr"]; r,g,b=color_rgb(v,vmin,vmax); fill=f"rgb({r},{g},{b})"; tip=f"{v:.1f} dB"
                popup_html=f"<div style='min-width:270px'><div style='font-size:20px;font-weight:700'>{v:.1f} dB</div><hr><b>Calculation</b><br>Frequency: {use['freq']:.3f} MHz<br>Distance: {use['distance_km']:.3f} km<br>Tx Power: {use['tx_power']:.1f} dBm<br>Antenna Gain: {use['gain']:.1f} dB<br>FSPL: {use['fspl']:.1f} dB<br>Polarization Loss: {use['pol_loss']:.1f} dB<br>System Loss: {use['sys_loss']:.1f} dB<br>Noise Floor: {use['noise_floor']:.1f} dBm<br>DTM Elevation: {use['ground_elev']:.1f} m<br>LOS: CLEAR</div>"
            folium.Rectangle(bounds=[[south,west],[north,east]],stroke=False,fill=True,fill_color=fill,fill_opacity=.76,tooltip=folium.Tooltip(tip,sticky=True),popup=folium.Popup(popup_html,max_width=340)).add_to(m)

def add_legend(m,vmin,vmax):
    html=f'''<div style="position:fixed;bottom:20px;right:20px;z-index:9999;background:white;padding:10px 12px;border:1px solid #777;border-radius:6px;box-shadow:0 1px 5px rgba(0,0,0,.35);font-family:Arial;font-size:12px;width:220px"><b>SNR (dB)</b><div style="height:16px;margin-top:6px;border:1px solid #555;background:linear-gradient(to right,rgb(213,43,30),rgb(247,148,30),rgb(255,221,51),rgb(46,204,64))"></div><div style="display:flex;justify-content:space-between;margin-top:3px"><span>{vmin:g}</span><span>{(vmin+vmax)/2:g}</span><span>{vmax:g}</span></div><div style="margin-top:5px">Red = weak · Green = strong<br>Gray = DTM LOS blocked</div></div>'''
    m.get_root().html.add_child(Element(html))

for k,d in [("mode",None),("path",None),("aoi",None),("path_confirmed",False),("aoi_confirmed",False),("analysis",None),("run_requested",False),("selected_freq_idx","all")]:
    if k not in st.session_state:st.session_state[k]=d

with st.sidebar:
    st.title("✈️ Flight Planner"); st.subheader("Geometry")
    path_label="אישור מסלול טיסה" if st.session_state.path and not st.session_state.path_confirmed else "מסלול טיסה"
    if st.button("✈️  "+path_label,use_container_width=True,type="primary" if st.session_state.mode=="path" else "secondary"):
        if st.session_state.path and not st.session_state.path_confirmed:st.session_state.path_confirmed=True
        else:st.session_state.mode="path";st.session_state.path=None;st.session_state.path_confirmed=False;st.session_state.analysis=None
        st.rerun()
    aoi_label="אישור AOI" if st.session_state.aoi and not st.session_state.aoi_confirmed else "AOI"
    if st.button("📐  "+aoi_label,use_container_width=True,type="primary" if st.session_state.mode=="aoi" else "secondary"):
        if st.session_state.aoi and not st.session_state.aoi_confirmed:st.session_state.aoi_confirmed=True
        else:st.session_state.mode="aoi";st.session_state.aoi=None;st.session_state.aoi_confirmed=False;st.session_state.analysis=None
        st.rerun()
    if st.button("🗑️  נקה בחירות",use_container_width=True):
        st.session_state.path=None;st.session_state.aoi=None;st.session_state.path_confirmed=False;st.session_state.aoi_confirmed=False;st.session_state.analysis=None;st.session_state.mode=None;st.rerun()
    st.divider();st.subheader("Mission")
    min_freq=st.number_input("Min Frequency (MHz)",30.0,18000.0,100.0,1.0); max_freq=st.number_input("Max Frequency (MHz)",30.0,18000.0,100.0,1.0); steps=st.number_input("Frequency Steps",1,50,1,1); altitude_kft=st.number_input("Aircraft Altitude AMSL (KFT)",0.0,100.0,35.0,1.0)
    st.subheader("RF / DTM")
    path_samples=st.number_input("Flight Path Samples",2,200,30,1); grid_size=st.number_input("AOI Grid Resolution",15,60,35,5); los_enabled=st.checkbox("DTM Line-of-Sight Masking",True); los_samples=st.number_input("DTM LOS Samples",10,150,40,5)
    tx_power=st.number_input("Tx Power (dBm)",-50.0,100.0,37.0,.5); pol_loss=st.number_input("Polarization Loss (dB)",0.0,30.0,3.0,.5); sys_loss=st.number_input("System Loss (dB)",0.0,30.0,0.0,.5)
    st.caption("Noise Bandwidth: fixed at 25 kHz"); noise_figure=st.number_input("Receiver Noise Figure NF (dB)",0.0,30.0,DEFAULT_NF_DB,.5,help="NF is receiver noise added above thermal noise. If unknown, 0 dB means ideal receiver assumption.")
    st.subheader("Heat Map Scale"); scale_min=st.number_input("Color Bar Min (dB)",-200.0,200.0,-25.0,1.0); scale_max=st.number_input("Color Bar Max (dB)",scale_min+1.0,250.0,30.0,1.0)
    st.caption("Grid = AOI resolution (N×N cells). LOS samples = samples along aircraft→ground ray for DTM blocking check.")
    if st.session_state.analysis and st.session_state.analysis.get("freqs"):
        labels = ["All freqs (aggregate / best)"] + [f"{f:.3f} MHz" for f in st.session_state.analysis["freqs"}}]
        choice = st.selectbox("Frequency view", options=list(range(len(labels))), format_func=lambda i: labels[i], key="freq_view_box")
        st.session_state.selected_freq_idx = "all" if choice == 0 else choice - 1
    ready=bool(st.session_state.path_confirmed and st.session_state.aoi_confirmed and st.session_state.path and st.session_state.aoi)
    if st.button("🚀  Run DTM + SNR Analysis",type="primary",use_container_width=True,disabled=not ready):st.session_state.run_requested=True
    st.caption("מסלול: "+(✅ מאושר" if st.session_state.path_confirmed else "❌ לא מאושר"));st.caption("AOI: "+(✅ מאושר" if st.session_state.aoi_confirmed else "❌ לא מאושר"))
