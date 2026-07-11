from launch import LaunchDescription
from launch_ros.actions import Node

def generate_launch_description():
    return LaunchDescription([
        Node(
            package='radar_referee',
            executable='radar_node',
            name='radar_node',
            output='screen'
        )
    ])