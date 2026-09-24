// Contexto OpenStreetMap: consulta la base de datos de OSM (Overpass API) dentro del
// encuadre de trabajo y la usa para dar contexto a lo que la comunidad mapea
// (localidad, servicio de salud, escuela y agua más cercanos) y para crear retos del juego.
import { distanciaKm } from './geo.js';
import { esc, fmtKm, almacen } from './ui.js';

/* global L */

const SERVIDORES = [
  'https://overpass-api.de/api/interpreter',
  'https://overpass.kumi.systems/api/interpreter',
  'https://maps.mail.ru/osm/tools/overpass/api/interpreter',
];
const TIEMPO_MAX_MS = 60000;
const CLAVE_CACHE = 'mesa3d-osm-';

const TIPOS = {
  city: 'Ciudad', town: 'Pueblo', village: 'Localidad', hamlet: 'Caserío',
  hospital: 'Hospital', clinic: 'Clínica', doctors: 'Consultorio', centre: 'Centro de salud', doctor: 'Consultorio',
  school: 'Escuela', college: 'Colegio', university: 'Universidad', kindergarten: 'Preescolar',
  water: 'Cuerpo de agua', river: 'Río', stream: 'Arroyo', canal: 'Canal',
};

export const CATEGORIAS = {
  localidades: {
    nombre: 'Localidades', color: '#1b2330', frase: 'Localidad',
    consulta: (bb, conCaserios) => `node["place"~"^(city|town|village${conCaserios ? '|hamlet' : ''})$"]["name"](${bb});`,
    tipo: (t) => t.place,
    es: (t, el) => el.type === 'node' && /^(city|town|village|hamlet)$/.test(t.place || '') && !!t.name,
  },
  salud: {
    nombre: 'Salud', color: '#c4532d', frase: 'Salud',
    consulta: (bb) => `nwr["amenity"~"^(hospital|clinic|doctors)$"](${bb});nwr["healthcare"~"^(hospital|clinic|centre|doctor)$"](${bb});`,
    tipo: (t) => (/^(hospital|clinic|doctors)$/.test(t.amenity || '') ? t.amenity : t.healthcare),
    es: (t) => /^(hospital|clinic|doctors)$/.test(t.amenity || '') || /^(hospital|clinic|centre|doctor)$/.test(t.healthcare || ''),
  },
  educacion: {
    nombre: 'Escuelas', color: '#7b4bb7', frase: 'Escuela',
    consulta: (bb) => `nwr["amenity"~"^(school|college|university|kindergarten)$"](${bb});`,
    tipo: (t) => t.amenity,
    es: (t) => /^(school|college|university|kindergarten)$/.test(t.amenity || ''),
  },
  agua: {
    nombre: 'Agua', color: '#0f8b8d', frase: 'Agua',
    consulta: (bb) => `nwr["natural"="water"]["name"](${bb});way["waterway"~"^(river|stream|canal)$"]["name"](${bb});`,
    tipo: (t) => t.waterway || t.natural,
    es: (t) => !!t.name && (t.natural === 'water' || /^(river|stream|canal)$/.test(t.waterway || '')),
  },
};

const RADIO_LOCALIDAD = { city: 7, town: 5.5, village: 4, hamlet: 3 };

export class ContextoOSM {
  constructor(map) {
    this.map = map;
    this.renderer = L.canvas({ padding: 0.4 });
    this.capas = {};
    this.datos = {};          // cat → [{ id, nombre, tipo, lat, lng }]
    this.visibles = { localidades: true, salud: true, educacion: true, agua: true };
    this.bbox = null;          // [s, w, n, e]
    this.areaKm2 = 0;
    this.cache = new Map();
    this.accionesPopup = [];  // [{ texto, visible(el, cat), fn(el, cat) }] — p. ej. el juego
    this.alCambiar = () => {};
    for (const cat of Object.keys(CATEGORIAS)) this.capas[cat] = L.layerGroup().addTo(map);
    // Con poco acercamiento los puntos se hacen pequeños para no tapar el mapa
    map.on('zoomend', () => this._escalar());
  }

  _factor() {
    const z = this.map.getZoom();
    return z <= 8 ? 0.45 : z === 9 ? 0.6 : z === 10 ? 0.8 : 1;
  }

  _escalar() {
    const f = this._factor();
    for (const capa of Object.values(this.capas)) capa.eachLayer((m) => m.setRadius(Math.max(1.6, m._radioBase * f)));
  }

