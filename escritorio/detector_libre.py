# -*- coding: utf-8 -*-
"""
Detector de MAPEO LIBRE (Tkinter + OpenCV) — versión laptop.

Detecta plastilina de colores (rojo, naranja, amarillo, verde, morado, café; blanco y negro
opcionales) sobre la mesa / mapa / modelo 3D, la clasifica en punto, línea o polígono y la
georreferencia con 4 cruces AZULES en las esquinas. Una flecha azul apuntando al norte,
junto a la cruz de arriba a la derecha del mapa, permite orientar automáticamente.

Salida (se sobrescribe en cada cuadro mientras hay calibración):
    salidas/detecciones_puntos.geojson
    salidas/detecciones_lineas.geojson
    salidas/detecciones_poligonos.geojson
    salidas/sesion.json            (modo y área, para que el mapa se sincronice)
El botón "Abrir mapa" levanta un servidor local y abre web/mapa/ en el navegador.

Historial: v6 calibración manual con cruces azules y cámara USB; v7 presets de la Cuenca
del Valle de México; v8 presets compartidos, orientación y salidas/; v9 área editable en
vivo, flecha de norte, paleta completa de plastilina, botón de mapa y nuevo estilo.
"""

import os
import threading
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

import cv2
import numpy as np
from pyproj import Transformer

from georreferencia import (PRESETS, ORIENTACIONES, SALIDAS_DIR, VIDEOS_DIR, ordenar_esquinas, homografia,
                            resolver_esquinas, escribir_sesion, escribir_json, direccion_flecha, elongacion, ELONGACION_FLECHA,
                            cargar_colores, guardar_colores, mascara_hsv, hex_a_bgr)
from estilo import aplicar_estilo, boton, C
import servidor_mapa

# ================== CONFIG DEFAULT ==================

DEFAULT_PRESET = "cuenca_valle_mexico"
# 'norte_arriba' reproduce exactamente el mapeo de la v7; 'auto' usa la flecha de norte si la ve
DEFAULT_ORIENTACION = "auto"

DEFAULT_VIDEO_FILE = os.path.join(VIDEOS_DIR, "20250327_130515_referencia.mp4")
DEFAULT_VIDEO_SPEED_MS = 25

# Ventanas: 0 = ninguna, 1 = solo recorte, 2 = todas
DEFAULT_SHOW_MODE = 1
DEFAULT_SCALE = 1.0
DEFAULT_RECALIB_EVERY = 0  # 0 = calibración manual

# Cruces azules (fijas en esta versión; el azul no se usa para plastilina)
BLUE_RANGE = [[100, 130], [80, 255], [80, 255]]

DEFAULT_PARAMS = {
    "MIN_AREA_POINT": 5,
    "MIN_AREA_LINE": 30,
    "MIN_LINE_LENGTH": 20,   # px
    "MIN_LINE_ASPECT": 4.0,  # largo/ancho
    "MIN_AREA_POLY": 150,
    "K_LONG": 11,
    "K_SHORT": 3,
    "MORPH_ITERS": 1,
}

# ================== Núcleo de detección ==================

def init_transformer(epsg_src, epsg_dst=4326):
    return Transformer.from_crs(epsg_src, epsg_dst, always_xy=True)

def find_blue_cross_corners(frame):
    """
    Detecta las 4 cruces AZULES en las esquinas del área de trabajo y, si existe,
    la flecha azul de norte (figura alargada). Retorna esquinas [TL, TR, BR, BL],
    el recorte y un diccionario con la máscara, los centroides y la flecha.
    """
    blurred = cv2.GaussianBlur(frame, (3, 3), 0)
    hsv = cv2.cvtColor(blurred, cv2.COLOR_BGR2HSV)
    mask = mascara_hsv(hsv, BLUE_RANGE)

    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel)
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    centroids = []
    flecha, area_flecha = None, 0
    for c in contours:
        area = cv2.contourArea(c)
        if area < 20:
            continue
        if area >= 60 and elongacion(c) >= ELONGACION_FLECHA:
            if area > area_flecha:
                flecha, area_flecha = direccion_flecha(mask, c), area
            continue
        x, y, w, h = cv2.boundingRect(c)
        ar = w / h if h > 0 else 0
        if ar < 0.3 or ar > 3.5:
            continue
        M = cv2.moments(c)
        if M["m00"] > 0:
            centroids.append((M["m10"] / M["m00"], M["m01"] / M["m00"]))

    dbg = {"mask_blue": mask, "centroids": centroids, "flecha": flecha}
    if len(centroids) < 4:
        return None, None, dbg

    corners = ordenar_esquinas(centroids)
    xs, ys = corners[:, 0], corners[:, 1]
    pad = 5
    xmin = max(0, int(np.floor(xs.min())) - pad)
    ymin = max(0, int(np.floor(ys.min())) - pad)
    xmax = min(frame.shape[1] - 1, int(np.ceil(xs.max())) + pad)
    ymax = min(frame.shape[0] - 1, int(np.ceil(ys.max())) + pad)
    return corners, (xmin, ymin, xmax, ymax), dbg

def compute_homography_from_corners(corners_img, geo_bounds_utm, orientacion="norte_arriba", flecha=None):
    esquinas, _ = resolver_esquinas(orientacion, corners_img, flecha)
    return homografia(corners_img, geo_bounds_utm, esquinas)

