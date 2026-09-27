// Contexto OpenStreetMap: consulta la base de datos de OSM (Overpass API) dentro del
// encuadre de trabajo y la usa para dar contexto a lo que la comunidad mapea
// (localidad, municipio, montaña, parque, agua… más cercanos) y para crear retos del juego.
//
// Cada categoría es una consulta independiente: si el servidor falla con una (504, 429,
// tiempo agotado), las demás se cargan igual y solo esa se puede reintentar. Lo consultado
// se guarda en el dispositivo por área, así que funciona sin internet la siguiente vez.
import { distanciaKm } from './geo.js';
import { esc, fmtKm, almacen } from './ui.js';

/* global L */

const DE = 'https://overpass-api.de/api/interpreter';
const KUMI = 'https://overpass.kumi.systems/api/interpreter';
const MAILRU = 'https://maps.mail.ru/osm/tools/overpass/api/interpreter';
const COFFEE = 'https://overpass.private.coffee/api/interpreter';
// Un 504 de Overpass casi siempre es saturación momentánea: se reintenta alternando
// servidores y con pausas crecientes (en segundos).
const INTENTOS = [[DE, 0], [MAILRU, 2], [DE, 4], [KUMI, 5], [DE, 8], [COFFEE, 8], [DE, 12]];
const TIEMPO_MAX_MS = 35000;
// Datos pre-descargados por área (escritorio/herramientas/descargar_osm.py)
const RUTA_DATOS = new URL('../datos_osm/', import.meta.url).href;
const CLAVE_CACHE = 'mesa3d-osm2-';
const AREA_GRANDE_KM2 = 6000;   // en áreas grandes se omiten las categorías más densas

const TIPOS = {
  city: 'Ciudad', town: 'Pueblo', village: 'Localidad', hamlet: 'Caserío',
  suburb: 'Colonia', neighbourhood: 'Barrio', quarter: 'Barrio',
  peak: 'Cerro / montaña', volcano: 'Volcán',
  park: 'Parque', nature_reserve: 'Reserva natural', national_park: 'Parque nacional', protected_area: 'Área protegida',
  water: 'Cuerpo de agua', river: 'Río', stream: 'Arroyo', canal: 'Canal', lake: 'Lago', reservoir: 'Presa', lagoon: 'Laguna',
  harbour: 'Puerto', marina: 'Marina', ferry_terminal: 'Terminal de ferry', port: 'Puerto',
  hospital: 'Hospital', clinic: 'Clínica', doctors: 'Consultorio',
  school: 'Escuela', college: 'Colegio', university: 'Universidad', kindergarten: 'Preescolar',
  municipio: 'Municipio / alcaldía',
};

const re = (s) => new RegExp(`^(${s})$`);

