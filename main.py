
import sys
import io
import json
import math
import time
import logging
import traceback
import numpy as np
from PIL import Image
import srtm
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QHBoxLayout, QVBoxLayout,
    QLabel, QPushButton, QGroupBox, QFormLayout, QDoubleSpinBox, QTextEdit,
    QTabWidget, QTableWidget, QTableWidgetItem, QHeaderView, QSpinBox, QCheckBox,
    QProgressBar, QComboBox, QDialog, QFileDialog, QMessageBox, QToolBar
)
from PyQt6.QtCore import Qt, QObject, pyqtSlot, pyqtSignal, QThread
from PyQt6.QtGui import QAction
from PyQt6.QtWebEngineWidgets import QWebEngineView
from PyQt6.QtWebChannel import QWebChannel
import os
import datetime
import logging
import base64
import io
from PIL import Image as PILImage
import folium
from folium.plugins import Draw
from folium.raster_layers import ImageOverlay
import branca.colormap as cm
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.figure import Figure

# Logging setup
LOG_DIR = os.path.join(os.getcwd(), 'logs')
os.makedirs(LOG_DIR, exist_ok=True)
LOG_FILE = os.path.join(LOG_DIR, datetime.datetime.now().strftime('run_%Y%m%d_%H%M%S.log'))
logger = logging.getLogger('flight_planner')
logger.setLevel(logging.DEBUG)
fh = logging.FileHandler(LOG_FILE, encoding='utf-8')
fh.setLevel(logging.DEBUG)
formatter = logging.Formatter('%(asctime)s %(levelname)s: %(message)s')
fh.setFormatter(formatter)
logger.addHandler(fh)

def log_event(msg):
    try:
        logger.info(msg)
    except Exception:
        pass

# ---------------------------------------------------------------------------
# Physics / RF constants
# ---------------------------------------------------------------------------
EARTH_RADIUS_M = 6371000.0
K_FACTOR = 4.0 / 3.0  # standard "effective earth radius" factor used for radio LOS
LOS_SAMPLES = 20       # fixed internal resolution for the terrain-masking check

COLOR_STOPS = [
    (0.0, (213, 43, 30)),    # red    - weakest signal
    (0.4, (247, 148, 30)),   # orange
    (0.7, (255, 221, 51)),   # yellow
    (1.0, (46, 204, 64)),    # green  - at/above the required level
]


def value_to_rgba(norm_array):
    h, w = norm_array.shape
    rgba = np.zeros((h, w, 4), dtype=np.uint8)
    clipped = np.clip(norm_array, 0.0, 1.0)
    for k in range(len(COLOR_STOPS) - 1):
        x0, c0 = COLOR_STOPS[k]
        x1, c1 = COLOR_STOPS[k + 1]
        mask = (clipped >= x0) & (clipped <= x1)
        if not np.any(mask):
            continue
        t = (clipped[mask] - x0) / (x1 - x0) if x1 > x0 else 0
        for ch in range(3):
            rgba[..., ch][mask] = (c0[ch] + t * (c1[ch] - c0[ch])).astype(np.uint8)
    rgba[..., 3] = 190
    return rgba


def snr_to_rgba(snr_grid, vmin=-25.0, vmax=30.0):
    norm = (snr_grid - vmin) / (vmax - vmin)
    norm = np.clip(norm, 0.0, 1.0)
    return value_to_rgba(norm)


def build_raster_overlay(value_grid, bounds, vmin=None, vmax=None, upsample_factor=4, opacity=0.75):
    """Turns a coarse 2D grid of values into a smooth, georeferenced color image
    overlay. Pass explicit vmin/vmax to keep the color scale IDENTICAL across
    different views (otherwise each grid would auto-scale to its own min/max)."""
    if vmin is None or vmax is None:
        vmin = float(np.min(value_grid)) if vmin is None else vmin
        vmax = float(np.max(value_grid)) if vmax is None else vmax
    if vmax == vmin:
        vmax = vmin + 1.0

    img_f = Image.fromarray(value_grid.astype(np.float32), mode="F")
    new_size = (value_grid.shape[1] * upsample_factor, value_grid.shape[0] * upsample_factor)
    smooth_array = np.array(img_f.resize(new_size, Image.BICUBIC))
    smooth_array = np.flipud(smooth_array)

    norm = (smooth_array - vmin) / (vmax - vmin)
    rgba = value_to_rgba(norm)

    overlay = ImageOverlay(
        image=rgba, bounds=bounds, opacity=opacity, interactive=False, cross_origin=False,
    )
    return overlay, vmin, vmax


class Bridge(QObject):
    def __init__(self, map_view):
        super().__init__()
        self.map_view = map_view

    @pyqtSlot(str)
    def handle_aoi(self, geojson_str):
        try:
            data = json.loads(geojson_str)
            coords = data['geometry']['coordinates'][0]
            lats = [p[1] for p in coords]
            lons = [p[0] for p in coords]
            self.map_view.aoi_bounds = {
                'min_lat': min(lats), 'max_lat': max(lats),
                'min_lon': min(lons), 'max_lon': max(lons)
            }
            self.map_view.dashboard.results_box.append("AOI selected successfully.")
            # Also print to stdout for automated verification
            print('AOI_SELECTED:', json.dumps(self.map_view.aoi_bounds), flush=True)
            log_event(f"AOI_SELECTED: {json.dumps(self.map_view.aoi_bounds)}")
        except Exception as e:
            print('AOI handle error:', e, flush=True)
            traceback.print_exc()
            log_event(f"AOI handle error: {e}")

    @pyqtSlot(str)
    def update_waypoints(self, waypoints_json):
        try:
            waypoints = json.loads(waypoints_json)
            self.map_view.waypoints = waypoints
            self.map_view.dashboard.results_box.append(f"Flight path updated: {len(waypoints)} points.")
            # Also print to stdout so the running process can be verified
            print('WAYPOINTS_UPDATED:', json.dumps({'n': len(waypoints), 'waypoints': waypoints}), flush=True)
            log_event(f"WAYPOINTS_UPDATED: {json.dumps({'n': len(waypoints), 'waypoints': waypoints})}")
        except Exception as e:
            print('update_waypoints error:', e, flush=True)
            traceback.print_exc()
            log_event(f"update_waypoints error: {e}")

    @pyqtSlot(float, float)
    def request_calc_details(self, lat, lon):
        self.map_view.show_calc_details(lat, lon)

    @pyqtSlot(str)
    def log(self, msg):
        try:
            print('JS_LOG:', msg, flush=True)
            log_event(f'JS_LOG: {msg}')
            # also show basic JS logs in the dashboard results box for visibility
            try:
                if hasattr(self.map_view, 'dashboard') and self.map_view.dashboard:
                    self.map_view.dashboard.results_box.append(f"JS: {msg}")
            except Exception:
                pass
        except Exception:
            pass


