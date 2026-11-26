#!/usr/bin/env python3
"""diagnostico_direccion.py: MIDE que eje de /joy sigue al stick derecho horizontal y con que signo.

No supone nada: compara, mientras mueves el stick, el valor CRUDO del kernel (evdev, ABS_Z) contra
cada eje de /joy (game_controller_node con el SDL propio) y calcula la correlacion de cada uno.
Con eso decide eje_dir y signo_dir de control_remoto.yaml para que
        derecha = dir +100 = S+100 = servo a 1680 us   (convencion del firmware carro-v2)

Uso (en una terminal de verdad, control prendido, ningun launch abierto):
    source /opt/ros/humble/setup.bash && source ~/robocar_ws/install/setup.bash
    python3 ~/robocar_ws/scripts/diagnostico_direccion.py              # mide y propone
    python3 ~/robocar_ws/scripts/diagnostico_direccion.py --aplicar    # ademas escribe eje_dir y signo_dir en el yaml

Durante la medicion: mantén LB apretado (como en la prueba que fallo) y mueve el stick DERECHO de un
lado al otro, lento y a fondo, varias veces. No toques nada mas.
"""
import argparse
import os
import re
import select
import signal
import subprocess
import sys
import threading
import time

import numpy as np

try:
    import evdev
    from evdev import ecodes
except ImportError:
    sys.exit('Falta evdev (sudo apt install python3-evdev).')

RAIZ = os.path.expanduser('~/robocar_ws')
YAML_FUENTE = os.path.join(RAIZ, 'src', 'robocar', 'config', 'control_remoto.yaml')
SALIDA = os.path.join(RAIZ, 'config', 'diagnostico_direccion.yaml')
EJES_JOY = ['LX', 'LY', 'RX', 'RY', 'LT', 'RT']


