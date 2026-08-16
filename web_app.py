import math
import time
import base64
import io
import numpy as np
import streamlit as st
import folium
from folium.plugins import Draw
from folium.raster_layers import ImageOverlay
from streamlit_folium import st_folium
from PIL import Image
import srtm

st.set_page_config(page_title="Flight Planner", page_icon="✈️", layout="wide")

DEFAULT_GAIN = [(30,-35.0),(60,-24.5),(90,-12.5),(500,-1.0),(1000,3.0),(2000,3.0),(3000,2.0),(4500,2.0),(5000,2.0),(6000,4.0)]
EARTH_RADIUS_M = 6371000.0
C = 299792458.0


def interp_gain(freq):
    if freq <= DEFAULT_GAIN[0][0]: return DEFAULT_GAIN[0][1]
    if freq >= DEFAULT_GAIN[-1][0]: return DEFAULT_GAIN[-1][1]
    for i in range(1,len(DEFAULT_GAIN)):
        f0,g0=DEFAULT_GAIN[i-1]; f1,g1=DEFAULT_GAIN[i]
        if freq <= f1:
            t=(freq-f0)/(f1-f0); return g0+t*(g1-g0)
    return DEFAULT_GAIN[-1][1]


def haversine_m(lat1,lon1,lat2,lon2):
    p=math.pi/180.0
    a=math.sin((lat2-lat1)*p/2)**2+math.cos(lat1*p)*math.cos(lat2*p)*math.sin((lon2-lon1)*p/2)**2
    return 2*EARTH_RADIUS_M*math.asin(math.sqrt(a))


def fspl_db(distance_m,freq_mhz):
    if distance_m<=0: return 0.0
    return 20*math.log10(4*math.pi*distance_m*freq_mhz*1e6/C)


def point_in_polygon(lat,lon,polygon):
    inside=False; j=len(polygon)-1
    for i in range(len(polygon)):
        yi,xi=polygon[i]; yj,xj=polygon[j]
        if ((yi>lat)!=(yj>lat)):
            x_cross=(xj-xi)*(lat-yi)/((yj-yi) or 1e-15)+xi
            if lon<x_cross: inside=not inside
        j=i
    return inside


def resample_path(points,n):
    if len(points)<=1: return points
    n=max(2,int(n)); cumulative=[0.0]
    for a,b in zip(points[:-1],points[1:]): cumulative.append(cumulative[-1]+haversine_m(a[0],a[1],b[0],b[1]))
    total=cumulative[-1]
    if total<=0: return [points[0]]
    result=[]
    for k in range(n):
        target=total*k/(n-1); seg=1
        while seg<len(cumulative)-1 and cumulative[seg]<target: seg+=1
        a=points[seg-1]; b=points[seg]; length=cumulative[seg]-cumulative[seg-1]
        t=(target-cumulative[seg-1])/length if length else 0.0
        result.append([a[0]+t*(b[0]-a[0]),a[1]+t*(b[1]-a[1])])
    return result


def color_rgba(values,vmin,vmax):
    norm=np.clip((values-vmin)/max(vmax-vmin,1e-9),0,1)
    stops=[(0.0,(213,43,30)),(0.4,(247,148,30)),(0.7,(255,221,51)),(1.0,(46,204,64))]
    out=np.zeros((*values.shape,4),dtype=np.uint8)
    for i in range(len(stops)-1):
        x0,c0=stops[i]; x1,c1=stops[i+1]; mask=(norm>=x0)&(norm<=x1)
        if np.any(mask):
            t=(norm[mask]-x0)/(x1-x0)
            for ch in range(3): out[...,ch][mask]=(c0[ch]+t*(c1[ch]-c0[ch])).astype(np.uint8)
    out[...,3]=190
    return out


def image_overlay_data_uri(rgba):
    """Convert the RGBA numpy image to a PNG data URI so Folium never tries to JSON-serialize a numpy/PIL object."""
    image=Image.fromarray(np.ascontiguousarray(rgba,dtype=np.uint8),mode="RGBA")
    buffer=io.BytesIO()
    image.save(buffer,format="PNG",optimize=True)
    encoded=base64.b64encode(buffer.getvalue()).decode("ascii")
    return "data:image/png;base64,"+encoded