class AntennaPatternDialog(QDialog):
    """Simple gain-vs-frequency plot built from the Rx Antenna Gain table."""

    def __init__(self, freqs, gains, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Antenna Pattern (Gain vs Frequency)")
        self.resize(650, 450)
        layout = QVBoxLayout()

        fig = Figure(figsize=(6, 4))
        canvas = FigureCanvas(fig)
        ax = fig.add_subplot(111)
        ax.plot(freqs, gains, marker='o', markersize=3, color='#2c7be5', linewidth=1.5)
        ax.set_xlabel("Frequency (MHz)")
        ax.set_ylabel("Gain (dBi)")
        ax.set_title("Rx Antenna Gain vs Frequency")
        ax.grid(True, alpha=0.3)
        fig.tight_layout()

        layout.addWidget(canvas)
        self.setLayout(layout)


class Dashboard(QWidget):
    def __init__(self):
        super().__init__()
        self.setFixedWidth(380)
        main_layout = QVBoxLayout()

        self.tabs = QTabWidget()
        self.tab_mission = QWidget()
        self.tabs.addTab(self.tab_mission, "Mission Ops")
        
        # Dialogs instead of tabs for ENG and DATA
        self.eng_dialog = QDialog(self)
        self.eng_dialog.setWindowTitle("Engineering (RF)")
        
        self.data_dialog = QDialog(self)
        self.data_dialog.setWindowTitle("Data Table")
        self.data_dialog.resize(800, 600)

        self.setup_mission_tab()
        self.setup_engineer_tab()
        self.setup_data_tab()

        main_layout.addWidget(self.tabs)

        # Progress bar restored to the dashboard layout permanently
        progress_layout = QHBoxLayout()
        self.eta_label = QLabel("ETA: --:--")
        self.eta_label.setStyleSheet("font-weight: bold; color: #2c7be5;")
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setFixedWidth(200)
        
        progress_layout.addWidget(self.progress_bar)
        progress_layout.addWidget(self.eta_label)
        
        main_layout.addWidget(QLabel("Progress & Status:"))
        main_layout.addLayout(progress_layout)

        self.results_box = QTextEdit()
        self.results_box.setReadOnly(True)
        self.results_box.setFixedHeight(120)
        main_layout.addWidget(self.results_box)

        self.setLayout(main_layout)

    def setup_mission_tab(self):
        layout = QVBoxLayout()

        settings_group = QGroupBox("Frequency Range & Altitude")
        form = QFormLayout()

        self.freq_min_input = QDoubleSpinBox()
        self.freq_min_input.setRange(30, 18000)
        self.freq_min_input.setValue(100)
        self.freq_min_input.setSuffix(" MHz")
        form.addRow("Min Frequency:", self.freq_min_input)

        self.freq_max_input = QDoubleSpinBox()
        self.freq_max_input.setRange(30, 18000)
        self.freq_max_input.setValue(100)
        self.freq_max_input.setSuffix(" MHz")
        form.addRow("Max Frequency:", self.freq_max_input)

        self.freq_steps_input = QSpinBox()
        self.freq_steps_input.setRange(1, 50)
        self.freq_steps_input.setValue(1)
        form.addRow("Frequency Steps:", self.freq_steps_input)

        self.freq_min_input.valueChanged.connect(self._update_freq_steps_bounds)
        self.freq_max_input.valueChanged.connect(self._update_freq_steps_bounds)

        self.alt_input = QDoubleSpinBox()
        self.alt_input.setRange(0, 100)
        self.alt_input.setValue(35)
        self.alt_input.setSuffix(" KFT")
        form.addRow("Aircraft Altitude:", self.alt_input)

        settings_group.setLayout(form)
        layout.addWidget(settings_group)

        self.aoi_btn = QPushButton("Select AOI (Draw Rectangle)")
        self.path_btn = QPushButton("Select Flight Path (Click Map)")
        self.run_btn = QPushButton("Run Analysis")
        self.clear_btn = QPushButton("Clear Map")

        layout.addWidget(self.aoi_btn)
        layout.addWidget(self.path_btn)
        layout.addWidget(self.run_btn)
        layout.addWidget(self.clear_btn)

        self.display_group = QGroupBox("Display Controls")
        display_form = QFormLayout()

        self.mode_display_combo = QComboBox()
        self.mode_display_combo.addItems(["Average (Full Path)", "Single Point"])
        display_form.addRow("Show:", self.mode_display_combo)

        self.point_display_input = QSpinBox()
        self.point_display_input.setRange(1, 1)
        self.point_display_input.setEnabled(False)
        display_form.addRow("Aircraft Waypoint #:", self.point_display_input)

        self.freq_display_combo = QComboBox()
        display_form.addRow("Frequency:", self.freq_display_combo)

        self.snr_min_input = QDoubleSpinBox()
        self.snr_min_input.setRange(-200, 200)
        self.snr_min_input.setValue(-25.0)
        self.snr_min_input.setSuffix(" dB")
        display_form.addRow("SNR Min:", self.snr_min_input)

        self.snr_max_input = QDoubleSpinBox()
        self.snr_max_input.setRange(-200, 200)
        self.snr_max_input.setValue(30.0)
        self.snr_max_input.setSuffix(" dB")
        display_form.addRow("SNR Max:", self.snr_max_input)

        self.apply_scale_btn = QPushButton("Apply Color Scale")
        display_form.addRow("", self.apply_scale_btn)

        self.display_group.setLayout(display_form)
        self.display_group.setVisible(False)
        layout.addWidget(self.display_group)

        self.mode_display_combo.currentTextChanged.connect(
            lambda text: self.point_display_input.setEnabled(text == "Single Point")
        )

        layout.addStretch()
        self.tab_mission.setLayout(layout)

    def _update_freq_steps_bounds(self):
        # Ensure max_freq is not smaller than min_freq
        if self.freq_max_input.value() < self.freq_min_input.value():
            self.freq_max_input.setValue(self.freq_min_input.value())
        if self.freq_max_input.value() > self.freq_min_input.value():
            self.freq_steps_input.setMinimum(2)
            if self.freq_steps_input.value() < 2:
                self.freq_steps_input.setValue(2)
        else:
            self.freq_steps_input.setMinimum(1)

    def setup_engineer_tab(self):
        layout = QVBoxLayout()

        params_group = QGroupBox("RF & Engine Parameters")
        form = QFormLayout()

        self.los_checkbox = QCheckBox("Enable Line-of-Sight (DTM Masking)")
        self.los_checkbox.setChecked(True)
        form.addRow("", self.los_checkbox)

        self.path_samples_input = QSpinBox()
        self.path_samples_input.setRange(1, 1000)
        self.path_samples_input.setValue(10)
        form.addRow("Flight Path Samples (X):", self.path_samples_input)

        self.tx_power_input = QDoubleSpinBox()
        self.tx_power_input.setRange(-50, 100)
        self.tx_power_input.setValue(37)
        self.tx_power_input.setSuffix(" dBm")
        form.addRow("Tx Power (Pt):", self.tx_power_input)

        self.pol_loss_input = QDoubleSpinBox()
        self.pol_loss_input.setRange(0, 30)
        self.pol_loss_input.setValue(3.0)
        self.pol_loss_input.setSuffix(" dB")
        form.addRow("Polarization Loss (Lpol):", self.pol_loss_input)

        self.sys_loss_input = QDoubleSpinBox()
        self.sys_loss_input.setRange(0, 30)
        self.sys_loss_input.setValue(0.0)
        self.sys_loss_input.setSuffix(" dB")
        form.addRow("System Loss (Lsys):", self.sys_loss_input)

        self.required_level_input = QDoubleSpinBox()
        self.required_level_input.setRange(-150, 50)
        self.required_level_input.setValue(-90.0)
        self.required_level_input.setSuffix(" dBm")
        form.addRow("Required Level (Green Threshold):", self.required_level_input)

        params_group.setLayout(form)
        layout.addWidget(params_group)

        layout.addWidget(QLabel("Rx Antenna Gain (Gr) by Frequency:"))
        self.gain_table = QTableWidget()
        self.gain_table.setColumnCount(2)
        self.gain_table.setHorizontalHeaderLabels(["Freq (MHz)", "Gain (dBi)"])
        self.gain_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)

        default_data = [
            (30, -35.0),
            (60, -24.5),
            (90, -12.5),
            (500, -1.0),
            (1000, 3.0),
            (2000, 3.0),
            (3000, 2.0),
            (4500, 2.0),
            (5000, 2.0),
            (6000, 4.0)
        ]
        
        self.gain_table.setRowCount(len(default_data))
        for i, (f, g) in enumerate(default_data):
            self.gain_table.setItem(i, 0, QTableWidgetItem(str(f)))
            self.gain_table.setItem(i, 1, QTableWidgetItem(str(g)))
            item_f = self.gain_table.item(i, 0)
            item_f.setFlags(item_f.flags() ^ Qt.ItemFlag.ItemIsEditable)

        layout.addWidget(self.gain_table)
        self.eng_dialog.setLayout(layout)

    def setup_data_tab(self):
        layout = QVBoxLayout()
        self.data_table = QTableWidget()
        self.data_table.setColumnCount(7)
        self.data_table.setHorizontalHeaderLabels(
            ["Freq(MHz)", "Point", "Lat", "Lon", "Elev(m)", "Pr(dBm)", "SNR(dB)"]
        )
        self.data_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        layout.addWidget(self.data_table)
        
        self.export_btn = QPushButton("Export to CSV")
        self.export_btn.clicked.connect(self.export_csv)
        layout.addWidget(self.export_btn)
        
        self.data_dialog.setLayout(layout)

    def export_csv(self):
        path, _ = QFileDialog.getSaveFileName(self, "Save CSV", "", "CSV Files (*.csv)")
        if path:
            try:
                with open(path, 'w', encoding='utf-8') as f:
                    f.write("Freq(MHz),Point,Lat,Lon,Elev(m),Pr(dBm),SNR(dB)\n")
                    for row in range(self.data_table.rowCount()):
                        row_data = []
                        for col in range(self.data_table.columnCount()):
                            item = self.data_table.item(row, col)
                            row_data.append(item.text() if item else "")
                        f.write(",".join(row_data) + "\n")
                QMessageBox.information(self, "Success", "Data exported successfully.")
            except Exception as e:
                QMessageBox.critical(self, "Error", f"Could not save file: {e}")

    def populate_display_controls(self, freq_list, num_waypoints):
        self.freq_display_combo.blockSignals(True)
        self.freq_display_combo.clear()
        for f in freq_list:
            self.freq_display_combo.addItem(f"{f:.2f} MHz")
        self.freq_display_combo.blockSignals(False)

        self.point_display_input.blockSignals(True)
        self.point_display_input.setRange(1, max(num_waypoints, 1))
        self.point_display_input.blockSignals(False)

        self.display_group.setVisible(True)

    def get_rx_gain(self, target_freq):
        rows = self.gain_table.rowCount()
        freqs = [float(self.gain_table.item(r, 0).text()) for r in range(rows)]
        gains = [float(self.gain_table.item(r, 1).text()) for r in range(rows)]

        if target_freq <= freqs[0]:
            return gains[0]
        if target_freq >= freqs[-1]:
            return gains[-1]

        for i in range(1, rows):
            if freqs[i] >= target_freq:
                f0, f1 = freqs[i - 1], freqs[i]
                g0, g1 = gains[i - 1], gains[i]
                t = (target_freq - f0) / (f1 - f0) if f1 > f0 else 0
                return g0 + t * (g1 - g0)
        return gains[-1]

    def get_frequency_list(self):
        min_f = float(self.freq_min_input.value())
        max_f = float(self.freq_max_input.value())
        steps = max(1, int(self.freq_steps_input.value()))
        if steps == 1:
            return [min_f]
        return [min_f + (max_f - min_f) * i / (steps - 1) for i in range(steps)]


