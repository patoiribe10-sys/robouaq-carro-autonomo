#!/usr/bin/env python3
"""prueba_camaras_tolerancia.py: prueba la tolerancia a fallos de camaras_node SIN tocar nvargus.

Usa procesos hijos FALSOS (no abren ninguna camara) para comprobar la logica de Camara:
  * sin /dev/videoN no se lanza ningun proceso (no se llama a nvargus) y se detecta cuando aparece
  * la espera tras una caida crece 2, 4, 8, 16, 30, 30 s y vuelve a empezar tras estar estable
  * una camara que falla no afecta a la otra
  * el cuadro de "sin senal" es negro y decodifica

Uso:
    source /opt/ros/humble/setup.bash && source ~/robocar_ws/install/setup.bash
    python3 scripts/prueba_camaras_tolerancia.py
"""
import os
import sys
import tempfile
import time

import cv2
import numpy as np

from robocar.camaras_node import Camara, siguiente_espera

FAKE = r'''
import struct, sys, time
modo = sys.argv[1]
if modo == "falla":
    sys.exit(2)
dato = bytes.fromhex(sys.argv[2])
t0 = time.time()
while True:
    sys.stdout.buffer.write(struct.pack(">I", len(dato)) + dato); sys.stdout.buffer.flush()
    time.sleep(0.05)
    if modo == "cae" and time.time() - t0 > 3.0:
        sys.exit(4)
'''
_ok, _jpg = cv2.imencode('.jpg', np.full((48, 64, 3), 90, np.uint8))
JPEG_HEX = _jpg.tobytes().hex()

resultados = []


def check(nombre, ok, detalle=''):
    resultados.append(ok)
    print(f"[{'OK ' if ok else 'FALLA'}] {nombre} {detalle}", flush=True)


class Log:
    def __init__(self):
        self.lineas = []

    def info(self, m, **k):
        self.lineas.append(('INFO', m))

    def warn(self, m, **k):
        self.lineas.append(('WARN', m))


def camara(nombre, modo, dispositivo, ancho=64, alto=48, **kw):
    cmd = lambda: [sys.executable, '-c', FAKE, modo, JPEG_HEX]
    log = Log()
    c = Camara(nombre, 0, ancho, alto, 30, 15, 70, log, dispositivo=dispositivo, cmd_worker=cmd, **kw)
    return c, log


def esperar(cond, seg, paso=0.05):
    fin = time.monotonic() + seg
    while time.monotonic() < fin:
        if cond():
            return True
        time.sleep(paso)
    return cond()


