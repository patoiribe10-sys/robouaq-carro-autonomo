#!/usr/bin/env python3
"""prueba_estres_camara.py: ¿se queda sin memoria el nvargus-daemon al abrir la camara?

Abre y cierra cam0 N veces con gst-launch (5 cuadros cada vez) mientras un proceso auxiliar ocupa
memoria hasta dejar poca RAM libre. En cada ciclo registra los MINIMOS de MemFree, CmaFree y
MemAvailable, si la camara abrio, si el nvargus-daemon se cayo y si el daemon registro errores de
memoria. Sirve para comparar ANTES y DESPUES de cambiar vm.min_free_kbytes (fase F3) y para saber
si lo que se agota es la RAM normal o la CMA.

Uso:
    python3 scripts/prueba_estres_camara.py --etiqueta "F3 antes"
    python3 scripts/prueba_estres_camara.py --etiqueta "F3 despues"
    python3 scripts/prueba_estres_camara.py --ciclos 2 --sin-estres --etiqueta prueba   # control, sin ocupar memoria

Puede tumbar el nvargus-daemon unos segundos (systemd lo reinicia solo). Cierra antes cualquier
nodo de camara (camaras_node) y el navegador. Seguridad: el auxiliar nunca baja MemAvailable de
--min-disponible-mb y tiene oom_score_adj 1000 (si hay OOM, mata primero al auxiliar).
Salida: ~/robocar_ws/mediciones_estres_camara.tsv
"""
import argparse
import os
import subprocess
import sys
import threading
import time

TSV = os.path.expanduser('~/robocar_ws/mediciones_estres_camara.tsv')
GST = ['gst-launch-1.0', '-e', 'nvarguscamerasrc', 'sensor-id=0', 'num-buffers=5', '!',
       'video/x-raw(memory:NVMM),width=640,height=480,format=NV12,framerate=30/1', '!',
       'nvvidconv', '!', 'video/x-raw,format=BGRx', '!', 'fakesink']

HOG = r'''
import sys, time
objetivo_libre = int(sys.argv[1]) * 1024      # kB de MemFree que se quiere dejar
min_disp = int(sys.argv[2]) * 1024            # kB de MemAvailable que NUNCA se rebasa
try:
    open('/proc/self/oom_score_adj', 'w').write('1000')
except OSError:
    pass
def mem():
    d = {}
    for l in open('/proc/meminfo'):
        k, v = l.split(':'); d[k] = int(v.split()[0])
    return d
CH = 32 * 1024 * 1024
bloques = []
def ocupar():
    b = bytearray(CH)
    for i in range(0, CH, 4096):
        b[i] = 1
    bloques.append(b)
while True:
    m = mem()
    if m['MemFree'] <= objetivo_libre or m['MemAvailable'] <= min_disp:
        break
    ocupar()
m = mem()
print('listo', m['MemFree'] // 1024, m['MemAvailable'] // 1024, len(bloques) * 32, flush=True)
while True:                                    # mantener la presion si el kernel libera cache
    time.sleep(0.5)
    m = mem()
    if m['MemFree'] > objetivo_libre and m['MemAvailable'] > min_disp:
        ocupar()
'''


def meminfo():
    d = {}
    with open('/proc/meminfo') as f:
        for l in f:
            k, v = l.split(':')
            d[k] = int(v.split()[0])
    return d


