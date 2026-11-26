#!/usr/bin/env python3
"""prueba_cierre_puente.py: estresa el CIERRE de serial_bridge_node contra un ESP32 falso.

Arranca el puente (ejecutable directo), lo deja hablar con el ESP32 falso unos segundos (la telemetria llega
a 20 Hz, asi que el hilo del puerto esta publicando casi todo el tiempo) y le manda SIGINT en un instante
distinto cada vez. Repite N veces y comprueba que SIEMPRE:
  * el puente se despide con V0,S0 (al menos 3 veces al final),
  * termina con codigo 0 y sin Traceback.
Corre en ROS_DOMAIN_ID=77 (aislado del real).

Uso:  source /opt/ros/humble/setup.bash && source ~/robocar_ws/install/setup.bash
      python3 scripts/prueba_cierre_puente.py [N=25]
"""
import importlib.util
import os
import random
import signal
import subprocess
import sys
import time

os.environ['ROS_DOMAIN_ID'] = os.environ.get('ROS_DOMAIN_ID_PRUEBAS', '77')
AQUI = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location('pps', os.path.join(AQUI, 'prueba_puente_simulado.py'))
pps = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pps)

EXE = os.path.expanduser('~/robocar_ws/install/robocar/lib/robocar/serial_bridge_node')


def una(i):
    esp = pps.ESP32Falso()
    esp.abrir()
    p = subprocess.Popen([EXE, '--ros-args', '-p', f'puerto:={pps.SIM}', '-p', 'puerto_respaldo:=/dev/no_existe'],
                         stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, start_new_session=True)
    time.sleep(2.0 + random.random() * 0.4)               # conecta, saluda y empieza la telemetria
    n0 = len(esp.recibido)
    p.send_signal(signal.SIGINT)
    # una segunda senal poco despues, como hace ros2 launch
    time.sleep(random.random() * 0.15)
    try:
        p.send_signal(signal.SIGINT)
    except Exception:
        pass
    try:
        salida, _ = p.communicate(timeout=15)
        rc = p.returncode
    except subprocess.TimeoutExpired:
        p.kill()
        salida, _ = p.communicate()
        rc = 'TIMEOUT'
    time.sleep(0.2)
    finales = [l for _, l in esp.recibido[n0:] if l.startswith('V')]
    despide = finales.count('V0,S0') >= 3
    tb = 'Traceback' in salida
    esp.desconectar()
    return despide, rc, tb, salida


def main():
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 25
    random.seed(7)
    malas = 0
    for i in range(1, n + 1):
        despide, rc, tb, salida = una(i)
        ok = despide and rc == 0 and not tb
        if not ok:
            malas += 1
            print(f'[FALLA] corrida {i}: se despidio={despide} codigo={rc} traceback={tb}')
            if tb:
                print('        ' + '\n        '.join(salida.strip().splitlines()[-6:]))
    print(f'\n{n - malas} de {n} cierres limpios (con despedida V0,S0 x3, codigo 0 y sin Traceback)')
    sys.exit(0 if malas == 0 else 1)


if __name__ == '__main__':
    main()
