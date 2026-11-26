#!/usr/bin/env python3
"""camaras_node: transmite las dos camaras CSI en crudo al navegador (MJPEG) y muestra
el estado del carro en grande.

    http://<ip de la Jetson>:8080/            pagina con las dos camaras y el estado
    http://<ip de la Jetson>:8080/cam0.mjpg   cam0 (ojo de pez, carriles)
    http://<ip de la Jetson>:8080/cam1.mjpg   cam1 (normal, senales)
    http://<ip de la Jetson>:8080/estado.json estado del carro y de las camaras

Reglas:
  * Si nadie esta viendo una camara, NO se abre ni se codifica nada. La camara se abre
    cuando llega el primer espectador y se cierra 3 s despues de que se va el ultimo.
  * Un solo JPEG por cuadro, sin importar cuantas personas lo vean.
  * Si una camara no abre (por ejemplo cam1, que aun no tiene cable CSI), se muestra
    "sin senal" y se reintenta cada 5 s mientras alguien la este viendo.
  * Sin CDN ni librerias externas: solo biblioteca estandar, OpenCV del sistema (con
    GStreamer) y rclpy. NO instalar opencv-python con pip.
  * Si la Jetson se ahoga con las dos camaras, bajar fps_stream a 10 antes de tocar la
    resolucion.
"""
import json
import os
import select
import struct
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import cv2
import numpy as np
import rclpy
from rclpy.node import Node

from robocar_interfaces.msg import EstadoCarro

try:
    from rclpy.executors import ExternalShutdownException
except ImportError:  # pragma: no cover
    class ExternalShutdownException(Exception):
        pass

SCRIPT_CAMARA = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'camara_worker.py')
GRACIA_S = 3.0          # tiempo que sigue abierta la camara despues del ultimo espectador
REINTENTO_S = 5.0       # cada cuanto se reintenta abrir una camara que fallo
ARRANQUE_MAX_S = 25.0   # tiempo maximo para el primer cuadro (abrir + 20 de calentamiento)
SIN_CUADROS_S = 6.0     # una vez andando, si pasan tantos s sin cuadro se reinicia el proceso

MOTIVO_SALIDA = {
    2: 'no abre la camara (revisa el cable CSI o la memoria)',
    3: 'la camara abre pero no entrega cuadros',
    4: 'se perdio la imagen',
}


