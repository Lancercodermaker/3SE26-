# Ubuntu 22.04 Competition Deployment Guide

This guide is for deploying and testing `sdr_receiver` on the Ubuntu 22.04 radar host.

## 1. What Must Be Ready

Target machine:

- Ubuntu 22.04
- ROS2 Humble
- `colcon`
- `libiio` and `iio_info`
- SDR front end reachable at `ip:192.168.2.1`
- Radar project publishes judge context on `/judge/radar_context`

Install common dependencies:

```bash
sudo apt update
sudo apt install -y \
  build-essential cmake git \
  python3-colcon-common-extensions \
  libiio-dev libiio-utils
```

If ROS2 Humble is not installed yet, install it first following your team image procedure. At minimum, the receiver needs `rclcpp`, `std_msgs`, `ament_cmake`, and `rosidl_default_generators`.

## 2. Workspace Layout

Recommended radar workspace layout:

```text
radar_ws/
  src/
    radar_referee/          # modified by teammate
    vision_interface/       # if used by radar project
    sdr_receiver/           # this package
```

Copy this package into the ROS2 workspace:

```bash
mkdir -p ~/radar_ws/src
cp -a /path/to/sdr_receiver ~/radar_ws/src/
cd ~/radar_ws
```

## 3. Build

Every new terminal or boot script must set up ROS2 before building or running:

```bash
source /opt/ros/humble/setup.bash
cd ~/radar_ws
colcon build --packages-select sdr_receiver
source install/setup.bash
```

If the package is built together with the radar project:

```bash
source /opt/ros/humble/setup.bash
cd ~/radar_ws
colcon build
source install/setup.bash
```

## 4. Pre-Match Profile Check

Edit the profile file before the match:

```text
~/radar_ws/src/sdr_receiver/config/sdr_profiles/competition_profiles.yaml
```

Confirm the active keys exist for the selected match:

```text
profile_sets:
  bo3_game1:
    front_end_A:
      RED:
        JAM_L1_KEY:
        JAM_L2_KEY:
        JAM_L3_KEY:
        INFO_UNDER_L3:
      BLUE:
        ...
```

Competition mode will not silently borrow another BO3 game, front end, team, or target when `require_profile=true`.

## 5. Hardware Smoke Test

Check SDR connectivity:

```bash
iio_info -u ip:192.168.2.1
```

Expected result:

- `ad9361-phy` is visible
- `cf-ad9361-lpc` is visible
- command does not hang or timeout

If this fails, fix USB network, SDR IP, cable, power, or driver before starting ROS2 receiver.

## 6. Mock Context Test Without SDR

Terminal 1:

```bash
source /opt/ros/humble/setup.bash
cd ~/radar_ws
source install/setup.bash
ros2 run sdr_receiver mock_radar_context_publisher
```

Terminal 2:

```bash
source /opt/ros/humble/setup.bash
cd ~/radar_ws
source install/setup.bash
ros2 run sdr_receiver mock_jam_code_subscriber
```

Terminal 3:

```bash
source /opt/ros/humble/setup.bash
cd ~/radar_ws
source install/setup.bash
ros2 run sdr_receiver sdr_receiver_node --ros-args \
  -p run_mode:=competition \
  -p use_real_sdr:=false \
  -p require_profile:=false \
  -p match_slot:=bo3_game1 \
  -p front_end_id:=front_end_A
```

This verifies ROS2 graph, parameters, message generation, and judge-context state handling without SDR hardware.

## 7. Competition Run With SDR

Start the radar project first, or at least ensure `/judge/radar_context` will be published.

Run receiver:

```bash
source /opt/ros/humble/setup.bash
cd ~/radar_ws
source install/setup.bash
ros2 run sdr_receiver sdr_receiver_node --ros-args \
  -p run_mode:=competition \
  -p use_real_sdr:=true \
  -p fallback_to_mock:=false \
  -p require_profile:=true \
  -p profile_path:=src/sdr_receiver/config/sdr_profiles/competition_profiles.yaml \
  -p match_slot:=bo3_game1 \
  -p front_end_id:=front_end_A \
  -p max_jam_break_level:=3 \
  -p terminal_ui:=true \
  -p publish_debug:=true
```

For BO3 game 2 or game 3, change only:

```bash
-p match_slot:=bo3_game2 -p front_end_id:=front_end_B
```

