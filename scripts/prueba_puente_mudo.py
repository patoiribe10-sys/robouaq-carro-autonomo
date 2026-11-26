#!/usr/bin/env python3
"""prueba_puente_mudo.py: el puente debe reabrir el puerto si abre "mudo" (sin telemetria).

El 26-nov el CH340 de la Jetson abrio mudo en ~1 de cada 2 aperturas. Aqui un ESP32 falso se queda mudo los
primeros 7 s; el puente (reabrir_sin_telemetria_s = 2) debe cerrar y reabrir, y al volver la telemetria
publicar conectado = true. Corre en ROS_DOMAIN_ID=77.
"""
import importlib.util, os, signal, subprocess, sys, time
os.environ['ROS_DOMAIN_ID'] = os.environ.get('ROS_DOMAIN_ID_PRUEBAS', '77')
AQUI = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location('pps', os.path.join(AQUI, 'prueba_puente_simulado.py'))
pps = importlib.util.module_from_spec(spec); spec.loader.exec_module(pps)
import rclpy
EXE = os.path.expanduser('~/robocar_ws/install/robocar/lib/robocar/serial_bridge_node')

esp = pps.ESP32Falso(); esp.abrir(); esp.mudo = True
rclpy.init(); sonda = pps.Probador()
p = subprocess.Popen([EXE, '--ros-args', '-p', f'puerto:={pps.SIM}', '-p', 'puerto_respaldo:=/dev/no_existe',
                      '-p', 'reabrir_sin_telemetria_s:=2.0'], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                     start_new_session=True)
t0 = time.monotonic(); desmudado = False; conectado_t = None
while time.monotonic() - t0 < 25.0:
    sonda.esperar(0.1)
    if not desmudado and time.monotonic() - t0 > 7.0:
        esp.mudo = False; desmudado = True
    if conectado_t is None and any(m.conectado and t > t0 + 7.0 for t, m in sonda.estados):
        conectado_t = time.monotonic() - t0
        break
antes = [m for t, m in sonda.estados if t < t0 + 6.5]
p.send_signal(signal.SIGINT)
try: salida, _ = p.communicate(timeout=15)
except subprocess.TimeoutExpired: p.kill(); salida, _ = p.communicate()
esp.desconectar(); sonda.destroy_node(); rclpy.shutdown()
pps.check('mientras esta mudo publica conectado=false', bool(antes) and not any(m.conectado for m in antes), f'({len(antes)} mensajes)')
pps.check('avisa que cierra y reabre el puerto', 'lo cierro y lo reabro' in salida)
pps.check('al volver la telemetria publica conectado=true', conectado_t is not None, f'({conectado_t:.1f} s)' if conectado_t else '')
pps.check('sin Traceback', 'Traceback' not in salida)
ok = sum(pps.resultados); print(f'\n{ok} de {len(pps.resultados)} comprobaciones OK'); sys.exit(0 if ok == len(pps.resultados) else 1)
