# -*- coding: utf-8 -*-
"""
Georreferenciación y colores compartidos por detector_libre.py y detector_juego.py.

Lee config/georreferencia.json y config/colores.json (los mismos archivos que usa la
versión web), ordena las cruces azules, reconoce la flecha de norte y calcula la
homografía cruces → esquinas del área de trabajo.
"""

import datetime
import json
import os
import time

import cv2
import numpy as np

REPO_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_GEO = os.path.join(REPO_DIR, 'config', 'georreferencia.json')
CONFIG_COLORES = os.path.join(REPO_DIR, 'config', 'colores.json')

# Salidas en tiempo real: el mapa web (web/mapa/) las lee desde aquí
SALIDAS_DIR = os.path.join(REPO_DIR, 'salidas')
VIDEOS_DIR = os.path.join(REPO_DIR, 'datos', 'videos')


def _sin_comentarios(d):
    return {k: v for k, v in d.items() if not k.startswith('_')}


def _cargar_config():
    with open(CONFIG_GEO, encoding='utf-8') as fh:
        cfg = json.load(fh)
    return _sin_comentarios(cfg['presets']), _sin_comentarios(cfg['orientaciones'])


PRESETS, ORIENTACIONES = _cargar_config()

# Coordenadas (u, v) normalizadas de cada esquina geográfica: u crece al Este, v al Norte
_UV = {'NW': (0.0, 1.0), 'NE': (1.0, 1.0), 'SE': (1.0, 0.0), 'SW': (0.0, 0.0)}


# ================== Colores de plastilina ==================

def cargar_colores():
    with open(CONFIG_COLORES, encoding='utf-8') as fh:
        return _sin_comentarios(json.load(fh)['colores'])


def guardar_colores(colores):
    """Escribe los rangos calibrados de vuelta en config/colores.json (los usa también la web)."""
    with open(CONFIG_COLORES, encoding='utf-8') as fh:
        cfg = json.load(fh)
    cfg['colores'] = colores
    with open(CONFIG_COLORES, 'w', encoding='utf-8') as fh:
        json.dump(cfg, fh, ensure_ascii=False, indent=2)


def mascara_hsv(hsv, rango):
    """inRange con soporte de tono que da la vuelta (H bajo > H alto, p. ej. el rojo 170→7)."""
    (h_lo, h_hi), (s_lo, s_hi), (v_lo, v_hi) = rango
    if h_lo <= h_hi:
        return cv2.inRange(hsv, np.array([h_lo, s_lo, v_lo], np.uint8), np.array([h_hi, s_hi, v_hi], np.uint8))
    a = cv2.inRange(hsv, np.array([h_lo, s_lo, v_lo], np.uint8), np.array([179, s_hi, v_hi], np.uint8))
    b = cv2.inRange(hsv, np.array([0, s_lo, v_lo], np.uint8), np.array([h_hi, s_hi, v_hi], np.uint8))
    return cv2.bitwise_or(a, b)


def hex_a_bgr(h):
    h = h.lstrip('#')
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    return (b, g, r)


# ================== Cruces y flecha de norte ==================

ELONGACION_FLECHA = 1.7   # largo/ancho mínimo para considerar una figura azul como flecha


def elongacion(contorno):
    (_, _), (w, h), _ = cv2.minAreaRect(contorno)
    return max(w, h) / max(min(w, h), 1e-6)


def direccion_flecha(mascara, contorno):
    """
    Centroide y dirección (vector unitario hacia la punta) de una flecha.
    La punta está en el extremo donde la figura es más ancha (la cabeza del triángulo).
    """
    x, y, w, h = cv2.boundingRect(contorno)
    sub = mascara[y:y + h, x:x + w]
    ys, xs = np.nonzero(sub)
    if len(xs) < 5:
        return None
    pts = np.column_stack([xs + x, ys + y]).astype(np.float64)
    c = pts.mean(axis=0)
    d = pts - c
    cov = d.T @ d / len(d)
    th = 0.5 * np.arctan2(2 * cov[0, 1], cov[0, 0] - cov[1, 1])
    v = np.array([np.cos(th), np.sin(th)])
    vp = np.array([-v[1], v[0]])
    t, perp = d @ v, d @ vp
    tmin, tmax = t.min(), t.max()
    corte = 0.35 * (tmax - tmin)
    fin_pos, fin_neg = t > tmax - corte, t < tmin + corte
    ancho_pos = np.ptp(perp[fin_pos]) if fin_pos.any() else 0
    ancho_neg = np.ptp(perp[fin_neg]) if fin_neg.any() else 0
    return (float(c[0]), float(c[1])), (v if ancho_pos >= ancho_neg else -v)


