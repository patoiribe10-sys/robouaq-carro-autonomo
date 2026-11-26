#!/usr/bin/env python3
"""latencia_ros.py: tramos ROS de la latencia, con el stack REAL (puente al ESP32 real) en ROS_DOMAIN_ID=77.

Lanza control_remoto_launch.py con joy:=false camaras:=false, inyecta /joy como lo haria game_controller_node
(20 Hz de autorepeat + un mensaje inmediato en cada cambio) y mide, por cada cambio brusco del stick derecho:
    T1  /joy publicado      -> /carro/cmd con la direccion nueva      (teleop)
    T2  /carro/cmd          -> 1a telemetria con s distinto            (puente + serial + firmware + 20 Hz)
    T3  /joy publicado      -> 1a telemetria con s distinto            (total, empieza a moverse)
    T4  /joy publicado      -> telemetria en el valor final            (total, incluye la rampa del servo)
Solo mueve la direccion (a fondo (+-100 %)): LB apretado pero stick de velocidad en 0, el motor no recibe nada.
Uso: source /opt/ros/humble/setup.bash && source ~/robocar_ws/install/setup.bash && python3 scripts/latencia_ros.py [N=30]
"""
import os, random, signal, statistics, subprocess, sys, time
os.environ['ROS_DOMAIN_ID'] = os.environ.get('ROS_DOMAIN_ID_PRUEBAS', '77')
import rclpy
from sensor_msgs.msg import Joy
from robocar_interfaces.msg import ComandoCarro, EstadoCarro

def stats(x):
    x = sorted(x); return f'mediana {statistics.median(x):5.0f}  media {statistics.mean(x):5.0f}  p90 {x[int(.9*len(x))]:5.0f}  max {x[-1]:5.0f} ms'

def main():
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 30
    p = subprocess.Popen(['ros2', 'launch', 'robocar', 'control_remoto_launch.py', 'joy:=false', 'camaras:=false'],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True,
                         preexec_fn=lambda: (signal.signal(signal.SIGINT, signal.SIG_DFL), signal.signal(signal.SIGTERM, signal.SIG_DFL)))
    rclpy.init(); nodo = rclpy.create_node('latencia_ros')
    pub = nodo.create_publisher(Joy, '/joy', 10)
    cmds, ests = [], []
    nodo.create_subscription(ComandoCarro, '/carro/cmd', lambda m: cmds.append((time.monotonic(), m.dir, m.vel)), 100)
    nodo.create_subscription(EstadoCarro, '/carro/estado', lambda m: ests.append((time.monotonic(), m.dir, m.conectado)), 100)
    axes = [0.0] * 6; botones = [0] * 21; botones[9] = 1          # LB apretado
    def mandar():
        m = Joy(); m.axes = list(axes); m.buttons = list(botones); pub.publish(m)
    def girar(seg, cada=0.05):
        fin = time.monotonic() + seg; sig = time.monotonic()
        while time.monotonic() < fin:
            if time.monotonic() >= sig: mandar(); sig += cada
            rclpy.spin_once(nodo, timeout_sec=0.001)
    try:
        t = time.monotonic()
        while time.monotonic() - t < 25 and not (ests and ests[-1][2] and time.monotonic() - ests[-1][0] < 0.5):
            girar(0.2)
        if not (ests and ests[-1][2]): sys.exit('El puente no conecto con el ESP32 (puerto mudo?).')
        girar(1.0)
        T1, T2, T3, T4, objetivo = [], [], [], [], 0
        for k in range(n):
            girar(0.7 + random.random() * 0.5)
            nuevo = 100 if objetivo == 0 else 0
            previo = ests[-1][1]
            axes[2] = -nuevo / 100.0                                # signo_dir -1: stick a la derecha (-) -> dir +
            n_c, n_e = len(cmds), len(ests)
            t0 = time.monotonic(); mandar()                         # mensaje inmediato, como joy al cambiar el stick
            sig = t0 + 0.05; tc = te1 = te2 = None
            while time.monotonic() - t0 < 1.5 and te2 is None:
                if time.monotonic() >= sig: mandar(); sig += 0.05
                rclpy.spin_once(nodo, timeout_sec=0.001)
                if tc is None:
                    for t, d, v in cmds[n_c:]:
                        if abs(d - nuevo) < 1: tc = t; break
                for t, d, c in ests[n_e:]:
                    if te1 is None and d != previo: te1 = t
                    if te2 is None and abs(d - nuevo) < 1: te2 = t
            objetivo = nuevo
            if tc and te1 and te2:
                T1.append((tc - t0) * 1000); T2.append((te1 - tc) * 1000); T3.append((te1 - t0) * 1000); T4.append((te2 - t0) * 1000)
        girar(0.3)
        vel_max = max((abs(v) for _, _, v in cmds), default=0)
        print(f'{len(T1)} de {n} cambios medidos; |vel| maximo mandada durante la prueba: {vel_max:.0f} (debe ser 0)')
        print(f'T1 /joy -> /carro/cmd                    : {stats(T1)}')
        print(f'T2 /carro/cmd -> 1a telemetria distinta  : {stats(T2)}')
        print(f'T3 /joy -> empieza a moverse (telemetria): {stats(T3)}')
        print(f'T4 /joy -> valor final (con rampa servo) : {stats(T4)}')
    finally:
        axes[2] = 0.0
        try: girar(0.4)
        except Exception: pass
        p.send_signal(signal.SIGINT)
        try: p.wait(timeout=15)
        except subprocess.TimeoutExpired: p.kill()
        nodo.destroy_node(); rclpy.shutdown()

if __name__ == '__main__':
    main()
