// Mapa compartido por la versión laptop (web/mapa) y la del celular (web/movil).
//
// Capas:
//   · En vivo    — lo que la cámara detecta en este momento (se reemplaza en cada actualización)
//   · Mapeo      — lo que se decide conservar: detecciones guardadas + dibujos a mano,
//                  con nombre, nota y color; se guarda en el navegador y se exporta a GeoJSON
//   · Capas      — archivos GeoJSON / KML / GPX cargados por el usuario
//   · Encuadre, imagen de fondo y (en modo juego) puntos de control
import { PALETA, colorPlastilina, nombreColor } from './config.js';
import { distanciaKm } from './geo.js';
import { icono } from './iconos.js';
import { esc, fmtKm, avisar, editarPropiedades, confirmar, descargar, fechaArchivo, almacen } from './ui.js';

/* global L, toGeoJSON */

const CLAVE_MAPEO = 'mesa3d-mapeo-v1';
const NOMBRE_TIPO = { point: 'Punto', line: 'Línea', polygon: 'Polígono', Point: 'Punto', LineString: 'Línea', Polygon: 'Polígono', MultiPolygon: 'Polígono', MultiLineString: 'Línea' };

// ---------- geometría ligera para mostrar medidas ----------
function puntoRepresentativo(g) {
  if (g.type === 'Point') return [g.coordinates[1], g.coordinates[0]];
  const pts = g.type === 'Polygon' ? g.coordinates[0] : g.type === 'MultiPolygon' ? g.coordinates[0][0]
    : g.type === 'MultiLineString' ? g.coordinates[0] : g.coordinates;
  if (g.type.includes('Line')) { const m = pts[Math.floor(pts.length / 2)]; return [m[1], m[0]]; }
  const n = pts.length;
  return [pts.reduce((s, c) => s + c[1], 0) / n, pts.reduce((s, c) => s + c[0], 0) / n];
}
function largoKm(coords) {
  let t = 0;
  for (let i = 1; i < coords.length; i++) t += distanciaKm(coords[i - 1][1], coords[i - 1][0], coords[i][1], coords[i][0]);
  return t;
}
function areaKm2(anillo) {
  const lat0 = anillo.reduce((s, c) => s + c[1], 0) / anillo.length;
  const kx = 111.32 * Math.cos(lat0 * Math.PI / 180), ky = 110.57;
  let a = 0;
  for (let i = 0, j = anillo.length - 1; i < anillo.length; j = i++) {
    a += (anillo[j][0] * kx) * (anillo[i][1] * ky) - (anillo[i][0] * kx) * (anillo[j][1] * ky);
  }
  return Math.abs(a / 2);
}
function medida(g) {
  if (g.type === 'LineString') return `Largo ≈ ${fmtKm(largoKm(g.coordinates))}`;
  if (g.type === 'Polygon') { const a = areaKm2(g.coordinates[0]); return `Área ≈ ${a < 1 ? `${Math.round(a * 100)} ha` : `${a.toFixed(1)} km²`}`; }
  if (g.type === 'Point') return `${g.coordinates[1].toFixed(5)}, ${g.coordinates[0].toFixed(5)}`;
  return '';
}
function circuloAPoligono(c, radioM, n = 48) {
  const R = 6378137, out = [];
  for (let i = 0; i <= n; i++) {
    const a = (i / n) * 2 * Math.PI;
    out.push([
      c.lng + (radioM / R) * Math.cos(a) * 180 / Math.PI / Math.cos(c.lat * Math.PI / 180),
      c.lat + (radioM / R) * Math.sin(a) * 180 / Math.PI,
    ]);
  }
  return { type: 'Polygon', coordinates: [out] };
}