class Camara:
    """Una camara CSI. La captura corre en un proceso hijo (camara_worker.py) y solo
    mientras hay espectadores."""

    def __init__(self, nombre, sensor_id, ancho, alto, fps_sensor, fps_stream, calidad, log):
        self.nombre = nombre
        self.sensor_id = sensor_id
        self.ancho, self.alto = ancho, alto
        self.fps_sensor = fps_sensor
        self.fps_stream = max(1.0, fps_stream)
        self.calidad = int(calidad)
        self.log = log

        self.cond = threading.Condition()
        self.espectadores = 0
        self.jpeg = None
        self.num_cuadro = 0
        self.abierta = False
        self.error = ''
        self.fps_real = 0.0
        self._ultimo_espectador = 0.0
        self._parar = False
        self._hilo = threading.Thread(target=self._bucle, name=f'cam-{nombre}', daemon=True)

    # ---------------------------------------------------------------- control
    def iniciar(self):
        self._hilo.start()

    def parar(self):
        with self.cond:
            self._parar = True
            self.cond.notify_all()
        self._hilo.join(timeout=8.0)

    @property
    def parando(self):
        return self._parar

    def agregar_espectador(self):
        with self.cond:
            self.espectadores += 1
            self.cond.notify_all()

    def quitar_espectador(self):
        with self.cond:
            self.espectadores = max(0, self.espectadores - 1)
            self._ultimo_espectador = time.monotonic()
            self.cond.notify_all()

    def esperar_cuadro(self, ultimo, timeout):
        """Espera un cuadro nuevo (distinto de 'ultimo'). Regresa (jpeg o None, numero)."""
        with self.cond:
            self.cond.wait_for(lambda: self.num_cuadro != ultimo or self._parar, timeout)
            return self.jpeg, self.num_cuadro

    def info(self):
        with self.cond:
            return {
                'abierta': self.abierta,
                'espectadores': self.espectadores,
                'fps': round(self.fps_real, 1),
                'error': self.error,
            }

    def placeholder(self):
        """JPEG con 'sin senal' / 'abriendo' para cuando no hay imagen."""
        texto = 'abriendo camara...' if not self.error else 'SIN SENAL'
        img = np.full((self.alto, self.ancho, 3), 40, dtype=np.uint8)
        cv2.putText(img, f'{self.nombre}: {texto}', (20, self.alto // 2),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.9, (200, 200, 200), 2, cv2.LINE_AA)
        if self.error:
            cv2.putText(img, self.error[:60], (20, self.alto // 2 + 40),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.6, (150, 150, 150), 1, cv2.LINE_AA)
        ok, jpg = cv2.imencode('.jpg', img, [cv2.IMWRITE_JPEG_QUALITY, 60])
        return jpg.tobytes() if ok else b''

    # ------------------------------------------------------- proceso de captura
    def _lanzar(self):
        cmd = [sys.executable, SCRIPT_CAMARA,
               '--sensor', str(self.sensor_id), '--ancho', str(self.ancho), '--alto', str(self.alto),
               '--fps-sensor', str(int(self.fps_sensor)), '--fps-stream', str(self.fps_stream),
               '--calidad', str(self.calidad)]
        try:
            return subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, bufsize=0)
        except OSError as e:
            self._falla(f'no pude lanzar el proceso de la camara: {e}')
            return None

    def _detener(self, proc):
        """Cierra el proceso hijo con calma (SIGTERM) y, si no obedece, a la fuerza."""
        try:
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=4.0)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=2.0)
        except Exception:
            pass
        try:
            proc.stdout.close()
        except Exception:
            pass

    def _cerrada(self):
        with self.cond:
            self.abierta = False
            self.jpeg = None
            self.fps_real = 0.0
        self.log.info(f'{self.nombre}: nadie la ve, camara cerrada')

    def _falla(self, texto):
        with self.cond:
            self.abierta = False
            self.error = texto
            self.jpeg = None
            self.fps_real = 0.0
            self.num_cuadro += 1
            self.cond.notify_all()
        self.log.warn(f'{self.nombre}: {texto}. Reintento en {REINTENTO_S:.0f} s si alguien la sigue viendo',
                      throttle_duration_sec=30.0)

    @staticmethod
    def _ultimo_jpeg(buf):
        """Saca de 'buf' todos los cuadros completos y regresa el mas reciente (o None)."""
        ultimo = None
        while len(buf) >= 4:
            n = struct.unpack('>I', bytes(buf[:4]))[0]
            if n == 0 or n > 5_000_000:      # dato corrupto: descartar todo
                buf.clear()
                break
            if len(buf) < 4 + n:
                break
            ultimo = bytes(buf[4:4 + n])
            del buf[:4 + n]
        return ultimo

    def _bucle(self):
        proc = None
        buf = bytearray()
        proximo_intento = 0.0
        t_inicio = 0.0
        t_ultimo_cuadro = 0.0
        t_fps = time.monotonic()
        n_fps = 0
        while True:
            with self.cond:
                if self._parar:
                    break
                ver = self.espectadores > 0
                if not ver and proc is None:
                    self.cond.wait(0.5)              # dormida: no abre ni codifica nada
                    continue
                en_gracia = (time.monotonic() - self._ultimo_espectador) < GRACIA_S
            ahora = time.monotonic()

            if proc is not None and not ver and not en_gracia:
                self._detener(proc)
                proc = None
                buf.clear()
                self._cerrada()
                continue

            if proc is None:                          # aqui ver == True
                if ahora < proximo_intento:
                    time.sleep(0.2)
                    continue
                proc = self._lanzar()
                if proc is None:
                    proximo_intento = time.monotonic() + REINTENTO_S
                    continue
                buf.clear()
                t_inicio = time.monotonic()
                t_ultimo_cuadro = 0.0
                t_fps, n_fps = t_inicio, 0
                continue

            # --- leer lo que mande el hijo
            lista, _, _ = select.select([proc.stdout], [], [], 0.2)
            if lista:
                try:
                    datos = os.read(proc.stdout.fileno(), 262144)
                except OSError:
                    datos = b''
                if datos:
                    buf += datos
            jpg = self._ultimo_jpeg(buf)
            if jpg is not None:
                ahora = time.monotonic()
                t_ultimo_cuadro = ahora
                n_fps += 1
                if ahora - t_fps >= 2.0:
                    self.fps_real = n_fps / (ahora - t_fps)
                    t_fps, n_fps = ahora, 0
                primera = False
                with self.cond:
                    if not self.abierta:
                        primera = True
                    self.abierta = True
                    self.error = ''
                    self.jpeg = jpg
                    self.num_cuadro += 1
                    self.cond.notify_all()
                if primera:
                    self.log.info(f'{self.nombre}: camara abierta (sensor {self.sensor_id})')

            # --- ¿murio el hijo?
            codigo = proc.poll()
            if codigo is not None:
                if codigo < 0:
                    motivo = f'el proceso de la camara murio (senal {-codigo})'
                else:
                    motivo = MOTIVO_SALIDA.get(codigo, f'el proceso de la camara termino (codigo {codigo})')
                self._detener(proc)
                proc = None
                buf.clear()
                self._falla(motivo)
                proximo_intento = time.monotonic() + REINTENTO_S
                continue

            # --- ¿se quedo mudo?
            ahora = time.monotonic()
            if t_ultimo_cuadro > 0:
                mudo = ahora - t_ultimo_cuadro > SIN_CUADROS_S
            else:
                mudo = ahora - t_inicio > ARRANQUE_MAX_S
            if mudo:
                self._detener(proc)
                proc = None
                buf.clear()
                self._falla('la camara dejo de mandar cuadros')
                proximo_intento = time.monotonic() + REINTENTO_S

        if proc is not None:
            self._detener(proc)


PAGINA = """<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Stitch - camaras y estado</title>
<style>
  :root { color-scheme: dark; }
  body { margin: 0; background: #111; color: #eee; font-family: system-ui, sans-serif; }
  header { display: flex; gap: 16px; padding: 16px; flex-wrap: wrap; }
  .caja { flex: 1 1 320px; border-radius: 14px; padding: 18px 24px; text-align: center; }
  .caja .etq { font-size: 1.1rem; opacity: .85; letter-spacing: .05em; text-transform: uppercase; }
  .caja .val { font-size: clamp(2.6rem, 8vw, 5.5rem); font-weight: 800; line-height: 1.1; }
  .caja .sub { font-size: 1.1rem; opacity: .85; min-height: 1.4em; }
  .ok    { background: #0d5c2b; }
  .aviso { background: #8a5a00; }
  .malo  { background: #8e1b1b; }
  .gris  { background: #3a3a3a; }
  main { display: grid; grid-template-columns: repeat(auto-fit, minmax(340px, 1fr)); gap: 12px; padding: 0 16px 16px; }
  figure { margin: 0; background: #1b1b1b; border-radius: 10px; overflow: hidden; }
  figcaption { padding: 8px 12px; font-size: .95rem; opacity: .85; }
  img { width: 100%; display: block; background: #000; min-height: 200px; }
  footer { padding: 0 16px 16px; font-size: .9rem; opacity: .7; }
</style>
</head>
<body>
<header>
  <div id="c_estado" class="caja gris">
    <div class="etq">Estado del carro</div>
    <div id="estado" class="val">...</div>
    <div id="estado_sub" class="sub">&nbsp;</div>
  </div>
  <div id="c_bat" class="caja gris">
    <div class="etq">Bateria</div>
    <div id="bat" class="val">...</div>
    <div id="bat_sub" class="sub">&nbsp;</div>
  </div>
</header>
<main>
  <figure><img src="/cam0.mjpg" alt="cam0"><figcaption id="t0">cam0 - ojo de pez (carriles)</figcaption></figure>
  <figure><img src="/cam1.mjpg" alt="cam1"><figcaption id="t1">cam1 - frente (senales)</figcaption></figure>
</main>
<footer id="pie">Las camaras solo se encienden mientras esta pagina esta abierta.</footer>
<script>
const ESTADOS = {
  0: ["TODO BIEN", "ok"],
  1: ["PARO DE EMERGENCIA", "malo"],
  2: ["SIN COMANDOS", "aviso"],
  3: ["BATERIA BAJA", "aviso"]
};
function pinta(id, clase) { document.getElementById(id).className = "caja " + clase; }
function texto(id, t) { document.getElementById(id).textContent = t; }
async function actualiza() {
  try {
    const r = await fetch("/estado.json", {cache: "no-store"});
    const e = await r.json();
    if (!e.conectado) {
      texto("estado", "SIN DATOS"); texto("estado_sub", "el ESP32 no esta mandando telemetria"); pinta("c_estado", "gris");
      texto("bat", "-- V"); texto("bat_sub", ""); pinta("c_bat", "gris");
    } else {
      const d = ESTADOS[e.estado] || ["ESTADO " + e.estado, "aviso"];
      texto("estado", d[0]);
      texto("estado_sub", "vel " + e.pulsos_s + " pulsos/s   dir " + e.dir);
      pinta("c_estado", d[1]);
      if (e.bateria_mv === 0) {
        texto("bat", "solo USB"); texto("bat_sub", "sin bateria conectada"); pinta("c_bat", "gris");
      } else {
        const v = e.bateria_mv / 1000;
        texto("bat", v.toFixed(1) + " V");
        if (v < 9.9)       { texto("bat_sub", "CRITICA: motor apagado"); pinta("c_bat", "malo"); }
        else if (v < 10.5) { texto("bat_sub", "baja: velocidad limitada al 50 %"); pinta("c_bat", "aviso"); }
        else               { texto("bat_sub", "ok"); pinta("c_bat", "ok"); }
      }
    }
    for (const n of [0, 1]) {
      const c = e["cam" + n];
      let t = "cam" + n + (n === 0 ? " - ojo de pez (carriles)" : " - frente (senales)");
      if (c.abierta) t += "  |  " + c.fps + " fps"; else if (c.error) t += "  |  " + c.error;
      texto("t" + n, t);
    }
  } catch (err) {
    texto("estado", "SIN CONEXION"); texto("estado_sub", "la Jetson no responde"); pinta("c_estado", "malo");
    texto("bat", "-- V"); pinta("c_bat", "gris");
  }
}
setInterval(actualiza, 500);
actualiza();
</script>
</body>
</html>
"""


class Manejador(BaseHTTPRequestHandler):
    timeout = 10                      # si un cliente deja de leer, no se queda colgado el hilo

    def log_message(self, formato, *args):
        pass                          # sin ruido en la terminal

    def do_GET(self):
        ruta = self.path.split('?', 1)[0]
        nodo = self.server.nodo
        if ruta in ('/', '/index.html'):
            self._enviar(200, 'text/html; charset=utf-8', PAGINA.encode('utf-8'))
        elif ruta == '/estado.json':
            self._enviar(200, 'application/json', json.dumps(nodo.estado_dict()).encode('utf-8'))
        elif ruta in ('/cam0.mjpg', '/cam1.mjpg'):
            self._stream(nodo.camaras[ruta[1:5]])
        elif ruta == '/favicon.ico':
            self._enviar(204, 'image/x-icon', b'')
        else:
            self._enviar(404, 'text/plain; charset=utf-8', b'No encontrado')

    def _enviar(self, codigo, tipo, cuerpo):
        self.send_response(codigo)
        self.send_header('Content-Type', tipo)
        self.send_header('Content-Length', str(len(cuerpo)))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        if cuerpo:
            self.wfile.write(cuerpo)

    def _stream(self, cam):
        self.send_response(200)
        self.send_header('Content-Type', 'multipart/x-mixed-replace; boundary=frame')
        self.send_header('Cache-Control', 'no-store, private')
        self.send_header('Pragma', 'no-cache')
        self.end_headers()
        cam.agregar_espectador()
        ultimo = -1
        try:
            while not cam.parando:
                jpg, num = cam.esperar_cuadro(ultimo, 1.0)
                ultimo = num
                if jpg is None:
                    jpg = cam.placeholder()       # sin imagen: "sin senal" una vez por segundo
                if not jpg:
                    continue
                self.wfile.write(b'--frame\r\nContent-Type: image/jpeg\r\nContent-Length: '
                                 + str(len(jpg)).encode() + b'\r\n\r\n')
                self.wfile.write(jpg)
                self.wfile.write(b'\r\n')
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, TimeoutError, OSError):
            pass
        finally:
            cam.quitar_espectador()


