# -*- coding: utf-8 -*-
# ==============================
# Detección de cruces verdes + Homografía (calibración) y lectura del video referencia
# ==============================

import cv2
import numpy as np
import geopandas as gpd
from shapely.geometry import Point, LineString, Polygon
from pyproj import Transformer
import os

# ------------------------------
# Configuración de coordenadas extremas (UTM EPSG:32614) del modelo físico
# Ajusta estos valores si los extremos reales cambian
# xmin, ymin, xmax, ymax (en UTM)
GEO_BOUNDS_UTM = (416316.969, 2079317.310, 617400.705, 2256323.915)

# Video de referencia con cruces verdes en los extremos
VIDEO_PATH = "./Mesa3D/20250327_130515_referencia.mp4"
# ------------------------------

def init_transformer():
    # Transformación UTM 32614 -> WGS84
    return Transformer.from_crs(32614, 4326, always_xy=True)

def order_corners(pts):
    """
    Ordena 4 puntos (x,y) en el orden: TL, TR, BR, BL
    usando suma y diferencia.
    """
    pts = np.array(pts, dtype=np.float32)
    s = pts.sum(axis=1)
    d = np.diff(pts, axis=1).ravel()

    tl = pts[np.argmin(s)]
    br = pts[np.argmax(s)]
    tr = pts[np.argmax(d)]
    bl = pts[np.argmin(d)]
    return np.array([tl, tr, br, bl], dtype=np.float32)

def find_green_cross_corners(frame):
    """
    Detecta cruces verdes en un frame.
    Retorna:
      corners_img (4x2): puntos (x,y) en imagen ordenados TL,TR,BR,BL
      crop_box: (xmin, ymin, xmax, ymax) para recortar el área útil
      dbg (dict): máscaras para depuración
    """
    blurred = cv2.GaussianBlur(frame, (3,3), 0)
    hsv = cv2.cvtColor(blurred, cv2.COLOR_BGR2HSV)

    # Rango de verde (ajustable según iluminación)
    lower = np.array([35, 45, 40], dtype=np.uint8)
    upper = np.array([80, 255, 255], dtype=np.uint8)

    mask = cv2.inRange(hsv, lower, upper)
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3,3))
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel)

    # Contornos de regiones verdes (las cruces deberían ser componentes distintas)
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    centroids = []
    for c in contours:
        area = cv2.contourArea(c)
        if area < 20:
            continue
        x, y, w, h = cv2.boundingRect(c)
        # cruces suelen tener aspecto relativamente cuadrado; filtra outliers muy alargados
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

    # Si hay más de 4, elegimos los 4 más extremos por convexidad/posición
    # En la práctica, ordenar por s=x+y y d=x-y funciona bien para esquinas
    pts = np.array(centroids, dtype=np.float32)
    s = pts.sum(axis=1)
    d = np.diff(pts, axis=1).ravel()

    tl = pts[np.argmin(s)]
    br = pts[np.argmax(s)]
    tr = pts[np.argmax(d)]
    bl = pts[np.argmin(d)]

    corners = np.array([tl, tr, br, bl], dtype=np.float32)

    # Caja de recorte a partir de las esquinas detectadas
    xs = corners[:,0]
    ys = corners[:,1]
    xmin, xmax = int(np.floor(xs.min())), int(np.ceil(xs.max()))
    ymin, ymax = int(np.floor(ys.min())), int(np.ceil(ys.max()))

    # Margen pequeño por seguridad
    pad = 5
    xmin = max(0, xmin - pad)
    ymin = max(0, ymin - pad)
    xmax = min(frame.shape[1]-1, xmax + pad)
    ymax = min(frame.shape[0]-1, ymax + pad)

    return corners, (xmin, ymin, xmax, ymax), {"mask_green": mask}

def compute_homography_from_corners(corners_img, geo_bounds_utm):
    """
    Calcula homografía H que mapea (x_img, y_img, 1) -> (X_utm, Y_utm, w).
    corners_img: puntos imagen ordenados [TL, TR, BR, BL]
    geo_bounds_utm: (xmin, ymin, xmax, ymax) en UTM
    """
    xmin, ymin, xmax, ymax = geo_bounds_utm

    # OJO: En imagen, "top" es y pequeño, que corresponde a Y UTM grande (ymax).
    # Para video vertical (portrait) con Norte/Sur invertidos (top = Norte)
    dst_pts = np.array([
        [xmin, ymax],  # esquina superior en el video -> Norte (ymax)
        [xmin, ymin],  # esquina inferior en el video -> Sur   (ymin)
        [xmax, ymin],  # inferior opuesta
        [xmax, ymax],  # superior opuesta
    ], dtype=np.float32)


    H, status = cv2.findHomography(corners_img, dst_pts, method=cv2.RANSAC)
    return H