export class Mapa {
  constructor(elId) {
    this.map = L.map(elId, { zoomControl: false, preferCanvas: false }).setView([19.3, -99.1], 8);
    L.control.zoom({ position: 'topright' }).addTo(this.map);
    this.map.attributionControl.setPrefix('<a href="https://leafletjs.com" target="_blank" rel="noopener">Leaflet</a>');

    const osm = L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', {
      maxZoom: 19, attribution: 'Mapa y datos © <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noopener">colaboradores de OpenStreetMap</a>',
    }).addTo(this.map);
    this.bases = {
      'OpenStreetMap': osm,
      'OSM Humanitario': L.tileLayer('https://{s}.tile.openstreetmap.fr/hot/{z}/{x}/{y}.png', { maxZoom: 19, attribution: '© colaboradores de OpenStreetMap · HOT' }),
      'Relieve (OpenTopoMap)': L.tileLayer('https://{s}.tile.opentopomap.org/{z}/{x}/{y}.png', { maxZoom: 17, attribution: '© colaboradores de OpenStreetMap · SRTM · OpenTopoMap' }),
      'Satélite (Esri)': L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}', { maxZoom: 19, attribution: 'Imágenes © Esri' }),
    };

    this.gEncuadre = L.featureGroup().addTo(this.map);
    this.gImagen = L.featureGroup().addTo(this.map);
    this.gCargadas = L.featureGroup().addTo(this.map);
    this.gGuardado = L.featureGroup().addTo(this.map);
    this.gVivo = L.featureGroup().addTo(this.map);

    this.controlCapas = L.control.layers(this.bases, {
      'Detecciones en vivo': this.gVivo,
      'Mapeo guardado': this.gGuardado,
      'Capas cargadas': this.gCargadas,
      'Imagen de fondo': this.gImagen,
      'Encuadre y cruces': this.gEncuadre,
    }, { position: 'topright', collapsed: true }).addTo(this.map);

    this.osm = null;             // ContextoOSM (lo asigna la app)
    this.bounds = null;
    this.overlay = null;
    this.vivo = { type: 'FeatureCollection', features: [] };
    this._firmaVivo = '';
    this.guardadas = almacen.leer(CLAVE_MAPEO, []);
    this.capaPorId = new Map();
    this.alCambiar = () => {};      // cambió el mapeo guardado
    this.alCambiarVivo = () => {};  // cambiaron las detecciones en vivo

    this._iniciarDibujo();
    this.guardadas.forEach((f) => this._agregarGuardada(f));
  }

  // ================== Dibujo a mano (Geoman) ==================
  _iniciarDibujo() {
    L.PM.setOptIn(true);         // solo el mapeo guardado es editable
    this.map.pm.setLang('es');
    this.map.pm.addControls({
      position: 'topright',
      drawCircleMarker: false, drawText: false, cutPolygon: false, rotateMode: false,
    });
    this.map.pm.setGlobalOptions({ pathOptions: { color: PALETA[6] } });

    this.map.on('pm:create', ({ layer, shape }) => {
      let geometry;
      if (shape === 'Circle') geometry = circuloAPoligono(layer.getLatLng(), layer.getRadius());
      else geometry = layer.toGeoJSON().geometry;
      this.map.removeLayer(layer);
      const f = this._nuevaGuardada(geometry, { origen: 'dibujo', color: PALETA[6] });
      this.abrir(f.properties.id);
    });
    this.map.on('pm:remove', ({ layer }) => {
      if (layer._idMapeo) this._quitarGuardada(layer._idMapeo, false);
    });
  }

  // ================== Encuadre ==================
  // esquinas: [SW, NW, NE, SE] en [lat, lng]
  setEncuadre(esquinas, etiqueta, preset = null) {
    this.gEncuadre.clearLayers();
    L.polygon(esquinas, { color: '#5865f2', weight: 2.5, dashArray: '8 6', fill: false, interactive: false, pmIgnore: true }).addTo(this.gEncuadre);
    const cruz = L.divIcon({ className: 'cruz-mapa', html: '<span></span>', iconSize: [22, 22], iconAnchor: [11, 11] });
    ['SO', 'NO', 'NE', 'SE'].forEach((lbl, i) => {
      L.marker(esquinas[i], { icon: cruz, pmIgnore: true }).bindTooltip(`Cruz ${lbl}`, { direction: 'top' }).addTo(this.gEncuadre);
    });
    this.bounds = L.latLngBounds(esquinas);
    this.etiquetaArea = etiqueta;
    if (this.overlay) this.overlay.setBounds(this.bounds);
    if (this.osm) this.osm.setArea(esquinas, preset);
    this.ajustarEncuadre();
  }

  ajustarEncuadre() {
    if (this.bounds) this.map.fitBounds(this.bounds, { padding: [24, 24] });
  }

  ajustarA(grupo) {
    if (grupo.getLayers().length) this.map.fitBounds(grupo.getBounds().pad(0.2), { maxZoom: 15 });
  }

  // ================== Detecciones en vivo ==================
  setDetecciones(fc) {
    this.vivo = fc;
    const firma = JSON.stringify(fc.features.map((f) => f.geometry.coordinates));
    if (firma === this._firmaVivo) return;
    this._firmaVivo = firma;
    this.gVivo.clearLayers();
    for (const f of fc.features) {
      const c = colorPlastilina(f.properties.color);
      const capa = L.geoJSON(f, {
        pmIgnore: true,
        style: () => ({ color: c, weight: 3, fillColor: c, fillOpacity: 0.3, dashArray: '2 5', lineCap: 'round' }),
        pointToLayer: (_, ll) => L.circleMarker(ll, { radius: 7, color: '#0a0d3a', weight: 2, fillColor: c, fillOpacity: 0.95, pmIgnore: true }),
      }).getLayers()[0];
      capa.bindPopup(() => this._popupVivo(f), { className: 'popup-papel', maxWidth: 280, autoPanPaddingTopLeft: [16, 16], autoPanPaddingBottomRight: [64, 16] });
      this.gVivo.addLayer(capa);
    }
    this.alCambiarVivo();
  }

  _popupVivo(f) {
    const div = document.createElement('div');
    const tipo = NOMBRE_TIPO[f.properties.type] || 'Detección';
    const [lat, lng] = puntoRepresentativo(f.geometry);
    div.innerHTML = `<div class="popup-etiqueta" style="--c:${colorPlastilina(f.properties.color)}">En vivo · plastilina ${nombreColor(f.properties.color)}</div>
      <div class="popup-titulo">${tipo}</div>
      <div class="popup-coord">${medida(f.geometry)}</div>
      ${this.osm ? this.osm.contextoHTML(lat, lng) : ''}
      <div class="popup-acciones"><button class="btn btn-chico btn-tinta" data-guardar>${icono('guardar')} Guardar y anotar</button></div>`;
    div.querySelector('[data-guardar]').onclick = async () => {
      this.map.closePopup();
      const nueva = this.guardarDeteccion(f);
      const props = await editarPropiedades(nueva.properties);
      if (props) this.actualizarPropiedades(nueva.properties.id, props);
    };
    return div;
  }

  // ================== Mapeo guardado ==================
  _nuevaGuardada(geometry, props = {}) {
    const f = {
      type: 'Feature',
      geometry,
      properties: {
        id: `m${Date.now().toString(36)}${Math.random().toString(36).slice(2, 6)}`,
        nombre: '', nota: '', color: PALETA[0], origen: 'deteccion', fecha: new Date().toISOString(),
        ...props,
      },
    };
    this.guardadas.push(f);
    this._agregarGuardada(f);
    this._persistir();
    return f;
  }

  guardarDeteccion(f) {
    return this._nuevaGuardada(JSON.parse(JSON.stringify(f.geometry)), {
      origen: 'deteccion',
      plastilina: f.properties.color,
      tipo: f.properties.type,
      color: colorPlastilina(f.properties.color),
    });
  }

  guardarTodasLasDetecciones() {
    const n = this.vivo.features.length;
    if (!n) { avisar('No hay detecciones en vivo para guardar.', 'err'); return 0; }
    this.vivo.features.forEach((f) => this.guardarDeteccion(f));
    avisar(`${n} ${n === 1 ? 'elemento guardado' : 'elementos guardados'} en el mapeo.`, 'ok');
    return n;
  }

  _estilo(f) {
    const c = f.properties.color || PALETA[0];
    return { color: c, weight: f.geometry.type.includes('Line') ? 4 : 2.5, fillColor: c, fillOpacity: 0.28 };
  }

  _agregarGuardada(f) {
    const capa = L.geoJSON(f, {
      pmIgnore: false,
      style: () => this._estilo(f),
      pointToLayer: (_, ll) => L.circleMarker(ll, { radius: 8, color: '#ffffff', weight: 2.5, fillColor: f.properties.color, fillOpacity: 1, pmIgnore: false }),
    }).getLayers()[0];
    if (!capa) return;
    capa._idMapeo = f.properties.id;
    capa.bindPopup(() => this._popupGuardada(f.properties.id), { className: 'popup-papel', maxWidth: 300, autoPanPaddingTopLeft: [16, 16], autoPanPaddingBottomRight: [64, 16] });
    if (f.properties.nombre) capa.bindTooltip(esc(f.properties.nombre), { direction: 'top', className: 'etiqueta-mapeo' });
    const alEditar = () => {
      const g = capa.toGeoJSON().geometry;
      const actual = this.guardadas.find((x) => x.properties.id === f.properties.id);
      if (actual) { actual.geometry = g; this._persistir(); }
    };
    capa.on('pm:edit pm:dragend', alEditar);
    this.capaPorId.set(f.properties.id, capa);
    this.gGuardado.addLayer(capa);
  }

  _popupGuardada(id) {
    const f = this.guardadas.find((x) => x.properties.id === id);
    const div = document.createElement('div');
    if (!f) return div;
    const p = f.properties;
    const [lat, lng] = puntoRepresentativo(f.geometry);
    const origen = p.origen === 'dibujo' ? 'Dibujado a mano' : `Plastilina ${nombreColor(p.plastilina)}`;
    div.innerHTML = `<div class="popup-etiqueta" style="--c:${esc(p.color)}">${esc(origen)} · ${NOMBRE_TIPO[f.geometry.type] || ''}</div>
      <div class="popup-titulo">${esc(p.nombre || 'Sin nombre')}</div>
      ${p.nota ? `<p class="popup-nota">${esc(p.nota)}</p>` : ''}
      <div class="popup-coord">${medida(f.geometry)}</div>
      ${this.osm ? this.osm.contextoHTML(lat, lng) : ''}
      <div class="popup-acciones">
        <button class="btn btn-chico btn-tinta" data-editar>${icono('lapiz')} Nombre, nota y color</button>
        <button class="btn btn-chico btn-borde" data-borrar aria-label="Eliminar">${icono('basura')}</button>
      </div>`;
    div.querySelector('[data-editar]').onclick = async () => {
      this.map.closePopup();
      const props = await editarPropiedades(p);
      if (props) { this.actualizarPropiedades(id, props); this.abrir(id); }
    };
    div.querySelector('[data-borrar]').onclick = async () => {
      this.map.closePopup();
      if (await confirmar('Eliminar elemento', `¿Eliminar “${p.nombre || NOMBRE_TIPO[f.geometry.type]}” del mapeo?`, 'Eliminar')) this._quitarGuardada(id);
    };
    return div;
  }

  actualizarPropiedades(id, props) {
    const f = this.guardadas.find((x) => x.properties.id === id);
    if (!f) return;
    f.properties = { ...f.properties, ...props };
    const vieja = this.capaPorId.get(id);
    if (vieja) this.gGuardado.removeLayer(vieja);
    this._agregarGuardada(f);
    this._persistir();
  }

  _quitarGuardada(id, quitarCapa = true) {
    this.guardadas = this.guardadas.filter((x) => x.properties.id !== id);
    const capa = this.capaPorId.get(id);
    if (capa && quitarCapa) this.gGuardado.removeLayer(capa);
    this.capaPorId.delete(id);
    this._persistir();
  }

  async borrarMapeo() {
    if (!this.guardadas.length) return;
    if (!(await confirmar('Borrar mapeo', `Se borrarán ${this.guardadas.length} elementos guardados en este navegador. Exporta antes si los necesitas.`, 'Borrar todo'))) return;
    this.guardadas = [];
    this.gGuardado.clearLayers();
    this.capaPorId.clear();
    this._persistir();
  }

  // Reemplaza todo el mapeo guardado (al cargar un respaldo)
  reemplazarMapeo(features) {
    this.guardadas = features.filter((f) => f.geometry && f.properties?.id);
    this.gGuardado.clearLayers();
    this.capaPorId.clear();
    this.guardadas.forEach((f) => this._agregarGuardada(f));
    this._persistir();
    this.ajustarA(this.gGuardado);
  }

  abrir(id) {
    const capa = this.capaPorId.get(id);
    if (!capa) return;
    if (capa.getLatLng) this.map.setView(capa.getLatLng(), Math.max(this.map.getZoom(), 12));
    else this.map.fitBounds(capa.getBounds().pad(0.4), { maxZoom: 15 });
    capa.openPopup();
  }

  _persistir() {
    almacen.guardar(CLAVE_MAPEO, this.guardadas);
    this.alCambiar();
  }

  // ================== Exportar / importar ==================
  _conContexto(f) {
    const [lat, lng] = puntoRepresentativo(f.geometry);
    const extra = this.osm && this.osm.cargado ? this.osm.contextoPropiedades(lat, lng) : {};
    return { ...f, properties: { ...f.properties, ...extra } };
  }

  exportarMapeo() {
    if (!this.guardadas.length) return avisar('El mapeo guardado está vacío.', 'err');
    descargar(`mapeo_${fechaArchivo()}.geojson`, { type: 'FeatureCollection', features: this.guardadas.map((f) => this._conContexto(f)) });
  }

  exportarVivo() {
    if (!this.vivo.features.length) return avisar('No hay detecciones en vivo.', 'err');
    descargar(`detecciones_${fechaArchivo()}.geojson`, { type: 'FeatureCollection', features: this.vivo.features.map((f) => this._conContexto(f)) });
  }

  async importarMapeo(file) {
    try {
      const fc = await this._leerComoGeoJSON(file);
      let n = 0;
      for (const f of fc.features || []) {
        if (!f.geometry) continue;
        const p = f.properties || {};
        this._nuevaGuardada(f.geometry, {
          nombre: p.nombre || p.name || '', nota: p.nota || p.description || '',
          color: colorPlastilina(p.color),
          origen: p.origen || 'importado', plastilina: p.plastilina,
        });
        n++;
      }
      avisar(`${n} elementos agregados al mapeo.`, 'ok');
      this.ajustarA(this.gGuardado);
    } catch (e) {
      avisar(`No se pudo leer el archivo: ${e.message}`, 'err');
    }
  }

  async _leerComoGeoJSON(file) {
    const texto = await file.text();
    const nombre = file.name.toLowerCase();
    if (nombre.endsWith('.kml') || nombre.endsWith('.gpx')) {
      if (typeof toGeoJSON === 'undefined') throw new Error('lector KML/GPX no disponible');
      const xml = new DOMParser().parseFromString(texto, 'text/xml');
      return nombre.endsWith('.kml') ? toGeoJSON.kml(xml) : toGeoJSON.gpx(xml);
    }
    const g = JSON.parse(texto);
    return g.type === 'FeatureCollection' ? g : { type: 'FeatureCollection', features: g.type === 'Feature' ? [g] : [] };
  }

  async cargarCapa(file) {
    try {
      const fc = await this._leerComoGeoJSON(file);
      const capa = L.geoJSON(fc, {
        pmIgnore: true,
        style: { color: '#ec48bd', weight: 2, fillOpacity: 0.12 },
        pointToLayer: (_, ll) => L.circleMarker(ll, { radius: 5, color: '#ffffff', weight: 1.5, fillColor: '#ec48bd', fillOpacity: 0.9, pmIgnore: true }),
        onEachFeature: (f, l) => {
          const p = f.properties || {};
          const nombre = p.nombre || p.name || p.NOMBRE || p.NOM_LOC;
          if (nombre) l.bindTooltip(esc(nombre));
        },
      });
      this.gCargadas.addLayer(capa);
      this.controlCapas.addOverlay(capa, esc(file.name));
      this.ajustarA(capa);
      avisar(`Capa “${file.name}” cargada (${fc.features.length} elementos).`, 'ok');
    } catch (e) {
      avisar(`No se pudo leer el archivo: ${e.message}`, 'err');
    }
  }

  // ================== Imagen de fondo (ajustada al encuadre) ==================
  cargarImagen(file) {
    if (!this.bounds) return avisar('Define primero el área de trabajo.', 'err');
    const reader = new FileReader();
    reader.onload = (e) => {
      if (this.overlay) this.gImagen.removeLayer(this.overlay);
      this.overlay = L.imageOverlay(e.target.result, this.bounds, { opacity: 0.85, pmIgnore: true });
      this.gImagen.addLayer(this.overlay);
      this.ajustarEncuadre();
    };
    reader.readAsDataURL(file);
  }

  quitarImagen() {
    if (this.overlay) this.gImagen.removeLayer(this.overlay);
    this.overlay = null;
  }

  setOpacidadImagen(v) { if (this.overlay) this.overlay.setOpacity(v); }

  invalidar() { this.map.invalidateSize(); }

  // Lista para el panel: [{ id, nombre, tipo, color }]
  listaMapeo() {
    return this.guardadas.map((f) => ({
      id: f.properties.id,
      nombre: f.properties.nombre || `${NOMBRE_TIPO[f.geometry.type] || 'Elemento'} sin nombre`,
      nota: f.properties.nota,
      tipo: NOMBRE_TIPO[f.geometry.type] || '',
      color: f.properties.color,
    })).reverse();
  }
}

export { puntoRepresentativo };
