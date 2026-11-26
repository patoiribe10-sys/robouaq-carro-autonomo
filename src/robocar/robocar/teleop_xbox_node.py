#!/usr/bin/env python3
"""
teleop_xbox_node.py — Control remoto con Xbox One Controller
=============================================================
Modos de operación (publicados en /mode):
  "manual"  → Xbox controla carro. Nodos de visión NO corren.
  "observe" → Xbox controla carro. Visión corre en modo pasivo:
              cámaras activas, detecciones marcadas en stream,
              FSM calcula decisiones pero NO las ejecuta.
              PID sí interviene (controla velocidad del joystick).
              Servo respeta límites físicos.
  "auto"    → Autónomo completo. Xbox solo para ESTOP y cambio de modo.

Mapeo Xbox One Controller:
  Stick izquierdo Y  = Velocidad (adelante/atrás)
  Stick derecho X    = Dirección (izquierda/derecha)
  Bumper derecho (RB) = Boost velocidad (mantener presionado)
  Bumper izquierdo (LB) = Velocidad reducida (precisión)
  Botón A            = Modo AUTO
  Botón B            = Modo MANUAL
  Botón X            = Modo OBSERVE
  Botón Y            = ESTOP (parada de emergencia en cualquier modo)
  Start              = Iniciar carrera (pub /nav/start)
  Back/Select        = Reset FSM a IDLE

Ejes Xbox (pygame):
  Axis 0 = Stick izq X    Axis 1 = Stick izq Y (invertido)
  Axis 2 = Trigger izq     Axis 3 = Stick der X
  Axis 4 = Stick der Y     Axis 5 = Trigger der

Botones Xbox (pygame en Linux):
  0=A  1=B  2=X  3=Y  4=LB  5=RB  6=Back  7=Start  8=Xbox  9=StickL  10=StickR
"""

import rclpy
from rclpy.node import Node
from std_msgs.msg import Float32, Int32, String, Bool
import threading
import time

try:
    import pygame
    PYGAME_OK = True
except ImportError:
    PYGAME_OK = False


