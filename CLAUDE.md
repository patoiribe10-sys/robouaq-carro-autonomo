# Carro autónomo RoboUAQ — contexto para Claude Code en la Jetson

Última actualización: 26-nov-2026. Este archivo reemplaza al CLAUDE.md viejo (el de hace un año, de la placa de KiCad). El viejo quedó como `CLAUDE_viejo.md` por si hace falta consultarlo, pero lo que diga ahí sobre la placa ya no aplica.

---

## Quién y para qué

- Dueño: Alain Iribe Haddad ("Pato"), estudiante de Ingeniería Mecatrónica en la Universidad Anáhuac Querétaro, equipo RoboUAQ.
- Proyecto: carro RC escala 1:10 autónomo para la competencia "Robot Conducción Urbana" (UAQ). La siguiente edición es en **mayo de 2027**.
- Reto: recorrer un circuito urbano en menos de 5 minutos, respetando señales y esquivando obstáculos. Solo se permite visión (cámaras).
- También hay un artículo del proyecto completo, así que todo lo que se pruebe se documenta (fotos, mediciones, decisiones, fallas).

**Orden de trabajo que definió Pato:**
1. PCB v2 y curso → ya terminado.
2. **Pruebas básicas en manual: control de Xbox, sin IA ← estamos aquí.**
3. Después, conducción autónoma con visión.
4. Documentar todo en el camino.

---

## Reglas para trabajar con Pato (respetarlas siempre)

- Responder **en español**. Explicar en sencillo lo que no sea obvio; no da por hecho que entienda todo.
- Textos humanos, que no suenen a IA.
- **Subagentes:** preguntar primero si se usan. Si dice que sí: **Opus para planear y hacer debug, Sonnet para programar y explorar. Nunca Haiku.**
- **NO subir firmware al ESP32 hasta que Pato diga "súbelo".** Compilar sí se puede.
- Commits con `Co-Authored-By: Claude ...` y **sin** la línea `Claude-Session`.
- Prefiere archivos completos listos para usar, no parches con `sed`.
- Probar por partes: servo, luego motor, luego juntos, y hasta después ROS.
- Antes de algo difícil de revertir (borrar, reinstalar, flashear, cambiar configuración del sistema), preguntar.
- La contraseña de la Jetson la escribe Pato; no pedirla ni guardarla.
- Al cerrar ("cierra sesión"): agregar al final de este archivo una entrada con fecha de lo que se hizo y lo que sigue (sin borrar las anteriores), y dar a Pato un prompt de continuación.

---

## Objetivos de esta etapa

1. Jetson al 100 y comunicada con la laptop de Pato (Windows 11, VS Code con PlatformIO).
2. Revisar todo `~/robocar_ws` (código de hace un año, hecho para la placa vieja) y decidir qué se reaprovecha, qué se corrige y qué se reescribe.
3. Reescribir lo necesario:
   - ESP32: C con estructuras y apuntadores (PlatformIO, Arduino-ESP32 3.x).
   - Jetson: Python con ROS 2 Humble, pyserial y el OpenCV del sistema.
4. Flujo laptop → Jetson → ESP32 para programar y probar a la vez: editar desde la laptop (VS Code Remote-SSH), compilar y subir el firmware desde la Jetson (PlatformIO CLI, entorno `jetson`, por `/dev/esp32`), ver serial y logs desde la laptop.
5. **Meta:** mover el carro con el control de Xbox conectado a la Jetson por Bluetooth y ver las dos cámaras por WiFi en el navegador de la laptop.

---

## Jetson

- Jetson Orin Nano, nombre "Stitch", usuario `patoih`. JetPack 6.2 (L4T R36.4.7), Ubuntu 22.04, ROS 2 Humble, Python 3.10.
- **No actualizar Ubuntu a 24.04**: JetPack 6 y ROS 2 Humble son de 22.04.
- La IP cambia según la red (ver "Cómo entrar a la Jetson" abajo). La que tenía en la escuela (`172.26.169.39`) ya no sirve.
- Red por USB-C: debería aparecer en la laptop como `192.168.55.1` (servicio `nv-l4t-usb-device-mode`). El 25-nov no apareció; está pendiente averiguar por qué.
- Alimentación: desde J5 de la PCB v2 (+10 V, medido 9.8 V, no baja con carga). Plug de barril centro positivo. Con la batería abajo de ~11 V se pierde margen. En el banco también puede ir a su eliminador.
- Modos de energía (`/etc/nvpmodel.conf`): **`-m 0` = 15W, `-m 1` = 25W, `-m 2` = MAXN_SUPER (sin tope de CPU/GPU; es el modo actual), `-m 3` = 7W.** Ojo: antes este archivo decía que `-m 2` era 25 W; es MAXN_SUPER, **25 W es `-m 1`**. Para la competencia se decide en la fase F4 (ver "Optimización de la Jetson"): MAXN_SUPER con `sudo jetson_clocks` para latencia fija, o `-m 1` si la batería lo pide. Hoy no se ha cambiado el modo.
- Control Xbox One: emparejado por Bluetooth y aparece como `/dev/input/js0` (verificado el 26-nov). No hace falta xpadneo: se va a leer con `game_controller_node` del paquete `joy` (ros-humble-joy 3.3.0).

### Cómo entrar a la Jetson (la IP fija ya no es la única forma)
1. **Tailscale: funciona desde cualquier red con internet** (casa, escuela, hotspot). La Jetson se llama `stitch` en la tailnet y su IP es `100.97.122.71`. La laptop también tiene que tener Tailscale con la misma cuenta. Entrar con `ssh patoih@stitch` o `ssh patoih@100.97.122.71`; en VS Code Remote-SSH, el mismo host.
2. **Red local: IP o `stitch.local`**, con la laptop en la misma red. La IP actual en la red de la casa es `192.168.1.3`, pero cambia de una red a otra, así que es mejor `stitch.local` (mDNS con `avahi-daemon`, resuelve a `192.168.1.3`). Avahi lleva `deny-interfaces=docker0` en `/etc/avahi/avahi-daemon.conf` (respaldo `.bak`) porque si no anunciaba la IP de Docker (`172.17.0.1`) en lugar de la de la WiFi.
3. **Hotspot del celular de Pato.** La Jetson lo tiene guardado y se conecta sola cuando no hay una red mejor. La laptop también debe conectarse a ese hotspot; luego se entra por `stitch.local`, por la IP que le dé el celular o por Tailscale.

La Jetson elige sola la mejor red conocida que encuentre: primero la de la casa (prioridad 30), luego la de la escuela (20) y al final el hotspot del celular de Pato (10). La red de invitados de la escuela está guardada pero sin autoconexión. Los perfiles de la escuela y del hotspot usan la MAC real de la tarjeta (`cloned-mac-address permanent`) para que la escuela siga reconociendo a la Jetson (registrada en Lions-Net por MAC; el valor no se guarda en el repo, se ve con `ip link show wlP1p1s0`). Las contraseñas de WiFi las guarda NetworkManager en archivos que solo lee root; nunca van en el repo ni en este archivo.

Detalles de Tailscale: instalado con el script oficial (agrega el repositorio apt de `pkgs.tailscale.com`). `tailscale status` avisa que faltan las reglas `connmark` de iptables (el kernel de Tegra no trae ese módulo); no afecta entrar por SSH, solo funciones como subnet router o exit node, que no se usan. La caducidad de la llave de `stitch` ya está desactivada (Pato, 26-nov, la consola dice "Expiry disabled"); por defecto caducaría a los 180 días, hacia fines de mayo de 2027, justo en la competencia.

