# -*- coding: utf-8 -*-
"""
Detector con UI (Tkinter) – v5
Cambios v5:
- Botones: Pausar captura / Reanudar captura / Recalibrar ahora.
- Sin opción de webcam (solo Archivo o URL/DroidCam).
- La pausa detiene el procesamiento y guardado, pero el video se sigue mostrando.
- Overlay "PAUSADO" cuando la captura está detenida.
- Recalibración automática cada N frames (default 100) + manual por botón.

Requiere: opencv-python, numpy, geopandas, shapely, pyproj
pip install opencv-python numpy geopandas shapely pyproj
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
DEFAULT_GEO_BOUNDS_UTM = (416316.969, 2079317.310, 617400.705, 2256323.915)  # xmin, ymin, xmax, ymax
DEFAULT_EPSG_SRC = 32614
DEFAULT_VIDEO_FILE = "./Mesa3D/20250327_130515_referencia.mp4"
DEFAULT_VIDEO_SPEED_MS = 25

# Ventanas: 0 = ninguna, 1 = solo recorte, 2 = todas
DEFAULT_SHOW_MODE = 1
DEFAULT_SCALE = 0.45
DEFAULT_RECALIB_EVERY = 100   # pedido

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

def init_transformer(epsg_src=32614, epsg_dst=4326):
    return Transformer.from_crs(epsg_src, epsg_dst, always_xy=True)


def find_green_cross_corners(frame):
    blurred = cv2.GaussianBlur(frame, (3, 3), 0)
    hsv = cv2.cvtColor(blurred, cv2.COLOR_BGR2HSV)
    lower = np.array([35, 45, 40], dtype=np.uint8)
    upper = np.array([80, 255, 255], dtype=np.uint8)
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
        return None, None, {"mask_green": mask}

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

    return corners, (xmin, ymin, xmax, ymax), {"mask_green": mask}


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
        if d['geometry'].geom_type.lower() == tipo_geom.lower():
            feature = {
                "type": "Feature",
                "properties": {"color": d['color'], "type": d['type']},
                "geometry": d['geometry'].__geo_interface__
            }
            features.append(feature)
    if features:
        gdf = gpd.GeoDataFrame.from_features(features, crs="EPSG:4326")
        gdf.to_file(nombre, driver='GeoJSON')


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


def process_frame_with_offset(frame_cropped, transformer, H, xoff, yoff, show_mode, scale, params):
    detections = []

    blurred = cv2.GaussianBlur(frame_cropped, (3, 3), 0)
    hsv = cv2.cvtColor(blurred, cv2.COLOR_BGR2HSV)

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

            # Proyecta a geo
            geo_points = []
            for point in approx:
                cx, cy = point[0]
                lonlat = raster_to_geo_homography(cx + xoff, cy + yoff, H, transformer)
                if lonlat:
                    geo_points.append(lonlat)

            if len(geo_points) < 2:
                geom_type = 'point'
                geometry = Point(geo_points[0]) if geo_points else None
            else:
                # Medidas de linealidad
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

            if geometry is None:
                continue

            if show_mode >= 1:
                if geom_type == 'line':
                    color = (0, 255, 255) if color_name == 'yellow' else (0, 255, 0)
                    box_points = cv2.boxPoints(rect).astype(int)
                    cv2.drawContours(frame_cropped, [box_points], 0, color, 2)
                elif geom_type == 'polygon':
                    color = (0, 165, 255) if color_name == 'yellow' else (0, 100, 0)
                    cv2.drawContours(frame_cropped, [approx], -1, color, 2)
                else:
                    cv2.circle(frame_cropped, (int(x), int(y)), 5, (0, 0, 255), -1)

            if color_name == 'yellow':
                if geom_type == 'point': yellow_points += 1
                elif geom_type == 'line': yellow_lines += 1
                else:                      yellow_polygons += 1
            else:
                if geom_type == 'point': green_points += 1
                elif geom_type == 'line': green_lines += 1
                else:                      green_polygons += 1

            detections.append({
                "geometry": geometry,
                "color": color_name,
                "type": geom_type
            })

    if show_mode >= 1:
        display_frame = frame_cropped.copy()
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


def run_pipeline(video_source, src_type, geo_bounds_utm, epsg_src,
                 show_mode, scale, video_speed, recalib_every,
                 live_params: LiveParams,
                 pause_event: threading.Event,
                 recalib_event: threading.Event,
                 stop_event: threading.Event):
    """
    pause_event: si está SET -> PAUSADO (no procesa/guarda)
    recalib_event: si está SET -> recalibrar en el próximo frame
    stop_event: para terminar el hilo con seguridad
    """
    os.makedirs('geojson', exist_ok=True)
    transformer = init_transformer(epsg_src)

    # Abrir captura
    if src_type == 'file':
        cap = cv2.VideoCapture(video_source)
    else:  # 'url'
        cap = cv2.VideoCapture(video_source)

    if not cap.isOpened():
        messagebox.showerror("Error", f"No se pudo abrir la fuente de video: {video_source}")
        return

    # Calibración inicial
    H = None
    crop_box = None
    for _ in range(120):
        if stop_event.is_set(): break
        ret, frame0 = cap.read()
        if not ret:
            break
        corners_img, crop_box, dbg = find_green_cross_corners(frame0)
        if corners_img is not None:
            H = compute_homography_from_corners(corners_img, geo_bounds_utm)
            if show_mode == 2:
                dbg_frame = frame0.copy()
                for (x, y) in corners_img.astype(int):
                    cv2.drawMarker(dbg_frame, (x, y), (0, 255, 0),
                                   markerType=cv2.MARKER_CROSS, markerSize=18, thickness=2)
                draw_small("Cruces Verdes Detectadas", dbg_frame, scale)
                if "mask_green" in dbg:
                    draw_small("Mascara Verde (cruces)", cv2.cvtColor(dbg["mask_green"], cv2.COLOR_GRAY2BGR), scale)
                cv2.waitKey(150)
            break

    if H is None or crop_box is None:
        cap.release()
        cv2.destroyAllWindows()
        messagebox.showwarning("Aviso", "No se detectaron 4 cruces verdes para calibrar.")
        return

    xmin, ymin, xmax, ymax = crop_box

    # Loop principal con recalibración
    frame_count = 0
    try:
        while not stop_event.is_set():
            ret, frame = cap.read()
            if not ret:
                break
            frame_count += 1

            # Recalibración automática o forzada
            need_recalib = False
            if recalib_every > 0 and (frame_count % recalib_every == 0):
                need_recalib = True
            if recalib_event.is_set():
                need_recalib = True
                recalib_event.clear()

            if need_recalib:
                corners_img2, crop_box2, _ = find_green_cross_corners(frame)
                if corners_img2 is not None and crop_box2 is not None:
                    H2 = compute_homography_from_corners(corners_img2, geo_bounds_utm)
                    if H2 is not None:
                        H = H2
                        xmin, ymin, xmax, ymax = crop_box2

            # Recorte y display
            frame_cropped = frame[ymin:ymax, xmin:xmax].copy()

            if pause_event.is_set():
                # Mostrar overlay "PAUSADO"
                if show_mode >= 1:
                    overlay = frame_cropped.copy()
                    cv2.putText(overlay, "PAUSADO", (20, 40),
                                cv2.FONT_HERSHEY_SIMPLEX, 1.1, (0, 0, 255), 3, cv2.LINE_AA)
                    draw_small("SISTEMA DE DETECCION (recorte)", overlay, scale)
                # seguir mostrando sin procesar ni guardar
                if cv2.waitKey(video_speed) & 0xFF == ord('q'):
                    break
                continue

            # Procesamiento normal
            params_now = live_params.get()
            detections = process_frame_with_offset(frame_cropped, transformer, H, xmin, ymin, show_mode, scale, params_now)

            if detections:
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
        self.title("Detección Mesa 3D – Configuración (v5)")
        self.geometry("820x720")
        self.resizable(False, False)

        # Fuente de video (solo file o url)
        self.src_type = tk.StringVar(value="file")
        self.video_path = tk.StringVar(value=DEFAULT_VIDEO_FILE)
        self.url_str = tk.StringVar(value="http://127.0.0.1:4747/video")

        # Geografía
        xmin, ymin, xmax, ymax = DEFAULT_GEO_BOUNDS_UTM
        self.epsg_src = tk.StringVar(value=str(DEFAULT_EPSG_SRC))
        self.xmin = tk.StringVar(value=str(xmin))
        self.ymin = tk.StringVar(value=str(ymin))
        self.xmax = tk.StringVar(value=str(xmax))
        self.ymax = tk.StringVar(value=str(ymax))

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

    def _build(self):
        pad = {'padx': 8, 'pady': 4}

        # Fuente
        frm_src = ttk.LabelFrame(self, text="Fuente de video")
        frm_src.pack(fill="x", **pad)
        ttk.Radiobutton(frm_src, text="Archivo", value="file", variable=self.src_type).grid(row=0, column=0, sticky="w", padx=6)
        ttk.Radiobutton(frm_src, text="URL (DroidCam/HTTP/RTSP)", value="url", variable=self.src_type).grid(row=0, column=1, sticky="w", padx=6)
        ttk.Label(frm_src, text="Ruta archivo:").grid(row=1, column=0, sticky="e")
        ent_file = ttk.Entry(frm_src, textvariable=self.video_path, width=54)
        ent_file.grid(row=1, column=1, sticky="we")
        ttk.Button(frm_src, text="Buscar…", command=self.pick_file).grid(row=1, column=2, padx=6)
        ttk.Label(frm_src, text="URL:").grid(row=2, column=0, sticky="e")
        ttk.Entry(frm_src, textvariable=self.url_str, width=54).grid(row=2, column=1, sticky="we")
        for i in range(3):
            frm_src.grid_columnconfigure(i, weight=1)

        # Geo
        frm_geo = ttk.LabelFrame(self, text="Extremos UTM (EPSG origen)")
        frm_geo.pack(fill="x", **pad)
        ttk.Label(frm_geo, text="EPSG origen:").grid(row=0, column=0, sticky="e")
        ttk.Entry(frm_geo, textvariable=self.epsg_src, width=10).grid(row=0, column=1, sticky="w")
        ttk.Label(frm_geo, text="xmin:").grid(row=1, column=0, sticky="e"); ttk.Entry(frm_geo, textvariable=self.xmin, width=14).grid(row=1, column=1, sticky="w")
        ttk.Label(frm_geo, text="ymin:").grid(row=1, column=2, sticky="e"); ttk.Entry(frm_geo, textvariable=self.ymin, width=14).grid(row=1, column=3, sticky="w")
        ttk.Label(frm_geo, text="xmax:").grid(row=2, column=0, sticky="e"); ttk.Entry(frm_geo, textvariable=self.xmax, width=14).grid(row=2, column=1, sticky="w")
        ttk.Label(frm_geo, text="ymax:").grid(row=2, column=2, sticky="e"); ttk.Entry(frm_geo, textvariable=self.ymax, width=14).grid(row=2, column=3, sticky="w")
        for i in range(4):
            frm_geo.grid_columnconfigure(i, weight=1)

        # Visualización / Loop
        frm_opt = ttk.LabelFrame(self, text="Visualización y loop")
        frm_opt.pack(fill="x", **pad)
        ttk.Label(frm_opt, text="Ventanas:").grid(row=0, column=0, sticky="e")
        ttk.Radiobutton(frm_opt, text="Ninguna", value=0, variable=self.show_mode).grid(row=0, column=1, sticky="w")
        ttk.Radiobutton(frm_opt, text="Solo recorte", value=1, variable=self.show_mode).grid(row=0, column=2, sticky="w")
        ttk.Radiobutton(frm_opt, text="Todas", value=2, variable=self.show_mode).grid(row=0, column=3, sticky="w")
        ttk.Label(frm_opt, text="Escala (0.2–0.9):").grid(row=1, column=0, sticky="e")
        ttk.Entry(frm_opt, textvariable=self.scale, width=10).grid(row=1, column=1, sticky="w")
        ttk.Label(frm_opt, text="Velocidad (ms):").grid(row=1, column=2, sticky="e")
        ttk.Entry(frm_opt, textvariable=self.video_speed, width=10).grid(row=1, column=3, sticky="w")
        ttk.Label(frm_opt, text="Recalibrar cada N frames:").grid(row=2, column=0, sticky="e")
        ttk.Entry(frm_opt, textvariable=self.recalib_every, width=10).grid(row=2, column=1, sticky="w")

        # Pestañas de parámetros
        nb = ttk.Notebook(self)
        nb.pack(fill="x", **pad)

        tab_pts = ttk.Frame(nb); nb.add(tab_pts, text="Puntos")
        tab_lin = ttk.Frame(nb); nb.add(tab_lin, text="Líneas")
        tab_pol = ttk.Frame(nb); nb.add(tab_pol, text="Polígonos")
        tab_mor = ttk.Frame(nb); nb.add(tab_mor, text="Morfología")

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
        ttk.Button(frm_btn, text="Recalibrar ahora", command=self.force_recalib).pack(side="left", padx=6)
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
        else:  # 'url'
            source = self.url_str.get()
            if not source.lower().startswith(("http://", "https://", "rtsp://")):
                messagebox.showwarning("URL", "Ingresa una URL válida (http/https/rtsp).")
                return

        self.worker_thread = threading.Thread(
            target=run_pipeline,
            args=(source, src_type, geo_bounds, epsg_src,
                  self.show_mode.get(), scale, video_speed, recalib_every,
                  self.live_params, self.pause_event, self.recalib_event, self.stop_event),
            daemon=True
        )
        self.worker_thread.start()
        messagebox.showinfo("Ejecutando",
                            "Detección iniciada.\n"
                            "Usa Pausar/Reanudar para controlar la captura.\n"
                            "‘Recalibrar ahora’ fuerza actualización de cruces y homografía.")

    def pause_capture(self):
        self.pause_event.set()

    def resume_capture(self):
        self.pause_event.clear()

    def force_recalib(self):
        self.recalib_event.set()

    def _on_close(self):
        # Señal para terminar el hilo y cerrar ventanas
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
