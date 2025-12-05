# -*- coding: utf-8 -*-
"""
Detector con UI (Tkinter) – v6 (calibración manual, cruces azules, cámara USB)

Cambios v6:
- Calibración manual por defecto (solo al hacer clic en "Recalibrar ahora")
- Escala inicial = 1
- Nuevas coordenadas por defecto: guerrero_costa_chica (EPSG:6369)
- Cruces de calibración ahora son AZULES
- Nueva opción: Cámara/Capturadora por cable USB
- Colores de detección: AMARILLO y VERDE (originales)
- Si no hay cruces azules, NO se corta el procesamiento: se detecta visualmente sobre
  el frame completo, sin georreferenciar ni guardar GeoJSON.
- Overlay "SIN CALIBRACIÓN" cuando no hay homografía activa.
"""

import cv2
import numpy as np
import geopandas as gpd
from shapely.geometry import Point, LineString, Polygon
from pyproj import Transformer
import os
import threading
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

# ================== CONFIG DEFAULT ==================

# Coordenadas Guerrero Costa Chica (EPSG:6369 - Mexico ITRF2008 / UTM zone 14N)
GUERRERO_COSTA_CHICA_UTM = (436770.3242, 1832196.0532, 506936.9275, 1892877.8394)  # xmin, ymin, xmax, ymax
GUERRERO_COSTA_CHICA_EPSG = 6369

# Coordenadas Cuenca Valle de México (comentadas, para referencia)
# CUENCA_VALLE_MEXICO_UTM = (416316.969, 2079317.310, 617400.705, 2256323.915)  # xmin, ymin, xmax, ymax
# CUENCA_VALLE_MEXICO_EPSG = 32614

# Usar Guerrero Costa Chica por defecto
DEFAULT_GEO_BOUNDS_UTM = GUERRERO_COSTA_CHICA_UTM
DEFAULT_EPSG_SRC = GUERRERO_COSTA_CHICA_EPSG

DEFAULT_VIDEO_FILE = "./Mesa3D/20250327_130515_referencia.mp4"
DEFAULT_VIDEO_SPEED_MS = 25

# Ventanas: 0 = ninguna, 1 = solo recorte, 2 = todas
DEFAULT_SHOW_MODE = 1
DEFAULT_SCALE = 1.0  # Escala inicial = 1
DEFAULT_RECALIB_EVERY = 0  # 0 = calibración manual (no automática)

# Umbrales/Parámetros por defecto
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

def init_transformer(epsg_src=6369, epsg_dst=4326):
    return Transformer.from_crs(epsg_src, epsg_dst, always_xy=True)

def find_blue_cross_corners(frame):
    """
    Detecta las 4 cruces AZULES en las esquinas del área de trabajo.
    Retorna las esquinas ordenadas y el bounding box.
    """
    blurred = cv2.GaussianBlur(frame, (3, 3), 0)
    hsv = cv2.cvtColor(blurred, cv2.COLOR_BGR2HSV)
    
    # Rango HSV para azul (cruces azules)
    lower = np.array([100, 80, 80], dtype=np.uint8)
    upper = np.array([130, 255, 255], dtype=np.uint8)
    mask = cv2.inRange(hsv, lower, upper)
    
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel)
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    centroids = []
    for c in contours:
        area = cv2.contourArea(c)
        if area < 20:
            continue
        x, y, w, h = cv2.boundingRect(c)
        ar = w / h if h > 0 else 0
        if ar < 0.3 or ar > 3.5:
            continue
        M = cv2.moments(c)
        if M["m00"] > 0:
            cx = M["m10"] / M["m00"]
            cy = M["m01"] / M["m00"]
            centroids.append((cx, cy))

    if len(centroids) < 4:
        return None, None, {"mask_blue": mask}

    pts = np.array(centroids, dtype=np.float32)
    s = pts.sum(axis=1)
    d = np.diff(pts, axis=1).ravel()
    tl = pts[np.argmin(s)]
    br = pts[np.argmax(s)]
    tr = pts[np.argmax(d)]
    bl = pts[np.argmin(d)]
    corners = np.array([tl, tr, br, bl], dtype=np.float32)

    xs = corners[:, 0]
    ys = corners[:, 1]
    xmin, xmax = int(np.floor(xs.min())), int(np.ceil(xs.max()))
    ymin, ymax = int(np.floor(ys.min())), int(np.ceil(ys.max()))
    pad = 5
    xmin = max(0, xmin - pad)
    ymin = max(0, ymin - pad)
    xmax = min(frame.shape[1] - 1, xmax + pad)
    ymax = min(frame.shape[0] - 1, ymax + pad)

    return corners, (xmin, ymin, xmax, ymax), {"mask_blue": mask}

