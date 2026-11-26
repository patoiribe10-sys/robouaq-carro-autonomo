#!/usr/bin/env python3
"""
detect_node.py — Detección de señales con cámara frontal (cam1)

Estrategia dual:
  PRIMARIO:   AprilTag 36h11 (dt_apriltags) → ID + distancia
  SECUNDARIO: YOLOv8n si hay modelo disponible

Señales oficiales RoboUAQ 2026:
  no_pasar, sin_salida, vuelta_derecha, vuelta_izquierda, siga_de_frente, alto

Publica:
  /detection/sign        (String)  — nombre de la señal detectada
  /detection/tag_id      (Int32)   — ID del AprilTag
  /detection/distance    (Float32) — distancia en metros
  /detection/debug_image (CompressedImage) — frame con overlays

Mode-aware: en "manual" → no procesa
"""

import rclpy
from rclpy.node import Node
from std_msgs.msg import String, Int32, Float32, Bool
from sensor_msgs.msg import CompressedImage

import threading
import time
import os

import numpy as np

try:
    import cv2
    CV2_OK = True
except ImportError:
    CV2_OK = False

try:
    import yaml
    YAML_OK = True
except ImportError:
    YAML_OK = False

try:
    import dt_apriltags
    APRILTAG_OK = True
except ImportError:
    APRILTAG_OK = False

YOLO_OK = False
YOLO_MODEL = None


def _try_load_yolo(model_dir):
    global YOLO_OK, YOLO_MODEL
    try:
        from ultralytics import YOLO
        engine = os.path.join(model_dir, 'best.engine')
        pt = os.path.join(model_dir, 'best.pt')
        if os.path.exists(engine):
            YOLO_MODEL = YOLO(engine, task='detect')
            YOLO_OK = True
        elif os.path.exists(pt):
            YOLO_MODEL = YOLO(pt)
            YOLO_OK = True
    except Exception:
        pass


def gstreamer_csi(sensor_id=1, width=640, height=480, fps=30):
    return (
        f"nvarguscamerasrc sensor-id={sensor_id} ! "
        f"video/x-raw(memory:NVMM),width=(int){width},height=(int){height},"
        f"format=(string)NV12,framerate=(fraction){fps}/1 ! "
        f"nvvidconv ! video/x-raw,format=(string)BGRx ! "
        f"videoconvert ! video/x-raw,format=(string)BGR ! "
        f"appsink sync=false drop=true max-buffers=1"
    )


# Colores por señal para debug overlay
SIGN_COLORS = {
    'no_pasar':        (0, 0, 255),
    'sin_salida':      (0, 0, 200),
    'vuelta_derecha':  (255, 165, 0),
    'vuelta_izquierda':(255, 165, 0),
    'siga_de_frente':  (0, 255, 0),
    'alto':            (0, 0, 255),
    'unknown':         (128, 128, 128),
}


