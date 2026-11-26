# PLAN SEMANA 1 — VERSIÓN FINAL
## Principio: adelantar TODO lo que no necesite el carrito armado

---

## DESDE CASA — Solo Jetson + cámaras

### Bloque A: Ambiente + dependencias (30 min)
```bash
ssh patoih@172.26.169.39
mkdir -p ~/robocar_ws/src ~/robocar_ws/esp32_firmware/src
mkdir -p ~/robocar_ws/models/signs ~/robocar_ws/scripts ~/robocar_ws/config

sudo systemctl stop nvgetty && sudo systemctl disable nvgetty
sudo usermod -aG dialout patoih

pip3 install pyserial flask flask-socketio pygame dt-apriltags --break-system-packages
sudo apt install -y ros-humble-cv-bridge ros-humble-image-transport

python3 -c "import serial, dt_apriltags, cv2, pygame; print('Todo OK')"
```

### Bloque B: Copiar archivos + Claude Code genera nodos (1-2 hrs)
```bash
# Desde laptop:
scp CLAUDE.md PROMPT_CLAUDE_CODE.md test_uart.py patoih@172.26.169.39:~/robocar_ws/
scp -r esp32_firmware/ patoih@172.26.169.39:~/robocar_ws/
scp teleop_xbox_node.py patoih@172.26.169.39:~/robocar_ws/src/robocar/robocar/

# En la Jetson:
cd ~/robocar_ws && claude
# Pegar contenido de PROMPT_CLAUDE_CODE.md → esperar que genere todo
```

### Bloque C: Build workspace (15 min)
```bash
cd ~/robocar_ws && colcon build --symlink-install
source install/setup.bash
echo "source ~/robocar_ws/install/setup.bash" >> ~/.bashrc
```

### Bloque C2: Test Xbox controller SIN carrito (15 min)
**Necesitas:** Jetson + Xbox controller pareado
**NO necesitas:** Carrito, PCB, motor

```bash
# Verificar que la Jetson ve el control:
ls /dev/input/js*    # Debe mostrar /dev/input/js0
cat /dev/input/js0   # Mover sticks, debe salir basura binaria (Ctrl+C)

# Correr teleop solo (sin serial_bridge):
ros2 run robocar teleop_xbox_node

# En otra terminal, verificar que publica:
ros2 topic echo /cmd_vel
ros2 topic echo /cmd_steer
ros2 topic echo /mode
```
Mover sticks y verificar que los valores cambian. Presionar B/X/A para cambiar modo. Presionar Y para ESTOP.

### Bloque D: Imprimir materiales (20 min)
- Tablero de ajedrez 9×6 (25mm, sobre cartón rígido)
- AprilTags 36h11 IDs 0-5 (6-8cm, papel en cartón)
- 6 señales del reglamento (cada una arriba de su AprilTag)

### Bloque E: Calibrar cámaras en escritorio (1.5 hrs)
**Necesitas:** Jetson + cámaras + tablero impreso
```bash
python3 scripts/calibrate_fisheye.py --sensor-id 0 --output config/camera_fisheye.yaml
python3 scripts/calibrate_fisheye.py --sensor-id 1 --output config/camera_normal.yaml --no-fisheye
```

### Bloque F: Test detect_node con AprilTags en mesa (30 min)
```bash
ros2 run robocar detect_node
# Otra terminal: ros2 topic echo /detection/sign
```
Sostener cada tag frente a Camera 1 → verificar detección correcta.

### Bloque G: Test lane_node en escritorio (30 min)
Cinta negra sobre papel blanco frente a Camera 0.
```bash
ros2 run robocar lane_node
# Otra terminal: ros2 topic echo /lane/detected
```

---

## EN EL TALLER — PCB, carrito, espacio grande

### Bloque H: Soldar PCB + verificar (2-3 hrs) — LUNES
Orden: Mini560 → headers ESP32 → headers TB6612 → J1/J2/J3 → terminales.
Multímetro antes de insertar componentes.