def compute_homography_from_corners(corners_img, geo_bounds_utm):
    xmin, ymin, xmax, ymax = geo_bounds_utm
    dst_pts = np.array([
        [xmin, ymax],  # TL imagen -> Norte (ymax)
        [xmin, ymin],  # TR imagen -> Sur (ymin)
        [xmax, ymin],  # BR
        [xmax, ymax],  # BL
    ], dtype=np.float32)
    H, _ = cv2.findHomography(corners_img, dst_pts, method=cv2.RANSAC)
    return H

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
    features = []
    for d in datos:
        if d['geometry'] is None:
            continue  # sin georreferencia: no guardamos nada
        if d['geometry'].geom_type.lower() == tipo_geom.lower():
            feature = {
                "type": "Feature",
                "properties": {"color": d['color'], "type": d['type']},
                "geometry": d['geometry'].__geo_interface__
            }
            features.append(feature)
    if features:
        try:
            gdf = gpd.GeoDataFrame.from_features(features, crs="EPSG:4326")
            gdf.to_file(nombre, driver='GeoJSON')
        except Exception as e:
            print(f"[WARN] No se pudo escribir {nombre}: {e}")

# ================== Pipeline principal ==================

class LiveParams:
    """Parámetros compartidos (sliders) con lectura segura."""
    def __init__(self, init_dict):
        self._lock = threading.Lock()
        self._d = dict(init_dict)
    def update(self, **kwargs):
        with self._lock:
            self._d.update(kwargs)
    def get(self):
        with self._lock:
            return dict(self._d)

def process_frame_generic(frame_in, transformer, H, xoff, yoff, show_mode, scale, params):
    """
    Procesa un frame (recortado o completo).
    Detecta colores AMARILLO y VERDE.
    - Si H es None => SIN georreferencia: dibuja y cuenta, pero 'geometry' va en None (no se guardará).
    - Si H existe => georreferencia normal.
    """
    detections = []

    blurred = cv2.GaussianBlur(frame_in, (3, 3), 0)
    hsv = cv2.cvtColor(blurred, cv2.COLOR_BGR2HSV)

    # Colores originales: amarillo y verde
    color_ranges = {
        'yellow': (np.array([15, 105, 100]), np.array([30, 255, 255])),
        'green':  (np.array([35, 45,  40]), np.array([80, 255, 255]))
    }

    yellow_points = yellow_lines = yellow_polygons = 0
    green_points = green_lines = green_polygons = 0

    for color_name, (lower, upper) in color_ranges.items():
        base_mask = cv2.inRange(hsv, lower, upper)
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

            # Si hay H => proyecta; si no, quedará geometry=None (no se guardará)
            geo_points = []
            if H is not None:
                for point in approx:
                    cx, cy = point[0]
                    lonlat = raster_to_geo_homography(cx + xoff, cy + yoff, H, transformer)
                    if lonlat:
                        geo_points.append(lonlat)

            # Clasificación geométrica
            if H is None or len(geo_points) < 2:
                # sin homografía: representamos como punto para la UI, pero NO guardamos (geometry=None)
                geom_type = 'point'
                geometry = None if H is None else (Point(geo_points[0]) if geo_points else None)
            else:
                length, width, line_aspect = contour_line_metrics(approx)
                if (area >= params["MIN_AREA_LINE"] and
                    line_aspect >= params["MIN_LINE_ASPECT"] and
                    max(w2, h2) >= params["MIN_LINE_LENGTH"]):
                    geom_type = 'line'
                    geometry = LineString(geo_points)
                elif area > params["MIN_AREA_POLY"] and aspect_ratio < 2.0:
                    geom_type = 'polygon'
                    if geo_points[0] != geo_points[-1]:
                        geo_points.append(geo_points[0])
                    geometry = Polygon(geo_points)
                else:
                    geom_type = 'point'
                    geometry = Point(geo_points[0])

            # Dibujo en pantalla
            if show_mode >= 1:
                if geom_type == 'line':
                    color = (0, 255, 255) if color_name == 'yellow' else (0, 255, 0)
                    box_points = cv2.boxPoints(rect).astype(int)
                    cv2.drawContours(frame_in, [box_points], 0, color, 2)
                elif geom_type == 'polygon':
                    color = (0, 165, 255) if color_name == 'yellow' else (0, 100, 0)
                    cv2.drawContours(frame_in, [approx], -1, color, 2)
                else:
                    cv2.circle(frame_in, (int(x), int(y)), 5, (0, 0, 255), -1)

            # Stats
            if color_name == 'yellow':
                if geom_type == 'point': yellow_points += 1
                elif geom_type == 'line': yellow_lines += 1
                else:                      yellow_polygons += 1
            else:
                if geom_type == 'point': green_points += 1
                elif geom_type == 'line': green_lines += 1
                else:                      green_polygons += 1

            detections.append({
                "geometry": geometry,  # None si no hay H
                "color": color_name,
                "type": geom_type
            })

    if show_mode >= 1:
        display_frame = frame_in.copy()
        y_pos = 22
        cv2.putText(display_frame, "AMARILLO", (10, y_pos), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 215, 255), 1)
        cv2.putText(display_frame, f"P:{yellow_points} L:{yellow_lines} POL:{yellow_polygons}", (110, y_pos),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.45, (200, 255, 255), 1)
        y_pos += 22
        cv2.putText(display_frame, "VERDE", (10, y_pos), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (50, 205, 50), 1)
        cv2.putText(display_frame, f"P:{green_points} L:{green_lines} POL:{green_polygons}", (110, y_pos),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.45, (50, 255, 50), 1)
        draw_small("SISTEMA DE DETECCION (recorte)", display_frame, scale)

    return detections

