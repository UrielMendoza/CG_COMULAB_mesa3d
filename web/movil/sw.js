// Service worker: permite usar la app sin internet después de la primera visita
// (importante en comunidades con conexión limitada). Las teselas del mapa base y las
// consultas a OpenStreetMap sí requieren conexión; sin internet, usa "Imagen de fondo".
const VERSION = 'mesa3d-movil-v2';

const APP = [
  './', './index.html', './manifest.webmanifest', './js/app.js', './js/deteccion.js',
  './icons/icono.svg', './icons/icono-192.png', './icons/icono-512.png',
  '../comun/css/comun.css',
  '../comun/js/config.js', '../comun/js/geo.js', '../comun/js/mapa.js', '../comun/js/osm.js',
  '../comun/js/juego.js', '../comun/js/paneles.js', '../comun/js/ui.js', '../comun/js/iconos.js',
  '../../config/georreferencia.json',
];

// Librerías y tipografías externas: se guardan la primera vez y después se sirven desde caché
const LIBRERIAS = ['cdn.jsdelivr.net', 'docs.opencv.org', 'fonts.googleapis.com', 'fonts.gstatic.com'];

self.addEventListener('install', (e) => {
  e.waitUntil(caches.open(VERSION).then((c) => c.addAll(APP)).then(() => self.skipWaiting()));
});

self.addEventListener('activate', (e) => {
  e.waitUntil(
    caches.keys()
      .then((ks) => Promise.all(ks.filter((k) => k !== VERSION).map((k) => caches.delete(k))))
      .then(() => self.clients.claim()),
  );
});

self.addEventListener('fetch', (e) => {
  const req = e.request;
  if (req.method !== 'GET') return;
  const url = new URL(req.url);

  if (LIBRERIAS.includes(url.hostname)) {
    e.respondWith(caches.match(req).then((hit) => hit || fetch(req).then((res) => {
      if (res.ok || res.type === 'opaque') {
        const copia = res.clone();
        caches.open(VERSION).then((c) => c.put(req, copia));
      }
      return res;
    })));
    return;
  }

  if (url.origin === self.location.origin) {
    // Primero la red (para recibir actualizaciones); la caché si no hay conexión
    e.respondWith(fetch(req).then((res) => {
      if (res.ok) {
        const copia = res.clone();
        caches.open(VERSION).then((c) => c.put(req, copia));
      }
      return res;
    }).catch(() => caches.match(req).then((hit) => hit || Response.error())));
  }
});
