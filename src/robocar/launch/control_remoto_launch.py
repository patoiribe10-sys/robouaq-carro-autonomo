"""control_remoto_launch.py: manejar con el control de Xbox y ver las camaras en el navegador.

Arranca:
  game_controller_node  -> /joy (del control Bluetooth)
  teleop_xbox_node      -> /carro/cmd (hombre muerto en LB)
  serial_bridge_node    -> habla con el ESP32 por /dev/esp32 y publica /carro/estado
  camaras_node          -> http://<ip de la Jetson>:8080

Todos los parametros estan en config/control_remoto.yaml.

    ros2 launch robocar control_remoto_launch.py
"""
import os

from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch_ros.actions import Node


def generate_launch_description():
    config = os.path.join(get_package_share_directory('robocar'), 'config', 'control_remoto.yaml')

    return LaunchDescription([
        Node(package='joy', executable='game_controller_node', name='game_controller_node',
             parameters=[config], output='screen'),
        Node(package='robocar', executable='teleop_xbox_node', name='teleop_xbox_node',
             parameters=[config], output='screen'),
        Node(package='robocar', executable='serial_bridge_node', name='serial_bridge_node',
             parameters=[config], output='screen'),
        Node(package='robocar', executable='camaras_node', name='camaras_node',
             parameters=[config], output='screen'),
    ])
