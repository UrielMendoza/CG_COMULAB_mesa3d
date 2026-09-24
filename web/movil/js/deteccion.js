// Núcleo de detección con OpenCV.js — puerto directo de escritorio/deteccion_ui.py
// (modo Mesa 3D) y escritorio/deteccion_ui_juego.py (modo Juego).
//
// Todas las coordenadas de píxel son del frame procesado (ya reescalado).
// `georef(px, py)` devuelve [lon, lat] o null si todavía no hay calibración.

// ================== Utilidades ==================

// cv.inRange requiere Mats de límites del mismo tamaño; hacerlo en JS es más simple y igual de rápido.
function mascaraHSV(cv, hsv, p, pref) {
  const lo = [p[`${pref}_H_LOW`], p[`${pref}_S_LOW`], p[`${pref}_V_LOW`]];
  const hi = [p[`${pref}_H_HIGH`], p[`${pref}_S_HIGH`], p[`${pref}_V_HIGH`]];
  const mask = new cv.Mat(hsv.rows, hsv.cols, cv.CV_8UC1);
  const src = hsv.data, dst = mask.data;
  for (let i = 0, j = 0; j < dst.length; i += 3, j++) {
    const h = src[i], s = src[i + 1], v = src[i + 2];
    dst[j] = (h >= lo[0] && h <= hi[0] && s >= lo[1] && s <= hi[1] && v >= lo[2] && v <= hi[2]) ? 255 : 0;
  }
  return mask;
}

function aHSV(cv, rgb, k) {
  const blurred = new cv.Mat(), hsv = new cv.Mat();
  cv.GaussianBlur(rgb, blurred, new cv.Size(k, k), 0, 0, cv.BORDER_DEFAULT);
  cv.cvtColor(blurred, hsv, cv.COLOR_RGB2HSV);
  blurred.delete();
  return hsv;
}

function morph(cv, src, op, kernel, iters = 1) {
  const dst = new cv.Mat();
  if (iters <= 0) { src.copyTo(dst); return dst; } // mismo comportamiento que OpenCV con iterations=0
  cv.morphologyEx(src, dst, op, kernel, new cv.Point(-1, -1), iters,
    cv.BORDER_CONSTANT, cv.morphologyDefaultBorderValue());
  return dst;
}

function contornos(cv, mask) {
  const cs = new cv.MatVector(), hier = new cv.Mat();
  cv.findContours(mask, cs, hier, cv.RETR_EXTERNAL, cv.CHAIN_APPROX_SIMPLE);
  hier.delete();
  return cs;
}

function puntosDe(mat) {
  const d = mat.data32S, pts = [];
  for (let i = 0; i < d.length; i += 2) pts.push([d[i], d[i + 1]]);
  return pts;
}

// Esquinas de un RotatedRect (equivalente a cv2.boxPoints)
function boxPoints(rect) {
  const a = rect.angle * Math.PI / 180, c = Math.cos(a) * 0.5, s = Math.sin(a) * 0.5;
  const { x, y } = rect.center, { width: w, height: h } = rect.size;
  return [
    [x - s * h - c * w, y + c * h - s * w],
    [x + s * h - c * w, y - c * h - s * w],
    [x + s * h + c * w, y - c * h + s * w],
    [x - s * h + c * w, y + c * h + s * w],
  ];
}

// Equivalente a contour_line_metrics(): cv2.fitLine(DIST_L2) = eje principal por mínimos cuadrados
function metricasLinea(pts) {
  if (pts.length < 2) return { length: 0, width: 0, aspect: 0 };
  let mx = 0, my = 0;
  for (const [x, y] of pts) { mx += x; my += y; }
  mx /= pts.length; my /= pts.length;
  let sxx = 0, sxy = 0, syy = 0;
  for (const [x, y] of pts) { const dx = x - mx, dy = y - my; sxx += dx * dx; sxy += dx * dy; syy += dy * dy; }
  const th = 0.5 * Math.atan2(2 * sxy, sxx - syy);
  const vx = Math.cos(th), vy = Math.sin(th);
  let tmin = Infinity, tmax = -Infinity, wsum = 0;
  for (const [x, y] of pts) {
    const dx = x - mx, dy = y - my;
    const t = dx * vx + dy * vy;
    if (t < tmin) tmin = t;
    if (t > tmax) tmax = t;
    wsum += Math.abs(-dx * vy + dy * vx);
  }
  const length = tmax - tmin;
  const width = Math.max((wsum / pts.length) * 2, 1e-6);
  return { length, width, aspect: length / width };
}

