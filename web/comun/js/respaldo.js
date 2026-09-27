// Respaldo de los datos del usuario.
//
// Todo lo que se captura (mapeo guardado con nombres, notas y colores; participantes,
// puntos de control y puntajes del juego; datos OSM consultados) vive SOLO en el
// navegador de este dispositivo (localStorage). No se envía a ningún servidor ni lo ve
// nadie más. Este módulo permite descargarlo a un archivo y cargarlo en otro equipo.
import { descargar, fechaArchivo, elegirArchivo, confirmar, avisar } from './ui.js';

const VERSION = 1;

export function crearRespaldo(mapa, juego) {
  return {
    app: 'mesa3d',
    version: VERSION,
    fecha: new Date().toISOString(),
    mapeo: { type: 'FeatureCollection', features: mapa.guardadas },
    juego: juego.s,
  };
}

export function descargarRespaldo(mapa, juego) {
  descargar(`mesa3d_respaldo_${fechaArchivo()}.json`, crearRespaldo(mapa, juego), 'application/json');
}

export async function cargarRespaldo(mapa, juego) {
  const file = await elegirArchivo('.json');
  if (!file) return;
  let r;
  try {
    r = JSON.parse(await file.text());
    if (r.app !== 'mesa3d') throw new Error('no es un respaldo de Cartografía sensorial');
  } catch (e) {
    return avisar(`No se pudo leer el respaldo: ${e.message}`, 'err');
  }
  const n = r.mapeo?.features?.length || 0;
  const ok = await confirmar('Cargar respaldo',
    `El respaldo del ${new Date(r.fecha).toLocaleString('es-MX')} tiene ${n} elementos de mapeo y ${r.juego?.participantes?.length || 0} participantes. Reemplazará lo que hay ahora en este dispositivo.`,
    'Reemplazar');
  if (!ok) return;
  mapa.reemplazarMapeo(r.mapeo?.features || []);
  if (r.juego) juego.reemplazarEstado(r.juego);
  avisar('Respaldo cargado.', 'ok');
}
