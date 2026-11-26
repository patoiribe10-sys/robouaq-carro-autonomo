#!/usr/bin/env python3
"""Uso: source /opt/ros/humble/setup.bash && source ~/robocar_ws/install/setup.bash && python3 scripts/prueba_teleop_simulado.py

Prueba del teleop_xbox_node SIN control: publica /joy falsos y revisa /carro/cmd."""
import os
import signal
import subprocess
import sys
import time

# Las pruebas SIMULADAS corren en OTRO dominio ROS (77), aislado del real (0): asi sus /joy y /carro/cmd falsos
# no se mezclan con el control real ni con otra prueba corriendo al mismo tiempo (ROS_LOCALHOST_ONLY es 0).
os.environ['ROS_DOMAIN_ID'] = os.environ.get('ROS_DOMAIN_ID_PRUEBAS', '77')

import rclpy
from rclpy.node import Node
from sensor_msgs.msg import Joy

from robocar_interfaces.msg import ComandoCarro

EXE = os.path.expanduser('~/robocar_ws/install/robocar/lib/robocar/teleop_xbox_node')
res = []


def check(nombre, ok, detalle=''):
    res.append(ok)
    print(f"[{'OK ' if ok else 'FALLA'}] {nombre} {detalle}", flush=True)


class P(Node):
    def __init__(self):
        super().__init__('probador_teleop')
        self.pj = self.create_publisher(Joy, '/joy', 10)
        self.cmds = []
        self.create_subscription(ComandoCarro, '/carro/cmd', lambda m: self.cmds.append((time.monotonic(), m.vel, m.dir)), 10)
        self.ejes = [0.0] * 6
        self.botones = [0] * 21
        self.enviar_joy = True

    def joy(self):
        m = Joy()
        m.axes = [float(a) for a in self.ejes]
        m.buttons = list(self.botones)
        self.pj.publish(m)

    def correr(self, seg, hz=20):
        fin = time.monotonic() + seg
        while time.monotonic() < fin:
            if self.enviar_joy:
                self.joy()
            rclpy.spin_once(self, timeout_sec=1.0 / hz)

    def desde(self, t0):
        return [(v, d) for t, v, d in self.cmds if t >= t0]