def raster_to_geo_homography(cx, cy, H, transformer):
    pt = np.array([[cx, cy]], dtype=np.float32)
    pt_h = cv2.perspectiveTransform(np.array([pt]), H)[0][0]
    xutm, yutm = float(pt_h[0]), float(pt_h[1])
    lon, lat = transformer.transform(xutm, yutm)
    return (lon, lat)

def refine_mask_for_lines(mask, k_long=11, k_short=3, iters=1):
    k_long = max(3, int(k_long) | 1)   # impar ≥3
    k_short = max(3, int(k_short) | 1)
    k_h = cv2.getStructuringElement(cv2.MORPH_RECT, (k_long, 1))
    k_v = cv2.getStructuringElement(cv2.MORPH_RECT, (1, k_long))
    k_s = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k_short, k_short))
    m = cv2.morphologyEx(mask, cv2.MORPH_OPEN, k_s, iterations=1)
    m_h = cv2.morphologyEx(m, cv2.MORPH_CLOSE, k_h, iterations=iters)
    m_v = cv2.morphologyEx(m, cv2.MORPH_CLOSE, k_v, iterations=iters)
    return cv2.bitwise_or(m_h, m_v)

def contour_line_metrics(cnt):
    if len(cnt) < 2:
        return 0.0, 0.0, 0.0
    [vx, vy, x0, y0] = cv2.fitLine(cnt, cv2.DIST_L2, 0, 0.01, 0.01)
    v = np.array([vx, vy], dtype=np.float32).reshape(2)
    p0 = np.array([x0, y0], dtype=np.float32).reshape(2)
    pts = cnt.reshape(-1, 2).astype(np.float32)
    t = (pts - p0) @ v
    length = float(t.max() - t.min())
    vp = np.array([-v[1], v[0]], dtype=np.float32)
    w = np.abs(((pts - p0) @ vp)).mean() * 2.0
    width = float(max(w, 1e-6))
    aspect = float(length / width)
    return length, width, aspect

def draw_small(window_name, frame, scale):
    h, w = frame.shape[:2]
    resized = cv2.resize(frame, (max(1, int(w * scale)), max(1, int(h * scale))))
    cv2.imshow(window_name, resized)

def guardar_geojson(datos, tipo_geom, nombre):
    """
    Escribe SIEMPRE el archivo (vacío si no hay nada de ese tipo), para que el mapa
    no se quede con posiciones viejas cuando una pieza desaparece o cambia el área.
    """
    features = [{
        "type": "Feature",
        "properties": {"color": d['color'], "type": d['type']},
        "geometry": d['geometry'],
    } for d in datos if d['geometry'] is not None and d['geometry']['type'] == tipo_geom]
    try:
        escribir_json(nombre, {"type": "FeatureCollection", "features": features})
    except OSError as e:
        print(f"[WARN] No se pudo escribir {nombre}: {e}")

# ================== Pipeline principal ==================

class LiveParams:
    """Parámetros compartidos entre la ventana y el hilo de video, con lectura segura."""
    def __init__(self, init_dict):
        self._lock = threading.Lock()
        self._d = dict(init_dict)
    def update(self, **kwargs):
        with self._lock:
            self._d.update(kwargs)
    def get(self):
        with self._lock:
            return dict(self._d)