### Arranque automático del carro (`robocar.service`) — INSTALADO y habilitado (Pato, 26-nov 17:13); prueba de arranque en frío PENDIENTE
Meta (Pato, 26-nov): prender el switch con la Jetson en la batería (J5), esperar, y que la web y el control ya estén corriendo **sin entrar por SSH**. Archivos: `systemd/robocar.service` (servicio del sistema, `User=patoih`, grupos `input video bluetooth dialout`, `Restart=on-failure` cada 5 s, sin límite de reinicios, `KillSignal=SIGINT`/`KillMode=mixed`, logs al journal) y `scripts/arranque_carro.sh` (espera con tope de 30 s a una IP de WiFi o cable, al Bluetooth encendido y a `/dev/esp32`; si algo no aparece **sigue de todos modos**, porque la red y el control pueden llegar después y el puente reintenta solo abrir el ESP32 cada 2 s; luego `exec ros2 launch robocar control_remoto_launch.py` en el dominio ROS 0 con logs de ROS en `/dev/shm`). El mapa SDL propio lo pasa el propio launch. `NetworkManager-wait-online` está deshabilitado: `network-online.target` no esperaría nada, por eso la espera de red es del script.
- **Control:** emparejado, `Trusted: yes` y `Paired: yes`; `AutoEnable=true` en `/etc/bluetooth/main.conf`; se reconecta solo al prenderlo. **Sin verificar:** qué hace `game_controller_node` si el control está apagado cuando arranca (mi prueba con `device_id` inexistente no sirvió: encontró el control igual).
- **Instalado como servicio real del sistema (26-nov 17:13, Pato con sudo; idéntico al del repo):** arrancó solo con sus grupos y sin sesión de por medio: `game_controller_node` encontró el control, el puente abrió el ESP32 (`pong`, 12.65 V), la web respondió 200 y **cam0 abrió desde el servicio** (51 cuadros en 5 s = 10 fps, se cierra sola al irse el espectador, `nvargus-daemon` con el mismo PID). Con el control **desconectado y vuelto a conectar** con el servicio corriendo, `game_controller_node` lo recuperó solo ("CONTROL ENCONTRADO" otra vez, `Volvio /joy`); eso **no** es la prueba de arrancar sin control.
- **Redes guardadas (revisado, solo lectura):** las dos con contraseña (casa y hotspot del celular) tienen `psk-flags=0` (la guarda el sistema) y `permissions` vacío; Lions-Net y la de invitados no llevan clave. Ninguna depende de la sesión del escritorio.
- **PRUEBA DE ARRANQUE EN FRÍO (pendiente):** (1) apagar el control; (2) `sudo reboot`; (3) esperar ~40 s SIN prender el control y correr `python3 ~/robocar_ws/scripts/revisar_arranque.py` (debe decir web lista y `CONTROL: NO ha sido encontrado`); abrir la web desde la laptop; (4) prender el control, esperar ~10 s y volver a correr el script: debe decir `arrancó SIN control y lo encontró DESPUÉS` y `llegó /joy`; (5) con ruedas al aire, LB + sticks. Si el control queda `Connected: yes` y tras ~15 s no hay "CONTROL ENCONTRADO", `game_controller_node` no detecta controles tardíos: habría que reiniciar el servicio cuando el control se conecte (p. ej. regla udev o un vigilante). Log a mano: `journalctl -u robocar -b --no-pager | grep -E "arranque_carro|Controller Found|No llega /joy|Volvio /joy|Cámaras listas|Puerto abierto|pong"`. Tiempos reales: `systemd-analyze` y `systemd-analyze critical-chain robocar.service`.
- **Ensayo sin instalar (`systemd-run --user`, ESP32 y control reales):** la web responde a los **2.7 s** de arrancar; con `kill -9` al launch se reinicia solo y la web vuelve a los **7.7 s** (5 s de `RestartSec`), sin procesos huérfanos; `stop` cierra en **0.6 s** con los 4 nodos `finished cleanly`. Lo que NO se pudo probar sin sudo: el entorno real de un servicio del sistema (sin sesión gráfica, grupos por `SupplementaryGroups`).
- **Comandos** (los de lectura no piden sudo: `patoih` está en `adm`):
  - Instalar y activar: `sudo cp ~/robocar_ws/systemd/robocar.service /etc/systemd/system/ && sudo systemctl daemon-reload && sudo systemctl enable --now robocar`
  - Ver estado: `systemctl status robocar` · log en vivo: `journalctl -u robocar -f` · log de este arranque: `journalctl -u robocar -b --no-pager`
  - Reiniciar: `sudo systemctl restart robocar` (obligatorio después de cada `colcon build`) · parar: `sudo systemctl stop robocar` · arrancar: `sudo systemctl start robocar`
  - Desactivar (no arranca al prender) y parar: `sudo systemctl disable --now robocar` · quitarlo del todo: `sudo systemctl disable --now robocar && sudo rm /etc/systemd/system/robocar.service && sudo systemctl daemon-reload`
  - **Antes de subir firmware, de `prueba_serial.py` o de lanzar el launch a mano: `sudo systemctl stop robocar`** (si no, dos puentes pelean por `/dev/esp32` y `pio upload` falla). `scripts/prevuelo.sh` lo avisa.
- **Medir el arranque real** (después de instalarlo y reiniciar): `systemd-analyze` · `systemd-analyze critical-chain robocar.service` · `journalctl -u robocar -b -o short-monotonic --no-pager | grep -E "arranque_carro|Cámaras listas|Puerto abierto"` (los segundos son desde el inicio del kernel; la web responde al aparecer `Cámaras listas`).
- **Estimación previa** (arranque del 26-nov, ANTES de apagar Docker y demás de F2; **no es una medición del servicio**): dependencias listas — NetworkManager y Bluetooth a los 14.1 s, `nvargus-daemon` a los 17.0 s, ESP32 (`ch34x attached`) a los 22.6 s del kernel — y la web a los 2.7 s del inicio del servicio. Esperar al ESP32 haría arrancar el servicio hacia los ~22.6 s (web hacia los ~25 s); sin esa espera sería hacia los ~17 s (web hacia los ~20 s). No se pudo recuperar cuándo tuvo IP la WiFi. El arranque debería salir más corto que los 22.7 s medidos (Docker costaba 4.6 s).
- **Opcional, NO hecho:** arrancar sin escritorio (`sudo systemctl set-default multi-user.target`). Ahorra unos **1.4 GB** de RAM (RSS de gnome-shell 381 MB, Xorg 173 MB, gjs 110 MB y demás; cota superior, cuenta páginas compartidas) y quita carga de CPU/GPU durante el arranque. El servicio no depende del escritorio. Se revierte con `set-default graphical.target`.

### Software viejo en `~/robocar_ws` (revisar, no confiar)
- Actualización del 26-nov: `serial_bridge_node`, `teleop_xbox_node`, `launch/teleop_launch.py`, `esp32_firmware/`, `test_uart.py` y los prompts y planes de abril se archivaron en `archivo_viejo/` (siguen en el historial de git). El puente y el teleop se reescriben con el protocolo de `carro-v2`.
- Paquete `robocar` con los nodos `teleop_xbox_node`, `serial_bridge_node`, `lane_node`, `detect_node` y `nav_node`; dashboard Flask + SocketIO en el puerto 8080; scripts `calibrate_fisheye.py` y `view_camera.py`; launch files; `PROMPT_CLAUDE_CODE.md`.
- `colcon build` compilaba sin errores y el teleop del Xbox publicaba `/cmd_vel`.
- Calibración de cam0 (ojo de pez) hecha: tablero de 5×7 esquinas interiores, RMS 0.6417 px, en `config/camera_fisheye.yaml`.
- Modos de operación pensados: MANUAL (solo Xbox), OBSERVE (Xbox maneja y la visión mira) y AUTO.
- FSM pensada: IDLE → FOLLOW_LANE → APPROACHING_INTERSECTION → STOPPED_AT_INTERSECTION → EXECUTE_TURN → FINISH.
- **Escrito para la placa de KiCad**: STBY puenteado a mano, UART del header abandonado (TX2 a 0.54 V), comunicación por USB, PWM ×85 sin PID y arrancón a 255 por 150 ms. En la v2 eso cambia (ver abajo).

