# -*- coding: utf-8 -*-
"""
Detector Geopolis 2026 – v2.1 (CORREGIDO: Homografía)

Cambios v2.1:
- Corrección en el orden de las esquinas (TR y BL estaban invertidos).
- Cruces de calibración son AZULES.
- Solo detecta plastilina VERDE como puntos para el GeoJSON.
"""

import cv2
import numpy as np
import geopandas as gpd
from shapely.geometry import Point
from pyproj import Transformer
import os
import threading
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

# ================== CONFIG DEFAULT ==================

GUERRERO_COSTA_CHICA_UTM = (436770.3242, 1832196.0532, 506936.9275, 1892877.8394)
GUERRERO_COSTA_CHICA_EPSG = 6369

CUENCA_VALLE_MEXICO_3D_UTM = (416316.969, 2079317.310, 617400.705, 2256323.915)
CUENCA_VALLE_MEXICO_3D_EPSG = 32614

CUENCA_VALLE_MEXICO_UTM = (409907, 2074280, 612270, 2184872)
CUENCA_VALLE_MEXICO_EPSG = 32614

DEFAULT_GEO_BOUNDS_UTM = CUENCA_VALLE_MEXICO_UTM
DEFAULT_EPSG_SRC = CUENCA_VALLE_MEXICO_EPSG

DEFAULT_VIDEO_FILE = "./Mesa3D/20250327_130515_referencia.mp4"
DEFAULT_VIDEO_SPEED_MS = 25
DEFAULT_SHOW_MODE = 1
DEFAULT_SCALE = 1.0
DEFAULT_RECALIB_EVERY = 0 

DEFAULT_PARAMS = {
    "MIN_AREA": 8,
    "MAX_AREA": 8000,
    "GREEN_H_LOW": 35,
    "GREEN_H_HIGH": 85,
    "GREEN_S_LOW": 50,
    "GREEN_S_HIGH": 255,
    "GREEN_V_LOW": 50,
    "GREEN_V_HIGH": 255,
    "BLUE_H_LOW": 85,
    "BLUE_H_HIGH": 135,
    "BLUE_S_LOW": 50,
    "BLUE_S_HIGH": 255,
    "BLUE_V_LOW": 80,
    "BLUE_V_HIGH": 255,
}

# ================== Núcleo de detección ==================

def init_transformer(epsg_src=32614, epsg_dst=4326):
    return Transformer.from_crs(epsg_src, epsg_dst, always_xy=True)

