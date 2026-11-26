#!/usr/bin/env python3
"""latencia_serial.py: tramo SERIAL + FIRMWARE de la latencia (sin ROS, solo direccion, V siempre 0).

Manda V0,S<x> alternando 0 y 60 en instantes aleatorios y mide cuanto tarda la telemetria del ESP32 en
mostrar el cambio de s. Mientras no hay cambio, manda el comando vigente cada 50 ms (como el puente).
OJO: el extremo es la TELEMETRIA (20 Hz), que agrega entre 0 y 50 ms de cuantizacion que el servo no tiene:
el valor medido es una COTA SUPERIOR de lo que tarda el servo en recibir la orden.
Uso: python3 scripts/latencia_serial.py [N=40]
"""
import random, re, statistics, sys, time
import serial
TEL = re.compile(r'^v(-?\d+),s(-?\d+),b(\d+),e(\d+)$')

def abrir_vivo():
    for i in range(1, 7):
        ser = serial.Serial(); ser.port = '/dev/esp32'; ser.baudrate = 115200; ser.timeout = 0
        ser.dsrdtr = False; ser.rtscts = False; ser.dtr = False; ser.rts = False; ser.open()
        time.sleep(0.4); ser.reset_input_buffer(); time.sleep(0.6)
        if ser.read(500).count(b'\n') >= 5:
            return ser
        ser.close(); time.sleep(1.0)
    sys.exit('No logre un puerto con telemetria.')

def main():
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 40
    ser = abrir_vivo(); buf = bytearray(); lat = []; ini = []; actual = 0
    def leer():
        nonlocal buf
        d = ser.read(256)
        out = []
        if d:
            buf += d
            while b'\n' in buf:
                i = buf.index(b'\n'); l = bytes(buf[:i]).decode(errors='ignore').strip(); del buf[:i + 1]
                m = TEL.match(l)
                if m: out.append((time.monotonic(), int(m.group(2))))
        return out
    try:
        t_envio = time.monotonic()
        for k in range(n):
            # reposo aleatorio con el comando vigente (fase aleatoria respecto a la telemetria)
            fin = time.monotonic() + 0.6 + random.random() * 0.4
            while time.monotonic() < fin:
                if time.monotonic() >= t_envio: ser.write(f'V0,S{actual}\n'.encode()); t_envio += 0.05
                leer(); time.sleep(0.002)
            nuevo = 60 if actual == 0 else 0
            t0 = time.monotonic(); ser.write(f'V0,S{nuevo}\n'.encode()); t_envio = t0 + 0.05
            visto = None; primero = None; previo = actual
            while time.monotonic() - t0 < 1.5 and visto is None:
                if time.monotonic() >= t_envio: ser.write(f'V0,S{nuevo}\n'.encode()); t_envio += 0.05
                for t, s in leer():
                    if t >= t0 and primero is None and s != previo: primero = t
                    if t >= t0 and abs(s - nuevo) < 1 and visto is None: visto = t
                time.sleep(0.001)
            actual = nuevo
            if visto: lat.append((visto - t0) * 1000); ini.append(((primero or visto) - t0) * 1000)
    finally:
        for _ in range(3): ser.write(b'V0,S0\n'); time.sleep(0.03)
        ser.close()
    lat.sort()
    p = lambda q: lat[min(len(lat) - 1, int(q * len(lat)))]
    print(f'{len(lat)} de {n} cambios medidos')
    print(f'comando -> primera telemetria DISTINTA (empieza a moverse): mediana {statistics.median(ini):.0f}  min {min(ini):.0f}  max {max(ini):.0f} ms')
    print(f'comando -> telemetria en el valor FINAL (incluye la rampa del servo): mediana {statistics.median(lat):.0f}  p90 {p(0.9):.0f}  max {lat[-1]:.0f} ms')

if __name__ == '__main__':
    main()
