/**
 * ui-ops.js — allowed flight area + SNR margin wiring for Optimal Leg.
 * Loaded after ui.js.
 */
(function () {
  'use strict';

  const opsLayer = L.layerGroup().addTo(map);
  let opsArea = null;
  let opsCorners = [];
  let drawingOps = false;

  function updateOpsHint() {
    const h = $('opsAreaHint');
    if (!h) return;
    if (opsArea && opsArea.length >= 3) {
      h.innerHTML = 'Flight area: <b>' + opsArea.length + ' vertices</b> — optimal leg must stay inside.';
    } else {
      h.innerHTML = 'Flight area: <b>free</b> (no polygon). Optional: draw polygon so leg endpoints stay inside it.';
    }
  }

  function redrawOpsArea() {
    opsLayer.clearLayers();
    if (opsCorners.length) {
      opsCorners.forEach((p, i) => {
        L.circleMarker([p.lat, p.lng], { radius: 5, color: '#a78bfa', weight: 2, fillColor: '#c4b5fd', fillOpacity: 1 })
          .bindTooltip(String(i + 1), { permanent: false }).addTo(opsLayer);
      });
      if (opsCorners.length > 1) {
        L.polyline(opsCorners.map((p) => [p.lat, p.lng]), { color: '#8b5cf6', weight: 2, dashArray: '4 4' }).addTo(opsLayer);
      }
    }
    if (opsArea && opsArea.length >= 3) {
      L.polygon(opsArea.map((p) => [p.lat, p.lng]), {
        color: '#8b5cf6', weight: 2, fillColor: '#7c3aed', fillOpacity: 0.12,
      }).bindTooltip('Allowed flight area', { sticky: true }).addTo(opsLayer);
    }
  }

  function finishOpsArea() {
    if (opsCorners.length < 3) {
      setBanner('<b>Flight area:</b> Need at least 3 corners. Keep clicking.');
      return;
    }
    opsArea = opsCorners.slice();
    opsCorners = [];
    drawingOps = false;
    if (typeof hasAnalysis !== 'undefined') {
      hasAnalysis = typeof lastCells !== 'undefined' && lastCells && lastCells.length > 0;
      if (typeof updateModeButtons === 'function') updateModeButtons();
    }
    redrawOpsArea();
    updateOpsHint();
    setBanner('<b>Flight area set</b> · ' + opsArea.length + ' vertices. Generate Optimal Leg will stay inside it.');
  }

  window.__fpOpsArea = function () { return opsArea; };
  window.__fpSnrMargin = function () {
    const el = $('snrMargin');
    const v = el ? +el.value : 6;
    return Number.isFinite(v) ? v : 6;
  };

  const mOps = $('mOpsArea');
  if (mOps) {
    mOps.onclick = () => {
      const opt = $('modeOptimal');
      if (opt && !opt.classList.contains('on')) opt.click();
      drawingOps = true;
      opsCorners = [];
      opsArea = null;
      if (typeof hasAnalysis !== 'undefined') hasAnalysis = true;
      redrawOpsArea();
      updateOpsHint();
      setBanner('<b>Draw allowed flight area:</b> Click corners (≥3). Click near the first point to close (or double-click).');
    };
  }
  const clearOps = $('clearOpsArea');
  if (clearOps) {
    clearOps.onclick = () => {
      opsArea = null;
      opsCorners = [];
      drawingOps = false;
      if (typeof hasAnalysis !== 'undefined') {
        hasAnalysis = typeof lastCells !== 'undefined' && lastCells && lastCells.length > 0;
        if (typeof updateModeButtons === 'function') updateModeButtons();
      }
      redrawOpsArea();
      updateOpsHint();
      setBanner('<b>Flight area cleared</b> — free placement.');
    };
  }

  map.on('click', function (e) {
    if (!drawingOps) return;
    if (opsCorners.length >= 3) {
      const d = map.distance(e.latlng, L.latLng(opsCorners[0].lat, opsCorners[0].lng));
      if (d < 5000) {
        finishOpsArea();
        return;
      }
    }
    opsCorners.push({ lat: e.latlng.lat, lng: e.latlng.lng });
    redrawOpsArea();
    setBanner(
      opsCorners.length >= 3
        ? '<b>Flight area:</b> ' + opsCorners.length + ' points. Click near first point to close, or keep adding.'
        : '<b>Flight area:</b> ' + opsCorners.length + ' corner(s). Need at least 3.'
    );
  });

  map.on('dblclick', function (e) {
    if (drawingOps && opsCorners.length >= 3) {
      L.DomEvent.stop(e);
      finishOpsArea();
    }
  });

  const runBtn = $('runBtn');
  if (runBtn) {
    const prev = runBtn.onclick;
    runBtn.onclick = function (ev) {
      const p = typeof params === 'function' ? params() : {};
      p.snrMargin = window.__fpSnrMargin();
      if (typeof OptimalLegGenerator !== 'undefined' && OptimalLegGenerator.generate) {
        const orig = OptimalLegGenerator.generate;
        OptimalLegGenerator.generate = function (opts) {
          opts = opts || {};
          opts.marginDb = window.__fpSnrMargin();
          opts.opsArea = window.__fpOpsArea();
          if (opts.params) opts.params.snrMargin = opts.marginDb;
          return orig.call(this, opts);
        };
        try {
          if (typeof prev === 'function') return prev.call(this, ev);
        } finally {
          OptimalLegGenerator.generate = orig;
        }
      } else if (typeof prev === 'function') {
        return prev.call(this, ev);
      }
    };
  }

  const modeOpt = $('modeOptimal');
  if (modeOpt) {
    modeOpt.addEventListener('click', () => {
      setTimeout(() => {
        const sheet = $('sheet');
        if (sheet) sheet.classList.add('open');
        const leg = $('legLen');
        if (leg) {
          try { leg.focus(); leg.select(); } catch (e) {}
        }
        updateOpsHint();
      }, 50);
    });
  }

  updateOpsHint();
})();