def ordenar_esquinas(centroids):
    """Ordena los centroides de las cruces en su posición real: [TL, TR, BR, BL]."""
    pts = np.array(centroids, dtype=np.float32)
    s = pts.sum(axis=1)
    d = np.diff(pts, axis=1).ravel()  # y - x
    tl = pts[np.argmin(s)]
    br = pts[np.argmax(s)]
    tr = pts[np.argmin(d)]  # y - x mínimo: arriba a la derecha
    bl = pts[np.argmax(d)]  # y - x máximo: abajo a la izquierda
    return np.array([tl, tr, br, bl], dtype=np.float32)


def esquinas_desde_flecha(corners, flecha):
    """
    Con la flecha de norte (junto a la cruz NE) decide qué esquina geográfica es cada cruz.
    corners: [TL, TR, BR, BL] reales; flecha: (centroide, dirección hacia el norte).
    Resuelve las 4 rotaciones y también el espejo.
    """
    (cx, cy), d = flecha
    corners = np.asarray(corners, dtype=np.float64)
    i_ne = int(np.argmin(np.hypot(corners[:, 0] - cx, corners[:, 1] - cy)))
    a, b = (i_ne - 1) % 4, (i_ne + 1) % 4
    va, vb = corners[a] - corners[i_ne], corners[b] - corners[i_ne]
    # Desde la cruz NE, la esquina SE está hacia el sur (contra la flecha)
    se, nw = (a, b) if va @ d < vb @ d else (b, a)
    esquinas = [None] * 4
    esquinas[i_ne], esquinas[se], esquinas[nw], esquinas[(i_ne + 2) % 4] = 'NE', 'SE', 'NW', 'SW'
    return esquinas


def nombre_esquinas(esquinas):
    """Nombre legible de una asignación de esquinas (si coincide con una orientación conocida)."""
    for v in ORIENTACIONES.values():
        if v.get('esquinas') == list(esquinas):
            return v['nombre']
    return 'Imagen en espejo'


def resolver_esquinas(orientacion, corners=None, flecha=None):
    """
    Devuelve (esquinas, nota). Con 'auto' usa la flecha; si no hay flecha, cae en 'norte_arriba'.
    """
    if orientacion == 'auto':
        if flecha is not None and corners is not None:
            esq = esquinas_desde_flecha(corners, flecha)
            return esq, f'Orientación automática por flecha: {nombre_esquinas(esq)}'
        return ORIENTACIONES['norte_arriba']['esquinas'], 'No se vio la flecha de norte: se usó "Norte arriba"'
    return ORIENTACIONES[orientacion]['esquinas'], ORIENTACIONES[orientacion]['nombre']


def homografia(corners_img, geo_bounds_utm, esquinas):
    """
    Homografía imagen → coordenadas del EPSG origen.
    corners_img: [TL, TR, BR, BL] reales; esquinas: qué esquina geográfica ve cada cruz.
    """
    xmin, ymin, xmax, ymax = geo_bounds_utm
    if isinstance(esquinas, str):
        esquinas = ORIENTACIONES[esquinas]['esquinas']
    dst_pts = np.array([
        [xmin + _UV[e][0] * (xmax - xmin), ymin + _UV[e][1] * (ymax - ymin)] for e in esquinas
    ], dtype=np.float32)
    H, _ = cv2.findHomography(np.asarray(corners_img, dtype=np.float32), dst_pts, method=cv2.RANSAC)
    return H


# ================== Escritura de salidas ==================

def escribir_json(nombre, datos):
    """
    Escritura atómica (archivo temporal + reemplazo) para que el mapa nunca lea un archivo
    a medio escribir. En Windows el reemplazo falla si el mapa lo está leyendo justo en ese
    instante: se reintenta unos milisegundos y, si sigue ocupado, se omite este cuadro.
    """
    tmp = nombre + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as fh:
        json.dump(datos, fh, ensure_ascii=False)
    for _ in range(5):
        try:
            os.replace(tmp, nombre)
            return True
        except PermissionError:
            time.sleep(0.01)
    return False


# ================== Sesión para el mapa web ==================

def escribir_sesion(modo, epsg, bounds, orientacion):
    """
    Escribe salidas/sesion.json para que el mapa web (web/mapa/) se sincronice solo
    con el detector: modo (libre / juego), área de trabajo y orientación.
    Se reescribe cada vez que cambia el área desde la ventana del detector.
    """
    os.makedirs(SALIDAS_DIR, exist_ok=True)
    sesion = {
        'modo': modo,
        'epsg': int(epsg),
        'bounds': [float(v) for v in bounds],
        'orientacion': orientacion,
        'actualizado': datetime.datetime.now().isoformat(timespec='seconds'),
    }
    escribir_json(os.path.join(SALIDAS_DIR, 'sesion.json'), sesion)
