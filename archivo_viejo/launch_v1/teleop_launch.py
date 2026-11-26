"""
teleop_launch.py — Lanza solo teleop_xbox_node + serial_bridge_node

Uso: ros2 launch robocar teleop_launch.py
Modo: manual (Xbox controla, sin visión activa)
"""

from launch import LaunchDescription
from launch_ros.actions import Node
from launch.actions import DeclareLaunchArgument
from launch.substitutions import LaunchConfiguration


def generate_launch_description():
    uart_port = LaunchConfiguration('uart_port')
    max_vel = LaunchConfiguration('max_vel')

    return LaunchDescription([
        DeclareLaunchArgument('uart_port', default_value='/dev/ttyTHS1',
                              description='Puerto UART hacia ESP32'),
        DeclareLaunchArgument('max_vel', default_value='0.4',
                              description='Velocidad máxima del joystick (m/s)'),

        Node(
            package='robocar',
            executable='teleop_xbox_node',
            name='teleop_xbox_node',
            output='screen',
            parameters=[{
                'max_vel': max_vel,
                'boost_vel': 0.6,
                'slow_vel': 0.15,
                'steer_center': 1500,
                'steer_min': 1100,
                'steer_max': 1900,
            }],
        ),

        Node(
            package='robocar',
            executable='serial_bridge_node',
            name='serial_bridge_node',
            output='screen',
            parameters=[{
                'port': uart_port,
                'baud': 115200,
                'send_rate': 20.0,
                'fallback_port': '/dev/ttyUSB0',
            }],
        ),
    ])
