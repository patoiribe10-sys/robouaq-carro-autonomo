#!/usr/bin/env python3
"""
serial_bridge_node.py — Puente UART entre Jetson y ESP32

Suscribe:  /cmd_vel (Float32), /cmd_steer (Int32), /estop (Bool)
Publica:   /odom_vel (Float32), /steer_status (Int32), /encoder_ticks (Int32)
Puerto:    /dev/ttyTHS1 (Jetson hardware UART) con fallback a /dev/ttyUSB0
Protocolo: Jetson→ESP32: "V<vel>,S<steer>\n"   ej: "V0.30,S1500\n"
           ESP32→Jetson: "v<vel>,s<steer>,e<ticks>\n"
"""

import rclpy
from rclpy.node import Node
from std_msgs.msg import Float32, Int32, Bool

import threading
import time

try:
    import serial
    SERIAL_OK = True
except ImportError:
    SERIAL_OK = False


class SerialBridgeNode(Node):
    def __init__(self):
        super().__init__('serial_bridge_node')

        # ── Parámetros ──
        self.declare_parameter('port', '/dev/ttyTHS1')
        self.declare_parameter('baud', 115200)
        self.declare_parameter('send_rate', 20.0)   # Hz
        self.declare_parameter('fallback_port', '/dev/ttyUSB0')
        self.declare_parameter('watchdog_ms', 500)

        self.port = self.get_parameter('port').value
        self.baud = self.get_parameter('baud').value
        self.send_rate = self.get_parameter('send_rate').value
        self.fallback_port = self.get_parameter('fallback_port').value

        # ── Estado ──
        self.cmd_vel = 0.0
        self.cmd_steer = 1500
        self.estop_active = False
        self.ser = None
        self.connected = False

        # ── Publishers ──
        self.pub_odom = self.create_publisher(Float32, '/odom_vel', 10)
        self.pub_steer_status = self.create_publisher(Int32, '/steer_status', 10)
        self.pub_encoder = self.create_publisher(Int32, '/encoder_ticks', 10)

        # ── Subscribers ──
        self.create_subscription(Float32, '/cmd_vel', self.cmd_vel_cb, 10)
        self.create_subscription(Int32, '/cmd_steer', self.cmd_steer_cb, 10)
        self.create_subscription(Bool, '/estop', self.estop_cb, 10)

        # ── Timer de envío ──
        self.create_timer(1.0 / self.send_rate, self.send_command)

        # ── Thread de lectura ──
        self.read_thread = threading.Thread(target=self.read_loop, daemon=True)
        self.read_thread.start()

        # ── Conexión inicial ──
        self.connect()

        self.get_logger().info(
            f'serial_bridge listo — puerto: {self.port} @ {self.baud} baud'
        )

    # ── Callbacks ──
    def cmd_vel_cb(self, msg):
        if not self.estop_active:
            self.cmd_vel = float(msg.data)

    def cmd_steer_cb(self, msg):
        if not self.estop_active:
            self.cmd_steer = int(msg.data)

    def estop_cb(self, msg):
        self.estop_active = bool(msg.data)
        if self.estop_active:
            self.cmd_vel = 0.0
            self.cmd_steer = 1500
            self.get_logger().warn('ESTOP activo — motor parado')

    # ── Conexión y reconexión ──
    def connect(self):
        if not SERIAL_OK:
            self.get_logger().error(
                'pyserial no instalado. Instalar: pip3 install pyserial --break-system-packages'
            )
            return

        for port in [self.port, self.fallback_port]:
            try:
                self.ser = serial.Serial(
                    port, self.baud, timeout=0.1,
                    write_timeout=0.1
                )
                self.connected = True
                self.get_logger().info(f'UART conectado en {port}')
                return
            except serial.SerialException as e:
                self.get_logger().warn(f'No se pudo abrir {port}: {e}')

        self.connected = False
        self.get_logger().error(
            'Sin conexión UART. Revisando en 3 segundos...'
        )

    # ── Enviar comando al ESP32 ──
    def send_command(self):
        if not self.connected or self.ser is None:
            self.connect()
            return

        # ESTOP: siempre forzar parada independientemente del estado
        vel = 0.0 if self.estop_active else self.cmd_vel
        steer = 1500 if self.estop_active else self.cmd_steer

        cmd = f'V{vel:.3f},S{steer}\n'
        try:
            self.ser.write(cmd.encode('ascii'))
        except serial.SerialException as e:
            self.get_logger().error(f'Error escribiendo UART: {e}')
            self.connected = False
            self.ser = None

    # ── Thread de lectura continua ──
    def read_loop(self):
        buffer = ''
        while rclpy.ok():
            if not self.connected or self.ser is None:
                time.sleep(0.5)
                continue

            try:
                # Leer byte a byte de forma no-bloqueante
                if self.ser.in_waiting > 0:
                    chunk = self.ser.read(self.ser.in_waiting).decode('ascii', errors='ignore')
                    buffer += chunk

                    # Procesar líneas completas
                    while '\n' in buffer:
                        line, buffer = buffer.split('\n', 1)
                        self.parse_response(line.strip())
                else:
                    time.sleep(0.002)  # 500Hz polling para no saturar CPU

            except serial.SerialException as e:
                self.get_logger().error(f'Error leyendo UART: {e}')
                self.connected = False
                self.ser = None
                buffer = ''
                time.sleep(1.0)
            except Exception as e:
                self.get_logger().error(f'Error inesperado en read_loop: {e}')
                time.sleep(0.1)

    # ── Parsear respuesta del ESP32 ──
    def parse_response(self, line):
        # Formato esperado: "v<vel>,s<steer>,e<ticks>"
        if not line.startswith('v'):
            return
        try:
            parts = line[1:].split(',')
            if len(parts) < 3:
                return

            vel_part = parts[0]
            steer_part = parts[1]
            enc_part = parts[2]

            # v<vel>
            vel = float(vel_part)
            # s<steer>
            steer = int(steer_part.lstrip('s'))
            # e<ticks>
            ticks = int(enc_part.lstrip('e'))

            odom_msg = Float32()
            odom_msg.data = vel
            self.pub_odom.publish(odom_msg)

            steer_msg = Int32()
            steer_msg.data = steer
            self.pub_steer_status.publish(steer_msg)

            enc_msg = Int32()
            enc_msg.data = ticks
            self.pub_encoder.publish(enc_msg)

        except (ValueError, IndexError) as e:
            self.get_logger().debug(f'Línea malformada: "{line}" — {e}')


def main(args=None):
    rclpy.init(args=args)
    node = SerialBridgeNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        # Parada segura
        if node.connected and node.ser:
            try:
                node.ser.write(b'V0.000,S1500\n')
            except Exception:
                pass
            node.ser.close()
        node.destroy_node()
        rclpy.shutdown()


if __name__ == '__main__':
    main()