def analizar(z, ejes, boton_lb=None):
    """z: serie evdev ABS_Z (crudo, 0..65535) muestreada en cada mensaje de /joy.
    ejes: matriz (n, k) con los k ejes de /joy en esos mismos instantes.
    Regresa un dict con el diagnostico."""
    z = np.asarray(z, dtype=float)
    ejes = np.asarray(ejes, dtype=float)
    res = {'n': int(len(z)), 'rango_z': [float(z.min()), float(z.max())], 'ejes': [], 'veredicto': None}
    if len(z) < 20 or (z.max() - z.min()) < 0.5 * 65535:
        res['veredicto'] = 'sin_movimiento'
        return res
    zn = (z - z.mean()) / (z.std() or 1.0)
    for i in range(ejes.shape[1]):
        e = ejes[:, i]
        rango = float(e.max() - e.min())
        corr = 0.0
        if e.std() > 1e-6:
            corr = float(np.corrcoef(zn, (e - e.mean()) / e.std())[0, 1])
        res['ejes'].append({'eje': i, 'nombre': EJES_JOY[i] if i < len(EJES_JOY) else str(i),
                            'min': round(float(e.min()), 2), 'max': round(float(e.max()), 2),
                            'rango': round(rango, 2), 'corr': round(corr, 3)})
    candidatos = [e for e in res['ejes'] if e['rango'] >= 0.5]
    if not candidatos:
        res['veredicto'] = 'joy_no_se_movio'       # el kernel vio moverse el stick, pero /joy no
        return res
    mejor = max(candidatos, key=lambda e: abs(e['corr']))
    if abs(mejor['corr']) < 0.8:
        res['veredicto'] = 'ambiguo'
        return res
    res['veredicto'] = 'ok'
    res['eje_dir'] = mejor['eje']
    # derecha = ABS_Z sube. Si el eje sube con ABS_Z (corr > 0), derecha = +1 y signo_dir = +1;
    # si baja (corr < 0), derecha = -1 y signo_dir = -1. Asi derecha -> dir = +100 = S+ = 1680 us.
    res['signo_dir'] = 1.0 if mejor['corr'] > 0 else -1.0
    if boton_lb is not None:
        res['lb_apretado_pct'] = round(100.0 * float(np.mean(boton_lb)), 1)
    return res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--seg', type=float, default=10.0)
    ap.add_argument('--aplicar', action='store_true', help='escribe eje_dir y signo_dir en el yaml fuente')
    a = ap.parse_args()

    import rclpy
    from ament_index_python.packages import get_package_share_directory
    from rclpy.executors import SingleThreadedExecutor
    from rclpy.node import Node
    from sensor_msgs.msg import Joy

    dev = next((evdev.InputDevice(r) for r in evdev.list_devices()
                if 'Xbox' in evdev.InputDevice(r).name or 'Microsoft' in evdev.InputDevice(r).name), None)
    if dev is None:
        sys.exit('No encontré el control (¿prendido y conectado por Bluetooth?).')

    # --- estado compartido
    lock = threading.Lock()
    estado = {'z': dev.absinfo(ecodes.ABS_Z).value, 'lb': 0}
    muestras = []                     # (t, z, [ejes], lb_joy)
    parar = threading.Event()

    def hilo_evdev():
        while not parar.is_set():
            r, _, _ = select.select([dev.fd], [], [], 0.1)
            if not r:
                continue
            try:
                for ev in dev.read():
                    if ev.type == ecodes.EV_ABS and ev.code == ecodes.ABS_Z:
                        with lock:
                            estado['z'] = ev.value
                    elif ev.type == ecodes.EV_KEY and ev.code == ecodes.BTN_TL:
                        with lock:
                            estado['lb'] = ev.value
            except (BlockingIOError, OSError):
                continue

    rclpy.init()
    nodo = Node('diagnostico_direccion')
    capturando = {'si': False}

    def on_joy(m):
        if not capturando['si']:
            return
        with lock:
            z = estado['z']
        muestras.append((time.monotonic(), z, list(m.axes), m.buttons[9] if len(m.buttons) > 9 else 0))

    nodo.create_subscription(Joy, '/joy', on_joy, 10)
    ejecutor = SingleThreadedExecutor()
    ejecutor.add_node(nodo)
    hilo_ros = threading.Thread(target=ejecutor.spin, name='ejecutor')
    hilo_ros.start()
    time.sleep(0.8)

    proc = None

    def cerrar():
        """Orden: subproceso -> hilo evdev -> executor -> hilo ros -> nodo -> rclpy."""
        if proc is not None:
            try:
                os.killpg(proc.pid, signal.SIGINT)
                proc.wait(timeout=5)
            except Exception:
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except Exception:
                    pass
        parar.set()
        ejecutor.shutdown()
        hilo_ros.join(timeout=5.0)
        try:
            nodo.destroy_node()
        except Exception:
            pass
        if rclpy.ok():
            rclpy.shutdown()
        try:
            dev.close()
        except Exception:
            pass

    try:
        if nodo.count_publishers('/joy') > 0:
            print('!! Ya hay otro publicador en /joy (¿un launch o un game_controller_node abierto?). Ciérralo y repite.')
            return 1
        compartido = get_package_share_directory('robocar')
        yaml_cfg = os.path.join(compartido, 'config', 'control_remoto.yaml')
        sdl = os.path.join(compartido, 'config', 'sdl_gamecontroller.txt')
        env = dict(os.environ)
        if os.path.exists(sdl):
            env['SDL_GAMECONTROLLERCONFIG'] = open(sdl).read().strip()
            print('SDL propio:', sdl)
        else:
            print('(sin sdl_gamecontroller.txt: mapeo de fábrica)')
        proc = subprocess.Popen(['/opt/ros/humble/lib/joy/game_controller_node', '--ros-args', '--params-file', yaml_cfg],
                                env=env, start_new_session=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        threading.Thread(target=hilo_evdev, daemon=True).start()
        time.sleep(2.5)

        print(f'\nMantén LB APRETADO y mueve el stick DERECHO de un lado al otro, lento y a fondo, durante {a.seg:.0f} s.')
        input('ENTER para empezar (y empieza a mover): ')
        capturando['si'] = True
        t0 = time.monotonic()
        while time.monotonic() - t0 < a.seg:
            time.sleep(0.5)
            with lock:
                z = estado['z']
            barra = int(z / 65535 * 40)
            print(f"\r  ABS_Z (kernel) {z:5d} [{'#' * barra:<40}]  mensajes /joy: {len(muestras):4d}", end='', flush=True)
        capturando['si'] = False
        print('\n  listo, suelta.')
    except (KeyboardInterrupt, EOFError):
        print('\n(interrumpido)')
    finally:
        cerrar()

    if not muestras:
        print('No llegó nada de /joy.')
        return 1
    z = [m[1] for m in muestras]
    ejes = [m[2] for m in muestras]
    lb = [m[3] for m in muestras]
    r = analizar(z, ejes, lb)

    print(f"\n=== RESULTADO ({r['n']} muestras; ABS_Z del kernel recorrió {r['rango_z'][0]:.0f}..{r['rango_z'][1]:.0f}) ===")
    for e in r['ejes']:
        marca = '  <-- sigue al stick' if (r.get('eje_dir') == e['eje']) else ''
        print(f"  eje {e['eje']} ({e['nombre']:2s}): rango {e['min']:+.2f}..{e['max']:+.2f}   correlación con el stick {e['corr']:+.2f}{marca}")
    v = r['veredicto']
    if v == 'sin_movimiento':
        print('\nEl kernel no vio moverse el stick derecho horizontal (ABS_Z casi no cambió). Muévelo a fondo de lado a lado y repite.')
    elif v == 'joy_no_se_movio':
        print('\nEl kernel SÍ vio moverse el stick, pero NINGÚN eje de /joy se movió: el problema está antes de /joy (SDL o el mapa). '
              'Con el mapa propio, rightx:a2 debería seguirlo. Pégame esta salida.')
    elif v == 'ambiguo':
        print('\nNingún eje sigue claramente al stick (|correlación| < 0.8). Repite moviendo solo ese stick. Pégame esta salida.')
    else:
        print(f"\nCONCLUSIÓN MEDIDA: el stick derecho horizontal es el eje {r['eje_dir']} de /joy, "
              f"y al ir a la DERECHA ese eje {'SUBE' if r['signo_dir'] > 0 else 'BAJA'}.")
        print(f"  => eje_dir: {r['eje_dir']}   signo_dir: {r['signo_dir']}   (derecha => dir +100 => S+100 => servo 1680 us)")
        if 'lb_apretado_pct' in r:
            print(f"  LB (boton 9) estuvo apretado el {r['lb_apretado_pct']} % del tiempo de la medición"
                  + ('' if r['lb_apretado_pct'] > 80 else '  <-- si es bajo, el hombre muerto se soltó y por eso dir salía 0'))
        if a.aplicar:
            s = open(YAML_FUENTE).read()
            s2 = re.sub(r'(\n\s*eje_dir:\s*)-?\d+', lambda m: f"{m.group(1)}{r['eje_dir']}", s)
            s2 = re.sub(r'(\n\s*signo_dir:\s*)-?\d+(\.\d+)?', lambda m: f"{m.group(1)}{r['signo_dir']}", s2)
            open(YAML_FUENTE, 'w').write(s2)
            print(f'  Escribí eje_dir y signo_dir en {YAML_FUENTE}')
        else:
            print('  (no cambié nada; usa --aplicar para escribirlo en el yaml)')

    try:
        import yaml
        os.makedirs(os.path.dirname(SALIDA), exist_ok=True)
        yaml.safe_dump(r, open(SALIDA, 'w'), allow_unicode=True, sort_keys=False)
        print(f'  (guardado en {SALIDA})')
    except Exception:
        pass
    return 0 if v == 'ok' else 1


if __name__ == '__main__':
    codigo = main()
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(int(codigo or 0))     # sin destructores de C++ (evita 'terminate called...' al salir)
