# -*- coding: utf-8 -*-
"""
Detector para el JUEGO (Tkinter + OpenCV) — versión laptop.

Detecta plastilina VERDE como puntos y la georreferencia con 4 cruces AZULES (y, si está,
la flecha azul de norte). La dinámica (participantes, puntos de control, puntaje) vive en
el mapa web (web/mapa/, modo Juego): este programa solo alimenta las detecciones.
El botón "Abrir mapa" levanta un servidor local y abre el mapa en el navegador.

- Las cruces se detectan en cada cuadro y se dibujan para dar retroalimentación.
- La homografía solo se actualiza al presionar "Calibrar" (o al cambiar el área, en vivo).
- Las cruces no se guardan; solo los puntos verdes → salidas/detecciones_puntos.geojson
"""

import os
import threading
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

import cv2
import numpy as np
from pyproj import Transformer

from georreferencia import (PRESETS, ORIENTACIONES, SALIDAS_DIR, VIDEOS_DIR, ordenar_esquinas, homografia,
                            resolver_esquinas, escribir_sesion, escribir_json, direccion_flecha, elongacion, ELONGACION_FLECHA)
from estilo import aplicar_estilo, boton, C
import servidor_mapa

# ================== CONFIG DEFAULT ==================

DEFAULT_PRESET = "cuenca_valle_mexico"
# 'auto' usa la flecha de norte; sin flecha cae en 'norte_arriba'.
# La v2 de este detector equivalía a 'espejo' (ver "Problemas conocidos" en el README).
DEFAULT_ORIENTACION = "auto"

DEFAULT_VIDEO_FILE = os.path.join(VIDEOS_DIR, "20250327_130515_referencia.mp4")
DEFAULT_VIDEO_SPEED_MS = 25
DEFAULT_SHOW_MODE = 1
DEFAULT_SCALE = 1.0
DEFAULT_RECALIB_EVERY = 0  # manual

DEFAULT_PARAMS = {
    "MIN_AREA": 8,
    "MAX_AREA": 8000,
    # Plastilina verde
    "GREEN_H_LOW": 35, "GREEN_H_HIGH": 85,
    "GREEN_S_LOW": 50, "GREEN_S_HIGH": 255,
    "GREEN_V_LOW": 50, "GREEN_V_HIGH": 255,
    # Cruces y flecha AZULES
    "BLUE_H_LOW": 85, "BLUE_H_HIGH": 135,
    "BLUE_S_LOW": 50, "BLUE_S_HIGH": 255,
    "BLUE_V_LOW": 80, "BLUE_V_HIGH": 255,
}

# ================== Núcleo de detección ==================

def init_transformer(epsg_src, epsg_dst=4326):
    return Transformer.from_crs(epsg_src, epsg_dst, always_xy=True)

def find_blue_cross_corners(frame, params):
    """
    Detecta las 4 cruces AZULES en las esquinas y la flecha azul de norte (figura alargada).
    Retorna: corners, crop_box, centroids_all, debug_dict (máscara y flecha)
    """
    blurred = cv2.GaussianBlur(frame, (5, 5), 0)
    hsv = cv2.cvtColor(blurred, cv2.COLOR_BGR2HSV)
    lower = np.array([params["BLUE_H_LOW"], params["BLUE_S_LOW"], params["BLUE_V_LOW"]], dtype=np.uint8)
    upper = np.array([params["BLUE_H_HIGH"], params["BLUE_S_HIGH"], params["BLUE_V_HIGH"]], dtype=np.uint8)
    mask = cv2.inRange(hsv, lower, upper)

    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    centroids = []
    flecha, area_flecha = None, 0
    for c in contours:
        area = cv2.contourArea(c)
        if area < 15:
            continue
        if area >= 60 and elongacion(c) >= ELONGACION_FLECHA:
            if area > area_flecha:
                flecha, area_flecha = direccion_flecha(mask, c), area
            continue
        x, y, w, h = cv2.boundingRect(c)
        ar = w / h if h > 0 else 0
        if ar < 0.2 or ar > 5.0:
            continue
        M = cv2.moments(c)
        if M["m00"] > 0:
            centroids.append((M["m10"] / M["m00"], M["m01"] / M["m00"]))

    dbg = {"mask_blue": mask, "flecha": flecha}
    if len(centroids) < 4:
        return None, None, centroids, dbg

    corners = ordenar_esquinas(centroids)
    xs, ys = corners[:, 0], corners[:, 1]
    pad = 5
    xmin_c = max(0, int(np.floor(xs.min())) - pad)
    ymin_c = max(0, int(np.floor(ys.min())) - pad)
    xmax_c = min(frame.shape[1] - 1, int(np.ceil(xs.max())) + pad)
    ymax_c = min(frame.shape[0] - 1, int(np.ceil(ys.max())) + pad)
    return corners, (xmin_c, ymin_c, xmax_c, ymax_c), centroids, dbg