def list_available_cameras(max_test=10):
    """
    Detecta cámaras disponibles probando índices.
    Retorna lista de índices que funcionan.
    """
    available = []
    for i in range(max_test):
        cap = cv2.VideoCapture(i)
        if cap.isOpened():
            ret, _ = cap.read()
            if ret:
                available.append(i)
            cap.release()
    return available

def run_pipeline(video_source, src_type, geo_bounds_utm, epsg_src,
                 show_mode, scale, video_speed, recalib_every,
                 live_params: LiveParams,
                 pause_event: threading.Event,
                 recalib_event: threading.Event,
                 stop_event: threading.Event):

    os.makedirs('geojson', exist_ok=True)
    transformer = init_transformer(epsg_src)

    # Abrir captura según el tipo de fuente
    if src_type == 'camera':
        # video_source es el índice de la cámara (int)
        cap = cv2.VideoCapture(int(video_source))
    else:
        # file o url
        cap = cv2.VideoCapture(video_source)
    
    if not cap.isOpened():
        messagebox.showerror("Error", f"No se pudo abrir la fuente de video: {video_source}")
        return

    # Inicialización SIN calibración automática
    # La calibración solo se hace cuando el usuario presiona "Recalibrar ahora"
    H = None
    crop_box = None

    # Loop principal
    frame_count = 0
    try:
        while not stop_event.is_set():
            ret, frame = cap.read()
            if not ret:
                # Para cámaras en vivo, intentar reconectar
                if src_type == 'camera' or src_type == 'url':
                    cap.release()
                    if src_type == 'camera':
                        cap = cv2.VideoCapture(int(video_source))
                    else:
                        cap = cv2.VideoCapture(video_source)
                    continue
                break
            frame_count += 1

            # Recalibración: solo automática si recalib_every > 0, o manual con botón
            need_recalib = False
            if recalib_every > 0 and (frame_count % recalib_every == 0):
                need_recalib = True
            if recalib_event.is_set():
                need_recalib = True
                recalib_event.clear()

            if need_recalib:
                corners_img2, crop_box2, dbg = find_blue_cross_corners(frame)
                if corners_img2 is not None and crop_box2 is not None:
                    H2 = compute_homography_from_corners(corners_img2, geo_bounds_utm)
                    if H2 is not None:
                        H = H2
                        crop_box = crop_box2
                        if show_mode == 2:
                            dbg_frame = frame.copy()
                            for (x, y) in corners_img2.astype(int):
                                cv2.drawMarker(dbg_frame, (x, y), (255, 0, 0),
                                               markerType=cv2.MARKER_CROSS, markerSize=18, thickness=2)
                            draw_small("Cruces Azules Detectadas", dbg_frame, scale)
                            if "mask_blue" in dbg:
                                draw_small("Mascara Azul (cruces)", cv2.cvtColor(dbg["mask_blue"], cv2.COLOR_GRAY2BGR), scale)
                            cv2.waitKey(150)
                else:
                    # Mostrar mensaje si no se encontraron cruces
                    if show_mode >= 1:
                        print("[INFO] No se detectaron 4 cruces azules en este intento de calibración.")

            # Elegir frame de entrada según modo
            if H is not None and crop_box is not None:
                xmin, ymin, xmax, ymax = crop_box
                frame_in = frame[ymin:ymax, xmin:xmax].copy()
                xoff, yoff = xmin, ymin
                calibrated = True
            else:
                frame_in = frame.copy()
                xoff, yoff = 0, 0
                calibrated = False

            # Pausa: solo mostrar, sin procesar ni guardar
            if pause_event.is_set():
                if show_mode >= 1:
                    overlay = frame_in.copy()
                    cv2.putText(overlay, "PAUSADO", (20, 40),
                                cv2.FONT_HERSHEY_SIMPLEX, 1.1, (0, 0, 255), 3, cv2.LINE_AA)
                    draw_small("SISTEMA DE DETECCION (recorte)", overlay, scale)
                if cv2.waitKey(video_speed) & 0xFF == ord('q'):
                    break
                continue

            # Procesamiento
            params_now = live_params.get()
            detections = process_frame_generic(frame_in, transformer, H if calibrated else None,
                                               xoff, yoff, show_mode, scale, params_now)

            # Overlay de estado de calibración
            if show_mode >= 1 and not calibrated:
                overlay = frame_in.copy()
                cv2.putText(overlay, "SIN CALIBRACION - Presiona 'Recalibrar ahora'", (20, 40),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 140, 255), 2, cv2.LINE_AA)
                cv2.putText(overlay, "(Detectando sin georreferencia)", (20, 70),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 140, 255), 1, cv2.LINE_AA)
                draw_small("SISTEMA DE DETECCION (recorte)", overlay, scale)

            # Guardado solo si H activo (georreferenciado)
            if calibrated and detections:
                guardar_geojson(detections, 'Point',      'geojson/detecciones_puntos.geojson')
                guardar_geojson(detections, 'LineString', 'geojson/detecciones_lineas.geojson')
                guardar_geojson(detections, 'Polygon',    'geojson/detecciones_poligonos.geojson')

            if cv2.waitKey(video_speed) & 0xFF == ord('q'):
                break

    finally:
        cap.release()
        cv2.destroyAllWindows()