def main():
    proc = subprocess.Popen([EXE], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    rclpy.init()
    t = P()
    try:
        # 0) sin ningun /joy
        t.enviar_joy = False
        t.correr(1.5)
        c = t.desde(time.monotonic() - 1.0)
        check('sin /joy publica 0,0 (y sigue publicando a ~20 Hz)', len(c) >= 15 and all(x == (0, 0) for x in c), f'({len(c)} mensajes en 1 s)')
        t.enviar_joy = True

        # 1) stick al frente sin hombre muerto
        t.ejes[1] = 1.0
        t0 = time.monotonic(); t.correr(0.6)
        c = t.desde(t0)
        check('sin LB el stick a fondo NO mueve el carro', all(x == (0, 0) for x in c), f'(max vel {max(abs(v) for v, _ in c)})')

        # 2) con hombre muerto: rampa 10, 20, 30 y se queda en 30
        t.botones[9] = 1
        t0 = time.monotonic(); t.correr(0.8)
        vels = [v for v, _ in t.desde(t0)]
        sube = [v for v in vels if 0 < v]
        pasos = [b - a for a, b in zip([0] + vels, vels)]
        check('con LB y stick a fondo sube con rampa de maximo 10 por ciclo', max(pasos) <= 10, f'(primeros: {vels[:6]}, paso max {max(pasos)})')
        check('llega a 30 (vel_max) y no pasa', vels[-1] == 30 and max(vels) == 30, f'(final {vels[-1]}, max {max(vels)})')

        # 3) turbo
        t.botones[10] = 1
        t0 = time.monotonic(); t.correr(0.8)
        vels = [v for v, _ in t.desde(t0)]
        pasos = [b - a for a, b in zip([30] + vels, vels)]
        check('con RB sube a 60 (turbo) con rampa <= 10', vels[-1] == 60 and max(pasos) <= 10, f'(final {vels[-1]}, paso max {max(pasos)})')
        t.botones[10] = 0

        # 4) direccion
        t.ejes[1] = 0.0
        t.ejes[2] = -1.0
        t.correr(0.6)
        check('stick derecho a la derecha (eje 2 = -1.0) -> dir +100', t.cmds[-1][2] == 100, f'(dir {t.cmds[-1][2]})')
        t.ejes[2] = 1.0
        t.correr(0.4)
        check('stick derecho a la izquierda (eje 2 = +1.0) -> dir -100', t.cmds[-1][2] == -100, f'(dir {t.cmds[-1][2]})')
        t.ejes[2] = 0.05
        t.correr(0.4)
        check('dentro de la zona muerta (0.05) -> dir 0', t.cmds[-1][2] == 0, f'(dir {t.cmds[-1][2]})')
        t.ejes[2] = -0.54
        t.correr(0.4)
        check('a la mitad del recorrido (0.54) -> dir ~50', 45 <= t.cmds[-1][2] <= 55, f'(dir {t.cmds[-1][2]})')

        # 5) reversa
        t.ejes[1] = -1.0
        t.correr(2.0)
        check('reversa limitada a -20', t.cmds[-1][1] == -20, f'(vel {t.cmds[-1][1]})')

        # 6) soltar el hombre muerto en movimiento
        t.ejes[1] = 1.0
        t.correr(1.2)
        antes = t.cmds[-1]
        t.botones[9] = 0
        t0 = time.monotonic(); t.correr(0.4)
        c = t.desde(t0)
        primeros_cero = next((i for i, x in enumerate(c) if x == (0, 0)), None)
        check('al soltar LB frena a 0 y centra en <= 2 ciclos', primeros_cero is not None and primeros_cero <= 2 and all(d == 0 for _, d in c[primeros_cero:]),
              f'(venia en {antes[1:]}, secuencia {c[:4]})')

        # 7) volver a apretar con el stick a fondo: no da tiron
        t.ejes[2] = -1.0
        t.botones[9] = 1
        t0 = time.monotonic(); t.correr(0.5)
        vels = [v for v, _ in t.desde(t0)]
        check('al volver a apretar LB con el stick a fondo NO hay tiron (primer valor <= 10)', vels[0] <= 10, f'(secuencia {vels[:5]})')

        # 8) perder /joy
        t.ejes[2] = 0.0
        t.correr(0.8)
        en_marcha = t.cmds[-1]
        t.enviar_joy = False
        t_corte = time.monotonic()
        t.correr(1.0)
        ceros = [tt for tt, v, d in t.cmds if tt > t_corte and (v, d) == (0, 0)]
        demora = ceros[0] - t_corte if ceros else None
        tras = [(v, d) for tt, v, d in t.cmds if tt > t_corte]
        saltos = [v for v, _ in tras]
        check('al dejar de llegar /joy frena de golpe a 0 en <= 0.7 s', demora is not None and demora <= 0.7,
              f'(venia en {en_marcha[1:]}, demora {1000 * demora:.0f} ms)' if demora is not None else '(nunca)')
        check('y lo hace sin rampa (de golpe)', demora is not None and saltos[saltos.index(0) - 1] in (en_marcha[1],) if 0 in saltos[1:] else False,
              f'(valores justo antes del 0: {saltos[max(0, saltos.index(0) - 2):saltos.index(0) + 1] if 0 in saltos else saltos[:5]})')
        t.enviar_joy = True
        t.correr(0.6)
        check('si vuelve /joy y LB sigue apretado, retoma con rampa', 0 < t.cmds[-1][1] <= 30, f'(vel {t.cmds[-1][1]})')

        # 9) mensaje /joy corto: sin boton 9 -> no hay hombre muerto
        t.botones[9] = 0
        t.correr(0.4)

        class Corto(Joy):
            pass
        m = Joy(); m.axes = [0.0, 1.0, 0.0, 0.0]; m.buttons = [0, 0, 0, 0]
        t0 = time.monotonic()
        t.enviar_joy = False
        for _ in range(15):
            t.pj.publish(m); rclpy.spin_once(t, timeout_sec=0.05)
        c = t.desde(t0)
        check('si el mensaje /joy no trae el boton (indice fuera de rango) no hay movimiento', all(x == (0, 0) for x in c[-8:]), f'({c[-3:]})')

        # 10) cierre: ultimo mensaje 0,0
        t.enviar_joy = True
        t.botones[9] = 1; t.ejes[1] = 1.0
        t.correr(0.8)
        proc.send_signal(signal.SIGINT)
        t0 = time.monotonic()
        proc.wait(timeout=6)
        t.correr(0.3)
        check('al cerrar el teleop termina limpio y el ultimo comando es 0,0', proc.returncode == 0 and t.cmds[-1][1:] == (0, 0),
              f'(codigo {proc.returncode}, ultimo {t.cmds[-1][1:]})')
    finally:
        if proc.poll() is None:
            proc.kill()
        print('\n--- log del teleop ---')
        print(proc.stdout.read()[-1500:])
        t.destroy_node()
        rclpy.shutdown()
    print(f"\n{sum(res)} de {len(res)} comprobaciones OK")
    sys.exit(0 if all(res) else 1)


if __name__ == '__main__':
    main()
