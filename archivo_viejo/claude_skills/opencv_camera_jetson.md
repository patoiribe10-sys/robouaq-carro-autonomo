# OpenCV + Cámara CSI en Jetson — RoboUAQ

Aplica cuando: abras cámaras, hagas procesamiento de imagen, calibración o lane detection.

## Abrir cámaras CSI IMX219

```python
def gstreamer_pipeline(sensor_id=0, width=640, height=480, fps=30, flip=0):
    return (
        f"nvarguscamerasrc sensor-id={sensor_id} ! "
        f"video/x-raw(memory:NVMM), width=(int){width}, height=(int){height}, "
        f"format=(string)NV12, framerate=(fraction){fps}/1 ! "
        f"nvvidconv flip-method={flip} ! "
        f"video/x-raw, width=(int){width}, height=(int){height}, format=(string)BGRx ! "
        f"videoconvert ! "
        f"video/x-raw, format=(string)BGR ! appsink"
    )

cap0 = cv2.VideoCapture(gstreamer_pipeline(sensor_id=0), cv2.CAP_GSTREAMER)  # Fisheye arriba
cap1 = cv2.VideoCapture(gstreamer_pipeline(sensor_id=1), cv2.CAP_GSTREAMER)  # Normal frente
```

- cam0 (sensor_id=0): fisheye, apuntando hacia abajo, para lane following
- cam1 (sensor_id=1): normal, apuntando al frente, para señales/AprilTags
- NO usar `cv2.VideoCapture(0)` — las CSI requieren GStreamer

## Calibración

```python
# cam0 (fisheye): usar cv2.fisheye.*
# K: matriz intrínseca 3×3, D: coeficientes distorsión fisheye (4 coefs)
# Guardar en config/camera_fisheye.yaml

# cam1 (normal): usar cv2.calibrateCamera estándar
# Guardar en config/camera_normal.yaml
```

## Lane detection — bird's eye view

```python
# Parámetros en config/bird_eye_params.yaml
# src_points: trapecio en imagen original
# dst_points: rectángulo en imagen rectificada
M = cv2.getPerspectiveTransform(src_pts, dst_pts)
warped = cv2.warpPerspective(frame, M, (out_w, out_h))

# Umbral HSV para cinta eléctrica negra sobre PVC blanco/claro
hsv = cv2.cvtColor(warped, cv2.COLOR_BGR2HSV)
lower_dark = np.array([0, 0, 0])
upper_dark = np.array([180, 255, 80])
mask = cv2.inRange(hsv, lower_dark, upper_dark)
```

## Detección de cruce peatonal (crosswalk)

En vista bird's eye, buscar múltiples contornos horizontales agrupados (patrón zebra).
Publicar `crosswalk_detected=True` cuando se detecten ≥3 líneas horizontales paralelas.

## AprilTag detection (cam1)

```python
import dt_apriltags
detector = dt_apriltags.Detector(families='tag36h11')
gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
tags = detector.detect(gray)
for tag in tags:
    tag_id = tag.tag_id          # entero, mapear con apriltag_sign_map.yaml
    corners = tag.corners        # 4 esquinas para dibujar bbox
    pose = tag.pose_t            # traslación → distancia
    # Distancia aproximada: np.linalg.norm(pose)
```

## YOLOv8 inference (secundario)

```python
from ultralytics import YOLO
# Para Jetson exportar a TensorRT primero:
# model = YOLO('models/signs/best.pt')
# model.export(format='engine', imgsz=320)
model = YOLO('models/signs/best.engine', task='detect')
results = model(frame, imgsz=320, conf=0.5)
```

## Reglas de rendimiento

- Redimensionar a 640×480 o 320×240 antes de procesar
- lane_node objetivo: 30 FPS en cam0
- detect_node objetivo: 15–30 FPS en cam1 (AprilTags son ligeros)
- YOLO máx 10 FPS si se usa
- NO usar `cv2.imshow()` en producción — publicar como CompressedImage para dashboard

## Publicar debug image como CompressedImage

```python
from sensor_msgs.msg import CompressedImage
import cv2

def frame_to_compressed_msg(frame, node):
    msg = CompressedImage()
    msg.header.stamp = node.get_clock().now().to_msg()
    msg.format = "jpeg"
    _, buf = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, 80])
    msg.data = buf.tobytes()
    return msg
```
