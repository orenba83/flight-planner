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

def add_heat_cells(m,grid,details,bounds,vmin,vmax):
    rows,cols=grid.shape; minlat,minlon=bounds[0]; maxlat,maxlon=bounds[1]; dlat=(maxlat-minlat)/rows; dlon=(maxlon-minlon)/cols
    for iy in range(rows):
        for ix in range(cols):
            detail=details[iy][ix]
            if detail is None:continue
            north=maxlat-iy*dlat; south=maxlat-(iy+1)*dlat; west=minlon+ix*dlon; east=minlon+(ix+1)*dlon; center_lat=(south+north)/2; center_lon=(west+east)/2
            if detail["blocked"]:
                fill="#707070"; tip="LOS BLOCKED"
                popup_html=f"<div style='min-width:230px'><b>LOS BLOCKED</b><br>Lat: {center_lat:.5f}<br>Lon: {center_lon:.5f}<hr>Terrain blocks the line of sight.<br>DTM elevation: {detail['ground_elev']:.1f} m</div>"
            else:
                v=detail["snr"]; r,g,b=color_rgb(v,vmin,vmax); fill=f"rgb({r},{g},{b})"; tip=f"{v:.1f} dB"
                popup_html=f"<div style='min-width:270px'><div style='font-size:20px;font-weight:700'>{v:.1f} dB</div><hr><b>Calculation</b><br>Frequency: {detail['freq']:.3f} MHz<br>Distance: {detail['distance_km']:.3f} km<br>Tx Power: {detail['tx_power']:.1f} dBm<br>Antenna Gain: {detail['gain']:.1f} dB<br>FSPL: {detail['fspl']:.1f} dB<br>Polarization Loss: {detail['pol_loss']:.1f} dB<br>System Loss: {detail['sys_loss']:.1f} dB<br>Noise Floor: {detail['noise_floor']:.1f} dBm<br>DTM Elevation: {detail['ground_elev']:.1f} m<br>LOS: CLEAR</div>"
            folium.Rectangle(bounds=[[south,west],[north,east]],stroke=False,fill=True,fill_color=fill,fill_opacity=.76,tooltip=folium.Tooltip(tip,sticky=True),popup=folium.Popup(popup_html,max_width=340)).add_to(m)

def add_legend(m,vmin,vmax):
    html=f'''<div style="position:fixed;bottom:20px;right:20px;z-index:9999;background:white;padding:10px 12px;border:1px solid #777;border-radius:6px;box-shadow:0 1px 5px rgba(0,0,0,.35);font-family:Arial;font-size:12px;width:220px"><b>SNR (dB)</b><div style="height:16px;margin-top:6px;border:1px solid #555;background:linear-gradient(to right,rgb(213,43,30),rgb(247,148,30),rgb(255,221,51),rgb(46,204,64))"></div><div style="display:flex;justify-content:space-between;margin-top:3px"><span>{vmin:g}</span><span>{(vmin+vmax)/2:g}</span><span>{vmax:g}</span></div><div style="margin-top:5px">Red = weak · Green = strong<br>Gray = DTM LOS blocked</div></div>'''
    m.get_root().html.add_child(Element(html))

for k,d in [("mode",None),("path",None),("aoi",None),("path_confirmed",False),("aoi_confirmed",False),("analysis",None),("run_requested",False)]:
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
    ready=bool(st.session_state.path_confirmed and st.session_state.aoi_confirmed and st.session_state.path and st.session_state.aoi)
    if st.button("🚀  Run DTM + SNR Analysis",type="primary",use_container_width=True,disabled=not ready):st.session_state.run_requested=True
    st.caption("מסלול: "+("✅ מאושר" if st.session_state.path_confirmed else "❌ לא מאושר"));st.caption("AOI: "+("✅ מאושר" if st.session_state.aoi_confirmed else "❌ לא מאושר"))

