// App del celular: cámara → OpenCV.js → calibración con cruces azules → georreferencia → mapa.
// Mismo flujo que los detectores de la versión laptop, más el mapa compartido (web/comun).
import { cargarConfigGeo, PRESETS, MODOS, SLIDERS, RESOLUCIONES, DEFAULT_RESOLUCION, DEFAULT_FPS } from '../../comun/js/config.js';
import { calibrar, registrarEPSG, transformador, esquinasLatLng } from '../../comun/js/geo.js';
import { Mapa } from '../../comun/js/mapa.js';
import { ContextoOSM } from '../../comun/js/osm.js';
import { Juego } from '../../comun/js/juego.js';
import { panelMapeo, panelOSM, formularioArea } from '../../comun/js/paneles.js';
import { icono } from '../../comun/js/iconos.js';
import { $, avisar, almacen, pestanas, elegirArchivo } from '../../comun/js/ui.js';
import { buscarCruces, detectarMesa, detectarJuego, aGeoJSON } from './deteccion.js';

// Orden de carga: copia local opcional (web/movil/vendor/opencv.js) → CDN npm → sitio oficial de OpenCV
const FUENTES_OPENCV = [
  './vendor/opencv.js',
  'https://cdn.jsdelivr.net/npm/@techstark/opencv-js@4.10.0-release.1/dist/opencv.js',
  'https://docs.opencv.org/4.9.0/opencv.js',
];
const CLAVE = 'mesa3d-movil-ajustes-v2';

await cargarConfigGeo();

// ================== Ajustes ==================
function porDefecto() {
  const porModo = {};
  for (const [id, m] of Object.entries(MODOS)) {
    const pr = PRESETS[m.preset];
    porModo[id] = { preset: m.preset, epsg: pr.epsg, bounds: [...pr.bounds], orientacion: m.orientacion, recalibCada: 0, params: { ...m.params } };
  }
  return { modo: 'libre', resolucion: DEFAULT_RESOLUCION, fps: DEFAULT_FPS, vista: 'deteccion', camaraId: '', porModo };
}
const A = (() => {
  const def = porDefecto();
  const g = almacen.leer(CLAVE, null);
  if (!g) return def;
  for (const id of Object.keys(def.porModo)) {
    const gm = g.porModo?.[id] || {};
    def.porModo[id] = { ...def.porModo[id], ...gm, params: { ...def.porModo[id].params, ...(gm.params || {}) } };
  }
  return { ...def, ...g, porModo: def.porModo };
})();
const guardar = () => almacen.guardar(CLAVE, A);
const cfg = () => A.porModo[A.modo];

// ================== Estado de la cámara ==================
let cv = null;
let stream = null;
let corriendo = false;
let pausado = false;
let pedirCalibracion = false;
let georef = null;
let crop = null;
let tamProc = [0, 0];
let numFrame = 0;
let ultimoProceso = 0;
let ultimoMapa = 0;
let geojsonActual = { type: 'FeatureCollection', features: [] };
let wakeLock = null;

const video = $('video');
const lienzo = $('lienzo');
const ctx = lienzo.getContext('2d', { willReadFrequently: true });

// ================== Mapa, OSM, juego y paneles ==================
const mapa = new Mapa('mapa');
const osm = new ContextoOSM(mapa.map);
mapa.osm = osm;
const juego = new Juego(mapa, osm, () => geojsonActual);

const pMapeo = panelMapeo($('tabMapeo'), mapa);
mapa.alCambiar = pMapeo.render;
mapa.alCambiarVivo = pMapeo.vivo;
panelOSM($('tabOSM'), osm, mapa);
juego.montar($('tabJuego'));
const activarPestana = pestanas($('hoja'));

