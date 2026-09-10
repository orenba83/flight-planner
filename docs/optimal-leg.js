/**
 * OptimalLegGenerator — reverse-planning module
 * Straight leg of fixed length L maximizing standoff while covering AOI at min SNR.
 * Optional opsArea: entire leg must lie inside the allowed flight polygon.
 * If 100% coverage is impossible, returns best-effort leg (max coverage fraction).
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

  function pointInPoly(lat, lng, poly) {
    if (!poly || poly.length < 3) return true;
    let inside = false;
    for (let i = 0, j = poly.length - 1; i < poly.length; j = i++) {
      const yi = poly[i].lat,
        xi = poly[i].lng;
      const yj = poly[j].lat,
        xj = poly[j].lng;
      const intersect =
        yi > lat !== yj > lat &&
        lng < ((xj - xi) * (lat - yi)) / (yj - yi || 1e-15) + xi;
      if (intersect) inside = !inside;
    }
    return inside;
  }

  function legInsideOps(a, b, opsPoly, origin) {
    if (!opsPoly || opsPoly.length < 3) return true;
    const n = 12;
    for (let i = 0; i <= n; i++) {
      const t = i / n;
      const x = a.x + (b.x - a.x) * t;
      const y = a.y + (b.y - a.y) * t;
      const p = fromXY(x, y, origin);
      if (!pointInPoly(p.lat, p.lng, opsPoly)) return false;
    }
    return true;
  }

  function maxChordInOps(opsPoly) {
    if (!opsPoly || opsPoly.length < 2) return Infinity;
    let maxD = 0;
    for (let i = 0; i < opsPoly.length; i++) {
      for (let j = i + 1; j < opsPoly.length; j++) {
        const d = havMeters(opsPoly[i], opsPoly[j]);
        if (d > maxD) maxD = d;
      }
    }
    return maxD;
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

  function maxSlantRange(p, freq, minSnr, interpGain, marginDb) {
    const gr = typeof interpGain === 'function' ? interpGain(freq) : 0;
    const lpol = Number.isFinite(p.lpol) ? p.lpol : 0;
    const lsys = Number.isFinite(p.lsys) ? p.lsys : 0;
    const margin = Number.isFinite(marginDb) ? Math.max(0, marginDb) : 0;
    const noise = -174 + 10 * Math.log10(Math.max(p.bwHz || 25000, 1)) + (p.nf || 0);
    const maxFspl = (p.pt || 0) + gr - lpol - lsys - noise - minSnr - margin;
    if (!(maxFspl > 0)) return 0;
    return (Math.pow(10, maxFspl / 20) * C) / (4 * Math.PI * Math.max(freq, 1) * 1e6);
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
    return coverageFraction(aPts, bPts, aoiXY, Rh) >= 1 - 1e-9;
  }

  function coverageFraction(aPts, bPts, aoiXY, Rh) {
    if (!aoiXY.length) return 0;
    let ok = 0;
    for (let i = 0; i < aoiXY.length; i++) {
      const p = aoiXY[i];
      const d = distPointSeg(p.x, p.y, aPts.x, aPts.y, bPts.x, bPts.y);
      if (d <= Rh + 1) ok++;
    }
    return ok / aoiXY.length;
  }

  function generate(opts) {
    const aoi = opts.aoi;
    const L = Math.max(1000, opts.legLengthM || 100000);
    const p = opts.params || {};
    const minSnr = Number.isFinite(p.minSnr) ? p.minSnr : 10;
    const marginDb = 0;
    const interpGain = opts.interpGain || (() => 0);
    const freqs = p.freqs && p.freqs.length ? p.freqs : [100];

    let opsPoly = opts.opsArea && opts.opsArea.length >= 3 ? opts.opsArea : null;
    if (!opsPoly && typeof window !== 'undefined' && typeof window.__fpOpsArea === 'function') {
      const w = window.__fpOpsArea();
      if (w && w.length >= 3) opsPoly = w;
    }

    if (opsPoly) {
      const maxChord = maxChordInOps(opsPoly);
      if (L > maxChord * 1.02) {
        return {
          ok: false,
          message:
            'Leg length L=' +
            (L / 1000).toFixed(1) +
            ' km is longer than the allowed flight area (approx max span ' +
            (maxChord / 1000).toFixed(1) +
            ' km). Shorten L or enlarge the allowed flight area.',
          margins: { legLengthM: L, maxChordM: maxChord, minSnr },
        };
      }
    }

    let Rmax = Infinity;
    const perFreq = [];
    let worstFreq = freqs[0];
    for (const f of freqs) {
      const r = maxSlantRange(p, f, minSnr, interpGain, marginDb);
      const gr = typeof interpGain === 'function' ? interpGain(f) : 0;
      perFreq.push({ freq: f, Rmax: r, Gr: gr });
      if (r < Rmax) {
        Rmax = r;
        worstFreq = f;
      }
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
    const RmaxEff = Math.min(
      Rmax,
      Math.sqrt(horizon * horizon + (p.altM || 0) * (p.altM || 0))
    );
    Rmax = RmaxEff;
    const Rh = Math.min(horizFromSlant(RmaxFs, p.altM || 0), horizon);
    if (!(Rh > 50)) {
      return {
        ok: false,
        message:
          'Minimum SNR unattainable at this altitude (slant range < altitude or below horizon). Lower altitude or relax SNR / power.',
        margins: {
          RmaxM: Rmax,
          RmaxFsM: RmaxFs,
          horizonM: horizon,
          RhM: Rh,
          perFreq,
          minSnr,
          marginDb,
        },
      };
    }

    const center = aoi.getCenter();
    const origin = { lat: center.lat, lng: center.lng };
    const aoiPts = sampleAoiGrid(aoi, 12);
    const aoiXY = aoiPts.map((q) => toXY(q.lat, q.lng, origin));

    let bestFull = null;
    let bestPartial = null;

    function consider(a, b, standoff, side, deg, ux, uy, nx, ny) {
      if (opsPoly && !legInsideOps(a, b, opsPoly, origin)) return;
      const frac = coverageFraction(a, b, aoiXY, Rh);
      const cand = {
        a,
        b,
        standoff,
        side,
        headingDeg: deg,
        ux,
        uy,
        nx,
        ny,
        coverageFrac: frac,
      };
      if (frac >= 1 - 1e-9) {
        if (!bestFull || standoff > bestFull.standoff) bestFull = cand;
      }
      if (
        !bestPartial ||
        frac > bestPartial.coverageFrac + 1e-6 ||
        (Math.abs(frac - bestPartial.coverageFrac) < 1e-6 && standoff > bestPartial.standoff)
      ) {
        bestPartial = cand;
      }
    }

    for (let deg = 0; deg < 180; deg += 10) {
      const rad = (deg * Math.PI) / 180;
      const ux = Math.cos(rad);
      const uy = Math.sin(rad);
      const nx = -uy;
      const ny = ux;

      let minN = Infinity,
        maxN = -Infinity,
        minT = Infinity,
        maxT = -Infinity;
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
      const halfL = L / 2;

      for (const side of [1, -1]) {
        let lo = 0;
        let hi = Math.max(Rh, (maxN - minN) / 2 + Rh);
        let feasible = null;
        for (let iter = 0; iter < 18; iter++) {
          const mid = (lo + hi) / 2;
          const legCenterN = aoiCenterN + side * mid;
          const a = {
            x: aoiCenterT * ux + legCenterN * nx - halfL * ux,
            y: aoiCenterT * uy + legCenterN * ny - halfL * uy,
          };
          const b = {
            x: aoiCenterT * ux + legCenterN * nx + halfL * ux,
            y: aoiCenterT * uy + legCenterN * ny + halfL * uy,
          };
          if (legCovers(a, b, aoiXY, Rh) && (!opsPoly || legInsideOps(a, b, opsPoly, origin))) {
            feasible = { a, b, standoff: mid, side };
            lo = mid;
          } else {
            hi = mid;
          }
        }
        if (feasible) {
          consider(feasible.a, feasible.b, feasible.standoff, side, deg, ux, uy, nx, ny);
        }

        const maxS = Math.max(Rh, (maxN - minN) / 2 + Rh);
        const steps = 12;
        for (let s = 0; s <= steps; s++) {
          const standoff = (maxS * s) / steps;
          const legCenterN = aoiCenterN + side * standoff;
          for (const tOff of [-0.25, 0, 0.25]) {
            const legCenterT = aoiCenterT + tOff * (maxT - minT);
            const a = {
              x: legCenterT * ux + legCenterN * nx - halfL * ux,
              y: legCenterT * uy + legCenterN * ny - halfL * uy,
            };
            const b = {
              x: legCenterT * ux + legCenterN * nx + halfL * ux,
              y: legCenterT * uy + legCenterN * ny + halfL * uy,
            };
            consider(a, b, standoff, side, deg, ux, uy, nx, ny);
          }
        }
      }
    }

    const best = bestFull || bestPartial;
    if (!best) {
      return {
        ok: false,
        message:
          'Could not place any leg of L=' +
          (L / 1000).toFixed(1) +
          ' km' +
          (opsPoly ? ' inside the allowed flight area' : '') +
          '. Enlarge the flight area, shorten L, or clear the flight-area constraint.',
        margins: { RmaxM: Rmax, RhM: Rh, perFreq, minSnr, legLengthM: L },
      };
    }

    const p1 = fromXY(best.a.x, best.a.y, origin);
    const p2 = fromXY(best.b.x, best.b.y, origin);
    const actualLen = havMeters(p1, p2);
    const frac = best.coverageFrac != null ? best.coverageFrac : 1;
    const full = frac >= 1 - 1e-9;
    const pct = Math.round(frac * 1000) / 10;

    const warn = full
      ? null
      : 'Best-effort only: about ' +
        pct +
        '% of the AOI reaches min SNR along this leg (not 100%). ' +
        'Improve by longer L, lower min SNR, higher Tx power, larger flight area, or smaller AOI.';

    return {
      ok: true,
      partial: !full,
      coverageFrac: frac,
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
        coverageFrac: frac,
        coverage:
          (full ? '100%' : pct + '%') +
          ' AOI within Rh' +
          (opsPoly ? ' · leg inside flight area' : ''),
        marginDb,
        worstFreq,
      },
      warning: warn,
      message: full
        ? 'Optimal leg · L=' +
          (actualLen / 1000).toFixed(1) +
          ' km · standoff≈' +
          (best.standoff / 1000).toFixed(2) +
          ' km · Rh≈' +
          (Rh / 1000).toFixed(1) +
          ' km · worst freq ' +
          worstFreq +
          ' MHz · heading ' +
          best.headingDeg +
          '°' +
          (opsPoly ? ' · inside flight area' : '')
        : 'Best-effort leg · ~' +
          pct +
          '% AOI at min SNR · L=' +
          (actualLen / 1000).toFixed(1) +
          ' km · standoff≈' +
          (best.standoff / 1000).toFixed(2) +
          ' km · heading ' +
          best.headingDeg +
          '°' +
          (opsPoly ? ' · inside flight area' : '') +
          '. Not fully optimal — see warning.',
    };
  }

  function validateCoverage(path, aoi, p, interpGain) {
    if (!path || path.length < 2 || !aoi)
      return { ok: false, message: 'Missing path or AOI' };
    const minSnr = Number.isFinite(p.minSnr) ? p.minSnr : 10;
    const freqs = p.freqs && p.freqs.length ? p.freqs : [100];
    const marginDb = 0;
    let Rmax = Infinity;
    for (const f of freqs)
      Rmax = Math.min(Rmax, maxSlantRange(p, f, minSnr, interpGain, marginDb));
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
      message: ok
        ? 'Coverage OK at min SNR ' + minSnr + ' dB'
        : 'Coverage FAILED at min SNR ' + minSnr + ' dB',
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