  // esquinas: [[lat, lng] ×4] del encuadre
  setArea(esquinas) {
    const lats = esquinas.map((e) => e[0]), lngs = esquinas.map((e) => e[1]);
    const nuevo = [Math.min(...lats), Math.min(...lngs), Math.max(...lats), Math.max(...lngs)].map((v) => +v.toFixed(5));
    if (this.bbox && nuevo.every((v, i) => v === this.bbox[i])) return;
    this.bbox = nuevo;
    const [s, w, n, e] = nuevo;
    this.areaKm2 = distanciaKm(s, w, s, e) * distanciaKm(s, w, n, w);
    this.datos = {};
    this.fecha = null;
    Object.values(this.capas).forEach((c) => c.clearLayers());
    this.alCambiar();
    // Si esta área ya se consultó antes en este dispositivo, se carga sin internet
    const todas = Object.keys(CATEGORIAS);
    if (almacen.leer(this._clave(todas), null)) this.cargar(todas).catch(() => {});
  }

  _clave(cats) {
    return `${CLAVE_CACHE}${this.bbox.join(',')}|${this.areaKm2 < 6000}|${cats.join(',')}`;
  }

  get cargado() { return Object.keys(this.datos).length > 0; }

  // Una sola consulta para todas las categorías; el resultado se guarda en el dispositivo
  // para esa área, así se puede volver a usar sin internet (y sin cargar al servidor).
  async cargar(cats = Object.keys(CATEGORIAS), progreso = () => {}, forzar = false) {
    if (!this.bbox) throw new Error('Define primero el área de trabajo.');
    const bb = this.bbox.join(',');
    const conCaserios = this.areaKm2 < 6000;
    const clave = this._clave(cats);
    let guardado = forzar ? null : (this.cache.get(clave) || almacen.leer(clave, null));
    if (!guardado) {
      progreso('Consultando OpenStreetMap…');
      const q = `[out:json][timeout:120];(${cats.map((c) => CATEGORIAS[c].consulta(bb, conCaserios)).join('')});out center tags 20000;`;
      const elementos = (await this._consultar(q, progreso)).map((el) => ({
        type: el.type, id: el.id, lat: el.lat, lon: el.lon, center: el.center,
        tags: Object.fromEntries(['name', 'place', 'amenity', 'healthcare', 'natural', 'waterway', 'population'].filter((k) => el.tags?.[k]).map((k) => [k, el.tags[k]])),
      }));
      guardado = { fecha: new Date().toISOString(), elementos };
      almacen.guardar(clave, guardado);
    }
    this.cache.set(clave, guardado);
    this.fecha = guardado.fecha;
    for (const cat of cats) {
      this.datos[cat] = this._normalizar(guardado.elementos.filter((el) => CATEGORIAS[cat].es(el.tags || {}, el)), cat);
      this._dibujar(cat);
    }
    this.alCambiar();
    return this.resumen();
  }

  async _consultar(q, progreso) {
    let ultimoError = null;
    for (const [i, url] of SERVIDORES.entries()) {
      if (i > 0) progreso(`Probando otro servidor de OpenStreetMap (${i + 1}/${SERVIDORES.length})…`);
      const ctl = new AbortController();
      const t = setTimeout(() => ctl.abort(), TIEMPO_MAX_MS);
      try {
        const r = await fetch(url, { method: 'POST', body: 'data=' + encodeURIComponent(q), headers: { 'Content-Type': 'application/x-www-form-urlencoded' }, signal: ctl.signal });
        if (!r.ok) throw new Error(r.status === 429 ? 'servidor ocupado' : `HTTP ${r.status}`);
        return (await r.json()).elements || [];
      } catch (e) {
        ultimoError = e.name === 'AbortError' ? new Error('tiempo agotado') : e;
      } finally {
        clearTimeout(t);
      }
    }
    throw new Error(`No se pudo consultar OpenStreetMap (${ultimoError?.message || 'sin conexión'}). Intenta de nuevo en un minuto.`);
  }

  _normalizar(elementos, cat) {
    const def = CATEGORIAS[cat];
    const vistos = new Set();
    const out = [];
    for (const el of elementos) {
      const lat = el.lat ?? el.center?.lat, lng = el.lon ?? el.center?.lon;
      if (lat == null || !el.tags) continue;
      const clave = `${el.type}/${el.id}`;
      if (vistos.has(clave)) continue;
      vistos.add(clave);
      const tipo = def.tipo(el.tags);
      out.push({
        id: clave, lat, lng, tipo,
        nombre: el.tags.name || TIPOS[tipo] || def.frase,
        conNombre: !!el.tags.name,
        poblacion: +el.tags.population || 0,
      });
    }
    return out;
  }

  _dibujar(cat) {
    const capa = this.capas[cat];
    capa.clearLayers();
    const def = CATEGORIAS[cat];
    for (const el of this.datos[cat] || []) {
      const base = cat === 'localidades' ? (RADIO_LOCALIDAD[el.tipo] || 3.5) : 4;
      const m = L.circleMarker([el.lat, el.lng], {
        renderer: this.renderer,
        radius: Math.max(1.6, base * this._factor()),
        color: '#fffdf8', weight: 1.4, fillColor: def.color, fillOpacity: cat === 'localidades' ? 0.9 : 0.8,
        pmIgnore: true,
      });
      m._radioBase = base;
      m.bindTooltip(esc(el.nombre), { direction: 'top', offset: [0, -4], className: 'osm-tooltip' });
      m.on('click', () => this._popup(m, el, cat));
      capa.addLayer(m);
    }
    if (!this.visibles[cat]) this.map.removeLayer(capa);
  }

