# PROMPT PARA CLAUDE CODE — RoboUAQ 2026
# Copia y pega esto como primer mensaje en Claude Code dentro de ~/robocar_ws/

Lee CLAUDE.md primero. Ese archivo tiene toda la especificación del proyecto.

IMPORTANTE: El archivo `teleop_xbox_node.py` ya existe en el workspace (fue escrito a mano). NO lo reescribas. Solo asegúrate de incluirlo en setup.py como entry point.

Crea el workspace ROS 2 completo con estos archivos en orden de prioridad:

## Paso 1: Estructura base
Crea la estructura de paquete ROS 2 Python en `src/robocar/` con package.xml y setup.py. El paquete se llama `robocar`, usa `ament_python`. Incluye TODOS estos entry points en setup.py:
- teleop_xbox_node = robocar.teleop_xbox_node:main
- serial_bridge_node = robocar.serial_bridge_node:main
- lane_node = robocar.lane_node:main
- detect_node = robocar.detect_node:main
- nav_node = robocar.nav_node:main

Dependencias en package.xml: rclpy, std_msgs, sensor_msgs, cv_bridge.

## Paso 2: serial_bridge_node.py
Nodo puente UART ↔ ROS 2. Lee el protocolo del CLAUDE.md. Usa pyserial con thread de lectura no-bloqueante. Puerto: `/dev/ttyTHS1`. Fallback para debug: `/dev/ttyUSB0`. Incluye manejo de errores de conexión y reconexión automática.
- También suscribirse a `/estop` (Bool): si True, forzar V0,S1500 ignorando /cmd_vel y /cmd_steer.

## Paso 3: lane_node.py
Visión por computadora para seguir la línea. Camera 0 (fisheye CSI). Pipeline:
1. GStreamer capture (nvarguscamerasrc sensor-id=0)
2. Cargar calibración fisheye de config/camera_fisheye.yaml (si existe, sino usar raw)
3. Undistort → crop ROI inferior (60% inferior de la imagen)
4. Bird's eye view warp (4 puntos configurables en yaml)
5. HSV mask para líneas OSCURAS (cinta de aislar) sobre fondo CLARO (vinilo)
6. Encontrar contornos → centroide → calcular error de steering (-1.0 a +1.0)
7. Detectar CROSSWALK: múltiples contornos horizontales agrupados (paso de cebra)
8. Publicar error + flag de línea detectada + flag crosswalk detectado
9. Publicar debug image (CompressedImage) con overlay: bird's eye + contornos + flecha de dirección + crosswalk marcado

MODE-AWARE: suscribirse a `/mode`. En "manual" → no procesar (ahorrar CPU). En "observe" o "auto" → correr pipeline completo.

Robusto: si no detecta línea, publicar último valor conocido por 0.5s, luego flag detected=False.

## Paso 4: detect_node.py
Detección de señales con Camera 1 (sensor-id=1). Estrategia DUAL:
- PRIMARIO: AprilTag 36h11 con `dt_apriltags`. Mapeo tag_id→señal desde config/apriltag_sign_map.yaml.
- SECUNDARIO: YOLO si hay modelo disponible (models/signs/best.pt o best.engine).

Las 6 señales oficiales:
1. no_pasar → no entrar a esa calle
2. sin_salida → no entrar a esa calle
3. vuelta_derecha → girar a la derecha en intersección
4. vuelta_izquierda → girar a la izquierda en intersección
5. siga_de_frente → seguir recto
6. alto → DETENERSE (línea de meta)

Publica: `/detection/sign` (String), `/detection/tag_id` (Int32), `/detection/distance` (Float32).
Publica debug image (CompressedImage): frame con bounding boxes de AprilTags, tag ID, nombre de señal, distancia sobrepuesta.

MODE-AWARE: suscribirse a `/mode`. En "manual" → no procesar. En "observe" o "auto" → detectar y publicar.

