(function () {
  'use strict';

  // Single Aircraft Point must index the user-drawn waypoints, not interpolated path samples.
  let aircraftMarker = null;
  let selectedCell = null;
  const selectionLayer = L.layerGroup().addTo(map);

  function clampWaypointIndex() {
    const input = $('wpIndex');
    if (!input) return 1;
    const count = Math.max(1, path.length);
    input.min = 1;
    input.max = count;
    let index = Number.parseInt(input.value, 10);
    if (!Number.isFinite(index)) index = 1;
    index = Math.max(1, Math.min(count, index));
    input.value = String(index);
    return index;
  }

  function syncAircraftMarker() {
    const field = $('wpIndexField');
    const isSingle = $('viewMode') && $('viewMode').value === 'single';
    if (field) field.style.display = isSingle ? 'block' : 'none';
    if (aircraftMarker) {
      map.removeLayer(aircraftMarker);
      aircraftMarker = null;
    }
    if (!isSingle || !path.length) return;
    const index = clampWaypointIndex();
    const point = path[index - 1];
    if (!point) return;
    aircraftMarker = L.circleMarker([point.lat, point.lng], {
      radius: 10, color: '#fff', weight: 3, fillColor: '#f59e0b', fillOpacity: 1,
      pane: 'markerPane'
    }).addTo(map);
    aircraftMarker.bindTooltip('Aircraft point ' + index + ' / ' + path.length, {
      permanent: true, direction: 'top', offset: [0, -10]
    });
    aircraftMarker.bringToFront();
  }

  function closeCellDetails() {
    const panel = $('cellDetails');
    if (panel) panel.classList.remove('show');
    selectedCell = null;
    selectionLayer.clearLayers();
  }

  function showCellDetails(cell) {
    selectedCell = cell;
    const panel = $('cellDetails');
    const body = $('cellDetailsBody');
    if (!panel || !body || !cell) return;
    const snr = cellSnr(cell);
    const freq = freqLabel();
    const meets = snrOk(snr);
    const aperture = Number.isFinite(cell.phi) ? (cell.phi * 180 / Math.PI).toFixed(2) + '°' : '—';
    const ellipse = Number.isFinite(cell.a90) ? fmtKm(cell.a90) : 'Not available';
    const diffraction = Number.isFinite(cellDiff(cell)) ? cellDiff(cell).toFixed(2) + ' dB' : '—';
    body.innerHTML =
      '<div style="margin-bottom:9px;color:' + (meets ? '#86efac' : '#fca5a5') + ';font-weight:700">' +
      (meets ? 'Meets minimum SNR requirement' : 'Below minimum SNR requirement') + '</div>' +
      '<div class="cell-details-grid">' +
      '<div><span>Latitude</span><strong>' + cell.lat0.toFixed(5) + ' – ' + cell.lat1.toFixed(5) + '</strong></div>' +
      '<div><span>Longitude</span><strong>' + cell.lng0.toFixed(5) + ' – ' + cell.lng1.toFixed(5) + '</strong></div>' +
      '<div><span>SNR</span><strong>' + snr.toFixed(2) + ' dB</strong></div>' +
      '<div><span>Frequency view</span><strong>' + freq + '</strong></div>' +
      '<div><span>Terrain elevation</span><strong>' + (Number.isFinite(cell.gElev) ? cell.gElev.toFixed(0) + ' m AMSL' : '—') + '</strong></div>' +
      '<div><span>Diffraction loss</span><strong>' + diffraction + '</strong></div>' +
      '<div><span>DF aperture</span><strong>' + aperture + '</strong></div>' +
      '<div><span>Ellipse semi-axis a90</span><strong>' + ellipse + '</strong></div>' +
      '</div>';
    panel.classList.add('show');
    selectionLayer.clearLayers();
    L.rectangle([[cell.lat0, cell.lng0], [cell.lat1, cell.lng1]], {
      color: '#fff', weight: 3, fill: false, opacity: 1, interactive: false
    }).addTo(selectionLayer);
  }

  const originalRedrawPath = redrawPath;
  redrawPath = function () {
    originalRedrawPath();
    clampWaypointIndex();
    syncAircraftMarker();
  };

  const originalRunAnalysis = runAnalysis;
  runAnalysis = async function () {
    closeCellDetails();
    return originalRunAnalysis.apply(this, arguments);
  };

  const originalPaintHeat = paintHeat;
  paintHeat = function (kind) {
    originalPaintHeat(kind);
    let index = 0;
    heatLayer.eachLayer(function (layer) {
      const cell = lastCells[index++];
      if (!cell) return;
      layer.on('click', function () { showCellDetails(cell); });
    });
    if (selectedCell && lastCells.includes(selectedCell)) {
      showCellDetails(selectedCell);
    }
  };

  if ($('wpIndex')) {
    $('wpIndex').addEventListener('change', function () {
      clampWaypointIndex();
      syncAircraftMarker();
      if (hasAnalysis && $('viewMode').value === 'single') runAnalysis();
    });
    $('wpIndex').addEventListener('input', function () {
      clampWaypointIndex();
      syncAircraftMarker();
    });
  }
  if ($('viewMode')) {
    $('viewMode').addEventListener('change', syncAircraftMarker);
  }
  if ($('psamp')) {
    $('psamp').addEventListener('change', function () {
      // Path samples still control the resolution of mean/max analysis and DF aperture,
      // but never the number of selectable original aircraft waypoints.
      syncAircraftMarker();
    });
  }
  if ($('closeCellDetails')) $('closeCellDetails').addEventListener('click', closeCellDetails);
  if ($('clearBtn')) $('clearBtn').addEventListener('click', closeCellDetails);

  clampWaypointIndex();
  syncAircraftMarker();
})();
