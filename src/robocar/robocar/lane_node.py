#!/usr/bin/env python3
"""
lane_node.py — Detección de carril y crosswalk con cámara fisheye (cam0)

Pipeline: GStreamer CSI → undistort fisheye → bird's eye warp →
          HSV mask → contornos → error de steering + crosswalk detection

Publica:
  /lane/steering_error     (Float32)  — error de carril, -1.0=izq, +1.0=der
  /lane/detected           (Bool)     — ¿hay línea detectada?
  /lane/crosswalk_detected (Bool)     — ¿hay cruce peatonal?
  /lane/debug_image        (CompressedImage) — bird's eye con overlays

Mode-aware: en "manual" → no procesa (ahorra CPU)
"""

import rclpy
from rclpy.node import Node
from std_msgs.msg import Float32, Bool, String
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


def gstreamer_csi(sensor_id=0, width=640, height=480, fps=30):
    return (
        f"nvarguscamerasrc sensor-id={sensor_id} ! "
        f"video/x-raw(memory:NVMM),width=(int){width},height=(int){height},"
        f"format=(string)NV12,framerate=(fraction){fps}/1 ! "
        f"nvvidconv ! video/x-raw,format=(string)BGRx ! "
        f"videoconvert ! video/x-raw,format=(string)BGR ! "
        f"appsink sync=false drop=true max-buffers=1"
    )


