# 🔹 Importar las librerías
import cv2
import numpy as np
import geopandas as gpd
from shapely.geometry import Point
from pyproj import Transformer

def init_transformer():
    return Transformer.from_crs(32614, 4326, always_xy=True)

def raster_to_geo(cx, cy, width, height, transformer, xmin_geo, ymin_geo, xmax_geo, ymax_geo):
    try:
        x_geo = xmin_geo + (cx / width) * (xmax_geo - xmin_geo)
        y_geo = ymin_geo + (1 - cy / height) * (ymax_geo - ymin_geo)
        lon, lat = transformer.transform(x_geo, y_geo)
        return Point(lon, lat)
    except Exception as e:
        print(f"Error en transformación de coordenadas: {e}")
        return None

def process_frame(frame, width, height, transformer, xmin_geo, ymin_geo, xmax_geo, ymax_geo, show_frames):
    points = []
    yellow_count = 0
    green_count = 0

    # Preprocesamiento suave
    blurred = cv2.GaussianBlur(frame, (3,3), 0)
    hsv = cv2.cvtColor(blurred, cv2.COLOR_BGR2HSV)

    # Rangos de color ampliados pero precisos
    lower_yellow = np.array([15, 105, 100])  # ±5 en H
    upper_yellow = np.array([30, 255, 255])
    lower_green = np.array([35, 45, 40])     # ±10 en H
    upper_green = np.array([80, 255, 255])

    # Operaciones morfológicas optimizadas
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3,3))
    
    # Procesamiento para amarillo
    mask_yellow = cv2.inRange(hsv, lower_yellow, upper_yellow)
    mask_yellow = cv2.morphologyEx(mask_yellow, cv2.MORPH_OPEN, kernel)
    
    # Procesamiento para verde
    mask_green = cv2.inRange(hsv, lower_green, upper_green)
    mask_green = cv2.morphologyEx(mask_green, cv2.MORPH_OPEN, kernel)

    # Detección de contornos con validaciones ajustadas
    for color, mask in [('yellow', mask_yellow), ('green', mask_green)]:
        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        
        for contour in contours:
            area = cv2.contourArea(contour)
            if area < 2:  # Área mínima reducida
                continue
                
            x, y, w, h = cv2.boundingRect(contour)
            aspect_ratio = w / float(h)
            
            # Rango de relación de aspecto más flexible
            if not 0.0 < aspect_ratio < 15.0:  # Permite más formas
                continue

            cx, cy = x + w//2, y + h//2
            point = raster_to_geo(cx, cy, width, height, transformer, 
                                xmin_geo, ymin_geo, xmax_geo, ymax_geo)
            
            if point:
                points.append({"geometry": point, "color": color})
                if color == 'yellow': 
                    yellow_count += 1
                    draw_color = (0, 255, 255)  # Amarillo
                else: 
                    green_count += 1
                    draw_color = (0, 255, 0)   # Verde

                if show_frames:
                    cv2.rectangle(frame, (x, y), (x + w, y + h), draw_color, 2)
                    cv2.circle(frame, (cx, cy), 5, (0, 0, 255), -1)

    # Visualización
    if show_frames:
        display_frame = frame.copy()
        cv2.putText(display_frame, f"Amarillos: {yellow_count}", (10, 25), 
                   cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 215, 255), 2)
        cv2.putText(display_frame, f"Verdes: {green_count}", (10, 55), 
                   cv2.FONT_HERSHEY_SIMPLEX, 0.7, (50, 205, 50), 2)
        
        cv2.imshow("Detecciones", display_frame)
        cv2.imshow("Mascara Amarilla", mask_yellow)
        cv2.imshow("Mascara Verde", mask_green)

    return points

# 🔹 Función principal
def main():
    # Configuración
    video_path = "./Mesa3D/20241128_150153_punto.mp4"
    show_frames = True
    video_speed = 25  # ms entre frames
    scale_factor = 0.3
    ymin, xmin, ymax, xmax = 125, 50, 500, 300
    xmin_geo, ymin_geo, xmax_geo, ymax_geo = 416316.969, 2079317.310, 617400.705, 2256323.915
    
    # Inicializar componentes
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

            # Procesamiento y visualización
            points = process_frame(frame_cropped, w, h, transformer,
                                 xmin_geo, ymin_geo, xmax_geo, ymax_geo,
                                 show_frames)
            
            # Actualizar GeoJSON
            if points:
                gdf = gpd.GeoDataFrame(points, geometry='geometry', crs="EPSG:4326")
                gdf.to_file("detecciones_actual.geojson", driver="GeoJSON")

            # Control de salida
            if cv2.waitKey(video_speed) & 0xFF == ord('q'):
                break

    finally:
        cap.release()
        cv2.destroyAllWindows()
        print("Proceso finalizado")

if __name__ == "__main__":
    main()