class DetectNode(Node):
    def __init__(self):
        super().__init__('detect_node')

        # ── Parámetros ──
        self.declare_parameter('sensor_id', 1)
        self.declare_parameter('width', 640)
        self.declare_parameter('height', 480)
        self.declare_parameter('fps', 30)
        self.declare_parameter('config_dir', '')
        self.declare_parameter('model_dir', '')
        self.declare_parameter('apriltag_size', 0.06)  # metros (6cm)
        self.declare_parameter('min_detection_dist', 2.0)  # m máx distancia útil
        self.declare_parameter('conf_threshold', 0.5)

        self.sensor_id = self.get_parameter('sensor_id').value
        self.width = self.get_parameter('width').value
        self.height = self.get_parameter('height').value
        self.fps = self.get_parameter('fps').value
        self.tag_size = self.get_parameter('apriltag_size').value
        self.max_dist = self.get_parameter('min_detection_dist').value
        self.conf_thresh = self.get_parameter('conf_threshold').value

        ws = os.path.expanduser('~/robocar_ws')
        config_dir = self.get_parameter('config_dir').value or os.path.join(
            ws, 'src', 'robocar', 'config'
        )
        model_dir = self.get_parameter('model_dir').value or os.path.join(
            ws, 'models', 'signs'
        )
        self.config_dir = config_dir

        # ── Estado ──
        self.mode = 'manual'
        self.cap = None
        self.frame = None
        self.frame_lock = threading.Lock()

        # ── Calibración cam1 ──
        self.K_cam = None   # 3x3 intrínseca para AprilTag pose
        self.D_cam = None   # distorsión

        # ── Mapeo AprilTag → señal ──
        self.tag_map = {0: 'no_pasar', 1: 'sin_salida', 2: 'vuelta_derecha',
                        3: 'vuelta_izquierda', 4: 'siga_de_frente', 5: 'alto'}
        self._load_config()

        # ── AprilTag detector ──
        if APRILTAG_OK:
            self.detector = dt_apriltags.Detector(
                families='tag36h11',
                nthreads=2,
                quad_decimate=2.0,
                quad_sigma=0.0,
                refine_edges=1,
                decode_sharpening=0.25
            )
            self.get_logger().info('dt_apriltags detector listo')
        else:
            self.detector = None
            self.get_logger().warn(
                'dt_apriltags no instalado. '
                'Instalar: pip3 install dt-apriltags --break-system-packages'
            )

        # ── YOLO (secundario) ──
        _try_load_yolo(model_dir)
        if YOLO_OK:
            self.get_logger().info('Modelo YOLO cargado')
        else:
            self.get_logger().info('Sin modelo YOLO (normal si no está entrenado)')

        # ── Publishers ──
        self.pub_sign = self.create_publisher(String, '/detection/sign', 10)
        self.pub_tag_id = self.create_publisher(Int32, '/detection/tag_id', 10)
        self.pub_distance = self.create_publisher(Float32, '/detection/distance', 10)
        self.pub_debug = self.create_publisher(
            CompressedImage, '/detection/debug_image', 10
        )

        # ── Subscribers ──
        self.create_subscription(String, '/mode', self.mode_cb, 10)

        # ── Timer de procesamiento ──
        self.create_timer(1.0 / self.fps, self.process_frame)

        # ── Thread de captura ──
        self.cap_thread = threading.Thread(target=self.capture_loop, daemon=True)
        self.cap_thread.start()

        self.get_logger().info(
            f'detect_node listo — cam sensor-id={self.sensor_id}'
        )

    def _load_config(self):
        if not YAML_OK:
            return

        # Mapeo AprilTag
        tag_map_path = os.path.join(self.config_dir, 'apriltag_sign_map.yaml')
        if os.path.exists(tag_map_path):
            with open(tag_map_path) as f:
                data = yaml.safe_load(f)
            if data and 'tags' in data:
                self.tag_map = {int(k): v for k, v in data['tags'].items()}
            self.get_logger().info('apriltag_sign_map.yaml cargado')

        # Calibración cam1
        cam_path = os.path.join(self.config_dir, 'camera_normal.yaml')
        if os.path.exists(cam_path):
            with open(cam_path) as f:
                p = yaml.safe_load(f)
            if 'camera_matrix' in p:
                self.K_cam = np.array(
                    p['camera_matrix']['data'], dtype=np.float64
                ).reshape(3, 3)
                self.D_cam = np.array(
                    p['distortion_coefficients']['data'], dtype=np.float64
                )
                self.get_logger().info('Calibración cam1 cargada')

    def mode_cb(self, msg):
        new_mode = msg.data
        if new_mode != self.mode:
            self.mode = new_mode
            self.get_logger().info(f'detect_node modo: {self.mode}')

    def capture_loop(self):
        if not CV2_OK:
            self.get_logger().error('OpenCV no disponible')
            return

        pipeline = gstreamer_csi(self.sensor_id, self.width, self.height, self.fps)

        while rclpy.ok():
            if self.mode == 'manual':
                if self.cap is not None:
                    self.cap.release()
                    self.cap = None
                time.sleep(0.2)
                continue

            if self.cap is None or not self.cap.isOpened():
                self.cap = cv2.VideoCapture(pipeline, cv2.CAP_GSTREAMER)
                if not self.cap.isOpened():
                    self.get_logger().warn(
                        f'No se pudo abrir cam sensor-id={self.sensor_id}. '
                        'Reintentando en 2s...'
                    )
                    self.cap = None
                    time.sleep(2.0)
                    continue
                # Warm-up: esperar estabilización de exposición
                for _ in range(20):
                    self.cap.read()
                self.get_logger().info('Cámara detect abierta')

            ret, frame = self.cap.read()
            if not ret:
                self.cap.release()
                self.cap = None
                time.sleep(0.5)
                continue

            with self.frame_lock:
                self.frame = frame

    def process_frame(self):
        if self.mode == 'manual':
            return

        with self.frame_lock:
            frame = self.frame.copy() if self.frame is not None else None

        if frame is None:
            return

        sign_name, tag_id, distance, debug = self._detect(frame)

        sign_msg = String()
        sign_msg.data = sign_name
        self.pub_sign.publish(sign_msg)

        id_msg = Int32()
        id_msg.data = tag_id
        self.pub_tag_id.publish(id_msg)

        dist_msg = Float32()
        dist_msg.data = float(distance)
        self.pub_distance.publish(dist_msg)

        self.pub_debug.publish(self._to_compressed(debug))

    def _detect(self, frame):
        debug = frame.copy()
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)

        best_sign = ''
        best_id = -1
        best_dist = 0.0

        # ── PRIMARIO: AprilTags ──
        if self.detector is not None and APRILTAG_OK:
            # Parámetros de cámara para estimación de pose
            if self.K_cam is not None:
                fx = self.K_cam[0, 0]
                fy = self.K_cam[1, 1]
                cx = self.K_cam[0, 2]
                cy = self.K_cam[1, 2]
            else:
                fx = fy = 600.0
                cx = self.width / 2.0
                cy = self.height / 2.0

            cam_params = (fx, fy, cx, cy)

            try:
                tags = self.detector.detect(
                    gray,
                    estimate_tag_pose=True,
                    camera_params=cam_params,
                    tag_size=self.tag_size
                )
            except Exception as e:
                self.get_logger().debug(f'Error AprilTag detect: {e}')
                tags = []

            for tag in tags:
                tid = tag.tag_id
                sign = self.tag_map.get(tid, 'unknown')

                # Distancia desde pose translation
                dist = 0.0
                if tag.pose_t is not None:
                    dist = float(np.linalg.norm(tag.pose_t))

                if dist > self.max_dist:
                    continue

                # Dibujar en debug
                corners = tag.corners.astype(int)
                color = SIGN_COLORS.get(sign, (128, 128, 128))
                cv2.polylines(debug, [corners], True, color, 2)
                center = corners.mean(axis=0).astype(int)
                cv2.putText(
                    debug,
                    f'ID:{tid} {sign}',
                    (center[0] - 40, center[1] - 10),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 2
                )
                cv2.putText(
                    debug,
                    f'{dist:.2f}m',
                    (center[0] - 20, center[1] + 15),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.4, (255, 255, 0), 1
                )

                # Guardar mejor detección (más cercana)
                if best_sign == '' or dist < best_dist:
                    best_sign = sign
                    best_id = tid
                    best_dist = dist

        # ── SECUNDARIO: YOLO (si no detectó AprilTag y hay modelo) ──
        if best_sign == '' and YOLO_OK and YOLO_MODEL is not None:
            try:
                results = YOLO_MODEL(frame, imgsz=320, conf=self.conf_thresh, verbose=False)
                for r in results:
                    for box in r.boxes:
                        cls = int(box.cls[0])
                        conf = float(box.conf[0])
                        label = r.names.get(cls, 'unknown')
                        x1, y1, x2, y2 = map(int, box.xyxy[0])
                        cv2.rectangle(debug, (x1, y1), (x2, y2), (255, 165, 0), 2)
                        cv2.putText(
                            debug, f'{label} {conf:.2f}',
                            (x1, y1 - 5),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.45, (255, 165, 0), 1
                        )
                        if not best_sign:
                            best_sign = label
                            best_id = -1
                            best_dist = 0.0
            except Exception as e:
                self.get_logger().debug(f'Error YOLO: {e}')

        # HUD
        cv2.putText(
            debug,
            f'sign: {best_sign or "ninguna"}',
            (10, 20),
            cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 255), 2
        )

        return best_sign, best_id, best_dist, debug

    def _to_compressed(self, frame):
        msg = CompressedImage()
        msg.header.stamp = self.get_clock().now().to_msg()
        msg.format = 'jpeg'
        _, buf = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, 75])
        msg.data = buf.tobytes()
        return msg


def main(args=None):
    rclpy.init(args=args)
    node = DetectNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        if node.cap is not None:
            node.cap.release()
        node.destroy_node()
        rclpy.shutdown()


if __name__ == '__main__':
    main()