// Iconos de la interfaz
$('icoOSM').innerHTML = icono('globo');
$('btnCerrarHoja').innerHTML = icono('cerrar');
$('btnCamara').innerHTML = `${icono('camara')} Usar la cámara`;
$('btnVideo').innerHTML = `${icono('video')} Probar con un video`;
const etiqueta = (ico, txt) => `${icono(ico)}<span>${txt}</span>`;
$('btnCalibrar').innerHTML = etiqueta('mira', 'Calibrar');
$('btnPausa').innerHTML = etiqueta('pausa', 'Pausa');
$('btnVista').innerHTML = etiqueta('vista', 'Vista');
$('btnGuardar').innerHTML = etiqueta('guardar', 'Guardar');
$('btnPanel').innerHTML = etiqueta('panel', 'Panel');

// ================== OpenCV.js ==================
function cargarScript(src) {
  return new Promise((ok, fallo) => {
    const s = document.createElement('script');
    s.src = src;
    s.async = true;
    s.onload = () => ok();
    s.onerror = () => { s.remove(); fallo(new Error(src)); };
    document.head.appendChild(s);
  });
}

function esperarRuntime(timeoutMs = 60000) {
  return new Promise((ok, fallo) => {
    const t0 = performance.now();
    const g = window.cv;
    // Algunas compilaciones exponen `cv` como promesa o Module "thenable": no se debe
    // resolver una promesa con él (bucle infinito), solo leerlo cuando esté listo.
    if (g && typeof g.then === 'function' && !g.Mat) {
      g.then((m) => { if (m && m.Mat) { delete m.then; window.cv = m; } });
    }
    const revisar = () => {
      if (window.cv && window.cv.Mat) return ok(window.cv);
      if (performance.now() - t0 > timeoutMs) return fallo(new Error('tiempo agotado'));
      setTimeout(revisar, 100);
    };
    revisar();
  });
}

async function cargarOpenCV() {
  for (const src of FUENTES_OPENCV) {
    try {
      await cargarScript(src);
      cv = await esperarRuntime();
      return true;
    } catch (_) { /* siguiente fuente */ }
  }
  return false;
}

// ================== Georreferencia ==================
async function aplicarArea() {
  const c = cfg();
  if (!(await registrarEPSG(c.epsg))) {
    avisar(`No se reconoce EPSG:${c.epsg}. Revisa el código o conéctate a internet una vez.`, 'err');
    return false;
  }
  mapa.setEncuadre(esquinasLatLng(c.bounds, c.epsg), PRESETS[c.preset]?.nombre || `EPSG:${c.epsg}`);
  resetCalibracion();
  return true;
}

function resetCalibracion() {
  georef = null;
  crop = null;
  geojsonActual = { type: 'FeatureCollection', features: [] };
  mapa.setDetecciones(geojsonActual);
}

function calibrarCon(corners) {
  const c = cfg();
  const aMetros = calibrar(corners, c.bounds, c.orientacion);
  if (!aMetros) return false;
  const aLonLat = transformador(c.epsg);
  georef = (x, y) => { const [mx, my] = aMetros(x, y); return aLonLat(mx, my); };
  return true;
}

// ================== Fuente de video ==================
async function iniciarCamara() {
  if (!window.isSecureContext || !navigator.mediaDevices?.getUserMedia) {
    $('avisoSeguro').hidden = false;
    return avisar('La cámara necesita https:// o localhost.', 'err');
  }
  detenerFuente();
  const v = A.camaraId ? { deviceId: { exact: A.camaraId } } : { facingMode: { ideal: 'environment' } };
  try {
    stream = await navigator.mediaDevices.getUserMedia({ audio: false, video: { ...v, width: { ideal: 1920 }, height: { ideal: 1080 } } });
  } catch (e) {
    return avisar(`No se pudo abrir la cámara: ${e.message}`, 'err');
  }
  video.srcObject = stream;
  video.loop = false;
  await arrancar();
  listarCamaras();
}

async function iniciarArchivo(file) {
  if (!cv) return avisar('Espera a que termine de cargar OpenCV.', 'err');
  detenerFuente();
  video.srcObject = null;
  video.src = URL.createObjectURL(file);
  video.loop = true;
  await arrancar();
}