// visible = si se muestra al cargar (las más densas empiezan ocultas para no saturar el mapa)
export const CATEGORIAS = {
  localidades: {
    nombre: 'Localidades', frase: 'Localidad', color: '#0a0d3a', visible: true,
    consulta: (bb, grande) => `node["place"~"^(city|town|village${grande ? '' : '|hamlet'})$"]["name"](${bb});`,
    es: (t, el) => el.type === 'node' && re('city|town|village|hamlet').test(t.place || ''),
    tipo: (t) => t.place,
  },
  municipios: {
    nombre: 'Municipios y alcaldías', frase: 'Municipio', color: '#ec48bd', visible: true,
    consulta: (bb) => `relation["boundary"="administrative"]["admin_level"="6"]["name"](${bb});`,
    es: (t) => t.boundary === 'administrative',
    tipo: () => 'municipio',
  },
  colonias: {
    nombre: 'Colonias y barrios', frase: 'Colonia', color: '#7a83ff', visible: false,
    consulta: (bb, grande) => `nwr["place"~"^(suburb${grande ? '' : '|neighbourhood|quarter'})$"]["name"](${bb});`,
    es: (t) => re('suburb|neighbourhood|quarter').test(t.place || ''),
    tipo: (t) => t.place,
  },
  montanas: {
    nombre: 'Cerros y volcanes', frase: 'Cerro', color: '#6b5b4b', visible: true,
    consulta: (bb) => `node["natural"~"^(peak|volcano)$"]["name"](${bb});`,
    es: (t) => re('peak|volcano').test(t.natural || ''),
    tipo: (t) => t.natural,
  },
  parques: {
    nombre: 'Parques y reservas', frase: 'Parque', color: '#1faa6b', visible: true, agrupar: true,
    // En áreas grandes solo reservas y áreas protegidas (los parques urbanos se cuentan por miles)
    consulta: (bb, grande) => `nwr["leisure"~"^(${grande ? 'nature_reserve' : 'park|nature_reserve'})$"]["name"](${bb});relation["boundary"~"^(national_park|protected_area)$"]["name"](${bb});`,
    es: (t) => re('park|nature_reserve').test(t.leisure || '') || re('national_park|protected_area').test(t.boundary || ''),
    tipo: (t) => t.leisure || t.boundary,
  },
  agua: {
    nombre: 'Ríos, lagos y presas', frase: 'Agua', color: '#00b0f4', visible: true, agrupar: true,
    consulta: (bb, grande) => `nwr["natural"="water"]["name"](${bb});way["waterway"~"^(river|canal${grande ? '' : '|stream'})$"]["name"](${bb});`,
    es: (t) => t.natural === 'water' || re('river|stream|canal').test(t.waterway || ''),
    tipo: (t) => t.waterway || t.water || t.natural,
  },
  puertos: {
    nombre: 'Puertos y muelles', frase: 'Puerto', color: '#0b4f8a', visible: true,
    consulta: (bb) => `nwr["harbour"](${bb});nwr["leisure"="marina"](${bb});nwr["amenity"="ferry_terminal"](${bb});nwr["landuse"="port"](${bb});`,
    es: (t) => !!t.harbour || t.leisure === 'marina' || t.amenity === 'ferry_terminal' || t.landuse === 'port',
    tipo: (t) => (t.leisure === 'marina' ? 'marina' : t.amenity === 'ferry_terminal' ? 'ferry_terminal' : 'harbour'),
  },
  salud: {
    nombre: 'Salud', frase: 'Salud', color: '#e11d48', visible: false,
    consulta: (bb) => `nwr["amenity"~"^(hospital|clinic|doctors)$"](${bb});`,
    es: (t) => re('hospital|clinic|doctors').test(t.amenity || ''),
    tipo: (t) => t.amenity,
  },
  educacion: {
    nombre: 'Escuelas', frase: 'Escuela', color: '#a855f7', visible: false,
    consulta: (bb, grande) => `nwr["amenity"~"^(${grande ? 'university|college' : 'school|college|university'})$"](${bb});`,
    es: (t) => re('school|college|university|kindergarten').test(t.amenity || ''),
    tipo: (t) => t.amenity,
  },
};

// Categorías que siempre aparecen en el contexto de una pieza; de las demás, las 3 más cercanas
const CONTEXTO_FIJO = ['localidades', 'municipios'];

const RADIO_LOCALIDAD = { city: 7, town: 5.5, village: 4, hamlet: 3 };
const esperar = (ms) => new Promise((r) => setTimeout(r, ms));

export class ContextoOSM {
  constructor(map) {
    this.map = map;
    this.renderer = L.canvas({ padding: 0.4 });
    this.capas = {};
    this.datos = {};           // cat → [{ id, nombre, tipo, lat, lng }]
    this.errores = {};         // cat → mensaje del último fallo
    this.visibles = Object.fromEntries(Object.entries(CATEGORIAS).map(([k, c]) => [k, c.visible]));
    this.bbox = null;          // [s, w, n, e]
    this.areaKm2 = 0;
    this.fecha = null;
    this.accionesPopup = [];   // [{ texto, visible(el, cat), fn(el, cat) }] — p. ej. el juego
    this.alCambiar = () => {};
    for (const cat of Object.keys(CATEGORIAS)) this.capas[cat] = L.layerGroup();
    map.on('zoomend', () => this._escalar());
  }

  get grande() { return this.areaKm2 > AREA_GRANDE_KM2; }
  get cargado() { return Object.keys(this.datos).length > 0; }

  // esquinas: [[lat, lng] ×4] del encuadre; preset: clave del área (para usar datos pre-descargados)
  setArea(esquinas, preset = null) {
    const lats = esquinas.map((e) => e[0]), lngs = esquinas.map((e) => e[1]);
    const nuevo = [Math.min(...lats), Math.min(...lngs), Math.max(...lats), Math.max(...lngs)].map((v) => +v.toFixed(5));
    if (this.bbox && nuevo.every((v, i) => v === this.bbox[i])) return;
    this.bbox = nuevo;
    const [s, w, n, e] = nuevo;
    this.areaKm2 = distanciaKm(s, w, s, e) * distanciaKm(s, w, n, w);
    this.datos = {};
    this.errores = {};
    this.fecha = null;
    this.predescargado = null;
    this.preset = preset && preset !== 'personalizado' ? preset : null;
    this._archivo = undefined;
    Object.values(this.capas).forEach((c) => { c.clearLayers(); this.map.removeLayer(c); });
    // Nada se carga solo: los datos OSM aparecen hasta que se presiona "Consultar OSM"
    this.alCambiar();
  }

