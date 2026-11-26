#!/usr/bin/env python3
"""
nav_node.py — FSM de navegación autónoma RoboUAQ 2026

Estados:
  IDLE → FOLLOW_LANE → APPROACHING_INTERSECTION → STOPPED_AT_INTERSECTION
       → EXECUTE_TURN → FOLLOW_LANE
  FOLLOW_LANE → FINISH (señal "alto")

REGLA CRÍTICA 1.7: Detener 3 segundos antes de cruzar cada intersección.
REGLA 3.10: 20 segundos inmóvil = recorrido terminado (warning al operador).

Mode-aware:
  manual  → FSM pausa, no publica
  observe → FSM corre, calcula, publica /nav/state + /nav/decision_log, NO /cmd_vel
  auto    → FSM corre + publica /cmd_vel y /cmd_steer (control autónomo)
"""

import rclpy
from rclpy.node import Node
from std_msgs.msg import Float32, Int32, String, Bool

import time
import math


# ── Estados FSM ──
class State:
    IDLE = 'IDLE'
    FOLLOW_LANE = 'FOLLOW_LANE'
    APPROACHING_INTERSECTION = 'APPROACHING_INTERSECTION'
    STOPPED_AT_INTERSECTION = 'STOPPED_AT_INTERSECTION'
    EXECUTE_TURN = 'EXECUTE_TURN'
    FINISH = 'FINISH'
    STOPPED = 'STOPPED'


# Señales que indican intersección pero NO bloquean el paso
INTERSECTION_SIGNS = {
    'vuelta_derecha', 'vuelta_izquierda', 'siga_de_frente'
}
# Señales que bloquean el acceso
BLOCK_SIGNS = {'no_pasar', 'sin_salida'}


