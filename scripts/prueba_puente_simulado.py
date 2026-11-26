#!/usr/bin/env python3
"""Uso: source /opt/ros/humble/setup.bash && source ~/robocar_ws/install/setup.bash && python3 scripts/prueba_puente_simulado.py

Prueba del serial_bridge_node SIN hardware: un ESP32 falso sobre un pseudo-terminal.

Verifica: pong, telemetria -> /carro/estado, comandos a 20 Hz, timeout propio (V0,S0),
reconexion y parada al cerrar. No toca /dev/esp32.
"""
import os
import re
import signal
import subprocess
import sys
import tempfile
import threading
import time

import rclpy
from rclpy.node import Node

from robocar_interfaces.msg import ComandoCarro, EstadoCarro

EXE = os.path.expanduser('~/robocar_ws/install/robocar/lib/robocar/serial_bridge_node')
SIM = os.path.join(tempfile.gettempdir(), 'esp32_sim')


class ESP32Falso:
    """Lee comandos, contesta P con pong y manda telemetria a 20 Hz."""

    def __init__(self):
        self.master = None
        self.recibido = []          # (t, linea)
        self.pings = 0
        self.mv, self.estado, self.pulsos = 11800, 0, 120
        self.dir = 0
        self._parar = False
        self._hilos = []

    def abrir(self):
        self.master, slave = os.openpty()
        nombre = os.ttyname(slave)
        self._slave = slave          # lo dejamos abierto para que el pty no desaparezca
        if os.path.islink(SIM) or os.path.exists(SIM):
            os.remove(SIM)
        os.symlink(nombre, SIM)
        self._parar = False
        for f in (self._leer, self._telemetria):
            h = threading.Thread(target=f, daemon=True)
            h.start()
            self._hilos.append(h)
        return nombre

    def _leer(self):
        buf = b''
        while not self._parar:
            try:
                d = os.read(self.master, 256)
            except OSError:
                return
            buf += d
            while b'\n' in buf:
                linea, buf = buf.split(b'\n', 1)
                s = linea.decode(errors='ignore').strip().replace('\r', '')
                if s == 'P':
                    self.pings += 1
                    try:
                        os.write(self.master, b'pong\n')
                    except OSError:
                        pass
                elif s.startswith('V'):
                    self.recibido.append((time.monotonic(), s))
                    m = re.match(r'V(-?\d+),S(-?\d+)', s)
                    if m:
                        self.dir = int(m.group(2))

    def _telemetria(self):
        while not self._parar:
            time.sleep(0.05)
            try:
                os.write(self.master, f'v{self.pulsos},s{self.dir},b{self.mv},e{self.estado}\n'.encode())
            except OSError:
                return

    def desconectar(self):
        """Simula desenchufar el USB."""
        self._parar = True
        for fd in (self.master, self._slave):
            try:
                os.close(fd)
            except OSError:
                pass
        if os.path.islink(SIM):
            os.remove(SIM)


class Probador(Node):
    def __init__(self):
        super().__init__('probador_puente')
        self.pub = self.create_publisher(ComandoCarro, '/carro/cmd', 10)
        self.estados = []           # (t, msg)
        self.create_subscription(EstadoCarro, '/carro/estado', lambda m: self.estados.append((time.monotonic(), m)), 10)

    def mandar(self, vel, dir_, segundos, hz=20):
        fin = time.monotonic() + segundos
        while time.monotonic() < fin:
            m = ComandoCarro()
            m.vel, m.dir = vel, dir_
            self.pub.publish(m)
            rclpy.spin_once(self, timeout_sec=1.0 / hz)

    def esperar(self, segundos):
        fin = time.monotonic() + segundos
        while time.monotonic() < fin:
            rclpy.spin_once(self, timeout_sec=0.02)


resultados = []


def check(nombre, ok, detalle=''):
    resultados.append(ok)
    print(f"[{'OK ' if ok else 'FALLA'}] {nombre} {detalle}", flush=True)


