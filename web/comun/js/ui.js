// Utilidades de interfaz compartidas: avisos, diálogos, editor de propiedades, descargas.
import { icono } from './iconos.js';
import { PALETA } from './config.js';

export const $ = (id) => document.getElementById(id);
export const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
export const fmtKm = (km) => (km < 1 ? `${Math.round(km * 1000)} m` : `${km.toFixed(km < 10 ? 1 : 0)} km`);

// ================== Avisos ==================
let tAviso = null;
export function avisar(msg, tipo = '') {
  let el = $('aviso');
  if (!el) {
    el = document.createElement('div');
    el.id = 'aviso';
    el.className = 'aviso';
    el.setAttribute('role', 'status');
    el.setAttribute('aria-live', 'polite');
    document.body.appendChild(el);
  }
  el.textContent = msg;
  el.className = `aviso visible ${tipo}`;
  clearTimeout(tAviso);
  tAviso = setTimeout(() => { el.className = 'aviso'; }, 3400);
}

// ================== Diálogos ==================
// Crea un modal con `html` en el cuerpo. Devuelve { el, cerrar }.
export function modal(titulo, html, { ancho = 420 } = {}) {
  const ov = document.createElement('div');
  ov.className = 'modal';
  ov.innerHTML = `<div class="modal-card" style="width:min(${ancho}px,100%)" role="dialog" aria-modal="true">
      <div class="modal-cab"><h2>${esc(titulo)}</h2><button class="btn-icono" data-cerrar aria-label="Cerrar">${icono('cerrar')}</button></div>
      <div class="modal-cuerpo">${html}</div></div>`;
  const cerrar = () => { ov.remove(); document.removeEventListener('keydown', teclado); };
  const teclado = (e) => { if (e.key === 'Escape') cerrar(); };
  ov.addEventListener('click', (e) => { if (e.target === ov || e.target.closest('[data-cerrar]')) cerrar(); });
  document.addEventListener('keydown', teclado);
  document.body.appendChild(ov);
  const primero = ov.querySelector('input, textarea, select');
  if (primero) setTimeout(() => primero.focus(), 30);
  return { el: ov, cerrar };
}

export function pedirTexto(titulo, etiqueta, valor = '') {
  return new Promise((ok) => {
    const { el, cerrar } = modal(titulo, `
      <label class="campo">${esc(etiqueta)}<input type="text" value="${esc(valor)}"></label>
      <div class="acciones-modal"><button class="btn btn-borde" data-no>Cancelar</button><button class="btn btn-tinta" data-si>Aceptar</button></div>`);
    const inp = el.querySelector('input');
    const fin = (v) => { cerrar(); ok(v); };
    el.querySelector('[data-no]').onclick = () => fin(null);
    el.querySelector('[data-si]').onclick = () => fin(inp.value.trim());
    inp.onkeydown = (e) => { if (e.key === 'Enter') fin(inp.value.trim()); };
  });
}

export function confirmar(titulo, texto, si = 'Aceptar') {
  return new Promise((ok) => {
    const { el, cerrar } = modal(titulo, `<p>${esc(texto)}</p>
      <div class="acciones-modal"><button class="btn btn-borde" data-no>Cancelar</button><button class="btn btn-peligro" data-si>${esc(si)}</button></div>`);
    el.querySelector('[data-no]').onclick = () => { cerrar(); ok(false); };
    el.querySelector('[data-si]').onclick = () => { cerrar(); ok(true); };
  });
}

// Editor de nombre, nota y color de un elemento del mapa. Devuelve las nuevas propiedades o null.
export function editarPropiedades(props) {
  return new Promise((ok) => {
    const color = props.color || PALETA[0];
    const { el, cerrar } = modal('Editar elemento', `
      <label class="campo">Nombre<input type="text" data-nombre value="${esc(props.nombre || '')}" placeholder="Ej. Pozo de agua, escuela, zona de riesgo…"></label>
      <label class="campo">Nota<textarea rows="4" data-nota placeholder="Lo que la comunidad dijo sobre este lugar">${esc(props.nota || '')}</textarea></label>
      <div class="campo">Color
        <div class="paleta">${PALETA.map((c) => `<button type="button" class="muestra${c === color ? ' activa' : ''}" data-color="${c}" style="--c:${c}" aria-label="Color ${c}"></button>`).join('')}
          <label class="muestra muestra-libre" title="Otro color"><input type="color" value="${esc(color)}"></label>
        </div>
      </div>
      <div class="acciones-modal"><button class="btn btn-borde" data-no>Cancelar</button><button class="btn btn-tinta" data-si>Guardar</button></div>`);
    let elegido = color;
    const marcar = (c) => {
      elegido = c;
      el.querySelectorAll('.muestra').forEach((b) => b.classList.toggle('activa', b.dataset.color === c));
    };
    el.querySelectorAll('.muestra[data-color]').forEach((b) => { b.onclick = () => marcar(b.dataset.color); });
    el.querySelector('input[type=color]').oninput = (e) => marcar(e.target.value);
    el.querySelector('[data-no]').onclick = () => { cerrar(); ok(null); };
    el.querySelector('[data-si]').onclick = () => {
      const r = { ...props, nombre: el.querySelector('[data-nombre]').value.trim(), nota: el.querySelector('[data-nota]').value.trim(), color: elegido };
      cerrar();
      ok(r);
    };
  });
}

// ================== Archivos ==================
export function descargar(nombre, contenido, tipo = 'application/geo+json') {
  const blob = new Blob([typeof contenido === 'string' ? contenido : JSON.stringify(contenido, null, 2)], { type: tipo });
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = nombre;
  a.click();
  setTimeout(() => URL.revokeObjectURL(a.href), 1500);
}

export function elegirArchivo(accept) {
  return new Promise((ok) => {
    const inp = document.createElement('input');
    inp.type = 'file';
    inp.accept = accept;
    inp.onchange = () => ok(inp.files[0] || null);
    inp.click();
  });
}

export const fechaArchivo = () => new Date().toISOString().slice(0, 16).replace(/[:T]/g, '-');

// Guarda/lee JSON en localStorage sin romper en modo privado
export const almacen = {
  leer(k, def) { try { const v = JSON.parse(localStorage.getItem(k)); return v ?? def; } catch (_) { return def; } },
  guardar(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch (_) { /* sin espacio o modo privado */ } },
};

// Pestañas simples: contenedor con [data-pestana] botones y [data-contenido] secciones
export function pestanas(raiz, alCambiar) {
  const botones = raiz.querySelectorAll('[data-pestana]');
  const activar = (id) => {
    botones.forEach((b) => b.setAttribute('aria-selected', String(b.dataset.pestana === id)));
    raiz.querySelectorAll('[data-contenido]').forEach((s) => { s.hidden = s.dataset.contenido !== id; });
    if (alCambiar) alCambiar(id);
  };
  botones.forEach((b) => { b.onclick = () => activar(b.dataset.pestana); });
  return activar;
}