def process_frame_generic(frame_in, transformer, H, xoff, yoff, show_mode, scale, params, colores):
    """
    Procesa un frame (recortado o completo) para cada color de plastilina activo.
    - Si H es None => SIN georreferencia: dibuja y cuenta, pero 'geometry' va en None (no se guarda).
    - Si H existe => georreferencia normal.
    """
    detections = []
    blurred = cv2.GaussianBlur(frame_in, (3, 3), 0)
    hsv = cv2.cvtColor(blurred, cv2.COLOR_BGR2HSV)
    stats = {}

    for color_id, color in colores.items():
        if not color.get('activo'):
            continue
        stats[color_id] = {'point': 0, 'line': 0, 'polygon': 0}
        bgr = hex_a_bgr(color['hex'])
        base_mask = mascara_hsv(hsv, color['hsv'])
        mask_ref = refine_mask_for_lines(base_mask, params["K_LONG"], params["K_SHORT"], params["MORPH_ITERS"])
        contours, _ = cv2.findContours(mask_ref, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

        for contour in contours:
            area = cv2.contourArea(contour)
            if area < params["MIN_AREA_POINT"]:
                continue

            epsilon = 0.01 * cv2.arcLength(contour, True)
            approx = cv2.approxPolyDP(contour, epsilon, True)
            rect = cv2.minAreaRect(approx)
            (x, y), (w2, h2), angle = rect
            aspect_ratio = max(w2, h2) / max(min(w2, h2), 1e-6)

            geo_points = []
            if H is not None:
                for point in approx:
                    cx, cy = point[0]
                    geo_points.append(raster_to_geo_homography(cx + xoff, cy + yoff, H, transformer))

            if H is None or len(geo_points) < 2:
                geom_type = 'point'
                geometry = None if H is None or not geo_points else {"type": "Point", "coordinates": list(geo_points[0])}
            else:
                length, width, line_aspect = contour_line_metrics(approx)
                if (area >= params["MIN_AREA_LINE"] and line_aspect >= params["MIN_LINE_ASPECT"]
                        and max(w2, h2) >= params["MIN_LINE_LENGTH"]):
                    geom_type = 'line'
                    geometry = {"type": "LineString", "coordinates": [list(p) for p in geo_points]}
                elif area > params["MIN_AREA_POLY"] and aspect_ratio < 2.0:
                    geom_type = 'polygon'
                    anillo = [list(p) for p in geo_points]
                    if anillo[0] != anillo[-1]:
                        anillo.append(anillo[0])
                    geometry = {"type": "Polygon", "coordinates": [anillo]} if len(anillo) >= 4 else None
                else:
                    geom_type = 'point'
                    geometry = {"type": "Point", "coordinates": list(geo_points[0])}

            if show_mode >= 1:
                if geom_type == 'line':
                    cv2.drawContours(frame_in, [cv2.boxPoints(rect).astype(int)], 0, bgr, 2)
                elif geom_type == 'polygon':
                    cv2.drawContours(frame_in, [approx], -1, bgr, 2)
                else:
                    cv2.circle(frame_in, (int(x), int(y)), 6, (20, 20, 20), -1)
                    cv2.circle(frame_in, (int(x), int(y)), 4, bgr, -1)

            stats[color_id][geom_type] += 1
            detections.append({"geometry": geometry, "color": color_id, "type": geom_type})

    if show_mode >= 1:
        display_frame = frame_in.copy()
        y_pos = 22
        for color_id, s in stats.items():
            bgr = hex_a_bgr(colores[color_id]['hex'])
            cv2.rectangle(display_frame, (10, y_pos - 11), (22, y_pos + 1), bgr, -1)
            cv2.rectangle(display_frame, (10, y_pos - 11), (22, y_pos + 1), (255, 255, 255), 1)
            cv2.putText(display_frame, f"{colores[color_id]['nombre'].upper()}  P:{s['point']} L:{s['line']} POL:{s['polygon']}",
                        (30, y_pos), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (255, 255, 255), 1, cv2.LINE_AA)
            y_pos += 20
        draw_small("SISTEMA DE DETECCION (recorte)", display_frame, scale)

    return detections

def list_available_cameras(max_test=10):
    """Detecta cámaras disponibles probando índices."""
    available = []
    for i in range(max_test):
        cap = cv2.VideoCapture(i)
        if cap.isOpened():
            ret, _ = cap.read()
            if ret:
                available.append(i)
            cap.release()
    return available

def run_pipeline(video_source, src_type, show_mode, scale, video_speed, recalib_every,
                 live_params: LiveParams, geo_live: LiveParams, estado: LiveParams,
                 pause_event: threading.Event, recalib_event: threading.Event, stop_event: threading.Event):
    """
    Bucle de video. El área (EPSG, esquinas, orientación) se lee de `geo_live` en cada
    cuadro: si cambia desde la ventana, la homografía se recalcula al instante con las
    últimas cruces vistas, sin necesidad de reiniciar.
    """
    os.makedirs(SALIDAS_DIR, exist_ok=True)

    if src_type == 'camera':
        cap = cv2.VideoCapture(int(video_source))
    else:
        cap = cv2.VideoCapture(video_source)
    if not cap.isOpened():
        estado.update(texto=f"No se pudo abrir la fuente de video: {video_source}", ok=False)
        return

    H = None
    crop_box = None
    ultimas_cruces, ultima_flecha = None, None
    geo_version, transformer, geo = -1, None, None
    frame_count = 0
    try:
        while not stop_event.is_set():
            ret, frame = cap.read()
            if not ret:
                if src_type in ('camera', 'url'):
                    cap.release()
                    cap = cv2.VideoCapture(int(video_source) if src_type == 'camera' else video_source)
                    continue
                break
            frame_count += 1

            # ¿Cambió el área desde la ventana? → nuevo transformador, sesión y homografía
            g = geo_live.get()
            if g['version'] != geo_version:
                geo_version, geo = g['version'], g
                transformer = init_transformer(geo['epsg'])
                escribir_sesion('libre', geo['epsg'], geo['bounds'], geo['orientacion'])
                if ultimas_cruces is not None:
                    esquinas, nota = resolver_esquinas(geo['orientacion'], ultimas_cruces, ultima_flecha)
                    H = homografia(ultimas_cruces, geo['bounds'], esquinas)
                    estado.update(texto=f"Área actualizada. {nota}.", ok=True)

            need_recalib = recalib_every > 0 and frame_count % recalib_every == 0
            if recalib_event.is_set():
                need_recalib = True
                recalib_event.clear()

            if need_recalib:
                corners_img2, crop_box2, dbg = find_blue_cross_corners(frame)
                if corners_img2 is not None:
                    esquinas, nota = resolver_esquinas(geo['orientacion'], corners_img2, dbg['flecha'])
                    H2 = homografia(corners_img2, geo['bounds'], esquinas)
                    if H2 is not None:
                        H, crop_box = H2, crop_box2
                        ultimas_cruces, ultima_flecha = corners_img2, dbg['flecha']
                        estado.update(texto=f"Calibrado con 4 cruces. {nota}.", ok=True)
                        if show_mode == 2:
                            dbg_frame = frame.copy()
                            for (x, y) in corners_img2.astype(int):
                                cv2.drawMarker(dbg_frame, (x, y), (126, 237, 53), markerType=cv2.MARKER_CROSS, markerSize=18, thickness=2)
                            if dbg['flecha']:
                                (fx, fy), d = dbg['flecha']
                                cv2.arrowedLine(dbg_frame, (int(fx), int(fy)), (int(fx + d[0] * 60), int(fy + d[1] * 60)), (126, 237, 53), 3)
                            draw_small("Cruces Azules Detectadas", dbg_frame, scale)
                            draw_small("Mascara Azul (cruces)", cv2.cvtColor(dbg["mask_blue"], cv2.COLOR_GRAY2BGR), scale)
                            cv2.waitKey(150)
                else:
                    estado.update(texto=f"Se ven {len(dbg['centroids'])} de 4 cruces azules. Revisa la luz o el encuadre.", ok=False)

            if H is not None and crop_box is not None:
                xmin, ymin, xmax, ymax = crop_box
                frame_in = frame[ymin:ymax, xmin:xmax].copy()
                xoff, yoff = xmin, ymin
                calibrated = True
            else:
                frame_in = frame.copy()
                xoff, yoff = 0, 0
                calibrated = False

            if pause_event.is_set():
                if show_mode >= 1:
                    overlay = frame_in.copy()
                    cv2.putText(overlay, "PAUSADO", (20, 40), cv2.FONT_HERSHEY_SIMPLEX, 1.1, (0, 0, 255), 3, cv2.LINE_AA)
                    draw_small("SISTEMA DE DETECCION (recorte)", overlay, scale)
                if cv2.waitKey(video_speed) & 0xFF == ord('q'):
                    break
                continue

            params_now = live_params.get()
            detections = process_frame_generic(frame_in, transformer, H if calibrated else None,
                                               xoff, yoff, show_mode, scale, params_now, params_now['COLORES'])

            if show_mode >= 1 and not calibrated:
                overlay = frame_in.copy()
                cv2.putText(overlay, "SIN CALIBRACION - Presiona 'Calibrar'", (20, 40),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.7, (180, 180, 180), 2, cv2.LINE_AA)
                cv2.putText(overlay, "(Detectando sin georreferencia)", (20, 70),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.5, (180, 180, 180), 1, cv2.LINE_AA)
                draw_small("SISTEMA DE DETECCION (recorte)", overlay, scale)

            if calibrated:
                guardar_geojson(detections, 'Point', os.path.join(SALIDAS_DIR, 'detecciones_puntos.geojson'))
                guardar_geojson(detections, 'LineString', os.path.join(SALIDAS_DIR, 'detecciones_lineas.geojson'))
                guardar_geojson(detections, 'Polygon', os.path.join(SALIDAS_DIR, 'detecciones_poligonos.geojson'))

            if cv2.waitKey(video_speed) & 0xFF == ord('q'):
                break
    finally:
        cap.release()
        cv2.destroyAllWindows()

# ================== Interfaz Tkinter ==================

class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Cartografía sensorial · Detector de mapeo libre")
        self.geometry("880x880")
        self.minsize(820, 760)
        aplicar_estilo(self)

        self.src_type = tk.StringVar(value="file")
        self.video_path = tk.StringVar(value=DEFAULT_VIDEO_FILE)
        self.url_str = tk.StringVar(value="http://127.0.0.1:4747/video")
        self.camera_index = tk.StringVar(value="0")

        pr = PRESETS[DEFAULT_PRESET]
        xmin, ymin, xmax, ymax = pr["bounds"]
        self.epsg_src = tk.StringVar(value=str(pr["epsg"]))
        self.xmin = tk.StringVar(value=str(xmin))
        self.ymin = tk.StringVar(value=str(ymin))
        self.xmax = tk.StringVar(value=str(xmax))
        self.ymax = tk.StringVar(value=str(ymax))
        self.coord_preset = tk.StringVar(value=DEFAULT_PRESET)
        self.orientacion = tk.StringVar(value=ORIENTACIONES[DEFAULT_ORIENTACION]["nombre"])

        self.show_mode = tk.IntVar(value=DEFAULT_SHOW_MODE)
        self.scale = tk.DoubleVar(value=DEFAULT_SCALE)
        self.video_speed = tk.IntVar(value=DEFAULT_VIDEO_SPEED_MS)
        self.recalib_every = tk.IntVar(value=DEFAULT_RECALIB_EVERY)

        self.pause_event = threading.Event()
        self.recalib_event = threading.Event()
        self.stop_event = threading.Event()
        self.worker_thread = None

        self.colores = cargar_colores()
        self.live_params = LiveParams({**DEFAULT_PARAMS, "COLORES": self._copia_colores()})
        self.geo_live = LiveParams({"version": 0, "epsg": pr["epsg"], "bounds": pr["bounds"], "orientacion": DEFAULT_ORIENTACION})
        self.estado = LiveParams({"texto": "Listo. Elige la fuente de video y presiona Iniciar.", "ok": True})

        self._build()
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self._id_estado = self.after(400, self._refrescar_estado)

    # ------- helpers -------
    def _copia_colores(self):
        return {k: {**v, "hsv": [list(r) for r in v["hsv"]]} for k, v in self.colores.items()}

    def _labeled_scale(self, parent, text, from_, to, var_type=float, init=None, step=1.0, fmt="{:.1f}"):
        frm = ttk.Frame(parent)
        ttk.Label(frm, text=text, width=22).pack(side="left")
        val_lbl = ttk.Label(frm, text="", width=6)
        val_lbl.pack(side="right")
        sv = tk.DoubleVar(value=float(init) if init is not None else float(from_))
        scl = ttk.Scale(frm, from_=from_, to=to, orient="horizontal", variable=sv, length=240)
        scl.pack(fill="x", padx=8)
        def on_move(_=None):
            v = sv.get()
            v = int(round(v / step) * step) if var_type is int else round(v / step) * step
            val_lbl.config(text=f"{v}" if var_type is int else fmt.format(v))
        scl.configure(command=on_move)
        sv.trace_add('write', lambda *_: on_move())
        on_move()
        return frm, sv, on_move

    def _apply_preset(self, *args):
        pr = PRESETS.get(self.coord_preset.get())
        if not pr:
            return
        xmin, ymin, xmax, ymax = pr["bounds"]
        self.xmin.set(str(xmin)); self.ymin.set(str(ymin))
        self.xmax.set(str(xmax)); self.ymax.set(str(ymax))
        self.epsg_src.set(str(pr["epsg"]))
        self._update_preset_desc()
        self._aplicar_area()

    def _update_preset_desc(self):
        pr = PRESETS.get(self.coord_preset.get())
        self.preset_desc_label.config(text=f'{pr["nombre"]} — EPSG:{pr["epsg"]}' if pr else "Personalizado")

    def _orientacion_clave(self):
        nombre = self.orientacion.get()
        return next((k for k, v in ORIENTACIONES.items() if v["nombre"] == nombre), DEFAULT_ORIENTACION)

    def _aplicar_area(self, *_):
        """Envía el área al hilo de video: se aplica en vivo, sin reiniciar."""
        try:
            epsg = int(self.epsg_src.get())
            bounds = [float(self.xmin.get()), float(self.ymin.get()), float(self.xmax.get()), float(self.ymax.get())]
            assert bounds[0] < bounds[2] and bounds[1] < bounds[3]
            init_transformer(epsg)
        except Exception:
            messagebox.showerror("Área", "Revisa el EPSG y que xmin < xmax, ymin < ymax.")
            return
        v = self.geo_live.get()["version"] + 1
        self.geo_live.update(version=v, epsg=epsg, bounds=bounds, orientacion=self._orientacion_clave())
        if not (self.worker_thread and self.worker_thread.is_alive()):
            escribir_sesion('libre', epsg, bounds, self._orientacion_clave())
        self.estado.update(texto="Área aplicada.", ok=True)

    def _refrescar_estado(self):
        e = self.estado.get()
        self.lbl_estado.config(text=e["texto"], foreground=C['verde'] if e["ok"] else C['tenue'])
        self._id_estado = self.after(400, self._refrescar_estado)

    def _detect_cameras(self):
        self.btn_detect_cam.config(state="disabled")
        self.update()
        cameras = list_available_cameras(10)
        if cameras:
            self.camera_combo['values'] = [str(i) for i in cameras]
            self.camera_index.set(str(cameras[0]))
            messagebox.showinfo("Cámaras detectadas", f"Índices: {cameras}\n\n0 suele ser la integrada; 1+ USB o capturadoras.")
        else:
            messagebox.showwarning("Sin cámaras", "No se detectaron cámaras. Revisa conexión, drivers y que no esté en uso.")
        self.btn_detect_cam.config(state="normal")

    def _abrir_mapa(self):
        try:
            url = servidor_mapa.abrir('mapa')
            self.estado.update(texto=f"Mapa abierto en {url}", ok=True)
        except Exception as e:
            messagebox.showerror("Mapa", f"No se pudo abrir el mapa: {e}")

    # ------- construcción -------
    def _build(self):
        pad = {'padx': 12, 'pady': 5}

        cab = ttk.Frame(self)
        cab.pack(fill="x", padx=12, pady=(12, 4))
        ttk.Label(cab, text="MAPEO LIBRE", style="Titulo.TLabel").pack(side="left")
        boton(cab, "Abrir mapa web", self._abrir_mapa, "Blurple").pack(side="right")

        # Fuente de video
        frm_src = ttk.LabelFrame(self, text="Fuente de video")
        frm_src.pack(fill="x", **pad)
        ttk.Radiobutton(frm_src, text="Archivo", value="file", variable=self.src_type).grid(row=0, column=0, sticky="w", padx=6, pady=4)
        ttk.Radiobutton(frm_src, text="URL (DroidCam/HTTP/RTSP)", value="url", variable=self.src_type).grid(row=0, column=1, sticky="w", padx=6)
        ttk.Radiobutton(frm_src, text="Cámara/Capturadora USB", value="camera", variable=self.src_type).grid(row=0, column=2, sticky="w", padx=6)
        ttk.Label(frm_src, text="Archivo:").grid(row=1, column=0, sticky="e")
        ttk.Entry(frm_src, textvariable=self.video_path, width=50).grid(row=1, column=1, columnspan=2, sticky="we", pady=2)
        ttk.Button(frm_src, text="Buscar…", command=self.pick_file).grid(row=1, column=3, padx=6)
        ttk.Label(frm_src, text="URL:").grid(row=2, column=0, sticky="e")
        ttk.Entry(frm_src, textvariable=self.url_str, width=50).grid(row=2, column=1, columnspan=2, sticky="we", pady=2)
        ttk.Label(frm_src, text="Índice cámara:").grid(row=3, column=0, sticky="e")
        self.camera_combo = ttk.Combobox(frm_src, textvariable=self.camera_index, values=["0", "1", "2", "3", "4"], width=8, state="readonly")
        self.camera_combo.grid(row=3, column=1, sticky="w", padx=4, pady=4)
        self.btn_detect_cam = ttk.Button(frm_src, text="Detectar cámaras", command=self._detect_cameras)
        self.btn_detect_cam.grid(row=3, column=2, sticky="w", padx=6)
        for i in range(4):
            frm_src.grid_columnconfigure(i, weight=1)

        # Georreferenciación (se aplica en vivo)
        frm_geo = ttk.LabelFrame(self, text="Área de trabajo — las 4 cruces azules")
        frm_geo.pack(fill="x", **pad)
        ttk.Label(frm_geo, text="Área:").grid(row=0, column=0, sticky="e")
        preset_combo = ttk.Combobox(frm_geo, textvariable=self.coord_preset, values=list(PRESETS.keys()), state="readonly", width=25)
        preset_combo.grid(row=0, column=1, sticky="w", padx=4, pady=4)
        self.preset_desc_label = ttk.Label(frm_geo, text="", style="Tenue.TLabel")
        self.preset_desc_label.grid(row=0, column=2, columnspan=2, sticky="w", padx=4)
        self._update_preset_desc()
        preset_combo.bind("<<ComboboxSelected>>", self._apply_preset)

        ttk.Label(frm_geo, text="EPSG:").grid(row=1, column=0, sticky="e")
        ttk.Entry(frm_geo, textvariable=self.epsg_src, width=10).grid(row=1, column=1, sticky="w")
        ttk.Label(frm_geo, text="Orientación:").grid(row=1, column=2, sticky="e")
        ori = ttk.Combobox(frm_geo, textvariable=self.orientacion, state="readonly", width=28,
                           values=[v["nombre"] for v in ORIENTACIONES.values()])
        ori.grid(row=1, column=3, sticky="w", pady=4)
        ori.bind("<<ComboboxSelected>>", self._aplicar_area)
        ttk.Label(frm_geo, text="xmin:").grid(row=2, column=0, sticky="e"); ttk.Entry(frm_geo, textvariable=self.xmin, width=14).grid(row=2, column=1, sticky="w", pady=2)
        ttk.Label(frm_geo, text="ymin:").grid(row=2, column=2, sticky="e"); ttk.Entry(frm_geo, textvariable=self.ymin, width=14).grid(row=2, column=3, sticky="w")
        ttk.Label(frm_geo, text="xmax:").grid(row=3, column=0, sticky="e"); ttk.Entry(frm_geo, textvariable=self.xmax, width=14).grid(row=3, column=1, sticky="w", pady=2)
        ttk.Label(frm_geo, text="ymax:").grid(row=3, column=2, sticky="e"); ttk.Entry(frm_geo, textvariable=self.ymax, width=14).grid(row=3, column=3, sticky="w")
        boton(frm_geo, "Aplicar área", self._aplicar_area, "Blurple").grid(row=4, column=3, sticky="e", pady=6, padx=4)
        ttk.Label(frm_geo, text="Automática: pon una flecha azul apuntando al norte junto a la cruz de arriba a la derecha del mapa.",
                  style="Tenue.TLabel", wraplength=560).grid(row=4, column=0, columnspan=3, sticky="w", padx=4)
        for i in range(4):
            frm_geo.grid_columnconfigure(i, weight=1)

        # Visualización
        frm_opt = ttk.LabelFrame(self, text="Visualización")
        frm_opt.pack(fill="x", **pad)
        ttk.Label(frm_opt, text="Ventanas:").grid(row=0, column=0, sticky="e")
        ttk.Radiobutton(frm_opt, text="Ninguna", value=0, variable=self.show_mode).grid(row=0, column=1, sticky="w")
        ttk.Radiobutton(frm_opt, text="Solo recorte", value=1, variable=self.show_mode).grid(row=0, column=2, sticky="w")
        ttk.Radiobutton(frm_opt, text="Todas", value=2, variable=self.show_mode).grid(row=0, column=3, sticky="w")
        ttk.Label(frm_opt, text="Escala (0.2–1.5):").grid(row=1, column=0, sticky="e")
        ttk.Entry(frm_opt, textvariable=self.scale, width=8).grid(row=1, column=1, sticky="w", pady=3)
        ttk.Label(frm_opt, text="Velocidad (ms):").grid(row=1, column=2, sticky="e")
        ttk.Entry(frm_opt, textvariable=self.video_speed, width=8).grid(row=1, column=3, sticky="w")
        ttk.Label(frm_opt, text="Recalibrar cada N cuadros (0=manual):").grid(row=2, column=0, columnspan=2, sticky="e")
        ttk.Entry(frm_opt, textvariable=self.recalib_every, width=8).grid(row=2, column=2, sticky="w", pady=3)

        # Pestañas de parámetros
        frm_btn = ttk.Frame(self)
        boton(frm_btn, "Iniciar", self.start_detection, "Verde").pack(side="left", padx=4)
        ttk.Button(frm_btn, text="Pausar", command=self.pause_capture).pack(side="left", padx=4)
        ttk.Button(frm_btn, text="Reanudar", command=self.resume_capture).pack(side="left", padx=4)
        boton(frm_btn, "Calibrar (cruces)", self.force_recalib, "Blurple").pack(side="left", padx=4)
        ttk.Button(frm_btn, text="Salir", command=self._on_close).pack(side="right", padx=4)
        self.lbl_estado = ttk.Label(self, text="", wraplength=820)
        self.lbl_estado.pack(side="bottom", fill="x", padx=14, pady=(0, 10))
        frm_btn.pack(side="bottom", fill="x", padx=12, pady=6)

        nb = ttk.Notebook(self)
        nb.pack(fill="both", expand=True, **pad)
        tab_col = ttk.Frame(nb); nb.add(tab_col, text="Colores")
        tab_pts = ttk.Frame(nb); nb.add(tab_pts, text="Puntos")
        tab_lin = ttk.Frame(nb); nb.add(tab_lin, text="Líneas")
        tab_pol = ttk.Frame(nb); nb.add(tab_pol, text="Polígonos")
        tab_mor = ttk.Frame(nb); nb.add(tab_mor, text="Morfología")
        self._build_colores(tab_col)

        f, sv_min_area_point, _ = self._labeled_scale(tab_pts, "Área mínima punto", 1, 200, int, DEFAULT_PARAMS["MIN_AREA_POINT"], 1, "{:.0f}"); f.pack(fill="x", pady=4, padx=6)
        f, sv_min_area_line, _ = self._labeled_scale(tab_lin, "Área mínima línea", 5, 1000, int, DEFAULT_PARAMS["MIN_AREA_LINE"], 5, "{:.0f}"); f.pack(fill="x", pady=4, padx=6)
        f, sv_min_len, _ = self._labeled_scale(tab_lin, "Largo mínimo (px)", 5, 200, int, DEFAULT_PARAMS["MIN_LINE_LENGTH"], 1, "{:.0f}"); f.pack(fill="x", pady=4, padx=6)
        f, sv_min_aspect, _ = self._labeled_scale(tab_lin, "Aspecto mínimo (L/A)", 1.0, 12.0, float, DEFAULT_PARAMS["MIN_LINE_ASPECT"], 0.5, "{:.1f}"); f.pack(fill="x", pady=4, padx=6)
        f, sv_min_area_poly, _ = self._labeled_scale(tab_pol, "Área mínima polígono", 50, 5000, int, DEFAULT_PARAMS["MIN_AREA_POLY"], 10, "{:.0f}"); f.pack(fill="x", pady=4, padx=6)
        f, sv_k_long, _ = self._labeled_scale(tab_mor, "Kernel largo (px)", 3, 51, int, DEFAULT_PARAMS["K_LONG"], 2, "{:.0f}"); f.pack(fill="x", pady=4, padx=6)
        f, sv_k_short, _ = self._labeled_scale(tab_mor, "Kernel corto (px)", 3, 15, int, DEFAULT_PARAMS["K_SHORT"], 2, "{:.0f}"); f.pack(fill="x", pady=4, padx=6)
        f, sv_m_iters, _ = self._labeled_scale(tab_mor, "Iteraciones morfología", 0, 5, int, DEFAULT_PARAMS["MORPH_ITERS"], 1, "{:.0f}"); f.pack(fill="x", pady=4, padx=6)

        def sync_params(*_):
            self.live_params.update(
                MIN_AREA_POINT=int(sv_min_area_point.get()), MIN_AREA_LINE=int(sv_min_area_line.get()),
                MIN_LINE_LENGTH=int(sv_min_len.get()), MIN_LINE_ASPECT=float(sv_min_aspect.get()),
                MIN_AREA_POLY=int(sv_min_area_poly.get()), K_LONG=int(sv_k_long.get()),
                K_SHORT=int(sv_k_short.get()), MORPH_ITERS=int(sv_m_iters.get()),
            )
        for sv in (sv_min_area_point, sv_min_area_line, sv_min_len, sv_min_aspect, sv_min_area_poly, sv_k_long, sv_k_short, sv_m_iters):
            sv.trace_add('write', sync_params)
        sync_params()

        # Botones de control

    def _build_colores(self, tab):
        ttk.Label(tab, text="Colores de plastilina que se detectan (el azul queda reservado para las cruces y la flecha):",
                  style="Tenue.TLabel").pack(anchor="w", padx=6, pady=(8, 4))
        rejilla = ttk.Frame(tab); rejilla.pack(fill="x", padx=6)
        self.vars_activo = {}
        for i, (cid, col) in enumerate(self.colores.items()):
            celda = ttk.Frame(rejilla); celda.grid(row=i // 4, column=i % 4, sticky="w", padx=6, pady=3)
            tk.Label(celda, width=2, bg=col["hex"], relief="solid", bd=1).pack(side="left", padx=(0, 6))
            var = tk.BooleanVar(value=bool(col.get("activo")))
            ttk.Checkbutton(celda, text=col["nombre"], variable=var, command=self._sync_colores).pack(side="left")
            self.vars_activo[cid] = var

        sel = ttk.Frame(tab); sel.pack(fill="x", padx=6, pady=(10, 2))
        ttk.Label(sel, text="Ajustar el rango de:").pack(side="left")
        self.color_editado = tk.StringVar(value=next(iter(self.colores)))
        cb = ttk.Combobox(sel, textvariable=self.color_editado, values=list(self.colores.keys()), state="readonly", width=12)
        cb.pack(side="left", padx=6)
        cb.bind("<<ComboboxSelected>>", lambda e: self._cargar_rango())
        ttk.Button(sel, text="Guardar en config/colores.json", command=self._guardar_colores).pack(side="right")

        self.vars_rango = []
        etiquetas = ["H bajo", "H alto", "S bajo", "S alto", "V bajo", "V alto"]
        cuerpo = ttk.Frame(tab); cuerpo.pack(fill="x", padx=6)
        for j, et in enumerate(etiquetas):
            f, sv, _ = self._labeled_scale(cuerpo, et, 0, 179 if j < 2 else 255, int, 0, 1, "{:.0f}")
            f.grid(row=j // 2, column=j % 2, sticky="we", padx=4, pady=2)
            sv.trace_add('write', lambda *_: self._rango_a_color())
            self.vars_rango.append(sv)
        cuerpo.grid_columnconfigure(0, weight=1); cuerpo.grid_columnconfigure(1, weight=1)
        ttk.Label(tab, text="Si H bajo es mayor que H alto, el rango da la vuelta (así funciona el rojo: 170 → 7).",
                  style="Tenue.TLabel").pack(anchor="w", padx=6, pady=4)
        self._cargando_rango = False
        self._cargar_rango()

    def _cargar_rango(self):
        self._cargando_rango = True
        h, s, v = self.colores[self.color_editado.get()]["hsv"]
        for sv, val in zip(self.vars_rango, [h[0], h[1], s[0], s[1], v[0], v[1]]):
            sv.set(val)
        self._cargando_rango = False

    def _rango_a_color(self):
        if self._cargando_rango:
            return
        vals = [int(sv.get()) for sv in self.vars_rango]
        self.colores[self.color_editado.get()]["hsv"] = [[vals[0], vals[1]], [vals[2], vals[3]], [vals[4], vals[5]]]
        self._sync_colores()

    def _sync_colores(self):
        for cid, var in self.vars_activo.items():
            self.colores[cid]["activo"] = var.get()
        self.live_params.update(COLORES=self._copia_colores())

    def _guardar_colores(self):
        guardar_colores(self.colores)
        messagebox.showinfo("Colores", "Rangos guardados en config/colores.json.\nLa versión web los usa al abrirse.")

    def pick_file(self):
        path = filedialog.askopenfilename(title="Seleccionar video", filetypes=[("Videos", "*.mp4;*.avi;*.mov;*.mkv"), ("Todos", "*.*")])
        if path:
            self.video_path.set(path)

    def start_detection(self):
        if self.worker_thread and self.worker_thread.is_alive():
            messagebox.showinfo("Ejecución", "La detección ya está corriendo. Los cambios de área se aplican en vivo con 'Aplicar área'.")
            return
        self.pause_event.clear()
        self.recalib_event.clear()
        self.stop_event.clear()
        try:
            video_speed = int(self.video_speed.get())
            scale = float(self.scale.get()); assert 0.1 <= scale <= 1.5
            recalib_every = max(0, int(self.recalib_every.get()))
        except Exception as e:
            messagebox.showerror("Error", f"Parámetros inválidos: {e}")
            return
        self._aplicar_area()

        src_type = self.src_type.get()
        if src_type == 'file':
            source = self.video_path.get()
            if not source:
                messagebox.showwarning("Falta ruta", "Selecciona un archivo de video.")
                return
        elif src_type == 'url':
            source = self.url_str.get()
            if not source.lower().startswith(("http://", "https://", "rtsp://")):
                messagebox.showwarning("URL", "Ingresa una URL válida (http/https/rtsp).")
                return
        else:
            try:
                source = int(self.camera_index.get())
            except ValueError:
                messagebox.showwarning("Cámara", "Índice de cámara inválido.")
                return

        self.worker_thread = threading.Thread(
            target=run_pipeline,
            args=(source, src_type, self.show_mode.get(), scale, video_speed, recalib_every,
                  self.live_params, self.geo_live, self.estado, self.pause_event, self.recalib_event, self.stop_event),
            daemon=True,
        )
        self.worker_thread.start()
        self.estado.update(texto="Detectando. Cuando se vean las 4 cruces azules presiona 'Calibrar (cruces)'.", ok=True)

    def pause_capture(self):
        self.pause_event.set()

    def resume_capture(self):
        self.pause_event.clear()

    def force_recalib(self):
        self.recalib_event.set()

    def destroy(self):
        try:
            self.after_cancel(self._id_estado)
        except Exception:
            pass
        super().destroy()

    def _on_close(self):
        self.stop_event.set()
        try:
            if self.worker_thread:
                self.worker_thread.join(timeout=1.5)
        except Exception:
            pass
        self.destroy()

if __name__ == "__main__":
    app = App()
    app.mainloop()