### Trampas conocidas en la Jetson (costaron mucho aprenderlas)
- **Nunca `pip3 install opencv-python`.** Tapa al OpenCV del sistema y se pierde GStreamer. Si aparece, desinstalarlo.
- Pipeline de cámara que funciona (con 20 cuadros de calentamiento):
  `nvarguscamerasrc sensor-id=0 ! video/x-raw(memory:NVMM),width=640,height=480,format=NV12,framerate=30/1 ! nvvidconv ! video/x-raw,format=BGRx ! videoconvert ! video/x-raw,format=BGR ! appsink sync=false drop=true max-buffers=1`
- Arreglo de ldconfig para GStreamer: `/etc/ld.so.conf.d/000_a_nvidia-tegra.conf` se llama así para ordenarse antes de `000_cuda.conf` (si no, la libnvjpeg de CUDA tapa la de Jetson). No tocarlo.
- El usuario debe estar en el grupo `video` (si no, error de NvBufSurface con las cámaras).
- Driver CH340 (el USB-serial del ESP32): no viene en el kernel, pero `ch34x.ko` (compilado en `~/CH341SER`) ya está instalado en `/lib/modules/5.15.148-tegra/kernel/drivers/usb/serial/` y listado en `/etc/modules`, así que se carga solo en cada arranque. Ya no hace falta `insmod` ni un servicio. **Si algún día se actualiza el kernel, hay que recompilarlo** en `~/CH341SER` (el `vermagic` debe coincidir con `uname -r`). Por eso, desde el 26-nov, los seis paquetes de kernel están congelados con `apt-mark hold`: `nvidia-l4t-kernel`, `nvidia-l4t-kernel-dtbs`, `nvidia-l4t-kernel-oot-modules`, `nvidia-l4t-display-kernel`, `nvidia-l4t-kernel-headers` y `nvidia-l4t-kernel-oot-headers` (se ven con `apt-mark showhold`). **Para actualizar JetPack hay que quitar el hold a propósito** (`sudo apt-mark unhold <los seis>`), actualizar y **recompilar `ch34x.ko`** en `~/CH341SER` antes de reiniciar; si no, el ESP32 deja de aparecer. Ojo: actualizar el resto de `nvidia-l4t-*` sin el kernel puede dejarlo desfasado. Al conectar el ESP32 el kernel imprime un `WARNING: transfer buffer is on stack` de este driver; es conocido y no impide que funcione.
- El ESP32 aparece siempre como `/dev/esp32` (regla `/etc/udev/rules.d/99-esp32.rules`, que además le dice a ModemManager que lo ignore). El CH340 de la placa no trae número de serie: si se conecta otro CH340 al mismo tiempo, también coincide con la regla.
- `brltty` roba `/dev/ttyUSB0`; ya está desactivado.
- **Abrir el serial siempre con `dsrdtr=False, rtscts=False`** en pyserial. Si no, la línea DTR reinicia al ESP32.
- Librerías instaladas: ultralytics, dt-apriltags, pyserial, smbus2, Flask, Flask-SocketIO.

### Optimización de la Jetson (plan del 26-nov, una fase a la vez)
Objetivo: que `nvargus-daemon` no se quede sin memoria al manejar con las dos cámaras. **Nunca se tocan:** ssh, tailscaled, NetworkManager, bluetooth, avahi, nvargus-daemon, udev y la regla `/dev/esp32`, el hold del kernel, nada de NVIDIA/CUDA ni `systemd-timesyncd`. Herramientas (en `scripts/`): `medir_jetson.sh "<etiqueta>" [seg]` (solo lectura; agrega una línea a `mediciones_jetson.tsv`), `optimizar_jetson.sh <fase> [--aplicar]` (sin `--aplicar` solo muestra los comandos) y `revertir_optimizacion.sh <fase> [--aplicar]`. No hay swap file a propósito: la raíz es una microSD y el fallo de `nvargus` es memoria del kernel que no se manda a swap; el zram de 3.8 GB (de NVIDIA) se queda.

| Fase | Qué | RAM usada antes → después | Estado |
|---|---|---|---|
| F0 | Cerrar Epiphany/WebKit y Chromium | 3 503 → **1 973 MB** (disponible 3 835 → 5 367; **CmaFree 14–21 → 190 MB**, GPU 0–36 % → 8 %) | hecha |
| F1 | Overrides de autostart del usuario (gnome-software, Evolution, update-notifier, DejaDup, print-applet, onboard, orca, spice, ubuntu-advantage/report, tracker) y `systemctl --user mask` de tracker, gnome-remote-desktop y los 3 servicios de Evolution | 1 973 → **1 739 MB** (−234), servicios de usuario 43 → 38 | hecha |
| F2a | Docker y containerd (`disable --now`, sin desinstalar) | 1 739 → **1 713 MB** (−26; el RSS de 143 MB contaba páginas compartidas), servicios 50 → 48. `docker0` sigue hasta reiniciar; el ahorro de ~4.6 s de arranque se verá al reiniciar | hecha (Pato, con sudo) |
| F2b | `packagekit` y `fwupd` (mask/disable) | 1 713 → **1 665 MB** (−48), servicios 48 → 46. `apt` sigue funcionando | hecha (Pato, con sudo) |
| F2c | ModemManager, rpcbind, kerneloops, lpd y snap `cups` | 1 665 → **1 641 MB** (−24), servicios del sistema 46 → **41**, **el puerto 111 ya no escucha**. La regla `ID_MM_DEVICE_IGNORE` del ESP32 se conservó. `lpd` quedó deshabilitado pero su proceso (0.5 MB) sigue hasta reiniciar | hecha (Pato, con sudo) |
| F2d | geoclue, colord, switcheroo-control, power-profiles-daemon | 1 641 → 1 662 MB bruto, pero Claude Code creció 487 → 508 MB; sin contar Claude queda igual (1 154 MB). **No libera memoria medible**; servicios del sistema 41 → **38**. Hora sincronizada, GNOME sano | hecha (Pato, con sudo) |
| F2e | timers de apt, motd-news, update-notifier y man-db | sin efecto de memoria (1 646 MB); servicios del sistema 38 → 37. Evita que apt corra solo a mitad de una prueba; las listas hay que refrescarlas a mano con `sudo apt update` | hecha (Pato, con sudo) |
| F3 | `vm.min_free_kbytes` 45 056 → 262 144 + prueba de estrés de cámara midiendo también `CmaFree` | **Línea base con estrés (`scripts/prueba_estres_camara.py`): 40 de 40 ciclos OK**, con `MemFree` bajando hasta **75 MB** y `CmaFree` hasta **0 MB**, 0 reinicios del daemon, 0 errores de memoria. El fallo del 26-nov **no se reprodujo**. Abrir cam0 cuesta ~300 MB de `MemFree` y 0 de CMA | **no aplicada** (sin beneficio demostrado); el archivo `99-carro.conf` no existe. Se aplicaría solo si el fallo vuelve a aparecer |
| F4 | Modo de energía (medir 0/1/2 con y sin `jetson_clocks`) | — | pendiente; no se cambia sin OK |
| F5 | Modo texto en los días de manejo | — | **no por ahora** |

Hallazgo de F0: con el navegador cerrado la memoria contigua (CMA) pasó de 14–21 MB libres a 190 MB. Con Chromium abierto el 26-nov solo había 4 MB cuando falló la cámara. **Pero la prueba de F3 descartó que la CMA o la RAM libre normal sean la causa por sí solas:** con `CmaFree` en 0 MB y `MemFree` en 75 MB la cámara abrió 40 de 40 veces. El navegador sigue siendo el sospechoso, y la hipótesis es que el recurso que se agotó es la memoria de GPU/NvMap que ocupan sus procesos de render (no se ve en `MemFree` ni en `CmaFree` y `/sys/kernel/debug/nvmap` pide root). Para reproducirlo habría que abrir la cámara con un navegador pesado abierto. **No hace falta tocar `extlinux.conf`.** Cada fase se revierte con `scripts/revertir_optimizacion.sh <fase> --aplicar`. Reposo de referencia (F1): 5 601 MB disponibles, CPU 5 %, 4.1 W, 47 °C.

