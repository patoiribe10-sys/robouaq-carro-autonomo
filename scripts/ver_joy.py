#!/usr/bin/env python3
"""ver_joy.py: muestra en vivo que eje o boton de /joy cambia al mover el control, con su nombre.

Uso (en una terminal de verdad, con el control prendido):
    source /opt/ros/humble/setup.bash && source ~/robocar_ws/install/setup.bash
    python3 ~/robocar_ws/scripts/ver_joy.py --lanzar          # arranca game_controller_node con el SDL propio y muestra todo
    python3 ~/robocar_ws/scripts/ver_joy.py                   # solo escucha /joy (si ya hay un game_controller_node corriendo)
    python3 ~/robocar_ws/scripts/ver_joy.py --lanzar --duracion 5   # se detiene solo a los 5 s (para pruebas)

Mueve cada stick y aprieta cada boton: se imprime una linea cada vez que algo cambia. Ctrl+C para salir.
Los nombres corresponden al mapeo ESTANDAR de game_controller_node, que es el que se obtiene con el
SDL_GAMECONTROLLERCONFIG propio (config/sdl_gamecontroller.txt). Con el mapeo de fabrica de SDL los
nombres NO coinciden (ese es justo el problema de este control BLE).
(Evita `ros2 topic echo`: en esta Jetson el demonio de ros2 puede quedar en mal estado.)
"""
import argparse
import os
import signal
import subprocess
import sys
import time

import rclpy
from ament_index_python.packages import get_package_share_directory
from rclpy.node import Node
from sensor_msgs.msg import Joy

UMBRAL = 0.15   # cambio minimo en un eje para imprimirlo

EJES = ['LX  (stick izquierdo, horizontal)', 'LY  (stick izquierdo, vertical)',
        'RX  (stick derecho, horizontal)', 'RY  (stick derecho, vertical)',
        'LT  (gatillo izquierdo)', 'RT  (gatillo derecho)']
BOTONES = ['A', 'B', 'X', 'Y', 'VER (back)', 'LOGO Xbox (guide)', 'MENU (start)', 'clic stick izquierdo',
           'clic stick derecho', 'LB', 'RB', 'cruceta ARRIBA', 'cruceta ABAJO', 'cruceta IZQUIERDA',
           'cruceta DERECHA', 'COMPARTIR (misc1)']


def nombre_eje(i):
    return f'eje {i}: {EJES[i]}' if i < len(EJES) else f'eje {i}'


def nombre_boton(i):
    return f'boton {i}: {BOTONES[i]}' if i < len(BOTONES) else f'boton {i}'


class VerJoy(Node):
    def __init__(self):
        super().__init__('ver_joy')
        self.ant_ejes = None
        self.ant_botones = None
        self.t0 = None
        self.n = 0
        self.create_subscription(Joy, '/joy', self.cb, 10)
        print('Esperando mensajes de /joy ...', flush=True)

    def cb(self, m):
        self.n += 1
        if self.t0 is None:
            self.t0 = time.monotonic()
        if self.ant_ejes is None:
            print(f'/joy: {len(m.axes)} ejes y {len(m.buttons)} botones')
            print('REPOSO (suelta todo). Cada eje debe estar en ~0, salvo si un gatillo descansa en otro valor:')
            for i, a in enumerate(m.axes):
                print(f'   {nombre_eje(i):45s} {a:+.2f}')
            print('botones apretados en reposo:', [i for i, b in enumerate(m.buttons) if b] or 'ninguno')
            print('--- ahora mueve sticks y aprieta botones ---', flush=True)
            self.ant_ejes = list(m.axes)
            self.ant_botones = list(m.buttons)
            return
        for i, (a, b) in enumerate(zip(self.ant_ejes, m.axes)):
            if abs(b - a) >= UMBRAL:
                print(f'{nombre_eje(i):48s} {a:+.2f} -> {b:+.2f}', flush=True)
                self.ant_ejes[i] = b
            elif abs(b) < 0.05:
                self.ant_ejes[i] = b          # volvio al centro: listo para el siguiente movimiento
        for i, (a, b) in enumerate(zip(self.ant_botones, m.buttons)):
            if a != b:
                print(f'{nombre_boton(i):48s} {"PRESIONADO" if b else "soltado"}', flush=True)
                self.ant_botones[i] = b


def lanzar_control():
    """Arranca game_controller_node como lo hace el launch: mismos parametros y el SDL propio si existe."""
    compartido = get_package_share_directory('robocar')
    yaml = os.path.join(compartido, 'config', 'control_remoto.yaml')
    sdl = os.path.join(compartido, 'config', 'sdl_gamecontroller.txt')
    env = dict(os.environ)
    if os.path.exists(sdl):
        env['SDL_GAMECONTROLLERCONFIG'] = open(sdl).read().strip()
        print('Mapa de SDL propio:', sdl)
    else:
        print('(no hay sdl_gamecontroller.txt: mapeo de fábrica de SDL; los nombres de abajo no valdrán)')
    return subprocess.Popen(['/opt/ros/humble/lib/joy/game_controller_node', '--ros-args', '--params-file', yaml],
                            env=env, start_new_session=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--lanzar', action='store_true', help='arranca game_controller_node (con el SDL propio)')
    ap.add_argument('--duracion', type=float, default=0.0, help='segundos antes de salir solo (0 = hasta Ctrl+C)')
    a = ap.parse_args()

    rclpy.init()
    nodo = VerJoy()
    if a.lanzar:
        time.sleep(0.8)
        otros = nodo.count_publishers('/joy')
        if otros > 0:
            print(f'!! Ya hay {otros} publicador(es) en /joy (otro game_controller_node vivo, con el mapeo de fábrica). '
                  'Los valores saldrán mezclados. Ciérralo (pkill -x game_controller_n) y repite.')
    proc = lanzar_control() if a.lanzar else None
    t_fin = time.monotonic() + a.duracion if a.duracion > 0 else None
    try:
        while rclpy.ok() and (t_fin is None or time.monotonic() < t_fin):
            rclpy.spin_once(nodo, timeout_sec=0.1)
    except KeyboardInterrupt:
        pass
    finally:
        if nodo.t0:
            dt = time.monotonic() - nodo.t0
            print(f'\n{nodo.n} mensajes en {dt:.1f} s ({nodo.n / dt:.1f} Hz)')
        elif a.lanzar:
            print('\nNo llegó nada de /joy: ¿está prendido y conectado el control?')
        nodo.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()
        if proc is not None:
            try:
                os.killpg(proc.pid, signal.SIGINT)
                proc.wait(timeout=5)
            except Exception:
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except Exception:
                    pass
    sys.exit(0)


if __name__ == '__main__':
    main()