// Equivalente a refine_mask_for_lines()
function refinarMascaraLineas(cv, mask, kLong, kShort, iters) {
  kLong = Math.max(3, kLong | 1);
  kShort = Math.max(3, kShort | 1);
  const kH = cv.getStructuringElement(cv.MORPH_RECT, new cv.Size(kLong, 1));
  const kV = cv.getStructuringElement(cv.MORPH_RECT, new cv.Size(1, kLong));
  const kS = cv.getStructuringElement(cv.MORPH_ELLIPSE, new cv.Size(kShort, kShort));
  const m = morph(cv, mask, cv.MORPH_OPEN, kS, 1);
  const mH = morph(cv, m, cv.MORPH_CLOSE, kH, iters);
  const mV = morph(cv, m, cv.MORPH_CLOSE, kV, iters);
  const out = new cv.Mat();
  cv.bitwise_or(mH, mV, out);
  [kH, kV, kS, m, mH, mV].forEach((x) => x.delete());
  return out;
}

// ================== Cruces azules (calibración) ==================

// Equivalente a find_blue_cross_corners(). A diferencia de Python, devuelve las esquinas
// en su posición real [TL, TR, BR, BL]; la orientación del mapa se elige aparte (config.ORIENTACIONES).
export function buscarCruces(cv, rgb, p, cfg, conMascara = false) {
  const hsv = aHSV(cv, rgb, cfg.blur);
  const raw = mascaraHSV(cv, hsv, p, 'BLUE');
  hsv.delete();
  const k = cv.getStructuringElement(cv.MORPH_ELLIPSE, new cv.Size(3, 3));
  let mask = morph(cv, raw, cv.MORPH_OPEN, k);
  raw.delete();
  if (cfg.cerrar) { const m2 = morph(cv, mask, cv.MORPH_CLOSE, k); mask.delete(); mask = m2; }
  k.delete();

  const cs = contornos(cv, mask);
  const centroides = [];
  for (let i = 0; i < cs.size(); i++) {
    const c = cs.get(i);
    const area = cv.contourArea(c);
    const r = cv.boundingRect(c);
    const ar = r.height > 0 ? r.width / r.height : 0;
    if (area >= cfg.areaMin && ar >= cfg.arMin && ar <= cfg.arMax) {
      const M = cv.moments(c);
      if (M.m00 > 0) centroides.push([M.m10 / M.m00, M.m01 / M.m00]);
    }
    c.delete();
  }
  cs.delete();

  let mascara = null;
  if (conMascara) mascara = mask; else mask.delete();

  if (centroides.length < 4) return { corners: null, crop: null, centroides, mascara };

  const idx = (f, mejor) => centroides.reduce((b, pt, i) => (mejor(f(pt), f(centroides[b])) ? i : b), 0);
  const suma = (pt) => pt[0] + pt[1], dif = (pt) => pt[1] - pt[0];
  const tl = centroides[idx(suma, (a, b) => a < b)];
  const br = centroides[idx(suma, (a, b) => a > b)];
  const tr = centroides[idx(dif, (a, b) => a < b)]; // y−x mínimo: arriba-derecha
  const bl = centroides[idx(dif, (a, b) => a > b)]; // y−x máximo: abajo-izquierda
  const corners = [tl, tr, br, bl];

  const xs = corners.map((c) => c[0]), ys = corners.map((c) => c[1]);
  const pad = 5;
  const crop = [
    Math.max(0, Math.floor(Math.min(...xs)) - pad),
    Math.max(0, Math.floor(Math.min(...ys)) - pad),
    Math.min(rgb.cols - 1, Math.ceil(Math.max(...xs)) + pad),
    Math.min(rgb.rows - 1, Math.ceil(Math.max(...ys)) + pad),
  ];
  return { corners, crop, centroides, mascara };
}

// ================== Modo Mesa 3D: amarillo + verde → punto / línea / polígono ==================