---

## Hardware del carro

- Chasis 1:10 con diferencial. Dirección Ackermann con servo MG995 (directo, sin PCA9685).
- Motor de tracción: GM25-370 con encoder. Está gastado y lento; cambiarlo es opcional. El candidato es un GM25-370 12 V 330 RPM con encoder (UNIT Electronics).
- Batería LiPo 3S (11.1 V nominal, 12.4 V cargada).
- Cámaras: 2 × IMX219 CSI. cam0 = ojo de pez arriba viendo hacia abajo (carriles); cam1 = normal al frente (señales). Para cam1 falta un cable CSI (adaptador de 15 a 22 pines).

### PCB v2 "Autonomo_V2" (Altium, PCBWay) — la placa actual
100 × 100 mm, 4 capas. Llegó el 23-nov-2026 y ya está montada y probada.

| Bloque | Qué hace |
|---|---|
| Entrada | XT60 → fusible 10 A → switch en P2 → protección de inversión → VBAT. Sin switch o puente en P2 no prende |
| +5V lógica | TPS565201, 5 A |
| +3V3 | AP2112K, para el ESP32 |
| +VJET | TPS54560B, 10 V para la Jetson por J5 |
| +5V servo | TPS54560B propio (~5.04 V) con 1000 µF |
| ESP32 | ESP32-WROOM-32E integrado (no es DevKit) |
| USB | USB-C (J1) con CH340C. JP1 = auto-reset con dos jumpers |
| Motor | TB6612FNG con los canales A y B en paralelo |
| Paro de emergencia | Lazo normalmente cerrado en J10 (3-4). Si se abre, corta solo la batería del motor; servo, ESP32 y Jetson siguen vivos |
| Extras | buzzer (J8), luces (J9, voltaje de batería), tira LED WS2812 (J7), 2 × I2C (J11, J12), LED HB |

Voltajes medidos el 25-nov: VBAT 12.4 V, 3V3 3.3 V, 5V 5.0 V, servo 5.04 V, Jetson 9.8 V.

**Pines del ESP32 (fuente: `firmware/plantilla/include/pines_v2.h` del repo):**

| GPIO | Función |
|---|---|
| 34 / 35 | Encoder A / B |
| 25 | Servo (buffer a 5 V → J3) |
| 23 | PWM del motor |
| 21 / 22 | IN1 / IN2 del motor |
| 19 | STBY del TB6612 (HIGH = habilitado; en la v2 ya tiene pull-up) |
| 16 / 17 | UART2 RX / TX → J4 (para después) |
| 18 | HB hacia la Jetson (J4.4) |
| 36 | VBAT por divisor 100k/22k: V = mV × 5.545 |
| 39 | Paro por ADC, mismo divisor: > 5 V = paro activo |
| 27 | START (LOW = presionado) |
| 0 | BOOT (se puede leer como botón) |
| 2 | LED HB |
| 13 | Buzzer |
| 14 | Luces |
| 26 | Tira LED |
| 32 / 33 | I2C SDA / SCL |

**Servo: 1190 µs izquierda, 1360 centro, 1680 derecha (era 1730 hasta el 26-nov: en 1730 se forzaba; Pato confirmó que con 1680 llega al tope sin forzar). Nunca mandar 1100: se fuerza contra el tope.** `SERVO_VEL_US_S` = 3500 µs/s. Subido al ESP32 el 26-nov (noche); `plantilla`, `demo-recorrido` y `prueba-pcb` siguen con 1730.

ESP32Servo: llamar `ESP32PWM::allocateTimer(0)` antes de `ledcAttach` del motor (conflicto de timers).

### Cómo se conecta la Jetson con el ESP32
- **Ahora:** cable USB de la Jetson al USB-C J1 de la placa → `/dev/esp32` (regla udev `99-esp32.rules`, apunta a `/dev/ttyUSB0`), 115200.
- **PlatformIO en `firmware/carro-v2`:** hay dos entornos. `esp32doit-devkit-v1` (el de por defecto, sin puerto fijo) es el de la laptop con Windows y el botón Upload de VS Code. `jetson` (extiende al anterior con `upload_port` y `monitor_port` = `/dev/esp32`) es el de la Jetson: `pio run -e jetson -t upload` para subir y `pio device monitor -e jetson` para ver el serial. Los otros cuatro proyectos no llevan puerto fijo.
- **Después:** J4 al header de 40 pines de la Jetson → `/dev/ttyTHS1` (Serial2 del ESP32). J4.1 GND → pin 6, J4.2 TX → pin 10, J4.3 RX → pin 8. **Nunca correrlo a los pines 4-6-8-10: el pin 4 es 5 V.**
- Con los jumpers de JP1 puestos se puede programar sin tocar botones, pero el DTR puede reiniciar al ESP32 (ver la regla de `dsrdtr=False`). Programar a mano: BOOT presionado, toque a EN, soltar BOOT.
- Cerrar cualquier monitor serial antes de subir firmware.

### Protocolo Jetson ↔ ESP32 (firmware nuevo `firmware/carro-v2`, compila; falta subirlo y probarlo)
- Jetson → ESP32: `V<vel>,S<dir>\n` con vel y dir de -100 a 100 (vel negativa = reversa; dir -100 = izquierda total, 0 = centro, 100 = derecha total).
- ESP32 → Jetson (~20 Hz): `v<pulsos/s>,s<dir>,b<mV batería>,e<estado>\n` con e = 0 ok, 1 paro de emergencia, 2 timeout, 3 batería baja.
- `P\n` → responde `pong`. `?\n` → responde una línea de estado legible (estado, paro, batería, comando, PWM, encoder). Ni `P` ni `?` reinician el timeout.
- Si no llega un comando válido en 300 ms, frena y centra (estado 2). Por eso el nodo de la Jetson tiene que mandar comandos seguido (cada ~50–100 ms).
- Batería baja: < 10.5 V pita y limita la velocidad al 50 %; < 9.9 V apaga el motor.
- El firmware viejo usaba solo `V<vel>,S<dir>` y contestaba `v...,s...`; los nodos viejos hay que adaptarlos.

---

## Repositorio

- Hay **dos repos**, separados. `patoiribe10-sys/autonomo-v2-pcb` (público) es el de la placa y el curso: firmware, docs y hardware. En la laptop: `C:\Users\symka\autonomo-v2-pcb`; en la Jetson: `~/autonomo-v2-pcb`. `patoiribe10-sys/robouaq-carro-autonomo` es el carro completo y es `~/robocar_ws` entero (paquete ROS, config, `archivo_viejo/` y este CLAUDE.md). En la Jetson los dos remotos van por SSH (`git@github.com:...`). Este archivo vive **solo** en el repo del carro: el commit `4a92fc2` lo quitó de `autonomo-v2-pcb` (público) y dejó `jetson/README.md` apuntando aquí. **No copiarlo a la placa.**
- `firmware/`: proyectos de PlatformIO (`platform = espressif32@^8.0.0`, board `esp32doit-devkit-v1`, `upload_speed = 460800`):
  - `plantilla/` con `include/pines_v2.h`
  - `prueba-usb/`, `prueba-pcb/` (menú de pruebas) y `demo-recorrido/` (ya probados en la placa)
  - `carro-v2/`: firmware definitivo del carro (compila; falta subirlo y probarlo). Constantes en `include/config.h`; ENC_SIGNO = -1; batería crítica apaga el motor si pasa 1 s abajo de 9.9 V. Al prender marca e=2 (timeout) hasta el primer comando: es normal