# ================== Interfaz Tkinter ==================

class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Detección Mesa 3D – Configuración (v6 - cruces azules, cámara USB)")
        self.geometry("850x780")
        self.resizable(False, False)

        # Fuente de video (file, url, o camera)
        self.src_type = tk.StringVar(value="file")
        self.video_path = tk.StringVar(value=DEFAULT_VIDEO_FILE)
        self.url_str = tk.StringVar(value="http://127.0.0.1:4747/video")
        self.camera_index = tk.StringVar(value="0")

        # Geografía - usando guerrero_costa_chica por defecto
        xmin, ymin, xmax, ymax = DEFAULT_GEO_BOUNDS_UTM
        self.epsg_src = tk.StringVar(value=str(DEFAULT_EPSG_SRC))
        self.xmin = tk.StringVar(value=str(xmin))
        self.ymin = tk.StringVar(value=str(ymin))
        self.xmax = tk.StringVar(value=str(xmax))
        self.ymax = tk.StringVar(value=str(ymax))
        
        # Presets de coordenadas
        self.coord_preset = tk.StringVar(value="guerrero_costa_chica")

        # Visualización / loop
        self.show_mode = tk.IntVar(value=DEFAULT_SHOW_MODE)
        self.scale = tk.DoubleVar(value=DEFAULT_SCALE)
        self.video_speed = tk.IntVar(value=DEFAULT_VIDEO_SPEED_MS)
        self.recalib_every = tk.IntVar(value=DEFAULT_RECALIB_EVERY)

        # Flags/control del hilo
        self.pause_event = threading.Event()
        self.recalib_event = threading.Event()
        self.stop_event = threading.Event()
        self.worker_thread = None

        # Live params
        self.live_params = LiveParams(DEFAULT_PARAMS)

        self._build()
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    # ------- UI helpers -------
    def _labeled_scale(self, parent, text, from_, to, var_type=float, init=None, step=1.0, fmt="{:.1f}"):
        frm = ttk.Frame(parent)
        ttk.Label(frm, text=text).pack(side="left")
        val_lbl = ttk.Label(frm, text="")
        val_lbl.pack(side="right")
        sv = tk.DoubleVar(value=float(init) if init is not None else float(from_))
        scl = ttk.Scale(frm, from_=from_, to=to, orient="horizontal", variable=sv, length=240)
        scl.pack(fill="x", padx=8)
        def on_move(_=None):
            v = sv.get()
            if var_type is int:
                v = int(round(v / step) * step)
                val_lbl.config(text=f"{v}")
            else:
                v = round(v / step) * step
                val_lbl.config(text=fmt.format(v))
        scl.configure(command=on_move)
        on_move()
        return frm, sv, on_move

    def _apply_preset(self, *args):
        preset = self.coord_preset.get()
        if preset == "guerrero_costa_chica":
            xmin, ymin, xmax, ymax = GUERRERO_COSTA_CHICA_UTM
            epsg = GUERRERO_COSTA_CHICA_EPSG
        # elif preset == "cuenca_valle_mexico":
        #     xmin, ymin, xmax, ymax = CUENCA_VALLE_MEXICO_UTM
        #     epsg = CUENCA_VALLE_MEXICO_EPSG
        else:
            return
        
        self.xmin.set(str(xmin))
        self.ymin.set(str(ymin))
        self.xmax.set(str(xmax))
        self.ymax.set(str(ymax))
        self.epsg_src.set(str(epsg))

    def _detect_cameras(self):
        """Detecta cámaras disponibles y actualiza el combobox."""
        self.btn_detect_cam.config(state="disabled")
        self.update()
        
        cameras = list_available_cameras(10)
        if cameras:
            self.camera_combo['values'] = [str(i) for i in cameras]
            self.camera_index.set(str(cameras[0]))
            messagebox.showinfo("Cámaras detectadas", 
                f"Se encontraron {len(cameras)} cámara(s):\nÍndices: {cameras}\n\n"
                "Índice 0 suele ser la cámara integrada.\n"
                "Índices mayores suelen ser cámaras USB o capturadoras.")
        else:
            messagebox.showwarning("Sin cámaras", 
                "No se detectaron cámaras disponibles.\n\n"
                "Verifica que:\n"
                "• La cámara/capturadora esté conectada\n"
                "• Los drivers estén instalados\n"
                "• No esté siendo usada por otra aplicación")
        
        self.btn_detect_cam.config(state="normal")

    def _build(self):
        pad = {'padx': 8, 'pady': 4}

        # Fuente de video
        frm_src = ttk.LabelFrame(self, text="Fuente de video")
        frm_src.pack(fill="x", **pad)
        
        # Radiobuttons para tipo de fuente
        ttk.Radiobutton(frm_src, text="Archivo", value="file", variable=self.src_type).grid(row=0, column=0, sticky="w", padx=6)
        ttk.Radiobutton(frm_src, text="URL (DroidCam/HTTP/RTSP)", value="url", variable=self.src_type).grid(row=0, column=1, sticky="w", padx=6)
        ttk.Radiobutton(frm_src, text="Cámara/Capturadora USB", value="camera", variable=self.src_type).grid(row=0, column=2, sticky="w", padx=6)
        
        # Archivo
        ttk.Label(frm_src, text="Ruta archivo:").grid(row=1, column=0, sticky="e")
        ent_file = ttk.Entry(frm_src, textvariable=self.video_path, width=50)
        ent_file.grid(row=1, column=1, columnspan=2, sticky="we")
        ttk.Button(frm_src, text="Buscar…", command=self.pick_file).grid(row=1, column=3, padx=6)
        
        # URL
        ttk.Label(frm_src, text="URL:").grid(row=2, column=0, sticky="e")
        ttk.Entry(frm_src, textvariable=self.url_str, width=50).grid(row=2, column=1, columnspan=2, sticky="we")
        
        # Cámara/Capturadora
        ttk.Label(frm_src, text="Índice cámara:").grid(row=3, column=0, sticky="e")
        self.camera_combo = ttk.Combobox(frm_src, textvariable=self.camera_index, 
                                         values=["0", "1", "2", "3", "4"], width=8, state="readonly")
        self.camera_combo.grid(row=3, column=1, sticky="w", padx=4)
        self.btn_detect_cam = ttk.Button(frm_src, text="🔍 Detectar cámaras", command=self._detect_cameras)
        self.btn_detect_cam.grid(row=3, column=2, sticky="w", padx=6)
        
        # Info sobre cámaras
        ttk.Label(frm_src, text="(0=integrada, 1+=USB/capturadora)", 
                  foreground="gray").grid(row=3, column=3, sticky="w")
        
        for i in range(4):
            frm_src.grid_columnconfigure(i, weight=1)

        # Geo con presets
        frm_geo = ttk.LabelFrame(self, text="Extremos UTM (EPSG origen)")
        frm_geo.pack(fill="x", **pad)
        
        # Preset selector
        ttk.Label(frm_geo, text="Preset:").grid(row=0, column=0, sticky="e")
        preset_combo = ttk.Combobox(frm_geo, textvariable=self.coord_preset, 
                                    values=["guerrero_costa_chica"], state="readonly", width=25)
        preset_combo.grid(row=0, column=1, sticky="w", padx=4)
        preset_combo.bind("<<ComboboxSelected>>", self._apply_preset)
        
        ttk.Label(frm_geo, text="EPSG origen:").grid(row=1, column=0, sticky="e")
        ttk.Entry(frm_geo, textvariable=self.epsg_src, width=10).grid(row=1, column=1, sticky="w")
        ttk.Label(frm_geo, text="xmin:").grid(row=2, column=0, sticky="e"); ttk.Entry(frm_geo, textvariable=self.xmin, width=14).grid(row=2, column=1, sticky="w")
        ttk.Label(frm_geo, text="ymin:").grid(row=2, column=2, sticky="e"); ttk.Entry(frm_geo, textvariable=self.ymin, width=14).grid(row=2, column=3, sticky="w")
        ttk.Label(frm_geo, text="xmax:").grid(row=3, column=0, sticky="e"); ttk.Entry(frm_geo, textvariable=self.xmax, width=14).grid(row=3, column=1, sticky="w")
        ttk.Label(frm_geo, text="ymax:").grid(row=3, column=2, sticky="e"); ttk.Entry(frm_geo, textvariable=self.ymax, width=14).grid(row=3, column=3, sticky="w")
        for i in range(4):
            frm_geo.grid_columnconfigure(i, weight=1)

        # Visualización / Loop
        frm_opt = ttk.LabelFrame(self, text="Visualización y loop")
        frm_opt.pack(fill="x", **pad)
        ttk.Label(frm_opt, text="Ventanas:").grid(row=0, column=0, sticky="e")
        ttk.Radiobutton(frm_opt, text="Ninguna", value=0, variable=self.show_mode).grid(row=0, column=1, sticky="w")
        ttk.Radiobutton(frm_opt, text="Solo recorte", value=1, variable=self.show_mode).grid(row=0, column=2, sticky="w")
        ttk.Radiobutton(frm_opt, text="Todas", value=2, variable=self.show_mode).grid(row=0, column=3, sticky="w")
        ttk.Label(frm_opt, text="Escala (0.2–1.5):").grid(row=1, column=0, sticky="e")
        ttk.Entry(frm_opt, textvariable=self.scale, width=10).grid(row=1, column=1, sticky="w")
        ttk.Label(frm_opt, text="Velocidad (ms):").grid(row=1, column=2, sticky="e")
        ttk.Entry(frm_opt, textvariable=self.video_speed, width=10).grid(row=1, column=3, sticky="w")
        ttk.Label(frm_opt, text="Recalibrar cada N frames (0=manual):").grid(row=2, column=0, sticky="e")
        ttk.Entry(frm_opt, textvariable=self.recalib_every, width=10).grid(row=2, column=1, sticky="w")

        # Info sobre calibración
        info_frame = ttk.Frame(self)
        info_frame.pack(fill="x", **pad)
        ttk.Label(info_frame, text="⚠️ Calibración MANUAL por defecto. Las cruces de calibración deben ser AZULES.", 
                  foreground="blue").pack(anchor="w")

        # Pestañas de parámetros
        nb = ttk.Notebook(self)
        nb.pack(fill="x", **pad)

        tab_pts = ttk.Frame(nb); nb.add(tab_pts, text="Puntos")
        tab_lin = ttk.Frame(nb); nb.add(tab_lin, text="Líneas")
        tab_pol = ttk.Frame(nb); nb.add(tab_pol, text="Polígonos")
        tab_mor = ttk.Frame(nb); nb.add(tab_mor, text="Morfología")
        tab_cam = ttk.Frame(nb); nb.add(tab_cam, text="Info Cámara USB")

        # --- Puntos ---
        f, sv_min_area_point, _ = self._labeled_scale(tab_pts, "Área mínima punto", 1, 200, var_type=int,
                                                      init=DEFAULT_PARAMS["MIN_AREA_POINT"], step=1, fmt="{:.0f}")
        f.pack(fill="x", pady=4, padx=6)

        # --- Líneas ---
        f, sv_min_area_line, _ = self._labeled_scale(tab_lin, "Área mínima línea", 5, 1000, var_type=int,
                                                     init=DEFAULT_PARAMS["MIN_AREA_LINE"], step=5, fmt="{:.0f}"); f.pack(fill="x", pady=4, padx=6)
        f, sv_min_len, _        = self._labeled_scale(tab_lin, "Largo mínimo (px)", 5, 200, var_type=int,
                                                     init=DEFAULT_PARAMS["MIN_LINE_LENGTH"], step=1, fmt="{:.0f}"); f.pack(fill="x", pady=4, padx=6)
        f, sv_min_aspect, _     = self._labeled_scale(tab_lin, "Aspecto mínimo (L/A)", 1.0, 12.0, var_type=float,
                                                     init=DEFAULT_PARAMS["MIN_LINE_ASPECT"], step=0.5, fmt="{:.1f}"); f.pack(fill="x", pady=4, padx=6)

        # --- Polígonos ---
        f, sv_min_area_poly, _ = self._labeled_scale(tab_pol, "Área mínima polígono", 50, 5000, var_type=int,
                                                     init=DEFAULT_PARAMS["MIN_AREA_POLY"], step=10, fmt="{:.0f}")
        f.pack(fill="x", pady=4, padx=6)

        # --- Morfología ---
        f, sv_k_long, _  = self._labeled_scale(tab_mor, "Kernel largo (px)", 3, 51, var_type=int,
                                               init=DEFAULT_PARAMS["K_LONG"], step=2, fmt="{:.0f}"); f.pack(fill="x", pady=4, padx=6)
        f, sv_k_short, _ = self._labeled_scale(tab_mor, "Kernel corto (px)", 3, 15, var_type=int,
                                               init=DEFAULT_PARAMS["K_SHORT"], step=2, fmt="{:.0f}"); f.pack(fill="x", pady=4, padx=6)
        f, sv_m_iters, _ = self._labeled_scale(tab_mor, "Iteraciones morfología", 0, 5, var_type=int,
                                               init=DEFAULT_PARAMS["MORPH_ITERS"], step=1, fmt="{:.0f}"); f.pack(fill="x", pady=4, padx=6)

        # --- Info Cámara USB ---
        camera_info = """CONFIGURACIÓN DE CÁMARA/CAPTURADORA POR CABLE USB

OPCIONES DE HARDWARE:

1. CÁMARA WEB USB
   • Cualquier webcam USB estándar
   • Plug & Play en la mayoría de sistemas
   • Índice típico: 0 (si no hay cámara integrada) o 1

2. CAPTURADORA DE VIDEO HDMI → USB
   • Dispositivos como: Elgato Cam Link, AVerMedia, genéricas
   • Conecta cámara HDMI/DSLR al puerto USB
   • Aparece como webcam en el sistema
   • Índice típico: 1 o 2

3. CÁMARA INDUSTRIAL/CIENTÍFICA USB
   • Cámaras con SDK propio (pueden requerir drivers)
   • Basler, FLIR, IDS, etc.

REQUISITOS:
• Drivers instalados (Windows puede instalarlos automáticamente)
• Cámara NO en uso por otra aplicación
• Cable USB de buena calidad (preferir USB 3.0 para HD)

SOLUCIÓN DE PROBLEMAS:
• Si no detecta: desconectar y reconectar
• Probar diferentes puertos USB
• Verificar en "Administrador de dispositivos" (Windows)
• En Linux: ejecutar 'ls /dev/video*' para ver dispositivos

ÍNDICES TÍPICOS:
• 0 = Cámara integrada del laptop
• 1 = Primera cámara USB externa
• 2+ = Cámaras adicionales o capturadoras"""
        
        text_widget = tk.Text(tab_cam, wrap="word", height=20, width=70)
        text_widget.insert("1.0", camera_info)
        text_widget.config(state="disabled")
        text_widget.pack(fill="both", expand=True, padx=10, pady=10)

        # Vincular sliders => live_params
        def sync_params(*_):
            self.live_params.update(
                MIN_AREA_POINT=int(sv_min_area_point.get()),
                MIN_AREA_LINE=int(sv_min_area_line.get()),
                MIN_LINE_LENGTH=int(sv_min_len.get()),
                MIN_LINE_ASPECT=float(sv_min_aspect.get()),
                MIN_AREA_POLY=int(sv_min_area_poly.get()),
                K_LONG=int(sv_k_long.get()),
                K_SHORT=int(sv_k_short.get()),
                MORPH_ITERS=int(sv_m_iters.get()),
            )
        for sv in (sv_min_area_point, sv_min_area_line, sv_min_len, sv_min_aspect,
                   sv_min_area_poly, sv_k_long, sv_k_short, sv_m_iters):
            sv.trace_add('write', sync_params)
        sync_params()

        # Botones de control
        frm_btn = ttk.Frame(self); frm_btn.pack(fill="x", **pad)
        ttk.Button(frm_btn, text="Iniciar detección", command=self.start_detection).pack(side="left", padx=6)
        ttk.Button(frm_btn, text="Pausar captura", command=self.pause_capture).pack(side="left", padx=6)
        ttk.Button(frm_btn, text="Reanudar captura", command=self.resume_capture).pack(side="left", padx=6)
        
        # Botón de recalibrar destacado
        recalib_btn = ttk.Button(frm_btn, text="🔵 Recalibrar ahora", command=self.force_recalib)
        recalib_btn.pack(side="left", padx=6)
        
        ttk.Button(frm_btn, text="Salir", command=self._on_close).pack(side="right", padx=6)

    def pick_file(self):
        path = filedialog.askopenfilename(
            title="Seleccionar video",
            filetypes=[("Videos", "*.mp4;*.avi;*.mov;*.mkv"), ("Todos", "*.*")]
        )
        if path:
            self.video_path.set(path)

    def start_detection(self):
        if self.worker_thread and self.worker_thread.is_alive():
            messagebox.showinfo("Ejecución", "La detección ya está corriendo.")
            return

        # Reset flags
        self.pause_event.clear()
        self.recalib_event.clear()
        self.stop_event.clear()

        try:
            epsg_src = int(self.epsg_src.get())
            xmin = float(self.xmin.get()); ymin = float(self.ymin.get())
            xmax = float(self.xmax.get()); ymax = float(self.ymax.get())
            geo_bounds = (xmin, ymin, xmax, ymax)
            video_speed = int(self.video_speed.get())
            scale = float(self.scale.get()); assert 0.1 <= scale <= 1.5
            recalib_every = int(self.recalib_every.get()); recalib_every = max(0, recalib_every)
        except Exception as e:
            messagebox.showerror("Error", f"Parámetros inválidos: {e}")
            return

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
        else:  # 'camera'
            try:
                source = int(self.camera_index.get())
            except ValueError:
                messagebox.showwarning("Cámara", "Índice de cámara inválido.")
                return

        self.worker_thread = threading.Thread(
            target=run_pipeline,
            args=(source, src_type, geo_bounds, epsg_src,
                  self.show_mode.get(), scale, video_speed, recalib_every,
                  self.live_params, self.pause_event, self.recalib_event, self.stop_event),
            daemon=True
        )
        self.worker_thread.start()
        
        source_info = f"Cámara índice {source}" if src_type == 'camera' else source
        messagebox.showinfo("Ejecutando",
                            f"Detección iniciada.\n\n"
                            f"Fuente: {source_info}\n\n"
                            "IMPORTANTE:\n"
                            "• La calibración es MANUAL por defecto\n"
                            "• Las cruces deben ser de color AZUL\n"
                            "• Presiona 'Recalibrar ahora' cuando las cruces sean visibles\n\n"
                            "Sin calibración: se detecta sin georreferencia.\n"
                            "Con calibración: se recorta y guarda GeoJSON.")

    def pause_capture(self):
        self.pause_event.set()

    def resume_capture(self):
        self.pause_event.clear()

    def force_recalib(self):
        self.recalib_event.set()

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