// Equivalente a process_frame_generic(). `rgb` puede ser el recorte; (xoff, yoff) lo ubican en el frame.
export function detectarMesa(cv, rgb, p, georef, xoff, yoff, mascaraDe = null) {
  const hsv = aHSV(cv, rgb, 3);
  const detecciones = [], dibujo = [];
  const stats = { yellow: { point: 0, line: 0, polygon: 0 }, green: { point: 0, line: 0, polygon: 0 } };
  let mascara = null;

  for (const [color, pref] of [['yellow', 'YELLOW'], ['green', 'GREEN']]) {
    const base = mascaraHSV(cv, hsv, p, pref);
    const ref = refinarMascaraLineas(cv, base, p.K_LONG, p.K_SHORT, p.MORPH_ITERS);
    base.delete();
    const cs = contornos(cv, ref);
    if (mascaraDe === color) mascara = ref; else ref.delete();

    for (let i = 0; i < cs.size(); i++) {
      const c = cs.get(i);
      const area = cv.contourArea(c);
      if (area < p.MIN_AREA_POINT) { c.delete(); continue; }

      const approx = new cv.Mat();
      cv.approxPolyDP(c, approx, 0.01 * cv.arcLength(c, true), true);
      const rect = cv.minAreaRect(approx);
      const pts = puntosDe(approx).map(([x, y]) => [x + xoff, y + yoff]);
      approx.delete(); c.delete();
      const { width: w2, height: h2 } = rect.size;
      const aspectRatio = Math.max(w2, h2) / Math.max(Math.min(w2, h2), 1e-6);

      const geo = georef ? pts.map(([x, y]) => georef(x, y)) : [];

      let tipo, geometry = null;
      if (!georef || geo.length < 2) {
        tipo = 'point';
        geometry = georef && geo.length ? { type: 'Point', coordinates: geo[0] } : null;
      } else {
        const lm = metricasLinea(pts);
        if (area >= p.MIN_AREA_LINE && lm.aspect >= p.MIN_LINE_ASPECT && Math.max(w2, h2) >= p.MIN_LINE_LENGTH) {
          tipo = 'line';
          geometry = { type: 'LineString', coordinates: geo };
        } else if (area > p.MIN_AREA_POLY && aspectRatio < 2.0) {
          tipo = 'polygon';
          const anillo = [...geo];
          const [a, b] = [anillo[0], anillo[anillo.length - 1]];
          if (a[0] !== b[0] || a[1] !== b[1]) anillo.push(a);
          geometry = anillo.length >= 4 ? { type: 'Polygon', coordinates: [anillo] } : null;
        } else {
          tipo = 'point';
          geometry = { type: 'Point', coordinates: geo[0] };
        }
      }

      stats[color][tipo]++;
      detecciones.push({ geometry, color, type: tipo });
      const centro = [rect.center.x + xoff, rect.center.y + yoff];
      const caja = boxPoints(rect).map(([x, y]) => [x + xoff, y + yoff]);
      dibujo.push({ tipo, color, pts, caja, centro });
    }
    cs.delete();
  }
  hsv.delete();
  return { detecciones, dibujo, stats, mascara };
}

// ================== Modo Juego: solo plastilina verde → puntos ==================

// Equivalente a detect_green_points()
export function detectarJuego(cv, rgb, p, georef, xoff, yoff, conMascara = false) {
  const hsv = aHSV(cv, rgb, 5);
  const raw = mascaraHSV(cv, hsv, p, 'GREEN');
  hsv.delete();
  const k = cv.getStructuringElement(cv.MORPH_ELLIPSE, new cv.Size(5, 5));
  const m1 = morph(cv, raw, cv.MORPH_OPEN, k);
  const mask = morph(cv, m1, cv.MORPH_CLOSE, k);
  [raw, m1, k].forEach((x) => x.delete());

  const cs = contornos(cv, mask);
  const detecciones = [], dibujo = [];
  for (let i = 0; i < cs.size(); i++) {
    const c = cs.get(i);
    const area = cv.contourArea(c);
    if (area >= p.MIN_AREA && area <= p.MAX_AREA) {
      const M = cv.moments(c);
      if (M.m00 !== 0) {
        const cx = Math.trunc(M.m10 / M.m00) + xoff, cy = Math.trunc(M.m01 / M.m00) + yoff;
        const lonlat = georef ? georef(cx, cy) : null;
        detecciones.push({ geometry: lonlat ? { type: 'Point', coordinates: lonlat } : null, color: 'green', type: 'point' });
        dibujo.push({ tipo: 'point', color: 'green', centro: [cx, cy], n: detecciones.length });
      }
    }
    c.delete();
  }
  cs.delete();
  let mascara = null;
  if (conMascara) mascara = mask; else mask.delete();
  return { detecciones, dibujo, stats: { green: { point: detecciones.length } }, mascara };
}

// Convierte detecciones a FeatureCollection (mismas propiedades que los GeoJSON de la versión laptop)
export function aGeoJSON(detecciones) {
  return {
    type: 'FeatureCollection',
    features: detecciones.filter((d) => d.geometry).map((d) => ({
      type: 'Feature',
      properties: { color: d.color, type: d.type },
      geometry: d.geometry,
    })),
  };
}
