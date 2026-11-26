#!/usr/bin/env python3
"""
Test rápido de UART entre Jetson y ESP32.
Ejecutar DESPUÉS de flashear el firmware y conectar los cables.

Uso:
    python3 test_uart.py [--port /dev/ttyTHS1]

Verifica:
1. Conexión serial
2. Envío de comandos V,S
3. Recepción de telemetría v,s,e
4. Watchdog (deja de enviar → motor se detiene)
"""

import serial
import time
import sys
import argparse

def main():
    parser = argparse.ArgumentParser(description='Test UART ESP32 ↔ Jetson')
    parser.add_argument('--port', default='/dev/ttyTHS1',
                       help='Puerto serial (default: /dev/ttyTHS1, alternativa: /dev/ttyUSB0)')
    parser.add_argument('--baud', type=int, default=115200)
    args = parser.parse_args()

    print(f"[TEST] Abriendo {args.port} a {args.baud} baud...")
    try:
        ser = serial.Serial(args.port, args.baud, timeout=0.1)
    except serial.SerialException as e:
        print(f"[ERROR] No se pudo abrir {args.port}: {e}")
        print("  Intenta con: --port /dev/ttyUSB0")
        print("  O verifica: sudo usermod -aG dialout $USER && logout/login")
        sys.exit(1)

    time.sleep(2)  # Esperar reset del ESP32
    ser.reset_input_buffer()
    print("[OK] Puerto abierto\n")

    # Test 1: Leer telemetría pasiva (el ESP32 envía aunque no reciba)
    print("=" * 50)
    print("[TEST 1] Leyendo telemetría del ESP32 (5 segundos)...")
    print("  Esperando formato: v<vel>,s<steer>,e<ticks>")
    start = time.time()
    count = 0
    while time.time() - start < 5:
        line = ser.readline().decode('utf-8', errors='ignore').strip()
        if line:
            print(f"  ← {line}")
            count += 1
    if count == 0:
        print("  [WARN] No se recibió telemetría. Verifica:")
        print("    - TX2(ESP32 GPIO17) → RX(Jetson)")
        print("    - RX2(ESP32 GPIO16) → TX(Jetson)")
        print("    - GND compartido")
    else:
        print(f"  [OK] Recibidos {count} mensajes de telemetría")

    # Test 2: Enviar comando de servo (sin mover motor)
    print("\n" + "=" * 50)
    print("[TEST 2] Enviando comando: V0.0,S1500 (centro, sin motor)...")
    for i in range(10):
        ser.write(b'V0.00,S1500\n')
        time.sleep(0.05)
        line = ser.readline().decode('utf-8', errors='ignore').strip()
        if line:
            print(f"  → V0.00,S1500  ← {line}")

    # Test 3: Sweep de servo
    print("\n" + "=" * 50)
    input("[TEST 3] Presiona ENTER para hacer sweep de servo (izq → centro → der)...")
    for us in range(1200, 1801, 50):  # Izquierda a derecha
        cmd = f"V0.00,S{us}\n"
        ser.write(cmd.encode())
        time.sleep(0.1)
        print(f"  Servo: {us} µs")
    ser.write(b'V0.00,S1500\n')  # Volver a centro
    print("  Servo centrado")

    # Test 4: Motor muy lento (con confirmación)
    print("\n" + "=" * 50)
    resp = input("[TEST 4] ¿Probar motor a velocidad mínima? (s/n): ")
    if resp.lower() == 's':
        print("  Enviando V0.15 por 3 segundos...")
        start = time.time()
        while time.time() - start < 3:
            ser.write(b'V0.15,S1500\n')
            time.sleep(0.05)
            line = ser.readline().decode('utf-8', errors='ignore').strip()
            if line:
                print(f"  ← {line}")
        ser.write(b'V0.00,S1500\n')
        print("  Motor detenido")

    # Test 5: Watchdog
    print("\n" + "=" * 50)
    print("[TEST 5] Test de watchdog (dejar de enviar por 1s)...")
    ser.write(b'V0.10,S1500\n')
    time.sleep(0.1)
    print("  Enviado V0.10, ahora espero 1s sin enviar...")
    time.sleep(1.0)
    line = ser.readline().decode('utf-8', errors='ignore').strip()
    if line:
        print(f"  ← {line}")
        if 'v0.0' in line or 'v-0.0' in line:
            print("  [OK] Watchdog funcionando: velocidad = 0")
        else:
            print("  [INFO] Verificar manualmente si el motor se detuvo")

    print("\n" + "=" * 50)
    print("[DONE] Tests completados.")
    ser.close()

if __name__ == '__main__':
    main()