- `docs/` (sitio en GitHub Pages: https://patoiribe10-sys.github.io/autonomo-v2-pcb/), `manual/` (manual interactivo de la placa), `hardware/`, `reporte/`.
- Resultados de pruebas para el artículo: `docs/resultado.md`.
- Config del repo: `Alain Iribe Haddad <patoiribe10-sys@users.noreply.github.com>`, `pull.rebase=false`.
- El firmware se compila en la Jetson con PlatformIO 6.1.19 (`pip`, ARM64): `carro-v2` compila con RAM 7.1 % y flash 22.6 % (26-nov). Subir y ver el serial: `pio run -e jetson -t upload` y `pio device monitor -e jetson`, solo cuando Pato diga "súbelo".

---

## Plan de trabajo sugerido

1. **Red:** que la laptop vea la Jetson (misma WiFi u hotspot; ssh activo; revisar `nv-l4t-usb-device-mode`). Llave SSH desde la laptop para no escribir la contraseña cada vez.
2. **Revisión de `~/robocar_ws`:** inventario de cada archivo (qué hace, si sirve con la v2 y qué cambiar). Entregarle a Pato una tabla: reaprovechar / corregir / reescribir / borrar.
3. **Flujo:** VS Code Remote-SSH, PlatformIO CLI en la Jetson, repo clonado, driver CH340 como servicio, reglas udev para que el ESP32 sea siempre el mismo puerto (p. ej. `/dev/esp32`).
4. **Prueba sin ROS:** `firmware/carro-v2/tools/prueba_serial.py` desde la Jetson (primero `--solo-direccion`, ruedas al aire).
5. **ROS:** `teleop_xbox_node` → `/cmd_vel` → `serial_bridge_node` (protocolo nuevo, timeout, estado de batería y paro) → ESP32.
6. **Cámaras:** dashboard en el puerto 8080 con las dos cámaras, visto desde el navegador de la laptop.
7. **Documentar:** resultados en `docs/resultado.md`, fotos o video para el artículo.

Pendientes de antes que siguen vigentes: control PID de velocidad en el ESP32, calibrar cam1, calibración de vista de pájaro, ajustar los umbrales HSV de carriles en la pista real, probar AprilTags (familia 36h11, IDs 0–5 para las seis señales: no_pasar, sin_salida, vuelta_derecha, vuelta_izquierda, siga_de_frente, alto), alto obligatorio de 3 s antes de cada intersección, y construir la pista reducida de 5 × 6 m.

---

## Bitácora de sesiones

(Agregar al final, con fecha, cada vez que Pato diga "cierra sesión".)

### 25-nov-2026
- La PCB v2 ya está en el carro y probada con batería; el carro se movió con `demo-recorrido`.
- La Jetson se alimenta desde J5 sin problema, pero la laptop no la vio por USB-C. Se conectó monitor y teclado para configurarla.
- Se subió a GitHub el commit de la prueba con batería (`f7736d4`).
- Se empezó el firmware `carro-v2` (C con estructuras y apuntadores).
- Siguiente: red y SSH, revisión de `~/robocar_ws`, flujo de trabajo y manejo con el Xbox.

### 25-nov-2026 (noche, sesión en la Jetson)
- apt arreglado: `/etc/apt/sources.list` apuntaba a `us.archive.ubuntu.com` (404 en arm64). Respaldo en `/etc/apt/sources.list.bak`; se borraron las líneas repetidas 3 y 14 y las de jammy-updates, jammy-updates universe y jammy-backports pasaron a `ports.ubuntu.com/ubuntu-ports`. `apt update` sale limpio (quedan 36 paquetes por actualizar, sin tocar).
- tmux 3.2a instalado.
- Red: la Jetson está en la WiFi local con IP `192.168.1.3`. SSH listo: desde la laptop `ssh stitch` (alias a `patoih@192.168.1.3`) entra sin contraseña con llave ed25519, y VS Code ya está conectado por Remote-SSH.
- USB-C: el servicio `nv-l4t-usb-device-mode` está activo, pero `usb0`, `usb1` y `l4tbr0` están en DOWN (la laptop no ve `192.168.55.1`). Pendiente probar otro cable (puede ser uno que solo carga).
- Siguiente: revisión de `~/robocar_ws` (solo lectura, sin cambiar nada) y tabla de reaprovechar/corregir/reescribir/borrar.

### 26-nov-2026
- Kernel y driver: no hay kernel nuevo pendiente (el kernel en uso y el `vermagic` de `ch34x.ko` son `5.15.148-tegra`; la tanda de 503 paquetes del 25-nov no tocó `nvidia-l4t-*`). El driver CH340 ya se autocarga, así que no se hizo servicio. Los seis paquetes `nvidia-l4t-kernel*` quedaron congelados con `apt-mark hold` (ver "Trampas conocidas").
- Regla udev: `/dev/esp32` apunta al ESP32 (CH340 `1a86:7523`). El control Xbox ya aparece como `/dev/input/js0`.
- Revisión de `~/robocar_ws` con dos subagentes Sonnet (solo lectura) y tabla de reaprovechar, corregir, reescribir y borrar. Lo viejo se archivó en `archivo_viejo/` (no se borró nada).
- Dos repos en GitHub, por SSH desde la Jetson: `autonomo-v2-pcb` (movido a `~/autonomo-v2-pcb`) y `robouaq-carro-autonomo` (nuevo, es `~/robocar_ws`; dos commits: "v1" tal cual y el archivado). Antes de subir el segundo se cambió el correo del maintainer por el noreply de GitHub.
- Firmware: `carro-v2` compila en la Jetson (RAM 7.1 %, flash 22.6 %). Se corrigió el comentario del encoder (`ENC_SIGNO = -1` es correcto: en la placa real adelante contaba negativo) y el límite del `snprintf` de la telemetría. `platformio.ini` de `carro-v2` ahora tiene el entorno `esp32doit-devkit-v1` (por defecto, sin puerto) y `jetson` (con `/dev/esp32`). `firmware/README.md` lista los cinco proyectos. **No se subió nada al ESP32.**
- Red fuera de casa: Tailscale instalado (`stitch`, `100.97.122.71`), avahi arreglado (`stitch.local` resuelve a la IP de la WiFi y no a la de Docker), perfiles de WiFi con prioridades 30, 20 y 10 (casa, escuela y hotspot del celular de Pato), MAC real fija. Ver "Cómo entrar a la Jetson".
- Decisiones para los nodos nuevos (aún sin escribir): mensajes propios en un paquete `robocar_interfaces` (`ComandoCarro`, `EstadoCarro`); topics `/carro/cmd` y `/carro/estado`; puente con un solo hilo dueño del puerto, timeout propio de 300 ms que manda `V0,S0`, sin `ttyTHS1` por defecto; teleop con `/joy` de `game_controller_node`, **LB mantenido = hombre muerto**, stick izquierdo ↑↓ = velocidad, stick derecho ←→ = dirección, RB = turbo, `vel_max` 30 % (turbo 60 %) y rampa de velocidad de máximo 10 % por ciclo; nodo de cámaras que no abre ni codifica si nadie está viendo (si se ahoga con las dos, bajar a 10 fps antes que la resolución), con una página que muestra en grande el estado del carro y el voltaje de la batería. El paro por BOOT solo se libera con START físico.
- Tailscale: Pato desactivó la caducidad de la llave de `stitch` ("Expiry disabled") y corrió el `apt-mark hold` de los seis paquetes de kernel. Ya no son pendientes.
- **Paso 7 escrito y probado sin hardware** (aún sin tocar el ESP32): paquete `robocar_interfaces` (`ComandoCarro`, `EstadoCarro`); en `robocar`: `serial_bridge_node` (puente), `teleop_xbox_node`, `camaras_node` + `camara_worker.py`, `launch/control_remoto_launch.py` y `config/control_remoto.yaml`; herramienta `scripts/ver_joy.py`. Pruebas con un ESP32 falso en un pseudo-terminal y con `/joy` falsos: puente 17 de 17 (pong, telemetría, 50 ms entre comandos, `V0,S0` a los ~260 ms sin `/carro/cmd`, reconexión, cierre con `V0,S0` x3) y teleop 17 de 17 (hombre muerto, rampa de 10 %, turbo, zona muerta, pérdida de `/joy`). Cámaras: cam0 real a 15 fps, primer cuadro en 2.2 s, no abre nada sin espectadores, se cierra ~5 s después de que se va el último y se recupera sola si muere el proceso hijo.
- Trampas nuevas: (1) **`empy 4.2.1` instalado con pip en `~/.local` rompía `colcon build`** de los mensajes (ROS 2 Humble necesita el `empy 3.3.4` de apt). El 26-nov Pato lo desinstaló (`pip3 uninstall empy`) y `colcon build --symlink-install` funciona sin trucos. **No volver a instalar `empy` con pip** (si un `pip install` lo trae de dependencia, desinstalarlo). (2) **El `nvargus-daemon` se cae con `InsufficientMemory` / `double free`** cuando hay poca memoria (Chromium, Claude Desktop y GNOME comen RAM) y deja rota la conexión Argus del proceso que lo usaba; por eso la captura corre en un proceso hijo que se reinicia solo. Para manejar el carro conviene cerrar Chromium y Claude Desktop. (3) El demonio de ros2 puede quedar en mal estado (`ros2 topic echo` falla con `RuntimeError: !rclpy.ok()`); probablemente le llegó una señal de un `timeout` que lo arrancó dentro de su grupo de procesos. Arreglo: `ros2 daemon stop && ros2 daemon start` (probado, y `tailscale0` no estorba al DDS). Alternativa: `scripts/ver_joy.py`. (4) Al probar nodos con `ros2 run` y mandarles señales a mano, el hijo queda huérfano: lanzar el ejecutable directo. (5) **`ros2 launch` manda `SIGINT` dos veces** a cada nodo (y Ctrl+C en la terminal también): la segunda cortaba el cierre en mitad (`exit code -2`, el puente sin despedirse); los nodos llaman ahora a `robocar/cierre.py: ignorar_senales_de_cierre()` tras la primera señal. (6) `camaras_node` y los scripts con hilos a veces abortaban al salir con `terminate called without an active exception` (código −6); no se logró reproducir a voluntad, así que el cierre es ordenado (subprocesos → executor y su hilo → nodo → `rclpy`) y termina con `os._exit`, que se salta los destructores estáticos de C++. (7) **`pkill -f`, `pgrep -f` y `ps | grep` por nombre coinciden con la línea de comandos del propio shell** que los lanza si el texto de ese comando contiene los nombres (mató mi shell tres veces y produjo falsas alarmas de "2 procesos vivos" en la prueba del sistema): usar patrones anclados (`pgrep -f '^/usr/bin/python3 [^ ]*/camaras_node'`) o `pkill -x` con el nombre de 15 caracteres. (8) El **nombre** de un `SDL_GAMECONTROLLERCONFIG` no puede llevar comas.
- **Pendiente: confirmar con `scripts/ver_joy.py` los índices del control** (`eje_vel`, `eje_dir`, signos, LB = 9, RB = 10) antes de manejar; **Diagnóstico del control (26-nov):** SDL usa su mapeo de fábrica para el GUID `050000005e040000130b000013050000` (orden de un Xbox 360: `leftx:a0, lefty:a1, lefttrigger:a2, rightx:a3, righty:a4, righttrigger:a5` y botones `b0..b10` contiguos), pero el kernel 5.15 de la Jetson expone este control BLE con otros códigos: ejes `ABS_X, ABS_Y, ABS_Z, ABS_RZ, ABS_GAS, ABS_BRAKE` (SDL los numera `a0..a5` en ese orden: el stick derecho es `a2/a3` y los gatillos `a4/a5`) y **16 botones con huecos** (`BTN_C`, `BTN_Z`, `BTN_TL2`, `BTN_TR2` sin uso). Por eso `/joy` sale revuelto: el eje 3 es el gatillo derecho (en reposo da +1.0 invertido), el stick derecho horizontal llega como `lefttrigger` y LB/RB llegan como `back`/`start`. Solución propuesta: un `SDL_GAMECONTROLLERCONFIG` propio (**medido con `calibrar_control.py` y aplicado el 26-nov**: `src/robocar/config/sdl_gamecontroller.txt`; la medición coincidió con la hipótesis calculada antes de mover nada). `scripts/calibrar_control.py` hace la medición guiada (pide cada control, compara evdev con `/joy` y guarda `config/control_mapeo_medido.yaml` y `sdl_gamecontroller.propuesta.txt`); con `--sdl-propio` verifica la propuesta. Aplicado así: el launch lee `share/robocar/config/sdl_gamecontroller.txt` y lo pasa como variable de entorno `SDL_GAMECONTROLLERCONFIG` **solo a `game_controller_node`** (no a `joy_node`, que usa la API cruda de joystick y no tiene ese mapeo). Con eso `/joy` sigue el orden estándar y `control_remoto.yaml` conserva sus índices: `eje_vel` 1 (LY, arriba = +1), `eje_dir` 2 (RX, izquierda = +1, derecha = −1), LB = 9, RB = 10. **El nombre del mapa no puede llevar comas** (SDL separa los campos por comas). Convención del firmware: **`S = +100` = servo a 1730 µs = derecha**; el teleop manda `dir = +100` cuando el stick va a la derecha (derecha −1 × `signo_dir` −1). Ver en vivo con nombres: `python3 scripts/ver_joy.py --lanzar`. Ojo: si hay otro `game_controller_node` vivo (mapeo de fábrica), `/joy` sale mezclado y el eje 3 parpadea entre +1.00 y 0; `ver_joy` y `calibrar_control` avisan.
- **cam1 sin detectar (26-nov):** en el arranque de las 23:20 el kernel sí buscó el segundo imx219 (`10-0010`) y el device tree está en modo **IMX219 Dual** (`JetsonIO` en `/boot/extlinux/extlinux.conf`, overlay `tegra234-p3767-camera-p3768-imx219-dual.dtbo`), pero falló con `imx219_board_setup: error during i2c read probe (-121)`: el sensor no contesta por I2C en la dirección 0x10 del bus 10, mientras que cam0 (bus 9) se enlaza bien. Es un problema físico (cable o adaptador de 15 a 22 pines, orientación de los contactos, conector mal asentado, o módulo), no de configuración. Ver con `grep imx219 /var/log/kern.log | tail` (`dmesg` pide sudo). Pendientes de Pato: revisar/reacomodar los cables con la Jetson apagada y probar otro cable USB-C para la red por `192.168.55.1`.
- **`camaras_node` tolera que falte una cámara (26-nov):** cada cámara es independiente (su hilo y su proceso hijo). Antes de abrir una se revisa que exista su `/dev/videoN` (parámetros `dev_video_cam0/1`); si no existe **no se llama a `nvarguscamerasrc` con ese sensor** y solo se vuelve a revisar cada 10 s. Si abrió y se cae, reintenta con espera creciente 2, 4, 8, 16 y 30 s (vuelve a 2 s tras 10 s estable). La web muestra siempre los dos cuadros: el que falla sale en negro con "CAMn SIN SEÑAL" y el motivo corto (`no detectada` o `se cayó, reintentando` con la cuenta regresiva); el log de arranque dice el estado de cada una. Pruebas: `scripts/prueba_camaras_tolerancia.py` (12 de 12, con procesos hijos falsos: sin dispositivo no se lanza nada, aparición detectada, espera creciente, independencia, reinicio de la espera). **Prueba real, 2 min con cam1 sin detectar y las dos páginas abiertas:** el nodo levantó, cam0 a 15.0 fps (1 849 cuadros), cam1 en negro (126 cuadros de "sin señal"), **5 líneas de log en total, sin repetidos**, `nvargus-daemon` con el **mismo PID (1062) y 0 reinicios**, un solo proceso de captura (`--sensor 0`); `tegrastats`: CPU 20 %, VDD_IN 6.2 W, tj 48.8 °C (máx 49.7), RAM usada 2 144 MB (captura 37 % de un núcleo, nvargus 19 %). Ojo: el daemon escribe `Failed ImagerGUID 1` una vez cada vez que se conecta un cliente (enumera los dos módulos, también pidiendo solo el sensor 0); no lo provoca el nodo y es inofensivo.
- **Memoria y escritorio (26-nov):** con el escritorio gráfico quedaban ~3 GB disponibles y solo ~176 MB libres; Chromium (~2.1 GB de RSS) y GNOME/Xorg (~1.65 GB) son lo que más pesa (VS Code Server ~1 GB y Claude Code ~0.5 GB hacen falta). Plan acordado para los días de manejo: **A)** cerrar Chromium y medir con `tegrastats`; **B)** si no alcanza, `sudo systemctl stop gdm3` solo por esa sesión (se revierte con `start gdm3` o reiniciando); **C)** modo texto permanente solo si hace falta: `sudo systemctl set-default multi-user.target` (revertir con `graphical.target`; `sudo systemctl isolate graphical.target` vuelve sin reiniciar). SSH, Tailscale, NetworkManager (perfiles de sistema), Bluetooth, avahi y `nvargus-daemon` no dependen del escritorio. No se cambió nada del arranque.
- **Estado al cierre de esta sesión (26-nov, tarde):** el sistema completo está probado **sin el carro y sin tocar el ESP32**. `scripts/prueba_sistema_simulado.py` levanta el launch real contra un ESP32 falso con un `/joy` falso y la web, y pasa **27 de 27** (tres corridas seguidas): LB + stick arriba → `V30,S0`; stick derecho a la derecha → `S+100` (1730 µs, derecha); RB → `V60`; stick abajo → `V-20`; soltar LB frena en ~0.1 s; sin `/joy` frena en < 1 s; **si muere el teleop el puente manda `V0,S0` solo en 0.35 s**; paro, batería baja y "solo USB" llegan a la web; USB desenchufado y vuelto a enchufar reconecta solo; cam0 a 15 fps con cam1 en negro y `nvargus` con el mismo PID; cierre con Ctrl+C limpio. Con el stack completo: CPU 23 %, VDD_IN 6.2 W, tj 48–50 °C. El firmware se verificó **ejecutándolo** en el host (`~/autonomo-v2-pcb/firmware/carro-v2/tools/test_host`, 32 de 32): `S=+100` = 1730 µs, el servo nunca sale de 1190–1730 (jamás 1100), 13 líneas malformadas se ignoran sin tocar el comando, y las 240 variantes de telemetría las entiende el regex del puente. Nada se ha subido al ESP32.
- **Control cerrado: `verificar_teleop.py` pasa 10 de 10 (26-nov, tarde).** `diagnostico_direccion.py` midió con el control real: el stick derecho horizontal es el eje 2 de `/joy` con correlación **−1.00** contra `ABS_Z` del kernel (al ir a la derecha baja de +1 a −1), o sea `eje_dir 2`, `signo_dir −1.0` (derecha → `dir +100` → `S+100` → 1730 µs). Esos valores **ya estaban en el yaml** (`--aplicar` lo dejó idéntico), así que el 8/10 anterior no se arregló cambiando configuración. **Causa del fallo anterior: no demostrada.** Hipótesis más probable: coincidió con mis pruebas de aislamiento, que publicaban `/joy` (incluso con el mapeo de fábrica) en el **mismo dominio ROS 0** que el control real. Por eso las pruebas simuladas corren ahora en `ROS_DOMAIN_ID=77` (probado con un publicador intruso en el dominio 0). Al estar `ROS_LOCALHOST_ONLY=0`, cualquier nodo ROS en la misma red con el dominio 0 ve y puede pisar `/joy` y `/carro/cmd`. El eje 3 (RY) se mueve ±0.28 al mover el stick de lado a lado (acoplamiento normal) y el teleop no lo usa.
- **Cierre de los nodos (probado):** tras la primera señal se ignoran las siguientes (`ros2 launch` manda `SIGINT` dos veces) y un **perro guardián** (`robocar/cierre.py: perro_guardian`) mata a los hijos y sale a la fuerza si el cierre tarda más de 10 s (8 s en el puente y el teleop; probado con un cierre atorado a propósito). **`os._exit` solo en `camaras_node`** (y en los scripts de prueba/diagnóstico), después de cerrar el servidor web, los hilos y los procesos hijos (si uno siguiera vivo se mata antes) y de vaciar `stdout` y `stderr`; el puente y el teleop cierran normal. **Defecto real corregido en el puente:** al cerrar, `rclpy` invalida el contexto antes de que termine el hilo del puerto; si justo publicaba, `publish` lanzaba `RCLError` y el hilo moría **sin despedirse (`V0,S0` ×3)**. `scripts/prueba_cierre_puente.py` lo reproduce a voluntad (1 de 30 cierres fallaba): ahora **60 de 60** limpios, y el bucle está envuelto para llegar siempre a la despedida (el carro igual frenaría por el timeout de 300 ms del ESP32).
- **`scripts/prevuelo.sh`: LISTO, 0 fallas y 2 avisos (ambos cam1).** URLs para la laptop: `http://192.168.1.3:8080`, `http://stitch.local:8080`, o por Tailscale `http://100.97.122.71:8080`.
- **Al volver, en este orden:** (1) cuando Pato diga **"súbelo"**: `pio run -e jetson -t upload` en `~/autonomo-v2-pcb/firmware/carro-v2` con las ruedas al aire, luego `python3 firmware/carro-v2/tools/prueba_serial.py --solo-direccion` y después sin la bandera; (2) `scripts/prevuelo.sh`, `ros2 launch robocar control_remoto_launch.py` con las ruedas al aire y abrir la web en la laptop; después al piso a `vel_max` 30 %; (3) documentar en `docs/resultado.md` (mediciones, capturas, lugar para el video) y copiar la demo a `autonomo-v2-pcb/jetson/demo-control-remoto/`. Pendientes de hardware: cam1 (`-121`: conector/cable) y cable USB-C para `192.168.55.1`. F4 (modo de energía) espera el OK de Pato. El módulo `uinput` no está cargado (con `sudo modprobe uinput` se podría simular el control entero, opcional).

