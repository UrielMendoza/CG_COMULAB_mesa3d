# -*- coding: utf-8 -*-
"""
Detector Geopolis 2026 – v1 (imagen impresa satelital + plastilina verde)

Adaptación simplificada del detector Mesa 3D para la dinámica Geopolis 2026:
- Solo detecta cruces AZULES (azul claro) para georreferenciación
- Solo detecta plastilina VERDE como puntos
- Sin detección de amarillo, líneas ni polígonos
- Diseñado para funcionar sobre imágenes satelitales impresas (true color / falso color)
  donde hay muchos colores que no queremos detectar

Flujo:
1. Calibrar con 4 cruces azul claro en las esquinas de la imagen impresa
2. Detectar bolas de plastilina verde que los participantes colocan
3. Guardar las detecciones como puntos GeoJSON (solo verde)
4. El mapa web (mapa_dinamico.html) lee estos GeoJSON para la dinámica
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

# Coordenadas Guerrero Costa Chica (EPSG:6369)
GUERRERO_COSTA_CHICA_UTM = (436770.3242, 1832196.0532, 506936.9275, 1892877.8394)
GUERRERO_COSTA_CHICA_EPSG = 6369

# Coordenadas Cuenca del Valle de México — Mesa 3D original (EPSG:32614)
CUENCA_VALLE_MEXICO_3D_UTM = (416316.969, 2079317.310, 617400.705, 2256323.915)
CUENCA_VALLE_MEXICO_3D_EPSG = 32614

# Coordenadas Cuenca del Valle de México — Encuadre Sentinel-2 Geopolis 2026 (EPSG:32614)
# Nevado de Toluca → Valle de México → La Malinche — ~202×111 km
CUENCA_VALLE_MEXICO_UTM = (409907, 2074280, 612270, 2184872)
CUENCA_VALLE_MEXICO_EPSG = 32614

# Default para Geopolis 2026
DEFAULT_GEO_BOUNDS_UTM = CUENCA_VALLE_MEXICO_UTM
DEFAULT_EPSG_SRC = CUENCA_VALLE_MEXICO_EPSG

DEFAULT_VIDEO_FILE = "./Mesa3D/20250327_130515_referencia.mp4"
DEFAULT_VIDEO_SPEED_MS = 25
DEFAULT_SHOW_MODE = 1
DEFAULT_SCALE = 1.0
DEFAULT_RECALIB_EVERY = 0  # manual

# Parámetros de detección (solo puntos verdes)
DEFAULT_PARAMS = {
    "MIN_AREA": 8,        # área mínima en px para considerar detección
    "MAX_AREA": 8000,     # área máxima (filtrar blobs enormes / falsos positivos)
    # Rango HSV para plastilina verde — ajustable desde la UI
    "GREEN_H_LOW": 35,
    "GREEN_H_HIGH": 85,
    "GREEN_S_LOW": 50,
    "GREEN_S_HIGH": 255,
    "GREEN_V_LOW": 50,
    "GREEN_V_HIGH": 255,
    # Rango HSV para cruces azul claro — ajustable desde la UI
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
    Detecta las 4 cruces AZUL CLARO en las esquinas.
    Rango HSV parametrizado desde la UI.
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
        if area < 15:
            continue
        x, y, w, h = cv2.boundingRect(c)
        ar = w / h if h > 0 else 0
        if ar < 0.2 or ar > 5.0:
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
    xmin_c, xmax_c = int(np.floor(xs.min())), int(np.ceil(xs.max()))
    ymin_c, ymax_c = int(np.floor(ys.min())), int(np.ceil(ys.max()))
    pad = 5
    xmin_c = max(0, xmin_c - pad)
    ymin_c = max(0, ymin_c - pad)
    xmax_c = min(frame.shape[1] - 1, xmax_c + pad)
    ymax_c = min(frame.shape[0] - 1, ymax_c + pad)

    return corners, (xmin_c, ymin_c, xmax_c, ymax_c), {"mask_blue": mask}

def compute_homography_from_corners(corners_img, geo_bounds_utm):
    xmin, ymin, xmax, ymax = geo_bounds_utm
    dst_pts = np.array([
        [xmin, ymax],  # TL -> NW
        [xmax, ymax],  # TR -> NE
        [xmax, ymin],  # BR -> SE
        [xmin, ymin],  # BL -> SW
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
    """Guarda solo puntos verdes como GeoJSON."""
    features = []
    for d in detections:
        if d['geometry'] is None:
            continue
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
    """
    Detecta solo plastilina VERDE como puntos.
    Ignora todo lo demás (amarillo, rojo, etc. de la imagen impresa).
    """
    detections = []

    blurred = cv2.GaussianBlur(frame_in, (5, 5), 0)
    hsv = cv2.cvtColor(blurred, cv2.COLOR_BGR2HSV)

    # Rango verde parametrizado
    lower = np.array([params["GREEN_H_LOW"], params["GREEN_S_LOW"], params["GREEN_V_LOW"]], dtype=np.uint8)
    upper = np.array([params["GREEN_H_HIGH"], params["GREEN_S_HIGH"], params["GREEN_V_HIGH"]], dtype=np.uint8)
    mask = cv2.inRange(hsv, lower, upper)

    # Limpieza morfológica
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

        # Georreferenciar si hay homografía
        geometry = None
        if H is not None:
            try:
                lonlat = raster_to_geo(cx + xoff, cy + yoff, H, transformer)
                if lonlat:
                    geometry = Point(lonlat)
            except Exception:
                pass

        count += 1
        detections.append({"geometry": geometry, "color": "green", "type": "point"})

        # Dibujo en pantalla
        if show_mode >= 1:
            cv2.circle(frame_in, (cx, cy), 7, (0, 255, 0), 2)
            cv2.circle(frame_in, (cx, cy), 2, (0, 255, 0), -1)
            # Etiqueta con número
            cv2.putText(frame_in, str(count), (cx + 10, cy - 5),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.4, (255, 255, 255), 1, cv2.LINE_AA)

    if show_mode >= 1:
        display = frame_in.copy()
        cv2.putText(display, f"PUNTOS VERDES: {count}", (10, 22),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 0), 1, cv2.LINE_AA)
        draw_small("GEOPOLIS 2026 - Deteccion", display, scale)

    if show_mode == 2:
        draw_small("Mascara Verde", cv2.cvtColor(mask, cv2.COLOR_GRAY2BGR), scale)

    return detections

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

def run_pipeline(video_source, src_type, geo_bounds_utm, epsg_src,
                 show_mode, scale, video_speed, recalib_every,
                 live_params: LiveParams,
                 pause_event: threading.Event,
                 recalib_event: threading.Event,
                 stop_event: threading.Event):

    os.makedirs('geojson', exist_ok=True)
    transformer = init_transformer(epsg_src)

    if src_type == 'camera':
        cap = cv2.VideoCapture(int(video_source))
    else:
        cap = cv2.VideoCapture(video_source)

    if not cap.isOpened():
        messagebox.showerror("Error", f"No se pudo abrir: {video_source}")
        return

    H = None
    crop_box = None
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

            # Recalibración
            need_recalib = False
            if recalib_every > 0 and (frame_count % recalib_every == 0):
                need_recalib = True
            if recalib_event.is_set():
                need_recalib = True
                recalib_event.clear()

            if need_recalib:
                corners, crop2, dbg = find_blue_cross_corners(frame, params_now)
                if corners is not None and crop2 is not None:
                    H2 = compute_homography_from_corners(corners, geo_bounds_utm)
                    if H2 is not None:
                        H = H2
                        crop_box = crop2
                        if show_mode == 2:
                            dbg_frame = frame.copy()
                            for (x, y) in corners.astype(int):
                                cv2.drawMarker(dbg_frame, (x, y), (255, 100, 0),
                                               markerType=cv2.MARKER_CROSS, markerSize=20, thickness=2)
                            draw_small("Cruces Azules Detectadas", dbg_frame, scale)
                            if "mask_blue" in dbg:
                                draw_small("Mascara Azul", cv2.cvtColor(dbg["mask_blue"], cv2.COLOR_GRAY2BGR), scale)
                            cv2.waitKey(200)
                else:
                    if show_mode >= 1:
                        print("[INFO] No se detectaron 4 cruces azules.")

            # Frame de entrada
            if H is not None and crop_box is not None:
                xmin, ymin, xmax, ymax = crop_box
                frame_in = frame[ymin:ymax, xmin:xmax].copy()
                xoff, yoff = xmin, ymin
                calibrated = True
            else:
                frame_in = frame.copy()
                xoff, yoff = 0, 0
                calibrated = False

            # Pausa
            if pause_event.is_set():
                if show_mode >= 1:
                    overlay = frame_in.copy()
                    cv2.putText(overlay, "PAUSADO", (20, 40),
                                cv2.FONT_HERSHEY_SIMPLEX, 1.1, (0, 0, 255), 3, cv2.LINE_AA)
                    draw_small("GEOPOLIS 2026 - Deteccion", overlay, scale)
                if cv2.waitKey(video_speed) & 0xFF == ord('q'):
                    break
                continue

            # Detectar puntos verdes
            detections = detect_green_points(
                frame_in, transformer, H if calibrated else None,
                xoff, yoff, show_mode, scale, params_now
            )

            # Overlay sin calibración
            if show_mode >= 1 and not calibrated:
                overlay = frame_in.copy()
                cv2.putText(overlay, "SIN CALIBRACION", (20, 40),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 140, 255), 2, cv2.LINE_AA)
                cv2.putText(overlay, "Presiona 'Recalibrar' cuando las cruces azules sean visibles", (20, 65),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.4, (0, 140, 255), 1, cv2.LINE_AA)
                draw_small("GEOPOLIS 2026 - Deteccion", overlay, scale)

            # Guardar GeoJSON (solo puntos)
            if calibrated and detections:
                guardar_geojson_puntos(detections, 'geojson/detecciones_puntos.geojson')
                # También vaciar los otros archivos para que el mapa no muestre datos viejos
                for f in ['geojson/detecciones_lineas.geojson', 'geojson/detecciones_poligonos.geojson']:
                    try:
                        with open(f, 'w') as fh:
                            fh.write('{"type":"FeatureCollection","features":[]}')
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
        self.title("Geopolis 2026 — Detector Plastilina Verde (imagen impresa)")
        self.geometry("820x720")
        self.resizable(False, False)

        self.src_type = tk.StringVar(value="camera")
        self.video_path = tk.StringVar(value=DEFAULT_VIDEO_FILE)
        self.url_str = tk.StringVar(value="http://127.0.0.1:4747/video")
        self.camera_index = tk.StringVar(value="0")

        xmin, ymin, xmax, ymax = DEFAULT_GEO_BOUNDS_UTM
        self.epsg_src = tk.StringVar(value=str(DEFAULT_EPSG_SRC))
        self.xmin = tk.StringVar(value=str(xmin))
        self.ymin = tk.StringVar(value=str(ymin))
        self.xmax = tk.StringVar(value=str(xmax))
        self.ymax = tk.StringVar(value=str(ymax))
        self.coord_preset = tk.StringVar(value="cuenca_valle_mexico")

        self.show_mode = tk.IntVar(value=DEFAULT_SHOW_MODE)
        self.scale = tk.DoubleVar(value=DEFAULT_SCALE)
        self.video_speed = tk.IntVar(value=DEFAULT_VIDEO_SPEED_MS)
        self.recalib_every = tk.IntVar(value=DEFAULT_RECALIB_EVERY)

        self.pause_event = threading.Event()
        self.recalib_event = threading.Event()
        self.stop_event = threading.Event()
        self.worker_thread = None
        self.live_params = LiveParams(DEFAULT_PARAMS)

        self._build()
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    def _hsv_scale(self, parent, text, from_, to, init):
        """Crea un slider HSV compacto."""
        frm = ttk.Frame(parent)
        lbl = ttk.Label(frm, text=text, width=10)
        lbl.pack(side="left")
        val_lbl = ttk.Label(frm, text=str(init), width=4)
        val_lbl.pack(side="right")
        sv = tk.IntVar(value=init)
        scl = ttk.Scale(frm, from_=from_, to=to, orient="horizontal", variable=sv, length=200)
        scl.pack(fill="x", padx=4)
        def on_move(_=None):
            val_lbl.config(text=str(int(sv.get())))
        scl.configure(command=on_move)
        return frm, sv

    def _apply_preset(self, *args):
        preset = self.coord_preset.get()
        presets = {
            "guerrero_costa_chica": (GUERRERO_COSTA_CHICA_UTM, GUERRERO_COSTA_CHICA_EPSG),
            "cuenca_valle_mexico": (CUENCA_VALLE_MEXICO_UTM, CUENCA_VALLE_MEXICO_EPSG),
            "cuenca_valle_mexico_3d": (CUENCA_VALLE_MEXICO_3D_UTM, CUENCA_VALLE_MEXICO_3D_EPSG),
        }
        if preset not in presets:
            return
        (xmin, ymin, xmax, ymax), epsg = presets[preset]
        self.xmin.set(str(xmin)); self.ymin.set(str(ymin))
        self.xmax.set(str(xmax)); self.ymax.set(str(ymax))
        self.epsg_src.set(str(epsg))
        self._update_preset_desc()

    def _update_preset_desc(self):
        descs = {
            "guerrero_costa_chica": "EPSG:6369 — Guerrero Costa Chica",
            "cuenca_valle_mexico": "EPSG:32614 — Sentinel-2 Geopolis (Nevado→Malinche)",
            "cuenca_valle_mexico_3d": "EPSG:32614 — Mesa 3D original",
        }
        self.preset_desc.config(text=descs.get(self.coord_preset.get(), ""))

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
        pad = {'padx': 8, 'pady': 3}

        # === Título ===
        title_frm = ttk.Frame(self)
        title_frm.pack(fill="x", padx=8, pady=(8, 2))
        ttk.Label(title_frm, text="🎯 Geopolis 2026 — Detector de Plastilina Verde",
                  font=("Segoe UI", 13, "bold")).pack(anchor="w")
        ttk.Label(title_frm, text="Solo detecta puntos verdes sobre imagen impresa satelital. Cruces azul claro para georreferenciación.",
                  foreground="gray").pack(anchor="w")

        # === Fuente de video ===
        frm_src = ttk.LabelFrame(self, text="Fuente de video")
        frm_src.pack(fill="x", **pad)
        ttk.Radiobutton(frm_src, text="Archivo", value="file", variable=self.src_type).grid(row=0, column=0, sticky="w", padx=6)
        ttk.Radiobutton(frm_src, text="URL", value="url", variable=self.src_type).grid(row=0, column=1, sticky="w", padx=6)
        ttk.Radiobutton(frm_src, text="Cámara USB", value="camera", variable=self.src_type).grid(row=0, column=2, sticky="w", padx=6)
        ttk.Label(frm_src, text="Archivo:").grid(row=1, column=0, sticky="e")
        ttk.Entry(frm_src, textvariable=self.video_path, width=45).grid(row=1, column=1, columnspan=2, sticky="we")
        ttk.Button(frm_src, text="Buscar…", command=self.pick_file).grid(row=1, column=3, padx=4)
        ttk.Label(frm_src, text="URL:").grid(row=2, column=0, sticky="e")
        ttk.Entry(frm_src, textvariable=self.url_str, width=45).grid(row=2, column=1, columnspan=2, sticky="we")
        ttk.Label(frm_src, text="Cámara:").grid(row=3, column=0, sticky="e")
        self.camera_combo = ttk.Combobox(frm_src, textvariable=self.camera_index,
                                         values=["0","1","2","3","4"], width=6, state="readonly")
        self.camera_combo.grid(row=3, column=1, sticky="w", padx=4)
        self.btn_detect_cam = ttk.Button(frm_src, text="🔍 Detectar", command=self._detect_cameras)
        self.btn_detect_cam.grid(row=3, column=2, sticky="w")
        for i in range(4): frm_src.grid_columnconfigure(i, weight=1)

        # === Coordenadas ===
        frm_geo = ttk.LabelFrame(self, text="Georreferenciación UTM")
        frm_geo.pack(fill="x", **pad)
        ttk.Label(frm_geo, text="Preset:").grid(row=0, column=0, sticky="e")
        pc = ttk.Combobox(frm_geo, textvariable=self.coord_preset,
                          values=["cuenca_valle_mexico", "cuenca_valle_mexico_3d", "guerrero_costa_chica"],
                          state="readonly", width=25)
        pc.grid(row=0, column=1, sticky="w", padx=4)
        pc.bind("<<ComboboxSelected>>", self._apply_preset)
        self.preset_desc = ttk.Label(frm_geo, text="", foreground="gray")
        self.preset_desc.grid(row=0, column=2, columnspan=2, sticky="w")
        self._update_preset_desc()

        ttk.Label(frm_geo, text="EPSG:").grid(row=1, column=0, sticky="e")
        ttk.Entry(frm_geo, textvariable=self.epsg_src, width=8).grid(row=1, column=1, sticky="w")
        ttk.Label(frm_geo, text="xmin:").grid(row=2, column=0, sticky="e"); ttk.Entry(frm_geo, textvariable=self.xmin, width=14).grid(row=2, column=1, sticky="w")
        ttk.Label(frm_geo, text="ymin:").grid(row=2, column=2, sticky="e"); ttk.Entry(frm_geo, textvariable=self.ymin, width=14).grid(row=2, column=3, sticky="w")
        ttk.Label(frm_geo, text="xmax:").grid(row=3, column=0, sticky="e"); ttk.Entry(frm_geo, textvariable=self.xmax, width=14).grid(row=3, column=1, sticky="w")
        ttk.Label(frm_geo, text="ymax:").grid(row=3, column=2, sticky="e"); ttk.Entry(frm_geo, textvariable=self.ymax, width=14).grid(row=3, column=3, sticky="w")
        for i in range(4): frm_geo.grid_columnconfigure(i, weight=1)

        # === Visualización ===
        frm_opt = ttk.LabelFrame(self, text="Visualización")
        frm_opt.pack(fill="x", **pad)
        ttk.Radiobutton(frm_opt, text="Ninguna", value=0, variable=self.show_mode).grid(row=0, column=0, sticky="w", padx=6)
        ttk.Radiobutton(frm_opt, text="Detección", value=1, variable=self.show_mode).grid(row=0, column=1, sticky="w", padx=6)
        ttk.Radiobutton(frm_opt, text="Todo + máscaras", value=2, variable=self.show_mode).grid(row=0, column=2, sticky="w", padx=6)
        ttk.Label(frm_opt, text="Escala:").grid(row=1, column=0, sticky="e")
        ttk.Entry(frm_opt, textvariable=self.scale, width=6).grid(row=1, column=1, sticky="w")
        ttk.Label(frm_opt, text="Vel (ms):").grid(row=1, column=2, sticky="e")
        ttk.Entry(frm_opt, textvariable=self.video_speed, width=6).grid(row=1, column=3, sticky="w")
        ttk.Label(frm_opt, text="Recalib cada N frames (0=manual):").grid(row=2, column=0, columnspan=2, sticky="e")
        ttk.Entry(frm_opt, textvariable=self.recalib_every, width=6).grid(row=2, column=2, sticky="w")

        # === Pestañas de ajuste HSV ===
        nb = ttk.Notebook(self)
        nb.pack(fill="x", **pad)

        tab_green = ttk.Frame(nb); nb.add(tab_green, text="🟢 Verde (plastilina)")
        tab_blue  = ttk.Frame(nb); nb.add(tab_blue,  text="🔵 Azul (cruces)")
        tab_area  = ttk.Frame(nb); nb.add(tab_area,  text="📐 Área")

        # --- Verde ---
        f, sv_gh_lo = self._hsv_scale(tab_green, "H bajo:", 0, 179, DEFAULT_PARAMS["GREEN_H_LOW"]); f.pack(fill="x", pady=2, padx=6)
        f, sv_gh_hi = self._hsv_scale(tab_green, "H alto:", 0, 179, DEFAULT_PARAMS["GREEN_H_HIGH"]); f.pack(fill="x", pady=2, padx=6)
        f, sv_gs_lo = self._hsv_scale(tab_green, "S bajo:", 0, 255, DEFAULT_PARAMS["GREEN_S_LOW"]); f.pack(fill="x", pady=2, padx=6)
        f, sv_gs_hi = self._hsv_scale(tab_green, "S alto:", 0, 255, DEFAULT_PARAMS["GREEN_S_HIGH"]); f.pack(fill="x", pady=2, padx=6)
        f, sv_gv_lo = self._hsv_scale(tab_green, "V bajo:", 0, 255, DEFAULT_PARAMS["GREEN_V_LOW"]); f.pack(fill="x", pady=2, padx=6)
        f, sv_gv_hi = self._hsv_scale(tab_green, "V alto:", 0, 255, DEFAULT_PARAMS["GREEN_V_HIGH"]); f.pack(fill="x", pady=2, padx=6)

        ttk.Label(tab_green, text="Tip: Si la imagen impresa tiene verdes, sube S bajo para filtrar solo plastilina saturada.",
                  foreground="gray", wraplength=500).pack(anchor="w", padx=6, pady=4)

        # --- Azul ---
        f, sv_bh_lo = self._hsv_scale(tab_blue, "H bajo:", 0, 179, DEFAULT_PARAMS["BLUE_H_LOW"]); f.pack(fill="x", pady=2, padx=6)
        f, sv_bh_hi = self._hsv_scale(tab_blue, "H alto:", 0, 179, DEFAULT_PARAMS["BLUE_H_HIGH"]); f.pack(fill="x", pady=2, padx=6)
        f, sv_bs_lo = self._hsv_scale(tab_blue, "S bajo:", 0, 255, DEFAULT_PARAMS["BLUE_S_LOW"]); f.pack(fill="x", pady=2, padx=6)
        f, sv_bs_hi = self._hsv_scale(tab_blue, "S alto:", 0, 255, DEFAULT_PARAMS["BLUE_S_HIGH"]); f.pack(fill="x", pady=2, padx=6)
        f, sv_bv_lo = self._hsv_scale(tab_blue, "V bajo:", 0, 255, DEFAULT_PARAMS["BLUE_V_LOW"]); f.pack(fill="x", pady=2, padx=6)
        f, sv_bv_hi = self._hsv_scale(tab_blue, "V alto:", 0, 255, DEFAULT_PARAMS["BLUE_V_HIGH"]); f.pack(fill="x", pady=2, padx=6)

        ttk.Label(tab_blue, text="Tip: Para azul claro, baja H bajo (~85) y sube V bajo. Usa modo 'Todo + máscaras' para ver la máscara azul.",
                  foreground="gray", wraplength=500).pack(anchor="w", padx=6, pady=4)

        # --- Área ---
        f, sv_min_area = self._hsv_scale(tab_area, "Mín (px²):", 1, 500, DEFAULT_PARAMS["MIN_AREA"]); f.pack(fill="x", pady=4, padx=6)
        f, sv_max_area = self._hsv_scale(tab_area, "Máx (px²):", 100, 20000, DEFAULT_PARAMS["MAX_AREA"]); f.pack(fill="x", pady=4, padx=6)

        ttk.Label(tab_area, text="Ajusta el área mínima para ignorar ruido y máxima para ignorar blobs grandes de la imagen.",
                  foreground="gray", wraplength=500).pack(anchor="w", padx=6, pady=4)

        # Sync sliders -> live_params
        all_svs = [sv_gh_lo, sv_gh_hi, sv_gs_lo, sv_gs_hi, sv_gv_lo, sv_gv_hi,
                   sv_bh_lo, sv_bh_hi, sv_bs_lo, sv_bs_hi, sv_bv_lo, sv_bv_hi,
                   sv_min_area, sv_max_area]

        def sync(*_):
            self.live_params.update(
                GREEN_H_LOW=int(sv_gh_lo.get()), GREEN_H_HIGH=int(sv_gh_hi.get()),
                GREEN_S_LOW=int(sv_gs_lo.get()), GREEN_S_HIGH=int(sv_gs_hi.get()),
                GREEN_V_LOW=int(sv_gv_lo.get()), GREEN_V_HIGH=int(sv_gv_hi.get()),
                BLUE_H_LOW=int(sv_bh_lo.get()), BLUE_H_HIGH=int(sv_bh_hi.get()),
                BLUE_S_LOW=int(sv_bs_lo.get()), BLUE_S_HIGH=int(sv_bs_hi.get()),
                BLUE_V_LOW=int(sv_bv_lo.get()), BLUE_V_HIGH=int(sv_bv_hi.get()),
                MIN_AREA=int(sv_min_area.get()), MAX_AREA=int(sv_max_area.get()),
            )
        for sv in all_svs:
            sv.trace_add('write', sync)
        sync()

        # === Botones ===
        frm_btn = ttk.Frame(self); frm_btn.pack(fill="x", padx=8, pady=6)
        ttk.Button(frm_btn, text="▶ Iniciar", command=self.start_detection).pack(side="left", padx=4)
        ttk.Button(frm_btn, text="⏸ Pausar", command=self.pause_capture).pack(side="left", padx=4)
        ttk.Button(frm_btn, text="▶ Reanudar", command=self.resume_capture).pack(side="left", padx=4)

        recalib_btn = tk.Button(frm_btn, text="🔵 Recalibrar ahora", command=self.force_recalib,
                                bg="#2196F3", fg="white", font=("Segoe UI", 10, "bold"),
                                relief="flat", padx=10, pady=4)
        recalib_btn.pack(side="left", padx=8)

        ttk.Button(frm_btn, text="Salir", command=self._on_close).pack(side="right", padx=4)

    def pick_file(self):
        path = filedialog.askopenfilename(
            title="Seleccionar video",
            filetypes=[("Videos", "*.mp4;*.avi;*.mov;*.mkv"), ("Todos", "*.*")]
        )
        if path:
            self.video_path.set(path)

    def start_detection(self):
        if self.worker_thread and self.worker_thread.is_alive():
            messagebox.showinfo("Info", "Ya está corriendo.")
            return
        self.pause_event.clear()
        self.recalib_event.clear()
        self.stop_event.clear()

        try:
            epsg_src = int(self.epsg_src.get())
            geo_bounds = (float(self.xmin.get()), float(self.ymin.get()),
                          float(self.xmax.get()), float(self.ymax.get()))
            video_speed = int(self.video_speed.get())
            scale = float(self.scale.get()); assert 0.1 <= scale <= 2.0
            recalib_every = max(0, int(self.recalib_every.get()))
        except Exception as e:
            messagebox.showerror("Error", f"Parámetros inválidos: {e}")
            return

        src_type = self.src_type.get()
        if src_type == 'file':
            source = self.video_path.get()
            if not source: messagebox.showwarning("Falta", "Selecciona un archivo."); return
        elif src_type == 'url':
            source = self.url_str.get()
            if not source.lower().startswith(("http://","https://","rtsp://")): messagebox.showwarning("URL","URL inválida."); return
        else:
            try: source = int(self.camera_index.get())
            except: messagebox.showwarning("Cámara","Índice inválido."); return

        self.worker_thread = threading.Thread(
            target=run_pipeline,
            args=(source, src_type, geo_bounds, epsg_src,
                  self.show_mode.get(), scale, video_speed, recalib_every,
                  self.live_params, self.pause_event, self.recalib_event, self.stop_event),
            daemon=True
        )
        self.worker_thread.start()

        src_info = f"Cámara {source}" if src_type == 'camera' else source
        messagebox.showinfo("Geopolis 2026",
            f"Detector iniciado.\n\n"
            f"Fuente: {src_info}\n"
            f"Preset: {self.coord_preset.get()}\n"
            f"EPSG: {epsg_src}\n\n"
            "• Solo detecta plastilina VERDE (puntos)\n"
            "• Cruces AZUL CLARO para calibración\n"
            "• Presiona 'Recalibrar' cuando las cruces sean visibles\n"
            "• Ajusta los rangos HSV en las pestañas si hay falsos positivos")

    def pause_capture(self): self.pause_event.set()
    def resume_capture(self): self.pause_event.clear()
    def force_recalib(self): self.recalib_event.set()

    def _on_close(self):
        self.stop_event.set()
        try:
            if self.worker_thread: self.worker_thread.join(timeout=1.5)
        except: pass
        self.destroy()

if __name__ == "__main__":
    app = App()
    app.mainloop()