async function arrancar() {
  try { await video.play(); } catch (_) { /* silenciado */ }
  $('inicio').hidden = true;
  document.body.classList.remove('sin-camara');
  setTimeout(() => mapa.invalidar(), 60);
  $('estadoCamara').hidden = false;
  $('btnCalibrar').disabled = false;
  $('btnPausa').disabled = false;
  corriendo = true;
  numFrame = 0;
  pedirWakeLock();
  requestAnimationFrame(bucle);
}

function detenerFuente() {
  corriendo = false;
  if (stream) { stream.getTracks().forEach((t) => t.stop()); stream = null; }
  if (video.src) { URL.revokeObjectURL(video.src); video.removeAttribute('src'); video.load(); }
  resetCalibracion();
  tamProc = [0, 0];
  $('inicio').hidden = false;
  document.body.classList.add('sin-camara');
  $('estadoCamara').hidden = true;
  $('btnCalibrar').disabled = true;
  $('btnPausa').disabled = true;
  if (wakeLock) { wakeLock.release().catch(() => {}); wakeLock = null; }
}

async function listarCamaras() {
  try {
    const devs = (await navigator.mediaDevices.enumerateDevices()).filter((d) => d.kind === 'videoinput');
    const sel = $('selCamara');
    sel.innerHTML = '<option value="">Trasera (automática)</option>' + devs.map((d, i) => `<option value="${d.deviceId}">${d.label || `Cámara ${i + 1}`}</option>`).join('');
    sel.value = devs.some((d) => d.deviceId === A.camaraId) ? A.camaraId : '';
  } catch (_) { /* sin permiso aún */ }
}

async function pedirWakeLock() {
  try { if ('wakeLock' in navigator) wakeLock = await navigator.wakeLock.request('screen'); } catch (_) { /* no soportado */ }
}
document.addEventListener('visibilitychange', () => { if (document.visibilityState === 'visible' && corriendo) pedirWakeLock(); });

// ================== Bucle de procesamiento ==================
function bucle(t) {
  if (!corriendo) return;
  if (t - ultimoProceso >= 1000 / A.fps) {
    ultimoProceso = t;
    try { procesar(); } catch (e) { console.error(e); }
  }
  requestAnimationFrame(bucle);
}

function procesar() {
  if (!cv || video.readyState < 2 || !video.videoWidth) return;
  const vw = video.videoWidth, vh = video.videoHeight;
  const esc = Math.min(1, A.resolucion / Math.max(vw, vh));
  const W = Math.round(vw * esc), H = Math.round(vh * esc);
  if (W !== tamProc[0] || H !== tamProc[1]) {
    tamProc = [W, H];
    lienzo.width = W; lienzo.height = H;
    georef = null; crop = null;
  }

  ctx.drawImage(video, 0, 0, W, H);
  const rgba = cv.matFromImageData(ctx.getImageData(0, 0, W, H));
  const rgb = new cv.Mat();
  cv.cvtColor(rgba, rgb, cv.COLOR_RGBA2RGB);
  rgba.delete();

  const c = cfg();
  const p = c.params;
  numFrame++;

  // Las cruces se buscan siempre (retroalimentación); la homografía solo cambia al calibrar
  const cruces = buscarCruces(cv, rgb, p, MODOS[A.modo].cruces, A.vista === 'blue');
  if ((c.recalibCada > 0 && numFrame % c.recalibCada === 0) || pedirCalibracion) {
    const manual = pedirCalibracion;
    pedirCalibracion = false;
    if (cruces.corners && calibrarCon(cruces.corners)) {
      crop = cruces.crop;
      if (manual) avisar('Calibrado: las 4 cruces se reconocieron.', 'ok');
    } else if (manual) {
      avisar(`Se ven ${cruces.centroides.length} de 4 cruces azules. Revisa la luz o el encuadre.`, 'err');
    }
  }
  const calibrado = !!(georef && crop);

  if (pausado) {
    dibujarEstado({ cruces, calibrado, pausa: true });
    if (cruces.mascara) cruces.mascara.delete();
    rgb.delete();
    return;
  }

  let sub = rgb, xoff = 0, yoff = 0;
  if (calibrado) {
    const [x0, y0, x1, y1] = crop;
    sub = rgb.roi(new cv.Rect(x0, y0, x1 - x0, y1 - y0));
    xoff = x0; yoff = y0;
  }
  const g = calibrado ? georef : null;
  const vistaColor = A.vista === 'yellow' || A.vista === 'green' ? A.vista : null;
  const r = A.modo === 'libre'
    ? detectarMesa(cv, sub, p, g, xoff, yoff, vistaColor)
    : detectarJuego(cv, sub, p, g, xoff, yoff, vistaColor === 'green');
  if (sub !== rgb) sub.delete();
  rgb.delete();

  const mascara = A.vista === 'blue' ? cruces.mascara : r.mascara;
  if (mascara) pintarMascara(mascara, A.vista === 'blue' ? 0 : xoff, A.vista === 'blue' ? 0 : yoff, W, H);
  if (cruces.mascara) cruces.mascara.delete();
  if (r.mascara) r.mascara.delete();

  dibujarDetecciones(r.dibujo);
  dibujarEstado({ cruces, calibrado, stats: r.stats });

  if (calibrado) {
    geojsonActual = aGeoJSON(r.detecciones);
    const ahora = performance.now();
    if (ahora - ultimoMapa > 700) { ultimoMapa = ahora; mapa.setDetecciones(geojsonActual); }
  }
}

