// Contenido de las pestañas compartidas por el mapa (laptop) y la app del celular.
import { PRESETS, ORIENTACIONES } from './config.js';
import { CATEGORIAS } from './osm.js';
import { icono } from './iconos.js';
import { esc, avisar, elegirArchivo } from './ui.js';

// ================== Pestaña "Mapeo" ==================
export function panelMapeo(el, mapa) {
  const render = () => {
    const lista = mapa.listaMapeo();
    const nVivo = mapa.vivo.features.length;
    el.innerHTML = `
      <section class="seccion">
        <h3>En vivo <span class="cuenta" data-cuenta-vivo>${nVivo}</span></h3>
        <p class="ayuda">Lo que la cámara ve ahora. Cambia en cuanto se mueve la plastilina; guárdalo para conservarlo y anotarlo.</p>
        <div class="rejilla-botones">
          <button class="btn btn-tinta" data-guardar-todo ${nVivo ? '' : 'disabled'}>${icono('guardar')} Guardar todo</button>
          <button class="btn btn-borde" data-exp-vivo ${nVivo ? '' : 'disabled'}>${icono('descargar')} GeoJSON</button>
        </div>
      </section>
      <section class="seccion">
        <h3>Mapeo guardado <span class="cuenta">${lista.length}</span></h3>
        <p class="ayuda">Toca un elemento en el mapa para ponerle nombre, nota y color. Dibuja a mano con la barra de la derecha del mapa.</p>
        <ul class="lista-mapeo">${lista.slice(0, 60).map((x) => `<li><button data-abrir="${x.id}"><i style="--c:${esc(x.color)}"></i><span><b>${esc(x.nombre)}</b>${x.nota ? `<small>${esc(x.nota)}</small>` : `<small>${x.tipo}</small>`}</span></button></li>`).join('') || '<li class="vacio">Aún no hay nada guardado.</li>'}</ul>
        <div class="fila-botones">
          <button class="btn btn-chico btn-borde" data-exp>${icono('descargar')} Exportar</button>
          <button class="btn btn-chico btn-borde" data-imp>${icono('subir')} Importar</button>
          <button class="btn btn-chico btn-borde" data-borrar>${icono('basura')} Borrar</button>
        </div>
      </section>
      <section class="seccion">
        <h3>Capas de apoyo</h3>
        <button class="btn btn-borde ancho" data-capa>${icono('capas')} Cargar capa (GeoJSON, KML, GPX)</button>
        <div class="imagen-fondo">
          <button class="btn btn-borde ancho" data-imagen>${icono('archivo')} Imagen de fondo sobre el encuadre</button>
          <label class="fila-campo">Opacidad <input type="range" min="0" max="1" step="0.05" value="0.85" data-opacidad></label>
          <button class="btn btn-chico btn-borde" data-quitar-imagen>Quitar imagen</button>
        </div>
        <p class="ayuda">La imagen (por ejemplo la misma impresión satelital) se estira a las esquinas del área; sirve también sin internet.</p>
      </section>`;
    const q = (s) => el.querySelector(s);
    q('[data-guardar-todo]').onclick = () => mapa.guardarTodasLasDetecciones();
    q('[data-exp-vivo]').onclick = () => mapa.exportarVivo();
    q('[data-exp]').onclick = () => mapa.exportarMapeo();
    q('[data-imp]').onclick = async () => { const f = await elegirArchivo('.geojson,.json,.kml,.gpx'); if (f) mapa.importarMapeo(f); };
    q('[data-borrar]').onclick = () => mapa.borrarMapeo();
    q('[data-capa]').onclick = async () => { const f = await elegirArchivo('.geojson,.json,.kml,.gpx'); if (f) mapa.cargarCapa(f); };
    q('[data-imagen]').onclick = async () => { const f = await elegirArchivo('image/*'); if (f) mapa.cargarImagen(f); };
    q('[data-opacidad]').oninput = (e) => mapa.setOpacidadImagen(Number(e.target.value));
    q('[data-quitar-imagen]').onclick = () => mapa.quitarImagen();
    el.querySelectorAll('[data-abrir]').forEach((b) => { b.onclick = () => mapa.abrir(b.dataset.abrir); });
  };
  // Actualización ligera: solo el contador y los botones de "En vivo"
  const vivo = () => {
    const n = mapa.vivo.features.length;
    const c = el.querySelector('[data-cuenta-vivo]');
    if (!c) return;
    c.textContent = n;
    el.querySelector('[data-guardar-todo]').disabled = !n;
    el.querySelector('[data-exp-vivo]').disabled = !n;
  };
  render();
  return { render, vivo };
}