## Paso 5: nav_node.py
FSM según el CLAUDE.md ACTUALIZADO.
- **REGLA CRÍTICA: detenerse 3 segundos antes de cada cruce de calles (regla 1.7)**
- Estados: IDLE, FOLLOW_LANE, APPROACHING_INTERSECTION, STOPPED_AT_INTERSECTION, EXECUTE_TURN, FINISH
- Detecta intersección por: crosswalk_detected (del lane_node) O AprilTag en rango (<1m)
- Se detiene 3s, lee la señal, ejecuta la maniobra correspondiente
- "alto" = meta → para permanentemente
- Si 20s sin moverse → log warning (regla 3.10)
- Cooldown de 5s por tag_id detectado
- Publica: `/cmd_vel`, `/cmd_steer`, `/nav/state` (String con nombre del estado), `/nav/decision_log` (String con log legible tipo "Detected vuelta_derecha at 0.8m → turning right")
- Suscribirse a: `/nav/start` (Bool), `/nav/reset` (Bool), `/estop` (Bool)

MODE-AWARE (CRÍTICO):
- Suscribirse a `/mode`.
- "manual" → FSM pausada, no procesar nada
- "observe" → FSM corre normalmente, calcula decisiones, publica `/nav/state` y `/nav/decision_log`, PERO **NO publica a /cmd_vel ni /cmd_steer**. El Xbox controla el carro mientras tú ves qué haría el sistema autónomo.
- "auto" → FSM corre Y SÍ publica a /cmd_vel y /cmd_steer. Control autónomo completo.

## Paso 6: Launch files (TRES archivos)

### teleop_launch.py
Lanza solo: teleop_xbox_node + serial_bridge_node.
Modo por defecto: "manual". Para probar el carrito con Xbox sin visión.

### observe_launch.py
Lanza TODOS los nodos: teleop_xbox_node + serial_bridge_node + lane_node + detect_node + nav_node.
Modo por defecto: "observe". Manejas con Xbox pero ves todo lo que haría el autónomo.

### robocar_launch.py
Lanza TODOS los nodos. Modo por defecto: "auto". Para la competencia.
Incluye parámetros configurables: camera IDs, UART port, velocidades.

## Paso 7: Config files placeholder
- `config/camera_fisheye.yaml` — calibración vacía con estructura correcta
- `config/camera_normal.yaml` — calibración vacía
- `config/bird_eye_params.yaml` — 4 puntos del trapecio con valores default razonables para cámara a ~15cm de altura
- `config/apriltag_sign_map.yaml` — mapeo tag_id→señal:
  ```yaml
  tags:
    0: no_pasar
    1: sin_salida
    2: vuelta_derecha
    3: vuelta_izquierda
    4: siga_de_frente
    5: alto
  ```

## Paso 8: Script de calibración
`scripts/calibrate_fisheye.py` — captura 20 imágenes de tablero de ajedrez (9x6), calcula matriz de calibración fisheye, guarda a yaml. Acepta `--sensor-id` y `--no-fisheye` para cámara normal.

## Paso 9: Web dashboard
`scripts/dashboard.py` — Flask + SocketIO en puerto 8080:
- Pestaña Cam0: stream MJPEG de `/lane/debug_image`
- Pestaña Cam1: stream MJPEG de `/detection/debug_image`
- Pestaña PID: gráfica tiempo real velocidad target vs odom
- Pestaña FSM: estado actual + log de decisiones (`/nav/decision_log`)
- Pestaña Control: botones MANUAL/OBSERVE/AUTO, ESTOP, START, RESET (publican a los topics correspondientes)
- Muestra velocidad actual, steering, modo activo

## Reglas generales:
- Python 3.10, ROS 2 Humble
- NO uses cv2.VideoCapture(int) para CSI — usa GStreamer pipeline strings
- Maneja gracefully la ausencia de cámaras/joystick (para probar en PC sin hardware)
- Cada nodo debe funcionar independientemente (para testing gradual)
- Usa logging de ROS 2 (self.get_logger().info/warn/error)
- Incluye modo dry-run/simulación donde sea posible
- Comenta en español las secciones importantes
- Las marcas de pista son cinta de aislar (oscura) sobre vinilo (claro)
- AprilTags son el método primario de detección, YOLO es secundario
- TODOS los nodos (excepto serial_bridge y teleop) deben suscribirse a `/mode` y respetar los 3 modos
- Los debug images son CompressedImage (JPEG) para eficiencia de ancho de banda
