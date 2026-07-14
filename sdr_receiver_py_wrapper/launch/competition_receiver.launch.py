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
            DeclareLaunchArgument("max_jam_break_level", default_value="3"),
            DeclareLaunchArgument("key_publish_min_interval_sec", default_value="0.5"),
            DeclareLaunchArgument("key_retry_limit", default_value="-1"),
            DeclareLaunchArgument("context_authority_topic", default_value=""),
            DeclareLaunchArgument("context_topic", default_value=""),
            DeclareLaunchArgument("context_stable_count", default_value="3"),
            DeclareLaunchArgument("context_stable_sec", default_value="1.0"),
            DeclareLaunchArgument("lock_team_after_start", default_value="true"),
            DeclareLaunchArgument("enable_fallback_topics", default_value="true"),
            DeclareLaunchArgument("fallback_self_id", default_value="0"),
            DeclareLaunchArgument("profile_path", default_value=""),
            DeclareLaunchArgument("match_slot", default_value="bo3_game1"),
            DeclareLaunchArgument("front_end_id", default_value="front_end_A"),
            DeclareLaunchArgument("decoder_primary", default_value="improved_v67"),
            DeclareLaunchArgument("decoder_shadow", default_value=""),
            DeclareLaunchArgument("acquisition_queue_size", default_value="8"),
            DeclareLaunchArgument("record_queue_size", default_value="32"),
            DeclareLaunchArgument("adc_code_scale", default_value="2048.0"),
            DeclareLaunchArgument("rf_clipping_ratio", default_value="0.001"),
            DeclareLaunchArgument("enable_micro_tune", default_value="false"),
            DeclareLaunchArgument("micro_tune_max_hz", default_value="0.0"),
            DeclareLaunchArgument("micro_tune_step_hz", default_value="0.0"),
            DeclareLaunchArgument("micro_tune_timeout_sec", default_value="0.0"),
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
            DeclareLaunchArgument("initial_rx_gain", default_value="20"),
            DeclareLaunchArgument("initial_rf_bw_hz", default_value="0"),
            DeclareLaunchArgument("initial_freq_offset_hz", default_value="0"),
            DeclareLaunchArgument("initial_info_filter", default_value=""),
            DeclareLaunchArgument("record_iq", default_value="true"),
            DeclareLaunchArgument("iq_record_dir", default_value="$HOME/sdr_iq_records"),
            DeclareLaunchArgument("iq_record_prefix", default_value="competition"),
            DeclareLaunchArgument("iq_record_max_sec", default_value="900.0"),
            DeclareLaunchArgument("iq_record_max_bytes", default_value="17179869184"),
            DeclareLaunchArgument("iq_record_every_n", default_value="1"),
            Node(
                package="sdr_receiver_py_wrapper",
                executable="sdr_receiver_py_wrapper_node",
                name="sdr_receiver_py_wrapper_competition",
                output="screen",
                emulate_tty=True,
                parameters=[
                    {
                        "run_mode": "competition",
                        "original_script_path": LaunchConfiguration("original_script_path"),
                        "publish_ros_outputs": True,
                        "debug_accept_ros_control": False,
                        "max_jam_break_level": ParameterValue(
                            LaunchConfiguration("max_jam_break_level"),
                            value_type=int,
                        ),
                        "key_publish_min_interval_sec": ParameterValue(
                            LaunchConfiguration("key_publish_min_interval_sec"),
                            value_type=float,
                        ),
                        "key_retry_limit": ParameterValue(
                            LaunchConfiguration("key_retry_limit"),
                            value_type=int,
                        ),
                        "context_authority_topic": LaunchConfiguration(
                            "context_authority_topic"
                        ),
                        "context_topic": LaunchConfiguration("context_topic"),
                        "context_stable_count": ParameterValue(
                            LaunchConfiguration("context_stable_count"),
                            value_type=int,
                        ),
                        "context_stable_sec": ParameterValue(
                            LaunchConfiguration("context_stable_sec"),
                            value_type=float,
                        ),
                        "lock_team_after_start": ParameterValue(
                            LaunchConfiguration("lock_team_after_start"),
                            value_type=bool,
                        ),
                        "enable_fallback_topics": ParameterValue(
                            LaunchConfiguration("enable_fallback_topics"),
                            value_type=bool,
                        ),
                        "fallback_self_id": ParameterValue(
                            LaunchConfiguration("fallback_self_id"),
                            value_type=int,
                        ),
                        "profile_path": LaunchConfiguration("profile_path"),
                        "match_slot": LaunchConfiguration("match_slot"),
                        "front_end_id": LaunchConfiguration("front_end_id"),
                        "decoder_primary": LaunchConfiguration("decoder_primary"),
                        "decoder_shadow": LaunchConfiguration("decoder_shadow"),
                        "acquisition_queue_size": ParameterValue(
                            LaunchConfiguration("acquisition_queue_size"),
                            value_type=int,
                        ),
                        "record_queue_size": ParameterValue(
                            LaunchConfiguration("record_queue_size"),
                            value_type=int,
                        ),
                        "adc_code_scale": ParameterValue(
                            LaunchConfiguration("adc_code_scale"),
                            value_type=float,
                        ),
                        "rf_clipping_ratio": ParameterValue(
                            LaunchConfiguration("rf_clipping_ratio"),
                            value_type=float,
                        ),
                        "enable_micro_tune": ParameterValue(
                            LaunchConfiguration("enable_micro_tune"),
                            value_type=bool,
                        ),
                        "micro_tune_max_hz": ParameterValue(
                            LaunchConfiguration("micro_tune_max_hz"),
                            value_type=float,
                        ),
                        "micro_tune_step_hz": ParameterValue(
                            LaunchConfiguration("micro_tune_step_hz"),
                            value_type=float,
                        ),
                        "micro_tune_timeout_sec": ParameterValue(
                            LaunchConfiguration("micro_tune_timeout_sec"),
                            value_type=float,
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
                    }
                ],
            ),
        ]
    )