// ================== Pestaña "OSM" ==================
export function panelOSM(el, osm, mapa) {
  let estado = '';
  const render = () => {
    const r = osm.resumen();
    const area = osm.areaKm2 ? `${Math.round(osm.areaKm2).toLocaleString('es-MX')} km²` : '—';
    el.innerHTML = `
      <section class="seccion osm-intro">
        <div class="osm-sello">${icono('globo')}<span>OpenStreetMap</span></div>
        <p>El mapa colaborativo del mundo, hecho por personas como las de tu comunidad. Consulta sus datos dentro del área de trabajo (${area}) para saber qué hay cerca de cada pieza de plastilina.</p>
        <button class="btn btn-osm ancho" data-cargar>${icono('refrescar')} ${osm.cargado ? 'Volver a consultar' : 'Consultar datos OSM del área'}</button>
        ${estado ? `<p class="ayuda estado-osm">${esc(estado)}</p>` : ''}
        ${osm.fecha ? `<p class="ayuda">Datos guardados en este dispositivo el ${new Date(osm.fecha).toLocaleDateString('es-MX', { day: 'numeric', month: 'long', year: 'numeric' })}: funcionan sin internet.</p>` : ''}
      </section>
      <section class="seccion">
        <h3>Capas de OpenStreetMap</h3>
        <ul class="leyenda-osm">${Object.entries(CATEGORIAS).map(([k, c]) => `
          <li><label><input type="checkbox" data-cat="${k}" ${osm.visibles[k] ? 'checked' : ''}><i style="--c:${c.color}"></i>${c.nombre}</label><span>${r[k] != null ? r[k].toLocaleString('es-MX') : '—'}</span></li>`).join('')}</ul>
        ${osm.cargado ? `<p class="ayuda">Toca cualquier pieza mapeada para ver la localidad, el servicio de salud, la escuela y el agua más cercanos. El GeoJSON exportado incluye esas columnas (osm_localidades, osm_salud…).</p>` : ''}
      </section>
      <section class="seccion">
        <h3>Buscar un lugar</h3>
        <input type="search" placeholder="${osm.cargado ? 'Nombre de localidad, escuela, río…' : 'Primero consulta los datos OSM'}" data-buscar ${osm.cargado ? '' : 'disabled'}>
        <ul class="resultados-busqueda" data-resultados></ul>
      </section>
      <p class="credito-osm">Datos © colaboradores de <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noopener">OpenStreetMap</a>, licencia ODbL. ¿Falta algo en tu comunidad? <a href="https://www.openstreetmap.org/fixthemap" target="_blank" rel="noopener">Agrégalo a OSM</a>.</p>`;
    const q = (s) => el.querySelector(s);
    q('[data-cargar]').onclick = async () => {
      const b = q('[data-cargar]');
      b.disabled = true;
      try {
        const res = await osm.cargar(undefined, (m) => { estado = m; b.textContent = m; }, osm.cargado);
        const total = Object.values(res).reduce((a, n) => a + n, 0);
        estado = `${total.toLocaleString('es-MX')} elementos de OSM en el área.`;
        avisar(estado, 'ok');
      } catch (e) {
        estado = e.message;
        avisar(e.message, 'err');
      }
      render();
    };
    el.querySelectorAll('[data-cat]').forEach((c) => { c.onchange = () => osm.setVisible(c.dataset.cat, c.checked); });
    const buscar = q('[data-buscar]');
    buscar.oninput = () => {
      const res = osm.buscar(buscar.value);
      q('[data-resultados]').innerHTML = res.map((x, i) => `<li><button data-i="${i}"><i style="--c:${CATEGORIAS[x.cat].color}"></i>${esc(x.nombre)}</button></li>`).join('');
      q('[data-resultados]').querySelectorAll('button').forEach((b) => {
        b.onclick = () => { const x = res[+b.dataset.i]; mapa.map.setView([x.lat, x.lng], 14); };
      });
    };
  };
  osm.alCambiar = render;
  render();
  return render;
}

