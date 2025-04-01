# 🔹 Importar las librerías
import cv2
import numpy as np
import geopandas as gpd
from shapely.geometry import Point, LineString, Polygon
from pyproj import Transformer
import os

def init_transformer():
    return Transformer.from_crs(32614, 4326, always_xy=True)

def raster_to_geo(cx, cy, width, height, transformer, xmin_geo, ymin_geo, xmax_geo, ymax_geo):
    try:
        x_geo = xmin_geo + (cx / width) * (xmax_geo - xmin_geo)
        y_geo = ymin_geo + (1 - cy / height) * (ymax_geo - ymin_geo)
        lon, lat = transformer.transform(x_geo, y_geo)
        return (lon, lat)
    except Exception as e:
        print(f"Error en transformación de coordenadas: {e}")
        return None

def process_frame(frame, width, height, transformer, xmin_geo, ymin_geo, xmax_geo, ymax_geo, show_frames):
    detections = []
    yellow_points = yellow_lines = yellow_polygons = 0
    green_points = green_lines = green_polygons = 0
    mask_yellow = None
    mask_green = None

    # Preprocesamiento
    blurred = cv2.GaussianBlur(frame, (3,3), 0)
    hsv = cv2.cvtColor(blurred, cv2.COLOR_BGR2HSV)

    # Rangos de color
    color_ranges = {
        'yellow': (np.array([15, 105, 100]), np.array([30, 255, 255])),
        'green': (np.array([35, 45, 40]), np.array([80, 255, 255]))
    }

    # Operaciones morfológicas
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
            if area < 5: continue

            # Aproximación del contorno
            epsilon = 0.01 * cv2.arcLength(contour, True)
            approx = cv2.approxPolyDP(contour, epsilon, True)
            
            # Convertir puntos a coordenadas geográficas
            geo_points = []
            for point in approx:
                cx, cy = point[0]
                coords = raster_to_geo(cx, cy, width, height, transformer,
                                     xmin_geo, ymin_geo, xmax_geo, ymax_geo)
                if coords:
                    geo_points.append(coords)
            
            if len(geo_points) < 2:
                continue

            # Determinar tipo de geometría
            rect = cv2.minAreaRect(approx)
            (x, y), (w, h), angle = rect
            aspect_ratio = max(w, h) / min(w, h) if min(w, h) > 0 else 0
            
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

            # Actualizar contadores
            if color_name == 'yellow':
                if geom_type == 'point': yellow_points += 1
                elif geom_type == 'line': yellow_lines += 1
                else: yellow_polygons += 1
            else:
                if geom_type == 'point': green_points += 1
                elif geom_type == 'line': green_lines += 1
                else: green_polygons += 1

            # Dibujar resultados
            if show_frames:
                box_points = cv2.boxPoints(rect).astype(int)
                if geom_type == 'line':
                    color = (0, 255, 255) if color_name == 'yellow' else (0, 255, 0)
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

    # Visualización mejorada
    if show_frames:
        display_frame = frame.copy()
        y_pos = 25
        # Amarillo
        cv2.putText(display_frame, "AMARILLO", (10, y_pos), 
                   cv2.FONT_HERSHEY_SIMPLEX, 0.4, (0, 215, 255), 1)
        cv2.putText(display_frame, f"P: {yellow_points}  L: {yellow_lines}  POL: {yellow_polygons}", 
                   (100, y_pos), cv2.FONT_HERSHEY_SIMPLEX, 0.4, (200, 255, 255), 1)
        # Verde
        y_pos += 25
        cv2.putText(display_frame, "VERDE", (10, y_pos), 
                   cv2.FONT_HERSHEY_SIMPLEX, 0.4, (50, 205, 50), 1)
        cv2.putText(display_frame, f"P: {green_points}  L: {green_lines}  POL: {green_polygons}", 
                   (100, y_pos), cv2.FONT_HERSHEY_SIMPLEX, 0.4, (50, 255, 50), 1)
        
        cv2.imshow("SISTEMA DE DETECCION", display_frame)
        if mask_yellow is not None:
            cv2.imshow("Mascara Amarilla", mask_yellow)
        if mask_green is not None:
            cv2.imshow("Mascara Verde", mask_green)

    return detections

def guardar_geojson(datos, tipo_geom, nombre):
    features = []
    for d in datos:
        if d['geometry'].geom_type.lower() == tipo_geom.lower():
            feature = {
                "type": "Feature",
                "properties": {
                    "color": d['color'],
                    "type": d['type']
                },
                "geometry": d['geometry'].__geo_interface__
            }
            features.append(feature)
    
    if features:
        gdf = gpd.GeoDataFrame.from_features(features, crs="EPSG:4326")
        gdf.to_file(nombre, driver='GeoJSON')

def main():
    # Configuración
    video_path = "./Mesa3D/20241128_151857_todos.mp4"
    #video_path = "http://192.168.1.66:4747/video"
    show_frames = True
    video_speed = 25
    scale_factor = 0.3
    ymin, xmin, ymax, xmax = 125, 50, 500, 300
    geo_bounds = (416316.969, 2079317.310, 617400.705, 2256323.915)
    
    # Directorio para resultados
    os.makedirs('geojson', exist_ok=True)

    transformer = init_transformer()
    cap = cv2.VideoCapture(video_path)
    
    if not cap.isOpened():
        print("Error: No se pudo abrir el video")
        return

    try:
        while cap.isOpened():
            ret, frame = cap.read()
            if not ret: break

            # Preprocesamiento
            frame_resized = cv2.resize(frame, None, fx=scale_factor, fy=scale_factor)
            frame_cropped = frame_resized[ymin:ymax, xmin:xmax]
            h, w = frame_cropped.shape[:2]

            # Procesamiento
            detections = process_frame(frame_cropped, w, h, transformer,
                                     *geo_bounds, show_frames)
            
            # Guardar en GeoJSONs separados
            if detections:
                guardar_geojson(detections, 'Point', 'geojson/detecciones_puntos.geojson')
                guardar_geojson(detections, 'LineString', 'geojson/detecciones_lineas.geojson')
                guardar_geojson(detections, 'Polygon', 'geojson/detecciones_poligonos.geojson')

            # Control de salida
            if cv2.waitKey(video_speed) & 0xFF == ord('q'):
                break

    finally:
        cap.release()
        cv2.destroyAllWindows()
        print("Proceso finalizado - Archivos guardados en directorio 'geojson'")

if __name__ == "__main__":
    main()