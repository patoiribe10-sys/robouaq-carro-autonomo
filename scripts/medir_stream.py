#!/usr/bin/env python3
"""medir_stream.py: peso y ritmo de los JPEG que transmite camaras_node (cam0), con el stack real en ROS_DOMAIN_ID=77.

Levanta el launch SIN abrir el ESP32 (puerto:=/dev/no_existe), lee /cam0.mjpg durante --seg segundos, descarta
los primeros 5 s (el letrero "ABRIENDO" y el calentamiento) y reporta tamaño de JPEG, fps y resolucion.
Uso: source /opt/ros/humble/setup.bash && source ~/robocar_ws/install/setup.bash
     python3 scripts/medir_stream.py [--seg 15] [--guardar /ruta/cuadro.jpg]
"""
import argparse, os, signal, statistics, subprocess, sys, time, urllib.request
os.environ['ROS_DOMAIN_ID'] = os.environ.get('ROS_DOMAIN_ID_PRUEBAS', '77')
import numpy as np
import cv2


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--seg', type=float, default=15.0)
    ap.add_argument('--guardar', default='')
    a = ap.parse_args()
    p = subprocess.Popen(['ros2', 'launch', 'robocar', 'control_remoto_launch.py', 'puerto:=/dev/no_existe'],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True,
                         preexec_fn=lambda: (signal.signal(signal.SIGINT, signal.SIG_DFL), signal.signal(signal.SIGTERM, signal.SIG_DFL)))
    frames = []                                   # (t, bytes)
    try:
        for _ in range(60):                       # esperar a que la web responda
            try:
                urllib.request.urlopen('http://localhost:8080/estado.json', timeout=1).read(); break
            except Exception:
                time.sleep(0.5)
        t0 = time.monotonic(); buf = b''
        r = urllib.request.urlopen('http://localhost:8080/cam0.mjpg', timeout=10)
        while time.monotonic() - t0 < a.seg:
            buf += r.read(16384)
            while True:
                i = buf.find(b'\xff\xd8'); j = buf.find(b'\xff\xd9', i + 2) if i >= 0 else -1
                if i < 0 or j < 0: break
                frames.append((time.monotonic() - t0, buf[i:j + 2])); buf = buf[j + 2:]
        r.close()
    finally:
        p.send_signal(signal.SIGINT)
        try: p.wait(timeout=15)
        except subprocess.TimeoutExpired: p.kill()
    util = [(t, f) for t, f in frames if t >= 5.0]
    if len(util) < 10: sys.exit(f'Muy pocos cuadros utiles ({len(util)}): la camara no abrio.')
    kb = [len(f) / 1024 for _, f in util]
    dur = util[-1][0] - util[0][0]
    im = cv2.imdecode(np.frombuffer(util[-1][1], np.uint8), cv2.IMREAD_COLOR)
    g = cv2.cvtColor(im, cv2.COLOR_BGR2GRAY)
    print(f'resolucion del cuadro: {im.shape[1]}x{im.shape[0]}   nitidez (Laplaciano): {cv2.Laplacian(g, cv2.CV_64F).var():.0f}')
    print(f'{len(util)} cuadros en {dur:.1f} s = {(len(util) - 1) / dur:.1f} fps')
    print(f'peso de cada JPEG: media {statistics.mean(kb):.1f} KB   mediana {statistics.median(kb):.1f}   min {min(kb):.1f}   max {max(kb):.1f}')
    print(f'ancho de banda del video: {statistics.mean(kb) * 8 * (len(util) - 1) / dur / 1000:.2f} Mbit/s')
    if a.guardar:
        open(a.guardar, 'wb').write(util[-1][1]); print('cuadro guardado en', a.guardar)


if __name__ == '__main__':
    main(); sys.stdout.flush(); os._exit(0)
