/**
 * OptimalLegGenerator — reverse-planning module
 * Generates a straight flight leg of fixed length L that maximizes standoff
 * from the AOI while guaranteeing SNR >= minSnr for every AOI sample
 * across all configured frequencies (worst-case link budget).
 *
 * Range is FSPL limited AND capped by radio horizon (k=4/3 Earth).
 * Includes safety margin so Run Analysis (with diffraction) still meets min SNR.
 * Optional opsArea polygon constrains leg endpoints.
 * Does not mutate global path/AOI state; caller applies the result.
 */
(function (global) {
  'use strict';

  const C = 299792458;
  const R_EARTH = 6371000;

  function havMeters(a, b) {
    const p = Math.PI / 180;
    const dLat = (b.lat - a.lat) * p;
    const dLon = (b.lng - a.lng) * p;
    const x =
      Math.sin(dLat / 2) ** 2 +
      Math.cos(a.lat * p) * Math.cos(b.lat * p) * Math.sin(dLon / 2) ** 2;
    return 2 * R_EARTH * Math.asin(Math.min(1, Math.sqrt(x)));
  }

  function toXY(lat, lng, origin) {
    const mLat = 111320;
    const mLng = 111320 * Math.cos((origin.lat * Math.PI) / 180);
    return {
      x: (lng - origin.lng) * mLng,
      y: (lat - origin.lat) * mLat,
    };
  }

  function fromXY(x, y, origin) {
    const mLat = 111320;
    const mLng = 111320 * Math.cos((origin.lat * Math.PI) / 180);
    return {
      lat: origin.lat + y / mLat,
      lng: origin.lng + x / mLng,
    };
  }

  /** Ray-casting point-in-polygon. poly = [{lat,lng}, ...] */
  function pointInPoly(lat, lng, poly) {
    if (!poly || poly.length < 3) return true;
    let inside = false;
    for (let i = 0, j = poly.length - 1; i < poly.length; j = i++) {
      const yi = poly[i].lat, xi = poly[i].lng;
      const yj = poly[j].lat, xj = poly[j].lng;
      const intersect = ((yi > lat) !== (yj > lat)) &&
        (lng < (xj - xi) * (lat - yi) / ((yj - yi) || 1e-15) + xi);
      if (intersect) inside = !inside;
    }
    return inside;
  }

  function legInsideOps(a, b, opsPoly, origin) {
    if (!opsPoly || opsPoly.length < 3) return true;
    const pa = fromXY(a.x, a.y, origin);
    const pb = fromXY(b.x, b.y, origin);
    const mid = { x: (a.x + b.x) / 2, y: (a.y + b.y) / 2 };
    const pm = fromXY(mid.x, mid.y, origin);
    return pointInPoly(pa.lat, pa.lng, opsPoly) &&
      pointInPoly(pb.lat, pb.lng, opsPoly) &&
      pointInPoly(pm.lat, pm.lng, opsPoly);
  }

  function distPointSeg(px, py, ax, ay, bx, by) {
    const dx = bx - ax;
    const dy = by - ay;
    const len2 = dx * dx + dy * dy;
    if (len2 < 1e-12) return Math.hypot(px - ax, py - ay);
    let t = ((px - ax) * dx + (py - ay) * dy) / len2;
    t = Math.max(0, Math.min(1, t));
    return Math.hypot(px - (ax + t * dx), py - (ay + t * dy));
  }

  /**
   * Max slant range where SNR >= minSnr after safety margin.
   * Same link budget as forward analysis (Pt, Gr(f), NF, BW, Lpol, Lsys).
   */
  function maxSlantRange(p, freq, minSnr, interpGain, marginDb) {
    const gr = typeof interpGain === 'function' ? interpGain(freq) : 0;
    const lpol = Number.isFinite(p.lpol) ? p.lpol : 0;
    const lsys = Number.isFinite(p.lsys) ? p.lsys : 0;
    const margin = Number.isFinite(marginDb) ? Math.max(0, marginDb) : 0;
    const noise = -174 + 10 * Math.log10(Math.max(p.bwHz || 25000, 1)) + (p.nf || 0);
    const maxFspl = (p.pt || 0) + gr - lpol - lsys - noise - minSnr - margin;
    if (!(maxFspl > 0)) return 0;
    const d = Math.pow(10, maxFspl / 20) * C / (4 * Math.PI * Math.max(freq, 1) * 1e6);
    return d;
  }

  function horizFromSlant(slant, altM) {
    const a = Math.abs(altM || 0);
    if (slant <= a) return 0;
    return Math.sqrt(slant * slant - a * a);
  }

  function radioHorizonMeters(altM) {
    const h = Math.max(0, altM || 0);
    const kR = (4 / 3) * R_EARTH;
    return Math.sqrt(2 * kR * h + h * h);
  }

  function sampleAoiGrid(bounds, n) {
    n = Math.max(4, Math.min(24, n | 0));
    const sw = bounds.getSouthWest();
    const ne = bounds.getNorthEast();
    const pts = [];
    for (let iy = 0; iy <= n; iy++) {
      for (let ix = 0; ix <= n; ix++) {
        pts.push({
          lat: sw.lat + ((ne.lat - sw.lat) * iy) / n,
          lng: sw.lng + ((ne.lng - sw.lng) * ix) / n,
        });
      }
    }
    return pts;
  }

  function legCovers(aPts, bPts, aoiXY, Rh) {
    for (let i = 0; i < aoiXY.length; i++) {
      const p = aoiXY[i];
      const d = distPointSeg(p.x, p.y, aPts.x, aPts.y, bPts.x, bPts.y);
      if (d > Rh + 1) return false;
    }
    return true;
  }

  function generate(opts) {
    const aoi = opts.aoi;
    const L = Math.max(1000, opts.legLengthM || 100000);
    const p = opts.params || {};
    const minSnr = Number.isFinite(p.minSnr) ? p.minSnr : 10;
    const marginDb = Number.isFinite(opts.marginDb) ? opts.marginDb
      : (Number.isFinite(p.snrMargin) ? p.snrMargin : 6);
    const interpGain = opts.interpGain || (() => 0);
    const freqs = p.freqs && p.freqs.length ? p.freqs : [100];
    const opsPoly = opts.opsArea && opts.opsArea.length >= 3 ? opts.opsArea : null;

    let Rmax = Infinity;
    const perFreq = [];
    let worstFreq = freqs[0];
    for (const f of freqs) {
      const r = maxSlantRange(p, f, minSnr, interpGain, marginDb);
      const gr = typeof interpGain === 'function' ? interpGain(f) : 0;
      perFreq.push({ freq: f, Rmax: r, Gr: gr });
      if (r < Rmax) { Rmax = r; worstFreq = f; }
    }
    if (!(Rmax > 50)) {
      return {
        ok: false,
        message:
          'Minimum SNR unattainable: link budget yields near-zero range. Increase Tx power, improve antenna gain, lower min SNR, or reduce frequency.',
        margins: { perFreq, minSnr, marginDb },
      };
    }

    const horizon = radioHorizonMeters(p.altM || 0);
    const RmaxFs = Rmax;
    const RmaxEff = Math.min(Rmax, Math.sqrt(horizon * horizon + (p.altM || 0) * (p.altM || 0)));
    Rmax = RmaxEff;
    const Rh = Math.min(horizFromSlant(RmaxFs, p.altM || 0), horizon);
    if (!(Rh > 50)) {
      return {
        ok: false,
        message:
          'Minimum SNR unattainable at this altitude (slant range < altitude or below horizon). Lower altitude or relax SNR / power.',
        margins: { RmaxM: Rmax, RmaxFsM: RmaxFs, horizonM: horizon, RhM: Rh, perFreq, minSnr, marginDb },
      };
    }

    const center = aoi.getCenter();
    const origin = { lat: center.lat, lng: center.lng };
    const aoiPts = sampleAoiGrid(aoi, 12);
    const aoiXY = aoiPts.map((q) => toXY(q.lat, q.lng, origin));

    let best = null;

    for (let deg = 0; deg < 180; deg += 10) {
      const rad = (deg * Math.PI) / 180;
      const ux = Math.cos(rad);
      const uy = Math.sin(rad);
      const nx = -uy;
      const ny = ux;

      let minN = Infinity, maxN = -Infinity, minT = Infinity, maxT = -Infinity;
      for (const q of aoiXY) {
        const t = q.x * ux + q.y * uy;
        const n = q.x * nx + q.y * ny;
        minN = Math.min(minN, n);
        maxN = Math.max(maxN, n);
        minT = Math.min(minT, t);
        maxT = Math.max(maxT, t);
      }
      const aoiCenterT = (minT + maxT) / 2;
      const aoiCenterN = (minN + maxN) / 2;

      for (const side of [1, -1]) {
        let lo = 0;
        let hi = Math.max(Rh, (maxN - minN) / 2 + Rh);
        let feasible = null;
        for (let iter = 0; iter < 18; iter++) {
          const mid = (lo + hi) / 2;
          const legCenterN = aoiCenterN + side * mid;
          const legCenterT = aoiCenterT;
          const halfL = L / 2;
          const a = {
            x: legCenterT * ux + legCenterN * nx - halfL * ux,
            y: legCenterT * uy + legCenterN * ny - halfL * uy,
          };
          const b = {
            x: legCenterT * ux + legCenterN * nx + halfL * ux,
            y: legCenterT * uy + legCenterN * ny + halfL * uy,
          };
          if (legCovers(a, b, aoiXY, Rh) && legInsideOps(a, b, opsPoly, origin)) {
            feasible = { a, b, standoff: mid, side };
            lo = mid;
          } else {
            hi = mid;
          }
        }
        if (feasible) {
          if (!best || feasible.standoff > best.standoff) {
            best = { ...feasible, headingDeg: deg, ux, uy, nx, ny };
          }
        }
      }
    }

    if (!best) {
      return {
        ok: false,
        message:
          'Cannot cover the entire AOI at the required min SNR with leg length ' +
          (L / 1000).toFixed(1) +
          ' km' +
          (opsPoly ? ' inside the allowed flight area' : '') +
          '. Try longer leg, lower min SNR, higher Tx power, smaller AOI, or larger flight area. Rmax≈' +
          (Rmax / 1000).toFixed(1) +
          ' km, Rh≈' +
          (Rh / 1000).toFixed(1) +
          ' km.',
        margins: { RmaxM: Rmax, RmaxFsM: RmaxFs, horizonM: horizon, RhM: Rh, perFreq, minSnr, marginDb, legLengthM: L },
      };
    }

    const p1 = fromXY(best.a.x, best.a.y, origin);
    const p2 = fromXY(best.b.x, best.b.y, origin);
    const actualLen = havMeters(p1, p2);

    return {
      ok: true,
      path: [p1, p2],
      standoffM: best.standoff,
      RmaxM: Rmax,
      RhM: Rh,
      headingDeg: best.headingDeg,
      legLengthM: actualLen,
      margins: {
        minSnr,
        perFreq,
        RmaxM: Rmax,
        RmaxFsM: RmaxFs,
        horizonM: horizon,
        RhM: Rh,
        standoffM: best.standoff,
        coverage: '100% AOI within Rh (FSPL + ' + marginDb + ' dB margin, horizon k=4/3)',
        marginDb,
        worstFreq,
      },
      message:
        'Optimal leg · L=' +
        (actualLen / 1000).toFixed(1) +
        ' km · standoff≈' +
        (best.standoff / 1000).toFixed(2) +
        ' km · Rh≈' +
        (Rh / 1000).toFixed(1) +
        ' km · worst freq ' +
        worstFreq +
        ' MHz · margin ' +
        marginDb +
        ' dB · heading ' +
        best.headingDeg +
        '°' +
        (opsPoly ? ' · constrained to flight area' : ''),
    };
  }

  function validateCoverage(path, aoi, p, interpGain) {
    if (!path || path.length < 2 || !aoi) return { ok: false, message: 'Missing path or AOI' };
    const minSnr = Number.isFinite(p.minSnr) ? p.minSnr : 10;
    const freqs = p.freqs && p.freqs.length ? p.freqs : [100];
    const marginDb = Number.isFinite(p.snrMargin) ? p.snrMargin : 6;
    let Rmax = Infinity;
    for (const f of freqs) Rmax = Math.min(Rmax, maxSlantRange(p, f, minSnr, interpGain, marginDb));
    const horizon = radioHorizonMeters(p.altM || 0);
    const Rh = Math.min(horizFromSlant(Rmax, p.altM || 0), horizon);
    const origin = { lat: aoi.getCenter().lat, lng: aoi.getCenter().lng };
    const aoiXY = sampleAoiGrid(aoi, 16).map((q) => toXY(q.lat, q.lng, origin));
    const a = toXY(path[0].lat, path[0].lng, origin);
    const b = toXY(path[path.length - 1].lat, path[path.length - 1].lng, origin);
    const ok = legCovers(a, b, aoiXY, Rh);
    return {
      ok,
      RhM: Rh,
      RmaxM: Rmax,
      horizonM: horizon,
      message: ok ? 'Coverage OK at min SNR ' + minSnr + ' dB' : 'Coverage FAILED at min SNR ' + minSnr + ' dB',
    };
  }

  global.OptimalLegGenerator = {
    generate,
    validateCoverage,
    maxSlantRange,
    horizFromSlant,
    radioHorizonMeters,
    pointInPoly,
  };
})(typeof window !== 'undefined' ? window : globalThis);
