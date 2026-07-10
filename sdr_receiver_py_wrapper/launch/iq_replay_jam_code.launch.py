from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument
from launch.substitutions import EnvironmentVariable, LaunchConfiguration
from launch_ros.actions import Node
from launch_ros.parameter_descriptions import ParameterValue


def generate_launch_description():
    original_script_default = EnvironmentVariable("SDR_RECEIVER_ORIGINAL_SCRIPT", default_value="auto")
    iq_path_default = EnvironmentVariable("SDR_IQ_SOURCE_PATH", default_value="")
    iq_center_default = EnvironmentVariable("SDR_IQ_SOURCE_CENTER_HZ", default_value="433920000")
    iq_sample_rate_default = EnvironmentVariable("SDR_IQ_SOURCE_SAMPLE_RATE", default_value="2000000")
    team_default = EnvironmentVariable("SDR_IQ_REPLAY_TEAM", default_value="BLUE")
    target_default = EnvironmentVariable("SDR_IQ_REPLAY_TARGET", default_value="L1")

    return LaunchDescription(
        [
            DeclareLaunchArgument("original_script_path", default_value=original_script_default),
            DeclareLaunchArgument("iq_source_path", default_value=iq_path_default),
            DeclareLaunchArgument("iq_source_loop", default_value="true"),
            DeclareLaunchArgument("iq_source_throttle", default_value="true"),
            DeclareLaunchArgument("iq_source_center_hz", default_value=iq_center_default),
            DeclareLaunchArgument("iq_source_sample_rate", default_value=iq_sample_rate_default),
            DeclareLaunchArgument("iq_source_start_offset_sec", default_value="0.0"),
            DeclareLaunchArgument("initial_team", default_value=team_default),
            DeclareLaunchArgument("initial_target", default_value=target_default),
            Node(
                package="sdr_receiver_py_wrapper",
                executable="sdr_receiver_py_wrapper_node",
                name="sdr_receiver_py_wrapper_iq_jam_code",
                output="screen",
                emulate_tty=True,
                parameters=[
                    {
                        "run_mode": "debug",
                        "original_script_path": LaunchConfiguration("original_script_path"),
                        "publish_ros_outputs": True,
                        "debug_accept_ros_control": False,
                        "start_receiver": True,
                        "import_allow_adi_stub": True,
                        "enable_fallback_topics": False,
                        "iq_source_path": LaunchConfiguration("iq_source_path"),
                        "iq_source_loop": ParameterValue(
                            LaunchConfiguration("iq_source_loop"),
                            value_type=bool,
                        ),
                        "iq_source_throttle": ParameterValue(
                            LaunchConfiguration("iq_source_throttle"),
                            value_type=bool,
                        ),
                        "iq_source_center_hz": ParameterValue(
                            LaunchConfiguration("iq_source_center_hz"),
                            value_type=float,
                        ),
                        "iq_source_sample_rate": ParameterValue(
                            LaunchConfiguration("iq_source_sample_rate"),
                            value_type=int,
                        ),
                        "iq_source_start_offset_sec": ParameterValue(
                            LaunchConfiguration("iq_source_start_offset_sec"),
                            value_type=float,
                        ),
                        "initial_team": LaunchConfiguration("initial_team"),
                        "initial_target": LaunchConfiguration("initial_target"),
                        "record_iq": False,
                    }
                ],
            ),
        ]
    )