function pintarMascara(mask, xoff, yoff, W, H) {
  ctx.fillStyle = '#000';
  ctx.fillRect(0, 0, W, H);
  const im = new ImageData(mask.cols, mask.rows);
  const d = mask.data;
  for (let i = 0, j = 0; i < d.length; i++, j += 4) {
    im.data[j] = im.data[j + 1] = im.data[j + 2] = d[i];
    im.data[j + 3] = 255;
  }
  ctx.putImageData(im, xoff, yoff);
}

// ================== Dibujo sobre el video ==================
const COL = { yellow: '#ffd21f', green: '#3ee07f' };

function trazo(pts, cerrar) {
  if (!pts || !pts.length) return;
  ctx.beginPath();
  ctx.moveTo(pts[0][0], pts[0][1]);
  for (let i = 1; i < pts.length; i++) ctx.lineTo(pts[i][0], pts[i][1]);
  if (cerrar) ctx.closePath();
  ctx.stroke();
}

function dibujarDetecciones(dibujo) {
  const lw = Math.max(2, lienzo.width / 420);
  for (const d of dibujo) {
    const c = COL[d.color];
    ctx.lineWidth = lw;
    ctx.strokeStyle = c;
    if (d.tipo === 'line') trazo(d.caja, true);
    else if (d.tipo === 'polygon') { ctx.setLineDash([lw * 2, lw * 1.5]); trazo(d.pts, true); ctx.setLineDash([]); }
    else {
      const [x, y] = d.centro;
      ctx.beginPath(); ctx.arc(x, y, lw * 3.5, 0, Math.PI * 2); ctx.stroke();
      ctx.fillStyle = c; ctx.beginPath(); ctx.arc(x, y, lw, 0, Math.PI * 2); ctx.fill();
      if (d.n) { ctx.fillStyle = '#fff'; ctx.font = `600 ${Math.round(lw * 5)}px "JetBrains Mono", monospace`; ctx.fillText(String(d.n), x + lw * 4.5, y - lw * 2); }
    }
  }
}

