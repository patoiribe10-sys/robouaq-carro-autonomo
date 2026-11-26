"""control_remoto_launch.py: manejar con el control de Xbox y ver las camaras en el navegador.

Arranca:
  game_controller_node  -> /joy (del control Bluetooth)
  teleop_xbox_node      -> /carro/cmd (hombre muerto en LB)
  serial_bridge_node    -> habla con el ESP32 por /dev/esp32 y publica /carro/estado
  camaras_node          -> http://<ip de la Jetson>:8080

Todos los parametros estan en config/control_remoto.yaml.

Si existe config/sdl_gamecontroller.txt (una linea de SDL_GAMECONTROLLERCONFIG), se la pasa a
game_controller_node. Sirve para este control BLE (045e:0b13), cuyo mapeo de fabrica en SDL
no coincide con los codigos que da el kernel 5.15 de la Jetson. Ver scripts/calibrar_control.py.

    ros2 launch robocar control_remoto_launch.py
"""
import os

from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch_ros.actions import Node


def generate_launch_description():
    compartido = get_package_share_directory('robocar')
    config = os.path.join(compartido, 'config', 'control_remoto.yaml')

    env_control = {}
    sdl = os.path.join(compartido, 'config', 'sdl_gamecontroller.txt')
    if os.path.exists(sdl):
        with open(sdl) as f:
            env_control['SDL_GAMECONTROLLERCONFIG'] = f.read().strip()

    return LaunchDescription([
        Node(package='joy', executable='game_controller_node', name='game_controller_node',
             parameters=[config], additional_env=env_control, output='screen'),
        Node(package='robocar', executable='teleop_xbox_node', name='teleop_xbox_node',
             parameters=[config], output='screen'),
        Node(package='robocar', executable='serial_bridge_node', name='serial_bridge_node',
             parameters=[config], output='screen'),
        Node(package='robocar', executable='camaras_node', name='camaras_node',
             parameters=[config], output='screen'),
    ])