class MapView(QWidget):
    def __init__(self, dashboard):
        super().__init__()
        self.dashboard = dashboard
        self.waypoints = []
        self.aoi_bounds = None
        self.elevation_data = srtm.get_data()

        self.analysis_results = {}
        self.analysis_bounds = None
        self.analysis_waypoints = None
        self.analysis_freqs = []
        self.analysis_elevation_grid = None
        self.analysis_vmin = None
        self.analysis_vmax = None
        self.analysis_running = False
        self.last_snr_grid = None
        self.last_snr_bounds = None

        self.web_view = QWebEngineView()
        layout = QVBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.web_view)
        self.setLayout(layout)

        self.channel = QWebChannel()
        self.bridge = Bridge(self)
        self.channel.registerObject("bridge", self.bridge)
        self.web_view.page().setWebChannel(self.channel)

        self.dashboard.aoi_btn.clicked.connect(self.enable_aoi_draw)
        self.dashboard.path_btn.clicked.connect(self.enable_path_mode)
        self.dashboard.run_btn.clicked.connect(self.trigger_run)
        self.dashboard.clear_btn.clicked.connect(self.clear_everything)

        self.dashboard.mode_display_combo.currentTextChanged.connect(self.refresh_display)
        self.dashboard.point_display_input.valueChanged.connect(self.refresh_display)
        self.dashboard.freq_display_combo.currentTextChanged.connect(self.refresh_display)
        self.dashboard.apply_scale_btn.clicked.connect(self.apply_color_scale)

        self.redraw_map()

    def trigger_run(self):
        # Bridge between GUI and run_analysis with logging
        try:
            self.dashboard.results_box.append('Triggering analysis...')
            log_event('User triggered Run Analysis')
        except Exception:
            pass
        try:
            self.run_analysis()
        except Exception as e:
            log_event(f'trigger_run error: {e}')

    def enable_aoi_draw(self):
        # Ask the page to run startAOIDraw but wait until the bridge/map are ready.
        self.dashboard.results_box.append("Draw a rectangle on the map (AOI).")
        log_event('Button pressed: enable_aoi_draw')
        js = '''(function(){
            function doDraw(){
                if (window.startAOIDraw){
                    try{
                        if (window._aoiClickHandler) {
                            var m = window.map || window.__MAPVAR__ || null;
                            if (m) { m.off('click', window._aoiClickHandler); window._aoiClickHandler = null; window._aoiPoints = []; }
                            return 'stopped';
                        }
                        var r = window.startAOIDraw();
                        if (typeof r === 'undefined' || r === 'not-ready') { setTimeout(doDraw,150); return 'retrying'; }
                        return r;
                    } catch(e){ return 'error:'+e; }
                }
                setTimeout(doDraw,150);
            }
            return doDraw();
        })();'''
        try:
            def cb(res):
                try:
                    self.dashboard.results_box.append(f"JS: {res}")
                    log_event(f"JS call startAOIDraw result: {res}")
                except Exception:
                    pass
            self.web_view.page().runJavaScript(js, cb)
        except Exception as e:
            log_event(f'enable_aoi_draw error: {e}')
            try:
                self.web_view.page().runJavaScript(js)
            except Exception as e2:
                log_event(f'enable_aoi_draw fallback error: {e2}')
                pass

    def enable_path_mode(self):
        # Ask the page to enable PATH mode; wait until setMode is available.
        self.dashboard.results_box.append("Click on the map to add plane waypoints (draggable).")
        log_event('Button pressed: enable_path_mode')
        js = '''(function(){
            function doSet(){
                if (window.setMode){
                    try{
                        if (window._fp_clickHandler) {
                            window.setMode('NONE');
                            return 'stopped';
                        }
                        var r = window.setMode('PATH');
                        if (typeof r === 'undefined' || r === 'not-ready') { setTimeout(doSet,150); return 'retrying'; }
                        return r;
                    } catch(e){ return 'error:'+e; }
                }
                setTimeout(doSet,150);
            }
            return doSet();
        })();'''
        try:
            def cb2(res):
                try:
                    self.dashboard.results_box.append(f"JS: {res}")
                    log_event(f"JS call setMode result: {res}")
                except Exception:
                    pass
            self.web_view.page().runJavaScript(js, cb2)
        except Exception as e:
            log_event(f'enable_path_mode error: {e}')
            try:
                self.web_view.page().runJavaScript(js)
            except Exception as e2:
                log_event(f'enable_path_mode fallback error: {e2}')
                pass

    def clear_everything(self):
        # Clear AOI and path on both Python and JS sides without resetting map zoom
        try:
            log_event('Button pressed: clear_everything')
            self.analysis_results = {}
            self.analysis_bounds = None
            self.analysis_waypoints = None
            self.analysis_freqs = []
            self.analysis_elevation_grid = None
            self.last_snr_grid = None
            self.last_snr_bounds = None
            self.dashboard.freq_display_combo.clear()
            self.dashboard.data_table.setRowCount(0)
            self.dashboard.display_group.setVisible(False)
            self.dashboard.snr_min_input.setValue(-25.0)
            self.dashboard.snr_max_input.setValue(30.0)
            self.waypoints = []
            self.aoi_bounds = None
            # Call JS functions to clear layers
            try:
                self.web_view.page().runJavaScript('if(window.clearMap) clearMap(); else { if(window.clearPath) clearPath(); if(window.clearAOI) clearAOI(); if(window.clearSNRLegend) clearSNRLegend(); }', lambda r: log_event(f'clear_map_js_result: {r}'))
            except Exception as e:
                log_event(f'clear_everything js call error: {e}')
            self.dashboard.results_box.append('Map cleared (AOI and path removed).')
        except Exception as e:
            log_event(f'clear_everything error: {e}')
            traceback.print_exc()

    def apply_color_scale(self):
        try:
            if self.last_snr_grid is None or self.last_snr_bounds is None:
                QMessageBox.information(self.dashboard, "Color Scale", "No SNR heatmap is available yet. Run analysis first.")
                return

            vmin = float(self.dashboard.snr_min_input.value())
            vmax = float(self.dashboard.snr_max_input.value())
            if vmax <= vmin:
                QMessageBox.warning(self.dashboard, "Color Scale", "SNR Max must be greater than SNR Min.")
                return

            grid = np.asarray(self.last_snr_grid, dtype=np.float32)
            rgba = snr_to_rgba(grid, vmin=vmin, vmax=vmax)
            pil_img = PILImage.fromarray(rgba.astype(np.uint8), mode='RGBA')
            buf = io.BytesIO()
            pil_img.save(buf, format='PNG')
            data_url = 'data:image/png;base64,' + base64.b64encode(buf.getvalue()).decode('ascii')

            bounds = self.last_snr_bounds
            js = f"(function(){{ if(window.addSNROverlay){{ return addSNROverlay('{data_url}', [[{bounds[0][0]},{bounds[0][1]}],[{bounds[1][0]},{bounds[1][1]}]], {vmin}, {vmax}); }} else return 'no-adder'; }})();"
            self.web_view.page().runJavaScript(js, lambda res: (log_event(f"Apply color scale result: {res}"), self.dashboard.results_box.append(f"Color scale updated: {vmin:.1f} dB to {vmax:.1f} dB")))
            self.web_view.page().runJavaScript(f"(function(){{ if(window.addSNRLegend){{ return addSNRLegend({vmin}, {vmax}); }} else return 'no-legend'; }})();", lambda res: log_event(f"Updated legend result: {res}"))
        except Exception as e:
            log_event(f'apply_color_scale error: {e}')
            traceback.print_exc()

    def get_interpolated_path(self, waypoints, num_samples):
        if not waypoints:
            return []
        if len(waypoints) == 1 or num_samples <= 1:
            return [waypoints[0]]

        def dist(p1, p2):
            return math.sqrt((p1[0] - p2[0]) ** 2 + (p1[1] - p2[1]) ** 2)

        total_len = 0
        segs = [0]
        for i in range(1, len(waypoints)):
            total_len += dist(waypoints[i - 1], waypoints[i])
            segs.append(total_len)

        if total_len == 0:
            return [waypoints[0]]

        step = total_len / (num_samples - 1)
        interpolated = []
        for i in range(num_samples):
            target = i * step
            for j in range(1, len(segs)):
                if segs[j] >= target or j == len(segs) - 1:
                    seg_len = segs[j] - segs[j - 1]
                    frac = (target - segs[j - 1]) / seg_len if seg_len > 0 else 0
                    p1 = waypoints[j - 1]
                    p2 = waypoints[j]
                    new_point = [p1[0] + frac * (p2[0] - p1[0]), p1[1] + frac * (p2[1] - p1[1])]
                    interpolated.append(new_point)
                    break
        return interpolated


