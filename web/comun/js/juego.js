// Dinámica de juego genérica: el facilitador registra participantes y define puntos de control
// (a mano sobre el mapa, desde un archivo o con localidades de OpenStreetMap). Cada participante
// ubica los lugares con plastilina verde y el sistema mide qué tan cerca quedó.
import { distanciaKm } from './geo.js';
import { icono } from './iconos.js';
import { puntoRepresentativo } from './mapa.js';
import { esc, fmtKm, avisar, modal, pedirTexto, confirmar, descargar, elegirArchivo, fechaArchivo, almacen } from './ui.js';

/* global L, toGeoJSON */

const CLAVE = 'mesa3d-juego-v1';
const COLOR = { hit: '#2e8b57', near: '#d08a00', miss: '#c4532d' };
const ETIQUETA = { hit: 'Dentro', near: 'Cerca', miss: 'Fuera' };
const id = (p) => `${p}${Date.now().toString(36)}${Math.random().toString(36).slice(2, 5)}`;

export class Juego {
  // mapa: Mapa; osm: ContextoOSM; obtenerDetecciones: () => FeatureCollection en vivo
  constructor(mapa, osm, obtenerDetecciones) {
    this.mapa = mapa;
    this.map = mapa.map;
    this.osm = osm;
    this.obtenerDetecciones = obtenerDetecciones;
    this.activo = false;
    this.colocando = false;
    this.el = null;

    const g = almacen.leer(CLAVE, {});
    this.s = {
      participantes: g.participantes || [],   // [{ id, nombre }]
      turno: g.turno || null,                 // id del participante en turno
      objetivos: g.objetivos || [],           // [{ id, nombre, lat, lng, origen }]
      radio: g.radio || 5,                    // km
      resultados: g.resultados || {},         // `${objetivo}|${participante}` → { km, puntos, estado, cerca }
      mostrar: !!g.mostrar,
    };

    this.gObjetivos = L.featureGroup();
    this.gResultados = L.featureGroup();

    this.map.on('click', (e) => { if (this.colocando) this._colocarEn(e.latlng); });
    // En cualquier punto OSM se puede crear un punto de control
    this.osm.accionesPopup.push({
      texto: 'Usar como punto de control',
      visible: () => this.activo,
      fn: (el) => this._agregar({ nombre: el.nombre, lat: el.lat, lng: el.lng, origen: 'osm' }),
    });
  }

  // ================== Estado ==================
  _guardar() { almacen.guardar(CLAVE, this.s); this.render(); this._dibujar(); }

  get participante() { return this.s.participantes.find((p) => p.id === this.s.turno) || this.s.participantes[0] || null; }

  totales(pid) {
    let puntos = 0, hit = 0, near = 0, miss = 0;
    for (const [k, r] of Object.entries(this.s.resultados)) {
      if (!k.endsWith(`|${pid}`)) continue;
      puntos += r.puntos;
      if (r.estado === 'hit') hit++; else if (r.estado === 'near') near++; else miss++;
    }
    return { puntos, hit, near, miss };
  }

  setActivo(v) {
    this.activo = v;
    if (v) { this.gObjetivos.addTo(this.map); this.gResultados.addTo(this.map); this._dibujar(); }
    else { this.map.removeLayer(this.gObjetivos); this.map.removeLayer(this.gResultados); this._setColocando(false); }
  }

  // ================== Participantes ==================
  agregarParticipante(nombre) {
    nombre = nombre.trim();
    if (!nombre) return;
    const p = { id: id('p'), nombre };
    this.s.participantes.push(p);
    if (!this.s.turno) this.s.turno = p.id;
    this._guardar();
  }

  async quitarParticipante(pid) {
    const p = this.s.participantes.find((x) => x.id === pid);
    if (!p || !(await confirmar('Quitar participante', `¿Quitar a “${p.nombre}” y sus puntos?`, 'Quitar'))) return;
    this.s.participantes = this.s.participantes.filter((x) => x.id !== pid);
    for (const k of Object.keys(this.s.resultados)) if (k.endsWith(`|${pid}`)) delete this.s.resultados[k];
    if (this.s.turno === pid) this.s.turno = this.s.participantes[0]?.id || null;
    this._guardar();
  }

