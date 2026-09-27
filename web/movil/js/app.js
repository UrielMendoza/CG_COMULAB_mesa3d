// App del celular: cámara → OpenCV.js → calibración con cruces (y flecha de norte) azules →
// georreferencia → mapa. Mismo flujo que los detectores de la versión laptop, más el mapa
// compartido (web/comun).
import { cargarConfigGeo, PRESETS, MODOS, SLIDERS, COLORES, RESOLUCIONES, DEFAULT_RESOLUCION, DEFAULT_FPS } from '../../comun/js/config.js';
import { calibrar, resolverEsquinas, registrarEPSG, transformador, esquinasLatLng } from '../../comun/js/geo.js';
import { Mapa } from '../../comun/js/mapa.js';
import { ContextoOSM } from '../../comun/js/osm.js';
import { Juego } from '../../comun/js/juego.js';
import { panelMapeo, panelOSM, formularioArea } from '../../comun/js/paneles.js';
import { icono } from '../../comun/js/iconos.js';
import { $, esc, avisar, almacen, pestanas, elegirArchivo } from '../../comun/js/ui.js';
import { buscarCruces, detectarMesa, detectarJuego, aGeoJSON } from './deteccion.js';
import { montarSinConexion } from '../../comun/js/sinconexion.js';

// Orden de carga: copia local opcional (web/movil/vendor/opencv.js) → CDN npm → sitio oficial de OpenCV
const FUENTES_OPENCV = [
  './vendor/opencv.js',
  'https://cdn.jsdelivr.net/npm/@techstark/opencv-js@4.10.0-release.1/dist/opencv.js',
  'https://docs.opencv.org/4.9.0/opencv.js',
];
// v4: nuevos valores por defecto (Valle de México; solo verde, amarillo y rojo activos)
const CLAVE = 'mesa3d-movil-ajustes-v4';

await cargarConfigGeo();