def compute_homography_from_corners(corners_img, geo_bounds_utm, orientacion="norte_arriba", flecha=None):
    esquinas, _ = resolver_esquinas(orientacion, corners_img, flecha)
    return homografia(corners_img, geo_bounds_utm, esquinas)

def raster_to_geo(cx, cy, H, transformer):
    pt = np.array([[[cx, cy]]], dtype=np.float32)
    pt_h = cv2.perspectiveTransform(pt, H)[0][0]
    xutm, yutm = float(pt_h[0]), float(pt_h[1])
    lon, lat = transformer.transform(xutm, yutm)
    return (lon, lat)

def draw_small(window_name, frame, scale):
    h, w = frame.shape[:2]
    resized = cv2.resize(frame, (max(1, int(w * scale)), max(1, int(h * scale))))
    cv2.imshow(window_name, resized)

def guardar_geojson_puntos(detections, nombre):
    """Escribe SIEMPRE el archivo (vacío si no hay piezas) para que el mapa no muestre posiciones viejas."""
    features = [{
        "type": "Feature",
        "properties": {"color": "verde", "type": "point"},
        "geometry": d['geometry'],
    } for d in detections if d['geometry'] is not None]
    try:
        escribir_json(nombre, {"type": "FeatureCollection", "features": features})
    except OSError as e:
        print(f"[WARN] No se pudo escribir {nombre}: {e}")

# ================== Pipeline ==================

class LiveParams:
    def __init__(self, init_dict):
        self._lock = threading.Lock()
        self._d = dict(init_dict)
    def update(self, **kwargs):
        with self._lock:
            self._d.update(kwargs)
    def get(self):
        with self._lock:
            return dict(self._d)

def detect_green_points(frame_in, transformer, H, xoff, yoff, show_mode, scale, params):
    """Detecta solo plastilina VERDE como puntos."""
    detections = []
    blurred = cv2.GaussianBlur(frame_in, (5, 5), 0)
    hsv = cv2.cvtColor(blurred, cv2.COLOR_BGR2HSV)
    lower = np.array([params["GREEN_H_LOW"], params["GREEN_S_LOW"], params["GREEN_V_LOW"]], dtype=np.uint8)
    upper = np.array([params["GREEN_H_HIGH"], params["GREEN_S_HIGH"], params["GREEN_V_HIGH"]], dtype=np.uint8)
    mask = cv2.inRange(hsv, lower, upper)
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel, iterations=1)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel, iterations=1)
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    count = 0
    for contour in contours:
        area = cv2.contourArea(contour)
        if area < params["MIN_AREA"] or area > params["MAX_AREA"]:
            continue
        M = cv2.moments(contour)
        if M["m00"] == 0:
            continue
        cx = int(M["m10"] / M["m00"])
        cy = int(M["m01"] / M["m00"])
        geometry = None
        if H is not None:
            lon, lat = raster_to_geo(cx + xoff, cy + yoff, H, transformer)
            geometry = {"type": "Point", "coordinates": [lon, lat]}
        count += 1
        detections.append({"geometry": geometry, "color": "verde", "type": "point"})
        if show_mode >= 1:
            cv2.circle(frame_in, (cx, cy), 7, (126, 237, 53), 2)
            cv2.circle(frame_in, (cx, cy), 2, (126, 237, 53), -1)
            cv2.putText(frame_in, str(count), (cx + 10, cy - 5), cv2.FONT_HERSHEY_SIMPLEX, 0.4, (255, 255, 255), 1, cv2.LINE_AA)

    if show_mode == 2:
        draw_small("Mascara Verde", cv2.cvtColor(mask, cv2.COLOR_GRAY2BGR), scale)
    return detections, count