def raster_to_geo_homography(cx, cy, H, transformer):
    """
    Convierte un punto (cx, cy) en imagen a lon/lat usando homografía (img->UTM) + transformación a WGS84.
    """
    pt = np.array([ [cx, cy] ], dtype=np.float32)
    pt_h = cv2.perspectiveTransform(np.array([pt]), H)[0][0]  # -> (Xutm, Yutm)
    xutm, yutm = float(pt_h[0]), float(pt_h[1])
    lon, lat = transformer.transform(xutm, yutm)
    return (lon, lat)

def refine_mask_for_lines(mask, k_long=7, k_short=3, iters=1):
    """
    Une trazos tipo línea aplicando cierres horizontales y verticales.
    Devuelve una máscara refinada que favorece componentes alargados.
    """
    # Kernels orientados
    k_h = cv2.getStructuringElement(cv2.MORPH_RECT, (k_long, 1))
    k_v = cv2.getStructuringElement(cv2.MORPH_RECT, (1, k_long))
    k_s = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k_short, k_short))

    # Limpieza suave
    m = cv2.morphologyEx(mask, cv2.MORPH_OPEN, k_s, iterations=1)

    # Cierres para unir segmentos lineales
    m_h = cv2.morphologyEx(m, cv2.MORPH_CLOSE, k_h, iterations=iters)
    m_v = cv2.morphologyEx(m, cv2.MORPH_CLOSE, k_v, iterations=iters)

    # Fusión
    m_fused = cv2.bitwise_or(m_h, m_v)
    return m_fused

def contour_line_metrics(cnt):
    """
    Métrica robusta de linealidad usando fitLine:
    - length: largo del contorno proyectado sobre la recta
    - width: dispersión ortogonal (ancho)
    - aspect: length/width
    """
    if len(cnt) < 2:
        return 0.0, 0.0, 0.0

    # Ajuste de línea
    [vx, vy, x0, y0] = cv2.fitLine(cnt, cv2.DIST_L2, 0, 0.01, 0.01)
    v = np.array([vx, vy], dtype=np.float32).reshape(2)
    p0 = np.array([x0, y0], dtype=np.float32).reshape(2)

    pts = cnt.reshape(-1, 2).astype(np.float32)
    # Proyección escalar de cada punto sobre la dirección v
    t = (pts - p0) @ v
    length = float(t.max() - t.min())

    # Distancia ortogonal promedio a la recta (ancho)
    # vector perpendicular a v
    vp = np.array([-v[1], v[0]], dtype=np.float32)
    w = np.abs(((pts - p0) @ vp)).mean() * 2.0  # ~doble del semi-ancho promedio

    width = float(max(w, 1e-6))
    aspect = float(length / width)
    return length, width, aspect


def show_small_window(window_name, frame, scale=0.5):
    h, w = frame.shape[:2]
    resized = cv2.resize(frame, (int(w * scale), int(h * scale)))
    cv2.imshow(window_name, resized)