def make_map(draw_mode,center=(31.8,35.0),zoom=7):
    m=folium.Map(location=list(center),zoom_start=zoom,control_scale=True,tiles="OpenStreetMap")
    if draw_mode=="path":
        options={"polyline":True,"polygon":False,"rectangle":False,"circle":False,"marker":False,"circlemarker":False}
    else:
        options={"polyline":False,"polygon":True,"rectangle":True,"circle":False,"marker":False,"circlemarker":False}
    Draw(export=False,position="topleft",draw_options=options,edit_options={"edit":True,"remove":True}).add_to(m)
    return m


def add_saved_geometry(m):
    if st.session_state.get("path"):
        folium.PolyLine(st.session_state.path,color="red",weight=5,tooltip="Flight Path").add_to(m)
        folium.Marker(st.session_state.path[0],tooltip="Aircraft start").add_to(m)
    if st.session_state.get("aoi"):
        folium.Polygon(st.session_state.aoi,color="blue",weight=3,fill=True,fill_opacity=0.12,tooltip="AOI").add_to(m)


@st.cache_resource(show_spinner=False)
def get_srtm(): return srtm.get_data(srtm1=False,srtm3=True)


def terrain_elevation(provider,lat,lon,cache):
    key=(round(float(lat),5),round(float(lon),5))
    if key in cache: return cache[key]
    try: value=provider.get_elevation(lat,lon,approximate=True); value=0.0 if value is None else float(value)
    except Exception: value=0.0
    cache[key]=value
    return value


def los_clear(provider,alat,alon,aalt,glat,glon,gelev,cache,samples=10):
    for k in range(1,samples):
        t=k/samples; lat=alat+(glat-alat)*t; lon=alon+(glon-alon)*t
        terrain=terrain_elevation(provider,lat,lon,cache); ray=aalt+(gelev-aalt)*t
        if terrain>ray+5.0: return False
    return True


def log_line(box,lines,text):
    stamp=time.strftime("%H:%M:%S"); lines.append(f"[{stamp}] {text}"); box.code("\n".join(lines[-16:]),language="text")


st.title("✈️ Flight Planner")
st.caption("Select the flight path and AOI first. Nothing is calculated until Run Analysis is pressed.")
for key,default in [("draw_mode","path"),("path",None),("aoi",None),("analysis",None)]:
    if key not in st.session_state: st.session_state[key]=default

with st.sidebar:
    st.header("Mission Parameters")
    min_freq=st.number_input("Min Frequency (MHz)",30.0,18000.0,100.0,1.0)
    max_freq=st.number_input("Max Frequency (MHz)",30.0,18000.0,100.0,1.0)
    steps=st.number_input("Frequency Steps",1,50,1,1)
    altitude_kft=st.number_input("Aircraft Altitude AMSL (KFT)",0.0,100.0,35.0,1.0)
    st.header("RF / Analysis")
    path_samples=st.number_input("Flight Path Samples",2,200,20,1)
    grid_size=st.number_input("AOI Grid Resolution",15,60,35,5)
    los_enabled=st.checkbox("DTM Line-of-Sight Masking",value=True)
    los_samples=st.number_input("DTM LOS Samples",5,50,10,5)
    tx_power=st.number_input("Tx Power (dBm)",-50.0,100.0,37.0,0.5)
    pol_loss=st.number_input("Polarization Loss (dB)",0.0,30.0,3.0,0.5)
    sys_loss=st.number_input("System Loss (dB)",0.0,30.0,0.0,0.5)
    noise_bw=st.number_input("Noise Bandwidth (MHz)",0.001,1000.0,1.0,0.001)
    noise_figure=st.number_input("Noise Figure (dB)",0.0,30.0,6.0,0.5)
    snr_min=st.number_input("SNR Display Min (dB)",-200.0,200.0,-25.0,1.0)
    snr_max=st.number_input("SNR Display Max (dB)",-200.0,200.0,30.0,1.0)

