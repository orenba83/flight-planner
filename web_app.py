import math
import numpy as np
import streamlit as st
import folium
from folium.plugins import Draw
from folium.raster_layers import ImageOverlay
from streamlit_folium import st_folium
from PIL import Image

st.set_page_config(page_title="Flight Planner", page_icon="✈️", layout="wide")
DEFAULT_GAIN=[(30,-35.0),(60,-24.5),(90,-12.5),(500,-1.0),(1000,3.0),(2000,3.0),(3000,2.0),(4500,2.0),(5000,2.0),(6000,4.0)]
R=6371000.0; C=299792458.0

def interp_gain(f):
    if f<=DEFAULT_GAIN[0][0]: return DEFAULT_GAIN[0][1]
    if f>=DEFAULT_GAIN[-1][0]: return DEFAULT_GAIN[-1][1]
    for i in range(1,len(DEFAULT_GAIN)):
        f0,g0=DEFAULT_GAIN[i-1]; f1,g1=DEFAULT_GAIN[i]
        if f<=f1:
            t=(f-f0)/(f1-f0); return g0+t*(g1-g0)
    return DEFAULT_GAIN[-1][1]

def hav(lat1,lon1,lat2,lon2):
    p=math.pi/180; a=math.sin((lat2-lat1)*p/2)**2+math.cos(lat1*p)*math.cos(lat2*p)*math.sin((lon2-lon1)*p/2)**2
    return 2*R*math.asin(math.sqrt(a))

def fspl(d,f): return 0 if d<=0 else 20*math.log10(4*math.pi*d*f*1e6/C)

def colors(v,vmin,vmax):
    n=np.clip((v-vmin)/max(vmax-vmin,1e-9),0,1); stops=[(0,(213,43,30)),(.4,(247,148,30)),(.7,(255,221,51)),(1,(46,204,64))]; out=np.zeros((*v.shape,4),np.uint8)
    for i in range(len(stops)-1):
        x0,c0=stops[i]; x1,c1=stops[i+1]; m=(n>=x0)&(n<=x1)
        if np.any(m):
            t=(n[m]-x0)/(x1-x0)
            for ch in range(3): out[...,ch][m]=(c0[ch]+t*(c1[ch]-c0[ch])).astype(np.uint8)
    out[...,3]=190; return out

def make_map(draw=True):
    m=folium.Map(location=[31.8,35.0],zoom_start=7,control_scale=True,tiles='OpenStreetMap')
    if draw: Draw(export=False,position='topleft',draw_options={'polyline':True,'polygon':True,'rectangle':True,'circle':False,'marker':False,'circlemarker':False},edit_options={'edit':True,'remove':True}).add_to(m)
    return m

@st.cache_resource(show_spinner=False)
def load_dtm():
    import srtm
    return srtm.get_data()

def elev(dtm,lat,lon):
    try:
        x=dtm.get_elevation(float(lat),float(lon)); return float(x) if x is not None else 0.0
    except Exception: return 0.0

def rf(dtm,alat,alon,aalt,tlat,tlon,f,tx,gain,pol,sys,noise,use_dtm,los_samples):
    ge=elev(dtm,tlat,tlon) if use_dtm else 0.0
    h=hav(alat,alon,tlat,tlon); d=math.sqrt(h*h+(aalt-ge)**2); pr=tx+gain-fspl(d,f)-pol-sys; blocked=False
    if use_dtm and h>1:
        for i in range(1,los_samples):
            t=i/los_samples; la=alat+(tlat-alat)*t; lo=alon+(tlon-alon)*t; te=elev(dtm,la,lo); ray=aalt+(ge-aalt)*t
            if te>ray+2: blocked=True; break
        if blocked: pr=-150.0
    return ge,h,d,pr,pr-noise,not blocked