class TeleopXboxNode(Node):
    def __init__(self):
        super().__init__('teleop_xbox_node')

        # ── Parámetros ──
        self.declare_parameter('max_vel', 0.4)
        self.declare_parameter('boost_vel', 0.6)
        self.declare_parameter('slow_vel', 0.2)
        self.declare_parameter('steer_center', 1500)
        self.declare_parameter('steer_min', 1100)
        self.declare_parameter('steer_max', 1900)
        self.declare_parameter('deadzone', 0.1)
        self.declare_parameter('publish_rate', 20.0)  # Hz

        self.max_vel = self.get_parameter('max_vel').value
        self.boost_vel = self.get_parameter('boost_vel').value
        self.slow_vel = self.get_parameter('slow_vel').value
        self.steer_center = self.get_parameter('steer_center').value
        self.steer_min = self.get_parameter('steer_min').value
        self.steer_max = self.get_parameter('steer_max').value
        self.deadzone = self.get_parameter('deadzone').value
        self.rate = self.get_parameter('publish_rate').value

        # ── Estado ──
        self.mode = 'manual'
        self.estop = False
        self.cmd_vel = 0.0
        self.cmd_steer = self.steer_center
        self.joystick_connected = False

        # ── Publishers ──
        self.pub_vel = self.create_publisher(Float32, '/cmd_vel', 10)
        self.pub_steer = self.create_publisher(Int32, '/cmd_steer', 10)
        self.pub_mode = self.create_publisher(String, '/mode', 10)
        self.pub_estop = self.create_publisher(Bool, '/estop', 10)
        self.pub_start = self.create_publisher(Bool, '/nav/start', 10)
        self.pub_reset = self.create_publisher(Bool, '/nav/reset', 10)

        # ── Subscribers para feedback (mostrar en log) ──
        self.create_subscription(Float32, '/odom_vel', self.odom_cb, 10)
        self.create_subscription(String, '/detection/sign', self.sign_cb, 10)
        self.create_subscription(String, '/nav/state', self.nav_state_cb, 10)

        self.odom_vel = 0.0
        self.last_sign = ''
        self.nav_state = 'IDLE'

        # ── Timer de publicación ──
        self.create_timer(1.0 / self.rate, self.publish_loop)

        # ── Timer de status (cada 2 seg muestra estado) ──
        self.create_timer(2.0, self.print_status)

        # ── Thread del joystick ──
        self.joy_thread = threading.Thread(target=self.joystick_loop, daemon=True)
        self.joy_thread.start()

        self.get_logger().info(
            '\n'
            '╔═══════════════════════════════════════════╗\n'
            '║   TELEOP XBOX — Control Remoto RoboCar    ║\n'
            '╠═══════════════════════════════════════════╣\n'
            '║  Stick Izq Y  = Velocidad                 ║\n'
            '║  Stick Der X  = Dirección                 ║\n'
            '║  RB = Boost  |  LB = Precisión            ║\n'
            '║  A = AUTO  |  B = MANUAL  |  X = OBSERVE  ║\n'
            '║  Y = ESTOP (emergencia)                   ║\n'
            '║  Start = Iniciar  |  Back = Reset         ║\n'
            '╚═══════════════════════════════════════════╝'
        )

    # ── Callbacks de feedback ──
    def odom_cb(self, msg):
        self.odom_vel = msg.data

    def sign_cb(self, msg):
        self.last_sign = msg.data

    def nav_state_cb(self, msg):
        self.nav_state = msg.data

    # ── Status periódico ──
    def print_status(self):
        mode_str = self.mode.upper()
        if self.estop:
            mode_str = '!! ESTOP !!'
        self.get_logger().info(
            f'[{mode_str}] vel={self.cmd_vel:+.2f} steer={self.cmd_steer} '
            f'| odom={self.odom_vel:.2f} | sign={self.last_sign or "-"} '
            f'| fsm={self.nav_state} | joy={"OK" if self.joystick_connected else "NO"}'
        )

    # ── Aplicar deadzone ──
    def apply_deadzone(self, value):
        if abs(value) < self.deadzone:
            return 0.0
        # Remap del rango [deadzone, 1.0] → [0.0, 1.0]
        sign = 1.0 if value > 0 else -1.0
        return sign * (abs(value) - self.deadzone) / (1.0 - self.deadzone)

    # ── Loop del joystick (thread separado) ──
    def joystick_loop(self):
        if not PYGAME_OK:
            self.get_logger().error(
                'pygame no instalado. Instalar con: '
                'pip3 install pygame --break-system-packages'
            )
            return

        pygame.init()
        pygame.joystick.init()

        self.get_logger().info('Esperando control Xbox...')

        joystick = None
        while rclpy.ok():
            try:
                pygame.event.pump()

                # Intentar conectar joystick si no hay
                if joystick is None or not self.joystick_connected:
                    pygame.joystick.quit()
                    pygame.joystick.init()
                    if pygame.joystick.get_count() > 0:
                        joystick = pygame.joystick.Joystick(0)
                        joystick.init()
                        self.joystick_connected = True
                        self.get_logger().info(
                            f'Control conectado: {joystick.get_name()}'
                        )
                    else:
                        self.joystick_connected = False
                        time.sleep(1.0)
                        continue

                # Leer eventos
                for event in pygame.event.get():
                    if event.type == pygame.JOYBUTTONDOWN:
                        self.handle_button(event.button)
                    elif event.type == pygame.JOYDEVICEREMOVED:
                        self.joystick_connected = False
                        joystick = None
                        self.get_logger().warn('Control desconectado!')
                        # Seguridad: parar el carro
                        self.cmd_vel = 0.0
                        self.cmd_steer = self.steer_center

                if not self.joystick_connected or joystick is None:
                    time.sleep(0.1)
                    continue

                # ── Leer ejes solo si modo manual u observe ──
                if self.mode in ('manual', 'observe') and not self.estop:
                    # Velocidad: stick izquierdo Y (axis 1, invertido)
                    raw_vel = -joystick.get_axis(1)  # Arriba = positivo
                    vel_input = self.apply_deadzone(raw_vel)

                    # Dirección: stick derecho X (axis 3)
                    raw_steer = joystick.get_axis(3)
                    steer_input = self.apply_deadzone(raw_steer)

                    # Modificadores de velocidad
                    rb_pressed = joystick.get_button(5)  # RB = boost
                    lb_pressed = joystick.get_button(4)  # LB = precisión

                    if rb_pressed:
                        active_max_vel = self.boost_vel
                    elif lb_pressed:
                        active_max_vel = self.slow_vel
                    else:
                        active_max_vel = self.max_vel

                    # Calcular comandos
                    self.cmd_vel = vel_input * active_max_vel

                    # Mapear steer: -1..+1 → steer_min..steer_max
                    steer_range = (self.steer_max - self.steer_min) / 2.0
                    self.cmd_steer = int(
                        self.steer_center + steer_input * steer_range
                    )
                    self.cmd_steer = max(
                        self.steer_min, min(self.steer_max, self.cmd_steer)
                    )

                time.sleep(0.02)  # ~50Hz polling

            except Exception as e:
                self.get_logger().error(f'Error joystick: {e}')
                self.joystick_connected = False
                joystick = None
                time.sleep(1.0)

    # ── Manejo de botones ──
    def handle_button(self, button):
        if button == 3:  # Y → ESTOP
            self.estop = not self.estop
            if self.estop:
                self.cmd_vel = 0.0
                self.cmd_steer = self.steer_center
                self.get_logger().warn('!! ESTOP ACTIVADO !!')
            else:
                self.get_logger().info('ESTOP desactivado')
            msg = Bool()
            msg.data = self.estop
            self.pub_estop.publish(msg)
            return

        if self.estop:
            self.get_logger().warn('ESTOP activo — presiona Y para desactivar')
            return

        if button == 0:  # A → AUTO
            self.mode = 'auto'
            self.cmd_vel = 0.0
            self.cmd_steer = self.steer_center
            self.get_logger().info('MODO: AUTO — control autónomo')
        elif button == 1:  # B → MANUAL
            self.mode = 'manual'
            self.get_logger().info('MODO: MANUAL — solo joystick')
        elif button == 2:  # X → OBSERVE
            self.mode = 'observe'
            self.get_logger().info('MODO: OBSERVE — joystick + visión pasiva')
        elif button == 7:  # Start → Iniciar carrera
            msg = Bool()
            msg.data = True
            self.pub_start.publish(msg)
            self.get_logger().info('>> CARRERA INICIADA <<')
        elif button == 6:  # Back → Reset
            msg = Bool()
            msg.data = True
            self.pub_reset.publish(msg)
            self.get_logger().info('>> FSM RESETEADA <<')

    # ── Loop de publicación ──
    def publish_loop(self):
        # Publicar modo siempre
        mode_msg = String()
        mode_msg.data = self.mode
        self.pub_mode.publish(mode_msg)

        # ESTOP: forzar parada
        if self.estop:
            vel_msg = Float32()
            vel_msg.data = 0.0
            self.pub_vel.publish(vel_msg)
            steer_msg = Int32()
            steer_msg.data = self.steer_center
            self.pub_steer.publish(steer_msg)
            return

        # En manual u observe: publicar comandos del joystick
        if self.mode in ('manual', 'observe'):
            vel_msg = Float32()
            vel_msg.data = float(self.cmd_vel)
            self.pub_vel.publish(vel_msg)

            steer_msg = Int32()
            steer_msg.data = self.cmd_steer
            self.pub_steer.publish(steer_msg)

        # En auto: NO publicar (nav_node controla)


def main(args=None):
    rclpy.init(args=args)
    node = TeleopXboxNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        # Parada segura al salir
        vel_msg = Float32()
        vel_msg.data = 0.0
        node.pub_vel.publish(vel_msg)
        steer_msg = Int32()
        steer_msg.data = 1500
        node.pub_steer.publish(steer_msg)
        node.destroy_node()


if __name__ == '__main__':
    main()