class LaneNode(Node):
    def __init__(self):
        super().__init__('lane_node')

        # ── Parámetros ──
        self.declare_parameter('sensor_id', 0)
        self.declare_parameter('width', 640)
        self.declare_parameter('height', 480)
        self.declare_parameter('fps', 30)
        self.declare_parameter('config_dir', '')
        self.declare_parameter('steer_gain', 1.0)
        self.declare_parameter('lost_lane_timeout', 0.5)  # s

        self.sensor_id = self.get_parameter('sensor_id').value
        self.width = self.get_parameter('width').value
        self.height = self.get_parameter('height').value
        self.fps = self.get_parameter('fps').value
        self.steer_gain = self.get_parameter('steer_gain').value
        self.lost_lane_timeout = self.get_parameter('lost_lane_timeout').value

        # Config dir: buscar en share del paquete o path explícito
        config_dir = self.get_parameter('config_dir').value
        if not config_dir:
            ws = os.path.expanduser('~/robocar_ws')
            config_dir = os.path.join(ws, 'src', 'robocar', 'config')
        self.config_dir = config_dir

        # ── Estado ──
        self.mode = 'manual'
        self.last_steering_error = 0.0
        self.last_detected_time = 0.0
        self.cap = None
        self.running = False

        # ── Calibración / parámetros bird's eye ──
        self.K = None          # matriz intrínseca fisheye
        self.D = None          # coeficientes distorsión
        self.M_warp = None     # matriz perspectiva
        self.out_w = 400
        self.out_h = 400
        self.roi_top = 0.4
        self.hsv_lower = np.array([0, 0, 0])
        self.hsv_upper = np.array([180, 255, 80])
        self.min_area = 500
        self.morph_k = 5
        self.crosswalk_min_lines = 3
        self.crosswalk_gap_px = 15

        self._load_config()

        # ── Publishers ──
        self.pub_error = self.create_publisher(Float32, '/lane/steering_error', 10)
        self.pub_detected = self.create_publisher(Bool, '/lane/detected', 10)
        self.pub_crosswalk = self.create_publisher(Bool, '/lane/crosswalk_detected', 10)
        self.pub_debug = self.create_publisher(CompressedImage, '/lane/debug_image', 10)

        # ── Subscriber modo ──
        self.create_subscription(String, '/mode', self.mode_cb, 10)

        # ── Timer de procesamiento ──
        self.create_timer(1.0 / self.fps, self.process_frame)

        # ── Thread de captura ──
        self.frame = None
        self.frame_lock = threading.Lock()
        self.cap_thread = threading.Thread(target=self.capture_loop, daemon=True)
        self.cap_thread.start()

        self.get_logger().info(
            f'lane_node listo — cam sensor-id={self.sensor_id}'
        )

    # ── Carga de calibración y parámetros ──
    def _load_config(self):
        if not YAML_OK:
            self.get_logger().warn('yaml no disponible, usando parámetros default')
            return

        # Bird's eye params
        bep = os.path.join(self.config_dir, 'bird_eye_params.yaml')
        if os.path.exists(bep):
            with open(bep) as f:
                p = yaml.safe_load(f)
            self.out_w = p.get('output_width', 400)
            self.out_h = p.get('output_height', 400)
            self.roi_top = p.get('roi_top_fraction', 0.4)
            lo = p.get('hsv_dark_lower', [0, 0, 0])
            hi = p.get('hsv_dark_upper', [180, 255, 80])
            self.hsv_lower = np.array(lo, dtype=np.uint8)
            self.hsv_upper = np.array(hi, dtype=np.uint8)
            self.min_area = p.get('min_contour_area', 500)
            self.morph_k = p.get('morph_kernel_size', 5)
            self.crosswalk_min_lines = p.get('crosswalk_min_lines', 3)
            self.crosswalk_gap_px = p.get('crosswalk_line_gap_px', 15)

            src = np.float32(p['src_points'])
            dst = np.float32(p['dst_points'])
            self.M_warp = cv2.getPerspectiveTransform(src, dst)
            self.get_logger().info('bird_eye_params.yaml cargado')
        else:
            self.get_logger().warn(
                f'bird_eye_params.yaml no encontrado en {bep} — usando defaults'
            )
            self._default_warp()

        # Calibración fisheye
        fyp = os.path.join(self.config_dir, 'camera_fisheye.yaml')
        if os.path.exists(fyp):
            with open(fyp) as f:
                p = yaml.safe_load(f)
            if 'camera_matrix' in p:
                K_data = p['camera_matrix']['data']
                D_data = p['distortion_coefficients']['data']
                self.K = np.array(K_data, dtype=np.float64).reshape(3, 3)
                self.D = np.array(D_data, dtype=np.float64).reshape(1, -1)
                self.get_logger().info('Calibración fisheye cargada')

    def _default_warp(self):
        src = np.float32([[200, 200], [440, 200], [580, 420], [60, 420]])
        dst = np.float32([[50, 50], [350, 50], [350, 350], [50, 350]])
        self.M_warp = cv2.getPerspectiveTransform(src, dst)

    # ── Callback de modo ──
    def mode_cb(self, msg):
        new_mode = msg.data
        if new_mode != self.mode:
            self.mode = new_mode
            self.get_logger().info(f'lane_node modo: {self.mode}')

    # ── Thread de captura de cámara ──
    def capture_loop(self):
        if not CV2_OK:
            self.get_logger().error('OpenCV no disponible')
            return

        pipeline = gstreamer_csi(self.sensor_id, self.width, self.height, self.fps)
        self.get_logger().info(f'Abriendo cámara: sensor-id={self.sensor_id}')

        while rclpy.ok():
            if self.mode == 'manual':
                # Cerrar cámara para ahorrar recursos
                if self.cap is not None:
                    self.cap.release()
                    self.cap = None
                time.sleep(0.2)
                continue

            # Abrir cámara si no está abierta
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
                self.get_logger().info('Cámara lane abierta')

            ret, frame = self.cap.read()
            if not ret:
                self.get_logger().warn('Frame vacío — reconectando cámara...')
                self.cap.release()
                self.cap = None
                time.sleep(0.5)
                continue

            with self.frame_lock:
                self.frame = frame

    # ── Timer principal de procesamiento ──
    def process_frame(self):
        if self.mode == 'manual':
            return

        with self.frame_lock:
            frame = self.frame.copy() if self.frame is not None else None

        if frame is None:
            return

        steering_error, lane_detected, crosswalk, debug_img = self._process(frame)

        now = time.time()
        if lane_detected:
            self.last_steering_error = steering_error
            self.last_detected_time = now

        # Mantener último valor conocido por lost_lane_timeout segundos
        if not lane_detected and (now - self.last_detected_time) < self.lost_lane_timeout:
            steering_error = self.last_steering_error
            lane_detected = True

        # Publicar
        err_msg = Float32()
        err_msg.data = float(steering_error)
        self.pub_error.publish(err_msg)

        det_msg = Bool()
        det_msg.data = lane_detected
        self.pub_detected.publish(det_msg)

        cw_msg = Bool()
        cw_msg.data = crosswalk
        self.pub_crosswalk.publish(cw_msg)

        if debug_img is not None:
            self.pub_debug.publish(self._to_compressed(debug_img))

    # ── Pipeline de visión ──
    def _process(self, frame):
        if not CV2_OK or self.M_warp is None:
            return 0.0, False, False, None

        debug = frame.copy()
        h, w = frame.shape[:2]

        # 1. Undistort fisheye (si hay calibración)
        if self.K is not None and self.D is not None:
            frame = cv2.fisheye.undistortImage(frame, self.K, self.D)

        # 2. Crop ROI inferior
        roi_y = int(h * self.roi_top)
        roi = frame[roi_y:, :]

        # 3. Bird's eye warp
        # Ajustar src_points al ROI recortado
        warped = cv2.warpPerspective(
            roi, self.M_warp, (self.out_w, self.out_h)
        )
        debug_bird = warped.copy()

        # 4. Máscara HSV para líneas oscuras
        hsv = cv2.cvtColor(warped, cv2.COLOR_BGR2HSV)
        mask = cv2.inRange(hsv, self.hsv_lower, self.hsv_upper)

        # Operaciones morfológicas para limpiar ruido
        k = cv2.getStructuringElement(
            cv2.MORPH_RECT, (self.morph_k, self.morph_k)
        )
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, k)
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, k)

        # 5. Contornos
        contours, _ = cv2.findContours(
            mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
        )

        # Filtrar contornos pequeños
        valid = [c for c in contours if cv2.contourArea(c) > self.min_area]

        if not valid:
            return 0.0, False, False, debug_bird

        # 6. Calcular centroide de la línea más grande
        lane_detected = True
        largest = max(valid, key=cv2.contourArea)
        M_mom = cv2.moments(largest)
        if M_mom['m00'] == 0:
            return 0.0, False, False, debug_bird

        cx = int(M_mom['m10'] / M_mom['m00'])
        center = self.out_w // 2

        # Error normalizado: -1 (izquierda) a +1 (derecha)
        steering_error = float(cx - center) / float(center) * self.steer_gain
        steering_error = max(-1.0, min(1.0, steering_error))

        # 7. Detectar crosswalk con mask de zonas BLANCAS (franjas del paso de cebra)
        mask_white = cv2.inRange(
            hsv,
            np.array([0, 0, 180], dtype=np.uint8),
            np.array([180, 40, 255], dtype=np.uint8),
        )
        white_contours_raw, _ = cv2.findContours(
            mask_white, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
        )
        white_contours = [c for c in white_contours_raw if cv2.contourArea(c) > 300]
        crosswalk = self._detect_crosswalk(white_contours)

        # 8. Dibujar overlays en debug
        cv2.drawContours(debug_bird, valid, -1, (0, 255, 0), 2)
        cv2.circle(debug_bird, (cx, self.out_h // 2), 8, (0, 0, 255), -1)
        cv2.line(debug_bird, (center, 0), (center, self.out_h), (255, 0, 0), 1)

        # Flecha de dirección
        arrow_x = int(center + steering_error * center * 0.8)
        cv2.arrowedLine(
            debug_bird,
            (center, self.out_h - 20),
            (arrow_x, self.out_h - 60),
            (0, 255, 255), 3, tipLength=0.4
        )

        if crosswalk:
            cv2.putText(
                debug_bird, 'CROSSWALK', (10, 30),
                cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 165, 255), 2
            )

        cv2.putText(
            debug_bird, f'err={steering_error:.2f}', (10, self.out_h - 10),
            cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1
        )

        return steering_error, lane_detected, crosswalk, debug_bird

    def _detect_crosswalk(self, contours):
        """Detecta patrón zebra: múltiples contornos horizontales agrupados."""
        if len(contours) < self.crosswalk_min_lines:
            return False

        # Obtener bounding boxes y buscar líneas aproximadamente horizontales
        rects = []
        for c in contours:
            x, y, cw, ch = cv2.boundingRect(c)
            # Línea horizontal: ancho >> alto
            if cw > ch * 2 and cw > 30:
                rects.append((y, cw, ch))

        if len(rects) < self.crosswalk_min_lines:
            return False

        # Verificar que estén agrupadas verticalmente (gaps pequeños)
        rects.sort(key=lambda r: r[0])
        gaps = []
        for i in range(1, len(rects)):
            gap = rects[i][0] - rects[i - 1][0]
            gaps.append(gap)

        if not gaps:
            return False

        avg_gap = sum(gaps) / len(gaps)
        return avg_gap < self.out_h * 0.2 and len(rects) >= self.crosswalk_min_lines

    # ── Comprimir frame a JPEG ──
    def _to_compressed(self, frame):
        msg = CompressedImage()
        msg.header.stamp = self.get_clock().now().to_msg()
        msg.format = 'jpeg'
        _, buf = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, 75])
        msg.data = buf.tobytes()
        return msg


def main(args=None):
    rclpy.init(args=args)
    node = LaneNode()
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
