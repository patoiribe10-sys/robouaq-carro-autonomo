# ROS 2 Node Patterns — RoboUAQ

Aplica cuando: generes o edites nodos ROS 2 Python en este proyecto.

## Estructura base de nodo

Todo nodo debe seguir este patrón:

```python
#!/usr/bin/env python3
import rclpy
from rclpy.node import Node

class MiNodo(Node):
    def __init__(self):
        super().__init__('mi_nodo')
        # Parámetros declarados explícitamente
        self.declare_parameter('param', valor_default)
        # Subscribers / Publishers / Timers aquí

def main(args=None):
    rclpy.init(args=args)
    node = MiNodo()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()

if __name__ == '__main__':
    main()
```

## Topics del proyecto

| Topic | Tipo | Dirección | Descripción |
|-------|------|-----------|-------------|
| `/cmd_vel` | `std_msgs/Float32` | → serial_bridge | Velocidad m/s |
| `/cmd_steer` | `std_msgs/Int32` | → serial_bridge | Steering en microsegundos (1200–1800) |
| `/odom_vel` | `std_msgs/Float32` | serial_bridge → | Velocidad real medida |
| `/steer_status` | `std_msgs/Int32` | serial_bridge → | Steering actual |
| `/encoder_ticks` | `std_msgs/Int32` | serial_bridge → | Ticks acumulados del encoder |
| `/lane/steering_error` | `std_msgs/Float32` | lane_node → | Error de carril (-1.0 a 1.0) |
| `/lane/detected` | `std_msgs/Bool` | lane_node → | ¿Hay línea detectada? |
| `/lane/crosswalk_detected` | `std_msgs/Bool` | lane_node → | ¿Hay cruce peatonal? |
| `/lane/debug_image` | `sensor_msgs/CompressedImage` | lane_node → | Vista bird's eye con overlays |
| `/detection/sign` | `std_msgs/String` | detect_node → | Señal detectada (alto, siga, etc.) |
| `/detection/tag_id` | `std_msgs/Int32` | detect_node → | ID del AprilTag |
| `/detection/distance` | `std_msgs/Float32` | detect_node → | Distancia a la señal en metros |
| `/detection/debug_image` | `sensor_msgs/CompressedImage` | detect_node → | Frame con bboxes y labels |
| `/nav/state` | `std_msgs/String` | nav_node → | Estado actual FSM |
| `/nav/decision_log` | `std_msgs/String` | nav_node → | Log legible de decisiones |
| `/mode` | `std_msgs/String` | teleop → todos | "manual" / "observe" / "auto" |
| `/estop` | `std_msgs/Bool` | teleop → todos | True = parada de emergencia |
| `/nav/start` | `std_msgs/Bool` | teleop → nav | Iniciar recorrido |
| `/nav/reset` | `std_msgs/Bool` | teleop → nav | Resetear FSM |

## Reglas de este proyecto

- NUNCA usar `time.sleep()` en nodos — usar `Timer` de ROS 2
- Los nodos de visión (lane_node, detect_node) deben apagarse en modo "manual" (no procesar)
- El nodo `serial_bridge_node.py` es el ÚNICO que escribe al ESP32 por UART
- Siempre declarar parámetros con `declare_parameter` para poder cambiarlos sin recompilar
- Usar `--symlink-install` en colcon para que cambios en Python no requieran rebuild
- Puerto UART: `/dev/ttyTHS1` a 115200 baud
- En modo "observe": nav_node calcula decisiones pero NO publica en /cmd_vel ni /cmd_steer

## Protocolo UART ESP32 ↔ Jetson

- Jetson → ESP32: `V<vel_m/s>,S<servo_µs>\n`  (ej: `V0.30,S1500\n`)
- ESP32 → Jetson: `v<vel>,s<steer>,e<ticks>\n` (ej: `v0.295,s1500,e12345\n`)
- Watchdog ESP32: si no recibe comando en 500ms, para motor y centra servo
- serial_bridge envía a 20Hz
