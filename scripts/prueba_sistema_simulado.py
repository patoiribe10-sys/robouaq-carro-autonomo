#!/usr/bin/env python3
"""prueba_sistema_simulado.py: integracion de TODO el sistema sin el carro y sin tocar el ESP32 real.

Arranca el launch real (control_remoto_launch.py) con:
  * puerto:=<pseudo-terminal>  -> un ESP32 FALSO (el mismo de prueba_puente_simulado.py)
  * joy:=false                 -> este script inyecta un /joy falso (igual que un control, mapeo estandar)
  * camaras:=true              -> camaras_node REAL (cam0 abre de verdad; cam1 sale en negro si no esta)
y comprueba el camino completo: /joy -> teleop -> /carro/cmd -> puente -> "V<vel>,S<dir>" al ESP32 falso,
y de regreso telemetria -> /carro/estado -> http://localhost:8080/estado.json (lo que ve la pagina).
Tambien prueba: hombre muerto, turbo, reversa, perder /joy, MORIR el teleop, desenchufar y volver a
enchufar el USB, la web con las dos camaras y el cierre con Ctrl+C. Mide con tegrastats.

Uso:
    source /opt/ros/humble/setup.bash && source ~/robocar_ws/install/setup.bash
    python3 scripts/prueba_sistema_simulado.py                 # con camaras
    python3 scripts/prueba_sistema_simulado.py --sin-camaras
"""
import argparse
import importlib.util
import json
import os
import signal
import subprocess
import sys
import threading
import time
import urllib.request

# Las pruebas SIMULADAS corren en OTRO dominio ROS (77), aislado del real (0): asi sus /joy y /carro/cmd falsos
# no se mezclan con el control real ni con otra prueba corriendo al mismo tiempo (ROS_LOCALHOST_ONLY es 0).
os.environ['ROS_DOMAIN_ID'] = os.environ.get('ROS_DOMAIN_ID_PRUEBAS', '77')

import rclpy
from rclpy.executors import SingleThreadedExecutor
from rclpy.node import Node
from sensor_msgs.msg import Joy

AQUI = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location('pps', os.path.join(AQUI, 'prueba_puente_simulado.py'))
pps = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pps)          # reutiliza ESP32Falso y la ruta SIM del pseudo-terminal

resultados = []


def check(nombre, ok, detalle=''):
    resultados.append(ok)
    print(f"[{'OK ' if ok else 'FALLA'}] {nombre} {detalle}", flush=True)


def http(ruta, timeout=4):
    return urllib.request.urlopen('http://127.0.0.1:8080' + ruta, timeout=timeout).read()


def estado():
    return json.loads(http('/estado.json'))


def esperar(cond, seg, paso=0.05):
    fin = time.monotonic() + seg
    while time.monotonic() < fin:
        try:
            if cond():
                return True
        except Exception:
            pass
        time.sleep(paso)
    try:
        return bool(cond())
    except Exception:
        return False


