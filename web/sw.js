// Service worker de toda la web (web/mapa y web/movil).
// · La app abre rápido aunque la red sea lenta (datos móviles): red con límite de tiempo,
//   y si no responde se entrega la copia guardada.
// · "Descargar para usar sin internet" (web/comun/js/sinconexion.js) guarda OpenCV, los datos
//   OSM del área y la app en el caché OFFLINE; y, si se eligió, activa el guardado de las
//   partes del mapa base que se van viendo (caché TESELAS, con límite de tamaño).
// Todo queda en el almacenamiento del navegador de este dispositivo (Cache Storage).
const VERSION = 'cartografia-v5';
const OFFLINE = 'cartografia-offline';     // lo que se descarga con el botón (no se borra al actualizar)
const TESELAS = 'cartografia-teselas';     // mapa base visto; solo existe si el usuario lo activó
const LIBRERIAS = 'cartografia-librerias'; // OpenCV y tipografías: se guardan una sola vez y sobreviven a las actualizaciones
const ESPERA_RED_MS = 4000;

// Raíz de web/ (este archivo también se importa desde web/movil/sw.js)
const BASE = new URL(self.location.pathname.endsWith('/movil/sw.js') ? '../' : './', self.location).href;
const APP = [
  'movil/', 'movil/index.html', 'movil/manifest.webmanifest', 'movil/js/app.js', 'movil/js/deteccion.js',
  'movil/icons/icono.svg', 'movil/icons/icono-192.png', 'movil/icons/icono-512.png',
  'mapa/', 'mapa/index.html', 'mapa/js/app.js',
  'comun/css/comun.css',
  'comun/js/config.js', 'comun/js/geo.js', 'comun/js/mapa.js', 'comun/js/osm.js', 'comun/js/juego.js',
  'comun/js/paneles.js', 'comun/js/ui.js', 'comun/js/iconos.js', 'comun/js/respaldo.js', 'comun/js/sinconexion.js',
  'comun/vendor/leaflet/leaflet.js', 'comun/vendor/leaflet/leaflet.css',
  'comun/vendor/geoman/leaflet-geoman.js', 'comun/vendor/geoman/leaflet-geoman.css',
  'comun/vendor/proj4.js', 'comun/vendor/togeojson.umd.js',
  '../config/georreferencia.json', '../config/colores.json',
].map((p) => new URL(p, BASE).href);

const EXTERNOS = ['cdn.jsdelivr.net', 'docs.opencv.org', 'fonts.googleapis.com', 'fonts.gstatic.com'];
const SERVIDORES_TESELAS = /(^|\.)(tile\.openstreetmap\.org|tile\.openstreetmap\.fr|tile\.opentopomap\.org|arcgisonline\.com)$/;

self.addEventListener('install', (e) => {
  e.waitUntil(caches.open(VERSION).then((c) => c.addAll(APP)).then(() => self.skipWaiting()));
});

self.addEventListener('activate', (e) => {
  // Se borran solo versiones viejas de la app; lo descargado por el usuario se conserva
  e.waitUntil(
    caches.keys()
      .then((ks) => Promise.all(ks.filter((k) => ![VERSION, OFFLINE, TESELAS, LIBRERIAS].includes(k)).map((k) => caches.delete(k))))
      .then(() => self.clients.claim()),
  );
});

function guardar(req, res, cache = VERSION) {
  if (res && (res.ok || res.type === 'opaque')) {
    const copia = res.clone();
    caches.open(cache).then((c) => c.put(req, copia));
  }
  return res;
}

// ---------- Mapa base (solo si el usuario lo activó) ----------
let guardadasDesdeRecorte = 0;
async function limiteTeselas() {
  const c = await caches.open(TESELAS);
  const r = await c.match('/__limite_mb');
  return r ? Number(await r.text()) : 0;
}
async function recortarTeselas() {
  const mb = await limiteTeselas();
  const c = await caches.open(TESELAS);
  const claves = (await c.keys()).filter((k) => !k.url.endsWith('/__limite_mb'));
  const maximo = Math.floor((mb * 1024) / 18);   // ~18 KB por pieza de mapa
  for (const k of claves.slice(0, Math.max(0, claves.length - maximo))) await c.delete(k);
}
async function responderTesela(req) {
  const guardada = await caches.match(req);
  try {
    const res = await fetch(req);
    if (res.ok) {
      const c = await caches.open(TESELAS);
      await c.put(req, res.clone());
      if (++guardadasDesdeRecorte >= 100) { guardadasDesdeRecorte = 0; recortarTeselas(); }
    }
    return res;
  } catch (_) {
    return guardada || Response.error();
  }
}

self.addEventListener('fetch', (e) => {
  const req = e.request;
  if (req.method !== 'GET') return;
  const url = new URL(req.url);

  if (SERVIDORES_TESELAS.test(url.hostname)) {
    // Sin la opción activada, el mapa base se comporta como siempre (no se guarda nada)
    e.respondWith(caches.has(TESELAS).then((activo) => (activo ? responderTesela(req) : fetch(req))));
    return;
  }

  if (EXTERNOS.includes(url.hostname)) {
    e.respondWith(caches.match(req).then((hit) => hit || fetch(req).then((res) => guardar(req, res, LIBRERIAS))));
    return;
  }

  if (url.origin === self.location.origin) {
    e.respondWith((async () => {
      const deRed = fetch(req).then((res) => guardar(req, res));
      const guardada = await caches.match(req);
      if (!guardada) return deRed.catch(() => Response.error());
      const limite = new Promise((ok) => setTimeout(() => ok(guardada), ESPERA_RED_MS));
      return Promise.race([deRed.catch(() => guardada), limite]);
    })());
  }
});
