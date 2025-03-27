import cv2

def abrir_droidcam():
    # URL estándar de DroidCam (asegúrate que DroidCam esté ejecutándose en tu teléfono)
    video_path = 'http://192.168.1.66:4747/video'
    
    cap = cv2.VideoCapture(video_path)
    
    if not cap.isOpened():
        print(f"Error: No se pudo abrir la transmisión de video desde {video_path}")
        return
    
    while True:
        ret, frame = cap.read()
        
        if not ret:
            print("Error al leer el frame de DroidCam")
            break
        
        cv2.imshow("DroidCam Stream", frame)
        
        if cv2.waitKey(1) & 0xFF == ord('q'):
            break
    
    cap.release()
    cv2.destroyAllWindows()

if __name__ == "__main__":
    abrir_droidcam()