### 26-nov-2026 (tarde-noche, primera prueba con el carro real, ruedas al aire)
- **Firmware `carro-v2` subido** (Pato dijo "súbelo"): `pio run -e jetson -t upload` SUCCESS en 55.7 s, RAM 7.1 %, flash 22.6 %. Responde `P` → `pong` y `?` → estado; telemetría a 20 Hz; con el switch encendido: batería 12.68 V, sin paro, servo en 1360 µs.
- **Prueba sin ROS, todo confirmado por Pato a la vista:** dirección `S-100` = izquierda (1190 µs), `S0` = centro (1360), `S100` = derecha (1730); motor `V15` y `V25` adelante, `V-15` atrás (encoder: adelante positivo, atrás negativo); dirección con motor (`V15,S±100`). Con el mismo PWM 87 el encoder da ~+100 pulsos/s adelante y ~−400 atrás (sin explicar; repetir en el piso con carga). Sin PID las lecturas del encoder son ruidosas.
- **Trampa nueva: el puerto serial a veces abre "mudo"** (el CH340 con el `ch34x.ko` compilado a mano no manda ni recibe nada), en casi 1 de cada 2 aperturas. Causa **no demostrada** (hipótesis: el driver). Mis scripts de prueba exigen telemetría tras abrir y reabren si no llega. **El puente ya lo hace** (`reabrir_sin_telemetria_s`, 2 s; commit `b9f8a38`): en vivo abrió mudo, reabrió y contestó `pong`. El saludo `P` tarda ~2.6 s y el contador empieza después: ~4.6 s hasta la primera reapertura.
- **Control + web con el carro real: todo funciona** (LB hombre muerto, sticks, turbo, frenado, web con voltaje y cam0).
- **cam0:** salía desenfocada (nitidez, varianza del Laplaciano, **6.9**); Pato giró el anillo del lente y subió a **~240**. Salía **de cabeza** (montada al revés): ahora `giro_cam0: 2` en `control_remoto.yaml` (`flip-method=2` de `nvvidconv`, en la GPU); `giro_cam1: 0`. **La calibración del ojo de pez (RMS 0.64 px) ya no vale** (cambió el enfoque): rehacer con `calibrar_fisheye.py` ahora que el lente quedó fijo.
- **Trampa nueva: procesos lanzados con `&` desde un shell no interactivo heredan SIGINT ignorado** (así dejé dos stacks vivos y `kill -INT` no hacía nada). Lanzar con un `Popen` que restaure `SIGINT` (`preexec_fn`) o cerrar con `SIGTERM`. Dos puentes al mismo puerto producen `Puerto abierto` repetido y `pong` perdido.
- **Falta:** el carro al piso a `vel_max` 30 %; PID de velocidad; cam1 (`-121`); calibrar cam0 de nuevo; `docs/resultado.md` y demo en `autonomo-v2-pcb/jetson/demo-control-remoto/`. F4 y F5 siguen en espera.

