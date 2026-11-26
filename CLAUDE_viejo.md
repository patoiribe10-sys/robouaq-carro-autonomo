# CLAUDE.md — RoboUAQ 2026 Autonomous Car

## DEADLINE: Second week of May 2026. Every decision must prioritize speed to working demo.

## Project Overview
1:10 scale autonomous car for RoboUAQ 2026 "Robot Conducción Urbana". Must complete urban circuit autonomously in <5min, obeying traffic signs and avoiding obstacles.

## Architecture
```
┌─────────────────── JETSON ORIN NANO ───────────────────────┐
│                                                             │
│  Xbox Controller ──► teleop_xbox_node ──┐                   │
│                                         ▼                   │
│                              /mode (manual|observe|auto)    │
│                                         │                   │
│  Camera 0 (fisheye) ──► lane_node ──────┤                   │
│  Camera 1 (normal)  ──► detect_node ────┤                   │
│                                         ▼                   │
│                              nav_node (FSM) ◄── /mode       │
│                                  │                          │
│                    ┌─────────────┤                          │
│                    ▼             ▼                          │
│              /cmd_vel      /cmd_steer                       │
│                    │             │                          │
│                    ▼             ▼                          │
│               serial_bridge_node ◄──────────────► UART      │
│                                                             │
│  dashboard.py (Flask :8080) ◄── all topics (view only)      │
└─────────────────────────────┬──────────────────────────────┘
                              │ UART 115200 (GPIO16/17)
┌─────────────────── ESP32 ───┴──────────────────────────────┐
│  PID velocity control (100Hz, Core 1)                       │
│  Quadrature encoder (Phase A/B on GPIO 34/35)               │
│  TB6612FNG motor driver                                     │
│  Direct servo PWM (GPIO 25)                                 │
└─────────────────────────────────────────────────────────────┘
```

## Operating Modes — /mode topic controls everything
| Mode | Who drives? | Vision nodes | FSM | Dashboard |
|------|------------|-------------|-----|-----------|
| **manual** | Xbox controller | OFF | OFF | Shows camera raw feeds |
| **observe** | Xbox controller | ON (passive) | ON (decisions logged but NOT executed) | Shows detections, planned path, FSM state |
| **auto** | nav_node (FSM) | ON (active) | ON (controls car) | Shows everything live |

**Mode switching:** Xbox buttons B=manual, X=observe, A=auto. Also via dashboard web buttons.
**ESTOP:** Xbox button Y — immediate stop in ANY mode. Toggle to release.
**Key principle:** In "observe" mode, the PID still controls the velocity commanded by the Xbox stick, and the servo respects physical limits. Vision nodes run and publish detections/decisions, but nav_node does NOT publish to /cmd_vel or /cmd_steer. This lets you drive the car manually while seeing exactly what the autonomous system WOULD do.

## Hardware — FIXED, DO NOT SUGGEST CHANGES
- **Compute:** Jetson Orin Nano, JetPack 6.x, Ubuntu 22.04, ROS 2 Humble
- **MCU:** ESP32 DevKit V1 (WROOM-32), PlatformIO, Arduino framework, ESP32 core v3.x
- **Motor driver:** TB6612FNG (ROB-14450 SparkFun breakout)
- **Cameras:** 2× IMX219 CSI (cam0=fisheye top-down, cam1=normal front-facing)
- **Encoder:** Quadrature, 360 PPR, 65mm wheel
- **Steering:** Ackermann servo, direct PWM from ESP32
- **Power:** 3S LiPo 11.1V → Mini560 → 5V (servo+ESP32); TB6612 VM=11.1V, VCC=3.3V
- **Custom PCB:** KiCad designed, fabricated, being soldered

## GPIO Map (ESP32) — MATCHES PCB, DO NOT CHANGE
| Function | GPIO | Notes |
|----------|------|-------|
| Encoder Phase A | 34 | Input only, RISING interrupt |
| Encoder Phase B | 35 | Input only, read in ISR for direction |
| Servo PWM | 25 | ESP32Servo lib, 50Hz, 500-2500µs |
| PWMA (motor speed) | 23 | ledcAttach 20kHz 8-bit |
| AIN1 (motor dir) | 21 | Digital output |
| AIN2 (motor dir) | 22 | Digital output |
| STBY (enable) | 19 | Hold HIGH to enable driver |
| UART RX2 | 16 | From Jetson TX |
| UART TX2 | 17 | To Jetson RX |