class Joyero(Node):
    """Publica /joy a 20 Hz como lo haria game_controller_node con el mapa estandar."""

    def __init__(self):
        super().__init__('joyero_falso')
        self.pub = self.create_publisher(Joy, '/joy', 10)
        self.ejes = [0.0] * 6          # 0 LX, 1 LY (arriba +1), 2 RX (izquierda +1, derecha -1), 3 RY, 4 LT, 5 RT
        self.botones = [0] * 21        # 9 = LB, 10 = RB
        self.activo = True
        threading.Thread(target=self._bucle, daemon=True).start()

    def _bucle(self):
        while True:
            if self.activo:
                m = Joy()
                m.axes = [float(x) for x in self.ejes]
                m.buttons = list(self.botones)
                try:
                    self.pub.publish(m)
                except Exception:
                    return
            time.sleep(0.05)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--sin-camaras', action='store_true')
    a = ap.parse_args()
    con_cam = not a.sin_camaras

    esp = pps.ESP32Falso()
    esp.abrir()
    log_path = os.path.join(os.path.dirname(pps.SIM), 'sistema_simulado.log')
    logf = open(log_path, 'w')
    launch = subprocess.Popen(
        ['ros2', 'launch', 'robocar', 'control_remoto_launch.py', f'puerto:={pps.SIM}', 'joy:=false',
         f'camaras:={"true" if con_cam else "false"}'],
        stdout=logf, stderr=subprocess.STDOUT, start_new_session=True)

    rclpy.init()
    joy = Joyero()
    ej = SingleThreadedExecutor()
    ej.add_node(joy)
    hilo = threading.Thread(target=ej.spin)
    hilo.start()
    pid_nvargus0 = subprocess.run(['systemctl', 'show', 'nvargus-daemon', '-p', 'MainPID', '--value'],
                                  capture_output=True, text=True).stdout.strip()

    def ultimo():
        v = [l for _, l in esp.recibido if l.startswith('V')]
        return v[-1] if v else None

    def cmd_en(texto, seg):
        return esperar(lambda: ultimo() == texto, seg)

    try:
        # ---------------------------------------------------------------- arranque
        ok = esperar(lambda: esp.pings >= 1 and (not con_cam or estado()['conectado']), 30)
        check('el sistema levanta: el puente saluda con P y la web ve conectado=true', ok,
              f'(pings={esp.pings})')
        if not ok:
            raise RuntimeError('no levanto')
        e = estado() if con_cam else {}
        if con_cam:
            check('la web reporta bateria 11.8 V y estado ok', e['bateria_mv'] == 11800 and e['estado'] == 0 and e['estado_texto'] == 'ok',
                  f"({e['bateria_mv']} mV, estado {e['estado']})")
            check('cam1 sin detectar (negro, no se lanza nada) y cam0 dormida hasta que alguien la vea', e['cam1']['estado'] == 'no_detectada' and e['cam0']['estado'] == 'dormida',
                  f"(cam0={e['cam0']['estado']}, cam1={e['cam1']['estado']})")
        check('sin /joy el carro recibe solo V0,S0', ultimo() in (None, 'V0,S0') and not any(l not in ('V0,S0',) for _, l in esp.recibido if l.startswith('V')))

        # ---------------------------------------------------------------- conduccion
        joy.botones[9] = 1                                  # LB: hombre muerto
        joy.ejes[1] = 1.0                                   # stick izquierdo arriba
        check('LB + stick arriba -> V30,S0', cmd_en('V30,S0', 2.0), f'(ultimo {ultimo()})')
        joy.ejes[2] = -1.0                                  # stick derecho a la DERECHA (en /joy es -1)
        check('stick derecho a la DERECHA -> S+100 (servo a 1730 us)', cmd_en('V30,S100', 2.0), f'(ultimo {ultimo()})')
        if con_cam:
            check('la web ve esa direccion de vuelta (telemetria s100)', esperar(lambda: estado()['dir'] == 100, 2.0), f"(dir {estado()['dir']})")
        joy.ejes[2] = 1.0                                   # IZQUIERDA
        check('stick derecho a la IZQUIERDA -> S-100 (servo a 1190 us)', cmd_en('V30,S-100', 2.0), f'(ultimo {ultimo()})')
        joy.ejes[2] = 0.0
        joy.botones[10] = 1                                 # RB turbo
        check('RB -> turbo V60', cmd_en('V60,S0', 2.5), f'(ultimo {ultimo()})')
        joy.botones[10] = 0
        joy.ejes[1] = -1.0
        check('stick abajo -> reversa V-20', cmd_en('V-20,S0', 2.5), f'(ultimo {ultimo()})')
        joy.ejes[1] = 1.0
        cmd_en('V30,S0', 2.0)

        # soltar el hombre muerto
        t0 = time.monotonic()
        joy.botones[9] = 0
        ok = cmd_en('V0,S0', 2.0)
        check('soltar LB frena y centra rapido', ok and time.monotonic() - t0 < 0.8, f'({time.monotonic() - t0:.2f} s)')

        # perder /joy en movimiento (control apagado o desconectado)
        joy.botones[9] = 1
        cmd_en('V30,S0', 2.0)
        t0 = time.monotonic()
        joy.activo = False
        ok = cmd_en('V0,S0', 3.0)
        check('si deja de llegar /joy, el carro frena en < 1 s', ok and time.monotonic() - t0 < 1.0, f'({time.monotonic() - t0:.2f} s)')
        joy.activo = True

        # MORIR el teleop en movimiento: el puente debe parar solo
        cmd_en('V30,S0', 2.0)
        pid_teleop = subprocess.run(['pgrep', '-f', r'^/usr/bin/python3 .*teleop_xbox_node'], capture_output=True, text=True).stdout.split()
        check('se encuentra el proceso del teleop', len(pid_teleop) >= 1, f'({pid_teleop})')
        if pid_teleop:
            t0 = time.monotonic()
            os.kill(int(pid_teleop[0]), signal.SIGKILL)
            ok = cmd_en('V0,S0', 3.0)
            check('si MUERE el teleop, el puente manda V0,S0 solo (timeout de 300 ms)', ok and time.monotonic() - t0 < 0.8,
                  f'({time.monotonic() - t0:.2f} s)')
        joy.botones[9] = 0
        joy.ejes[1] = 0.0

        # ---------------------------------------------------------------- telemetria -> web
        if con_cam:
            esp.estado = 1
            check('paro de emergencia llega a la web', esperar(lambda: estado()['estado'] == 1 and estado()['estado_texto'] == 'paro de emergencia', 2.0))
            esp.estado, esp.mv = 3, 10200
            check('bateria baja 10.2 V llega a la web', esperar(lambda: estado()['estado'] == 3 and estado()['bateria_mv'] == 10200, 2.0))
            esp.estado, esp.mv = 0, 0
            check('solo USB (b0) llega como bateria 0, no como error', esperar(lambda: estado()['conectado'] and estado()['bateria_mv'] == 0 and estado()['estado'] == 0, 2.0))
            esp.mv = 11800
            esperar(lambda: estado()['bateria_mv'] == 11800, 2.0)

            # ----------------------------------------------------- desenchufar / enchufar el USB
            esp.desconectar()
            check('USB desenchufado: la web marca SIN DATOS', esperar(lambda: not estado()['conectado'], 4.0))
            esp2 = pps.ESP32Falso()
            esp2.abrir()
            check('USB enchufado de nuevo: reconecta solo', esperar(lambda: estado()['conectado'], 8.0))
            esp = esp2

            # ----------------------------------------------------- web y camaras con el stack en marcha
            print('\n-- 15 s con las dos paginas abiertas y el carro "manejando" (tegrastats en paralelo) --', flush=True)
            tg = []
            def medir():
                r = subprocess.run(['tegrastats', '--interval', '1000'], capture_output=True, text=True, timeout=None) if False else None
            proc_tg = subprocess.Popen(['tegrastats', '--interval', '1000'], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
            threading.Thread(target=lambda: [tg.append(l) for l in proc_tg.stdout], daemon=True).start()
            frames = {'cam0': 0, 'cam1': 0}
            def leer(nombre):
                try:
                    r = urllib.request.urlopen(f'http://127.0.0.1:8080/{nombre}.mjpg', timeout=10)
                    fin = time.monotonic() + 15
                    while time.monotonic() < fin:
                        d = r.read(65536)
                        if not d:
                            break
                        frames[nombre] += d.count(b'--frame')
                except Exception:
                    pass
            hs = [threading.Thread(target=leer, args=(n,)) for n in ('cam0', 'cam1')]
            for h in hs: h.start()
            joy.botones[9] = 1; joy.ejes[1] = 1.0; joy.ejes[2] = -0.5
            time.sleep(8)
            e = estado()
            check('con la pagina abierta cam0 abre y transmite ~15 fps', e['cam0']['estado'] == 'ok' and e['cam0']['fps'] >= 10, f"({e['cam0']['estado']}, {e['cam0']['fps']} fps)")
            check('cam1 sigue en negro, sin tumbar a cam0', e['cam1']['estado'] == 'no_detectada' and e['cam0']['estado'] == 'ok')
            for h in hs: h.join()
            joy.botones[9] = 0; joy.ejes[1] = 0.0; joy.ejes[2] = 0.0
            proc_tg.terminate()
            check('cam0 mando muchos cuadros y cam1 el cuadro negro de "sin senal"', frames['cam0'] > 100 and frames['cam1'] >= 8, f"(cam0 {frames['cam0']} cuadros, cam1 {frames['cam1']})")
            cpu, vdd, tj, n = [], [], [], 0
            import re
            for l in tg:
                m = re.search(r'CPU \[([^\]]*)\]', l)
                if m:
                    vals = [int(x.split('%')[0]) for x in m.group(1).split(',')]
                    cpu.append(sum(vals) / len(vals))
                m = re.search(r'VDD_IN (\d+)mW', l); vdd.append(int(m.group(1))) if m else None
                m = re.search(r'tj@([\d.]+)C', l); tj.append(float(m.group(1))) if m else None
            if cpu:
                print(f"   tegrastats con el stack completo ({len(cpu)} muestras): CPU {sum(cpu) / len(cpu):.0f} % | VDD_IN {sum(vdd) / len(vdd):.0f} mW | tj {sum(tj) / len(tj):.1f} C (max {max(tj):.1f})")
            pid_nvargus1 = subprocess.run(['systemctl', 'show', 'nvargus-daemon', '-p', 'MainPID', '--value'], capture_output=True, text=True).stdout.strip()
            check('nvargus-daemon con el mismo PID', pid_nvargus0 == pid_nvargus1, f'({pid_nvargus0} -> {pid_nvargus1})')

    except RuntimeError as ex:
        print('abortado:', ex)
    finally:
        # ---------------------------------------------------------------- cierre con Ctrl+C
        print('\n-- cierre con Ctrl+C (SIGINT al grupo del launch) --', flush=True)
        n_antes = len(esp.recibido)
        t0 = time.monotonic()
        try:
            os.killpg(launch.pid, signal.SIGINT)
            launch.wait(timeout=20)
            cerro = True
        except Exception:
            cerro = False
            try:
                os.killpg(launch.pid, signal.SIGKILL)
            except Exception:
                pass
        dt = time.monotonic() - t0
        joy.activo = False
        ej.shutdown()
        hilo.join(timeout=5)
        joy.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()
        # Tras el Ctrl+C los nodos terminan su cierre ordenado (camaras_node mata el proceso de captura, que puede
        # estar en su pausa de 3 s): se espera hasta 10 s a que desaparezcan y se reporta cuanto tardaron.
        def contar_vivos():
            # pgrep ANCLADO al inicio del proceso: solo cuenta 'python3 .../<nodo>', no el shell que lanzo esta prueba
            # (un 'ps | grep' por nombre se contaba a si mismo cuando el comando contenia esos nombres).
            r = subprocess.run(['pgrep', '-fc', r'^/usr/bin/python3 [^ ]*/(serial_bridge_node|teleop_xbox_node|camaras_node|camara_worker)(\.py)?( |$)'],
                               capture_output=True, text=True)
            return int((r.stdout.strip() or '0').split()[0])
        t_vivos = time.monotonic()
        while contar_vivos() > 0 and time.monotonic() - t_vivos < 10.0:
            time.sleep(0.1)
        vivos = str(contar_vivos())
        tardaron = time.monotonic() - t_vivos
        if vivos != '0':
            quienes = subprocess.run(['pgrep', '-fa', r'^/usr/bin/python3 [^ ]*/(serial_bridge_node|teleop_xbox_node|camaras_node|camara_worker)(\.py)?( |$)'],
                                     capture_output=True, text=True).stdout
            print('   procesos que siguen vivos:')
            for l in quienes.splitlines():
                print('     ', l[:170])
        check('el launch cierra limpio con Ctrl+C', cerro, f'({dt:.1f} s)')
        check('no queda ningun nodo ni proceso de camara vivo (se espera hasta 10 s)', vivos == '0', f'({vivos} vivos; tardaron {tardaron:.1f} s tras salir el launch)')
        finales = [l for _, l in esp.recibido[n_antes:] if l.startswith('V')]
        check('el puente se despide con V0,S0', finales.count('V0,S0') >= 3, f'(ultimas {finales[-3:]})')
        logf.close()
        texto = open(log_path).read()
        esperados = ('Se perdio el puerto serial', 'exit code -9')      # los provoca esta misma prueba a proposito
        malos = [l for l in texto.splitlines() if ('Traceback' in l or '[ERROR]' in l) and 'Argus' not in l
                 and not any(x in l for x in esperados)]
        check('el log del launch no tiene Traceback ni ERROR', not malos, f'({len(malos)} lineas)')
        for l in malos[:6]:
            print('     ', l[:160])
        esp.desconectar()
        print(f'\n(log completo del launch: {log_path})')
        print(f"{sum(resultados)} de {len(resultados)} comprobaciones OK")
        sys.exit(0 if all(resultados) else 1)


if __name__ == '__main__':
    main()
