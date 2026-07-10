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
            DeclareLaunchArgument("mock_self_id", default_value="9"),
            DeclareLaunchArgument("mock_start_level", default_value="1"),
            DeclareLaunchArgument("mock_max_level", default_value="3"),
            DeclareLaunchArgument("mock_key_mutable", default_value="true"),
            Node(
                package="sdr_receiver",
                executable="mock_radar_context_publisher",
                name="mock_referee_context_for_iq_replay",
                output="screen",
                parameters=[
                    {
                        "topic": "/judge/radar_context",
                        "jam_code_topic": "/sdr/jam_code",
                        "self_id": ParameterValue(
                            LaunchConfiguration("mock_self_id"),
                            value_type=int,
                        ),
                        "start_level": ParameterValue(
                            LaunchConfiguration("mock_start_level"),
                            value_type=int,
                        ),
                        "max_level": ParameterValue(
                            LaunchConfiguration("mock_max_level"),
                            value_type=int,
                        ),
                        "key_mutable": ParameterValue(
                            LaunchConfiguration("mock_key_mutable"),
                            value_type=bool,
                        ),
                        "referee_online": True,
                        "advance_on_jam_code": True,
                    }
                ],
            ),
            Node(
                package="sdr_receiver",
                executable="mock_jam_code_subscriber",
                name="mock_radar_jam_code_sink",
                output="screen",
                parameters=[{"topic": "/sdr/jam_code"}],
            ),
            Node(
                package="sdr_receiver_py_wrapper",
                executable="sdr_receiver_py_wrapper_node",
                name="sdr_receiver_py_wrapper_iq_closed_loop",
                output="screen",
                emulate_tty=True,
                parameters=[
                    {
                        "run_mode": "competition",
                        "original_script_path": LaunchConfiguration("original_script_path"),
                        "publish_ros_outputs": True,
                        "debug_accept_ros_control": False,
                        "context_topic": "/judge/radar_context",
                        "enable_fallback_topics": True,
                        "fallback_self_id": 0,
                        "start_receiver": True,
                        "import_allow_adi_stub": True,
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