class CamarasNode(Node):

    def __init__(self):
        super().__init__('camaras_node')
        self.declare_parameter('puerto_http', 8080)
        self.declare_parameter('ancho', 640)
        self.declare_parameter('alto', 480)
        self.declare_parameter('fps_sensor', 30)
        self.declare_parameter('fps_stream', 15.0)        # si se ahoga, bajar a 10.0
        self.declare_parameter('calidad_jpeg', 70)
        self.declare_parameter('sensor_cam0', 0)
        self.declare_parameter('sensor_cam1', 1)
        self.declare_parameter('edad_max_estado', 1.5)    # s: telemetria mas vieja = sin datos

        g = lambda n: self.get_parameter(n).value
        self.edad_max = float(g('edad_max_estado'))
        self._estado = None
        self._t_estado = 0.0
        self._lock = threading.Lock()
        self.create_subscription(EstadoCarro, '/carro/estado', self._on_estado, 10)

        log = self.get_logger()
        comunes = (int(g('ancho')), int(g('alto')), int(g('fps_sensor')),
                   float(g('fps_stream')), int(g('calidad_jpeg')), log)
        self.camaras = {
            'cam0': Camara('cam0', int(g('sensor_cam0')), *comunes),
            'cam1': Camara('cam1', int(g('sensor_cam1')), *comunes),
        }
        for c in self.camaras.values():
            c.iniciar()

        puerto = int(g('puerto_http'))
        self.servidor = ThreadingHTTPServer(('0.0.0.0', puerto), Manejador)
        self.servidor.daemon_threads = True
        self.servidor.nodo = self
        self._hilo_http = threading.Thread(target=self.servidor.serve_forever, name='http', daemon=True)
        self._hilo_http.start()
        log.info(f'Camaras listas en http://0.0.0.0:{puerto}/ (no se abre ninguna hasta que alguien la vea)')

    def _on_estado(self, msg):
        with self._lock:
            self._estado = msg
            self._t_estado = time.monotonic()

    def estado_dict(self):
        with self._lock:
            m, t = self._estado, self._t_estado
        edad = (time.monotonic() - t) if m is not None else None
        fresco = bool(m is not None and m.conectado and edad is not None and edad <= self.edad_max)
        d = {
            'conectado': fresco,
            'estado': int(m.estado) if (fresco and m) else 255,
            'estado_texto': m.estado_texto if (fresco and m) else 'sin datos',
            'bateria_mv': int(m.bateria_mv) if (fresco and m) else 0,
            'pulsos_s': int(m.pulsos_s) if (fresco and m) else 0,
            'dir': int(m.dir) if (fresco and m) else 0,
            'edad_s': round(edad, 2) if edad is not None else None,
        }
        for nombre, cam in self.camaras.items():
            d[nombre] = cam.info()
        return d

    def cerrar(self):
        try:
            self.servidor.shutdown()
            self.servidor.server_close()
        except Exception:
            pass
        for c in self.camaras.values():
            c.parar()


def main(args=None):
    rclpy.init(args=args)
    nodo = CamarasNode()
    try:
        rclpy.spin(nodo)
    except (KeyboardInterrupt, ExternalShutdownException):
        pass
    finally:
        nodo.cerrar()
        nodo.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == '__main__':
    main()