# Provide lightweight stub implementations for missing MapView methods so the app starts
# (these are minimal safe fallbacks; the full analysis/redraw functionality can be restored later).

# Background worker for analysis
class AnalysisWorker(QObject):
    finished = pyqtSignal(object)
    progress = pyqtSignal(int)
    error = pyqtSignal(str)

    def __init__(self, params):
        super().__init__()
        self.params = params

    @pyqtSlot()
    def run(self):
        try:
            # Unpack params
            waypoints = self.params.get('waypoints', [])
            aoi_bounds = self.params.get('aoi_bounds')
            freqs = self.params.get('freqs', [100.0, 500.0, 1000.0])
            num_samples = int(self.params.get('path_samples', 10))
            Pt_dbm = float(self.params.get('tx_power_dbm', 37.0))
            Lpol = float(self.params.get('pol_loss_db', 3.0))
            Lsys = float(self.params.get('sys_loss_db', 0.0))
            alt_kft = float(self.params.get('alt_kft', 35.0))
            alt_m = alt_kft * 1000.0 * 0.3048

            # Build interpolated path
            interp_path = waypoints if len(waypoints) <= 1 else self._interp_path(waypoints, max(1, num_samples))

            # Prepare table rows
            rows = []
            # transmitter location
            if aoi_bounds:
                tx_lat = (aoi_bounds['min_lat'] + aoi_bounds['max_lat']) / 2.0
                tx_lon = (aoi_bounds['min_lon'] + aoi_bounds['max_lon']) / 2.0
            else:
                tx_lat, tx_lon = interp_path[0][0], interp_path[0][1]

            # Noise
            BW = 1e6
            NF = 6.0
            noise_floor_dbm = -174.0 + 10.0 * math.log10(BW) + NF
            terrain_data = self.params.get('terrain_data')
            los_enabled = bool(self.params.get('los_enabled', True))

            for f in freqs:
                for i, wp in enumerate(interp_path, start=1):
                    lat, lon = wp[0], wp[1]
                    # haversine horizontal distance
                    R = 6371000.0
                    phi1 = math.radians(tx_lat)
                    phi2 = math.radians(lat)
                    dphi = math.radians(lat - tx_lat)
                    dlambda = math.radians(lon - tx_lon)
                    a = math.sin(dphi/2.0)**2 + math.cos(phi1)*math.cos(phi2)*math.sin(dlambda/2.0)**2
                    horiz_dist = 2 * R * math.asin(math.sqrt(a))
                    d = math.sqrt(horiz_dist**2 + alt_m**2)
                    freq_hz = f * 1e6
                    c = 299792458.0
                    if d <= 0:
                        Lfs = 0.0
                    else:
                        Lfs = 20.0 * math.log10(4.0 * math.pi * d * freq_hz / c)
                    Gr = self.params.get('gain_lookup', lambda _f: 0.0)(f)
                    Pr_dbm = Pt_dbm + Gr - Lfs - Lpol - Lsys
                    SNR_db = Pr_dbm - noise_floor_dbm
                    rows.append((f, i, lat, lon, alt_m, Pr_dbm, SNR_db))

            # Compute SNR grid over bounds
            if aoi_bounds:
                min_lat = aoi_bounds['min_lat']; max_lat = aoi_bounds['max_lat']; min_lon = aoi_bounds['min_lon']; max_lon = aoi_bounds['max_lon']
            else:
                lats = [p[0] for p in interp_path]
                lons = [p[1] for p in interp_path]
                min_lat, max_lat = min(lats), max(lats)
                min_lon, max_lon = min(lons), max(lons)
                pad_lat = (max_lat - min_lat) * 0.05 if max_lat > min_lat else 0.01
                pad_lon = (max_lon - min_lon) * 0.05 if max_lon > min_lon else 0.01
                min_lat -= pad_lat; max_lat += pad_lat; min_lon -= pad_lon; max_lon += pad_lon

            nx = 80
            ny = 80
            lat_vals = np.linspace(max_lat, min_lat, ny)
            lon_vals = np.linspace(min_lon, max_lon, nx)

            snr_grid = np.zeros((ny, nx), dtype=np.float32)
            total = ny * nx
            counter = 0
            for iy, latg in enumerate(lat_vals):
                for ix, longg in enumerate(lon_vals):
                    best_snr = -999.0
                    for idx, wp in enumerate(interp_path):
                        wp_lat, wp_lon = wp[0], wp[1]
                        R = 6371000.0
                        phi1 = math.radians(wp_lat)
                        phi2 = math.radians(latg)
                        dphi = math.radians(latg - wp_lat)
                        dlambda = math.radians(longg - wp_lon)
                        a = math.sin(dphi/2.0)**2 + math.cos(phi1)*math.cos(phi2)*math.sin(dlambda/2.0)**2
                        horiz_dist = 2 * R * math.asin(math.sqrt(a))
                        d = math.sqrt(horiz_dist**2 + alt_m**2)

                        # LOS/terrain blockage check (if terrain data exists)
                        blocked = False
                        if los_enabled and terrain_data is not None:
                            max_check = 20
                            for step in range(1, max_check + 1):
                                t = step / max_check
                                sample_lat = wp_lat + (latg - wp_lat) * t
                                sample_lon = wp_lon + (longg - wp_lon) * t
                                try:
                                    terrain_elev = float(terrain_data.get_elevation(sample_lat, sample_lon) or 0.0)
                                except Exception:
                                    terrain_elev = 0.0
                                direct_h = (alt_m * (1.0 - t)) + (terrain_elev * t)
                                if terrain_elev > direct_h + 5.0:
                                    blocked = True
                                    break

                        if d <= 0:
                            Lfs = 0.0
                        else:
                            Lfs = 20.0 * math.log10(4.0 * math.pi * d * (sum(freqs) / len(freqs)) * 1e6 / 299792458.0)

                        if blocked:
                            cell_snr = -120.0
                        else:
                            cell_snr = 0.0
                            for f in freqs:
                                freq_hz = f * 1e6
                                c = 299792458.0
                                Lfs = 20.0 * math.log10(4.0 * math.pi * d * freq_hz / c) if d > 0 else 0.0
                                Gr = self.params.get('gain_lookup', lambda _f: 0.0)(f)
                                Pr_dbm = Pt_dbm + Gr - Lfs - Lpol - Lsys
                                cell_snr += (Pr_dbm - noise_floor_dbm)
                            cell_snr /= float(len(freqs))

                        if cell_snr > best_snr:
                            best_snr = cell_snr

                    snr_grid[iy, ix] = float(best_snr)
                    counter += 1
                    if counter % 500 == 0:
                        pct = int(100.0 * counter / total)
                        try:
                            self.progress.emit(min(99, pct))
                        except Exception:
                            pass

            try:
                self.progress.emit(100)
            except Exception:
                pass

            fixed_vmin = -25.0
            fixed_vmax = 30.0
            rgba = snr_to_rgba(snr_grid, vmin=fixed_vmin, vmax=fixed_vmax)

            # Save PNG to logs for debugging and also return a data URL for reliable rendering in the HTML map
            fname = os.path.join(LOG_DIR, 'snr_overlay_' + datetime.datetime.now().strftime('%Y%m%d_%H%M%S') + '.png')
            pil_img = PILImage.fromarray(rgba.astype(np.uint8), mode='RGBA')
            pil_img.save(fname, format='PNG')
            buf = io.BytesIO()
            pil_img.save(buf, format='PNG')
            data_url = 'data:image/png;base64,' + base64.b64encode(buf.getvalue()).decode('ascii')

            result = {
                'rows': rows,
                'overlay_file': fname,
                'overlay_data_url': data_url,
                'bounds': [[min_lat, min_lon], [max_lat, max_lon]],
                'freqs': freqs,
                'n_points': len(interp_path),
                'vmin': fixed_vmin,
                'vmax': fixed_vmax,
                'grid': snr_grid.astype(np.float32).tolist(),
            }
            self.finished.emit(result)
        except Exception as e:
            self.error.emit(traceback.format_exc())

    def _interp_path(self, waypoints, num_samples):
        if not waypoints:
            return []
        if len(waypoints) == 1 or num_samples <= 1:
            return [waypoints[0]]
        def dist(p1, p2):
            return math.sqrt((p1[0] - p2[0]) ** 2 + (p1[1] - p2[1]) ** 2)
        total_len = 0
        segs = [0]
        for i in range(1, len(waypoints)):
            total_len += dist(waypoints[i - 1], waypoints[i])
            segs.append(total_len)
        if total_len == 0:
            return [waypoints[0]]
        step = total_len / (num_samples - 1)
        interpolated = []
        for i in range(num_samples):
            target = i * step
            for j in range(1, len(segs)):
                if segs[j] >= target or j == len(segs) - 1:
                    seg_len = segs[j] - segs[j - 1]
                    frac = (target - segs[j - 1]) / seg_len if seg_len > 0 else 0
                    p1 = waypoints[j - 1]
                    p2 = waypoints[j]
                    new_point = [p1[0] + frac * (p2[0] - p1[0]), p1[1] + frac * (p2[1] - p1[1])]
                    interpolated.append(new_point)
                    break
        return interpolated