  // Datos pre-descargados del área (web/comun/datos_osm/<area>.json), o null si no hay
  async _predescargado() {
    if (this._archivo !== undefined) return this._archivo;
    this._archivo = null;
    if (!this.preset) return null;
    try {
      const r = await fetch(`${RUTA_DATOS}${this.preset}.json`);
      if (r.ok) this._archivo = await r.json();
    } catch (_) { /* sin archivo o sin conexión */ }
    return this._archivo;
  }

  _clave(cat) { return `${CLAVE_CACHE}${this.bbox.join(',')}|${cat}|${this.grande ? 'g' : 'c'}`; }

  // Carga categorías (todas por defecto). Devuelve { resumen, errores }.
  async cargar(cats = Object.keys(CATEGORIAS), progreso = () => {}, forzar = false) {
    if (!this.bbox) throw new Error('Define primero el área de trabajo.');
    const bb = this.bbox.join(',');
    const archivo = forzar ? null : await this._predescargado();
    for (const [i, cat] of cats.entries()) {
      if (!forzar && this.datos[cat]) continue;
      // 1) copia guardada en este dispositivo · 2) datos incluidos con la plataforma · 3) consulta en vivo
      const guardado = forzar ? null : almacen.leer(this._clave(cat), null);
      if (guardado) { this._usar(cat, guardado); continue; }
      if (archivo?.categorias?.[cat]) { this._usar(cat, archivo.categorias[cat]); this.predescargado = archivo.fecha; continue; }
      progreso(`OpenStreetMap: ${CATEGORIAS[cat].nombre.toLowerCase()} (${i + 1}/${cats.length})…`);
      try {
        const q = `[out:json][timeout:40];(${CATEGORIAS[cat].consulta(bb, this.grande)});out center tags 4000;`;
        const elementos = (await this._consultar(q, progreso)).map((el) => ({
          type: el.type, id: el.id, lat: el.lat ?? el.center?.lat, lon: el.lon ?? el.center?.lon,
          tags: Object.fromEntries(['name', 'place', 'amenity', 'natural', 'waterway', 'water', 'leisure', 'boundary',
            'harbour', 'landuse', 'population'].filter((k) => el.tags?.[k]).map((k) => [k, el.tags[k]])),
        }));
        const g = { fecha: new Date().toISOString(), elementos };
        almacen.guardar(this._clave(cat), g);
        this._usar(cat, g);
        delete this.errores[cat];
      } catch (e) {
        this.errores[cat] = e.message;
      }
      this.alCambiar();
    }
    return { resumen: this.resumen(), errores: { ...this.errores } };
  }

  _usar(cat, guardado) {
    this.fecha = guardado.fecha;
    this.datos[cat] = this._normalizar(guardado.elementos.filter((el) => el.lat != null && CATEGORIAS[cat].es(el.tags || {}, el)), cat);
    this._dibujar(cat);
  }

  // Servidores públicos; si uno está saturado (504/429) o tarda, se reintenta con pausa
  async _consultar(q, progreso = () => {}) {
    let ultimo = null;
    for (const [i, [url, pausa]] of INTENTOS.entries()) {
      if (pausa) { progreso(`Servidor de OSM ocupado; reintento ${i}/${INTENTOS.length - 1}…`); await esperar(pausa * 1000); }
      const ctl = new AbortController();
      const t = setTimeout(() => ctl.abort(), TIEMPO_MAX_MS);
      try {
        const r = await fetch(url, { method: 'POST', body: 'data=' + encodeURIComponent(q), headers: { 'Content-Type': 'application/x-www-form-urlencoded' }, signal: ctl.signal });
        if (!r.ok) throw new Error(r.status === 429 ? 'servidores ocupados' : r.status === 504 ? 'servidores saturados (504)' : `HTTP ${r.status}`);
        const j = await r.json();
        if (j.remark && /timed out|runtime error/i.test(j.remark)) throw new Error('consulta demasiado grande');
        return j.elements || [];
      } catch (e) {
        ultimo = e.name === 'AbortError' ? new Error('tiempo agotado') : e;
      } finally {
        clearTimeout(t);
      }
    }
    throw ultimo || new Error('sin conexión');
  }