function dibujarEstado({ cruces, calibrado, stats, pausa }) {
  const W = lienzo.width, H = lienzo.height;
  const lw = Math.max(2, W / 420);
  if (calibrado) {
    const [x0, y0, x1, y1] = crop;
    ctx.fillStyle = 'rgba(17,22,30,0.55)';
    ctx.fillRect(0, 0, W, y0); ctx.fillRect(0, y1, W, H - y1);
    ctx.fillRect(0, y0, x0, y1 - y0); ctx.fillRect(x1, y0, W - x1, y1 - y0);
  }
  const m = lw * 5;
  ctx.lineWidth = lw * 1.2;
  ctx.strokeStyle = '#6ea8ff';
  for (const [x, y] of cruces.centroides) {
    ctx.beginPath(); ctx.moveTo(x - m, y); ctx.lineTo(x + m, y); ctx.moveTo(x, y - m); ctx.lineTo(x, y + m); ctx.stroke();
  }
  if (cruces.corners) { ctx.lineWidth = 1; ctx.strokeStyle = 'rgba(110,168,255,0.8)'; trazo(cruces.corners, true); }
  if (pausa) {
    ctx.fillStyle = 'rgba(17,22,30,0.6)'; ctx.fillRect(0, 0, W, H);
    ctx.fillStyle = '#fff'; ctx.font = `600 ${Math.round(W / 18)}px Fraunces, Georgia, serif`;
    ctx.textAlign = 'center'; ctx.fillText('En pausa', W / 2, H / 2); ctx.textAlign = 'start';
  }

  const n = cruces.centroides.length;
  const pildoras = [calibrado
    ? '<span class="pildora calibrado"><i></i>Calibrado</span>'
    : `<span class="pildora" style="--c:${n >= 4 ? '#6ea8ff' : '#f3b58f'}"><i></i>Cruces ${Math.min(n, 4)}/4${n > 4 ? ` (+${n - 4})` : ''}</span>`];
  if (stats) {
    if (A.modo === 'libre') {
      const f = (s) => `${s.point}·${s.line}·${s.polygon}`;
      pildoras.push(`<span class="pildora" style="--c:${COL.yellow}"><i></i>${f(stats.yellow)}</span>`);
      pildoras.push(`<span class="pildora" style="--c:${COL.green}"><i></i>${f(stats.green)}</span>`);
    } else {
      pildoras.push(`<span class="pildora" style="--c:${COL.green}"><i></i>${stats.green.point} piezas</span>`);
    }
  }
  const html = pildoras.join('');
  const el = $('estadoCamara');
  if (el.innerHTML !== html) el.innerHTML = html;
  $('btnCalibrar').classList.toggle('lista', !!cruces.corners && !calibrado);
}

// ================== Panel "Cámara" ==================
const llenarArea = formularioArea($('formArea'), cfg(), async (area) => {
  Object.assign(cfg(), area);
  guardar();
  if (await aplicarArea()) avisar('Área aplicada. Vuelve a calibrar con las cruces.', 'ok');
}, { conOrientacion: true });

function pintarAjustes() {
  const c = cfg();
  llenarArea(c);
  $('selResolucion').innerHTML = RESOLUCIONES.map((r) => `<option value="${r}">${r} px</option>`).join('');
  $('selResolucion').value = A.resolucion;
  $('inpFps').value = A.fps;
  $('inpRecalib').value = c.recalibCada;
  $('selVistaCam').querySelector('option[value="yellow"]').hidden = A.modo !== 'libre';
  if (A.modo !== 'libre' && A.vista === 'yellow') A.vista = 'deteccion';
  $('selVistaCam').value = A.vista;
  pintarSliders();
}

function pintarSliders() {
  const cont = $('sliders');
  const p = cfg().params;
  cont.innerHTML = '';
  for (const [titulo, lista] of SLIDERS[A.modo]) {
    const grupo = document.createElement('div');
    grupo.className = 'grupo-sliders';
    grupo.innerHTML = `<h4>${titulo}</h4>`;
    for (const [clave, texto, min, max, paso] of lista) {
      const fila = document.createElement('label');
      fila.className = 'slider';
      fila.innerHTML = `<span>${texto}</span><input type="range" min="${min}" max="${max}" step="${paso}" value="${p[clave]}"><output>${p[clave]}</output>`;
      const inp = fila.querySelector('input'), out = fila.querySelector('output');
      inp.oninput = () => { p[clave] = Number(inp.value); out.textContent = inp.value; guardar(); };
      grupo.appendChild(fila);
    }
    cont.appendChild(grupo);
  }
}