def _mapview_run_analysis(self):
    # Prevent concurrent runs
    if getattr(self, 'analysis_running', False):
        log_event('run_analysis called but already running; ignoring')
        return
    self.analysis_running = True
    try:
        self.dashboard.results_box.append("Starting analysis (background)...\n")
        log_event('run_analysis (background): started')

        # Gather parameters and snapshot necessary data for the worker
        try:
            freq_list = self.dashboard.get_frequency_list()
            params = {
                'freqs': freq_list,
                'path_samples': int(self.dashboard.path_samples_input.value()),
                'tx_power_dbm': float(self.dashboard.tx_power_input.value()),
                'pol_loss_db': float(self.dashboard.pol_loss_input.value()),
                'sys_loss_db': float(self.dashboard.sys_loss_input.value()),
                'required_level_dbm': float(self.dashboard.required_level_input.value()),
                'alt_kft': float(self.dashboard.alt_input.value()),
                'waypoints': list(self.waypoints) if self.waypoints else [],
                'aoi_bounds': dict(self.aoi_bounds) if self.aoi_bounds else None,
                'gain_lookup': lambda f: self.dashboard.get_rx_gain(f),
                'terrain_data': getattr(self, 'elevation_data', None),
                'los_enabled': bool(self.dashboard.los_checkbox.isChecked()),
            }
            log_event(f"run_analysis params: {json.dumps({'freqs': params['freqs'], 'path_samples': params['path_samples'], 'tx_power_dbm': params['tx_power_dbm']})}")
        except Exception as e:
            log_event(f'run_analysis params read error: {e}')

        # If no inputs, abort
        if (not params['waypoints'] or len(params['waypoints']) == 0) and not params['aoi_bounds']:
            self.dashboard.results_box.append("No AOI or waypoints defined — cannot run analysis.\n")
            print('ANALYSIS_ABORTED: no inputs', flush=True)
            self.analysis_running = False
            return

        # Create worker and thread
        thread = QThread()
        worker = AnalysisWorker(params)
        worker.moveToThread(thread)

        def on_progress(pct):
            try:
                self.dashboard.progress_bar.setValue(max(0, min(100, pct)))
            except Exception:
                pass

        def on_error(trace):
            try:
                self.dashboard.results_box.append('Analysis error (worker): see log.\n')
                log_event(f'Analysis worker error: {trace}')
            except Exception:
                pass
            nonlocal thread, worker
            try:
                thread.quit()
            except Exception:
                pass
            self.analysis_running = False

        def on_finished(result):
            try:
                self.dashboard.progress_bar.setValue(100)
                self.last_snr_grid = np.array(result.get('grid', []), dtype=np.float32) if result.get('grid') else None
                self.last_snr_bounds = result.get('bounds')
                vmin = float(result.get('vmin', self.dashboard.snr_min_input.value()))
                vmax = float(result.get('vmax', self.dashboard.snr_max_input.value()))
                self.dashboard.snr_min_input.setValue(vmin)
                self.dashboard.snr_max_input.setValue(vmax)
                # populate table
                self.dashboard.data_table.setRowCount(0)
                for r_idx, row in enumerate(result.get('rows', [])):
                    self.dashboard.data_table.insertRow(r_idx)
                    for c_idx, val in enumerate(row):
                        try:
                            self.dashboard.data_table.setItem(r_idx, c_idx, QTableWidgetItem(str(val)))
                        except Exception:
                            pass

                # populate display controls
                try:
                    self.dashboard.populate_display_controls(result.get('freqs', []), result.get('n_points', 0))
                except Exception:
                    pass

                # call JS to add overlay using a data URL, which is more reliable in QWebEngine than file:// URLs
                overlay_data_url = result.get('overlay_data_url') or result.get('overlay_file')
                if overlay_data_url:
                    if overlay_data_url.startswith('C:/') or overlay_data_url.startswith('c:/'):
                        overlay_data_url = 'file:///' + overlay_data_url.replace('\\', '/')
                    js_overlay = f"(function(){{ if(window.addSNROverlay){{ return addSNROverlay('{overlay_data_url}', [[{result['bounds'][0][0]},{result['bounds'][0][1]}],[{result['bounds'][1][0]},{result['bounds'][1][1]}]], {result.get('vmin', -25.0)}, {result.get('vmax', 30.0)}); }} else return 'no-adder'; }})();"
                    try:
                        self.web_view.page().runJavaScript(js_overlay, lambda res: (self.dashboard.results_box.append(f"Overlay: {res}"), log_event(f"Overlay result: {res}")))
                    except Exception as e:
                        log_event(f'runJavaScript overlay call failed: {e}')

                # draw a fixed legend to match the fixed SNR scale
                js_legend = f"(function(){{ if(window.addSNRLegend){{ return addSNRLegend({result.get('vmin',-25.0)}, {result.get('vmax',30.0)}); }} else return 'no-legend'; }})();"
                try:
                    self.web_view.page().runJavaScript(js_legend, lambda res: log_event(f"Legend result: {res}"))
                except Exception as e:
                    log_event(f'runJavaScript legend call failed: {e}')

                self.dashboard.results_box.append(f"Analysis complete: freqs={result.get('freqs')} n_points={result.get('n_points')}\n")
                log_event(f"ANALYSIS_COMPLETE: freqs={result.get('freqs')}, n_points={result.get('n_points')}")
            except Exception as e:
                log_event(f'on_finished error: {e}')
            finally:
                try:
                    worker.deleteLater()
                    thread.quit()
                except Exception:
                    pass
                self.analysis_running = False

        worker.progress.connect(on_progress)
        worker.error.connect(on_error)
        worker.finished.connect(on_finished)
        worker.finished.connect(thread.quit)
        thread.started.connect(worker.run)
        thread.start()

        # keep references so GC doesn't kill them
        self._analysis_thread = thread
        self._analysis_worker = worker

    except Exception as e:
        log_event(f'run_analysis dispatch error: {e}')
        traceback.print_exc()
        self.analysis_running = False


