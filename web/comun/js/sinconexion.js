// "Descargar para usar sin internet": guarda en el dispositivo lo necesario para que la
// plataforma funcione sin conexión. Solo actúa cuando el usuario lo pide y después de
// confirmar, mostrando qué se guarda, cuánto ocupa, cuánto espacio hay y dónde queda.
//
// Todo se guarda en el almacenamiento del navegador (Cache Storage) con los mismos nombres
// que usa el service worker (web/sw.js), que es quien lo sirve cuando no hay internet.
import { icono } from './iconos.js';
import { esc, avisar, modal, confirmar } from './ui.js';

const OFFLINE = 'cartografia-offline';
const TESELAS = 'cartografia-teselas';
const LIBRERIAS = 'cartografia-librerias';
const WEB = new URL('../../', import.meta.url).href;       // raíz de web/
const MB = 1048576;

// Archivos de la aplicación (los mismos que precarga web/sw.js)
const APP = [
  'movil/', 'movil/index.html', 'movil/manifest.webmanifest', 'movil/js/app.js', 'movil/js/deteccion.js',
  'movil/icons/icono.svg', 'movil/icons/icono-192.png', 'movil/icons/icono-512.png',
  'mapa/', 'mapa/index.html', 'mapa/js/app.js',
  'comun/css/comun.css',
  'comun/js/config.js', 'comun/js/geo.js', 'comun/js/mapa.js', 'comun/js/osm.js', 'comun/js/juego.js',
  'comun/js/paneles.js', 'comun/js/ui.js', 'comun/js/iconos.js', 'comun/js/respaldo.js', 'comun/js/sinconexion.js',
  'comun/vendor/leaflet/leaflet.js', 'comun/vendor/leaflet/leaflet.css',
  'comun/vendor/leaflet/images/layers.png', 'comun/vendor/leaflet/images/layers-2x.png',
  'comun/vendor/leaflet/images/marker-icon.png', 'comun/vendor/leaflet/images/marker-icon-2x.png', 'comun/vendor/leaflet/images/marker-shadow.png',
  'comun/vendor/geoman/leaflet-geoman.js', 'comun/vendor/geoman/leaflet-geoman.css',
  'comun/vendor/proj4.js', 'comun/vendor/togeojson.umd.js',
  '../config/georreferencia.json', '../config/colores.json',
].map((p) => new URL(p, WEB).href);
const TAM_APP = 0.9 * MB;          // aproximado; se reporta el tamaño real al terminar
const TAM_OPENCV = 10.4 * MB;
const OPCIONES_MAPA = [[0, 'No guardar el mapa base'], [50, 'Hasta 50 MB'], [150, 'Hasta 150 MB'], [300, 'Hasta 300 MB']];

const fmt = (b) => (b >= 1024 * MB ? `${(b / 1024 / MB).toFixed(1)} GB` : b >= 10 * MB ? `${Math.round(b / MB)} MB` : b >= MB ? `${(b / MB).toFixed(1)} MB` : `${Math.max(1, Math.round(b / 1024))} KB`);

async function espacio() {
  try {
    const e = await navigator.storage.estimate();
    return { usado: e.usage || 0, libre: Math.max(0, (e.quota || 0) - (e.usage || 0)) };
  } catch (_) {
    return null;
  }
}

async function tamRemoto(url, porDefecto) {
  try {
    const r = await fetch(url, { method: 'HEAD', cache: 'no-store' });
    const n = +r.headers.get('Content-Length');
    return n > 0 ? n : porDefecto;
  } catch (_) {
    return porDefecto;
  }
}

// ¿Ya está guardado en algún caché de la plataforma? (p. ej. OpenCV desde la primera visita)
async function enCache(url) {
  try { return !!(await caches.match(url)); } catch (_) { return false; }
}

// Descarga con avance (para OpenCV, que es lo más pesado)
async function bajarConAvance(url, alAvance) {
  const r = await fetch(url, { cache: 'reload' });
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
  const lector = r.body.getReader();
  const partes = [];
  let n = 0;
  for (;;) {
    const { done, value } = await lector.read();
    if (done) break;
    partes.push(value);
    n += value.length;
    alAvance(n);
  }
  return new Response(new Blob(partes, { type: r.headers.get('Content-Type') || 'application/octet-stream' }), {
    status: 200, headers: { 'Content-Type': r.headers.get('Content-Type') || 'application/octet-stream' },
  });
}