  _popup(marker, el, cat) {
    const div = document.createElement('div');
    div.className = 'popup-osm';
    div.innerHTML = `<div class="popup-etiqueta" style="--c:${CATEGORIAS[cat].color}">${esc(TIPOS[el.tipo] || CATEGORIAS[cat].frase)} · OpenStreetMap</div>
      <div class="popup-titulo">${esc(el.nombre)}</div>
      ${el.poblacion ? `<div class="popup-dato">Población (OSM): ${el.poblacion.toLocaleString('es-MX')}</div>` : ''}
      <div class="popup-coord">${el.lat.toFixed(5)}, ${el.lng.toFixed(5)}</div>
      <a class="popup-link" href="https://www.openstreetmap.org/${el.id}" target="_blank" rel="noopener">Ver en openstreetmap.org</a>`;
    for (const a of this.accionesPopup) {
      if (a.visible && !a.visible(el, cat)) continue;
      const b = document.createElement('button');
      b.className = 'btn btn-chico btn-tinta';
      b.textContent = a.texto;
      b.onclick = () => { a.fn(el, cat); marker.closePopup(); };
      div.appendChild(b);
    }
    marker.bindPopup(div, { className: 'popup-papel', autoPanPaddingTopLeft: [16, 16], autoPanPaddingBottomRight: [64, 16] }).openPopup();
  }

  setVisible(cat, v) {
    this.visibles[cat] = v;
    if (v) this.capas[cat].addTo(this.map); else this.map.removeLayer(this.capas[cat]);
  }

  resumen() {
    return Object.fromEntries(Object.entries(this.datos).map(([k, v]) => [k, v.length]));
  }

  // Lo más cercano de cada categoría cargada a un punto
  cercanos(lat, lng) {
    const r = {};
    for (const [cat, lista] of Object.entries(this.datos)) {
      let mejor = null, dmin = Infinity;
      for (const el of lista) {
        if (cat === 'agua' && !el.conNombre) continue;
        const d = distanciaKm(lat, lng, el.lat, el.lng);
        if (d < dmin) { dmin = d; mejor = el; }
      }
      if (mejor) r[cat] = { nombre: mejor.nombre, tipo: TIPOS[mejor.tipo] || '', km: dmin };
    }
    return r;
  }

  // Bloque HTML "Según OpenStreetMap" para las ventanas de lo mapeado
  contextoHTML(lat, lng) {
    if (!this.cargado) {
      return '<div class="popup-osm-contexto vacio">Carga los datos OSM del área (pestaña OSM) para ver qué hay cerca.</div>';
    }
    const c = this.cercanos(lat, lng);
    const filas = Object.entries(c).map(([cat, v]) => `<li style="--c:${CATEGORIAS[cat].color}"><span>${CATEGORIAS[cat].frase}</span><b>${esc(v.nombre)}</b><em>${fmtKm(v.km)}</em></li>`).join('');
    return `<div class="popup-osm-contexto"><div class="popup-osm-titulo">Cerca, según OpenStreetMap</div><ul>${filas}</ul></div>`;
  }

  // Propiedades planas para exportar (osm_localidad, osm_localidad_km, …)
  contextoPropiedades(lat, lng) {
    const out = {};
    for (const [cat, v] of Object.entries(this.cercanos(lat, lng))) {
      out[`osm_${cat}`] = v.nombre;
      out[`osm_${cat}_km`] = +v.km.toFixed(2);
    }
    return out;
  }

  buscar(texto, max = 12) {
    const t = texto.trim().toLowerCase();
    if (t.length < 2) return [];
    const res = [];
    for (const [cat, lista] of Object.entries(this.datos)) {
      for (const el of lista) {
        if (el.conNombre && el.nombre.toLowerCase().includes(t)) res.push({ ...el, cat });
        if (res.length >= max) return res;
      }
    }
    return res;
  }

  // Localidades al azar para un reto (prefiere las que tienen más jerarquía)
  localidadesAleatorias(n) {
    const lista = (this.datos.localidades || []).filter((l) => l.conNombre);
    const pesos = { city: 4, town: 3, village: 2, hamlet: 1 };
    const bolsa = lista.map((l) => ({ l, k: Math.random() ** (1 / (pesos[l.tipo] || 1)) }));
    return bolsa.sort((a, b) => b.k - a.k).slice(0, n).map((b) => b.l);
  }
}