def list_available_cameras(max_test=10):
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
    os.makedirs(SALIDAS_DIR, exist_ok=True)
    cap = cv2.VideoCapture(int(video_source) if src_type == 'camera' else video_source)
    if not cap.isOpened():
        estado.update(texto=f"No se pudo abrir: {video_source}", ok=False)
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
            params_now = live_params.get()

            # Área editada en la ventana → se aplica en vivo con las últimas cruces vistas
            g = geo_live.get()
            if g['version'] != geo_version:
                geo_version, geo = g['version'], g
                transformer = init_transformer(geo['epsg'])
                escribir_sesion('juego', geo['epsg'], geo['bounds'], geo['orientacion'])
                if ultimas_cruces is not None:
                    esquinas, nota = resolver_esquinas(geo['orientacion'], ultimas_cruces, ultima_flecha)
                    H = homografia(ultimas_cruces, geo['bounds'], esquinas)
                    estado.update(texto=f"Área actualizada. {nota}.", ok=True)

            # Cruces y flecha en cada cuadro (retroalimentación visual)
            corners, crop_candidate, all_centroids, dbg = find_blue_cross_corners(frame, params_now)
            num_crosses_found = len(all_centroids)
            if show_mode == 2:
                draw_small("Mascara Azul (cruces y flecha)", cv2.cvtColor(dbg["mask_blue"], cv2.COLOR_GRAY2BGR), scale)

            need_recalib = recalib_every > 0 and frame_count % recalib_every == 0
            if recalib_event.is_set():
                need_recalib = True
                recalib_event.clear()
            if need_recalib:
                if corners is not None:
                    esquinas, nota = resolver_esquinas(geo['orientacion'], corners, dbg['flecha'])
                    H2 = homografia(corners, geo['bounds'], esquinas)
                    if H2 is not None:
                        H, crop_box = H2, crop_candidate
                        ultimas_cruces, ultima_flecha = corners, dbg['flecha']
                        estado.update(texto=f"Calibrado con 4 cruces. {nota}.", ok=True)
                else:
                    estado.update(texto=f"Se ven {num_crosses_found} de 4 cruces azules. Revisa la luz o el encuadre.", ok=False)

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
                    draw_small("JUEGO - Deteccion", overlay, scale)
                if cv2.waitKey(video_speed) & 0xFF == ord('q'):
                    break
                continue

            detections, green_count = detect_green_points(frame_in, transformer, H if calibrated else None,
                                                          xoff, yoff, show_mode, scale, params_now)

            if show_mode >= 1:
                display = frame_in.copy()
                for (cx, cy) in all_centroids:
                    ix, iy = int(cx) - xoff, int(cy) - yoff
                    if 0 <= ix < display.shape[1] and 0 <= iy < display.shape[0]:
                        # gris mientras faltan cruces, verde cuando se ven las 4
                        color_cruz = (126, 237, 53) if num_crosses_found >= 4 else (180, 180, 180)
                        cv2.drawMarker(display, (ix, iy), color_cruz, markerType=cv2.MARKER_CROSS, markerSize=16, thickness=2)
                if dbg['flecha']:
                    (fx, fy), d = dbg['flecha']
                    p0 = (int(fx) - xoff, int(fy) - yoff)
                    cv2.arrowedLine(display, p0, (int(p0[0] + d[0] * 40), int(p0[1] + d[1] * 40)), (126, 237, 53), 2, tipLength=0.4)
                if corners is not None:
                    pts_draw = corners.astype(int)
                    for i in range(4):
                        p1 = (pts_draw[i][0] - xoff, pts_draw[i][1] - yoff)
                        p2 = (pts_draw[(i + 1) % 4][0] - xoff, pts_draw[(i + 1) % 4][1] - yoff)
                        cv2.line(display, p1, p2, (126, 237, 53), 1, cv2.LINE_AA)
                cv2.putText(display, f"PUNTOS VERDES: {green_count}", (10, 22), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (126, 237, 53), 1, cv2.LINE_AA)
                if calibrated:
                    cv2.putText(display, f"CALIBRADO | Cruces: {num_crosses_found}/4", (10, 44), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (126, 237, 53), 1, cv2.LINE_AA)
                else:
                    cv2.putText(display, "SIN CALIBRACION - Presiona 'Calibrar'", (10, 44), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (180, 180, 180), 1, cv2.LINE_AA)
                    cv2.putText(display, f"Cruces visibles: {num_crosses_found}/4", (10, 64), cv2.FONT_HERSHEY_SIMPLEX, 0.4,
                                (126, 237, 53) if num_crosses_found >= 4 else (180, 180, 180), 1, cv2.LINE_AA)
                draw_small("JUEGO - Deteccion", display, scale)

            if calibrated:
                guardar_geojson_puntos(detections, os.path.join(SALIDAS_DIR, 'detecciones_puntos.geojson'))
                for f_name in ('detecciones_lineas.geojson', 'detecciones_poligonos.geojson'):
                    try:
                        escribir_json(os.path.join(SALIDAS_DIR, f_name), {"type": "FeatureCollection", "features": []})
                    except Exception:
                        pass

            if cv2.waitKey(video_speed) & 0xFF == ord('q'):
                break
    finally:
        cap.release()
        cv2.destroyAllWindows()