### Bloque I: Flash ESP32 + test hardware (1 hr) — LUNES
```bash
cd esp32_firmware && pio run -t upload && pio device monitor
# Escribir: V0.00,S1500  V0.00,S1200  V0.10,S1500  V0.00,S1500
```

### Bloque J: Test UART Jetson↔ESP32 (30 min) — LUNES
```bash
python3 test_uart.py --port /dev/ttyTHS1
```

### Bloque J2: PRIMER MANEJO CON XBOX (30 min) — LUNES
**Esto es lo nuevo y más emocionante. Con PCB soldada + UART probado:**
```bash
# Lanzar modo manual (Xbox + serial bridge):
ros2 launch robocar teleop_launch.py
```
Manejar el carro con el Xbox:
- □ Stick izquierdo arriba → carro avanza
- □ Stick derecho izq/der → carro gira
- □ RB (bumper derecho) → boost de velocidad
- □ LB (bumper izquierdo) → precisión lenta
- □ Y → ESTOP (para todo)
- □ Ajustar `max_vel` si va muy rápido o lento

**Este paso te da confianza de que todo el hardware funciona antes de tocar la visión.**

### Bloque K: Construir pista (1-2 hrs) — MARTES
Cinta negra + superficie clara. 2 intersecciones + curva (radio ≥120cm). Pasos peatonales. Señales con AprilTags.

### Bloque L: Calibrar bird's eye view EN la pista (30 min) — MARTES
Carro en la pista, editar `config/bird_eye_params.yaml`.

### Bloque L2: Modo OBSERVE en pista (1 hr) — MARTES/MIÉRCOLES
**Esto es clave para calibrar la visión sin riesgo:**
```bash
# Lanzar modo observe (Xbox + todos los nodos de visión pasivos):
ros2 launch robocar observe_launch.py
```
Abrir dashboard en laptop: `http://172.26.169.39:8080`

Manejar el carro con Xbox por la pista mientras observas:
- Tab Cam0: ¿detecta las líneas correctamente? ¿El bird's eye view se ve bien?
- Tab Cam1: ¿detecta los AprilTags? ¿Muestra la señal correcta?
- Tab FSM: ¿la FSM toma las decisiones correctas? ¿Se detendría en los cruces?
- Tab Control: verificar velocidad, steering, modo

**Si la visión marca decisiones incorrectas → ajustar parámetros sin riesgo.**
**Si las detecciones son buenas → cambiar a AUTO (botón A en el Xbox).**

### Bloque M: Modo AUTO en pista (2-3 hrs) — JUEVES/VIERNES
```bash
ros2 launch robocar robocar_launch.py
# O si ya estás en observe_launch, presionar A en el Xbox
```

Pruebas incrementales:
1. Solo lane following (quitar señales) — recta + curva
2. Agregar crosswalks — parada 3s en cada uno
3. Agregar señales — leer y obedecer
4. Circuito completo
5. **Tener siempre el Xbox en mano → Y para ESTOP si se va a estrellar**

---

## CRONOGRAMA VISUAL

```
           CASA (Jetson + cámaras + Xbox)  │    TALLER (PCB + pista)
───────────────────────────────────────────┼──────────────────────────
HOY/LUN    A: Ambiente (30 min)            │  H: Soldar PCB (2-3 hrs)
temprano   B: Claude Code (1-2 hrs)        │  I: Flash ESP32 (1 hr)
           C: Build workspace (15 min)     │  J: Test UART (30 min)
           C2: Test Xbox sin carro (15 min)│  J2: Primer manejo Xbox!
           D: Imprimir tags (20 min)       │
───────────────────────────────────────────┼──────────────────────────
LUN noche  E: Calibrar cámaras (1.5 hrs)  │
           F: Test AprilTags (30 min)      │
           G: Test lane_node (30 min)      │
───────────────────────────────────────────┼──────────────────────────
MARTES                                     │  K: Construir pista
                                           │  L: Calibrar bird's eye
                                           │  L2: OBSERVE en pista
───────────────────────────────────────────┼──────────────────────────
MIÉ        Ajustar parámetros desde SSH    │  Iterar OBSERVE: ajustar
            mientras carro en pista        │  HSV, umbrales, gains
───────────────────────────────────────────┼──────────────────────────
JUE/VIE                                    │  M: Modo AUTO en pista
                                           │  Integración completa
                                           │  Xbox en mano para ESTOP
───────────────────────────────────────────┼──────────────────────────
SÁB                                        │  Corridas completas
                                           │  Dashboard + tuning final
```