class NavNode(Node):
    def __init__(self):
        super().__init__('nav_node')

        # ── Parámetros ──
        self.declare_parameter('base_vel', 0.3)
        self.declare_parameter('approach_vel', 0.15)
        self.declare_parameter('turn_vel', 0.2)
        self.declare_parameter('steer_center', 1500)
        self.declare_parameter('steer_gain', 300.0)
        self.declare_parameter('steer_min', 1200)
        self.declare_parameter('steer_max', 1800)
        self.declare_parameter('intersection_stop_s', 3.0)
        self.declare_parameter('turn_duration_s', 2.5)
        self.declare_parameter('approach_dist', 1.0)   # m para iniciar approach
        self.declare_parameter('sign_cooldown_s', 5.0)
        self.declare_parameter('sign_confirm_frames', 3)
        self.declare_parameter('immobile_timeout_s', 20.0)

        self.base_vel = self.get_parameter('base_vel').value
        self.approach_vel = self.get_parameter('approach_vel').value
        self.turn_vel = self.get_parameter('turn_vel').value
        self.steer_center = self.get_parameter('steer_center').value
        self.steer_gain = self.get_parameter('steer_gain').value
        self.steer_min = self.get_parameter('steer_min').value
        self.steer_max = self.get_parameter('steer_max').value
        self.stop_duration = self.get_parameter('intersection_stop_s').value
        self.turn_duration = self.get_parameter('turn_duration_s').value
        self.approach_dist = self.get_parameter('approach_dist').value
        self.sign_cooldown = self.get_parameter('sign_cooldown_s').value
        self.confirm_frames = self.get_parameter('sign_confirm_frames').value
        self.immobile_timeout = self.get_parameter('immobile_timeout_s').value

        # ── Estado FSM ──
        self.state = State.IDLE
        self.mode = 'manual'
        self.estop = False

        # ── Datos de sensores ──
        self.steering_error = 0.0
        self.lane_detected = False
        self.crosswalk_detected = False
        self.current_sign = ''
        self.current_tag_id = -1
        self.current_distance = 0.0
        self.odom_vel = 0.0

        # ── Control de timing ──
        self.state_entered_at = time.time()
        self.last_moving_time = time.time()
        self.last_cmd_vel = 0.0

        # ── Control de señales ──
        self.pending_sign = ''
        self.pending_sign_count = 0
        self.sign_last_seen = {}   # tag_id → timestamp

        # ── Turno en ejecución ──
        self.turn_direction = 0  # -1=izq, 0=recto, +1=der

        # ── Publishers ──
        self.pub_vel = self.create_publisher(Float32, '/cmd_vel', 10)
        self.pub_steer = self.create_publisher(Int32, '/cmd_steer', 10)
        self.pub_state = self.create_publisher(String, '/nav/state', 10)
        self.pub_log = self.create_publisher(String, '/nav/decision_log', 10)

        # ── Subscribers ──
        self.create_subscription(Float32, '/lane/steering_error', self.lane_err_cb, 10)
        self.create_subscription(Bool, '/lane/detected', self.lane_det_cb, 10)
        self.create_subscription(Bool, '/lane/crosswalk_detected', self.crosswalk_cb, 10)
        self.create_subscription(String, '/detection/sign', self.sign_cb, 10)
        self.create_subscription(Int32, '/detection/tag_id', self.tag_id_cb, 10)
        self.create_subscription(Float32, '/detection/distance', self.distance_cb, 10)
        self.create_subscription(Float32, '/odom_vel', self.odom_cb, 10)
        self.create_subscription(String, '/mode', self.mode_cb, 10)
        self.create_subscription(Bool, '/estop', self.estop_cb, 10)
        self.create_subscription(Bool, '/nav/start', self.start_cb, 10)
        self.create_subscription(Bool, '/nav/reset', self.reset_cb, 10)

        # ── Timer principal FSM a 20Hz ──
        self.create_timer(0.05, self.fsm_tick)

        # ── Timer de publicación de estado ──
        self.create_timer(0.2, self.publish_state)

        self.get_logger().info('nav_node listo — esperando modo y señal de inicio')

    # ═══════════════════ CALLBACKS ═══════════════════

    def lane_err_cb(self, msg):
        self.steering_error = msg.data

    def lane_det_cb(self, msg):
        self.lane_detected = msg.data

    def crosswalk_cb(self, msg):
        self.crosswalk_detected = msg.data

    def sign_cb(self, msg):
        sign = msg.data
        if sign == self.pending_sign:
            self.pending_sign_count += 1
        else:
            self.pending_sign = sign
            self.pending_sign_count = 1

    def tag_id_cb(self, msg):
        self.current_tag_id = msg.data

    def distance_cb(self, msg):
        self.current_distance = msg.data

    def odom_cb(self, msg):
        self.odom_vel = msg.data
        if abs(self.odom_vel) > 0.05:
            self.last_moving_time = time.time()

    def mode_cb(self, msg):
        new_mode = msg.data
        if new_mode != self.mode:
            self.mode = new_mode
            self.get_logger().info(f'nav_node modo: {self.mode}')
            if self.mode == 'manual':
                self.state = State.IDLE

    def estop_cb(self, msg):
        self.estop = bool(msg.data)
        if self.estop:
            self.get_logger().warn('ESTOP — nav_node detenido')

    def start_cb(self, msg):
        if msg.data and self.state == State.IDLE:
            self._transition(State.FOLLOW_LANE)
            self._log('Carrera iniciada → FOLLOW_LANE')

    def reset_cb(self, msg):
        if msg.data:
            self._transition(State.IDLE)
            self.pending_sign = ''
            self.pending_sign_count = 0
            self.sign_last_seen = {}
            self._log('FSM reseteada → IDLE')

    # ═══════════════════ FSM PRINCIPAL ═══════════════════

    def fsm_tick(self):
        if self.mode == 'manual' or self.estop:
            return

        # Verificar inmovilidad (regla 3.10)
        if self.state in (State.FOLLOW_LANE, State.APPROACHING_INTERSECTION):
            immobile_s = time.time() - self.last_moving_time
            if immobile_s > self.immobile_timeout:
                self.get_logger().warn(
                    f'[REGLA 3.10] {immobile_s:.0f}s inmóvil — recorrido en riesgo!'
                )

        # Despachar estado
        if self.state == State.IDLE:
            pass

        elif self.state == State.FOLLOW_LANE:
            self._tick_follow_lane()

        elif self.state == State.APPROACHING_INTERSECTION:
            self._tick_approaching()

        elif self.state == State.STOPPED_AT_INTERSECTION:
            self._tick_stopped()

        elif self.state == State.EXECUTE_TURN:
            self._tick_execute_turn()

        elif self.state == State.FINISH:
            self._cmd(0.0, self.steer_center)

        elif self.state == State.STOPPED:
            self._cmd(0.0, self.steer_center)

    def _tick_follow_lane(self):
        # Seguir el carril
        steer = self._error_to_steer(self.steering_error)
        self._cmd(self.base_vel, steer)

        # Detectar señal "alto" → meta
        if self._sign_confirmed('alto'):
            self._cmd(0.0, self.steer_center)
            self._transition(State.FINISH)
            self._log(f'Señal ALTO detectada a {self.current_distance:.2f}m → FINISH')
            return

        # Detectar aproximación a intersección
        sign_near = (
            self.pending_sign_count >= self.confirm_frames
            and self.current_distance < self.approach_dist
            and self.current_distance > 0.0
        )
        if self.crosswalk_detected or sign_near:
            self._transition(State.APPROACHING_INTERSECTION)
            self._log(
                f'Intersección detectada '
                f'(crosswalk={self.crosswalk_detected}, '
                f'sign={self.pending_sign}@{self.current_distance:.2f}m)'
            )

    def _tick_approaching(self):
        # Reducir velocidad al acercarse
        steer = self._error_to_steer(self.steering_error)
        self._cmd(self.approach_vel, steer)

        # Detenerse completamente (velocidad muy baja o cruce muy cercano)
        close = self.current_distance < 0.4 and self.current_distance > 0.0
        slow = abs(self.odom_vel) < 0.05 and (time.time() - self.state_entered_at) > 0.5
        if close or slow:
            self._cmd(0.0, self.steer_center)
            self._transition(State.STOPPED_AT_INTERSECTION)
            self._log('Detenido ante intersección — esperando 3s (regla 1.7)')

    def _tick_stopped(self):
        # Mantenerse parado
        self._cmd(0.0, self.steer_center)

        elapsed = time.time() - self.state_entered_at
        if elapsed >= self.stop_duration:
            # Leer señal confirmada
            sign = self.pending_sign if self.pending_sign_count >= self.confirm_frames else ''
            self.turn_direction = self._sign_to_turn(sign)
            if self.turn_direction is None:
                self._transition(State.STOPPED)
                self._log(f'Señal {sign} — calle bloqueada, esperando operador')
            else:
                self._transition(State.EXECUTE_TURN)
                self._log(
                    f'3s completados — señal="{sign}" → '
                    f'dirección={["izq", "recto", "der"][self.turn_direction + 1]}'
                )

    def _tick_execute_turn(self):
        elapsed = time.time() - self.state_entered_at

        # Calcular steering para el giro
        if self.turn_direction == 1:       # derecha
            steer = self.steer_max
        elif self.turn_direction == -1:    # izquierda
            steer = self.steer_min
        else:                              # recto
            steer = self._error_to_steer(self.steering_error)

        self._cmd(self.turn_vel, steer)

        if self.turn_direction != 0:
            # Giro real: terminar cuando el carril vuelva a detectarse (mín. 1s)
            turn_done = self.lane_detected and elapsed >= 1.0
        else:
            # Recto: usar duración fija
            turn_done = elapsed >= self.turn_duration

        if turn_done:
            # Resetear cooldown y señal pendiente
            if self.current_tag_id >= 0:
                self.sign_last_seen[self.current_tag_id] = time.time()
            self.pending_sign = ''
            self.pending_sign_count = 0
            self._transition(State.FOLLOW_LANE)
            self._log('Giro completado → FOLLOW_LANE')

    # ═══════════════════ HELPERS ═══════════════════

    def _error_to_steer(self, error):
        steer = int(self.steer_center + error * self.steer_gain)
        return max(self.steer_min, min(self.steer_max, steer))

    def _sign_confirmed(self, sign_name):
        return (
            self.pending_sign == sign_name
            and self.pending_sign_count >= self.confirm_frames
        )

    def _sign_to_turn(self, sign):
        if sign == 'vuelta_derecha':
            return 1
        elif sign == 'vuelta_izquierda':
            return -1
        elif sign in BLOCK_SIGNS:
            return None  # calle bloqueada — no entrar
        else:
            return 0  # siga_de_frente o sin señal → recto

    def _transition(self, new_state):
        self.get_logger().info(f'FSM: {self.state} → {new_state}')
        self.state = new_state
        self.state_entered_at = time.time()

    def _cmd(self, vel, steer):
        # En observe: calcular pero NO publicar /cmd_vel ni /cmd_steer
        self.last_cmd_vel = vel
        if self.mode == 'auto' and not self.estop:
            vel_msg = Float32()
            vel_msg.data = float(vel)
            self.pub_vel.publish(vel_msg)

            steer_msg = Int32()
            steer_msg.data = int(steer)
            self.pub_steer.publish(steer_msg)

    def _log(self, message):
        self.get_logger().info(f'[NAV] {message}')
        msg = String()
        msg.data = message
        self.pub_log.publish(msg)

    def publish_state(self):
        msg = String()
        msg.data = self.state
        self.pub_state.publish(msg)


def main(args=None):
    rclpy.init(args=args)
    node = NavNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        # Parada segura
        if node.mode == 'auto':
            vel_msg = Float32()
            vel_msg.data = 0.0
            node.pub_vel.publish(vel_msg)
            steer_msg = Int32()
            steer_msg.data = 1500
            node.pub_steer.publish(steer_msg)
        node.destroy_node()
        rclpy.shutdown()


if __name__ == '__main__':
    main()
