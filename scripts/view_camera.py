#!/usr/bin/env python3
"""
view_camera.py — Visor debug de lane_node
Suscribe a /lane/debug_image (CompressedImage) y lo muestra con cv2.imshow.
Si el topic no tiene datos muestra pantalla de espera.
Uso:  python3 scripts/view_camera.py
      Q / ESC para salir
"""

import threading
import queue
import sys

import cv2
import numpy as np

import rclpy
from rclpy.node import Node
from std_msgs.msg import Float32
from sensor_msgs.msg import CompressedImage


frame_q: queue.Queue = queue.Queue(maxsize=2)
_err = [0.0]


class _ViewerNode(Node):
    def __init__(self):
        super().__init__('view_camera_node')
        self.create_subscription(
            CompressedImage, '/lane/debug_image', self._img_cb, 10
        )
        self.create_subscription(
            Float32, '/lane/steering_error', self._err_cb, 10
        )
        self.get_logger().info(
            'view_camera: esperando /lane/debug_image  (Q para salir)'
        )

    def _img_cb(self, msg):
        try:
            arr = np.frombuffer(msg.data, dtype=np.uint8)
            frame = cv2.imdecode(arr, cv2.IMREAD_COLOR)
            if frame is None:
                return
            try:
                frame_q.put_nowait(frame)
            except queue.Full:
                try:
                    frame_q.get_nowait()
                except queue.Empty:
                    pass
                frame_q.put_nowait(frame)
        except Exception:
            pass

    def _err_cb(self, msg):
        _err[0] = msg.data


def _ros_spin():
    rclpy.init()
    node = _ViewerNode()
    rclpy.spin(node)
    node.destroy_node()
    rclpy.shutdown()


def main():
    threading.Thread(target=_ros_spin, daemon=True).start()

    # Pantalla de espera cuando lane_node no está publicando
    wait_img = np.zeros((400, 640, 3), dtype=np.uint8)
    cv2.putText(wait_img, 'Esperando /lane/debug_image...',
                (55, 190), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (60, 60, 60), 2)
    cv2.putText(wait_img, '(lane_node debe estar corriendo)',
                (75, 230), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (40, 40, 40), 1)
    cv2.putText(wait_img, 'Q / ESC  para salir',
                (190, 380), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (30, 30, 30), 1)

    print("view_camera: abriendo ventana  [Q/ESC = salir]")

    while True:
        try:
            frame = frame_q.get(timeout=0.5)
        except queue.Empty:
            frame = wait_img.copy()

        # Overlay del error de steering
        err = _err[0]
        label = f'steer_err: {err:+.3f}'
        color = (0, 255, 255) if abs(err) < 0.5 else (0, 128, 255)
        cv2.putText(frame, label, (5, 22),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.65, color, 2)

        cv2.imshow('lane_node debug   [Q/ESC=salir]', frame)
        key = cv2.waitKey(30) & 0xFF
        if key in (ord('q'), ord('Q'), 27):
            break

    cv2.destroyAllWindows()


if __name__ == '__main__':
    main()
