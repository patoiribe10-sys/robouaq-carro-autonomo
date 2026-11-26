#!/usr/bin/env python3
"""ver_joy.py: muestra que eje o boton de /joy cambia al mover el control.

Sirve para confirmar los indices y los signos que usa teleop_xbox_node.

Uso (en otra terminal, con el control prendido):
    source /opt/ros/humble/setup.bash
    ros2 run joy game_controller_node        # si no esta corriendo ya
    python3 ~/robocar_ws/scripts/ver_joy.py

Mueve cada stick y aprieta cada boton: se imprime una linea cada vez que algo cambia.
Ctrl+C para salir. (Evita `ros2 topic echo`: en esta Jetson el demonio de ros2 falla.)
"""
import sys
import time

import rclpy
from rclpy.node import Node
from sensor_msgs.msg import Joy

UMBRAL = 0.15   # cambio minimo en un eje para imprimirlo


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
            print('ejes en reposo   :', [round(a, 2) for a in m.axes])
            print('botones en reposo:', list(m.buttons))
            print('--- ahora mueve sticks y aprieta botones ---', flush=True)
            self.ant_ejes = list(m.axes)
            self.ant_botones = list(m.buttons)
            return
        for i, (a, b) in enumerate(zip(self.ant_ejes, m.axes)):
            if abs(b - a) >= UMBRAL:
                print(f'eje {i}: {a:+.2f} -> {b:+.2f}', flush=True)
                self.ant_ejes[i] = b
            elif abs(b - a) > 0:
                self.ant_ejes[i] = b if abs(b) < 0.05 else self.ant_ejes[i]
        for i, (a, b) in enumerate(zip(self.ant_botones, m.buttons)):
            if a != b:
                print(f'boton {i}: {"PRESIONADO" if b else "soltado"}', flush=True)
                self.ant_botones[i] = b


def main():
    rclpy.init()
    nodo = VerJoy()
    try:
        rclpy.spin(nodo)
    except KeyboardInterrupt:
        pass
    finally:
        if nodo.t0:
            dt = time.monotonic() - nodo.t0
            print(f'\n{nodo.n} mensajes en {dt:.1f} s ({nodo.n / dt:.1f} Hz)')
        nodo.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()
    sys.exit(0)


if __name__ == '__main__':
    main()
