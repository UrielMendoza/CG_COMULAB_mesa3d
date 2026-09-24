"""
Prueba rápida de la fuente de video antes de abrir el detector.

Uso:
    python probar_camara.py                              # DroidCam en la IP por defecto
    python probar_camara.py http://192.168.1.66:4747/video
    python probar_camara.py 0                            # cámara USB / integrada (índice)

Presiona 'q' para salir.
"""
import sys
import cv2

DEFAULT_SOURCE = 'http://192.168.1.66:4747/video'  # URL estándar de DroidCam (IP del celular)


def abrir_fuente(source):
    source = int(source) if str(source).isdigit() else source
    cap = cv2.VideoCapture(source)

    if not cap.isOpened():
        print(f"Error: No se pudo abrir la transmisión de video desde {source}")
        return

    while True:
        ret, frame = cap.read()

        if not ret:
            print("Error al leer el frame")
            break

        cv2.imshow("Prueba de camara", frame)

        if cv2.waitKey(1) & 0xFF == ord('q'):
            break

    cap.release()
    cv2.destroyAllWindows()


if __name__ == "__main__":
    abrir_fuente(sys.argv[1] if len(sys.argv) > 1 else DEFAULT_SOURCE)
