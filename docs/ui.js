/**
 * UI helpers: tabs, save/load config, Optimal Path Generator mode.
 * Loaded after flight-planner.js.
 */
(function () {
  'use strict';

  document.querySelectorAll('.tabs button[data-tab]').forEach((btn) => {
    btn.addEventListener('click', () => {
      document.querySelectorAll('.tabs button[data-tab]').forEach((b) => b.classList.remove('on'));
      document.querySelectorAll('.tabpane').forEach((p) => p.classList.remove('on'));
      btn.classList.add('on');
      const pane = document.getElementById('tab-' + btn.dataset.tab);
      if (pane) pane.classList.add('on');
    });
  });

  const viewModeEl = $('viewMode');
  if (viewModeEl) {
    const syncWp = () => {
      const f = $('wpIndexField');
      if (f) f.style.display = viewModeEl.value === 'single' ? '' : 'none';
    };
    viewModeEl.addEventListener('change', syncWp);
    syncWp();
  }

  function collectConfig() {
    if (typeof readGainTable === 'function') readGainTable();
    const fields = {};
    const ids = typeof FIELD_IDS !== 'undefined' ? FIELD_IDS.slice() : [];
    ['legLen', 'minSnr'].forEach((id) => { if (!ids.includes(id)) ids.push(id); });
    ids.forEach((id) => {
      const el = $(id);
      if (!el) return;
      fields[id] = el.type === 'checkbox' ? el.checked : el.value;
    });
    return {
      version: 2,
      fields,
      gainTable: typeof gainTable !== 'undefined' ? gainTable : [],
      path: typeof path !== 'undefined' ? path : [],
      aoi: typeof aoi !== 'undefined' && aoi ? [[aoi.getSouth(), aoi.getWest()], [aoi.getNorth(), aoi.getEast()]] : null,
      center: map.getCenter(),
      zoom: map.getZoom(),
    };
  }

  function applyConfig(data) {
    if (!data) return;
    if (data.fields) {
      Object.keys(data.fields).forEach((id) => {
        const el = $(id);
        if (!el || data.fields[id] == null) return;
        if (el.type === 'checkbox') el.checked = !!data.fields[id];
        else el.value = data.fields[id];
      });
    }
    if (Array.isArray(data.gainTable) && data.gainTable.length && typeof renderGainTable === 'function') {
      gainTable = data.gainTable.map((r) => [+r[0], +r[1]]);
      renderGainTable();
    }
    if (Array.isArray(data.path)) {
      path = [];
      markers.forEach((m) => map.removeLayer(m));
      markers = [];
      pathLayer.clearLayers();
      data.path.forEach((p) => { if (typeof addPathPoint === 'function') addPathPoint(p.lat, p.lng, true); });
    }
    if (data.aoi && typeof setAoi === 'function') setAoi(L.latLngBounds(data.aoi[0], data.aoi[1]), true);
    if (data.center) map.setView(data.center, data.zoom || map.getZoom());
    if (typeof updateRun === 'function') updateRun();
    if (typeof persistSoon === 'function') persistSoon();
  }

  const saveBtn = $('saveCfgBtn');
  if (saveBtn) {
    saveBtn.onclick = () => {
      const payload = collectConfig();
      const blob = new Blob([JSON.stringify(payload, null, 2)], { type: 'application/json' });
      const a = document.createElement('a');
      a.href = URL.createObjectURL(blob);
      a.download = 'flight-planner-config.json';
      a.click();
      URL.revokeObjectURL(a.href);
      const r = $('result');
      if (r) r.textContent = 'Configuration downloaded.';
    };
  }

  const loadBtn = $('loadCfgBtn');
  const loadFile = $('loadCfgFile');
  if (loadBtn && loadFile) {
    loadBtn.onclick = () => loadFile.click();
    loadFile.addEventListener('change', () => {
      const file = loadFile.files && loadFile.files[0];
      if (!file) return;
      const reader = new FileReader();
      reader.onload = () => {
        try {
          applyConfig(JSON.parse(reader.result));
          const r = $('result');
          if (r) r.textContent = 'Configuration loaded.';
        } catch (e) {
          const r = $('result');
          if (r) r.textContent = 'Load failed: ' + (e.message || e);
        }
        loadFile.value = '';
      };
      reader.readAsText(file);
    });
  }

  let plannerMode = 'forward';
  const optLayer = L.layerGroup().addTo(map);

  function setPlannerMode(next) {
    plannerMode = next;
    const fwd = $('modeForward');
    const opt = $('modeOptimal');
    if (fwd) fwd.classList.toggle('on', next === 'forward');
    if (opt) opt.classList.toggle('on', next === 'optimal');
    const optPane = $('optControls');
    if (optPane) { optPane.style.display = next === 'optimal' ? 'block' : 'none'; optPane.classList.toggle('show', next === 'optimal'); }
    const runBtn = $('runBtn');
    if (next === 'optimal') {
      if (runBtn) { runBtn.textContent = 'Generate Optimal Leg'; runBtn.disabled = !aoi; }
      setBanner('<b>Optimal / Auto Leg:</b> Set Leg Length, optional flight area, draw AOI, then Generate.');
      const sheet = $('sheet');
      if (sheet) sheet.classList.add('open');
      if (optPane) {
        optPane.style.display = 'block';
        try { optPane.scrollIntoView({ block: 'nearest', behavior: 'smooth' }); } catch (e) {}
      }
      const leg = $('legLen');
      if (leg) { try { leg.focus(); leg.select(); } catch (e) {} }
    } else {
      if (typeof updateRun === 'function') updateRun();
      else if (runBtn) runBtn.textContent = 'Run Analysis';
      setBanner('<b>Forward mode:</b> Draw path and AOI, then Run Analysis.');
    }
  }

  const modeFwd = $('modeForward');
  const modeOpt = $('modeOptimal');
  if (modeFwd) modeFwd.onclick = () => setPlannerMode('forward');
  if (modeOpt) modeOpt.onclick = () => setPlannerMode('optimal');

  function applyOptimalPath(result) {
    if (!result.ok || !result.path) return;
    path = [];
    markers.forEach((m) => map.removeLayer(m));
    markers = [];
    pathLayer.clearLayers();
    optLayer.clearLayers();
    result.path.forEach((pt) => { if (typeof addPathPoint === 'function') addPathPoint(pt.lat, pt.lng, true); });
    L.polyline(result.path.map((p) => [p.lat, p.lng]), { color: '#22c55e', weight: 5, opacity: 0.95 }).addTo(optLayer);
    result.path.forEach((p, i) => {
      L.circleMarker([p.lat, p.lng], { radius: 7, color: '#16a34a', weight: 2, fillColor: '#86efac', fillOpacity: 1 })
        .bindTooltip(i === 0 ? 'Start' : 'End', { permanent: false }).addTo(optLayer);
    });
    // Zoom out so both AOI and the generated leg are fully visible
    try {
      const b = L.latLngBounds(result.path.map((p) => [p.lat, p.lng]));
      if (aoi) b.extend(aoi.getSouthWest()).extend(aoi.getNorthEast());
      map.fitBounds(b.pad(0.25));
    } catch (e) {
      if (aoi) map.fitBounds(aoi.pad(0.35));
    }
    if (typeof persistSoon === 'function') persistSoon();
    if (typeof updateRun === 'function') updateRun();
  }

  async function generateOptimalLeg() {
    if (!aoi) {
      $('sheet').classList.add('open');
      $('result').textContent = 'Draw an AOI first (two clicks for rectangle corners).';
      return;
    }
    if (typeof OptimalLegGenerator === 'undefined') {
      $('result').textContent = 'OptimalLegGenerator module not loaded.';
      return;
    }
    const legKm = Math.max(1, +($('legLen') && $('legLen').value) || 100);
    const p = typeof params === 'function' ? params() : {};
    if (!Number.isFinite(p.lpol)) p.lpol = 0;
    if (!Number.isFinite(p.lsys)) p.lsys = 0;
    if (!Number.isFinite(p.losN)) p.losN = 16;

    const genBtn = $('runBtn');
    if (genBtn) { genBtn.disabled = true; genBtn.textContent = 'Generating…'; }
    setBanner('<b>Optimal:</b> Computing max range envelope and leg placement…');
    await new Promise((r) => setTimeout(r, 20));

    try {
      const result = OptimalLegGenerator.generate({
        aoi,
        legLengthM: legKm * 1000,
        params: p,
        interpGain: typeof interpGain === 'function' ? interpGain : () => 0,
      });

      if (!result.ok) {
        $('sheet').classList.add('open');
        $('result').innerHTML = '<b>Optimal leg failed</b><br>' + result.message;
        setBanner('<b>Failed:</b> ' + result.message);
        return;
      }

      applyOptimalPath(result);
      const v = OptimalLegGenerator.validateCoverage(result.path, aoi, p, interpGain);
      const margins = result.margins || {};
      const horizonKm = margins.horizonM ? (margins.horizonM / 1000) : null;
      const fsKm = margins.RmaxFsM ? (margins.RmaxFsM / 1000) : null;
      const freqLines = (margins.perFreq || [])
        .map((pf) => {
          const fs = pf.Rmax / 1000;
          const capped = horizonKm != null ? Math.min(fs, horizonKm) : fs;
          return pf.freq + ' MHz → usable ' + capped.toFixed(1) + ' km'
            + (fs > capped + 0.5 ? ' (FSPL ' + fs.toFixed(0) + ' km, limited by horizon)' : '');
        })
        .join('<br>');

      $('sheet').classList.add('open');
      $('result').innerHTML =
        '<b>Optimal leg ready</b><br>' + result.message +
        '<br><br><b>Coverage:</b> ' + (v.ok ? 'PASS' : 'FAIL') +
        ' · min SNR ' + (p.minSnr || 10) + ' dB<br>' +
        '<b>Standoff:</b> ' + (result.standoffM / 1000).toFixed(2) + ' km<br>' +
        '<b>Usable ground range Rh:</b> ' + (result.RhM / 1000).toFixed(1) + ' km' +
        (horizonKm != null ? ' · <b>radio horizon:</b> ' + horizonKm.toFixed(0) + ' km' : '') + '<br>' +
        (fsKm != null && horizonKm != null && fsKm > horizonKm + 1
            ? '<span style="color:#fbbf24">FSPL alone would allow ~' + fsKm.toFixed(0) + ' km — capped by Earth horizon</span><br>'
            : '') +
        '<b>Heading:</b> ' + result.headingDeg + '°<br>' +
        (freqLines ? '<br><b>Per-frequency usable range:</b><br>' + freqLines : '') +
        '<br><br>Path loaded into markers. Switch to <b>Forward</b> and click <b>Run Analysis</b> for SNR heatmap.';

      setBanner('<b>Optimal leg drawn</b> · standoff ' + (result.standoffM / 1000).toFixed(2) +
        ' km · switch to Forward + Run Analysis for heatmap.');
    } catch (err) {
      $('result').textContent = 'Error: ' + (err.message || err);
      setBanner('<b>Error.</b> ' + (err.message || err));
    } finally {
      if (genBtn) {
        if (plannerMode === 'optimal') {
          genBtn.disabled = !aoi;
          genBtn.textContent = 'Generate Optimal Leg';
        } else if (typeof updateRun === 'function') updateRun();
      }
    }
  }

  const runBtn = $('runBtn');
  if (runBtn) {
    runBtn.onclick = function () {
      if (plannerMode === 'optimal') { generateOptimalLeg(); return; }
      if (typeof runAnalysis === 'function') runAnalysis();
    };
  }

  setPlannerMode('forward');
})();