def _mapview_redraw_map(self):
    # Create a minimal folium map and load it into the QWebEngineView so the UI shows something
    try:
        m = folium.Map(location=[31.0461, 34.8516], zoom_start=7, prefer_canvas=True)

        data = m.get_root().render()
        # Implement robust JS: find the Leaflet map instance, wait for bridge, provide AOI and PATH fallbacks
        map_var = m.get_name()
        extra_js = '''\n<script src="qrc:///qtwebchannel/qwebchannel.js"></script>
<script>
(function(){
    var map = null;

    function findMap() {
        // 1) try known folium variable
        try { if (window.__MAPVAR__ && typeof window.__MAPVAR__ === 'object') return window.__MAPVAR__; } catch(e){}
        // 2) search window for Leaflet map-like objects
        for (var k in window) {
            try {
                var v = window[k];
                if (!v || typeof v !== 'object') continue;
                // heuristic: Leaflet maps have _leaflet_id and _container
                if (v._leaflet_id && v._container) return v;
            } catch(e){}
        }
        // 3) try document for elements with leaflet-container and check global variables
        var els = document.getElementsByClassName('leaflet-container');
        if (els.length) {
            // try to retrieve map from known global names
            for (var k in window) {
                try { if (window[k] && window[k]._container === els[0]) return window[k]; } catch(e){}
            }
        }
        return null;
    }

    function setupBridge(cb) {
        function init() {
            if (typeof QWebChannel === 'undefined' || !window.qt || !window.qt.webChannelTransport) {
                // try again
                setTimeout(init, 150);
                return;
            }
            new QWebChannel(qt.webChannelTransport, function(channel) {
                window.bridge = channel.objects.bridge;
                if (window.bridge && window.bridge.log) window.bridge.log('bridge-ready');
                if (cb) cb();
            });
        }
        init();
    }

    // will ensure map and bridge are available before running action
    function ensureReady(action) {
        function runAction(){
            if (!map) map = findMap();
            window.map = map;
            window.__MAPVAR__ = map;
            if (!map) {
                // retry finding the map until it's available
                var retryMap = 0;
                var idMap = setInterval(function(){
                    if (!map) map = findMap();
                    if (map) { clearInterval(idMap); runAction(); return; }
                    retryMap += 1;
                    if (retryMap > 80) { clearInterval(idMap); console.log('ensureReady: map timeout'); if (window.bridge && window.bridge.log) window.bridge.log('map-timeout'); }
                }, 150);
                if (window.bridge && window.bridge.log) window.bridge.log('map-not-ready');
                return;
            }
            if (!window.bridge) {
                var retry = 0;
                var id = setInterval(function(){
                    if (!map) map = findMap();
                    if (window.bridge) { clearInterval(id); runAction(); }
                    retry += 1;
                    if (retry > 80) { clearInterval(id); console.log('ensureReady: bridge timeout'); if (window.bridge && window.bridge.log) window.bridge.log('bridge-timeout'); }
                }, 150);
                return;
            }
            try { action(); } catch (e) { if (window.bridge && window.bridge.log) window.bridge.log('ensureReady-action-error:' + e); }
        }
        runAction();
    }

    // AOI drawing fallback: manual two-click rectangle (no Draw toolbar)
    window.startAOIDraw = function(){
        // If map and bridge are already available, attach handler synchronously and return enabled
        try{
            var m0 = map || findMap();
            if (m0 && window.bridge) {
                // attach handlers immediately
                if (window._fp_clickHandler) { try { m0.off('click', window._fp_clickHandler); } catch (e) {} window._fp_clickHandler = null; }
                if (window._measureHandler) { try { m0.off('click', window._measureHandler); } catch (e) {} window._measureHandler = null; }
                if (window._aoiClickHandler) { try { m0.off('click', window._aoiClickHandler); } catch (e) {} }

                window._aoiPoints = [];
                window._mode = 'AOI';
                window._aoiClickHandler = function(e){
                    try{
                        if (window._mode !== 'AOI') return;
                        if (!window._aoiPoints) window._aoiPoints = [];
                        window._aoiPoints.push(e.latlng);
                        if (window.bridge && window.bridge.log) window.bridge.log('aoi-click:' + e.latlng.lat + ',' + e.latlng.lng);
                        console.log('aoi-click:', e.latlng.lat, e.latlng.lng);
                        if (window._aoiPoints.length === 2) {
                            var lat1 = window._aoiPoints[0].lat, lon1 = window._aoiPoints[0].lng;
                            var lat2 = window._aoiPoints[1].lat, lon2 = window._aoiPoints[1].lng;
                            var coords = [[lon1,lat1],[lon1,lat2],[lon2,lat2],[lon2,lat1],[lon1,lat1]];
                            var geo = {type:'Feature', geometry:{type:'Polygon', coordinates:[coords]}};
                            try{ window._aoiLayer = L.polygon([[coords[0][1],coords[0][0]],[coords[1][1],coords[1][0]],[coords[2][1],coords[2][0]],[coords[3][1],coords[3][0]]]).addTo(m0); }catch(e){}
                            if (window.bridge && window.bridge.handle_aoi) {
                                if (window.bridge && window.bridge.log) window.bridge.log('aoi-sending-to-python');
                                window.bridge.handle_aoi(JSON.stringify(geo));
                                if (window.bridge && window.bridge.log) window.bridge.log('aoi-sent-to-python');
                            }
                            m0.off('click', window._aoiClickHandler);
                            window._aoiClickHandler = null;
                            window._aoiPoints = [];
                            window._mode = 'NONE';
                            if (window.bridge && window.bridge.log) window.bridge.log('aoi-drawn-fallback');
                        }
                    }catch(e){ if (window.bridge && window.bridge.log) window.bridge.log('aoi-click-handler-error:'+e); }
                };
                m0.on('click', window._aoiClickHandler);
                if (window.bridge && window.bridge.log) window.bridge.log('aoi-fallback-enabled');
                return 'aoi-enabled';
            }
        }catch(e){ if (window.bridge && window.bridge.log) window.bridge.log('startAOIDraw-immediate-error:'+e); }

        // default return if handler not attached immediately; ensureReady will attach later
        var _res = 'not-ready';
        ensureReady(function(){
            try {
                var m = map || findMap();
                if (!m) { if (window.bridge && window.bridge.log) window.bridge.log('startAOIDraw-no-map'); return; }
                if (window._fp_clickHandler) { try { m.off('click', window._fp_clickHandler); } catch (e) {} window._fp_clickHandler = null; }
                if (window._measureHandler) { try { m.off('click', window._measureHandler); } catch (e) {} window._measureHandler = null; }
                if (window._aoiClickHandler) { try { m.off('click', window._aoiClickHandler); } catch (e) {} }

                window._aoiPoints = [];
                window._mode = 'AOI';
                window._aoiClickHandler = function(e){
                    try{
                        if (window._mode !== 'AOI') return;
                        if (!window._aoiPoints) window._aoiPoints = [];
                        window._aoiPoints.push(e.latlng);
                        if (window.bridge && window.bridge.log) window.bridge.log('aoi-click:' + e.latlng.lat + ',' + e.latlng.lng);
                        console.log('aoi-click:', e.latlng.lat, e.latlng.lng);
                        if (window._aoiPoints.length === 2) {
                            var lat1 = window._aoiPoints[0].lat, lon1 = window._aoiPoints[0].lng;
                            var lat2 = window._aoiPoints[1].lat, lon2 = window._aoiPoints[1].lng;
                            var coords = [[lon1,lat1],[lon1,lat2],[lon2,lat2],[lon2,lat1],[lon1,lat1]];
                            var geo = {type:'Feature', geometry:{type:'Polygon', coordinates:[coords]}};
                            try{ window._aoiLayer = L.polygon([[coords[0][1],coords[0][0]],[coords[1][1],coords[1][0]],[coords[2][1],coords[2][0]],[coords[3][1],coords[3][0]]]).addTo(m); }catch(e){}
                            if (window.bridge && window.bridge.handle_aoi) {
                                if (window.bridge && window.bridge.log) window.bridge.log('aoi-sending-to-python');
                                window.bridge.handle_aoi(JSON.stringify(geo));
                                if (window.bridge && window.bridge.log) window.bridge.log('aoi-sent-to-python');
                            }
                            m.off('click', window._aoiClickHandler);
                            window._aoiClickHandler = null;
                            window._aoiPoints = [];
                            window._mode = 'NONE';
                            if (window.bridge && window.bridge.log) window.bridge.log('aoi-drawn-fallback');
                        }
                    }catch(e){ if (window.bridge && window.bridge.log) window.bridge.log('aoi-click-handler-error:'+e); }
                };
                m.on('click', window._aoiClickHandler);
                if (window.bridge && window.bridge.log) window.bridge.log('aoi-fallback-enabled');
                _res = 'aoi-enabled';
            } catch (err) { if (window.bridge && window.bridge.log) window.bridge.log('startAOIDraw-error:'+err); }
        });
        return _res;
    };

    // PATH mode: click to add draggable markers
    window._fp_waypoints = [];
    window._fp_markers = [];
    window._fp_polyline = null;
    window._fp_clickHandler = null;
    window._measureHandler = null;
    window._measurePolyline = null;
    window._measureMarkers = [];

    function updatePolyline(){
        try{
            if (window._fp_polyline){ try{ map.removeLayer(window._fp_polyline); }catch(e){} window._fp_polyline = null; }
            if (window._fp_waypoints && window._fp_waypoints.length){
                var latlngs = window._fp_waypoints.map(function(p){ return [p[0], p[1]]; });
                window._fp_polyline = L.polyline(latlngs, {color:'red', weight:3}).addTo(map);
            }
        }catch(err){ if (window.bridge && window.bridge.log) window.bridge.log('updatePolyline-error:'+err); }
    }

    window.setMode = function(mode) {
        // Try to attach immediately if map + bridge available
        try{
            var m0 = map || findMap();
            if (m0 && window.bridge) {
                if (mode === 'PATH') {
                    if (window._aoiClickHandler) { try { m0.off('click', window._aoiClickHandler); } catch (e) {} window._aoiClickHandler = null; }
                    if (window._measureHandler) { try { m0.off('click', window._measureHandler); } catch (e) {} window._measureHandler = null; }
                    if (window._fp_clickHandler) { if (window.bridge && window.bridge.log) window.bridge.log('path-already-enabled'); return 'path-already-enabled'; }
                    window._mode = 'PATH';
                    window._fp_clickHandler = function(e){
                        try{
                            var latlng = e.latlng;
                            if (window.bridge && window.bridge.log) window.bridge.log('path-click:' + latlng.lat + ',' + latlng.lng);
                            console.log('path-click:', latlng.lat, latlng.lng);
                            var marker = L.marker(latlng, {draggable:true}).addTo(m0);
                            window._fp_markers.push(marker);
                            window._fp_waypoints.push([latlng.lat, latlng.lng]);
                            marker.on('dragend', function(ev){ var idx = window._fp_markers.indexOf(marker); if (idx>=0){ var p = marker.getLatLng(); window._fp_waypoints[idx] = [p.lat,p.lng]; updatePolyline(); var r = sendWaypoints(); if (window.bridge && window.bridge.log) window.bridge.log('sendWaypoints-result:' + r); } });
                            updatePolyline();
                            var r = sendWaypoints();
                            if (window.bridge && window.bridge.log) window.bridge.log('sendWaypoints-result:' + r);
                        }catch(e){ if (window.bridge && window.bridge.log) window.bridge.log('fp-click-handler-error:'+e); }
                    };
                    m0.on('click', window._fp_clickHandler);
                    if (window.bridge && window.bridge.log) window.bridge.log('path-enabled');
                    return 'path-enabled';
                } else {
                    if (window._fp_clickHandler) { try { m0.off('click', window._fp_clickHandler); } catch(e){} window._fp_clickHandler = null; }
                    window._mode = 'NONE';
                    if (window.bridge && window.bridge.log) window.bridge.log('path-disabled');
                    return 'path-disabled';
                }
            }
        }catch(e){ if (window.bridge && window.bridge.log) window.bridge.log('setMode-immediate-error:'+e); }

        var _res = 'not-ready';
        ensureReady(function(){
            try {
                var m = map || findMap();
                if (!m) { if (window.bridge && window.bridge.log) window.bridge.log('setMode-no-map'); return; }
                if (mode === 'PATH') {
                    if (window._aoiClickHandler) { try { m.off('click', window._aoiClickHandler); } catch (e) {} window._aoiClickHandler = null; }
                    if (window._measureHandler) { try { m.off('click', window._measureHandler); } catch (e) {} window._measureHandler = null; }
                    if (window._fp_clickHandler) {
                        if (window.bridge && window.bridge.log) window.bridge.log('path-already-enabled');
                        _res = 'path-already-enabled';
                        return;
                    }
                    window._mode = 'PATH';
                    window._fp_clickHandler = function(e){
                        try{
                            var latlng = e.latlng;
                            if (window.bridge && window.bridge.log) window.bridge.log('path-click:' + latlng.lat + ',' + latlng.lng);
                            console.log('path-click:', latlng.lat, latlng.lng);
                            var marker = L.marker(latlng, {draggable:true}).addTo(m);
                            window._fp_markers.push(marker);
                            window._fp_waypoints.push([latlng.lat, latlng.lng]);
                            marker.on('dragend', function(ev){ var idx = window._fp_markers.indexOf(marker); if (idx>=0){ var p = marker.getLatLng(); window._fp_waypoints[idx] = [p.lat,p.lng]; updatePolyline(); var r = sendWaypoints(); if (window.bridge && window.bridge.log) window.bridge.log('sendWaypoints-result:' + r); } });
                            updatePolyline();
                            var r = sendWaypoints();
                            if (window.bridge && window.bridge.log) window.bridge.log('sendWaypoints-result:' + r);
                        }catch(e){ if (window.bridge && window.bridge.log) window.bridge.log('fp-click-handler-error:'+e); }
                    };
                    m.on('click', window._fp_clickHandler);
                    if (window.bridge && window.bridge.log) window.bridge.log('path-enabled');
                    _res = 'path-enabled';
                } else {
                    if (window._fp_clickHandler) { m.off('click', window._fp_clickHandler); window._fp_clickHandler = null; }
                    window._mode = 'NONE';
                    if (window.bridge && window.bridge.log) window.bridge.log('path-disabled');
                    _res = 'path-disabled';
                }
            } catch(err){ if (window.bridge && window.bridge.log) window.bridge.log('setMode-error:'+err); }
        });
        return _res;
    };

    window.startMeasure = function(){
        ensureReady(function(){
            try {
                var m = map || findMap();
                if (!m) { if (window.bridge && window.bridge.log) window.bridge.log('startMeasure-no-map'); return; }
                if (window._measureHandler) {
                    m.off('click', window._measureHandler);
                    window._measureHandler = null;
                    if (window._measurePolyline) { m.removeLayer(window._measurePolyline); window._measurePolyline = null; }
                    for (var i = 0; i < window._measureMarkers.length; i++) { try { m.removeLayer(window._measureMarkers[i]); } catch (e) {} }
                    window._measureMarkers = [];
                    if (window.bridge && window.bridge.log) window.bridge.log('measure-disabled');
                    return;
                }
                if (window._fp_clickHandler) return; // path mode takes precedence
                if (window._aoiClickHandler) { try { m.off('click', window._aoiClickHandler); } catch (e) {} window._aoiClickHandler = null; }
                var points = [];
                window._mode = 'MEASURE';
                window._measureHandler = function(e){
                    points.push(e.latlng);
                    var marker = L.marker(e.latlng).addTo(m);
                    window._measureMarkers.push(marker);
                    if (points.length >= 2) {
                        if (window._measurePolyline) { try { m.removeLayer(window._measurePolyline); } catch (e) {} }
                        window._measurePolyline = L.polyline(points.map(function(p){ return [p.lat, p.lng]; }), {color: '#2c7be5', weight: 3, dashArray: '8 6'}).addTo(m);
                        var d = points[0].distanceTo(points[1]);
                        var label = L.popup().setLatLng(points[1]).setContent('Distance: ' + (d < 1000 ? d.toFixed(0) + ' m' : (d / 1000).toFixed(2) + ' km'));
                        label.addTo(m);
                        if (window.bridge && window.bridge.log) window.bridge.log('measure-distance:' + d);
                    }
                };
                m.on('click', window._measureHandler);
                if (window.bridge && window.bridge.log) window.bridge.log('measure-enabled');
            } catch(err){ if (window.bridge && window.bridge.log) window.bridge.log('startMeasure-error:'+err); }
        });
    };

    if (!window._mapClickHandler) {
        window._mapClickHandler = function(e){
            try{
                if (window._fp_clickHandler || window._measureHandler || window._aoiClickHandler) return; // path / ruler / AOI mode takes precedence
                if (window.bridge && window.bridge.log) window.bridge.log('map-click:' + e.latlng.lat + ',' + e.latlng.lng);
                console.log('map-click:', e.latlng.lat, e.latlng.lng);
                if (window.bridge && window.bridge.request_calc_details) {
                    window.bridge.request_calc_details(e.latlng.lat, e.latlng.lng);
                }
            }catch(err){ if (window.bridge && window.bridge.log) window.bridge.log('map-click-handler-error:'+err); }
        };
        try {
            if (map) {
                map.on('click', window._mapClickHandler);
            } else {
                var _attachId = setInterval(function(){ if (!map) map = findMap(); if (map){ try{ map.on('click', window._mapClickHandler); }catch(e){} clearInterval(_attachId); } }, 150);
            }
        } catch(e){ if (window.bridge && window.bridge.log) window.bridge.log('map-on-attach-error:'+e); }
    }

    function sendWaypoints(){
        var _r = 'none';
        try{
            var jsonStr = JSON.stringify(window._fp_waypoints);
            if (window.bridge && window.bridge.update_waypoints) { window.bridge.update_waypoints(jsonStr); _r = 'sent'; }
            updatePolyline();
            if (window.bridge && window.bridge.log) window.bridge.log('waypoints-sent');
        }catch(err){ if (window.bridge && window.bridge.log) window.bridge.log('sendWaypoints-error:'+err); _r = 'error'; }
        return _r;
    }

    // clear functions to remove AOI and path without reloading the map
    window.clearAOI = function(){ try{ var m = map || findMap(); if (window._aoiLayer){ if (m) m.removeLayer(window._aoiLayer); window._aoiLayer = null; if (window.bridge && window.bridge.log) window.bridge.log('aoi-cleared'); } if (window._aoiClickHandler) { try { if (m) m.off('click', window._aoiClickHandler); } catch(e){} window._aoiClickHandler = null; } window._aoiPoints = []; window._mode = 'NONE'; }catch(e){ if (window.bridge && window.bridge.log) window.bridge.log('clearAOI-error:'+e); } };
    window.clearSNRLegend = function(){ try{ if (window._snrLegend) { map.removeControl(window._snrLegend); window._snrLegend = null; } }catch(e){ if (window.bridge && window.bridge.log) window.bridge.log('clearSNRLegend-error:'+e); } };
    window.clearMap = function(){ try { window.clearAOI(); window.clearPath(); window.clearSNRLegend(); if (window.bridge && window.bridge.log) window.bridge.log('map-cleared'); return true; } catch (e) { if (window.bridge && window.bridge.log) window.bridge.log('clearMap-error:' + e); return false; } };
    window.addSNRLegend = function(minValue, maxValue){ try{ if (window._snrLegend) { try{ var _m0 = map || findMap(); if (_m0) _m0.removeControl(window._snrLegend); }catch(e){} window._snrLegend = null; } var legend = L.control({position: 'bottomright'}); legend.onAdd = function(){ var div = L.DomUtil.create('div', 'info legend'); div.style.background = 'rgba(255,255,255,0.85)'; div.style.border = '1px solid #ccc'; div.style.borderRadius = '4px'; div.style.padding = '6px 8px'; div.style.color = '#222'; div.style.fontSize = '11px'; div.innerHTML = '<div style="font-weight:bold; margin-bottom:4px;">SNR (dB)</div><div style="width:140px; height:12px; border:1px solid #999; background:linear-gradient(to right, #d52b1e, #f7941e, #ffdd33, #2ecc40);"></div><div style="display:flex; justify-content:space-between; margin-top:4px;"><span>' + Number(minValue).toFixed(1) + '</span><span>' + Number((minValue + maxValue) / 2.0).toFixed(1) + '</span><span>' + Number(maxValue).toFixed(1) + '</span></div>'; return div; }; try{ var _m = map || findMap(); if (!_m) { if (window.bridge && window.bridge.log) window.bridge.log('addSNRLegend-no-map'); return false; } legend.addTo(_m); window._snrLegend = legend; if (window.bridge && window.bridge.log) window.bridge.log('snr-legend-added'); return true; }catch(e){ if (window.bridge && window.bridge.log) window.bridge.log('addSNRLegend-error2:'+e); return false; } }catch(e){ if (window.bridge && window.bridge.log) window.bridge.log('addSNRLegend-error:'+e); return false; } };
    window.addSNROverlay = function(dataUrl, bounds, minValue, maxValue){ try{ var m = map || findMap(); if(!m){ if (window.bridge && window.bridge.log) window.bridge.log('addSNROverlay-no-map'); return false; } if(window._snr_overlay){ try{ m.removeLayer(window._snr_overlay); }catch(e){} window._snr_overlay = null; } var ol = L.imageOverlay(dataUrl, bounds, {opacity:0.75}); ol.addTo(m); window._snr_overlay = ol; try { m.fitBounds(bounds); } catch (e) {} if (typeof minValue !== 'undefined' && typeof maxValue !== 'undefined') { window.addSNRLegend(minValue, maxValue); } if(window.bridge && window.bridge.log) window.bridge.log('snr-overlay-added'); return true; }catch(e){ if(window.bridge && window.bridge.log) window.bridge.log('addSNROverlay-error:'+e); return false; } };
    window.clearPath = function(){ try{ var m = map || findMap(); if (window._fp_clickHandler) { try { if (m) m.off('click', window._fp_clickHandler); } catch(e){} window._fp_clickHandler = null; } if (window._fp_markers){ for(var i=0;i<window._fp_markers.length;i++){ try{ if (m) m.removeLayer(window._fp_markers[i]); }catch(e){} } window._fp_markers = []; window._fp_waypoints = []; } if (window._fp_polyline){ try{ if (m) m.removeLayer(window._fp_polyline); }catch(e){} window._fp_polyline = null; } if (window._snr_overlay){ try{ if (m) m.removeLayer(window._snr_overlay); }catch(e){} window._snr_overlay = null; } if (window._snrLegend){ try{ if (m) m.removeControl(window._snrLegend); }catch(e){} window._snrLegend = null; } if (window._measurePolyline){ try{ if (m) m.removeLayer(window._measurePolyline); }catch(e){} window._measurePolyline = null; } if (window._measureMarkers){ for(var i=0;i<window._measureMarkers.length;i++){ try{ if (m) m.removeLayer(window._measureMarkers[i]); }catch(e){} } window._measureMarkers = []; } if (window.bridge && window.bridge.log) window.bridge.log('path-cleared'); window._mode = 'NONE'; return true; }catch(e){ if (window.bridge && window.bridge.log) window.bridge.log('clearPath-error:'+e); return false; } };

    // Minimal ruler button in a single left-side control instead of a full toolbar.
    var rulerControl = L.control({position: 'topleft'});
    rulerControl.onAdd = function(){
        var div = L.DomUtil.create('div', 'leaflet-bar');
        var btn = document.createElement('a');
        btn.href = '#';
        btn.title = 'Measure distance';
        btn.innerHTML = '↔';
        btn.style.display = 'block';
        btn.style.width = '26px';
        btn.style.height = '26px';
        btn.style.lineHeight = '26px';
        btn.style.textAlign = 'center';
        btn.style.fontWeight = 'bold';
        btn.style.fontSize = '16px';
        btn.style.background = '#fff';
        btn.style.border = '1px solid #999';
        btn.style.borderRadius = '4px';
        btn.onclick = function(e){ e.preventDefault(); if (window.startMeasure) window.startMeasure(); };
        div.appendChild(btn);
        return div;
    };
    try {
        if (map) {
            rulerControl.addTo(map);
        } else {
            var _rulerAttach = setInterval(function(){ if (!map) map = findMap(); if (map){ try{ rulerControl.addTo(map); }catch(e){} clearInterval(_rulerAttach); } }, 150);
        }
    } catch(e){ if (window.bridge && window.bridge.log) window.bridge.log('ruler-addto-error:'+e); }

    // initialize bridge and map detection
    setupBridge(function(){ map = findMap(); if (!map) map = window.__MAPVAR__ || window.map || null; if (window.bridge && window.bridge.log) window.bridge.log('setup-complete'); });

})();
</script>\n'''
        extra_js = extra_js.replace('__MAPVAR__', map_var)
        # Try to insert before </body> if present, otherwise append
        if '</body>' in data:
            data = data.replace('</body>', extra_js + '</body>')
        else:
            data = data + extra_js
        # QWebEngineView expects full HTML
        self.web_view.setHtml(data)
    except Exception as e:
        try:
            self.dashboard.results_box.append(f"Could not redraw map: {e}\n")
        except Exception:
            pass


