// Georreferenciación: homografía cruces → esquinas del encuadre y reproyección a WGS84.
// Equivale a escritorio/georreferencia.py (homografia) + raster_to_geo() de la versión laptop.
import { PROJ4_DEFS, ORIENTACIONES } from './config.js';

/* global proj4 */

// Coordenadas normalizadas (u, v) de cada esquina geográfica: u crece al Este, v al Norte.
const UV = { NW: [0, 1], NE: [1, 1], SE: [1, 0], SW: [0, 0] };

// Resuelve A·x = b (n×n) por eliminación gaussiana con pivoteo parcial.
function resolver(A, b) {
  const n = b.length;
  const M = A.map((fila, i) => [...fila, b[i]]);
  for (let c = 0; c < n; c++) {
    let piv = c;
    for (let f = c + 1; f < n; f++) if (Math.abs(M[f][c]) > Math.abs(M[piv][c])) piv = f;
    if (Math.abs(M[piv][c]) < 1e-12) return null;
    [M[c], M[piv]] = [M[piv], M[c]];
    for (let f = c + 1; f < n; f++) {
      const k = M[f][c] / M[c][c];
      for (let j = c; j <= n; j++) M[f][j] -= k * M[c][j];
    }
  }
  const x = new Array(n).fill(0);
  for (let f = n - 1; f >= 0; f--) {
    let s = M[f][n];
    for (let j = f + 1; j < n; j++) s -= M[f][j] * x[j];
    x[f] = s / M[f][f];
  }
  return x;
}

// Homografía 3×3 (como arreglo de 9) que lleva 4 puntos origen a 4 puntos destino.
export function homografia4(src, dst) {
  const A = [], b = [];
  for (let i = 0; i < 4; i++) {
    const [x, y] = src[i], [u, v] = dst[i];
    A.push([x, y, 1, 0, 0, 0, -u * x, -u * y]); b.push(u);
    A.push([0, 0, 0, x, y, 1, -v * x, -v * y]); b.push(v);
  }
  const h = resolver(A, b);
  return h ? [...h, 1] : null;
}

export function aplicarH(H, x, y) {
  const w = H[6] * x + H[7] * y + H[8];
  return [(H[0] * x + H[1] * y + H[2]) / w, (H[3] * x + H[4] * y + H[5]) / w];
}

// ================== Orientación ==================

// Con la flecha de norte (junto a la cruz NE) decide qué esquina geográfica es cada cruz.
// corners: [TL, TR, BR, BL] reales; flecha: { c: [x, y], d: [dx, dy] hacia el norte }.
// Igual que esquinas_desde_flecha() de escritorio/georreferencia.py: resuelve giros y espejo.
export function esquinasDesdeFlecha(corners, flecha) {
  const [cx, cy] = flecha.c, [dx, dy] = flecha.d;
  let ne = 0, dmin = Infinity;
  corners.forEach(([x, y], i) => { const dd = Math.hypot(x - cx, y - cy); if (dd < dmin) { dmin = dd; ne = i; } });
  const a = (ne + 3) % 4, b = (ne + 1) % 4;
  const dot = (i) => (corners[i][0] - corners[ne][0]) * dx + (corners[i][1] - corners[ne][1]) * dy;
  const [se, nw] = dot(a) < dot(b) ? [a, b] : [b, a];
  const esq = [];
  esq[ne] = 'NE'; esq[se] = 'SE'; esq[nw] = 'NW'; esq[(ne + 2) % 4] = 'SW';
  return esq;
}

export function nombreEsquinas(esq) {
  const o = Object.values(ORIENTACIONES).find((v) => v.esquinas && v.esquinas.join() === esq.join());
  return o ? o.nombre : 'Imagen en espejo';
}

// Devuelve { esquinas, nota }. 'auto' usa la flecha; sin flecha cae en 'norte_arriba'.
export function resolverEsquinas(orientacion, corners, flecha) {
  if (orientacion === 'auto') {
    if (flecha && corners) {
      const esquinas = esquinasDesdeFlecha(corners, flecha);
      return { esquinas, nota: `Orientación por flecha: ${nombreEsquinas(esquinas)}`, auto: true };
    }
    return { esquinas: ORIENTACIONES.norte_arriba.esquinas, nota: 'No se vio la flecha de norte: se usó “Norte arriba”', auto: false };
  }
  const o = ORIENTACIONES[orientacion] || ORIENTACIONES.norte_arriba;
  return { esquinas: o.esquinas, nota: o.nombre, auto: false };
}

// corners: [[x,y] TL, TR, BR, BL] en píxeles del frame procesado; esquinas: ['NW', …] por cruz.
// Devuelve una función píxel → coordenadas del EPSG origen (metros), o null si la geometría es degenerada.
export function calibrar(corners, bounds, esquinas) {
  const H = homografia4(corners, esquinas.map((e) => UV[e]));
  if (!H) return null;
  const [xmin, ymin, xmax, ymax] = bounds;
  return (px, py) => {
    const [u, v] = aplicarH(H, px, py);
    return [xmin + u * (xmax - xmin), ymin + v * (ymax - ymin)];
  };
}

// ================== Proyecciones ==================

export async function registrarEPSG(epsg) {
  const code = `EPSG:${epsg}`;
  if (proj4.defs(code)) return true;
  if (PROJ4_DEFS[epsg]) { proj4.defs(code, PROJ4_DEFS[epsg]); return true; }
  try {
    const r = await fetch(`https://epsg.io/${epsg}.proj4`);
    if (!r.ok) return false;
    const def = (await r.text()).trim();
    if (!def.startsWith('+')) return false;
    proj4.defs(code, def);
    return true;
  } catch (_) {
    return false;
  }
}

// Función [x, y] (EPSG origen) → [lon, lat] (WGS84). Requiere registrarEPSG() antes.
export function transformador(epsg) {
  const conv = proj4(`EPSG:${epsg}`, 'EPSG:4326');
  return (x, y) => conv.forward([x, y]);
}

// Esquinas del encuadre en lat/lng para dibujar en Leaflet: [SW, NW, NE, SE]
export function esquinasLatLng(bounds, epsg) {
  const t = transformador(epsg);
  const [xmin, ymin, xmax, ymax] = bounds;
  return [[xmin, ymin], [xmin, ymax], [xmax, ymax], [xmax, ymin]].map(([x, y]) => {
    const [lon, lat] = t(x, y);
    return [lat, lon];
  });
}

// Distancia en km (misma fórmula que turf.distance, usada por la dinámica del juego)
export function distanciaKm(lat1, lng1, lat2, lng2) {
  const R = 6371.0088, rad = Math.PI / 180;
  const dLat = (lat2 - lat1) * rad, dLng = (lng2 - lng1) * rad;
  const a = Math.sin(dLat / 2) ** 2 + Math.cos(lat1 * rad) * Math.cos(lat2 * rad) * Math.sin(dLng / 2) ** 2;
  return 2 * R * Math.atan2(Math.sqrt(a), Math.sqrt(1 - a));
}
