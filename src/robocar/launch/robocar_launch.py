"""
robocar_launch.py — Lanzamiento completo autónomo (competencia)

Uso: ros2 launch robocar robocar_launch.py
Modo: auto — nav_node controla el carro completamente.
      Xbox solo para ESTOP y cambio de modo de emergencia.
"""

from launch import LaunchDescription
from launch_ros.actions import Node
from launch.actions import DeclareLaunchArgument
from launch.substitutions import LaunchConfiguration


def generate_launch_description():
    # Parámetros configurables en tiempo de lanzamiento
    uart_port = LaunchConfiguration('uart_port')
    cam0_id = LaunchConfiguration('cam0_id')
    cam1_id = LaunchConfiguration('cam1_id')
    base_vel = LaunchConfiguration('base_vel')
    steer_gain = LaunchConfiguration('steer_gain')

    return LaunchDescription([
        DeclareLaunchArgument('uart_port', default_value='/dev/ttyTHS1',
                              description='Puerto UART hacia ESP32'),
        DeclareLaunchArgument('cam0_id', default_value='0',
                              description='sensor-id cámara fisheye (lane)'),
        DeclareLaunchArgument('cam1_id', default_value='1',
                              description='sensor-id cámara normal (detect)'),
        DeclareLaunchArgument('base_vel', default_value='0.3',
                              description='Velocidad base autónoma (m/s)'),
        DeclareLaunchArgument('steer_gain', default_value='300.0',
                              description='Ganancia de steering (µs por unidad de error)'),

        Node(
            package='robocar',
            executable='teleop_xbox_node',
            name='teleop_xbox_node',
            output='screen',
            parameters=[{
                'max_vel': 0.4,
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

        Node(
            package='robocar',
            executable='lane_node',
            name='lane_node',
            output='screen',
            parameters=[{
                'sensor_id': cam0_id,
                'width': 640,
                'height': 480,
                'fps': 30,
            }],
        ),

        Node(
            package='robocar',
            executable='detect_node',
            name='detect_node',
            output='screen',
            parameters=[{
                'sensor_id': cam1_id,
                'width': 640,
                'height': 480,
                'fps': 30,
                'apriltag_size': 0.06,
            }],
        ),

        Node(
            package='robocar',
            executable='nav_node',
            name='nav_node',
            output='screen',
            parameters=[{
                'base_vel': base_vel,
                'approach_vel': 0.15,
                'turn_vel': 0.2,
                'steer_center': 1500,
                'steer_gain': steer_gain,
                'steer_min': 1200,
                'steer_max': 1800,
                'intersection_stop_s': 3.0,
                'turn_duration_s': 2.5,
                'approach_dist': 1.0,
                'sign_cooldown_s': 5.0,
                'sign_confirm_frames': 3,
                'immobile_timeout_s': 20.0,
            }],
        ),
    ])