center=[31.8,35.0]
if st.session_state.path:center=st.session_state.path[len(st.session_state.path)//2]
elif st.session_state.aoi:center=[sum(p[0] for p in st.session_state.aoi)/len(st.session_state.aoi),sum(p[1] for p in st.session_state.aoi)/len(st.session_state.aoi)]
if st.session_state.analysis:
    bounds=st.session_state.analysis["bounds"];center=[(bounds[0][0]+bounds[1][0])/2,(bounds[0][1]+bounds[1][1])/2];m=base_map(center,9);add_heat_cells(m,st.session_state.analysis["grid"],st.session_state.analysis["details"],bounds,scale_min,scale_max);add_geometry(m);add_legend(m,scale_min,scale_max)
else:
    m=base_map(center,8 if (st.session_state.path or st.session_state.aoi) else 7);add_geometry(m)
    if st.session_state.mode in ("path","aoi"):
        if (st.session_state.mode=="path" and not st.session_state.path_confirmed and not st.session_state.path) or (st.session_state.mode=="aoi" and not st.session_state.aoi_confirmed and not st.session_state.aoi):add_auto_draw(m,st.session_state.mode)
map_state=st_folium(m,height=760,use_container_width=True,returned_objects=["all_drawings"],key="mission_map")

if not st.session_state.analysis:
    drawings=map_state.get("all_drawings") or []
    if drawings:
        feature=drawings[-1];geo=feature.get("geometry",{});coords=geo.get("coordinates",[]);changed=False
        if st.session_state.mode=="path" and geo.get("type")=="LineString" and len(coords)>=2:st.session_state.path=[[p[1],p[0]] for p in coords];st.session_state.path_confirmed=False;changed=True
        elif st.session_state.mode=="aoi" and geo.get("type")=="Polygon" and coords:st.session_state.aoi=[[p[1],p[0]] for p in coords[0]];st.session_state.aoi_confirmed=False;changed=True
        if changed:st.rerun()

if st.session_state.run_requested and ready:
    st.session_state.run_requested=False
    if min_freq>max_freq:st.error("Max Frequency must be >= Min Frequency.");st.stop()
    if scale_max<=scale_min:st.error("Color Bar Max must be greater than Min.");st.stop()
    freqs=[min_freq] if int(steps)==1 else np.linspace(min_freq,max_freq,int(steps)).tolist();sampled_path=resample_path(st.session_state.path,int(path_samples));aircraft_alt_m=float(altitude_kft)*304.8;noise_floor=-174.0+10*math.log10(NOISE_BW_MHZ*1e6)+float(noise_figure)
    progress=st.progress(0.0,text="Loading DTM...");started=time.time()
    try:dtm=get_srtm()
    except Exception as e:st.error(f"DTM/SRTM could not be loaded: {e}");st.stop()
    aoi=st.session_state.aoi;lats=[p[0] for p in aoi];lons=[p[1] for p in aoi];minlat,maxlat=min(lats),max(lats);minlon,maxlon=min(lons),max(lons);n=int(grid_size);latvals=np.linspace(maxlat,minlat,n);lonvals=np.linspace(minlon,maxlon,n)
    cells=[(iy,ix,float(lat),float(lon)) for iy,lat in enumerate(latvals) for ix,lon in enumerate(lonvals) if point_in_polygon(float(lat),float(lon),aoi)]
    cache={};elev={}
    for i,(_,_,lat,lon) in enumerate(cells,1):
        elev[(round(lat,5),round(lon,5))]=terrain_elevation(dtm,lat,lon,cache)
        if i==1 or i==len(cells) or i%max(1,len(cells)//10)==0:progress.progress(.12*i/max(1,len(cells)),text=f"DTM {i}/{len(cells)}")
    grid=np.full((n,n),np.nan,dtype=np.float32);details=[[None for _ in range(n)] for _ in range(n)];blocked=0
    for i,(iy,ix,glat,glon) in enumerate(cells,1):
        gelev=elev[(round(glat,5),round(glon,5))];best=-math.inf;best_detail=None
        for alat,alon in sampled_path:
            horiz=haversine_m(alat,alon,glat,glon);d3=math.sqrt(horiz*horiz+(aircraft_alt_m-gelev)**2);visible=los_clear(dtm,alat,alon,aircraft_alt_m,glat,glon,gelev,cache,int(los_samples)) if los_enabled else True
            if not visible:continue
            for f in freqs:
                gain=interp_gain(float(f));fspl=fspl_db(d3,float(f));snr=tx_power+gain-fspl-pol_loss-sys_loss-noise_floor
                if snr>best:best=snr;best_detail={"snr":snr,"freq":float(f),"distance_km":d3/1000.0,"tx_power":tx_power,"gain":gain,"fspl":fspl,"pol_loss":pol_loss,"sys_loss":sys_loss,"noise_floor":noise_floor,"ground_elev":gelev,"blocked":False}
        if best_detail is None:blocked+=1;details[iy][ix]={"blocked":True,"ground_elev":gelev}
        else:grid[iy,ix]=best;details[iy][ix]=best_detail
        if i==1 or i==len(cells) or i%max(1,len(cells)//20)==0:progress.progress(.12+.88*i/max(1,len(cells)),text=f"DTM + SNR {i}/{len(cells)}")
    elapsed=time.time()-started;progress.progress(1.0,text="Analysis complete");st.session_state.analysis={"grid":grid,"details":details,"bounds":[[minlat,minlon],[maxlat,maxlon]],"path":sampled_path,"blocked":blocked,"elapsed":elapsed};st.rerun()
