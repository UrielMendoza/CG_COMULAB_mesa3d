// Configuración compartida por el mapa (web/mapa) y la app del celular (web/movil).
// Presets geográficos, orientaciones y colores de plastilina se leen de config/*.json,
// los mismos archivos que usan los detectores de la versión laptop (escritorio/).

const RUTA_CONFIG = new URL('../../../config/', import.meta.url).href;

export let PRESETS = {};
export let ORIENTACIONES = {};
export let COLORES = {};   // id → { nombre, hex, hsv: [[hLo,hHi],[sLo,sHi],[vLo,vHi]], activo }

const limpio = (o) => Object.fromEntries(Object.entries(o).filter(([k]) => !k.startsWith('_')));

export async function cargarConfigGeo() {
  const [geo, col] = await Promise.all([
    fetch(RUTA_CONFIG + 'georreferencia.json', { cache: 'no-cache' }).then((r) => r.json()),
    fetch(RUTA_CONFIG + 'colores.json', { cache: 'no-cache' }).then((r) => r.json()),
  ]);
  PRESETS = limpio(geo.presets);
  ORIENTACIONES = limpio(geo.orientaciones);
  COLORES = limpio(col.colores);
}

// Zonas UTM que cubren México; cualquier otro EPSG se intenta obtener de epsg.io.
export const PROJ4_DEFS = {};
for (let zona = 11; zona <= 16; zona++) {
  PROJ4_DEFS[32600 + zona] = `+proj=utm +zone=${zona} +datum=WGS84 +units=m +no_defs`;
  PROJ4_DEFS[6366 + (zona - 11)] = `+proj=utm +zone=${zona} +ellps=GRS80 +towgs84=0,0,0,0,0,0,0 +units=m +no_defs`;
}

// ================== Modos ==================
// Mismos parámetros que escritorio/detector_libre.py y escritorio/detector_juego.py.
// En modo libre los rangos de cada color vienen de config/colores.json.
export const MODOS = {
  libre: {
    nombre: 'Mapeo libre',
    desc: 'Plastilina de colores → puntos, líneas y polígonos',
    preset: 'guerrero_costa_chica',
    orientacion: 'auto',
    cruces: { blur: 3, cerrar: false, areaMin: 20, arMin: 0.3, arMax: 3.5 },
    params: {
      BLUE_H_LOW: 100, BLUE_H_HIGH: 130, BLUE_S_LOW: 80, BLUE_S_HIGH: 255, BLUE_V_LOW: 80, BLUE_V_HIGH: 255,
      MIN_AREA_POINT: 5, MIN_AREA_LINE: 30, MIN_LINE_LENGTH: 20, MIN_LINE_ASPECT: 4.0, MIN_AREA_POLY: 150,
      K_LONG: 11, K_SHORT: 3, MORPH_ITERS: 1,
    },
  },
  juego: {
    nombre: 'Juego',
    desc: 'Plastilina verde → puntos; los participantes buscan los puntos de control',
    preset: 'cuenca_valle_mexico',
    orientacion: 'auto',
    cruces: { blur: 5, cerrar: true, areaMin: 15, arMin: 0.2, arMax: 5.0 },
    params: {
      GREEN_H_LOW: 35, GREEN_H_HIGH: 85, GREEN_S_LOW: 50, GREEN_S_HIGH: 255, GREEN_V_LOW: 50, GREEN_V_HIGH: 255,
      BLUE_H_LOW: 85, BLUE_H_HIGH: 135, BLUE_S_LOW: 50, BLUE_S_HIGH: 255, BLUE_V_LOW: 80, BLUE_V_HIGH: 255,
      MIN_AREA: 8, MAX_AREA: 8000,
    },
  },
};

const hsv = (pref) => [
  [`${pref}_H_LOW`, 'H bajo', 0, 179, 1], [`${pref}_H_HIGH`, 'H alto', 0, 179, 1],
  [`${pref}_S_LOW`, 'S bajo', 0, 255, 1], [`${pref}_S_HIGH`, 'S alto', 0, 255, 1],
  [`${pref}_V_LOW`, 'V bajo', 0, 255, 1], [`${pref}_V_HIGH`, 'V alto', 0, 255, 1],
];
// Los colores de plastilina del modo libre tienen su propio editor (ver web/movil)
export const SLIDERS = {
  libre: [
    ['Cruces y flecha (azul)', hsv('BLUE')],
    ['Puntos', [['MIN_AREA_POINT', 'Área mínima', 1, 200, 1]]],
    ['Líneas', [
      ['MIN_AREA_LINE', 'Área mínima', 5, 1000, 5],
      ['MIN_LINE_LENGTH', 'Largo mínimo', 5, 200, 1],
      ['MIN_LINE_ASPECT', 'Aspecto L/A', 1, 12, 0.5],
    ]],
    ['Polígonos', [['MIN_AREA_POLY', 'Área mínima', 50, 5000, 10]]],
    ['Morfología', [
      ['K_LONG', 'Kernel largo', 3, 51, 2],
      ['K_SHORT', 'Kernel corto', 3, 15, 2],
      ['MORPH_ITERS', 'Iteraciones', 0, 5, 1],
    ]],
  ],
  juego: [
    ['Verde (piezas)', hsv('GREEN')],
    ['Cruces y flecha (azul)', hsv('BLUE')],
    ['Tamaño de la pieza', [
      ['MIN_AREA', 'Área mínima', 1, 500, 1],
      ['MAX_AREA', 'Área máxima', 100, 20000, 100],
    ]],
  ],
};

export const RESOLUCIONES = [480, 640, 800, 960, 1280];
export const DEFAULT_RESOLUCION = 960;
export const DEFAULT_FPS = 10;

// Paleta para colorear lo mapeado: la de la plastilina + acentos de la interfaz
export const PALETA = ['#FF0000', '#FFA500', '#FFFF00', '#008000', '#800080', '#8B4513', '#5865f2', '#ec48bd', '#00b0f4', '#FFFFFF', '#000000'];

// Nombres antiguos (versiones anteriores exportaban 'yellow' / 'green')
const ALIAS = { yellow: 'amarillo', green: 'verde' };

export function colorPlastilina(id) {
  const c = COLORES[ALIAS[id] || id];
  return c ? c.hex : (id && id.startsWith('#') ? id : '#5865f2');
}
// En femenino, porque se usa como "plastilina roja", "plastilina amarilla"…
const FEMENINO = { rojo: 'roja', amarillo: 'amarilla', morado: 'morada', blanco: 'blanca', negro: 'negra' };
export function nombreColor(id) {
  const k = ALIAS[id] || id;
  const c = COLORES[k];
  return FEMENINO[k] || (c ? c.nombre.toLowerCase() : (id || ''));
}