### 26-nov-2026 (noche, latencia del control)
- **Meta de Pato: menos de 100 ms de punta a punta.** Herramientas nuevas (todas con el stack real, en `ROS_DOMAIN_ID=77`): `scripts/latencia_serial.py` (comando → telemetría sin ROS), `scripts/latencia_ros.py` (`/joy` inyectado → `/carro/cmd` → telemetría, solo dirección, motor en 0) y `scripts/latencia_control.py` (evdev → `/joy` con la mano de Pato; `--adjuntar` para usar un stack ya corriendo; el stack se levanta con `puerto:=/dev/no_existe` para que el servo no se mueva).
- **Cambios de software aplicados (a petición de Pato):** el teleop publica `/carro/cmd` al llegar cada `/joy` (solo si el comando cambió; el timer de 20 Hz sigue como latido) y el puente escribe al serial al llegar cada `/carro/cmd` distinto (el latido de 50 ms sigue). La rampa del teleop pasó a ser **por tiempo transcurrido** (10 % cada 50 ms, nunca más de un paso completo por llamada) para que correrla por evento no la acelere.
- **Latencias medidas (medianas, dirección a fondo, 30-40 cambios):** antes → ahora: `/joy`→`/carro/cmd` 27 → **2 ms**; `/carro/cmd`→telemetría con el cambio 144 → **101 ms**; `/joy`→servo empieza (telemetría) 172 → **103 ms**; `/joy`→valor final 398 → 332 ms. El extremo es la telemetría a 20 Hz (suma 0–50 ms que el servo no tiene). USB-serial: `P`→`pong` **24 ms** (min 22.8, p90 ~41), sin depender de la fase. evdev→`/joy`: **2.5 ms**.
- **Servo en el firmware:** `SERVO_VEL_US_S` pasó de 1500 a **3500** µs/s (compilado, no subido; el MG995 recorre el rango en ~0.15 s). Con 1500 un giro a tope tardaba ~250 ms.
- **Sin resolver:** con el servo en movimiento, el ESP32 avanza `us_actual` por **escalones (~57 µs cada ~38 ms)** y los intervalos de telemetría se estiran (26 de 65 pasan de 60 ms, hasta 119; en reposo son 50.0 ± 0.5). Eso produce un retraso bimodal de ~50 ms. **Se descartó `ESP32Servo`** (`writeMicroseconds` es un `ledcWrite` sin esperas). Causa desconocida; para hallarla hay que instrumentar el firmware (`micros()` por pasada, máximo en `?`) y eso requiere subir. Ojo: sondear con `?` cada 12 ms **perturba** la medición (cada respuesta pesa ~120 B = 10 ms de UART); usar una sonda por prueba.
- **Control Bluetooth y video por WiFi:** WiFi RTL8822CE (PCIe) y Bluetooth (IMC 13d3:3549, USB) son del mismo módulo; la WiFi estaba en 2.4 GHz canal 11. Midiendo con la web cerrada (A, A2) el control reporta cada **15 ms** (mediana; p90 45; `/joy` 42 Hz); con cam0 transmitiendo por WiFi (B) cada **60 ms** (p90 180; `/joy` 25 Hz). A-B-A apoya que **el video atrasa los reportes del control**; B se midió una sola vez. En reposo `/joy` va a ~16 Hz (autorepeat 20) con huecos de 100 ms; los "8 a 13 Hz" que se creyeron antes eran un error de medición (contar desde antes del descubrimiento de DDS). `game_controller_node` pasa evdev→`/joy` en 2.5 ms: sus parámetros (`autorepeat_rate`, `coalesce_interval_ms`, `deadzone`) no son el problema. Opciones pendientes de OK de Pato: bajar `fps_stream` (15 → 10) o la resolución, pasar la WiFi a 5 GHz (NetworkManager: no se toca sin OK) o un adaptador Bluetooth USB aparte.
- **Trampa de medición:** el sello de evdev es de cuando el **kernel** recibe el reporte; el tramo físico stick → radio → kernel no se puede medir con software.
- **Pendiente:** subir el firmware (1680 y 3500 µs/s) cuando Pato diga "súbelo"; probar el carro en el piso a `vel_max` 30 %; PID de velocidad; cam1; recalibrar cam0.