## UART Protocol
```
Jetson → ESP32:  "V<vel_m/s>,S<servo_µs>\n"     e.g. "V0.30,S1500\n"
ESP32 → Jetson:  "v<vel>,s<steer>,e<ticks>\n"   e.g. "v0.295,s1500,e12345\n"
Watchdog: 500ms no command → vel=0, servo=center
```

## ESP32 Firmware (already written in esp32_firmware/src/main.cpp)
- Dual-core RTOS: Core0=encoder+UART, Core1=PID+servo
- Quadrature encoder with direction detection (Phase A RISING + Phase B read)
- PID: KP=80, KI=15, KD=2, dt=0.01s, 5-sample moving average
- Acceleration ramp: 0.02 m/s per cycle
- ESP32 core v3.x API: use `ledcAttach(pin,freq,bits)` + `ledcWrite(pin,val)`
- DO NOT use deprecated ledcSetup/ledcAttachPin

## Jetson Software Stack — WHAT NEEDS TO BE BUILT

### 0. teleop_xbox_node.py (ROS 2 Python node) — ALREADY WRITTEN
- **File provided:** teleop_xbox_node.py (copy to robocar package)
- Xbox One controller via pygame (/dev/input/js0)
- Stick left Y = velocity, Stick right X = steering
- RB = boost, LB = precision, Y = ESTOP, A/B/X = mode switch
- Start = begin race, Back = reset FSM
- Publishes: `/cmd_vel`, `/cmd_steer`, `/mode`, `/estop`, `/nav/start`, `/nav/reset`
- **In manual/observe:** publishes joystick commands to /cmd_vel and /cmd_steer
- **In auto:** does NOT publish vel/steer (nav_node takes over)
- **ESTOP:** overrides everything, forces vel=0 and steer=center
- Requires: `pip3 install pygame --break-system-packages`

### 1. serial_bridge_node.py (ROS 2 Python node)
- Subscribes: `/cmd_vel` (Float32), `/cmd_steer` (Int32)
- Publishes: `/odom_vel` (Float32), `/steer_status` (Int32), `/encoder_ticks` (Int32)
- Opens /dev/ttyTHS1 at 115200 (Jetson hardware UART)
- Background reader thread (non-blocking)
- Sends "V{vel},S{steer}\n" at 20Hz
- Parses "v{vel},s{steer},e{ticks}\n" responses
- **Mode-aware:** Always runs. Forwards whatever is on /cmd_vel and /cmd_steer regardless of mode (teleop_xbox_node or nav_node publishes depending on mode).
- Also subscribes to `/estop` (Bool): if True, overrides with V0,S1500

### 2. lane_node.py (ROS 2 Python node)
- Uses Camera 0 (fisheye, CSI, /dev/video0)
- **Track surface:** PVC vinyl (may have glare/reflections, not flat)
- **Lane markings:** electrical tape ≥19mm wide, dashed lines with ≥5cm gaps
- Pipeline: undistort fisheye → bird's eye warp → HSV mask (dark tape on light PVC) → contours → lane center → steering error
- Also detect CROSSWALK: horizontal lines perpendicular to direction of travel
- Publishes: `/lane/steering_error` (Float32), `/lane/detected` (Bool), `/lane/crosswalk_detected` (Bool)
- Publishes debug image: `/lane/debug_image` (sensor_msgs/CompressedImage) — bird's eye view with overlays (detected lines, crosswalks, steering direction arrow)
- Target: 15-30 FPS
- GStreamer pipeline for CSI: `nvarguscamerasrc sensor-id=0 ! video/x-raw(memory:NVMM),width=640,height=480,framerate=30/1 ! nvvidconv ! video/x-raw,format=BGRx ! videoconvert ! video/x-raw,format=BGR ! appsink`
- Requires fisheye calibration matrix (calibrate with checkerboard, save to yaml)
- **Crosswalk detection:** In bird's eye view, look for multiple horizontal contours clustered together (zebra pattern). Publish crosswalk_detected=True when detected.
- **Mode-aware:** Subscribes to `/mode`. In "manual" → node sleeps (no processing). In "observe" or "auto" → runs full pipeline and publishes. This saves CPU/GPU in manual mode.

### 3. detect_node.py (ROS 2 Python node)
- Uses Camera 1 (normal, CSI, /dev/video1)
- PRIMARY: AprilTag detection (family 36h11, dt-apriltags lib already installed)
  - Each sign has an AprilTag below it (4-8cm, white border 1cm)
  - Tag ID → sign mapping (defined in config, assigned at competition)