def main():
    esp = ESP32Falso()
    esp.abrir()
    rclpy.init()
    t = Probador()
    bridge = subprocess.Popen(
        [EXE, '--ros-args', '-p', f'puerto:={SIM}',
         '-p', 'puerto_respaldo:=/dev/no_existe', '-p', 'reintento:=1.0'],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    log = []
    threading.Thread(target=lambda: [log.append(l.rstrip()) for l in bridge.stdout], daemon=True).start()

    try:
        # 1) abre, saluda y recibe telemetria
        t.esperar(4.0)
        check('el puente mando P y el ESP32 falso lo recibio', esp.pings >= 1, f'(pings={esp.pings})')
        ult = t.estados[-1][1] if t.estados else None
        check('llega /carro/estado con conectado=true', bool(ult and ult.conectado), f'(mensajes={len(t.estados)})')
        if ult:
            check('telemetria bien leida: b11800, e0, ok', ult.bateria_mv == 11800 and ult.estado == 0 and ult.estado_texto == 'ok',
                  f'(mv={ult.bateria_mv} estado={ult.estado} texto={ult.estado_texto})')

        # 2) sin comandos desde el inicio: debe mandar V0,S0
        antes = len(esp.recibido)
        t.esperar(0.6)
        nuevos = [l for _, l in esp.recibido[antes:]]
        check('sin /carro/cmd manda solo V0,S0', nuevos and all(l == 'V0,S0' for l in nuevos), f'({len(nuevos)} lineas)')

        # 3) con comandos: V25,S-40 a ~20 Hz
        i0 = len(esp.recibido)
        t.mandar(25, -40, 1.0)
        reales = [l for _, l in esp.recibido[i0:]]
        n25 = sum(1 for l in reales if l == 'V25,S-40')
        check('con /carro/cmd manda V25,S-40', n25 >= 15, f'({n25} de {len(reales)} lineas en 1 s)')
        ts = [ts_ for ts_, l in esp.recibido[i0:] if l == 'V25,S-40']
        if len(ts) > 3:
            gaps = [b - a for a, b in zip(ts, ts[1:])]
            check('periodo entre comandos ~50 ms', 0.03 < sum(gaps) / len(gaps) < 0.08, f'(promedio {1000 * sum(gaps) / len(gaps):.0f} ms, max {1000 * max(gaps):.0f} ms)')

        # 4) cortar el publicador: V0,S0 antes de ~400 ms
        t_corte = time.monotonic()
        t.esperar(1.0)
        ceros = [ts_ for ts_, l in esp.recibido if ts_ > t_corte and l == 'V0,S0']
        demora = (ceros[0] - t_corte) if ceros else None
        check('al cortar el publicador manda V0,S0 en <= 450 ms', demora is not None and demora <= 0.45,
              f'(demora {1000 * demora:.0f} ms)' if demora is not None else '(nunca)')
        tras = [l for ts_, l in esp.recibido if ts_ > t_corte]
        ult_cmd = tras[-1] if tras else '(nada)'
        check('y se queda en V0,S0', ult_cmd == 'V0,S0', f'(ultima linea {ult_cmd})')

        # 5) estados 1 y 3
        for e, texto in ((1, 'paro de emergencia'), (3, 'bateria baja'), (2, 'timeout')):
            esp.estado = e
            t.esperar(0.5)
            u = t.estados[-1][1]
            check(f'estado {e} -> "{texto}"', u.estado == e and u.estado_texto == texto and u.conectado, f'(texto={u.estado_texto})')
        esp.estado = 0
        esp.mv = 0
        t.esperar(0.5)
        check('b0 (solo USB) llega como bateria_mv=0, no como error', t.estados[-1][1].bateria_mv == 0 and t.estados[-1][1].conectado)
        esp.mv = 11800

        # 6) desenchufar el USB
        esp.desconectar()
        t.esperar(2.0)
        check('al desconectar publica conectado=false', not t.estados[-1][1].conectado, f'(estado={t.estados[-1][1].estado})')

        # 7) reconectar
        esp2 = ESP32Falso()
        esp2.abrir()
        t.esperar(4.5)
        check('reconecta solo y vuelve conectado=true', t.estados[-1][1].conectado)
        check('en la reconexion tambien manda P', esp2.pings >= 1, f'(pings={esp2.pings})')

        # 8) apagar el puente con Ctrl+C: V0,S0 al final
        t.mandar(30, 10, 0.5)
        n_antes = len(esp2.recibido)
        bridge.send_signal(signal.SIGINT)
        bridge.wait(timeout=6)
        time.sleep(0.2)
        finales = [l for _, l in esp2.recibido[n_antes:]]
        check('al cerrar el puente manda V0,S0 (x3)', finales.count('V0,S0') >= 3, f'(ultimas: {finales[-4:]})')
        check('el puente termino limpio (codigo 0)', bridge.returncode == 0, f'(codigo {bridge.returncode})')
    finally:
        if bridge.poll() is None:
            bridge.kill()
        print('\n--- log del puente ---')
        for l in log[-25:]:
            print(l)
        try:
            t.destroy_node()
            rclpy.shutdown()
        except Exception:
            pass
        if os.path.islink(SIM):
            os.remove(SIM)
    print(f"\n{sum(resultados)} de {len(resultados)} comprobaciones OK")
    sys.exit(0 if all(resultados) else 1)


if __name__ == '__main__':
    main()
