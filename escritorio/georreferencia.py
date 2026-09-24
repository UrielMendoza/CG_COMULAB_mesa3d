# -*- coding: utf-8 -*-
"""
Georreferenciación compartida por detector_libre.py y detector_juego.py.

Lee los presets y las orientaciones de config/georreferencia.json (el mismo archivo
que usa la versión web) y calcula la homografía cruces → esquinas del encuadre.
"""

import json
import os

import cv2
import numpy as np

REPO_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_FILE = os.path.join(REPO_DIR, 'config', 'georreferencia.json')

# Salidas en tiempo real: el mapa web (web/mapa/) las lee desde aquí
SALIDAS_DIR = os.path.join(REPO_DIR, 'salidas')
VIDEOS_DIR = os.path.join(REPO_DIR, 'datos', 'videos')


def _cargar_config():
    with open(CONFIG_FILE, encoding='utf-8') as fh:
        cfg = json.load(fh)
    presets = {k: v for k, v in cfg['presets'].items() if not k.startswith('_')}
    orientaciones = {k: v for k, v in cfg['orientaciones'].items() if not k.startswith('_')}
    return presets, orientaciones


PRESETS, ORIENTACIONES = _cargar_config()

# Coordenadas (u, v) normalizadas de cada esquina geográfica: u crece al Este, v al Norte
_UV = {'NW': (0.0, 1.0), 'NE': (1.0, 1.0), 'SE': (1.0, 0.0), 'SW': (0.0, 0.0)}


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


def homografia(corners_img, geo_bounds_utm, orientacion='norte_arriba'):
    """
    Homografía imagen → coordenadas del EPSG origen.
    corners_img: [TL, TR, BR, BL] reales (ver ordenar_esquinas).
    orientacion: clave de ORIENTACIONES (qué esquina del mapa ve cada cruz).
    """
    xmin, ymin, xmax, ymax = geo_bounds_utm
    esquinas = ORIENTACIONES[orientacion]['esquinas']
    dst_pts = np.array([
        [xmin + _UV[e][0] * (xmax - xmin), ymin + _UV[e][1] * (ymax - ymin)] for e in esquinas
    ], dtype=np.float32)
    H, _ = cv2.findHomography(corners_img, dst_pts, method=cv2.RANSAC)
    return H


def escribir_sesion(modo, epsg, bounds, orientacion):
    """
    Escribe salidas/sesion.json para que el mapa web (web/mapa/) se sincronice solo
    con el detector: modo (libre / juego), área de trabajo y orientación.
    """
    import datetime
    os.makedirs(SALIDAS_DIR, exist_ok=True)
    sesion = {
        'modo': modo,
        'epsg': int(epsg),
        'bounds': [float(v) for v in bounds],
        'orientacion': orientacion,
        'inicio': datetime.datetime.now().isoformat(timespec='seconds'),
    }
    with open(os.path.join(SALIDAS_DIR, 'sesion.json'), 'w', encoding='utf-8') as fh:
        json.dump(sesion, fh, ensure_ascii=False, indent=1)
