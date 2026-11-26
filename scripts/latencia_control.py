#!/usr/bin/env python3
"""latencia_control.py: tramo CONTROL -> /joy de la latencia, con el control real y la mano de Pato.

Levanta el stack en ROS_DOMAIN_ID=77 SIN abrir el ESP32 (puerto:=/dev/no_existe: el servo no se mueve) y compara,
para cada movimiento brusco del stick derecho (eje evdev ABS_Z):
    t_evdev : sello de tiempo del kernel del evento (CLOCK_REALTIME, de python-evdev)
    t_joy   : hora (time.time()) en que /joy trae ese movimiento (|eje 2| > 0.5)
Tambien mide la frecuencia REAL de /joy (del primer al ultimo mensaje: no cuenta el arranque del descubrimiento de DDS,
que hacia parecer 8-13 Hz) y el intervalo entre eventos evdev mientras el stick se mueve (el ritmo del propio control).
Uso: source /opt/ros/humble/setup.bash && source ~/robocar_ws/install/setup.bash
     python3 scripts/latencia_control.py --etiqueta A --seg 30      (web cerrada)
     python3 scripts/latencia_control.py --etiqueta B --seg 30      (web abierta en la laptop con cam0)
Durante --seg: mantén LB y mueve el stick DERECHO a fondo de un lado al otro (~1 por segundo).
"""
import argparse, os, select, signal, statistics, subprocess, sys, threading, time
os.environ['ROS_DOMAIN_ID'] = os.environ.get('ROS_DOMAIN_ID_PRUEBAS', '77')
import evdev
from evdev import ecodes
import rclpy
from sensor_msgs.msg import Joy


def stats(x, u='ms'):
    x = sorted(x)
    return f'n={len(x)}  mediana {statistics.median(x):.1f}  media {statistics.mean(x):.1f}  p90 {x[int(.9 * len(x))]:.1f}  max {x[-1]:.1f} {u}'


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--seg', type=float, default=30.0)
    ap.add_argument('--etiqueta', default='')
    ap.add_argument('--adjuntar', action='store_true', help='no lanza el stack: se adjunta al que ya esta corriendo (dominio 77)')
    a = ap.parse_args()
    dev = next((evdev.InputDevice(r) for r in evdev.list_devices()
                if 'Xbox' in evdev.InputDevice(r).name or 'Microsoft' in evdev.InputDevice(r).name), None)
    if dev is None:
        sys.exit('No encontre el control.')
    info = dev.absinfo(ecodes.ABS_Z)
    centro = (info.min + info.max) / 2.0; mitad = (info.max - info.min) / 2.0
    ev_abs, joy = [], []            # (t_wall, valor_normalizado) ; (t_wall, eje2)
    parar = threading.Event()
    medir = threading.Event()

    def hilo_evdev():
        while not parar.is_set():
            r, _, _ = select.select([dev.fd], [], [], 0.1)
            if not r: continue
            try:
                for e in dev.read():
                    if e.type == ecodes.EV_ABS and e.code == ecodes.ABS_Z and medir.is_set():
                        ev_abs.append((e.timestamp(), (e.value - centro) / mitad))
            except (BlockingIOError, OSError):
                pass
    threading.Thread(target=hilo_evdev, daemon=True).start()

    p = None
    if not a.adjuntar:
        p = subprocess.Popen(['ros2', 'launch', 'robocar', 'control_remoto_launch.py', 'puerto:=/dev/no_existe'],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True,
                             preexec_fn=lambda: (signal.signal(signal.SIGINT, signal.SIG_DFL), signal.signal(signal.SIGTERM, signal.SIG_DFL)))
    rclpy.init(); nodo = rclpy.create_node('latencia_control')
    nodo.create_subscription(Joy, '/joy', lambda m: joy.append((time.time(), m.axes[2] if len(m.axes) > 2 else 0.0, m.buttons[9] if len(m.buttons) > 9 else 0)), 200)
    try:
        t = time.monotonic()
        while time.monotonic() - t < 25 and len(joy) < 5:        # esperar a que /joy llegue (descubrimiento DDS)
            rclpy.spin_once(nodo, timeout_sec=0.05)
        if len(joy) < 5: sys.exit('/joy no llega: ¿control apagado?')
        time.sleep(0.5)
        print(f'[{a.etiqueta}] MIDIENDO {a.seg:.0f} s: LB apretado y stick DERECHO a fondo de lado a lado', flush=True)
        medir.set(); n0 = len(joy)
        fin = time.monotonic() + a.seg
        while time.monotonic() < fin: rclpy.spin_once(nodo, timeout_sec=0.01)
        medir.clear()
        mj = joy[n0:]
    finally:
        parar.set()
        if p is not None:
            p.send_signal(signal.SIGINT)
            try: p.wait(timeout=15)
            except subprocess.TimeoutExpired: p.kill()
        nodo.destroy_node(); rclpy.shutdown()

    print(f'\n=== RESULTADO [{a.etiqueta}] ===')
    ts = [t for t, _, _ in mj]
    g = [(b - a_) * 1000 for a_, b in zip(ts, ts[1:])]
    print(f'/joy: {len(mj)} mensajes en {ts[-1] - ts[0]:.1f} s = {(len(mj) - 1) / (ts[-1] - ts[0]):.1f} Hz reales; intervalo: {stats(g)}')
    print(f'      LB apretado el {100 * sum(1 for _, _, lb in mj if lb) / len(mj):.0f} % del tiempo')
    mov = [(t, v) for t, v in ev_abs]
    gi = [(b[0] - a_[0]) * 1000 for a_, b in zip(mov, mov[1:]) if (b[0] - a_[0]) < 0.3]
    print(f'evdev (ABS_Z): {len(mov)} eventos; intervalo entre eventos mientras se mueve: {stats(gi) if gi else "sin datos"}')
    # flancos: el stick pasa de |x|<0.2 a |x|>0.5
    lat = []; estado = 'reposo'
    for t, v in mov:
        if estado == 'reposo' and abs(v) > 0.5:
            estado = 'activo'
            tj = next((tj for tj, e, _ in mj if tj >= t - 0.005 and abs(e) > 0.5), None)
            if tj: lat.append((tj - t) * 1000)
        elif estado == 'activo' and abs(v) < 0.2:
            estado = 'reposo'
    print(f'evdev -> /joy (cruce de |0.5|): {stats(lat) if lat else "no detecte movimientos bruscos (¿moviste el stick DERECHO a fondo?)"}')

if __name__ == '__main__':
    main()
    sys.stdout.flush(); os._exit(0)
