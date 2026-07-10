from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument
from launch.substitutions import EnvironmentVariable, LaunchConfiguration
from launch_ros.actions import Node
from launch_ros.parameter_descriptions import ParameterValue


def generate_launch_description():
    original_script_default = EnvironmentVariable("SDR_RECEIVER_ORIGINAL_SCRIPT", default_value="auto")

    return LaunchDescription(
        [
            DeclareLaunchArgument("original_script_path", default_value=original_script_default),
            DeclareLaunchArgument("publish_ros_outputs", default_value="true"),
            DeclareLaunchArgument("debug_accept_ros_control", default_value="false"),
            DeclareLaunchArgument("start_receiver", default_value="true"),
            DeclareLaunchArgument("import_allow_adi_stub", default_value="false"),
            DeclareLaunchArgument("iq_source_path", default_value=""),
            DeclareLaunchArgument("iq_source_loop", default_value="true"),
            DeclareLaunchArgument("iq_source_throttle", default_value="true"),
            DeclareLaunchArgument("iq_source_center_hz", default_value="0.0"),
            DeclareLaunchArgument("iq_source_start_offset_sec", default_value="0.0"),
            DeclareLaunchArgument("iq_source_sample_rate", default_value="0"),
            DeclareLaunchArgument("initial_team", default_value=""),
            DeclareLaunchArgument("initial_target", default_value=""),
            DeclareLaunchArgument("initial_rx_gain", default_value="-1"),
            DeclareLaunchArgument("initial_rf_bw_hz", default_value="0"),
            DeclareLaunchArgument("initial_freq_offset_hz", default_value="0"),
            DeclareLaunchArgument("initial_info_filter", default_value=""),
            DeclareLaunchArgument("initial_info_l2_rescue", default_value="false"),
            DeclareLaunchArgument("initial_info_l3_rescue", default_value="false"),
            DeclareLaunchArgument("record_iq", default_value="false"),
            DeclareLaunchArgument("iq_record_dir", default_value="$HOME/sdr_iq_records"),
            DeclareLaunchArgument("iq_record_prefix", default_value="debug"),
            DeclareLaunchArgument("iq_record_max_sec", default_value="0.0"),
            DeclareLaunchArgument("iq_record_max_bytes", default_value="0"),
            DeclareLaunchArgument("iq_record_every_n", default_value="1"),
            Node(
                package="sdr_receiver_py_wrapper",
                executable="sdr_receiver_py_wrapper_node",
                name="sdr_receiver_py_wrapper_debug",
                output="screen",
                emulate_tty=True,
                parameters=[
                    {
                        "run_mode": "debug",
                        "original_script_path": LaunchConfiguration("original_script_path"),
                        "publish_ros_outputs": ParameterValue(
                            LaunchConfiguration("publish_ros_outputs"),
                            value_type=bool,
                        ),
                        "debug_accept_ros_control": ParameterValue(
                            LaunchConfiguration("debug_accept_ros_control"),
                            value_type=bool,
                        ),
                        "start_receiver": ParameterValue(
                            LaunchConfiguration("start_receiver"),
                            value_type=bool,
                        ),
                        "import_allow_adi_stub": ParameterValue(
                            LaunchConfiguration("import_allow_adi_stub"),
                            value_type=bool,
                        ),
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
                        "iq_source_start_offset_sec": ParameterValue(
                            LaunchConfiguration("iq_source_start_offset_sec"),
                            value_type=float,
                        ),
                        "iq_source_sample_rate": ParameterValue(
                            LaunchConfiguration("iq_source_sample_rate"),
                            value_type=int,
                        ),
                        "initial_team": LaunchConfiguration("initial_team"),
                        "initial_target": LaunchConfiguration("initial_target"),
                        "initial_rx_gain": ParameterValue(
                            LaunchConfiguration("initial_rx_gain"),
                            value_type=int,
                        ),
                        "initial_rf_bw_hz": ParameterValue(
                            LaunchConfiguration("initial_rf_bw_hz"),
                            value_type=int,
                        ),
                        "initial_freq_offset_hz": ParameterValue(
                            LaunchConfiguration("initial_freq_offset_hz"),
                            value_type=int,
                        ),
                        "initial_info_filter": LaunchConfiguration("initial_info_filter"),
                        "initial_info_l2_rescue": ParameterValue(
                            LaunchConfiguration("initial_info_l2_rescue"),
                            value_type=bool,
                        ),
                        "initial_info_l3_rescue": ParameterValue(
                            LaunchConfiguration("initial_info_l3_rescue"),
                            value_type=bool,
                        ),
                        "record_iq": ParameterValue(
                            LaunchConfiguration("record_iq"),
                            value_type=bool,
                        ),
                        "iq_record_dir": LaunchConfiguration("iq_record_dir"),
                        "iq_record_prefix": LaunchConfiguration("iq_record_prefix"),
                        "iq_record_max_sec": ParameterValue(
                            LaunchConfiguration("iq_record_max_sec"),
                            value_type=float,
                        ),
                        "iq_record_max_bytes": ParameterValue(
                            LaunchConfiguration("iq_record_max_bytes"),
                            value_type=int,
                        ),
                        "iq_record_every_n": ParameterValue(
                            LaunchConfiguration("iq_record_every_n"),
                            value_type=int,
                        ),
                        "enable_micro_tune": False,
                    }
                ],
            ),
        ]
    )