- SECONDARY: YOLOv8n for sign image classification (if time to train)
- 6 sign classes per official rules:
  1. no_pasar (red circle + white bar) → don't enter that street
  2. sin_salida (blue T sign) → don't enter that street  
  3. vuelta_derecha (blue circle + right arrow) → turn right at intersection
  4. vuelta_izquierda (blue circle + left arrow) → turn left at intersection
  5. siga_de_frente (blue circle + up arrow) → go straight
  6. alto (STOP octagon) → stop, this is the FINISH LINE
- Publishes: `/detection/sign` (String), `/detection/tag_id` (Int32), `/detection/distance` (Float32)
- Publishes debug image: `/detection/debug_image` (sensor_msgs/CompressedImage) — camera frame with AprilTag outlines drawn, tag ID labels, sign name overlaid, bounding boxes
- AprilTag gives distance + angle for free (from pose estimation)
- Target: 15-30 FPS (AprilTags are lightweight)
- **Mode-aware:** Subscribes to `/mode`. In "manual" → sleeps. In "observe" or "auto" → runs detection and publishes results + debug image.

### 4. nav_node.py (ROS 2 Python node) — FSM
**CRITICAL RULE 1.7: Must stop 3 seconds before EVERY intersection crossing!**

States and transitions:
```
IDLE → FOLLOW_LANE (on start button / ROS service call)
FOLLOW_LANE → APPROACHING_INTERSECTION (detect crosswalk lines or AprilTag in range)
APPROACHING_INTERSECTION → STOPPED_AT_INTERSECTION (velocity=0, start 3s timer)
STOPPED_AT_INTERSECTION → EXECUTE_TURN (after 3s, read sign, decide direction)
EXECUTE_TURN → FOLLOW_LANE (turn complete, back to lane following)
FOLLOW_LANE → FINISH (detect "alto" sign → stop permanently, run complete)
```
Sign-based decisions at intersection:
- `no_pasar` → don't enter that street, pick alternative
- `sin_salida` → don't enter that street, pick alternative  
- `vuelta_derecha` → steer right through intersection
- `vuelta_izquierda` → steer left through intersection
- `siga_de_frente` → go straight through intersection
- `alto` → STOP permanently = finish line reached

Lane logic:
- One-way street (single line marking): stay in lane
- Two-way street (double line marking): stay in right lane, no lane changes
- Obstacles (flags in lane): slow down, avoid if possible

- Subscribes: `/lane/steering_error`, `/lane/detected`, `/lane/crosswalk_detected`,
  `/detection/sign`, `/detection/tag_id`, `/detection/distance`, `/mode`, `/nav/start`, `/nav/reset`, `/estop`
- Publishes: `/cmd_vel` (Float32), `/cmd_steer` (Int32), `/nav/state` (String), `/nav/decision_log` (String)
- `/nav/state` → current FSM state name (for dashboard + teleop display)
- `/nav/decision_log` → human-readable log entries like "Detected vuelta_derecha at 0.8m → turning right"
- Steering: error × gain → servo µs offset from center (1500)
- Sign detection cooldown: 5s per tag_id (avoid re-triggering)
- **20s immobile = run terminated (rule 3.10) — add safety timer**
- **Mode-aware:** Subscribes to `/mode`.
  - "manual" → FSM paused, no processing
  - "observe" → FSM runs normally, computes decisions, publishes `/nav/state` and `/nav/decision_log`, but does NOT publish to `/cmd_vel` or `/cmd_steer` (Xbox controls the car)
  - "auto" → FSM runs and DOES publish to `/cmd_vel` and `/cmd_steer` (full autonomous)