st.title('✈️ Flight Planner')
st.caption('Draw both mission geometry elements first. No RF analysis runs until you press Run Analysis.')
with st.sidebar:
    st.header('1. Mission Geometry'); st.write('**Red line = flight path. Blue rectangle/polygon = AOI.**')
    st.header('2. RF Parameters')
    minf=st.number_input('Min Frequency (MHz)',30.,18000.,1000.,1.); maxf=st.number_input('Max Frequency (MHz)',30.,18000.,1000.,1.); steps=st.number_input('Frequency Steps',1,50,1,1)
    alt=st.number_input('Aircraft Altitude (KFT)',0.,100.,35.,1.); tx=st.number_input('Tx Power (dBm)',-50.,100.,37.,.5); pol=st.number_input('Polarization Loss (dB)',0.,30.,3.,.5); sys=st.number_input('System Loss (dB)',0.,30.,0.,.5)
    samples=st.number_input('Flight Path Samples',2,1000,20,1); use_dtm=st.checkbox('Use DTM / SRTM terrain',True); los_samples=st.number_input('DTM LOS Samples',5,100,30,5)
    st.header('3. Display'); vmin=st.number_input('SNR Min (dB)',-200.,200.,-25.,1.); vmax=st.number_input('SNR Max (dB)',-200.,200.,30.,1.)
    run=st.button('🚀 Run Analysis',type='primary',use_container_width=True); clear=st.button('Clear Geometry / Results',use_container_width=True)
if 'drawings' not in st.session_state: st.session_state.drawings=[]
if 'analysis' not in st.session_state: st.session_state.analysis=None
if clear: st.session_state.drawings=[]; st.session_state.analysis=None; st.rerun()

m=make_map(True)
for f in st.session_state.drawings:
    g=f.get('geometry',{}); c=g.get('coordinates',[])
    if g.get('type')=='LineString': folium.PolyLine([[p[1],p[0]] for p in c],color='red',weight=4).add_to(m)
    elif g.get('type')=='Polygon' and c: folium.Polygon([[p[1],p[0]] for p in c[0]],color='blue',fill=True,fill_opacity=.12).add_to(m)
state=st_folium(m,height=650,key='geometry_map',returned_objects=['all_drawings'])
d=state.get('all_drawings') or []
if d!=st.session_state.drawings: st.session_state.drawings=d; st.session_state.analysis=None; st.rerun()
path=None; aoi=None
for f in st.session_state.drawings:
    g=f.get('geometry',{})
    if g.get('type')=='LineString' and path is None: path=[[p[1],p[0]] for p in g.get('coordinates',[])]
    elif g.get('type')=='Polygon' and aoi is None: aoi=[[p[1],p[0]] for p in g.get('coordinates',[[]])[0]]

c1,c2,c3=st.columns(3); c1.success(f'Flight Path: {len(path)} points' if path else 'Flight Path: not selected'); c2.success('AOI: selected' if aoi else 'AOI: not selected'); c3.info('Ready' if path and aoi else 'Draw both before analysis')