b1,b2,b3=st.columns([1,1,1])
if b1.button("✈️ Select Flight Path",use_container_width=True):
    st.session_state.draw_mode="path"
    st.rerun()
if b2.button("📐 Select AOI",use_container_width=True):
    st.session_state.draw_mode="aoi"
    st.rerun()
if b3.button("🗑️ Clear Selection",use_container_width=True):
    st.session_state.path=None; st.session_state.aoi=None; st.session_state.analysis=None; st.session_state.draw_mode="path"; st.rerun()

if st.session_state.draw_mode=="path":
    st.info("✈️ Flight Path mode active — the map toolbar now contains ONLY the line tool. Click it and draw the route.")
else:
    st.info("📐 AOI mode active — the map toolbar now contains rectangle and polygon tools. For a square/rectangle, click the rectangle tool and drag.")

center=[31.8,35.0]
if st.session_state.path: center=st.session_state.path[len(st.session_state.path)//2]
elif st.session_state.aoi: center=[sum(p[0] for p in st.session_state.aoi)/len(st.session_state.aoi),sum(p[1] for p in st.session_state.aoi)/len(st.session_state.aoi)]

m=make_map(st.session_state.draw_mode,center=center,zoom=8 if (st.session_state.path or st.session_state.aoi) else 7)
add_saved_geometry(m)
map_state=st_folium(m,height=760,use_container_width=True,returned_objects=["all_drawings"],key="geometry_map")

new_drawings=map_state.get("all_drawings") or []
if new_drawings:
    feature=new_drawings[-1]; geometry=feature.get("geometry",{}); coords=geometry.get("coordinates",[])
    if st.session_state.draw_mode=="path" and geometry.get("type")=="LineString":
        st.session_state.path=[[p[1],p[0]] for p in coords]; st.session_state.analysis=None
    elif st.session_state.draw_mode=="aoi" and geometry.get("type")=="Polygon" and coords:
        st.session_state.aoi=[[p[1],p[0]] for p in coords[0]]; st.session_state.analysis=None

c1,c2=st.columns(2)
with c1: st.write("**Flight Path:**","✅ Selected" if st.session_state.path else "❌ Not selected")
with c2: st.write("**AOI:**","✅ Selected" if st.session_state.aoi else "❌ Not selected")

run=st.button("🚀 Run DTM + SNR Analysis",type="primary",use_container_width=True,disabled=not(st.session_state.path and st.session_state.aoi))

if run:
    path=list(st.session_state.path); aoi=list(st.session_state.aoi)
    if min_freq>max_freq: st.error("Max Frequency must be greater than or equal to Min Frequency."); st.stop()
    if snr_max<=snr_min: st.error("SNR Display Max must be greater than SNR Display Min."); st.stop()
    freqs=[min_freq] if int(steps)==1 else np.linspace(min_freq,max_freq,int(steps)).tolist()
    sampled_path=resample_path(path,int(path_samples)); aircraft_alt_m=float(altitude_kft)*304.8
    noise_floor=-174.0+10.0*math.log10(float(noise_bw)*1e6)+float(noise_figure)
    log_box=st.empty(); progress=st.progress(0.0,text="Preparing analysis..."); eta_box=st.empty(); lines=[]; started=time.time()
    log_line(log_box,lines,"Starting analysis.")
    log_line(log_box,lines,f"Flight path: {len(path)} drawn points → {len(sampled_path)} aircraft positions.")
    log_line(log_box,lines,f"AOI: {len(aoi)} vertices."); log_line(log_box,lines,f"Frequencies: {len(freqs)}.")
    try: dtm=get_srtm()
    except Exception as exc: st.error(f"DTM/SRTM could not be loaded: {exc}"); st.stop()
    lats=[p[0] for p in aoi]; lons=[p[1] for p in aoi]; min_lat,max_lat=min(lats),max(lats); min_lon,max_lon=min(lons),max(lons)
    n=int(grid_size); lat_vals=np.linspace(max_lat,min_lat,n); lon_vals=np.linspace(min_lon,max_lon,n)
    cells=[(iy,ix,float(lat),float(lon)) for iy,lat in enumerate(lat_vals) for ix,lon in enumerate(lon_vals) if point_in_polygon(float(lat),float(lon),aoi)]
    total_cells=max(1,len(cells)); log_line(log_box,lines,f"AOI raster contains {len(cells)} cells.")
    elevation_cache={}; ground_elevation={}
    for idx,(_,_,lat,lon) in enumerate(cells,1):
        ground_elevation[(round(lat,5),round(lon,5))]=terrain_elevation(dtm,lat,lon,elevation_cache)
        if idx==1 or idx==len(cells) or idx%max(1,len(cells)//10)==0: progress.progress(0.10*idx/total_cells,text=f"Loading DTM: {idx}/{len(cells)}")
    snr_grid=np.full((n,n),np.nan,dtype=np.float32); completed=0
    for iy,ix,glat,glon in cells:
        gelev=ground_elevation[(round(glat,5),round(glon,5))]; best_snr=-999.0
        for alat,alon in sampled_path:
            horizontal=haversine_m(alat,alon,glat,glon); d3=math.sqrt(horizontal**2+(aircraft_alt_m-gelev)**2)
            for freq in freqs:
                snr=tx_power+interp_gain(float(freq))-fspl_db(d3,float(freq))-pol_loss-sys_loss-noise_floor
                if los_enabled and horizontal>1.0 and not los_clear(dtm,alat,alon,aircraft_alt_m,glat,glon,gelev,elevation_cache,int(los_samples)): snr=-150.0
                best_snr=max(best_snr,snr)
        snr_grid[iy,ix]=best_snr; completed+=1
        if completed==1 or completed==len(cells) or completed%max(1,len(cells)//20)==0:
            elapsed=time.time()-started; rate=completed/elapsed if elapsed>0 else 0; remaining=(len(cells)-completed)/rate if rate else 0
            progress.progress(0.10+0.88*completed/total_cells,text=f"Calculating SNR: {completed}/{len(cells)} cells")
            eta_box.info(f"Progress: {completed/total_cells*100:.1f}% | Elapsed: {elapsed:.1f}s | Estimated remaining: {remaining:.1f}s")
    elapsed=time.time()-started; progress.progress(1.0,text="Analysis complete"); eta_box.success(f"Completed in {elapsed:.1f}s")
    st.session_state.analysis={"grid":snr_grid,"bounds":[[min_lat,min_lon],[max_lat,max_lon]],"path":sampled_path,"elapsed":elapsed}

if st.session_state.analysis:
    analysis=st.session_state.analysis; grid=analysis["grid"]; bounds=analysis["bounds"]
    st.subheader("SNR Heat Map")
    result_map=folium.Map(location=[(bounds[0][0]+bounds[1][0])/2,(bounds[0][1]+bounds[1][1])/2],zoom_start=9,control_scale=True,tiles="OpenStreetMap")
    overlay=color_rgba(np.nan_to_num(grid,nan=-150.0),float(snr_min),float(snr_max))
    data_uri=image_overlay_data_uri(overlay)
    ImageOverlay(image=data_uri,bounds=bounds,opacity=0.70,interactive=False,cross_origin=False).add_to(result_map)
    folium.PolyLine(analysis["path"],color="red",weight=4,tooltip="Flight Path").add_to(result_map)
    folium.Polygon(st.session_state.aoi,color="blue",weight=2,fill=False,tooltip="AOI").add_to(result_map)
    st_folium(result_map,height=700,use_container_width=True,key="snr_result_map")
    valid=grid[np.isfinite(grid)]
    if valid.size:
        c1,c2,c3=st.columns(3); c1.metric("Aircraft samples",len(analysis["path"])); c2.metric("Max SNR",f"{float(np.max(valid)):.1f} dB"); c3.metric("Min SNR",f"{float(np.min(valid)):.1f} dB")
else:
    st.caption("Select Flight Path → draw the line → Select AOI → draw rectangle/polygon → Run Analysis.")