# ================== Interfaz Tkinter ==================

class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Cartografía sensorial · Detector del juego")
        self.geometry("860x820")
        self.minsize(800, 720)
        aplicar_estilo(self)

        self.src_type = tk.StringVar(value="camera")
        self.video_path = tk.StringVar(value=DEFAULT_VIDEO_FILE)
        self.url_str = tk.StringVar(value="http://192.168.1.68:4747/video")
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
        self.live_params = LiveParams(DEFAULT_PARAMS)
        self.geo_live = LiveParams({"version": 0, "epsg": pr["epsg"], "bounds": pr["bounds"], "orientacion": DEFAULT_ORIENTACION})
        self.estado = LiveParams({"texto": "Listo. Elige la fuente de video y presiona Iniciar.", "ok": True})

        self._build()
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self._id_estado = self.after(400, self._refrescar_estado)

    def _hsv_scale(self, parent, text, from_, to, init):
        frm = ttk.Frame(parent)
        ttk.Label(frm, text=text, width=10).pack(side="left")
        val_lbl = ttk.Label(frm, text=str(init), width=4)
        val_lbl.pack(side="right")
        sv = tk.IntVar(value=init)
        scl = ttk.Scale(frm, from_=from_, to=to, orient="horizontal", variable=sv, length=200)
        scl.pack(fill="x", padx=4)
        scl.configure(command=lambda _=None: val_lbl.config(text=str(int(sv.get()))))
        return frm, sv

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
        self.preset_desc.config(text=f'{pr["nombre"]} — EPSG:{pr["epsg"]}' if pr else "Personalizado")

    def _orientacion_clave(self):
        nombre = self.orientacion.get()
        return next((k for k, v in ORIENTACIONES.items() if v["nombre"] == nombre), DEFAULT_ORIENTACION)

    def _aplicar_area(self, *_):
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
            escribir_sesion('juego', epsg, bounds, self._orientacion_clave())
        self.estado.update(texto="Área aplicada.", ok=True)

    def _refrescar_estado(self):
        e = self.estado.get()
        self.lbl_estado.config(text=e["texto"], foreground=C['verde'] if e["ok"] else C['tenue'])
        self._id_estado = self.after(400, self._refrescar_estado)

    def _abrir_mapa(self):
        try:
            url = servidor_mapa.abrir('mapa')
            self.estado.update(texto=f"Mapa abierto en {url}", ok=True)
        except Exception as e:
            messagebox.showerror("Mapa", f"No se pudo abrir el mapa: {e}")

    def _detect_cameras(self):
        self.btn_detect_cam.config(state="disabled")
        self.update()
        cameras = list_available_cameras(10)
        if cameras:
            self.camera_combo['values'] = [str(i) for i in cameras]
            self.camera_index.set(str(cameras[0]))
            messagebox.showinfo("Cámaras", f"Encontradas: {cameras}\n(0=integrada, 1+=USB)")
        else:
            messagebox.showwarning("Sin cámaras", "No se detectaron cámaras.")
        self.btn_detect_cam.config(state="normal")

    def _build(self):
        pad = {'padx': 12, 'pady': 5}

        cab = ttk.Frame(self)
        cab.pack(fill="x", padx=12, pady=(12, 2))
        ttk.Label(cab, text="JUEGO", style="Titulo.TLabel").pack(side="left")
        boton(cab, "Abrir mapa web", self._abrir_mapa, "Blurple").pack(side="right")
        ttk.Label(self, text="Cruces y flecha azules = georreferencia (no se mapean). La plastilina verde son las respuestas. "
                             "Participantes y puntos de control se manejan en el mapa web.",
                  style="Tenue.TLabel", wraplength=800).pack(anchor="w", padx=12)

        frm_src = ttk.LabelFrame(self, text="Fuente de video")
        frm_src.pack(fill="x", **pad)
        ttk.Radiobutton(frm_src, text="Archivo", value="file", variable=self.src_type).grid(row=0, column=0, sticky="w", padx=6, pady=4)
        ttk.Radiobutton(frm_src, text="URL", value="url", variable=self.src_type).grid(row=0, column=1, sticky="w", padx=6)
        ttk.Radiobutton(frm_src, text="Cámara USB", value="camera", variable=self.src_type).grid(row=0, column=2, sticky="w", padx=6)
        ttk.Label(frm_src, text="Archivo:").grid(row=1, column=0, sticky="e")
        ttk.Entry(frm_src, textvariable=self.video_path, width=45).grid(row=1, column=1, columnspan=2, sticky="we", pady=2)
        ttk.Button(frm_src, text="Buscar…", command=self.pick_file).grid(row=1, column=3, padx=4)
        ttk.Label(frm_src, text="URL:").grid(row=2, column=0, sticky="e")
        ttk.Entry(frm_src, textvariable=self.url_str, width=45).grid(row=2, column=1, columnspan=2, sticky="we", pady=2)
        ttk.Label(frm_src, text="Cámara:").grid(row=3, column=0, sticky="e")
        self.camera_combo = ttk.Combobox(frm_src, textvariable=self.camera_index, values=["0", "1", "2", "3", "4"], width=6, state="readonly")
        self.camera_combo.grid(row=3, column=1, sticky="w", padx=4, pady=4)
        self.btn_detect_cam = ttk.Button(frm_src, text="Detectar", command=self._detect_cameras)
        self.btn_detect_cam.grid(row=3, column=2, sticky="w")
        for i in range(4):
            frm_src.grid_columnconfigure(i, weight=1)

        frm_geo = ttk.LabelFrame(self, text="Área de trabajo — las 4 cruces azules")
        frm_geo.pack(fill="x", **pad)
        ttk.Label(frm_geo, text="Área:").grid(row=0, column=0, sticky="e")
        pc = ttk.Combobox(frm_geo, textvariable=self.coord_preset, values=list(PRESETS.keys()), state="readonly", width=25)
        pc.grid(row=0, column=1, sticky="w", padx=4, pady=4)
        pc.bind("<<ComboboxSelected>>", self._apply_preset)
        self.preset_desc = ttk.Label(frm_geo, text="", style="Tenue.TLabel")
        self.preset_desc.grid(row=0, column=2, columnspan=2, sticky="w")
        self._update_preset_desc()
        ttk.Label(frm_geo, text="EPSG:").grid(row=1, column=0, sticky="e")
        ttk.Entry(frm_geo, textvariable=self.epsg_src, width=8).grid(row=1, column=1, sticky="w")
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
        ttk.Label(frm_geo, text="Automática: flecha azul apuntando al norte junto a la cruz de arriba a la derecha del mapa.",
                  style="Tenue.TLabel", wraplength=520).grid(row=4, column=0, columnspan=3, sticky="w", padx=4)
        for i in range(4):
            frm_geo.grid_columnconfigure(i, weight=1)

        frm_opt = ttk.LabelFrame(self, text="Visualización")
        frm_opt.pack(fill="x", **pad)
        ttk.Radiobutton(frm_opt, text="Ninguna", value=0, variable=self.show_mode).grid(row=0, column=0, sticky="w", padx=6, pady=4)
        ttk.Radiobutton(frm_opt, text="Detección", value=1, variable=self.show_mode).grid(row=0, column=1, sticky="w", padx=6)
        ttk.Radiobutton(frm_opt, text="Todo + máscaras", value=2, variable=self.show_mode).grid(row=0, column=2, sticky="w", padx=6)
        ttk.Label(frm_opt, text="Escala:").grid(row=1, column=0, sticky="e")
        ttk.Entry(frm_opt, textvariable=self.scale, width=6).grid(row=1, column=1, sticky="w", pady=3)
        ttk.Label(frm_opt, text="Vel (ms):").grid(row=1, column=2, sticky="e")
        ttk.Entry(frm_opt, textvariable=self.video_speed, width=6).grid(row=1, column=3, sticky="w")
        ttk.Label(frm_opt, text="Recalib cada N cuadros (0=manual):").grid(row=2, column=0, columnspan=2, sticky="e")
        ttk.Entry(frm_opt, textvariable=self.recalib_every, width=6).grid(row=2, column=2, sticky="w", pady=3)

        frm_btn = ttk.Frame(self)
        boton(frm_btn, "Iniciar", self.start_detection, "Verde").pack(side="left", padx=4)
        ttk.Button(frm_btn, text="Pausar", command=self.pause_capture).pack(side="left", padx=4)
        ttk.Button(frm_btn, text="Reanudar", command=self.resume_capture).pack(side="left", padx=4)
        boton(frm_btn, "Calibrar (cruces)", self.force_recalib, "Blurple").pack(side="left", padx=8)
        ttk.Button(frm_btn, text="Salir", command=self._on_close).pack(side="right", padx=4)
        self.lbl_estado = ttk.Label(self, text="", wraplength=800)
        self.lbl_estado.pack(side="bottom", fill="x", padx=14, pady=(0, 10))
        frm_btn.pack(side="bottom", fill="x", padx=12, pady=6)

        nb = ttk.Notebook(self)
        nb.pack(fill="both", expand=True, **pad)
        tab_green = ttk.Frame(nb); nb.add(tab_green, text="Verde (plastilina)")
        tab_blue = ttk.Frame(nb); nb.add(tab_blue, text="Azul (cruces y flecha)")
        tab_area = ttk.Frame(nb); nb.add(tab_area, text="Tamaño")

        svs = {}
        for tab, pref in ((tab_green, "GREEN"), (tab_blue, "BLUE")):
            for clave, et, hi in (("H_LOW", "H bajo:", 179), ("H_HIGH", "H alto:", 179), ("S_LOW", "S bajo:", 255),
                                  ("S_HIGH", "S alto:", 255), ("V_LOW", "V bajo:", 255), ("V_HIGH", "V alto:", 255)):
                f, sv = self._hsv_scale(tab, et, 0, hi, DEFAULT_PARAMS[f"{pref}_{clave}"])
                f.pack(fill="x", pady=2, padx=6)
                svs[f"{pref}_{clave}"] = sv
        ttk.Label(tab_green, text="Si la imagen impresa tiene verdes, sube S bajo para filtrar solo plastilina saturada.",
                  style="Tenue.TLabel", wraplength=600).pack(anchor="w", padx=6, pady=4)
        f, svs["MIN_AREA"] = self._hsv_scale(tab_area, "Mín (px²):", 1, 500, DEFAULT_PARAMS["MIN_AREA"]); f.pack(fill="x", pady=4, padx=6)
        f, svs["MAX_AREA"] = self._hsv_scale(tab_area, "Máx (px²):", 100, 20000, DEFAULT_PARAMS["MAX_AREA"]); f.pack(fill="x", pady=4, padx=6)

        def sync(*_):
            self.live_params.update(**{k: int(v.get()) for k, v in svs.items()})
        for sv in svs.values():
            sv.trace_add('write', sync)
        sync()


    def pick_file(self):
        path = filedialog.askopenfilename(title="Seleccionar video", filetypes=[("Videos", "*.mp4;*.avi;*.mov;*.mkv"), ("Todos", "*.*")])
        if path:
            self.video_path.set(path)

    def start_detection(self):
        if self.worker_thread and self.worker_thread.is_alive():
            messagebox.showinfo("Info", "Ya está corriendo. Los cambios de área se aplican en vivo con 'Aplicar área'.")
            return
        self.pause_event.clear()
        self.recalib_event.clear()
        self.stop_event.clear()
        try:
            video_speed = int(self.video_speed.get())
            scale = float(self.scale.get()); assert 0.1 <= scale <= 2.0
            recalib_every = max(0, int(self.recalib_every.get()))
        except Exception as e:
            messagebox.showerror("Error", f"Parámetros inválidos: {e}")
            return
        self._aplicar_area()

        src_type = self.src_type.get()
        if src_type == 'file':
            source = self.video_path.get()
            if not source:
                messagebox.showwarning("Falta", "Selecciona un archivo.")
                return
        elif src_type == 'url':
            source = self.url_str.get()
            if not source.lower().startswith(("http://", "https://", "rtsp://")):
                messagebox.showwarning("URL", "URL inválida.")
                return
        else:
            try:
                source = int(self.camera_index.get())
            except ValueError:
                messagebox.showwarning("Cámara", "Índice inválido.")
                return

        self.worker_thread = threading.Thread(
            target=run_pipeline,
            args=(source, src_type, self.show_mode.get(), scale, video_speed, recalib_every,
                  self.live_params, self.geo_live, self.estado, self.pause_event, self.recalib_event, self.stop_event),
            daemon=True,
        )
        self.worker_thread.start()
        self.estado.update(texto="Detectando. Cuando veas 4/4 cruces presiona 'Calibrar (cruces)'.", ok=True)

    def pause_capture(self): self.pause_event.set()
    def resume_capture(self): self.pause_event.clear()
    def force_recalib(self): self.recalib_event.set()

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