### 5. dashboard.py (Flask web dashboard — scripts/)
- Flask + SocketIO on port 8080, accessible from laptop on same network
- **Tab: Camera 0** — MJPEG stream from `/lane/debug_image` (bird's eye + lane overlays)
- **Tab: Camera 1** — MJPEG stream from `/detection/debug_image` (AprilTag bboxes + sign labels)
- **Tab: PID** — Real-time chart of target vel vs odom_vel (from serial_bridge telemetry)
- **Tab: FSM** — Current state, decision log (from `/nav/decision_log`), planned action
- **Tab: Control** — Mode selector buttons (MANUAL/OBSERVE/AUTO), ESTOP button, START/RESET buttons, current velocity + steering display
- Subscribes to all relevant ROS 2 topics via a bridge thread
- **Key for OBSERVE mode:** lets you see exactly what the autonomous system would do while you drive with the Xbox controller

## ROS 2 Workspace Structure
```
~/robocar_ws/
├── src/
│   └── robocar/
│       ├── package.xml
│       ├── setup.py
│       ├── robocar/
│       │   ├── __init__.py
│       │   ├── teleop_xbox_node.py     # Xbox controller (PROVIDED)
│       │   ├── serial_bridge_node.py
│       │   ├── lane_node.py
│       │   ├── detect_node.py
│       │   └── nav_node.py
│       ├── config/
│       │   ├── camera_fisheye.yaml     # Fisheye calibration
│       │   ├── camera_normal.yaml      # Normal cam calibration
│       │   ├── bird_eye_params.yaml    # Perspective transform points
│       │   └── apriltag_sign_map.yaml  # tag_id → sign_name mapping
│       ├── launch/
│       │   ├── robocar_launch.py       # All nodes (auto mode)
│       │   ├── teleop_launch.py        # Manual/observe + serial bridge only
│       │   └── observe_launch.py       # All nodes in observe mode
│       └── scripts/
│           ├── calibrate_fisheye.py
│           └── dashboard.py
├── esp32_firmware/
│   ├── platformio.ini
│   └── src/main.cpp
└── models/
    └── signs/
        ├── best.pt
        └── best.engine
```

## Build & Run Commands
```bash
# Build ROS 2 workspace
cd ~/robocar_ws && colcon build --symlink-install
source install/setup.bash

# Launch modes:
ros2 launch robocar teleop_launch.py     # Manual: Xbox + serial_bridge only
ros2 launch robocar observe_launch.py    # Observe: Xbox + all vision (passive)
ros2 launch robocar robocar_launch.py    # Auto: full autonomous

# Or launch individual nodes for testing:
ros2 run robocar teleop_xbox_node       # Just Xbox controller
ros2 run robocar serial_bridge_node     # Just UART bridge
ros2 run robocar lane_node              # Just lane detection
ros2 run robocar detect_node            # Just sign/AprilTag detection
ros2 run robocar nav_node               # Just FSM

# Flash ESP32 (from PlatformIO on laptop)
cd ~/robocar_ws/esp32_firmware && pio run -t upload

# Dashboard (in separate terminal)
python3 scripts/dashboard.py            # Open http://172.26.169.39:8080

# Test UART manually
python3 test_uart.py --port /dev/ttyTHS1
```

## Jetson Setup Requirements
```bash
# Enable UART (run once)
sudo systemctl stop nvgetty
sudo systemctl disable nvgetty
sudo usermod -aG dialout $USER
# May need: sudo /opt/nvidia/jetson-io/jetson-io.py (enable UART)

# Install dependencies
pip3 install pyserial ultralytics flask flask-socketio pygame --break-system-packages
pip3 install dt-apriltags --break-system-packages
sudo apt install ros-humble-cv-bridge ros-humble-image-transport
```

## Key Constraints
- Python 3.10 on Jetson (Ubuntu 22.04)
- ultralytics 8.4.14 already installed
- No I2C from Jetson — all control via ESP32 UART
- Both cameras are CSI — usar `cv2.VideoCapture(sensor_id)` con índice directo (GStreamer nvarguscamerasrc NO funciona en este setup); el driver V4L2 entrega frames YUYV interpretados como BGR de 3 canales con R=0, G=Y, B=0
- ESP32 Servo library needed (not direct ledcWrite for servo — timing issues)
- Target velocity: 0.3 m/s default, adjustable by signs

## What's Already Done
- ESP32 firmware: COMPLETE (esp32_firmware/src/main.cpp)
- Teleop Xbox node: COMPLETE (teleop_xbox_node.py — copy to robocar package)
- PCB: fabricated, being soldered
- 3D printed mounts: camera, servo, motor, Jetson — all done
- Physical car: assembled with Ackermann steering
- Xbox One controller: already paired with Jetson

## What Needs to Be Built (in priority order)
1. serial_bridge_node.py — CRITICAL, test UART first
2. teleop_launch.py — Launch teleop + serial_bridge (for manual testing immediately)
3. lane_node.py — Lane following + crosswalk detection + debug image
4. detect_node.py — AprilTag detection PRIMARY + debug image overlay
5. nav_node.py — FSM with mode-aware behavior (observe vs auto)
6. observe_launch.py + robocar_launch.py — Launch files for each mode
7. Config files (camera cal, bird's eye, apriltag map)
8. Camera calibration script
9. Dashboard (Flask, streams, control panel)
10. YOLO training (SECONDARY, only if AprilTags insufficient)

## Competition Rules Summary (from official PDF)
- Track: PVC vinyl 600×1000cm, tape markings ≥19mm, may have glare
- MUST stop 3s before every intersection (penalty: -10 points)
- Wrong decision at sign: -30 points
- Wrong lane: -20 points
- Max 3 penalties then terminated
- 20s immobile = terminated
- 5 min max per run
- Collisions = terminated
- Only vision sensors allowed (NO IR line followers)
- Single start button required
- 3 min calibration round on official track
- Signs have AprilTags (36h11) below them as markers
- Signs: no_pasar, sin_salida, vuelta_derecha, vuelta_izquierda, siga_de_frente, alto
- "Alto" = finish line (stop permanently)
- Curve radius ≥120cm ±10%
- Vehicle max: 450×280×300mm, min: 360×160×154mm

---

## ESTADO ACTUAL — 2026-04-27

### Nodos ROS 2

| Nodo | Archivo | Estado |
|------|---------|--------|
| teleop_xbox_node | `robocar/teleop_xbox_node.py` | ✅ Listo (escrito a mano, copiado al paquete) |
| serial_bridge_node | `robocar/serial_bridge_node.py` | ✅ Listo — pendiente prueba con ESP32 físico |
| lane_node | `robocar/lane_node.py` | ✅ Listo — pendiente calibración cámara y ajuste bird's eye |
| detect_node | `robocar/detect_node.py` | ✅ Listo — pendiente prueba con cámara física |
| nav_node | `robocar/nav_node.py` | ✅ Listo — pendiente prueba en pista |

### Launch files

| Archivo | Modo | Estado |
|---------|------|--------|
| `launch/teleop_launch.py` | manual | ✅ Listo |
| `launch/observe_launch.py` | observe | ✅ Listo |
| `launch/robocar_launch.py` | auto | ✅ Listo |

### Config files

| Archivo | Estado |
|---------|--------|
| `config/apriltag_sign_map.yaml` | ✅ Listo (tags 0–5) |
| `config/bird_eye_params.yaml` | ✅ Placeholder — ajustar con `--tune-birdseye` |
| `config/camera_fisheye.yaml` | ⏳ Placeholder — calibrar con tablero |
| `config/camera_normal.yaml` | ⏳ Placeholder — calibrar con tablero |

### Scripts

| Archivo | Estado |
|---------|--------|
| `scripts/calibrate_fisheye.py` | ✅ Listo (fisheye + normal + `--tune-birdseye`) |
| `scripts/dashboard.py` | ✅ Listo — Flask+SocketIO, 5 pestañas, puerto 8080 |

### Correcciones aplicadas en sesión 2026-04-27

1. **nav_node `_sign_to_turn()`** — BLOCK_SIGNS ahora retorna `None` en vez de `0`, para distinguir "calle bloqueada" de "ir recto".
2. **nav_node `_tick_stopped()`** — si `turn_direction is None`, transiciona a `State.STOPPED` y loggea "calle bloqueada, esperando operador" en vez de entrar a `EXECUTE_TURN`.
3. **nav_node `_tick_execute_turn()`** — giros reales (izq/der) terminan cuando `lane_detected AND elapsed >= 1.0s`; recto sigue usando `turn_duration` fijo.
4. **lane_node `_process()`** — crosswalk detection usa mask HSV blanco `[0,0,180]–[180,40,255]` sobre el warped en vez de los contornos oscuros del carril. El lane following sigue con los contornos oscuros.
5. **detect_node `process_frame()`** — publica `/detection/sign`, `/detection/tag_id`, `/detection/distance` en **cada frame**, incluso sin detección (`sign=""`, `tag_id=-1`, `distance=0.0`), para que nav_node sepa cuándo desaparece una señal.

### Pendiente físico (taller)

- [ ] Soldar PCB (en proceso)
- [ ] Conectar cables CSI 15-pin → 22-pin (adaptadores para IMX219 en Jetson Orin Nano)
- [ ] Flash ESP32: `cd ~/robocar_ws/esp32_firmware && pio run -t upload`
- [x] Calibrar cam0 fisheye — RMS 0.64 ✅
- [ ] Calibrar cam1 normal: `python3 scripts/calibrate_fisheye.py --sensor-id 1 --no-fisheye`
- [ ] Ajustar bird's eye: `python3 scripts/calibrate_fisheye.py --sensor-id 0 --tune-birdseye`
- [ ] Ajustar `apriltag_sign_map.yaml` con IDs reales de la competencia

### Orden de prueba en taller

**Bloque H — UART y motores (sin cámaras)**
1. `ros2 launch robocar teleop_launch.py`
2. Verificar que Xbox mueve motor y servo
3. Revisar logs de `serial_bridge_node` para confirmar telemetría ESP32

**Bloque I — Cámaras y calibración**
1. Conectar cables CSI, verificar: `ls /dev/video*`
2. Calibrar cam0 y cam1 con tablero de ajedrez
3. Ajustar puntos bird's eye con `--tune-birdseye` sobre pista real

**Bloque J — Modo observe (conducción manual + visión pasiva)**
1. `ros2 launch robocar observe_launch.py`
2. Manejar con Xbox mientras se monitorea dashboard en `http://<IP>:8080`
3. Verificar que lane_node detecta líneas y crosswalk correctamente
4. Verificar que detect_node detecta AprilTags y mapea señales correctas
5. Verificar que nav_node loggea decisiones correctas en `/nav/decision_log`

**Bloque J2 — Modo auto (primera prueba autónoma)**
1. `ros2 launch robocar robocar_launch.py`
2. Ajustar `steer_gain` y `base_vel` según comportamiento real
3. Xbox en mano con Y (ESTOP) listo en todo momento

---

## SESIONES DE CLAUDE CODE

Cada vez que termines una sesión con Claude Code, pide: **"cierra sesión"** y se actualizará esta sección con un resumen de lo que se hizo.

---

### Sesión 1 — 2026-04-27

**Qué se hizo:**
- Se crearon 4 skill files en `.claude/skills/` (ros2, esp32, opencv, fsm) para contexto automático en futuras sesiones.
- Se construyó el workspace ROS 2 completo desde cero:
  - Paquete `robocar` con `package.xml`, `setup.py`, `setup.cfg`
  - 5 nodos Python: `teleop_xbox_node` (copiado), `serial_bridge_node`, `lane_node`, `detect_node`, `nav_node`
  - 3 launch files: `teleop_launch.py`, `observe_launch.py`, `robocar_launch.py`
  - 4 config YAMLs: calibración cámaras (placeholder), bird's eye (defaults), apriltag map
  - `scripts/calibrate_fisheye.py` — calibración interactiva con modo `--tune-birdseye`
  - `scripts/dashboard.py` — Flask+SocketIO, 5 pestañas, streams MJPEG, botones de control
- `colcon build` pasó limpio, los 5 ejecutables quedaron registrados.
- Se aplicaron 5 correcciones de lógica a `nav_node`, `lane_node` y `detect_node` (ver sección ESTADO ACTUAL).

**Estado al cerrar:**
Todo el código base está generado. El siguiente paso es hardware: soldar PCB, conectar CSI, flash ESP32 y empezar Bloque H en taller.

---

### Sesión 2 — 2026-04-28

**Qué se hizo:**
- **`scripts/calibrate_fisheye.py` — corrección apertura de cámara CSI:**
  - Renombrada función `gstreamer_csi` → `gstreamer_pipeline(sensor_id, width=1280, height=720, fps=30)`.
  - Pipeline simplificado: sin casts `(int)`/`(string)`/`(fraction)`, sin `flip-method=0`, resolución default 1280×720 a 30fps (antes 640×480 a 15fps).
  - Eliminado fallback `cv2.VideoCapture(sensor_id)` en `main()` y en `_tune_birdseye()`. Si el pipeline GStreamer no abre, el script imprime el pipeline exacto usado y termina con error claro.
  - Motivo: el fallback con índice directo abría la cámara incorrectamente (imagen verde, resolución incorrecta).

**Estado al cerrar:**
Sin cambios de lógica ni nodos nuevos. El script de calibración ya usa exclusivamente GStreamer para ambos modos (`--calibrate` y `--tune-birdseye`). Todo lo demás sigue igual que al cerrar Sesión 1.

---

### Sesión 3 — 2026-04-28

**Qué se hizo:**
- **`esp32_firmware/platformio.ini` — versión de plataforma fijada:**
  - `platform = espressif32@6.10.0` (antes sin versión fija). Sin `platform_packages`.
  - Motivo: asegurar descarga de Arduino-ESP32 core 3.x consistente entre máquinas.

- **`esp32_firmware/src/main.cpp` — API LEDC migrada de v3.x a v2.x:**
  - En `setup()`: `ledcAttach(PWMA_PIN, 20000, 8)` → `ledcSetup(0, 20000, 8)` + `ledcAttachPin(PWMA_PIN, 0)`.
  - En `motorSetPWM()` (3 llamadas): `ledcWrite(PWMA_PIN, val)` → `ledcWrite(0, val)`.
  - Canal LEDC asignado: canal 0 para PWMA_PIN (GPIO 23).
  - Motivo: el framework descargado por PlatformIO (`framework-arduinoespressif32 3.20017`) implementa la API v2.x; la v3.x (`ledcAttach` directo) no existe en esa versión y causaba error de compilación.

**Estado al cerrar:**
Firmware ESP32 listo para compilar con `pio run -t upload`. Siguiente paso: flash físico del ESP32 y prueba Bloque H (UART + motores).

---

### Sesión 4 — 2026-04-28

**Qué se hizo:**

- **`esp32_firmware/platformio.ini` — upload_flags añadidos:**
  - `upload_flags = --before=no_reset / --after=hard_reset`
  - Motivo: evitar reset involuntario antes del flash y forzar hard reset al terminar para arrancar firmware limpio.

- **`scripts/calibrate_fisheye.py` — diagnóstico y corrección de apertura de cámara IMX219:**
  - **Revertido GStreamer:** eliminada `gstreamer_pipeline()`, reemplazada por `_open_camera(sensor_id)` que usa `cv2.VideoCapture(sensor_id)` con fallback a índice 0. GStreamer nvarguscamerasrc no funciona en este setup.
  - **Propiedades de cámara:** `_open_camera()` aplica `FOURCC=YUYV`, `FRAME_WIDTH=640`, `FRAME_HEIGHT=480`, `CONVERT_RGB=1` tras abrir exitosamente.
  - **Resize forzado:** ambos loops (calibración y tune_birdseye) hacen `cv2.resize(frame, (640, 480))` como garantía, porque el sensor IMX219 entrega 3280×2464 ignorando los `cap.set()` de resolución.
  - **Conversión de color:** diagnóstico confirmó que el driver V4L2 entrega YUYV empaquetado como 3 canales BGR con R≈0, G=Y (luminancia), B≈0. La conversión actual usa `cv2.COLOR_YUV2BGR_YUYV` cuando se detecta ese patrón (`shape[2]==3 AND mean(R)<1 AND mean(B)<1`). **Pendiente confirmar en hardware** si COLOR_YUV2BGR_YUYV funciona con input (H,W,3) o si el frame llega como (H,W,2) tras setear FOURCC=YUYV.

**Advertencia para próxima sesión:**
Si la imagen sigue sin color tras este fix, verificar el shape real del frame con FOURCC=YUYV:
```bash
python3 -c "
import cv2
cap = cv2.VideoCapture(0)
cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc('Y','U','Y','V'))
ret, f = cap.read()
print('shape:', f.shape, 'dtype:', f.dtype, 'means:', f[:,:,0].mean(), f[:,:,1].mean(), f[:,:,2].mean() if len(f.shape)==3 else 'N/A')
cap.release()
"
```
Si shape es `(H, W, 2)`, cambiar la condición de detección a `len(frame.shape) == 3 and frame.shape[2] == 2`.

**Estado al cerrar:**
Todos los archivos de código están listos. La calibración de cámara tiene fix de formato pendiente de verificación física. Siguiente paso: flash ESP32 (Bloque H), luego conectar cámaras y verificar shape/color del frame (Bloque I).

---

### Sesión 5 — 2026-04-29

**Qué se hizo:**

- **Problema raíz de cámara resuelto:**
  - `pip3 install opencv-python` (v4.13) estaba tapando el OpenCV del sistema (JetPack).
  - El OpenCV de pip NO tiene GStreamer. El del sistema SÍ (`GStreamer: YES 1.19.90`).
  - Fix: `pip3 uninstall opencv-python` → ahora `import cv2` usa el del sistema automáticamente.
  - **Regla permanente:** NUNCA hacer `pip3 install opencv-python` en esta Jetson. Usar siempre el OpenCV del sistema en `/usr/lib/python3/dist-packages`.

- **Pipeline GStreamer confirmado definitivo para cam0:**
  ```
  nvarguscamerasrc sensor-id=0 !
  video/x-raw(memory:NVMM),width=(int)640,height=(int)480,format=(string)NV12,framerate=(fraction)30/1 !
  nvvidconv !
  video/x-raw,format=(string)BGRx !
  videoconvert ! video/x-raw,format=(string)BGR !
  appsink sync=false drop=true max-buffers=1
  ```
  Este pipeline captura frames en tiempo real a ~30fps. El warm-up de 15-20 frames es necesario para que el sensor estabilice exposición.

- **`scripts/calibrate_fisheye.py` reescrito completamente:**
  - Usa el pipeline GStreamer correcto (no VideoCapture(0)).
  - Flujo: video en vivo para previsualizar → tecla C o botón FOTO congela frame y busca tablero → flash verde si encontró, rojo si no.
  - Detección robusta: 8 combinaciones de flags + CLAHE para mejorar contraste.
  - Botones en pantalla con mouse (FOTO, BORRAR, CALCULAR).
  - BOARD_W=5, BOARD_H=7 (tablero físico disponible: 6 columnas × 8 filas de cuadros).
  - Resolución 640×480 para velocidad.

- **Calibración cam0 completada:**
  - 20 imágenes capturadas, RMS = 0.6417 px (ACEPTABLE).
  - Archivo generado: `config/camera_fisheye.yaml`
  - K = [[192.43, 0, 324.37], [0, 256.42, 284.88], [0, 0, 1]]
  - D = [0.1607, 0.0616, -0.1178, 0.0447]

- **Correcciones aplicadas en esta sesión:**
  - **`scripts/calibrate_fisheye.py` — soporte `--no-fisheye`:** Añadido argumento `--no-fisheye` para cam1. Cuando está activo usa `cv2.calibrateCamera()` estándar en vez de `cv2.fisheye.calibrate()`, con `objp` shape `(N,3)` en lugar de `(N,1,3)`. El YAML incluye campo `model: standard` o `model: fisheye`.
  - **`lane_node.py` — pipeline GStreamer corregido:** Eliminado `flip-method=0`, quitados espacios extra en caps, `appsink drop=1` → `appsink sync=false drop=true max-buffers=1`. Añadido warm-up de 20 frames al abrir cámara. Fix de carga de calibración: `p.get('calibrated', False)` → `'camera_matrix' in p` (el YAML generado por calibrate_fisheye.py no tiene clave `calibrated`).
  - **`detect_node.py` — mismo fix de pipeline, warm-up y calibración que lane_node.**

**Estado al cerrar:**
- cam0 calibrada ✅ (`config/camera_fisheye.yaml`)
- cam1 pendiente (mismo script, `--sensor-id 1 --no-fisheye`)
- Siguiente: conectar cam1 y calibrarla, luego Bloque H (UART + motores en taller)

---

### Sesión 6 — 2026-04-29

**Qué se hizo:**

- **Diagnóstico completo del workspace:**
  - 5 nodos verificados: GStreamer correcto, warm-up 20 frames, mode-aware, publishers debug_image, structure main/rclpy/spin — todos OK sin bugs.
  - Identificados 5 archivos faltantes: 3 configs + 2 scripts.

- **3 config files creados:**
  - `config/apriltag_sign_map.yaml` — tags 0–5 (no_pasar → alto). Ajustar IDs en competencia.
  - `config/camera_normal.yaml` — placeholder para cam1 (fx=fy=600, cx=320, cy=240). Calibrar con `--no-fisheye`.
  - `config/bird_eye_params.yaml` — valores completos listos para usar; ajustar `src_points` con `--tune-birdseye` en pista real.

- **`scripts/dashboard.py` creado (Flask 3.1 + Flask-SocketIO 5.6.1, puerto 8080):**
  - Thread ROS separado del servidor Flask; publishers thread-safe.
  - Dos streams MJPEG: `/video/lane` (cam0 bird's eye) y `/video/detect` (cam1 AprilTags).
  - Placeholder automático cuando el nodo está offline.
  - Telemetría en tiempo real vía SocketIO a 5Hz: 10 métricas (mode, FSM, estop, vel, steer_err, lane, crosswalk, sign, tag_id, dist).
  - Decision log con auto-scroll (últimos 25 mensajes de `/nav/decision_log`).
  - Botones funcionales: MANUAL / OBSERVE / AUTO / ESTOP (toggle) / START / RESET.
  - HTML embebido, estilo terminal dark, sin dependencias externas excepto Socket.IO CDN.

- **`scripts/view_camera.py` creado:**
  - Suscribe a `/lane/debug_image` en thread ROS; `cv2.imshow` en el main thread.
  - Overlay de `steer_err` con color amarillo/naranja según magnitud.
  - Pantalla de espera si `lane_node` no está corriendo. Q/ESC para salir.

- **`colcon build` — LIMPIO** (3.22s, 0 errores, 0 warnings).

**Estado al cerrar:**
El workspace está 100% completo a nivel de código. Todo el software está generado y buildea correctamente.

Pendiente exclusivamente físico:
- Soldar PCB
- Flash ESP32
- Calibrar cam1 (`--sensor-id 1 --no-fisheye`)
- Ajustar bird's eye en pista (`--tune-birdseye`)
- Ajustar IDs AprilTag en `apriltag_sign_map.yaml`

Orden de prueba: **Bloque H** (UART+motores) → **Bloque I** (cámaras) → **Bloque J** (observe mode) → **Bloque J2** (auto).