  siguiente() {
    const ps = this.s.participantes;
    if (!ps.length) return;
    const i = ps.findIndex((p) => p.id === this.s.turno);
    this.s.turno = ps[(i + 1) % ps.length].id;
    this.gResultados.clearLayers();
    this._guardar();
    avisar(`Turno de ${this.participante.nombre}`);
  }

  // ================== Puntos de control ==================
  _agregar(o) {
    this.s.objetivos.push({ id: id('o'), ...o });
    this._guardar();
    avisar(`Punto de control agregado: ${o.nombre}`, 'ok');
  }

  _setColocando(v) {
    this.colocando = v;
    this.map.getContainer().classList.toggle('colocando', v);
    const b = document.getElementById('bannerColocar');
    if (b) b.hidden = !v;
    this._dibujar();
  }

  terminarColocar() { this._setColocando(false); this.render(); }

  async _colocarEn(latlng) {
    let sugerido = `Punto ${this.s.objetivos.length + 1}`;
    if (this.osm.cargado) {
      const c = this.osm.cercanos(latlng.lat, latlng.lng).localidades;
      if (c && c.km < 3) sugerido = c.nombre;
    }
    const nombre = await pedirTexto('Nuevo punto de control', 'Nombre del lugar', sugerido);
    if (nombre) this._agregar({ nombre, lat: latlng.lat, lng: latlng.lng, origen: 'mano' });
  }

  async cargarArchivo() {
    const file = await elegirArchivo('.geojson,.json,.csv,.kml');
    if (!file) return;
    try {
      const texto = await file.text();
      const nuevos = [];
      if (file.name.toLowerCase().endsWith('.csv')) {
        const filas = texto.trim().split(/\r?\n/).map((l) => l.split(/[,;\t]/).map((c) => c.trim().replace(/^"|"$/g, '')));
        const cab = filas.shift().map((c) => c.toLowerCase());
        const col = (...ops) => cab.findIndex((c) => ops.includes(c));
        const iN = col('nombre', 'name', 'lugar'), iLat = col('lat', 'latitud', 'latitude', 'y'), iLng = col('lon', 'lng', 'longitud', 'longitude', 'x');
        if (iLat < 0 || iLng < 0) throw new Error('el CSV necesita columnas lat y lon');
        filas.forEach((f, i) => {
          const lat = parseFloat(f[iLat]), lng = parseFloat(f[iLng]);
          if (Number.isFinite(lat) && Number.isFinite(lng)) nuevos.push({ nombre: (iN >= 0 && f[iN]) || `Punto ${i + 1}`, lat, lng });
        });
      } else {
        const fc = file.name.toLowerCase().endsWith('.kml')
          ? toGeoJSON.kml(new DOMParser().parseFromString(texto, 'text/xml'))
          : JSON.parse(texto);
        (fc.features || []).forEach((f, i) => {
          if (!f.geometry) return;
          const [lat, lng] = puntoRepresentativo(f.geometry);
          const p = f.properties || {};
          nuevos.push({ nombre: p.nombre || p.name || p.NOMBRE || `Punto ${i + 1}`, lat, lng });
        });
      }
      if (!nuevos.length) throw new Error('no se encontraron puntos');
      nuevos.forEach((o) => this.s.objetivos.push({ id: id('o'), ...o, origen: 'archivo' }));
      this._guardar();
      avisar(`${nuevos.length} puntos de control cargados de “${file.name}”.`, 'ok');
    } catch (e) {
      avisar(`No se pudo leer el archivo: ${e.message}`, 'err');
    }
  }

  async retoOSM(n) {
    try {
      if (!this.osm.datos.localidades) {
        avisar('Consultando localidades en OpenStreetMap…');
        await this.osm.cargar(['localidades']);
      }
      const elegidas = this.osm.localidadesAleatorias(n);
      if (!elegidas.length) return avisar('OSM no tiene localidades con nombre en esta área.', 'err');
      elegidas.forEach((l) => this.s.objetivos.push({ id: id('o'), nombre: l.nombre, lat: l.lat, lng: l.lng, origen: 'osm' }));
      this.s.mostrar = false;
      this._guardar();
      avisar(`Reto listo: ${elegidas.length} localidades de OpenStreetMap (ocultas).`, 'ok');
    } catch (e) {
      avisar(e.message, 'err');
    }
  }