// el: contenedor; opciones: { osm, opencv: URL o null, disponible: () => bool }
export function montarSinConexion(el, { osm, opencv = null }) {
  const soportado = 'caches' in window && 'serviceWorker' in navigator && window.isSecureContext;

  const render = async () => {
    if (!soportado) {
      el.innerHTML = `<section class="seccion"><h3>Usar sin internet</h3>
        <p class="ayuda">Esta opción necesita abrir la plataforma por <b>https://</b> (por ejemplo desde GitHub Pages) o desde <b>localhost</b>.</p></section>`;
      return;
    }
    const hayOffline = await caches.has(OFFLINE) && (!opencv || await enCache(opencv));
    const hayMapa = await caches.has(TESELAS);
    const e = await espacio();
    el.innerHTML = `<section class="seccion">
      <h3>Usar sin internet</h3>
      <p class="ayuda">${hayOffline
        ? `<b>Listo para usar sin internet</b> en este dispositivo${hayMapa ? ', incluidas las partes del mapa base que ya viste' : ''}.`
        : 'Descarga una vez, con internet, lo necesario para trabajar después sin conexión: la aplicación, la visión por computadora y los datos de OpenStreetMap del área. Nada se descarga hasta que presiones el botón.'}</p>
      ${e ? `<p class="ayuda">La plataforma ocupa <b>${fmt(e.usado)}</b> en este dispositivo.</p>` : ''}
      <div class="rejilla-botones">
        <button class="btn btn-verde" data-descargar>${icono('descargar')} ${hayOffline ? 'Actualizar descarga' : 'Descargar para usar sin internet'}</button>
        <button class="btn btn-borde" data-borrar ${hayOffline || hayMapa ? '' : 'disabled'}>${icono('basura')} Borrar lo descargado</button>
      </div>
    </section>`;
    el.querySelector('[data-descargar]').onclick = preguntar;
    el.querySelector('[data-borrar]').onclick = borrar;
  };

  async function preguntar() {
    const preset = osm.preset;
    const urlOSM = preset ? new URL(`comun/datos_osm/${preset}.json`, WEB).href : null;
    const [tamOSM, tieneCV, e] = await Promise.all([
      urlOSM ? tamRemoto(urlOSM, 1.2 * MB) : 0,
      opencv ? enCache(opencv) : true,
      espacio(),
    ]);
    const filas = [
      ['La aplicación y sus librerías', TAM_APP],
      opencv ? ['Visión por computadora (OpenCV)', tieneCV ? 0 : TAM_OPENCV, tieneCV ? 'ya guardada' : null] : null,
      urlOSM ? ['Datos de OpenStreetMap del área', tamOSM] : ['Datos de OpenStreetMap del área', 0, 'no hay datos incluidos para un área personalizada'],
    ].filter(Boolean);
    const total = filas.reduce((a, f) => a + f[1], 0);
    const { el: m, cerrar } = modal('¿Descargar para usar sin internet?', `
      <p>Se guardará en este dispositivo:</p>
      <ul class="lista-descarga">${filas.map(([n, b, nota]) => `<li><span>${esc(n)}</span><b>${nota ? esc(nota) : `≈ ${fmt(b)}`}</b></li>`).join('')}
        <li class="total"><span>Total a descargar</span><b data-total>≈ ${fmt(total)}</b></li>
      </ul>
      <label class="campo">Mapa base (las partes que veas mientras tengas internet)
        <select data-mapa>${OPCIONES_MAPA.map(([v, t]) => `<option value="${v}"${v === 50 ? ' selected' : ''}>${t}</option>`).join('')}</select>
      </label>
      <p class="ayuda">OpenStreetMap no permite descargar su mapa base de golpe. Con esta opción, cada zona que recorras en el mapa (con internet) queda guardada hasta el límite que elijas, y la puedes ver después sin conexión. Sin internet también puedes usar una <b>imagen de fondo</b> del área.</p>
      <div class="donde">
        <p><b>Dónde se guarda:</b> en el almacenamiento interno del navegador de este dispositivo (datos del sitio <code>${esc(location.host)}</code>). No aparece en Descargas ni en la galería.</p>
        <p><b>Espacio disponible para el navegador:</b> ${e ? fmt(e.libre) : 'no se pudo calcular'}${e ? ` · la plataforma ya ocupa ${fmt(e.usado)}` : ''}.</p>
        <p>Puedes liberar el espacio cuando quieras con <b>Borrar lo descargado</b> o borrando los datos del sitio en el navegador.</p>
      </div>
      <div class="acciones-modal"><button class="btn btn-borde" data-no>Cancelar</button><button class="btn btn-verde" data-si>Sí, descargar</button></div>`, { ancho: 480 });
    const sel = m.querySelector('[data-mapa]');
    const actualizar = () => {
      const extra = Number(sel.value) * MB;
      m.querySelector('[data-total]').textContent = `≈ ${fmt(total)}${extra ? ` + hasta ${fmt(extra)} de mapa base` : ''}`;
    };
    sel.onchange = actualizar;
    actualizar();
    m.querySelector('[data-no]').onclick = cerrar;
    m.querySelector('[data-si]').onclick = () => { const mapaMB = Number(sel.value); cerrar(); descargar({ urlOSM, mapaMB, tieneCV }); };
  }

  async function descargar({ urlOSM, mapaMB, tieneCV }) {
    const { el: m, cerrar } = modal('Descargando…', `
      <p data-paso>Preparando…</p>
      <div class="barra-avance"><i data-barra></i></div>
      <p class="ayuda">No cierres esta página hasta que termine.</p>`, { ancho: 420 });
    const paso = (t, frac) => {
      m.querySelector('[data-paso]').textContent = t;
      if (frac != null) m.querySelector('[data-barra]').style.width = `${Math.round(Math.min(1, frac) * 100)}%`;
    };
    try {
      const antes = await espacio();
      const c = await caches.open(OFFLINE);
      // 1) Aplicación
      for (const [i, url] of APP.entries()) {
        paso(`La aplicación (${i + 1}/${APP.length})…`, i / APP.length * 0.15);
        const r = await fetch(url, { cache: 'reload' });
        if (r.ok) await c.put(url, r);
      }
      // 2) OpenCV
      if (opencv && !tieneCV) {
        const res = await bajarConAvance(opencv, (n) => paso(`Visión por computadora: ${fmt(n)} de ~${fmt(TAM_OPENCV)}`, 0.15 + (n / TAM_OPENCV) * 0.75));
        await (await caches.open(LIBRERIAS)).put(opencv, res);
      }
      // 3) OSM del área
      if (urlOSM) {
        paso('Datos de OpenStreetMap del área…', 0.93);
        const r = await fetch(urlOSM, { cache: 'reload' });
        if (r.ok) await c.put(urlOSM, r);
      }
      // 4) Mapa base: se activa (o desactiva) el guardado de lo que se vaya viendo
      if (mapaMB > 0) {
        const t = await caches.open(TESELAS);
        await t.put('/__limite_mb', new Response(String(mapaMB)));
      } else {
        await caches.delete(TESELAS);
      }
      // Que el navegador no borre esto por su cuenta cuando necesite espacio
      try { await navigator.storage.persist(); } catch (_) { /* no soportado */ }
      const despues = await espacio();
      paso('Listo.', 1);
      cerrar();
      const usado = antes && despues ? Math.max(0, despues.usado - antes.usado) : 0;
      avisar(`Listo para usar sin internet${usado ? ` (se usaron ${fmt(usado)})` : ''}.`, 'ok');
    } catch (err) {
      cerrar();
      avisar(`La descarga no se completó: ${err.message}. Revisa la conexión e inténtalo de nuevo; lo ya descargado se conserva.`, 'err');
    }
    render();
  }

  async function borrar() {
    const e = await espacio();
    if (!(await confirmar('Borrar lo descargado', `Se borrará lo guardado para usar sin internet (OpenCV, datos OSM del área y mapa base visto)${e ? `; hoy la plataforma ocupa ${fmt(e.usado)}` : ''}. Tu mapeo, tus notas y el juego NO se borran.`, 'Borrar'))) return;
    await caches.delete(OFFLINE);
    await caches.delete(TESELAS);
    await caches.delete(LIBRERIAS);
    avisar('Se liberó el espacio de la descarga.', 'ok');
    render();
  }

  render();
  return render;
}