def main():
    tmp = tempfile.mkdtemp()
    dev = os.path.join(tmp, 'video1')                  # al principio NO existe
    dev_ok = os.path.join(tmp, 'video0')
    open(dev_ok, 'w').close()

    # 0) la secuencia de espera con los valores reales
    e, seq = 2.0, []
    for _ in range(6):
        seq.append(e)
        e = siguiente_espera(e, 30.0)
    check('la espera crece 2, 4, 8, 16, 30, 30 s', seq == [2.0, 4.0, 8.0, 16.0, 30.0, 30.0], f'({seq})')

    # 1) dispositivo ausente: con espectadores NO se lanza nada
    c, log = camara('cam1', 'ok', dev, ancho=640, alto=480, espera_disp_s=1.0, backoff_ini=0.5, backoff_max=4.0, estable_s=2.0)
    c.revisar_dispositivo()
    c.iniciar()
    c.agregar_espectador()
    time.sleep(3.2)
    i = c.info()
    check('sin /dev/videoN no se lanza ningun proceso (no se toca nvargus)', c.lanzamientos == 0,
          f'(lanzamientos={c.lanzamientos}, estado={i["estado"]})')
    check('el estado es no_detectada y existe=False', i['estado'] == 'no_detectada' and i['existe'] is False)
    check('el log no se llena de errores repetidos', len([l for l in log.lineas if l[0] == 'WARN']) == 0,
          f'({len(log.lineas)} lineas)')
    ph = c.placeholder()
    img = cv2.imdecode(np.frombuffer(ph, np.uint8), cv2.IMREAD_COLOR)
    check('el cuadro sin senal es 640x480, decodifica y es casi negro',
          img is not None and img.shape == (480, 640, 3) and img.mean() < 20, f'(media {None if img is None else round(float(img.mean()), 1)})')

    # 2) aparece el dispositivo: se detecta en <= espera_disp_s y empieza a transmitir
    t0 = time.monotonic()
    open(dev, 'w').close()
    ok = esperar(lambda: c.info()['estado'] == 'ok', 4.0)
    check('al aparecer /dev/videoN se detecta y se abre', ok and c.lanzamientos >= 1,
          f'(tardo {time.monotonic() - t0:.1f} s, espera_disp_s=1)')
    check('se registro la aparicion en el log', any('apareció' in m for _, m in log.lineas))
    c.parar()

    # 3) nadie la ve: aunque exista, no se abre
    c3, _ = camara('cam3', 'ok', dev_ok, espera_disp_s=1.0)
    c3.revisar_dispositivo()
    c3.iniciar()
    time.sleep(1.5)
    check('sin espectadores no se abre nada aunque exista el dispositivo', c3.lanzamientos == 0 and c3.info()['estado'] == 'dormida')
    c3.parar()

    # 4) espera creciente cuando falla siempre al abrir
    c4, log4 = camara('cam4', 'falla', dev_ok, espera_disp_s=1.0, backoff_ini=0.5, backoff_max=4.0, estable_s=2.0)
    tiempos = []
    orig = c4._lanzar

    def lanzar_registrando():
        tiempos.append(time.monotonic())
        return orig()
    c4._lanzar = lanzar_registrando
    c4.revisar_dispositivo()
    c4.iniciar()
    c4.agregar_espectador()
    time.sleep(10.5)
    esp = [round(b - a, 1) for a, b in zip(tiempos, tiempos[1:])]
    esperado = [0.5, 1.0, 2.0, 4.0, 4.0]
    cercano = len(esp) >= 4 and all(abs(x - y) <= 0.8 for x, y in zip(esp, esperado))
    check('la espera entre intentos crece (0.5, 1, 2, 4, 4 con la escala de prueba)', cercano, f'(medido {esp})')
    check('mientras espera, el nodo no se traba y reporta el motivo', c4.info()['estado'] in ('espera', 'abriendo') and
          ('no se pudo abrir' in c4.info()['motivo'] or c4.info()['estado'] == 'abriendo'), f'({c4.info()["motivo"]})')
    c4.parar()

    # 5) independencia: A falla siempre, B transmite
    a, _ = camara('camA', 'falla', dev_ok, espera_disp_s=1.0, backoff_ini=0.5, backoff_max=4.0)
    b, _ = camara('camB', 'ok', dev_ok, espera_disp_s=1.0, backoff_ini=0.5, backoff_max=4.0)
    for c_ in (a, b):
        c_.revisar_dispositivo(); c_.iniciar(); c_.agregar_espectador()
    esperar(lambda: b.info()['estado'] == 'ok', 3.0)
    n0 = b.num_cuadro
    time.sleep(4.0)
    n1 = b.num_cuadro
    check('camB sigue transmitiendo mientras camA falla una y otra vez', n1 - n0 > 40 and b.info()['estado'] == 'ok' and
          a.info()['estado'] in ('espera', 'abriendo'), f'(camB +{n1 - n0} cuadros en 4 s; camA {a.info()["estado"]}, lanzamientos A={a.lanzamientos})')
    a.parar(); b.parar()

    # 6) tras estar estable, la espera vuelve a empezar (no sigue creciendo)
    c6, _ = camara('cam6', 'cae', dev_ok, espera_disp_s=1.0, backoff_ini=0.5, backoff_max=4.0, estable_s=2.0)
    ts = []
    o6 = c6._lanzar

    def reg6():
        ts.append(time.monotonic())
        return o6()
    c6._lanzar = reg6
    c6.revisar_dispositivo(); c6.iniciar(); c6.agregar_espectador()
    time.sleep(12.0)
    huecos = [round(y - x, 1) for x, y in zip(ts, ts[1:])]          # = 3 s transmitiendo + espera
    esperas = [round(h - 3.05, 1) for h in huecos]
    check('si estuvo estable, la espera NO crece (queda en ~0.5 s)', len(esperas) >= 2 and max(esperas) <= 1.2,
          f'(huecos {huecos})')
    c6.parar()

    print(f"\n{sum(resultados)} de {len(resultados)} comprobaciones OK")
    sys.exit(0 if all(resultados) else 1)


if __name__ == '__main__':
    main()