  quitarObjetivo(oid) {
    this.s.objetivos = this.s.objetivos.filter((o) => o.id !== oid);
    for (const k of Object.keys(this.s.resultados)) if (k.startsWith(`${oid}|`)) delete this.s.resultados[k];
    this._guardar();
  }

  async quitarObjetivos() {
    if (!this.s.objetivos.length || !(await confirmar('Quitar puntos de control', 'Se quitarán todos los puntos de control y sus resultados.', 'Quitar todos'))) return;
    this.s.objetivos = [];
    this.s.resultados = {};
    this.gResultados.clearLayers();
    this._guardar();
  }

  exportarObjetivos() {
    if (!this.s.objetivos.length) return avisar('No hay puntos de control.', 'err');
    descargar(`puntos_control_${fechaArchivo()}.geojson`, {
      type: 'FeatureCollection',
      features: this.s.objetivos.map((o) => ({ type: 'Feature', properties: { nombre: o.nombre, origen: o.origen }, geometry: { type: 'Point', coordinates: [o.lng, o.lat] } })),
    });
  }

  // ================== Verificar ==================
  verificar() {
    const p = this.participante;
    if (!p) return avisar('Agrega al menos un participante.', 'err');
    if (!this.s.objetivos.length) return avisar('Define los puntos de control primero.', 'err');
    const piezas = this.obtenerDetecciones().features.map((f) => puntoRepresentativo(f.geometry));
    if (!piezas.length) return avisar('No hay piezas detectadas. Revisa que la cámara esté calibrada.', 'err');

    const R = this.s.radio;
    let nuevos = 0;
    this.gResultados.clearLayers();
    for (const o of this.s.objetivos) {
      let mejor = null, dmin = Infinity;
      for (const [lat, lng] of piezas) {
        const d = distanciaKm(lat, lng, o.lat, o.lng);
        if (d < dmin) { dmin = d; mejor = [lat, lng]; }
      }
      const k = `${o.id}|${p.id}`;
      if (!this.s.resultados[k]) {
        let puntos, estado;
        if (dmin <= R) { puntos = Math.round(100 * (1 - dmin / R) + 50); estado = 'hit'; }
        else if (dmin <= 2 * R) { puntos = Math.round(30 * (1 - (dmin - R) / R) + 10); estado = 'near'; }
        else { puntos = Math.max(0, Math.round(10 - dmin / 10)); estado = 'miss'; }
        const cerca = this.osm.cargado ? this.osm.cercanos(mejor[0], mejor[1]).localidades : null;
        this.s.resultados[k] = { km: dmin, puntos, estado, cerca: cerca && cerca.km < 5 ? cerca.nombre : null };
        nuevos += puntos;
      }
      this._dibujarResultado(o, this.s.resultados[k], mejor);
    }
    almacen.guardar(CLAVE, this.s);
    this.render();
    this._dibujar(true);
    this.map.fitBounds(this.gResultados.getBounds().pad(0.1), { maxZoom: 13 });
    this._aviso(p, nuevos);
  }

  _dibujarResultado(o, r, pieza) {
    const c = COLOR[r.estado];
    L.circle([o.lat, o.lng], { radius: this.s.radio * 1000, color: c, weight: 1.5, fillColor: c, fillOpacity: 0.1, interactive: false, pmIgnore: true }).addTo(this.gResultados);
    if (pieza) L.polyline([pieza, [o.lat, o.lng]], { color: c, weight: 2, dashArray: '4 6', interactive: false, pmIgnore: true }).addTo(this.gResultados);
  }

  _aviso(p, nuevos) {
    const t = this.totales(p.id);
    const div = document.createElement('div');
    div.className = 'resultado-turno';
    div.innerHTML = `<div class="rt-nombre">${esc(p.nombre)}</div><div class="rt-puntos">+${nuevos}</div><div class="rt-total">${t.puntos} puntos en total · ${t.hit} dentro · ${t.near} cerca · ${t.miss} fuera</div>`;
    document.body.appendChild(div);
    setTimeout(() => div.classList.add('saliendo'), 2600);
    setTimeout(() => div.remove(), 3200);
  }

