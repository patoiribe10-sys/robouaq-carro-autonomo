# ESP32 Firmware Rules — RoboUAQ

Aplica cuando: generes o edites código para el ESP32 de este proyecto.

## Hardware definitivo (PCB fabricada — NO CAMBIAR)

| Función | GPIO | Notas |
|---------|------|-------|
| ENC_A (cuadratura A) | 34 | Input only, RISING interrupt |
| ENC_B (cuadratura B) | 35 | Input only, leer en ISR para dirección |
| SERVO (directo) | 25 | ESP32Servo lib, 50Hz, 500–2500µs |
| PWMA (motor velocidad) | 23 | ledcAttach 20kHz 8-bit |
| AIN1 (dir motor) | 21 | Digital output |
| AIN2 (dir motor) | 22 | Digital output |
| STBY (TB6612 enable) | 19 | Mantener HIGH para habilitar driver |
| UART RX2 (desde Jetson) | 16 | Serial2 |
| UART TX2 (hacia Jetson) | 17 | Serial2 |

## API obligatoria (ESP32 Arduino Core v3.x)

```cpp
// CORRECTO — Core v3.x
ledcAttach(pin, freq, bits);
ledcWrite(pin, valor);

// INCORRECTO — API deprecated, NO USAR
ledcSetup(channel, freq, bits);   // NO
ledcAttachPin(pin, channel);      // NO
```

## Arquitectura de tareas RTOS

```cpp
// Core 0: encoder + UART (comunicación)
xTaskCreatePinnedToCore(encoderTask, "Encoder", 2048, NULL, 1, NULL, 0);
xTaskCreatePinnedToCore(uartTask,    "UART",    4096, NULL, 2, NULL, 0);

// Core 1: PID + servo (control tiempo real)
xTaskCreatePinnedToCore(pidTask,     "PID",     4096, NULL, 3, NULL, 1);

// Siempre vaciar loop()
void loop() { vTaskDelete(NULL); }
```

## Encoder cuadratura

- 360 PPR, rueda 65mm diámetro
- Phase A en GPIO 34 (RISING interrupt), Phase B en GPIO 35 (leer en ISR)
- Dirección: A sube y B=LOW → adelante; A sube y B=HIGH → atrás
- Usar `volatile int32_t encoderCount` — incrementar/decrementar con `+= 1` / `-= 1`

## PID parámetros base

- KP=80, KI=15, KD=2, dt=0.01s (100 Hz en Core 1)
- Promedio móvil de 5 muestras para velocidad
- Rampa de aceleración: máx 0.02 m/s por ciclo
- Velocidad objetivo default: 0.3 m/s

## Servo directo

```cpp
#include <ESP32Servo.h>
Servo servoDir;
servoDir.attach(SERVO_PIN, 500, 2500);  // GPIO 25
servoDir.writeMicroseconds(1500);       // Centro
// Rango: 1200µs (izq máx) — 1500µs (centro) — 1800µs (der máx)
```

## TB6612FNG wiring

- VM (motor power): 11.1V (LiPo directo vía PCB)
- VCC (lógica): 3.3V del ESP32 (NO 5V en esta PCB)
- STBY (GPIO 19): siempre HIGH para habilitar driver

## Protocolo UART (Serial2, 115200)

```
Jetson → ESP32:  "V<vel_m/s>,S<servo_µs>\n"    ej: "V0.30,S1500\n"
ESP32 → Jetson:  "v<vel>,s<steer>,e<ticks>\n"  ej: "v0.295,s1500,e12345\n"
Watchdog: 500ms sin comando → vel=0, servo=1500
```
