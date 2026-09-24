# -*- coding: utf-8 -*-
"""
Descarga una vez los datos de OpenStreetMap de cada área de config/georreferencia.json
y los guarda en web/comun/datos_osm/<area>.json.

Con esos archivos la web (laptop y celular) muestra el contexto OSM al instante y SIN
depender de los servidores públicos de Overpass, que suelen saturarse (errores 504/429)
con áreas grandes. Para áreas personalizadas la web sigue consultando en vivo.

Uso (desde la raíz del repositorio, con internet):
    python escritorio/herramientas/descargar_osm.py                 # todas las áreas
    python escritorio/herramientas/descargar_osm.py cuenca_valle_mexico

Las consultas son las mismas que usa web/comun/js/osm.js. Datos © colaboradores de
OpenStreetMap, licencia ODbL.
"""

import datetime
import json
import math
import os
import sys
import time
import urllib.parse
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from georreferencia import PRESETS, REPO_DIR  # noqa: E402
from pyproj import Transformer  # noqa: E402

SALIDA = os.path.join(REPO_DIR, 'web', 'comun', 'datos_osm')
SERVIDORES = [
    'https://overpass-api.de/api/interpreter',
    'https://overpass.kumi.systems/api/interpreter',
    'https://maps.mail.ru/osm/tools/overpass/api/interpreter',
]
AGENTE = 'Mesa3D-COMULAB/1.0 (+https://github.com/UrielMendoza/CG_COMULAB_mesa3d)'
AREA_GRANDE_KM2 = 6000
ETIQUETAS = ['name', 'place', 'amenity', 'natural', 'waterway', 'water', 'leisure', 'boundary', 'harbour', 'landuse', 'population']

# Mismas consultas que CATEGORIAS en web/comun/js/osm.js
CONSULTAS = {
    'localidades': lambda bb, g: f'node["place"~"^(city|town|village{"" if g else "|hamlet"})$"]["name"]({bb});',
    'municipios': lambda bb, g: f'relation["boundary"="administrative"]["admin_level"="6"]["name"]({bb});',
    'colonias': lambda bb, g: f'nwr["place"~"^(suburb{"" if g else "|neighbourhood|quarter"})$"]["name"]({bb});',
    'montanas': lambda bb, g: f'node["natural"~"^(peak|volcano)$"]["name"]({bb});',
    'parques': lambda bb, g: f'nwr["leisure"~"^({"nature_reserve" if g else "park|nature_reserve"})$"]["name"]({bb});relation["boundary"~"^(national_park|protected_area)$"]["name"]({bb});',
    'agua': lambda bb, g: f'nwr["natural"="water"]["name"]({bb});way["waterway"~"^(river|canal{"" if g else "|stream"})$"]["name"]({bb});',
    'puertos': lambda bb, g: f'nwr["harbour"]({bb});nwr["leisure"="marina"]({bb});nwr["amenity"="ferry_terminal"]({bb});nwr["landuse"="port"]({bb});',
    'salud': lambda bb, g: f'nwr["amenity"~"^(hospital|clinic|doctors)$"]({bb});',
    'educacion': lambda bb, g: f'nwr["amenity"~"^({"university|college" if g else "school|college|university"})$"]({bb});',
}


def haversine_km(lat1, lon1, lat2, lon2):
    r = math.pi / 180
    a = math.sin((lat2 - lat1) * r / 2) ** 2 + math.cos(lat1 * r) * math.cos(lat2 * r) * math.sin((lon2 - lon1) * r / 2) ** 2
    return 2 * 6371.0088 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def bbox_wgs84(preset):
    t = Transformer.from_crs(preset['epsg'], 4326, always_xy=True)
    xmin, ymin, xmax, ymax = preset['bounds']
    pts = [t.transform(x, y) for x, y in ((xmin, ymin), (xmin, ymax), (xmax, ymax), (xmax, ymin))]
    lons, lats = [p[0] for p in pts], [p[1] for p in pts]
    s, w, n, e = min(lats), min(lons), max(lats), max(lons)
    return [round(v, 5) for v in (s, w, n, e)]


def consultar(q, intentos=3):
    ultimo = None
    for ronda in range(intentos):
        for url in SERVIDORES:
            try:
                datos = urllib.parse.urlencode({'data': q}).encode()
                req = urllib.request.Request(url, data=datos, headers={'User-Agent': AGENTE, 'Accept': 'application/json'})
                with urllib.request.urlopen(req, timeout=200) as r:
                    j = json.load(r)
                if j.get('remark') and 'runtime error' in j['remark'].lower():
                    raise RuntimeError(j['remark'][:80])
                return j.get('elements', [])
            except Exception as e:  # 504, 429, tiempo agotado… se prueba el siguiente
                ultimo = e
                time.sleep(4 + ronda * 6)
    raise RuntimeError(f'sin respuesta de los servidores ({ultimo})')


def descargar(clave):
    preset = PRESETS[clave]
    s, w, n, e = bbox_wgs84(preset)
    area = haversine_km(s, w, s, e) * haversine_km(s, w, n, w)
    grande = area > AREA_GRANDE_KM2
    bb = f'{s},{w},{n},{e}'
    print(f'\n{preset["nombre"]}  ({round(area):,} km², {"grande" if grande else "normal"})')
    salida = {
        'fuente': 'OpenStreetMap vía Overpass API', 'licencia': 'ODbL — © colaboradores de OpenStreetMap',
        'area': clave, 'bbox': [s, w, n, e], 'grande': grande,
        'fecha': datetime.date.today().isoformat(), 'categorias': {},
    }
    for cat, f in CONSULTAS.items():
        q = f'[out:json][timeout:180];({f(bb, grande)});out center tags 6000;'
        t0 = time.time()
        try:
            els = consultar(q)
        except RuntimeError as err:
            print(f'  {cat:12s} ✗ {err}')
            continue
        elementos = [{
            'type': el['type'], 'id': el['id'],
            'lat': round(el.get('lat', el.get('center', {}).get('lat', 0)), 6),
            'lon': round(el.get('lon', el.get('center', {}).get('lon', 0)), 6),
            'tags': {k: el['tags'][k] for k in ETIQUETAS if k in el.get('tags', {})},
        } for el in els if 'lat' in el or 'center' in el]
        salida['categorias'][cat] = {'fecha': salida['fecha'], 'elementos': elementos}
        print(f'  {cat:12s} ✓ {len(elementos):5d} lugares  ({time.time() - t0:.0f} s)')
        time.sleep(2)   # cortesía con el servidor público
    os.makedirs(SALIDA, exist_ok=True)
    ruta = os.path.join(SALIDA, f'{clave}.json')
    with open(ruta, 'w', encoding='utf-8') as fh:
        json.dump(salida, fh, ensure_ascii=False, separators=(',', ':'))
    print(f'  → {os.path.relpath(ruta, REPO_DIR)} ({os.path.getsize(ruta) / 1024:.0f} KB)')


if __name__ == '__main__':
    claves = sys.argv[1:] or list(PRESETS)
    for c in claves:
        if c not in PRESETS:
            print(f'Área desconocida: {c}. Opciones: {", ".join(PRESETS)}')
            continue
        descargar(c)