  marcador() {
    if (!this.s.participantes.length) return avisar('Todavía no hay participantes.', 'err');
    const filas = this.s.participantes.map((p) => ({ p, t: this.totales(p.id) })).sort((a, b) => b.t.puntos - a.t.puntos);
    const html = `<ol class="marcador">${filas.map(({ p, t }, i) => `<li class="${i === 0 && t.puntos > 0 ? 'primero' : ''}"><span class="m-pos">${i + 1}</span><span class="m-nombre">${esc(p.nombre)}</span><span class="m-det">${t.hit} · ${t.near} · ${t.miss}</span><span class="m-pts">${t.puntos}</span></li>`).join('')}</ol>
      <p class="ayuda">Dentro · cerca · fuera, sobre ${this.s.objetivos.length} puntos de control con radio de ${this.s.radio} km.</p>`;
    modal('Marcador', html, { ancho: 460 });
  }

  async reiniciarPuntajes() {
    if (!(await confirmar('Reiniciar puntajes', 'Se borrarán los resultados de todos los participantes. Los participantes y los puntos de control se conservan.', 'Reiniciar'))) return;
    this.s.resultados = {};
    this.gResultados.clearLayers();
    this._guardar();
  }

  // ================== Capas del mapa ==================
  _dibujar(forzarVisibles = false) {
    this.gObjetivos.clearLayers();
    if (!this.activo) return;
    const visibles = this.s.mostrar || this.colocando || forzarVisibles;
    if (!visibles) return;
    const p = this.participante;
    for (const o of this.s.objetivos) {
      const r = p ? this.s.resultados[`${o.id}|${p.id}`] : null;
      const c = r ? COLOR[r.estado] : '#1b2330';
      const ico = L.divIcon({ className: 'objetivo', html: `<span style="--c:${c}"></span>`, iconSize: [26, 26], iconAnchor: [13, 13] });
      const txt = r ? `${esc(o.nombre)} · ${fmtKm(r.km)} · ${ETIQUETA[r.estado]}` : esc(o.nombre);
      L.marker([o.lat, o.lng], { icon: ico, pmIgnore: true })
        .bindTooltip(txt, { permanent: true, direction: 'top', offset: [0, -12], className: 'etiqueta-objetivo' })
        .addTo(this.gObjetivos);
    }
  }

  // ================== Panel ==================
  montar(el) {
    this.el = el;
    this.render();
  }

