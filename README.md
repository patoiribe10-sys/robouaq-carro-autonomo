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
