// Mapa de la versión laptop: lee en tiempo real lo que escriben los detectores de Python
// (escritorio/detector_libre.py, escritorio/detector_juego.py) en salidas/.
import { cargarConfigGeo, PRESETS, MODOS } from '../../comun/js/config.js';
import { registrarEPSG, esquinasLatLng } from '../../comun/js/geo.js';
import { Mapa } from '../../comun/js/mapa.js';
import { ContextoOSM } from '../../comun/js/osm.js';
import { Juego } from '../../comun/js/juego.js';
import { panelMapeo, panelOSM, formularioArea } from '../../comun/js/paneles.js';
import { icono } from '../../comun/js/iconos.js';
import { $, avisar, almacen, pestanas } from '../../comun/js/ui.js';

const SALIDAS = new URL('../../../salidas/', import.meta.url).href;
const ARCHIVOS = ['detecciones_puntos.geojson', 'detecciones_lineas.geojson', 'detecciones_poligonos.geojson'];
const CLAVE = 'mesa3d-mapa-ajustes-v1';

await cargarConfigGeo();

const A = almacen.leer(CLAVE, null) || {
  modo: 'libre',
  auto: true,
  area: { preset: MODOS.libre.preset, epsg: PRESETS[MODOS.libre.preset].epsg, bounds: PRESETS[MODOS.libre.preset].bounds },
};
const guardar = () => almacen.guardar(CLAVE, A);

const mapa = new Mapa('mapa');
const osm = new ContextoOSM(mapa.map);
mapa.osm = osm;
const juego = new Juego(mapa, osm, () => mapa.vivo);

// ================== Paneles ==================
$('icoOSM').innerHTML = icono('globo');
$('btnPanel').innerHTML = icono('panel');
$('btnPanel').onclick = () => document.body.classList.toggle('panel-abierto');
const activarPestana = pestanas($('lateral'), () => setTimeout(() => mapa.invalidar(), 50));
const pMapeo = panelMapeo($('tabMapeo'), mapa);
mapa.alCambiar = pMapeo.render;
mapa.alCambiarVivo = pMapeo.vivo;
panelOSM($('tabOSM'), osm, mapa);
juego.montar($('tabJuego'));
$('btnTerminarColocar').onclick = () => juego.terminarColocar();

const llenarArea = formularioArea($('formArea'), A.area, async (area) => {
  if (await aplicarArea(area)) { A.area = area; guardar(); avisar('Área aplicada.', 'ok'); }
});

async function aplicarArea(area) {
  if (!(await registrarEPSG(area.epsg))) {
    avisar(`No se reconoce EPSG:${area.epsg}.`, 'err');
    return false;
  }
  mapa.setEncuadre(esquinasLatLng(area.bounds, area.epsg), PRESETS[area.preset]?.nombre || `EPSG:${area.epsg}`);
  return true;
}

function setModo(modo) {
  A.modo = modo;
  guardar();
  document.querySelectorAll('#segModo button').forEach((b) => b.setAttribute('aria-pressed', String(b.dataset.modo === modo)));
  document.querySelector('[data-pestana="juego"]').hidden = modo !== 'juego';
  juego.setActivo(modo === 'juego');
  activarPestana(modo === 'juego' ? 'juego' : 'mapeo');
}
document.querySelectorAll('#segModo button').forEach((b) => { b.onclick = () => setModo(b.dataset.modo); });

// ================== Lectura de salidas/ ==================
async function leerJSON(nombre) {
  try {
    const r = await fetch(SALIDAS + nombre, { cache: 'no-store' });
    if (!r.ok) return { ok: false };
    const t = await r.text();
    return { ok: true, json: t.trim() ? JSON.parse(t) : null, fecha: Date.parse(r.headers.get('Last-Modified')) || 0 };
  } catch (_) {
    return { ok: false }; // archivo a medio escribir por el detector: se reintenta en la siguiente vuelta
  }
}

let ultimaSesion = '';
async function revisarSesion() {
  const r = await leerJSON('sesion.json');
  if (!r.ok || !r.json) return;
  const firma = JSON.stringify(r.json);
  if (firma === ultimaSesion) return;
  const primera = !ultimaSesion;
  ultimaSesion = firma;
  const s = r.json;
  const preset = Object.entries(PRESETS).find(([, p]) => p.epsg === s.epsg && p.bounds.every((v, i) => Math.abs(v - s.bounds[i]) < 1e-6))?.[0] || 'personalizado';
  A.area = { preset, epsg: s.epsg, bounds: s.bounds };
  llenarArea(A.area);
  await aplicarArea(A.area);
  if (s.modo && s.modo !== A.modo) setModo(s.modo);
  guardar();
  if (!primera || s.modo) avisar(`Sincronizado con el detector (${MODOS[s.modo]?.nombre || s.modo}).`, 'ok');
}

async function leerDetecciones() {
  const rs = await Promise.all(ARCHIVOS.map(leerJSON));
  const features = rs.flatMap((r) => (r.ok && r.json?.features) || []).filter((f) => f.geometry);
  mapa.setDetecciones({ type: 'FeatureCollection', features });
  const fecha = Math.max(0, ...rs.map((r) => r.fecha || 0));
  const hayArchivos = rs.some((r) => r.ok);
  const el = $('estadoDetector');
  const reciente = fecha && Date.now() - fecha < 15000;
  el.classList.toggle('vivo', !!reciente);
  el.querySelector('span').textContent = !hayArchivos
    ? 'Sin datos del detector todavía'
    : reciente
      ? `Detector activo · ${features.length} ${features.length === 1 ? 'pieza' : 'piezas'}`
      : `Última lectura ${fecha ? new Date(fecha).toLocaleTimeString('es-MX', { hour: '2-digit', minute: '2-digit' }) : ''} · ${features.length} piezas`;
}

$('chkAuto').checked = A.auto;
$('chkAuto').onchange = (e) => { A.auto = e.target.checked; guardar(); };
$('btnActualizar').onclick = () => { leerDetecciones(); revisarSesion(); };

// ================== Inicio ==================
await aplicarArea(A.area);
setModo(A.modo);
await revisarSesion();
await leerDetecciones();
setInterval(() => { if (A.auto) leerDetecciones(); }, 1500);
setInterval(revisarSesion, 4000);
if (window.matchMedia('(min-width: 821px)').matches) document.body.classList.add('panel-abierto');

window.mesa3d = { mapa, osm, juego, A };