## TABLA RESUMEN

| Bloque | Lugar | Tiempo | Qué usa | Depende de |
|--------|-------|--------|---------|------------|
| A | Casa | 30 min | Jetson + SSH | Nada |
| B | Casa | 1-2 hrs | CLAUDE.md, PROMPT, teleop_xbox_node.py | A |
| C | Casa | 15 min | colcon build | B |
| C2 | Casa | 15 min | Jetson + Xbox controller | C |
| D | Casa | 20 min | Impresora | Nada |
| E | Casa | 1.5 hrs | Jetson + cámaras + tablero | C, D |
| F | Casa | 30 min | Jetson + cam1 + AprilTags | C, D |
| G | Casa | 30 min | Jetson + cam0 + cinta | C, E |
| H | Taller | 2-3 hrs | PCB + cautín | Nada |
| I | Taller | 1 hr | ESP32 + PlatformIO | H |
| J | Taller | 30 min | Jetson + ESP32 + cables | I, A |
| J2 | Taller | 30 min | Carro + Xbox + teleop_launch | J, C2 |
| K | Taller | 1-2 hrs | Cinta + superficie | Nada |
| L | Pista | 30 min | Carro en pista | K, E, J2 |
| L2 | Pista | 1 hr | observe_launch + dashboard | L, F, G |
| M | Pista | 2-3 hrs | robocar_launch | L2 |

## ARCHIVOS POR BLOQUE

| Bloque | Archivos que usa |
|--------|-----------------|
| A | (solo comandos terminal) |
| B | CLAUDE.md, PROMPT_CLAUDE_CODE.md, teleop_xbox_node.py |
| C | colcon build (usa package.xml, setup.py generados) |
| C2 | teleop_xbox_node.py |
| D | AprilTags PDF, tablero ajedrez PDF, señales PDF |
| E | scripts/calibrate_fisheye.py → config/camera_*.yaml |
| F | detect_node.py, config/apriltag_sign_map.yaml |
| G | lane_node.py, config/bird_eye_params.yaml |
| H | (soldadura manual) |
| I | esp32_firmware/src/main.cpp, platformio.ini |
| J | test_uart.py |
| J2 | launch/teleop_launch.py |
| K | (cinta + superficie) |
| L | config/bird_eye_params.yaml |
| L2 | launch/observe_launch.py, scripts/dashboard.py |
| M | launch/robocar_launch.py |

## CRITERIOS DE "TERMINADO" — SÁBADO

```
□ Xbox controla el carro en modo MANUAL (sticks, ESTOP)
□ Modo OBSERVE: manejo con Xbox + visión pasiva muestra decisiones correctas
□ Modo AUTO: sigue líneas en recta y curva sin salirse
□ Se detiene 3 segundos en cada paso peatonal
□ Detecta AprilTags y lee la señal correcta
□ Gira según la señal (izq/der/recto)
□ No entra a calles con "no pasar" o "sin salida"
□ Se detiene en "alto" (meta)
□ Completa el circuito de 2 intersecciones + curva
□ Dashboard muestra todo en tiempo real
□ ESTOP funciona instantáneamente en cualquier modo
```