// ================== Formulario del área de trabajo ==================
// estado: { preset, epsg, bounds, orientacion? }; alAplicar(nuevoEstado)
export function formularioArea(el, estado, alAplicar, { conOrientacion = false, extra = '' } = {}) {
  const opcionesPreset = Object.entries(PRESETS).map(([k, v]) => `<option value="${k}">${esc(v.nombre)}</option>`).join('') + '<option value="personalizado">Personalizado…</option>';
  const opcionesOri = Object.entries(ORIENTACIONES).map(([k, v]) => `<option value="${k}">${esc(v.nombre)}</option>`).join('');
  el.innerHTML = `
    <label class="campo">Área predefinida <select data-preset>${opcionesPreset}</select></label>
    <div class="rejilla-2">
      <label class="campo">EPSG <input type="number" data-epsg></label>
      ${conOrientacion ? `<label class="campo">Orientación <select data-ori>${opcionesOri}</select></label>` : '<span></span>'}
      <label class="campo">xmin (oeste) <input type="number" step="any" data-b="0"></label>
      <label class="campo">ymin (sur) <input type="number" step="any" data-b="1"></label>
      <label class="campo">xmax (este) <input type="number" step="any" data-b="2"></label>
      <label class="campo">ymax (norte) <input type="number" step="any" data-b="3"></label>
    </div>
    ${extra}
    <button class="btn btn-tinta ancho" data-aplicar>Aplicar área</button>
    <p class="ayuda">Coordenadas de las esquinas donde van las cruces azules, en metros del EPSG indicado. Se editan en <code>config/georreferencia.json</code> para agregar áreas nuevas.</p>`;
  const q = (s) => el.querySelector(s);
  const llenar = (e) => {
    q('[data-preset]').value = PRESETS[e.preset] ? e.preset : 'personalizado';
    q('[data-epsg]').value = e.epsg;
    el.querySelectorAll('[data-b]').forEach((i) => { i.value = e.bounds[+i.dataset.b]; });
    if (conOrientacion) q('[data-ori]').value = e.orientacion;
  };
  llenar(estado);
  q('[data-preset]').onchange = (e) => {
    const pr = PRESETS[e.target.value];
    if (pr) llenar({ ...estado, preset: e.target.value, epsg: pr.epsg, bounds: pr.bounds, orientacion: conOrientacion ? q('[data-ori]').value : estado.orientacion });
  };
  q('[data-aplicar]').onclick = () => {
    const bounds = [...el.querySelectorAll('[data-b]')].map((i) => parseFloat(i.value));
    const epsg = parseInt(q('[data-epsg]').value, 10);
    if (bounds.some((v) => !Number.isFinite(v)) || !Number.isFinite(epsg) || bounds[0] >= bounds[2] || bounds[1] >= bounds[3]) {
      return avisar('Revisa las coordenadas: xmin < xmax y ymin < ymax.', 'err');
    }
    let preset = q('[data-preset]').value;
    const pr = PRESETS[preset];
    if (!pr || pr.epsg !== epsg || pr.bounds.some((v, i) => v !== bounds[i])) preset = 'personalizado';
    alAplicar({ preset, epsg, bounds, orientacion: conOrientacion ? q('[data-ori]').value : estado.orientacion });
  };
  return llenar;
}