// ================== Ajustes (se guardan solo en este dispositivo) ==================
const copiaColores = () => JSON.parse(JSON.stringify(COLORES));
function porDefecto() {
  const porModo = {};
  for (const [id, m] of Object.entries(MODOS)) {
    const pr = PRESETS[m.preset];
    porModo[id] = { preset: m.preset, epsg: pr.epsg, bounds: [...pr.bounds], orientacion: m.orientacion, recalibCada: 0, params: { ...m.params } };
  }
  porModo.libre.colores = copiaColores();
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
  // Colores nuevos agregados a config/colores.json aparecen aunque haya ajustes guardados
  def.porModo.libre.colores = { ...copiaColores(), ...(g.porModo?.libre?.colores || {}) };
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
let ultimas = null;          // { corners, flecha } de la última calibración (para recalcular al cambiar el área)
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

const pMapeo = panelMapeo($('tabMapeo'), mapa, juego);
mapa.alCambiar = pMapeo.render;
mapa.alCambiarVivo = pMapeo.vivo;
panelOSM($('tabOSM'), osm, mapa);
juego.montar($('tabJuego'));
// Descarga para usar sin internet: OpenCV se guarda con la misma dirección con que se carga
montarSinConexion($('sinConexion'), { osm, opencv: FUENTES_OPENCV[1] });
const activarPestana = pestanas($('hoja'));

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

// Descarga con progreso visible: en datos móviles OpenCV (~10 MB) tarda; si dejan de llegar
// datos durante 20 s se abandona esa fuente y se prueba la siguiente.
const esperar = (ms) => new Promise((r) => setTimeout(r, ms));

async function descargarConProgreso(url, alProgreso, estancadoMs = 20000) {
  const ctl = new AbortController();
  let reloj = setTimeout(() => ctl.abort(), estancadoMs);
  try {
    const r = await fetch(url, { signal: ctl.signal });
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    const lector = r.body.getReader();
    const partes = [];
    let n = 0;
    for (;;) {
      const { done, value } = await lector.read();
      if (done) break;
      partes.push(value);
      n += value.length;
      clearTimeout(reloj);
      reloj = setTimeout(() => ctl.abort(), estancadoMs);
      alProgreso(n);
    }
    return URL.createObjectURL(new Blob(partes, { type: 'text/javascript' }));
  } finally {
    clearTimeout(reloj);
  }
}

async function cargarOpenCV() {
  const estado = $('estadoOpenCV');
  for (const [i, src] of FUENTES_OPENCV.entries()) {
    try {
      if (src.startsWith('https://docs.opencv.org')) {
        // Este servidor no permite fetch (CORS): se carga como <script>, con límite de tiempo
        estado.textContent = 'Descargando visión por computadora desde otro servidor…';
        await Promise.race([cargarScript(src), esperar(180000).then(() => { throw new Error('tiempo agotado'); })]);
      } else {
        const url = await descargarConProgreso(src, (n) => {
          const mb = n / 1048576;
          estado.textContent = `Descargando visión por computadora: ${mb.toFixed(1)} de ~10 MB (solo la primera vez)`;
        });
        estado.textContent = 'Preparando la visión por computadora…';
        await cargarScript(url);
      }
      cv = await esperarRuntime();
      return true;
    } catch (_) {
      // La copia local es opcional: que no exista no significa que la conexión esté mal
      if (i < FUENTES_OPENCV.length - 1 && !src.startsWith('./')) estado.textContent = 'La conexión está lenta; probando otro servidor…';
    }
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
  mapa.setEncuadre(esquinasLatLng(c.bounds, c.epsg), PRESETS[c.preset]?.nombre || `EPSG:${c.epsg}`, c.preset);
  // Si ya había calibración, se recalcula con las mismas cruces: las piezas se mueven solas al nuevo lugar
  if (ultimas && crop && calibrarCon(ultimas.corners, ultimas.flecha)) {
    avisar('Área actualizada: las piezas ya están en su nuevo lugar.', 'ok');
  } else {
    resetCalibracion();
  }
  return true;
}

function resetCalibracion() {
  georef = null;
  crop = null;
  ultimas = null;
  geojsonActual = { type: 'FeatureCollection', features: [] };
  mapa.setDetecciones(geojsonActual);
}

// Devuelve la nota de orientación, o null si la geometría de las cruces no sirve
function calibrarCon(corners, flecha) {
  const c = cfg();
  const { esquinas, nota } = resolverEsquinas(c.orientacion, corners, flecha);
  const aMetros = calibrar(corners, c.bounds, esquinas);
  if (!aMetros) return null;
  const aLonLat = transformador(c.epsg);
  georef = (x, y) => { const [mx, my] = aMetros(x, y); return aLonLat(mx, my); };
  ultimas = { corners, flecha };
  return nota;
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
    sel.innerHTML = '<option value="">Trasera (automática)</option>' + devs.map((d, i) => `<option value="${d.deviceId}">${esc(d.label || `Cámara ${i + 1}`)}</option>`).join('');
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
    georef = null; crop = null; ultimas = null;
  }

  ctx.drawImage(video, 0, 0, W, H);
  const rgba = cv.matFromImageData(ctx.getImageData(0, 0, W, H));
  const rgb = new cv.Mat();
  cv.cvtColor(rgba, rgb, cv.COLOR_RGBA2RGB);
  rgba.delete();

  const c = cfg();
  const p = c.params;
  numFrame++;

  // Cruces y flecha se buscan siempre (retroalimentación); la homografía solo cambia al calibrar
  const cruces = buscarCruces(cv, rgb, p, MODOS[A.modo].cruces, A.vista === 'azul');
  if ((c.recalibCada > 0 && numFrame % c.recalibCada === 0) || pedirCalibracion) {
    const manual = pedirCalibracion;
    pedirCalibracion = false;
    const nota = cruces.corners ? calibrarCon(cruces.corners, cruces.flecha) : null;
    if (nota) {
      crop = cruces.crop;
      if (manual) avisar(`Calibrado con 4 cruces. ${nota}.`, 'ok');
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
  const vistaColor = A.vista !== 'deteccion' && A.vista !== 'azul' ? A.vista : null;
  const r = A.modo === 'libre'
    ? detectarMesa(cv, sub, p, c.colores, g, xoff, yoff, vistaColor)
    : detectarJuego(cv, sub, p, g, xoff, yoff, vistaColor === 'verde');
  if (sub !== rgb) sub.delete();
  rgb.delete();

  const mascara = A.vista === 'azul' ? cruces.mascara : r.mascara;
  if (mascara) pintarMascara(mascara, A.vista === 'azul' ? 0 : xoff, A.vista === 'azul' ? 0 : yoff, W, H);
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
    ctx.lineWidth = lw;
    ctx.strokeStyle = d.hex;
    if (d.tipo === 'line') trazo(d.caja, true);
    else if (d.tipo === 'polygon') { ctx.setLineDash([lw * 2, lw * 1.5]); trazo(d.pts, true); ctx.setLineDash([]); }
    else {
      const [x, y] = d.centro;
      ctx.strokeStyle = '#0a0d3a'; ctx.lineWidth = lw * 2.2;
      ctx.beginPath(); ctx.arc(x, y, lw * 3.5, 0, Math.PI * 2); ctx.stroke();
      ctx.strokeStyle = d.hex; ctx.lineWidth = lw;
      ctx.beginPath(); ctx.arc(x, y, lw * 3.5, 0, Math.PI * 2); ctx.stroke();
      if (d.n) { ctx.fillStyle = '#fff'; ctx.font = `600 ${Math.round(lw * 5)}px Inter, sans-serif`; ctx.fillText(String(d.n), x + lw * 4.5, y - lw * 2); }
    }
  }
}

function dibujarEstado({ cruces, calibrado, stats, pausa }) {
  const W = lienzo.width, H = lienzo.height;
  const lw = Math.max(2, W / 420);
  if (calibrado) {
    const [x0, y0, x1, y1] = crop;
    ctx.fillStyle = 'rgba(10,13,58,0.6)';
    ctx.fillRect(0, 0, W, y0); ctx.fillRect(0, y1, W, H - y1);
    ctx.fillRect(0, y0, x0, y1 - y0); ctx.fillRect(x1, y0, W - x1, y1 - y0);
  }
  const m = lw * 5;
  ctx.lineWidth = lw * 1.2;
  // Gris mientras faltan cruces; verde cuando se ven las 4
  const listo = cruces.centroides.length >= 4;
  ctx.strokeStyle = listo ? '#35ed7e' : '#b5bac1';
  for (const [x, y] of cruces.centroides) {
    ctx.beginPath(); ctx.moveTo(x - m, y); ctx.lineTo(x + m, y); ctx.moveTo(x, y - m); ctx.lineTo(x, y + m); ctx.stroke();
  }
  if (cruces.corners) { ctx.lineWidth = 1; ctx.strokeStyle = 'rgba(53,237,126,0.8)'; trazo(cruces.corners, true); }
  if (cruces.flecha) {
    // Flecha de norte reconocida: se dibuja su dirección en magenta
    const { c: [fx, fy], d: [dx, dy] } = cruces.flecha;
    const L = lw * 22, tx = fx + dx * L, ty = fy + dy * L;
    ctx.strokeStyle = '#35ed7e'; ctx.fillStyle = '#35ed7e'; ctx.lineWidth = lw * 1.5;
    ctx.beginPath(); ctx.moveTo(fx - dx * L * 0.4, fy - dy * L * 0.4); ctx.lineTo(tx, ty); ctx.stroke();
    ctx.beginPath(); ctx.moveTo(tx, ty);
    ctx.lineTo(tx - dx * lw * 6 - dy * lw * 4, ty - dy * lw * 6 + dx * lw * 4);
    ctx.lineTo(tx - dx * lw * 6 + dy * lw * 4, ty - dy * lw * 6 - dx * lw * 4);
    ctx.closePath(); ctx.fill();
    ctx.font = `800 ${Math.round(lw * 7)}px "Hanken Grotesk", sans-serif`;
    ctx.fillText('N', tx + dx * lw * 4 - lw * 2.5, ty + dy * lw * 4 + lw * 2.5);
  }
  if (pausa) {
    ctx.fillStyle = 'rgba(10,13,58,0.65)'; ctx.fillRect(0, 0, W, H);
    ctx.fillStyle = '#fff'; ctx.font = `800 ${Math.round(W / 16)}px "Hanken Grotesk", sans-serif`;
    ctx.textAlign = 'center'; ctx.fillText('EN PAUSA', W / 2, H / 2); ctx.textAlign = 'start';
  }

  const n = cruces.centroides.length;
  const pildoras = [calibrado
    ? '<span class="pildora calibrado"><i></i>Calibrado</span>'
    : `<span class="pildora" style="--c:${n >= 4 ? '#35ed7e' : '#8b90b8'}"><i></i>Cruces ${Math.min(n, 4)}/4${n > 4 ? ` (+${n - 4})` : ''}</span>`];
  if (c_orientacionAuto()) pildoras.push(`<span class="pildora" style="--c:${cruces.flecha ? '#35ed7e' : '#8b90b8'}"><i></i>${cruces.flecha ? 'Flecha N' : 'Sin flecha N'}</span>`);
  if (stats) {
    const tot = Object.entries(stats).filter(([, s]) => s.point + (s.line || 0) + (s.polygon || 0) > 0);
    for (const [col, s] of tot) {
      const hex = A.modo === 'libre' ? cfg().colores[col]?.hex : '#35ed7e';
      const txt = A.modo === 'libre' ? `${s.point}·${s.line}·${s.polygon}` : `${s.point} piezas`;
      pildoras.push(`<span class="pildora" style="--c:${hex}"><i></i>${txt}</span>`);
    }
  }
  const html = pildoras.join('');
  const el = $('estadoCamara');
  if (el.innerHTML !== html) el.innerHTML = html;
  $('btnCalibrar').classList.toggle('lista', !!cruces.corners && !calibrado);
}
const c_orientacionAuto = () => cfg().orientacion === 'auto';

// ================== Panel "Cámara" ==================
const llenarArea = formularioArea($('formArea'), cfg(), async (area) => {
  Object.assign(cfg(), area);
  guardar();
  if (await aplicarArea()) avisar(ultimas ? 'Área aplicada: las piezas ya están en su nuevo lugar.' : 'Área aplicada. Calibra con las cruces.', 'ok');
}, {
  conOrientacion: true,
  extra: '<p class="ayuda">Orientación automática: pon una <b>flecha azul apuntando al norte</b> junto a la cruz de arriba a la derecha del mapa (esquina noreste). Si no la pones, se usa “Norte arriba”; también puedes elegir la orientación a mano.</p>',
});

function pintarAjustes() {
  const c = cfg();
  llenarArea(c);
  $('selResolucion').innerHTML = RESOLUCIONES.map((r) => `<option value="${r}">${r} px</option>`).join('');
  $('selResolucion').value = A.resolucion;
  $('inpFps').value = A.fps;
  $('inpRecalib').value = c.recalibCada;
  // Vistas de máscara: una por color activo + la azul de las cruces
  const cols = A.modo === 'libre' ? Object.entries(c.colores).filter(([, v]) => v.activo) : [['verde', { nombre: 'Verde' }]];
  $('selVistaCam').innerHTML = '<option value="deteccion">Detección</option>'
    + cols.map(([k, v]) => `<option value="${k}">Máscara ${esc(v.nombre.toLowerCase())}</option>`).join('')
    + '<option value="azul">Máscara azul (cruces y flecha)</option>';
  if (![...$('selVistaCam').options].some((o) => o.value === A.vista)) A.vista = 'deteccion';
  $('selVistaCam').value = A.vista;
  pintarSliders();
}

function filaSlider(obj, clave, texto, min, max, paso, alCambiar) {
  const fila = document.createElement('label');
  fila.className = 'slider';
  fila.innerHTML = `<span>${texto}</span><input type="range" min="${min}" max="${max}" step="${paso}" value="${obj[clave]}"><output>${obj[clave]}</output>`;
  const inp = fila.querySelector('input'), out = fila.querySelector('output');
  inp.oninput = () => { obj[clave] = Number(inp.value); out.textContent = inp.value; alCambiar(); };
  return fila;
}

function pintarSliders() {
  const cont = $('sliders');
  const c = cfg();
  cont.innerHTML = '';
  // Editor de colores de plastilina (modo libre)
  if (A.modo === 'libre') {
    const bloque = document.createElement('div');
    bloque.className = 'editor-colores';
    bloque.innerHTML = '<h4>Colores de plastilina</h4><p class="ayuda">El azul queda reservado para las cruces y la flecha. Blanco y negro se confunden con el papel y las sombras: úsalos solo sobre fondos de color.</p>';
    for (const [id, col] of Object.entries(c.colores)) {
      const det = document.createElement('details');
      det.className = 'color-plastilina';
      det.innerHTML = `<summary><label class="check" onclick="event.stopPropagation()"><input type="checkbox" ${col.activo ? 'checked' : ''}></label><i style="--c:${col.hex}"></i><b>${esc(col.nombre)}</b><small>H ${col.hsv[0][0]}–${col.hsv[0][1]}</small></summary>`;
      det.querySelector('input').onchange = (e) => { col.activo = e.target.checked; guardar(); pintarAjustes(); };
      // hsv como objeto plano para los sliders
      const o = { hLo: col.hsv[0][0], hHi: col.hsv[0][1], sLo: col.hsv[1][0], sHi: col.hsv[1][1], vLo: col.hsv[2][0], vHi: col.hsv[2][1] };
      const aplicar = () => {
        col.hsv = [[o.hLo, o.hHi], [o.sLo, o.sHi], [o.vLo, o.vHi]];
        det.querySelector('small').textContent = `H ${o.hLo}–${o.hHi}`;
        guardar();
      };
      [['hLo', 'H bajo', 179], ['hHi', 'H alto', 179], ['sLo', 'S bajo', 255], ['sHi', 'S alto', 255], ['vLo', 'V bajo', 255], ['vHi', 'V alto', 255]]
        .forEach(([k, t, max]) => det.appendChild(filaSlider(o, k, t, 0, max, 1, aplicar)));
      bloque.appendChild(det);
    }
    cont.appendChild(bloque);
  }
  for (const [titulo, lista] of SLIDERS[A.modo]) {
    const grupo = document.createElement('div');
    grupo.className = 'grupo-sliders';
    grupo.innerHTML = `<h4>${titulo}</h4>`;
    for (const [clave, texto, min, max, paso] of lista) grupo.appendChild(filaSlider(c.params, clave, texto, min, max, paso, guardar));
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
  ultimas = null;
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
$('btnRestablecer').onclick = () => {
  cfg().params = { ...MODOS[A.modo].params };
  if (A.modo === 'libre') cfg().colores = copiaColores();
  guardar();
  pintarAjustes();
};

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
window.mesa3d = { A, mapa, osm, juego, get geojson() { return geojsonActual; }, iniciarArchivo, aplicarArea, cfg };
