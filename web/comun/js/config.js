// Configuración compartida por el mapa (web/mapa) y la app del celular (web/movil).
// Presets geográficos y orientaciones se leen de config/georreferencia.json, el mismo
// archivo que usan los detectores de la versión laptop (escritorio/).

export const RUTA_CONFIG_GEO = new URL('../../../config/georreferencia.json', import.meta.url).href;

export let PRESETS = {};
export let ORIENTACIONES = {};

export async function cargarConfigGeo() {
  const r = await fetch(RUTA_CONFIG_GEO, { cache: 'no-cache' });
  const cfg = await r.json();
  const limpio = (o) => Object.fromEntries(Object.entries(o).filter(([k]) => !k.startsWith('_')));
  PRESETS = limpio(cfg.presets);
  ORIENTACIONES = limpio(cfg.orientaciones);
}

// Zonas UTM que cubren México; cualquier otro EPSG se intenta obtener de epsg.io.
export const PROJ4_DEFS = {};
for (let zona = 11; zona <= 16; zona++) {
  PROJ4_DEFS[32600 + zona] = `+proj=utm +zone=${zona} +datum=WGS84 +units=m +no_defs`;
  PROJ4_DEFS[6366 + (zona - 11)] = `+proj=utm +zone=${zona} +ellps=GRS80 +towgs84=0,0,0,0,0,0,0 +units=m +no_defs`;
}

// ================== Modos ==================
// Mismos parámetros que escritorio/detector_libre.py y escritorio/detector_juego.py
export const MODOS = {
  libre: {
    nombre: 'Mapeo libre',
    desc: 'Plastilina amarilla y verde → puntos, líneas y polígonos',
    preset: 'guerrero_costa_chica',
    orientacion: 'norte_arriba',
    cruces: { blur: 3, cerrar: false, areaMin: 20, arMin: 0.3, arMax: 3.5 },
    params: {
      YELLOW_H_LOW: 15, YELLOW_H_HIGH: 30, YELLOW_S_LOW: 105, YELLOW_S_HIGH: 255, YELLOW_V_LOW: 100, YELLOW_V_HIGH: 255,
      GREEN_H_LOW: 35, GREEN_H_HIGH: 80, GREEN_S_LOW: 45, GREEN_S_HIGH: 255, GREEN_V_LOW: 40, GREEN_V_HIGH: 255,
      BLUE_H_LOW: 100, BLUE_H_HIGH: 130, BLUE_S_LOW: 80, BLUE_S_HIGH: 255, BLUE_V_LOW: 80, BLUE_V_HIGH: 255,
      MIN_AREA_POINT: 5, MIN_AREA_LINE: 30, MIN_LINE_LENGTH: 20, MIN_LINE_ASPECT: 4.0, MIN_AREA_POLY: 150,
      K_LONG: 11, K_SHORT: 3, MORPH_ITERS: 1,
    },
  },
  juego: {
    nombre: 'Juego',
    desc: 'Plastilina verde → puntos; los participantes buscan los puntos de control',
    preset: 'cuenca_valle_mexico',
    orientacion: 'norte_arriba',
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
export const SLIDERS = {
  libre: [
    ['Amarillo', hsv('YELLOW')],
    ['Verde', hsv('GREEN')],
    ['Cruces azules', hsv('BLUE')],
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
    ['Verde', hsv('GREEN')],
    ['Cruces azules', hsv('BLUE')],
    ['Tamaño de la pieza', [
      ['MIN_AREA', 'Área mínima', 1, 500, 1],
      ['MAX_AREA', 'Área máxima', 100, 20000, 100],
    ]],
  ],
};

export const RESOLUCIONES = [480, 640, 800, 960, 1280];
export const DEFAULT_RESOLUCION = 960;
export const DEFAULT_FPS = 10;

// Paleta para colorear lo mapeado (la plastilina primero)
export const PALETA = ['#e0a800', '#2e8b57', '#2458c6', '#c4532d', '#7b4bb7', '#0f8b8d', '#d6336c', '#1b2330', '#8a8f98'];
export const COLOR_PLASTILINA = { yellow: '#e0a800', green: '#2e8b57' };