### 26-nov-2026 (noche, firmware, video y arranque automático)
- **Firmware subido** (Pato: "sube lo del firmware 1680 y 3500us/s"): `pio run -e jetson -t upload` SUCCESS en 13.3 s. Confirmado: `S100` → `dir_us=1680`, y Pato confirmó que "llegó bien el servo" (sin forzar). La causa de los ~38 ms de bloqueo del bucle con el servo en movimiento sigue sin hallarse.
- **Video:** `control_remoto.yaml` ahora `ancho: 640`, `alto: 360`, `calidad_jpeg: 70`, `fps_stream: 10.0` (Pato). Medido con `scripts/medir_stream.py` (mismo encuadre): 640×480 a 15 fps = 25.5 KB por JPEG y 3.06 Mbit/s → 640×360 a 15 fps = 23.0 KB y 2.77 Mbit/s (−10 %; el encuadre 16:9 conserva todo el campo de visión y comprime peor por tener más detalle por píxel) → 640×360 a **10 fps** = 23.0 KB y **1.84 Mbit/s (−40 % contra el inicio)**. La calibración del ojo de pez no corresponde a 640×360: rehacer.
- **Privacidad:** los dos repos son públicos. Quitada la MAC de la WiFi del CLAUDE.md (commit normal, sin reescribir; sigue en commits viejos). Revisados árbol e historial de ambos repos (28 y 23 commits): sin contraseñas, llaves ni tokens (búsqueda por patrones). `jetson/CLAUDE.md` se quitó a propósito de la placa (commit `4a92fc2`): **no copiarlo allá**. Las IPs privadas y de Tailscale se quedan (decisión de Pato).
- **`robocar.service` preparado** (ver su sección arriba). Se instala con el OK de Pato.
- **Estado al cierre de esta tanda (26-nov, ~17:20):** `robocar.service` instalado y corriendo; el escritorio gráfico **se queda** (Pato: primero se prueba el servicio con escritorio y, si va bien, se quita después). Pato apagó el control y va a hacer `sudo reboot` para la prueba de arranque en frío. Commits: todo subido hasta `4444f05`; **queda 1 commit local** con `scripts/revisar_arranque.py` y esta nota (subir con OK de Pato). Pendientes: la prueba en frío; quitar el escritorio (solo si pasa); PID de velocidad; causa de los ~38 ms de bloqueo del bucle con el servo en movimiento; cam1; recalibrar cam0; medir el arranque real; el piso a `vel_max` 30 %.