async function setModo(modo) {
  A.modo = modo;
  guardar();
  document.querySelectorAll('#segModo button').forEach((b) => b.setAttribute('aria-pressed', String(b.dataset.modo === modo)));
  document.querySelector('#hoja [data-pestana="juego"]').hidden = modo !== 'juego';
  juego.setActivo(modo === 'juego');
  const actual = document.querySelector('#hoja [aria-selected="true"]')?.dataset.pestana;
  if (modo === 'juego') activarPestana('juego');
  else if (!actual || actual === 'juego') activarPestana('camara');
  pintarAjustes();
  await aplicarArea();
}

// ================== Eventos ==================
const abrirHoja = (pestana) => { if (pestana) activarPestana(pestana); $('hoja').classList.add('abierta'); };
const cerrarHoja = () => { $('hoja').classList.remove('abierta'); };

$('btnCamara').onclick = () => iniciarCamara();
$('btnVideo').onclick = async () => { const f = await elegirArchivo('video/*'); if (f) iniciarArchivo(f); };
$('btnDetener').onclick = () => { detenerFuente(); cerrarHoja(); };
$('btnCalibrar').onclick = () => { pedirCalibracion = true; };
$('btnPausa').onclick = () => {
  pausado = !pausado;
  $('btnPausa').classList.toggle('activa', pausado);
  $('btnPausa').innerHTML = pausado ? etiqueta('play', 'Seguir') : etiqueta('pausa', 'Pausa');
  if (video.src && !video.srcObject) { if (pausado) video.pause(); else video.play(); }
};
$('btnVista').onclick = () => {
  const orden = ['dividida', 'camara', 'mapa'];
  const pan = $('paneles');
  pan.dataset.vista = orden[(orden.indexOf(pan.dataset.vista) + 1) % orden.length];
  setTimeout(() => mapa.invalidar(), 60);
};
$('btnGuardar').onclick = () => mapa.guardarTodasLasDetecciones();
$('btnPanel').onclick = () => ($('hoja').classList.contains('abierta') ? cerrarHoja() : abrirHoja());
$('btnCerrarHoja').onclick = cerrarHoja;
$('btnTerminarColocar').onclick = () => juego.terminarColocar();
// Al empezar a colocar puntos de control se cierra la hoja para dejar ver el mapa
new MutationObserver(() => { if (!$('bannerColocar').hidden) { cerrarHoja(); $('paneles').dataset.vista = 'mapa'; setTimeout(() => mapa.invalidar(), 60); } })
  .observe($('bannerColocar'), { attributes: true, attributeFilter: ['hidden'] });

document.querySelectorAll('#segModo button').forEach((b) => { b.onclick = () => setModo(b.dataset.modo); });
$('selCamara').onchange = (e) => { A.camaraId = e.target.value; guardar(); if (stream) iniciarCamara(); };
$('selResolucion').onchange = (e) => { A.resolucion = Number(e.target.value); guardar(); };
$('inpFps').onchange = (e) => { A.fps = Math.min(30, Math.max(1, Number(e.target.value) || DEFAULT_FPS)); guardar(); };
$('inpRecalib').onchange = (e) => { cfg().recalibCada = Math.max(0, parseInt(e.target.value, 10) || 0); guardar(); };
$('selVistaCam').onchange = (e) => { A.vista = e.target.value; guardar(); };
$('btnRestablecer').onclick = () => { cfg().params = { ...MODOS[A.modo].params }; guardar(); pintarSliders(); };

new ResizeObserver(() => mapa.invalidar()).observe($('mapa'));

// ================== Inicio ==================
await setModo(A.modo);
if (!window.isSecureContext) $('avisoSeguro').hidden = false;

if (await cargarOpenCV()) {
  $('estadoOpenCV').textContent = 'Listo.';
  $('btnCamara').disabled = false;
} else {
  $('estadoOpenCV').textContent = 'No se pudo cargar OpenCV. Revisa la conexión a internet y recarga la página.';
}

if ('serviceWorker' in navigator && window.isSecureContext) navigator.serviceWorker.register('./sw.js').catch(() => {});

// Acceso para pruebas automáticas y depuración
window.mesa3d = { A, mapa, osm, juego, get geojson() { return geojsonActual; }, iniciarArchivo };
