# sdr_receiver

C++17 / ROS2 receiver node for the SDR radar wireless link. The package keeps a
single codebase for debug and competition mode.

## Implemented interfaces

Subscribed input:

- `/judge/radar_context` using `sdr_receiver/msg/RadarContext`
- optional fallback `/judge/self_id` and `/judge/radar_info` using `std_msgs/msg/UInt8`

Published output:

- `/sdr/jam_code` using `sdr_receiver/msg/JamCode`
- `/sdr/radar_wireless/raw_frame` using `sdr_receiver/msg/RadarWirelessFrame`
- `/sdr/radar_wireless/position`
- `/sdr/radar_wireless/hp`
- `/sdr/radar_wireless/projectile`
- `/sdr/radar_wireless/gold_occupation`
- `/sdr/radar_wireless/buff`
- `/sdr/status` and `/sdr/useful_data`

`RadarContext` is a local mock-compatible copy of the agreed field layout. If
the radar workspace provides the same contract from `vision_interface`, keep the
field semantics identical or add a small relay.

## Competition behavior

The node derives team from `self_id` (`9` red radar, `109` blue radar) and
extracts jam level from `radar_info_raw bit3-4`. It decodes `0x0A06` for the
current level first. When the decoded key level reaches `max_jam_break_level`,
the node publishes that final key and switches directly to INFO decoding without
waiting for a next `0x020E` frame.

Competition mode requires a profile entry for:

```text
match_slot / front_end_id / RED|BLUE / target
```

Targets are `JAM_L1_KEY`, `JAM_L2_KEY`, `JAM_L3_KEY`,
`INFO_UNDER_L1`, `INFO_UNDER_L2`, and `INFO_UNDER_L3`.

Example profile:

```text
config/sdr_profiles/competition_profiles.yaml
```

## Operation manual

For the full field procedure, keyboard map, dashboard interpretation, debug to
competition transition, and troubleshooting checklist, see:

```text
调试模式与比赛模式操作手册.md
```

## ROS2 build

On Ubuntu 22.04 with ROS2 sourced:

```bash
colcon build --packages-select sdr_receiver
source install/setup.bash
```

Run with hardware:

```bash
ros2 run sdr_receiver sdr_receiver_node --ros-args \
  -p run_mode:=competition \
  -p use_real_sdr:=true \
  -p fallback_to_mock:=false \
  -p profile_path:=config/sdr_profiles/competition_profiles.yaml \
  -p match_slot:=bo3_game1 \
  -p front_end_id:=front_end_A
```

Mock integration:

```bash
ros2 run sdr_receiver mock_radar_context_publisher
ros2 run sdr_receiver mock_jam_code_subscriber
ros2 run sdr_receiver mock_raw_frame_subscriber
```

## Local non-ROS build

If `ament_cmake` is not found, CMake builds only the standalone mock executable
from `src/main.cpp`. This is for core C++ smoke checks on Windows/Linux debug
machines, not the competition runtime.