def _mapview_refresh_display(self):
    # Minimal no-op that avoids crashes when UI controls change
    try:
        pass
    except Exception:
        pass


def _mapview_show_calc_details(self, lat, lon):
    try:
        if self.last_snr_grid is None or self.last_snr_bounds is None:
            QMessageBox.information(self.dashboard, "Calc Details", f"Lat: {lat:.6f}, Lon: {lon:.6f}\nNo SNR overlay available yet.")
            return

        min_lat = float(self.last_snr_bounds[0][0])
        max_lat = float(self.last_snr_bounds[1][0])
        min_lon = float(self.last_snr_bounds[0][1])
        max_lon = float(self.last_snr_bounds[1][1])
        grid = np.asarray(self.last_snr_grid, dtype=np.float32)
        if grid.size == 0:
            QMessageBox.information(self.dashboard, "Calc Details", f"Lat: {lat:.6f}, Lon: {lon:.6f}\nNo SNR grid available.")
            return

        ny, nx = grid.shape
        x = (lon - min_lon) / (max_lon - min_lon) if max_lon > min_lon else 0.5
        y = (max_lat - lat) / (max_lat - min_lat) if max_lat > min_lat else 0.5
        ix = int(np.clip(x * (nx - 1), 0, nx - 1))
        iy = int(np.clip(y * (ny - 1), 0, ny - 1))
        snr = float(grid[iy, ix])
        QMessageBox.information(self.dashboard, "SNR at point", f"Lat: {lat:.6f}\nLon: {lon:.6f}\nSNR: {snr:.2f} dB")
        self.dashboard.results_box.append(f"Clicked SNR: lat={lat:.6f}, lon={lon:.6f}, snr={snr:.2f} dB")
    except Exception as e:
        log_event(f'show_calc_details error: {e}')
        try:
            QMessageBox.information(self.dashboard, "Calc Details", f"Lat: {lat:.6f}, Lon: {lon:.6f}\nUnable to compute SNR at this point.")
        except Exception:
            pass


# Attach the stubs to the MapView class at runtime
MapView.run_analysis = _mapview_run_analysis
MapView.redraw_map = _mapview_redraw_map
MapView.refresh_display = _mapview_refresh_display
MapView.show_calc_details = _mapview_show_calc_details


if __name__ == '__main__':
    # Create the Qt application and a simple main window containing the map and dashboard
    app = QApplication(sys.argv)

    main_win = QMainWindow()
    dashboard = Dashboard()
    map_view = MapView(dashboard)

    central = QWidget()
    layout = QHBoxLayout()
    layout.setContentsMargins(0, 0, 0, 0)

    layout.addWidget(map_view)
    layout.addWidget(dashboard)
    central.setLayout(layout)

    main_win.setCentralWidget(central)
    main_win.setWindowTitle('Flight Planner')
    main_win.resize(1200, 800)
    main_win.show()

    # Start the Qt event loop
    sys.exit(app.exec())