def process_frame(frame, width, height, transformer, H, show_frames):
    """
    Igual que tu versión, pero usando homografía H para pasar a coordenadas geográficas.
    """
    detections = []
    yellow_points = yellow_lines = yellow_polygons = 0
    green_points = green_lines = green_polygons = 0
    mask_yellow = None
    mask_green = None

    # Preprocesamiento
    blurred = cv2.GaussianBlur(frame, (3,3), 0)
    hsv = cv2.cvtColor(blurred, cv2.COLOR_BGR2HSV)

    # Rangos de color (ajustables)
    color_ranges = {
        'yellow': (np.array([15, 105, 100]), np.array([30, 255, 255])),
        'green':  (np.array([35, 45,  40]), np.array([80, 255, 255]))
    }

    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3,3))
    
    # ----- parámetros de clasificación (ajústalos si hace falta) -----
    MIN_AREA_POINT     = 5
    MIN_AREA_POLY      = 150
    MIN_AREA_LINE      = 30
    MIN_LINE_LENGTH    = 20      # píxeles en el frame (tras recorte)
    MIN_LINE_ASPECT    = 4.0     # largo/ancho mínimo para línea
    # ---------------------------------------------------------------

    for color_name, (lower, upper) in color_ranges.items():
        base_mask = cv2.inRange(hsv, lower, upper)

        # 1) Máscara refinada para líneas
        mask_ref = refine_mask_for_lines(base_mask, k_long=9, k_short=3, iters=1)

        # 2) Encuentra contornos en la máscara refinada
        contours, _ = cv2.findContours(mask_ref, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

        # (opcional) si quieres depurar:
        if show_frames:
            # muestra más pequeño
            dbg = cv2.cvtColor(mask_ref, cv2.COLOR_GRAY2BGR)
            hdb, wdb = dbg.shape[:2]
            cv2.imshow(f"MaskRef-{color_name}", cv2.resize(dbg, (wdb//2, hdb//2)))

        for contour in contours:
            area = cv2.contourArea(contour)
            if area < MIN_AREA_POINT:
                continue

            # Aproximación geométrica
            epsilon = 0.01 * cv2.arcLength(contour, True)
            approx = cv2.approxPolyDP(contour, epsilon, True)

            # Métrica de linealidad
            line_length, line_width, line_aspect = contour_line_metrics(contour)

            # Rect y aspecto clásico (respaldo)
            rect = cv2.minAreaRect(contour)
            (x, y), (w2, h2), angle = rect
            rect_aspect = max(w2, h2) / max(min(w2, h2), 1e-6)

            # ----- Clasificación con prioridad a "ser línea" -----
            if area >= MIN_AREA_LINE and line_length >= MIN_LINE_LENGTH and line_aspect >= MIN_LINE_ASPECT:
                geom_type = 'line'
            elif area >= MIN_AREA_POLY and rect_aspect < 2.0:
                geom_type = 'polygon'
            else:
                geom_type = 'point'

            # Convertir a coordenadas georreferenciadas (usa tu homografía/offset)
            geo_points = []
            for p in approx:
                cx, cy = p[0]
                # OJO: si estás en process_frame_with_offset, recuerda sumar xoff/yoff
                lonlat = raster_to_geo_homography(cx + xoff, cy + yoff, H, transformer) \
                        if 'xoff' in locals() else raster_to_geo_homography(cx, cy, H, transformer)
                if lonlat:
                    geo_points.append(lonlat)

            if not geo_points:
                continue

            # Construcción de geometrías
            if geom_type == 'line':
                # Para líneas, puedes usar extremos del contorno proyectado sobre la recta
                # o simplemente la polilínea aproximada:
                geometry = LineString(geo_points)
                if color_name == 'yellow': yellow_lines += 1
                else:                       green_lines += 1
            elif geom_type == 'polygon' and len(geo_points) >= 3:
                if geo_points[0] != geo_points[-1]:
                    geo_points.append(geo_points[0])
                geometry = Polygon(geo_points)
                if color_name == 'yellow': yellow_polygons += 1
                else:                       green_polygons += 1
            else:
                geometry = Point(geo_points[0])
                if color_name == 'yellow': yellow_points += 1
                else:                       green_points += 1

            # Dibujo en el frame
            if show_frames:
                if geom_type == 'line':
                    color = (0, 255, 255) if color_name == 'yellow' else (0, 255, 0)
                    box_points = cv2.boxPoints(rect).astype(int)
                    cv2.drawContours(frame, [box_points], 0, color, 2)
                elif geom_type == 'polygon':
                    color = (0, 165, 255) if color_name == 'yellow' else (0, 100, 0)
                    cv2.drawContours(frame, [approx], -1, color, 2)
                else:
                    cv2.circle(frame, (int(x), int(y)), 5, (0, 0, 255), -1)

            detections.append({
                "geometry": geometry,
                "color": color_name,
                "type": geom_type
            })


    # HUD
    if show_frames:
        display_frame = frame.copy()
        y_pos = 25
        cv2.putText(display_frame, "AMARILLO", (10, y_pos), cv2.FONT_HERSHEY_SIMPLEX, 0.4, (0, 215, 255), 1)
        cv2.putText(display_frame, f"P: {yellow_points}  L: {yellow_lines}  POL: {yellow_polygons}", 
                    (100, y_pos), cv2.FONT_HERSHEY_SIMPLEX, 0.4, (200, 255, 255), 1)
        y_pos += 25
        cv2.putText(display_frame, "VERDE", (10, y_pos), cv2.FONT_HERSHEY_SIMPLEX, 0.4, (50, 205, 50), 1)
        cv2.putText(display_frame, f"P: {green_points}  L: {green_lines}  POL: {green_polygons}", 
                    (100, y_pos), cv2.FONT_HERSHEY_SIMPLEX, 0.4, (50, 255, 50), 1)
        
        show_small_window("SISTEMA DE DETECCION", display_frame, scale=0.4)


    return detections

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

def main():
    # Parámetros de visualización/velocidad
    show_frames = True
    video_speed = 25  # ms para waitKey
    scale_factor = 1.0  # Para la detección de cruces, mejor no reescalar

    # Salidas
    os.makedirs('geojson', exist_ok=True)

    cap = cv2.VideoCapture(VIDEO_PATH)
    if not cap.isOpened():
        print(f"Error: No se pudo abrir el video {VIDEO_PATH}")
        return

    # ------------------------------
    # 1) Leer primer frame(s) hasta detectar las 4 cruces verdes
    # ------------------------------
    H = None
    crop_box = None
    transformer = init_transformer()

    for _ in range(10):  # intenta en los primeros 10 frames
        ret, frame0 = cap.read()
        if not ret:
            break

        frame_det = frame0.copy()
        
        corners_img, crop_box, dbg = find_green_cross_corners(frame_det)
        if corners_img is not None:
            # Homografía imagen->UTM usando los extremos conocidos
            H = compute_homography_from_corners(corners_img, GEO_BOUNDS_UTM)

            if show_frames:
                # Visualiza las esquinas detectadas
                for (x, y) in corners_img.astype(int):
                    cv2.drawMarker(frame_det, (x, y), (0,255,0), markerType=cv2.MARKER_CROSS, markerSize=18, thickness=2)
                cv2.imshow("Cruces Verdes Detectadas", frame_det)
                if "mask_green" in dbg:
                    cv2.imshow("Mascara Verde (cruces)", dbg["mask_green"])
                cv2.waitKey(500)
            break

    if H is None or crop_box is None:
        print("No se pudieron detectar 4 cruces verdes para calibrar.")
        cap.release()
        cv2.destroyAllWindows()
        return

    xmin, ymin, xmax, ymax = crop_box

    # ------------------------------
    # 2) Loop principal ya recortando al área entre las cruces y usando H
    # ------------------------------
    try:
        while True:
            ret, frame = cap.read()
            if not ret:
                break

            # Recorte al área delimitada por las cruces
            frame_cropped = frame[ymin:ymax, xmin:xmax].copy()
            h, w = frame_cropped.shape[:2]

            # Procesamiento con homografía (cx,cy en coords del recorte -> sumamos offset al aplicar H)
            # NOTA: raster_to_geo_homography espera coordenadas en el sistema del frame original.
            # Por ello, dentro de process_frame usaremos un wrapper que haga el offset antes de perspectiveTransform.
            detections = process_frame_with_offset(frame_cropped, w, h, transformer, H, xmin, ymin, show_frames)

            # Guardar salidas
            if detections:
                guardar_geojson(detections, 'Point',     'geojson/detecciones_puntos.geojson')
                guardar_geojson(detections, 'LineString','geojson/detecciones_lineas.geojson')
                guardar_geojson(detections, 'Polygon',   'geojson/detecciones_poligonos.geojson')

            if show_frames:
                cv2.imshow("Recorte (Area Modelo)", frame_cropped)

            if cv2.waitKey(video_speed) & 0xFF == ord('q'):
                break

    finally:
        cap.release()
        cv2.destroyAllWindows()
        print("Proceso finalizado - Archivos guardados en 'geojson'.")

# --------- pequeño wrapper para usar H con recorte ----------
def process_frame_with_offset(frame_cropped, w, h, transformer, H, xoff, yoff, show_frames):
    """
    Igual que process_frame, pero ajustando las coordenadas de los puntos (cx,cy) del recorte
    al sistema de coordenadas del frame original antes de aplicar la homografía.
    """
    # Copiamos el núcleo de process_frame pero interceptando la conversión a geo:
    detections = []
    yellow_points = yellow_lines = yellow_polygons = 0
    green_points = green_lines = green_polygons = 0
    mask_yellow = None
    mask_green = None

    blurred = cv2.GaussianBlur(frame_cropped, (3,3), 0)
    hsv = cv2.cvtColor(blurred, cv2.COLOR_BGR2HSV)

    color_ranges = {
        'yellow': (np.array([15, 105, 100]), np.array([30, 255, 255])),
        'green':  (np.array([35, 45,  40]), np.array([80, 255, 255]))
    }
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3,3))

    for color_name, (lower, upper) in color_ranges.items():
        mask = cv2.inRange(hsv, lower, upper)
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel)

        if color_name == 'yellow':
            mask_yellow = mask
        else:
            mask_green = mask

        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

        for contour in contours:
            area = cv2.contourArea(contour)
            if area < 5:
                continue

            epsilon = 0.01 * cv2.arcLength(contour, True)
            approx = cv2.approxPolyDP(contour, epsilon, True)

            geo_points = []
            for point in approx:
                cx, cy = point[0]
                # Ajuste al sistema del frame original antes de aplicar H
                lonlat = raster_to_geo_homography(cx + xoff, cy + yoff, H, transformer)
                if lonlat:
                    geo_points.append(lonlat)

            if len(geo_points) < 2:
                continue

            rect = cv2.minAreaRect(approx)
            (x, y), (w2, h2), angle = rect
            aspect_ratio = max(w2, h2) / min(w2, h2) if min(w2, h2) > 0 else 0

            geom_type = 'point'
            geometry = Point(geo_points[0])

            if aspect_ratio > 3.5 and area > 30:
                geom_type = 'line'
                geometry = LineString(geo_points)
            elif area > 150 and aspect_ratio < 2.0:
                geom_type = 'polygon'
                if geo_points[0] != geo_points[-1]:
                    geo_points.append(geo_points[0])
                geometry = Polygon(geo_points)

            if color_name == 'yellow':
                if geom_type == 'point': yellow_points += 1
                elif geom_type == 'line': yellow_lines += 1
                else: yellow_polygons += 1
            else:
                if geom_type == 'point': green_points += 1
                elif geom_type == 'line': green_lines += 1
                else: green_polygons += 1

            if show_frames:
                box_points = cv2.boxPoints(rect).astype(int)
                if geom_type == 'line':
                    color = (0, 255, 255) if color_name == 'yellow' else (0, 255, 0)
                    cv2.drawContours(frame_cropped, [box_points], 0, color, 2)
                elif geom_type == 'polygon':
                    color = (0, 165, 255) if color_name == 'yellow' else (0, 100, 0)
                    cv2.drawContours(frame_cropped, [approx], -1, color, 2)
                else:
                    cv2.circle(frame_cropped, (int(x), int(y)), 5, (0, 0, 255), -1)

            detections.append({
                "geometry": geometry,
                "color": color_name,
                "type": geom_type
            })

    if show_frames:
        display_frame = frame_cropped.copy()
        y_pos = 25
        cv2.putText(display_frame, "AMARILLO", (10, y_pos), cv2.FONT_HERSHEY_SIMPLEX, 0.4, (0, 215, 255), 1)
        cv2.putText(display_frame, f"P: {yellow_points}  L: {yellow_lines}  POL: {yellow_polygons}",
                    (100, y_pos), cv2.FONT_HERSHEY_SIMPLEX, 0.4, (200, 255, 255), 1)
        y_pos += 25
        cv2.putText(display_frame, "VERDE", (10, y_pos), cv2.FONT_HERSHEY_SIMPLEX, 0.4, (50, 205, 50), 1)
        cv2.putText(display_frame, f"P: {green_points}  L: {green_lines}  POL: {green_polygons}",
                    (100, y_pos), cv2.FONT_HERSHEY_SIMPLEX, 0.4, (50, 255, 50), 1)
        cv2.imshow("SISTEMA DE DETECCION (recorte)", display_frame)

    return detections

if __name__ == "__main__":
    main()