if run:
    if not path: st.error('Draw a flight path (line) first.'); st.stop()
    if not aoi: st.error('Draw an AOI (rectangle/polygon) first.'); st.stop()
    if minf>maxf: st.error('Max Frequency must be >= Min Frequency.'); st.stop()
    if vmax<=vmin: st.error('SNR Max must be > SNR Min.'); st.stop()
    dtm=None
    if use_dtm:
        with st.spinner('Loading DTM / SRTM terrain data...'): dtm=load_dtm()
    freqs=[minf] if steps==1 else np.linspace(minf,maxf,int(steps)).tolist(); altm=alt*304.8; noise=-174+10*math.log10(1e6)+6
    gains={f:interp_gain(f) for f in freqs}
    if len(path)>samples:
        cum=[0.0]
        for i in range(1,len(path)): cum.append(cum[-1]+hav(*path[i-1],*path[i]))
        total=cum[-1]; targets=np.linspace(0,total,int(samples)); sampled=[]
        for target in targets:
            if target<=0: sampled.append(path[0]); continue
            if target>=total: sampled.append(path[-1]); continue
            for i in range(1,len(cum)):
                if cum[i]>=target:
                    seg=cum[i]-cum[i-1]; t=(target-cum[i-1])/seg if seg else 0; sampled.append([path[i-1][0]+t*(path[i][0]-path[i-1][0]),path[i-1][1]+t*(path[i][1]-path[i-1][1])]); break
    else: sampled=path
    la=[p[0] for p in aoi]; lo=[p[1] for p in aoi]; mnla,mxla=min(la),max(la); mnlo,mxlo=min(lo),max(lo); ny=nx=55
    lats=np.linspace(mxla,mnla,ny); lons=np.linspace(mnlo,mxlo,nx); grid=np.full((ny,nx),-999.,np.float32); rows=[]; center=[sum(la)/len(la),sum(lo)/len(lo)]
    progress=st.progress(0.,text='Calculating SNR coverage...')
    for f in freqs:
        for i,(alat,alon) in enumerate(sampled,1):
            ge,h,d3,pr,snr,los=rf(dtm,alat,alon,altm,center[0],center[1],f,tx,gains[f],pol,sys,noise,use_dtm,los_samples)
            rows.append({'Freq (MHz)':f,'Aircraft Point':i,'Aircraft Lat':alat,'Aircraft Lon':alon,'Ground Elev (m)':ge,'Horizontal (km)':h/1000,'3D Distance (km)':d3/1000,'Pr (dBm)':pr,'SNR (dB)':snr,'LOS':'OK' if los else 'BLOCKED'})
    total=ny*nx; done=0
    for iy,lat in enumerate(lats):
        for ix,lon in enumerate(lons):
            best=-999.
            for alat,alon in sampled:
                for f in freqs:
                    _,_,_,_,snr,_=rf(dtm,alat,alon,altm,lat,lon,f,tx,gains[f],pol,sys,noise,use_dtm,los_samples); best=max(best,snr)
            grid[iy,ix]=best; done+=1
        progress.progress(done/total,text=f'Calculating SNR coverage... {done}/{total}')
    progress.empty(); st.session_state.analysis={'rows':rows,'grid':grid,'bounds':[[mnla,mnlo],[mxla,mxlo]],'freqs':freqs,'path':sampled,'dtm':use_dtm}

if st.session_state.analysis:
    an=st.session_state.analysis; sn=[r['SNR (dB)'] for r in an['rows']]; st.success('Analysis complete — SNR uses aircraft distance + DTM/SRTM terrain masking.')
    a,b,c,d=st.columns(4); a.metric('Aircraft samples',len(an['path'])); b.metric('Frequencies',len(an['freqs'])); c.metric('Max SNR',f'{max(sn):.1f} dB'); d.metric('Min SNR',f'{min(sn):.1f} dB'); st.dataframe(an['rows'],use_container_width=True)
    rm=make_map(False)
    for f in st.session_state.drawings:
        g=f.get('geometry',{}); c=g.get('coordinates',[])
        if g.get('type')=='LineString': folium.PolyLine([[p[1],p[0]] for p in c],color='red',weight=4).add_to(rm)
        elif g.get('type')=='Polygon' and c: folium.Polygon([[p[1],p[0]] for p in c[0]],color='blue',fill=False).add_to(rm)
    ImageOverlay(image=Image.fromarray(colors(an['grid'],vmin,vmax),mode='RGBA'),bounds=an['bounds'],opacity=.70,interactive=False).add_to(rm); st.subheader('SNR Coverage'); st_folium(rm,height=650,key='results_map')
    st.caption('DTM/SRTM supplies ground elevation, changes 3D propagation distance, and is sampled along each aircraft-to-ground ray for terrain blockage. Blocked rays are forced to -150 dBm.')
else: st.caption('Draw both the flight path and AOI first. No analysis is performed until Run Analysis is pressed.')
