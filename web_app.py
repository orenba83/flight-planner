import math
import io
import base64
import numpy as np
import streamlit as st
import folium
from folium.plugins import Draw
from folium.raster_layers import ImageOverlay
from streamlit_folium import st_folium
from PIL import Image

st.set_page_config(page_title="Flight Planner", page_icon="✈️", layout="wide")

DEFAULT_GAIN = [(30, -35.0), (60, -24.5), (90, -12.5), (500, -1.0),
                (1000, 3.0), (2000, 3.0), (3000, 2.0), (4500, 2.0),
                (5000, 2.0), (6000, 4.0)]


def interp_gain(freq):
    if freq <= DEFAULT_GAIN[0][0]:
        return DEFAULT_GAIN[0][1]
    if freq >= DEFAULT_GAIN[-1][0]:
        return DEFAULT_GAIN[-1][1]
    for i in range(1, len(DEFAULT_GAIN)):
        f0, g0 = DEFAULT_GAIN[i - 1]
        f1, g1 = DEFAULT_GAIN[i]
        if freq <= f1:
            t = (freq - f0) / (f1 - f0)
            return g0 + t * (g1 - g0)
    return DEFAULT_GAIN[-1][1]


def haversine_m(lat1, lon1, lat2, lon2):
    r = 6371000.0
    p = math.pi / 180.0
    dlat = (lat2 - lat1) * p
    dlon = (lon2 - lon1) * p
    a = math.sin(dlat / 2) ** 2 + math.cos(lat1 * p) * math.cos(lat2 * p) * math.sin(dlon / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def fspl_db(distance_m, freq_mhz):
    if distance_m <= 0:
        return 0.0
    return 20.0 * math.log10(4.0 * math.pi * distance_m * freq_mhz * 1e6 / 299792458.0)


def color_rgba(values, vmin=-25.0, vmax=30.0):
    norm = np.clip((values - vmin) / (vmax - vmin), 0, 1)
    stops = [(0.0, (213, 43, 30)), (0.4, (247, 148, 30)),
             (0.7, (255, 221, 51)), (1.0, (46, 204, 64))]
    out = np.zeros((*values.shape, 4), dtype=np.uint8)
    for i in range(len(stops) - 1):
        x0, c0 = stops[i]
        x1, c1 = stops[i + 1]
        mask = (norm >= x0) & (norm <= x1)
        if not np.any(mask):
            continue
        t = (norm[mask] - x0) / (x1 - x0)
        for ch in range(3):
            out[..., ch][mask] = (c0[ch] + t * (c1[ch] - c0[ch])).astype(np.uint8)
    out[..., 3] = 190
    return out


def make_map():
    m = folium.Map(location=[31.8, 35.0], zoom_start=7, control_scale=True, tiles="OpenStreetMap")
    Draw(export=False, position="topleft", draw_options={
        "polyline": True, "polygon": True, "rectangle": True,
        "circle": False, "marker": True, "circlemarker": False
    }, edit_options={"edit": True, "remove": True}).add_to(m)
    return m


st.title("✈️ Flight Planner")
st.caption("Cloud Web Preview — Streamlit version. The original PyQt6 desktop application remains unchanged.")

with st.sidebar:
    st.header("Mission Ops")
    min_freq = st.number_input("Min Frequency (MHz)", 30.0, 18000.0, 100.0, 1.0)
    max_freq = st.number_input("Max Frequency (MHz)", 30.0, 18000.0, 100.0, 1.0)
    steps = st.number_input("Frequency Steps", 1, 50, 1, 1)
    altitude_kft = st.number_input("Aircraft Altitude (KFT)", 0.0, 100.0, 35.0, 1.0)

    st.header("RF & Engine Parameters")
    los = st.checkbox("Enable Line-of-Sight / terrain masking", value=False,
                      help="Terrain masking is disabled by default in the cloud preview. Enable only after terrain data is available in the cloud runtime.")
    path_samples = st.number_input("Flight Path Samples", 1, 1000, 10, 1)
    tx_power = st.number_input("Tx Power (dBm)", -50.0, 100.0, 37.0, 0.5)
    pol_loss = st.number_input("Polarization Loss (dB)", 0.0, 30.0, 3.0, 0.5)
    sys_loss = st.number_input("System Loss (dB)", 0.0, 30.0, 0.0, 0.5)
    required = st.number_input("Required Level / Green Threshold (dBm)", -150.0, 50.0, -90.0, 1.0)

    st.header("Display")
    snr_min = st.number_input("SNR Min (dB)", -200.0, 200.0, -25.0, 1.0)
    snr_max = st.number_input("SNR Max (dB)", -200.0, 200.0, 30.0, 1.0)
    run = st.button("🚀 Run Analysis", type="primary", use_container_width=True)
    clear = st.button("Clear Map", use_container_width=True)

if "drawings" not in st.session_state:
    st.session_state.drawings = []
if "analysis" not in st.session_state:
    st.session_state.analysis = None

if clear:
    st.session_state.drawings = []
    st.session_state.analysis = None
    st.rerun()

m = make_map()

if st.session_state.drawings:
    for feature in st.session_state.drawings:
        geom = feature.get("geometry", {})
        coords = geom.get("coordinates", [])
        if geom.get("type") == "LineString":
            pts = [[p[1], p[0]] for p in coords]
            folium.PolyLine(pts, color="red", weight=4).add_to(m)
        elif geom.get("type") == "Polygon" and coords:
            pts = [[p[1], p[0]] for p in coords[0]]
            folium.Polygon(pts, color="blue", fill=True, fill_opacity=0.12).add_to(m)

map_state = st_folium(m, height=650, width=None, returned_objects=["all_drawings"])

new_drawings = map_state.get("all_drawings") or []
if new_drawings and new_drawings != st.session_state.drawings:
    st.session_state.drawings = new_drawings
    st.rerun()

# Collect the first line as the flight path and the first polygon/rectangle as AOI.
path = None
polygon = None
for feature in st.session_state.drawings:
    geom = feature.get("geometry", {})
    if geom.get("type") == "LineString" and path is None:
        path = [[p[1], p[0]] for p in geom.get("coordinates", [])]
    elif geom.get("type") == "Polygon" and polygon is None:
        polygon = [[p[1], p[0]] for p in geom.get("coordinates", [[]])[0]]

if path:
    st.info(f"Flight path: {len(path)} points selected.")
if polygon:
    st.info("AOI: polygon/rectangle selected.")

if run:
    if not path and not polygon:
        st.error("Draw a flight path (line) or an AOI (rectangle/polygon) on the map first.")
    else:
        if min_freq > max_freq:
            st.error("Max Frequency must be greater than or equal to Min Frequency.")
            st.stop()
        freqs = [min_freq] if steps == 1 else np.linspace(min_freq, max_freq, int(steps)).tolist()
        rx_gain = float(np.mean([interp_gain(f) for f in freqs]))
        alt_m = altitude_kft * 304.8
        bw_hz = 1e6
        nf = 6.0
        noise_floor = -174.0 + 10.0 * math.log10(bw_hz) + nf

        if path:
            tx_lat, tx_lon = path[0]
            eval_points = path
        else:
            tx_lat = sum(p[0] for p in polygon) / len(polygon)
            tx_lon = sum(p[1] for p in polygon) / len(polygon)
            eval_points = [[tx_lat, tx_lon]]

        rows = []
        for f in freqs:
            for i, (lat, lon) in enumerate(eval_points, 1):
                horiz = haversine_m(tx_lat, tx_lon, lat, lon)
                d3 = math.sqrt(horiz * horiz + alt_m * alt_m)
                loss = fspl_db(d3, f)
                pr = tx_power + interp_gain(f) - loss - pol_loss - sys_loss
                snr = pr - noise_floor
                rows.append({"Freq (MHz)": f, "Point": i, "Lat": lat, "Lon": lon,
                             "Elev (m)": alt_m, "Pr (dBm)": pr, "SNR (dB)": snr})

        # Build an SNR coverage grid when an AOI is available.
        grid = None
        bounds = None
        if polygon:
            lats = [p[0] for p in polygon]
            lons = [p[1] for p in polygon]
            min_lat, max_lat = min(lats), max(lats)
            min_lon, max_lon = min(lons), max(lons)
            ny, nx = 55, 55
            lat_vals = np.linspace(max_lat, min_lat, ny)
            lon_vals = np.linspace(min_lon, max_lon, nx)
            grid = np.empty((ny, nx), dtype=np.float32)
            for iy, lat in enumerate(lat_vals):
                for ix, lon in enumerate(lon_vals):
                    best = -999.0
                    for f in freqs:
                        horiz = haversine_m(tx_lat, tx_lon, lat, lon)
                        d3 = math.sqrt(horiz * horiz + alt_m * alt_m)
                        pr = tx_power + interp_gain(f) - fspl_db(d3, f) - pol_loss - sys_loss
                        best = max(best, pr - noise_floor)
                    grid[iy, ix] = best
            bounds = [[min_lat, min_lon], [max_lat, max_lon]]

        st.session_state.analysis = {"rows": rows, "grid": grid, "bounds": bounds,
                                     "required": required, "freqs": freqs}

analysis = st.session_state.analysis
if analysis:
    st.success("Analysis complete.")
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Points", len(analysis["rows"]))
    c2.metric("Frequencies", len(analysis["freqs"]))
    snrs = [r["SNR (dB)"] for r in analysis["rows"]]
    c3.metric("Max SNR", f"{max(snrs):.1f} dB")
    c4.metric("Min SNR", f"{min(snrs):.1f} dB")

    st.dataframe(analysis["rows"], use_container_width=True)
    if analysis["grid"] is not None:
        overlay_map = make_map()
        bounds = analysis["bounds"]
        rgba = color_rgba(analysis["grid"], snr_min, snr_max)
        img = Image.fromarray(rgba, mode="RGBA")
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        data_url = "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode("ascii")
        ImageOverlay(image=data_url, bounds=bounds, opacity=0.70, interactive=False).add_to(overlay_map)
        folium.Rectangle(bounds=bounds, color="blue", fill=False).add_to(overlay_map)
        st.subheader("SNR Coverage")
        st_folium(overlay_map, height=650, width=None)
        st.caption("Coverage calculation uses the same free-space RF approach as the current desktop worker. Terrain masking is not applied in this first cloud preview.")
else:
    st.caption("Draw a LineString for the flight path or a Rectangle/Polygon for the AOI, then press Run Analysis.")