class Muestreo(threading.Thread):
    """Guarda los minimos de memoria mientras corre un ciclo."""

    def __init__(self):
        super().__init__(daemon=True)
        self.parar = False
        self.min_free = self.min_cma = self.min_disp = 10 ** 9

    def run(self):
        while not self.parar:
            m = meminfo()
            self.min_free = min(self.min_free, m['MemFree'] // 1024)
            self.min_cma = min(self.min_cma, m['CmaFree'] // 1024)
            self.min_disp = min(self.min_disp, m['MemAvailable'] // 1024)
            time.sleep(0.05)


def pid_daemon():
    r = subprocess.run(['systemctl', 'show', 'nvargus-daemon', '-p', 'MainPID', '--value'],
                       capture_output=True, text=True)
    return r.stdout.strip()


def errores_daemon(desde):
    r = subprocess.run(['journalctl', '-u', 'nvargus-daemon', '--since', desde, '--no-pager', '-q'],
                       capture_output=True, text=True)
    t = r.stdout
    return sum(t.count(x) for x in ('InsufficientMemory', 'error 12', 'double free'))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--ciclos', type=int, default=40)
    ap.add_argument('--libre-mb', type=int, default=150, help='MemFree objetivo con el estres')
    ap.add_argument('--min-disponible-mb', type=int, default=700, help='MemAvailable que nunca se rebasa')
    ap.add_argument('--etiqueta', default='sin etiqueta')
    ap.add_argument('--sin-estres', action='store_true', help='control: no ocupa memoria')
    a = ap.parse_args()

    # patron anclado al inicio del proceso: si no, pgrep -f coincide con el shell que lanzo este script
    if subprocess.run(['pgrep', '-f', r'^/usr/bin/python3 .*(camaras_node|camara_worker)'],
                      capture_output=True).stdout.strip():
        print('Hay un camaras_node o camara_worker corriendo; cierralo antes de la prueba.')
        sys.exit(1)

    hog = None
    filas, fallos_seguidos = [], 0
    try:
        m0 = meminfo()
        print(f"Inicio: MemFree {m0['MemFree'] // 1024} MB, MemAvailable {m0['MemAvailable'] // 1024} MB, "
              f"CmaFree {m0['CmaFree'] // 1024} MB")
        if not a.sin_estres:
            hog = subprocess.Popen([sys.executable, '-c', HOG, str(a.libre_mb), str(a.min_disponible_mb)],
                                   stdout=subprocess.PIPE, text=True)
            linea = hog.stdout.readline().split()
            print(f'Estres listo: MemFree {linea[1]} MB, MemAvailable {linea[2]} MB, auxiliar ocupa {linea[3]} MB')

        for c in range(1, a.ciclos + 1):
            desde = time.strftime('%Y-%m-%d %H:%M:%S')
            pid0 = pid_daemon()
            mu = Muestreo(); mu.start()
            t0 = time.time()
            try:
                r = subprocess.run(GST, capture_output=True, text=True, timeout=30)
                salida, rc = r.stdout + r.stderr, r.returncode
            except subprocess.TimeoutExpired:
                salida, rc = 'TIMEOUT', -1
            dt = time.time() - t0
            mu.parar = True; mu.join(1)
            ok = rc == 0 and 'Got EOS' in salida
            pid1 = pid_daemon()
            err = errores_daemon(desde)
            motivo = '' if ok else ('timeout' if salida == 'TIMEOUT' else
                                    'CaptureSession' if 'Failed to create CaptureSession' in salida else
                                    'InsufficientMemory' if 'InsufficientMemory' in salida else f'rc={rc}')
            fila = (a.etiqueta, c, int(ok), rc, f'{dt:.1f}', mu.min_free, mu.min_cma, mu.min_disp,
                    int(pid0 != pid1), err, motivo)
            filas.append(fila)
            print(f"ciclo {c:2d}: {'OK ' if ok else 'FALLA'} {dt:4.1f}s  MemFree min {mu.min_free:5d} MB  "
                  f"CmaFree min {mu.min_cma:4d} MB  daemon {'REINICIADO' if pid0 != pid1 else 'estable'}"
                  f"{'  errores_daemon=' + str(err) if err else ''}{'  ' + motivo if motivo else ''}", flush=True)
            fallos_seguidos = 0 if ok else fallos_seguidos + 1
            if fallos_seguidos >= 5:
                print('5 fallos seguidos: paro la prueba para no seguir golpeando al daemon.')
                break
            if meminfo()['MemAvailable'] // 1024 < 400:
                print('MemAvailable < 400 MB: paro por seguridad.')
                break
            time.sleep(1.5)
    finally:
        if hog is not None:
            hog.terminate()
            try:
                hog.wait(5)
            except subprocess.TimeoutExpired:
                hog.kill()

    if not filas:
        return
    nuevo = not os.path.exists(TSV)
    with open(TSV, 'a') as f:
        if nuevo:
            f.write('etiqueta\tciclo\tok\trc\ttiempo_s\tMemFree_min_MB\tCmaFree_min_MB\tMemAvail_min_MB\t'
                    'daemon_reiniciado\terrores_daemon\tmotivo\n')
        for fila in filas:
            f.write('\t'.join(str(x) for x in fila) + '\n')

    n, ok = len(filas), sum(f[2] for f in filas)
    malos = [f for f in filas if not f[2]]
    print(f'\n=== {a.etiqueta}: {ok} de {n} ciclos OK, {n - ok} fallos, '
          f'{sum(f[8] for f in filas)} reinicios del daemon ===')
    print(f'Minimos en toda la prueba: MemFree {min(f[5] for f in filas)} MB, CmaFree {min(f[6] for f in filas)} MB, '
          f'MemAvailable {min(f[7] for f in filas)} MB')
    if malos:
        print('Durante los FALLOS: MemFree min', [f[5] for f in malos], '| CmaFree min', [f[6] for f in malos])
        oks = [f for f in filas if f[2]]
        if oks:
            print('Durante los OK:     MemFree min', min(f[5] for f in oks), '| CmaFree min', min(f[6] for f in oks))
    print(f'(guardado en {TSV})')


if __name__ == '__main__':
    main()