or:

```bash
-p match_slot:=bo3_game3 -p front_end_id:=front_end_C
```

## 8. Useful Runtime Checks

In another ROS2-sourced shell:

```bash
ros2 topic echo /sdr/status
ros2 topic echo /sdr/jam_code
ros2 topic echo /sdr/radar_wireless/raw_frame
ros2 topic list | grep sdr
```

Expected state flow:

```text
WaitingContext
JamDecode L1
WaitLevelUpdate
JamDecode L2
WaitLevelUpdate
JamDecode L3
InfoDecode
```

Important behavior:

- `self_id=9` means RED.
- `self_id=109` means BLUE.
- `radar_info_raw bit3-4` selects jam level `1..3`.
- `jam_level=0` is invalid in competition mode.
- At `max_jam_break_level`, the receiver still decodes and publishes that final key once, then switches directly to INFO.

## 9. Does ROS2 Have To Run In A Terminal?

No. ROS2 does not require a visible terminal.

What ROS2 requires is that the process environment is prepared before the node starts:

```bash
source /opt/ros/humble/setup.bash
source ~/radar_ws/install/setup.bash
```

A terminal is just the most convenient manual way to do that. For competition, you can also run the receiver from:

- a `tmux` session
- a shell script
- a `systemd` service
- a launch file
- a supervisor process started by your radar app

For reliability during a match, `tmux` or `systemd` is usually better than a loose terminal window.

## 10. Simple tmux Start

```bash
tmux new -s radar_sdr
source /opt/ros/humble/setup.bash
cd ~/radar_ws
source install/setup.bash
ros2 run sdr_receiver sdr_receiver_node --ros-args \
  -p run_mode:=competition \
  -p use_real_sdr:=true \
  -p fallback_to_mock:=false \
  -p require_profile:=true \
  -p profile_path:=src/sdr_receiver/config/sdr_profiles/competition_profiles.yaml \
  -p match_slot:=bo3_game1 \
  -p front_end_id:=front_end_A
```

Detach without stopping:

```text
Ctrl+b, then d
```

Reattach:

```bash
tmux attach -t radar_sdr
```

## 11. Example systemd Service

Create a run script:

```bash
mkdir -p ~/radar_ws/scripts
nano ~/radar_ws/scripts/run_sdr_receiver.sh
```

Script content:

```bash
#!/usr/bin/env bash
set -e
source /opt/ros/humble/setup.bash
cd /home/REPLACE_USER/radar_ws
source install/setup.bash
exec ros2 run sdr_receiver sdr_receiver_node --ros-args \
  -p run_mode:=competition \
  -p use_real_sdr:=true \
  -p fallback_to_mock:=false \
  -p require_profile:=true \
  -p profile_path:=src/sdr_receiver/config/sdr_profiles/competition_profiles.yaml \
  -p match_slot:=bo3_game1 \
  -p front_end_id:=front_end_A
```

Make executable:

```bash
chmod +x ~/radar_ws/scripts/run_sdr_receiver.sh
```

Service file:

```bash
sudo nano /etc/systemd/system/sdr_receiver.service
```

Content:

```ini
[Unit]
Description=SDR Receiver ROS2 Node
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=REPLACE_USER
ExecStart=/home/REPLACE_USER/radar_ws/scripts/run_sdr_receiver.sh
Restart=on-failure
RestartSec=2

[Install]
WantedBy=multi-user.target
```

Enable and start:

```bash
sudo systemctl daemon-reload
sudo systemctl enable sdr_receiver.service
sudo systemctl start sdr_receiver.service
```

View logs:

```bash
journalctl -u sdr_receiver.service -f
```

## 12. Match-Day Checklist

Before match:

- `iio_info -u ip:192.168.2.1` passes.
- Correct `match_slot` and `front_end_id` are selected.
- `competition_profiles.yaml` has entries for current team and targets.
- Radar project publishes `/judge/radar_context`.
- `/sdr/status` shows valid `self_id` and `jam_level`.
- `/sdr/jam_code` subscriber in radar project is ready.

During match:

- Do not use keyboard control in competition mode.
- Do not allow fallback to mock SDR.
- Watch `/sdr/status` or service logs.

Emergency rollback:

```bash
sudo systemctl stop sdr_receiver.service
```

or stop the tmux/terminal process with `Ctrl+C`.