  render() {
    if (!this.el) return;
    const p = this.participante;
    const s = this.s;
    this.el.innerHTML = `
      <section class="seccion">
        <h3>Participantes <span class="cuenta">${s.participantes.length}</span></h3>
        <form class="fila" data-form-part>
          <input type="text" placeholder="Nombre de la persona o equipo" aria-label="Nombre del participante" maxlength="40">
          <button class="btn btn-tinta" type="submit">${icono('mas')} Agregar</button>
        </form>
        <ul class="participantes">${s.participantes.map((x) => {
          const t = this.totales(x.id);
          return `<li class="${x.id === p?.id ? 'en-turno' : ''}" data-part="${x.id}"><button class="p-nombre">${esc(x.nombre)}</button><span class="p-pts">${t.puntos}</span><button class="btn-icono chico" data-quitar-part="${x.id}" aria-label="Quitar">${icono('cerrar')}</button></li>`;
        }).join('') || '<li class="vacio">Agrega a quienes van a jugar.</li>'}</ul>
      </section>

      <section class="seccion">
        <h3>Puntos de control <span class="cuenta">${s.objetivos.length}</span></h3>
        <p class="ayuda">Los lugares que los participantes deben ubicar con plastilina verde.</p>
        <div class="rejilla-botones">
          <button class="btn btn-borde ${this.colocando ? 'activo' : ''}" data-colocar>${icono('pin')} ${this.colocando ? 'Terminar' : 'Colocar a mano'}</button>
          <button class="btn btn-borde" data-archivo>${icono('archivo')} Desde archivo</button>
        </div>
        <div class="reto-osm">
          <div><b>Reto con OpenStreetMap</b><span>Elige localidades reales del área al azar.</span></div>
          <input type="number" min="1" max="30" value="5" aria-label="Número de localidades" data-n-reto>
          <button class="btn btn-osm" data-reto>${icono('dado')} Crear</button>
        </div>
        <ul class="objetivos">${s.objetivos.map((o) => {
          const r = p ? s.resultados[`${o.id}|${p.id}`] : null;
          const res = r ? `<span class="o-res ${r.estado}" title="${r.cerca ? `Su pieza cayó cerca de ${esc(r.cerca)}` : ''}">${fmtKm(r.km)} · +${r.puntos}</span>` : '<span class="o-res">—</span>';
          return `<li><span class="o-origen ${o.origen}" title="${esc(o.origen)}"></span><span class="o-nombre">${s.mostrar || r ? esc(o.nombre) : '••••••'}</span>${res}<button class="btn-icono chico" data-quitar-obj="${o.id}" aria-label="Quitar">${icono('cerrar')}</button></li>`;
        }).join('') || '<li class="vacio">Colócalos en el mapa, cárgalos de un archivo (GeoJSON, KML o CSV con nombre, lat, lon) o crea un reto con OSM.</li>'}</ul>
        <div class="fila-botones">
          <button class="btn btn-chico btn-borde" data-mostrar>${icono(s.mostrar ? 'ojoNo' : 'ojo')} ${s.mostrar ? 'Ocultar' : 'Mostrar'}</button>
          <button class="btn btn-chico btn-borde" data-exportar>${icono('descargar')} Exportar</button>
          <button class="btn btn-chico btn-borde" data-quitar-todos>${icono('basura')} Quitar todos</button>
        </div>
      </section>

      <section class="seccion">
        <h3>Reglas</h3>
        <label class="fila-campo">Radio de acierto <span><input type="number" min="0.1" step="0.5" value="${s.radio}" data-radio> km</span></label>
        <p class="ayuda">Dentro del radio: 50–150 puntos (más cerca, más puntos). Hasta el doble del radio: 10–40. Fuera: casi nada.</p>
      </section>

      <section class="seccion turno">
        <div class="turno-de">Turno de <b>${esc(p?.nombre || '—')}</b></div>
        <button class="btn btn-azul btn-grande" data-verificar>${icono('diana')} Verificar turno</button>
        <div class="rejilla-botones tres">
          <button class="btn btn-borde" data-siguiente>${icono('siguiente')} Siguiente</button>
          <button class="btn btn-borde" data-marcador>${icono('trofeo')} Marcador</button>
          <button class="btn btn-borde" data-reiniciar>${icono('refrescar')} Reiniciar</button>
        </div>
      </section>`;

    const q = (sel) => this.el.querySelector(sel);
    q('[data-form-part]').onsubmit = (e) => { e.preventDefault(); const i = e.target.querySelector('input'); this.agregarParticipante(i.value); };
    this.el.querySelectorAll('[data-part] .p-nombre').forEach((b) => {
      b.onclick = () => { this.s.turno = b.closest('li').dataset.part; this.gResultados.clearLayers(); this._guardar(); };
    });
    this.el.querySelectorAll('[data-quitar-part]').forEach((b) => { b.onclick = () => this.quitarParticipante(b.dataset.quitarPart); });
    this.el.querySelectorAll('[data-quitar-obj]').forEach((b) => { b.onclick = () => this.quitarObjetivo(b.dataset.quitarObj); });
    q('[data-colocar]').onclick = () => { this._setColocando(!this.colocando); this.render(); };
    q('[data-archivo]').onclick = () => this.cargarArchivo();
    q('[data-reto]').onclick = () => this.retoOSM(Math.max(1, Math.min(30, parseInt(q('[data-n-reto]').value, 10) || 5)));
    q('[data-mostrar]').onclick = () => { this.s.mostrar = !this.s.mostrar; this._guardar(); };
    q('[data-exportar]').onclick = () => this.exportarObjetivos();
    q('[data-quitar-todos]').onclick = () => this.quitarObjetivos();
    q('[data-radio]').onchange = (e) => { this.s.radio = Math.max(0.1, parseFloat(e.target.value) || 5); this._guardar(); };
    q('[data-verificar]').onclick = () => this.verificar();
    q('[data-siguiente]').onclick = () => this.siguiente();
    q('[data-marcador]').onclick = () => this.marcador();
    q('[data-reiniciar]').onclick = () => this.reiniciarPuntajes();
  }
}