def find_blue_cross_corners(frame, params):
    """
    Detecta las 4 cruces AZULES y las ordena correctamente.
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
    for c in contours:
        area = cv2.contourArea(c)
        if area < 15: continue
        M = cv2.moments(c)
        if M["m00"] > 0:
            cx = M["m10"] / M["m00"]
            cy = M["m01"] / M["m00"]
            centroids.append((cx, cy))

    if len(centroids) < 4:
        return None, None, centroids, {"mask_blue": mask}

    pts = np.array(centroids, dtype=np.float32)
    
    # ORDENAMIENTO DE PUNTOS (CORREGIDO)
    # s = x + y -> El mínimo es TL, el máximo es BR
    s = pts.sum(axis=1)
    tl = pts[np.argmin(s)]
    br = pts[np.argmax(s)]

    # d = y - x -> 
    # TR: x grande, y pequeño -> y-x es el valor más negativo (MÍNIMO)
    # BL: x pequeño, y grande -> y-x es el valor más positivo (MÁXIMO)
    d = np.diff(pts, axis=1).ravel()
    tr = pts[np.argmin(d)] # <-- CORRECCIÓN: Antes era argmax
    bl = pts[np.argmax(d)] # <-- CORRECCIÓN: Antes era argmin
    
    corners = np.array([tl, tr, br, bl], dtype=np.float32)

    xs = corners[:, 0]
    ys = corners[:, 1]
    xmin_c, xmax_c = int(np.floor(xs.min())), int(np.ceil(xs.max()))
    ymin_c, ymax_c = int(np.floor(ys.min())), int(np.ceil(ys.max()))
    pad = 5
    xmin_c = max(0, xmin_c - pad)
    ymin_c = max(0, ymin_c - pad)
    xmax_c = min(frame.shape[1] - 1, xmax_c + pad)
    ymax_c = min(frame.shape[0] - 1, ymax_c + pad)

    return corners, (xmin_c, ymin_c, xmax_c, ymax_c), centroids, {"mask_blue": mask}

def compute_homography_from_corners(corners_img, geo_bounds_utm):
    xmin, ymin, xmax, ymax = geo_bounds_utm
    # Mapeo: TL->NW, TR->NE, BR->SE, BL->SW
    dst_pts = np.array([
        [xmin, ymax],
        [xmax, ymax],
        [xmax, ymin],
        [xmin, ymin],
    ], dtype=np.float32)
    H, _ = cv2.findHomography(corners_img, dst_pts, method=cv2.RANSAC)
    return H

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
    features = []
    for d in detections:
        if d['geometry'] is None: continue
        features.append({
            "type": "Feature",
            "properties": {"color": "green", "type": "point"},
            "geometry": d['geometry'].__geo_interface__
        })
    if features:
        try:
            gdf = gpd.GeoDataFrame.from_features(features, crs="EPSG:4326")
            gdf.to_file(nombre, driver='GeoJSON')
        except Exception as e:
            print(f"[WARN] No se pudo escribir {nombre}: {e}")

# ================== Pipeline y Clases de Control ==================

class LiveParams:
    def __init__(self, init_dict):
        self._lock = threading.Lock()
        self._d = dict(init_dict)
    def update(self, **kwargs):
        with self._lock: self._d.update(kwargs)
    def get(self):
        with self._lock: return dict(self._d)

def detect_green_points(frame_in, transformer, H, xoff, yoff, show_mode, scale, params):
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
        if area < params["MIN_AREA"] or area > params["MAX_AREA"]: continue
        M = cv2.moments(contour)
        if M["m00"] == 0: continue
        cx, cy = int(M["m10"] / M["m00"]), int(M["m01"] / M["m00"])
        geometry = None
        if H is not None:
            try:
                lonlat = raster_to_geo(cx + xoff, cy + yoff, H, transformer)
                if lonlat: geometry = Point(lonlat)
            except: pass
        count += 1
        detections.append({"geometry": geometry, "color": "green", "type": "point"})
        if show_mode >= 1:
            cv2.circle(frame_in, (cx, cy), 7, (0, 255, 0), 2)
            cv2.putText(frame_in, str(count), (cx + 10, cy - 5), cv2.FONT_HERSHEY_SIMPLEX, 0.4, (255, 255, 255), 1)
    if show_mode == 2:
        draw_small("Mascara Verde", cv2.cvtColor(mask, cv2.COLOR_GRAY2BGR), scale)
    return detections, count

def run_pipeline(video_source, src_type, geo_bounds_utm, epsg_src,
                 show_mode, scale, video_speed, recalib_every,
                 live_params, pause_event, recalib_event, stop_event):
    os.makedirs('geojson', exist_ok=True)
    transformer = init_transformer(epsg_src)
    cap = cv2.VideoCapture(int(video_source) if src_type == 'camera' else video_source)
    H, crop_box, frame_count = None, None, 0

    try:
        while not stop_event.is_set():
            ret, frame = cap.read()
            if not ret: break
            frame_count += 1
            params_now = live_params.get()
            corners, crop_candidate, all_centroids, dbg = find_blue_cross_corners(frame, params_now)
            
            if recalib_event.is_set() or (recalib_every > 0 and frame_count % recalib_every == 0):
                recalib_event.clear()
                if corners is not None:
                    H = compute_homography_from_corners(corners, geo_bounds_utm)
                    crop_box = crop_candidate

            if H is not None and crop_box is not None:
                xmin, ymin, xmax, ymax = crop_box
                frame_in = frame[ymin:ymax, xmin:xmax].copy()
                xoff, yoff, calibrated = xmin, ymin, True
            else:
                frame_in, xoff, yoff, calibrated = frame.copy(), 0, 0, False

            if pause_event.is_set():
                if cv2.waitKey(video_speed) & 0xFF == ord('q'): break
                continue

            detections, green_count = detect_green_points(frame_in, transformer, H if calibrated else None, xoff, yoff, show_mode, scale, params_now)

            if show_mode >= 1:
                display = frame_in.copy()
                for (cx, cy) in all_centroids:
                    ix, iy = int(cx) - xoff, int(cy) - yoff
                    if 0 <= ix < display.shape[1] and 0 <= iy < display.shape[0]:
                        cv2.drawMarker(display, (ix, iy), (255, 100, 0), cv2.MARKER_CROSS, 16, 2)
                cv2.putText(display, f"VERDES: {green_count} | CALIB: {calibrated}", (10, 25), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 0), 2)
                draw_small("GEOPOLIS 2026", display, scale)

            if calibrated and detections:
                guardar_geojson_puntos(detections, 'geojson/detecciones_puntos.geojson')

            if cv2.waitKey(video_speed) & 0xFF == ord('q'): break
    finally:
        cap.release()
        cv2.destroyAllWindows()

# ================== Interfaz Tkinter (Simplificada para el ejemplo) ==================

class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Geopolis 2026 — Corrección Homografía")
        self.geometry("800x600")
        
        # Variables de control
        self.src_type = tk.StringVar(value="camera")
        self.camera_index = tk.StringVar(value="0")
        self.epsg_src = tk.StringVar(value=str(DEFAULT_EPSG_SRC))
        self.xmin = tk.StringVar(value=str(DEFAULT_GEO_BOUNDS_UTM[0]))
        self.ymin = tk.StringVar(value=str(DEFAULT_GEO_BOUNDS_UTM[1]))
        self.xmax = tk.StringVar(value=str(DEFAULT_GEO_BOUNDS_UTM[2]))
        self.ymax = tk.StringVar(value=str(DEFAULT_GEO_BOUNDS_UTM[3]))
        
        self.live_params = LiveParams(DEFAULT_PARAMS)
        self.pause_event = threading.Event()
        self.recalib_event = threading.Event()
        self.stop_event = threading.Event()
        self.worker_thread = None

        self._build_ui()

    def _build_ui(self):
        ttk.Label(self, text="Sistema de Detección Geopolis 2026", font=("Arial", 14, "bold")).pack(pady=10)
        
        f_coords = ttk.LabelFrame(self, text="Coordenadas UTM (Límites de las cruces)")
        f_coords.pack(padx=10, pady=5, fill="x")
        ttk.Label(f_coords, text="xmin:").grid(row=0, column=0); ttk.Entry(f_coords, textvariable=self.xmin).grid(row=0, column=1)
        ttk.Label(f_coords, text="ymin:").grid(row=0, column=2); ttk.Entry(f_coords, textvariable=self.ymin).grid(row=0, column=3)
        ttk.Label(f_coords, text="xmax:").grid(row=1, column=0); ttk.Entry(f_coords, textvariable=self.xmax).grid(row=1, column=1)
        ttk.Label(f_coords, text="ymax:").grid(row=1, column=2); ttk.Entry(f_coords, textvariable=self.ymax).grid(row=1, column=3)

        btn_f = ttk.Frame(self)
        btn_f.pack(pady=20)
        ttk.Button(btn_f, text="▶ Iniciar", command=self.start).pack(side="left", padx=5)
        ttk.Button(btn_f, text="🔵 Recalibrar", command=lambda: self.recalib_event.set()).pack(side="left", padx=5)
        ttk.Button(btn_f, text="Salir", command=self.quit).pack(side="left", padx=5)

    def start(self):
        geo_bounds = (float(self.xmin.get()), float(self.ymin.get()), float(self.xmax.get()), float(self.ymax.get()))
        self.worker_thread = threading.Thread(
            target=run_pipeline,
            args=(0, 'camera', geo_bounds, int(self.epsg_src.get()), 1, 1.0, 25, 0,
                  self.live_params, self.pause_event, self.recalib_event, self.stop_event),
            daemon=True
        )
        self.worker_thread.start()

if __name__ == "__main__":
    App().mainloop()