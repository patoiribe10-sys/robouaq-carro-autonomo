# RoboUAQ — carro autónomo

Carro RC escala 1:10 que debe recorrer un circuito urbano en menos de 5 minutos, respetando señales y esquivando obstáculos, usando solo cámaras. Es el proyecto del equipo RoboUAQ (Universidad Anáhuac Querétaro) para la competencia "Robot Conducción Urbana". La siguiente edición es en mayo de 2027.

Este repositorio es el software del carro completo, que corre en una Jetson Orin Nano con ROS 2 Humble. El diseño de la placa (PCB v2) y el firmware del ESP32 viven en otro repositorio: [autonomo-v2-pcb](https://github.com/patoiribe10-sys/autonomo-v2-pcb).

## Cómo va

1. Placa y pista: terminado.
2. **Pruebas básicas en manual (control de Xbox, sin IA): en curso.**
3. Conducción autónoma con visión: pendiente.

## Qué hay aquí

- `src/robocar/`: paquete ROS 2 con los nodos del carro (puente serial con el ESP32, teleop con el control de Xbox, visión, navegación) y sus launch files.
- `config/`: calibración de las cámaras, vista de pájaro y mapa de señales (AprilTags).
- `scripts/`: calibración de la cámara de ojo de pez, visor de cámara y dashboard.
- `archivo_viejo/`: código de la primera versión (placa anterior), guardado como historia del proyecto. No se usa.
- `CLAUDE.md`: contexto del proyecto y bitácora de sesiones.

## Compilar

En la Jetson, con ROS 2 Humble:

```bash
cd ~/robocar_ws
source /opt/ros/humble/setup.bash
colcon build --symlink-install
source install/setup.bash
```

Ojo con OpenCV: se usa el del sistema (trae GStreamer para las cámaras CSI). No instalar `opencv-python` con pip.

## Manejar con el control de Xbox y ver las cámaras

Con el control conectado por Bluetooth y el ESP32 por USB (`/dev/esp32`):

```bash
source /opt/ros/humble/setup.bash && source ~/robocar_ws/install/setup.bash
ros2 launch robocar control_remoto_launch.py
```

Abre `http://<ip de la Jetson>:8080` en el navegador de la laptop: se ven las dos cámaras, el estado del carro y el voltaje de la batería. Las cámaras solo se encienden mientras la página está abierta. Si una cámara falta o se cae, la otra sigue transmitiendo y la que falla aparece en negro con "CAMn SIN SEÑAL" y el motivo (no hace falta reiniciar nada).

- **LB mantenido** = hombre muerto (sin LB el carro se detiene). Stick izquierdo ↑↓ = velocidad, stick derecho ←→ = dirección, **RB** = turbo.
- Los índices de los ejes y botones, y los límites de velocidad, están en `src/robocar/config/control_remoto.yaml`. Para confirmar los índices: `python3 scripts/ver_joy.py`.
- Si el carro no se mueve en la dirección esperada, cambia `signo_vel` o `signo_dir` en ese archivo.

Pruebas sin hardware (un ESP32 falso y un control falso): `scripts/prueba_puente_simulado.py` y `scripts/prueba_teleop_simulado.py`.

Si `colcon build` falla al generar los mensajes con un error de `empy` (`module 'em' has no attribute 'BUFFERED_OPT'`), es que hay un `empy` 4.x instalado con pip en `~/.local` tapando al de ROS: `pip3 uninstall empy`.