  _normalizar(elementos, cat) {
    const def = CATEGORIAS[cat];
    const vistos = new Set();
    const out = [];
    for (const el of elementos) {
      const nombre = el.tags.name;
      // Ríos y parques vienen en muchos tramos: uno por nombre cada ~5 km basta
      const clave = def.agrupar && nombre ? `${nombre}|${Math.round(el.lat * 20)}|${Math.round(el.lon * 20)}` : `${el.type}/${el.id}`;
      if (vistos.has(clave)) continue;
      vistos.add(clave);
      const tipo = def.tipo(el.tags);
      out.push({
        id: `${el.type}/${el.id}`, lat: el.lat, lng: el.lon, tipo,
        nombre: nombre || TIPOS[tipo] || def.frase,
        conNombre: !!nombre,
        poblacion: +el.tags.population || 0,
      });
    }
    return out;
  }

  _factor() {
    const z = this.map.getZoom();
    return z <= 8 ? 0.45 : z === 9 ? 0.6 : z === 10 ? 0.8 : 1;
  }

  _escalar() {
    const f = this._factor();
    for (const capa of Object.values(this.capas)) capa.eachLayer((m) => m.setRadius(Math.max(1.6, m._radioBase * f)));
  }

  _dibujar(cat) {
    const capa = this.capas[cat];
    capa.clearLayers();
    const def = CATEGORIAS[cat];
    for (const el of this.datos[cat] || []) {
      const base = cat === 'localidades' ? (RADIO_LOCALIDAD[el.tipo] || 3.5) : cat === 'municipios' ? 6 : 4.5;
      const m = L.circleMarker([el.lat, el.lng], {
        renderer: this.renderer, radius: Math.max(1.6, base * this._factor()),
        color: '#ffffff', weight: 1.4, fillColor: def.color, fillOpacity: 0.9, pmIgnore: true,
      });
      m._radioBase = base;
      m.bindTooltip(esc(el.nombre), { direction: 'top', offset: [0, -4], className: 'osm-tooltip' });
      m.on('click', () => this._popup(m, el, cat));
      capa.addLayer(m);
    }
    if (this.visibles[cat]) capa.addTo(this.map); else this.map.removeLayer(capa);
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
      b.className = 'btn btn-chico btn-blurple';
      b.textContent = a.texto;
      b.onclick = () => { a.fn(el, cat); marker.closePopup(); };
      div.appendChild(b);
    }
    marker.bindPopup(div, { className: 'popup-papel', autoPanPaddingTopLeft: [16, 16], autoPanPaddingBottomRight: [64, 16] }).openPopup();
  }

  setVisible(cat, v) {
    this.visibles[cat] = v;
    if (v && this.datos[cat]) this.capas[cat].addTo(this.map); else this.map.removeLayer(this.capas[cat]);
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
        if (!el.conNombre) continue;
        const d = distanciaKm(lat, lng, el.lat, el.lng);
        if (d < dmin) { dmin = d; mejor = el; }
      }
      if (mejor) r[cat] = { nombre: mejor.nombre, tipo: TIPOS[mejor.tipo] || '', km: dmin };
    }
    return r;
  }

  // Bloque HTML "Cerca, según OpenStreetMap" para las ventanas de lo mapeado
  contextoHTML(lat, lng) {
    if (!this.cargado) {
      return '<div class="popup-osm-contexto vacio">Consulta los datos OSM del área (pestaña OSM) para ver qué hay cerca.</div>';
    }
    const c = this.cercanos(lat, lng);
    const fijos = CONTEXTO_FIJO.filter((k) => c[k]).map((k) => [k, c[k]]);
    const otros = Object.entries(c).filter(([k]) => !CONTEXTO_FIJO.includes(k)).sort((a, b) => a[1].km - b[1].km).slice(0, 3);
    const filas = [...fijos, ...otros].map(([cat, v]) => `<li style="--c:${CATEGORIAS[cat].color}"><span>${CATEGORIAS[cat].frase}</span><b>${esc(v.nombre)}</b><em>${fmtKm(v.km)}</em></li>`).join('');
    return `<div class="popup-osm-contexto"><div class="popup-osm-titulo">Cerca, según OpenStreetMap</div><ul>${filas}</ul></div>`;
  }

  // Propiedades planas para exportar (osm_localidades, osm_localidades_km, …)
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

  // Lugares al azar para un reto (localidades con más peso según su jerarquía, cerros, parques…)
  lugaresAleatorios(n, cats = ['localidades']) {
    const pesos = { city: 4, town: 3, village: 2, hamlet: 1 };
    const lista = cats.flatMap((c) => (this.datos[c] || []).filter((l) => l.conNombre));
    const bolsa = lista.map((l) => ({ l, k: Math.random() ** (1 / (pesos[l.tipo] || 2)) }));
    return bolsa.sort((a, b) => b.k - a.k).slice(0, n).map((b) => b.l);
  }
}
