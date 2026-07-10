# Ubuntu 22.04 比赛主机部署与测试说明

本文用于在雷达 Ubuntu 22.04 比赛主机上部署、启动和测试 `sdr_receiver`。

## 1. 前置条件

比赛主机需要具备：

- Ubuntu 22.04
- ROS2 Humble
- `colcon`
- `libiio` 与 `iio_info`
- 接收端 SDR 可通过 `ip:192.168.2.1` 访问
- 雷达工程能够发布 `/judge/radar_context`

常用依赖安装：

```bash
sudo apt update
sudo apt install -y \
  build-essential cmake git \
  python3-colcon-common-extensions \
  libiio-dev libiio-utils
```

如果 ROS2 Humble 尚未安装，请先按队伍镜像或官方流程安装。接收端至少需要 `rclcpp`、`std_msgs`、`ament_cmake`、`rosidl_default_generators`。

## 2. 推荐工作区结构

如果当前只测试接收端，不需要把完整雷达主工程一起放进来。`~/radar_ws` 可以只是一个最小 ROS2 工作区，里面只包含 `sdr_receiver` 一个包：

```text
radar_ws/
  src/
    sdr_receiver/           # 本接收端包
```

后续需要和完整雷达工程联调时，再把接收端包放进雷达 ROS2 工作区：

```text
radar_ws/
  src/
    radar_referee/          # 队友负责修改
    vision_interface/       # 如果雷达工程使用
    sdr_receiver/           # 本接收端包
```

复制包：

```bash
mkdir -p ~/radar_ws/src
cp -a /path/to/sdr_receiver ~/radar_ws/src/
cd ~/radar_ws
```

复制完成后，先确认 `package.xml` 位于下面这个位置：

```bash
ls ~/radar_ws/src/sdr_receiver/package.xml
```

如果这里提示文件不存在，`colcon build --packages-select sdr_receiver` 会报：

```text
ignoring unknown package 'sdr_receiver' in --packages-select
```

这表示当前工作区没有发现名为 `sdr_receiver` 的 ROS2 包。常见原因是包还没复制到 `~/radar_ws/src/`，或者复制后多套了一层目录，例如变成了 `~/radar_ws/src/iq_recevier/sdr_receiver/package.xml`。

可以用下面命令检查当前工作区发现了哪些包：

```bash
cd ~/radar_ws
colcon list
find src -maxdepth 3 -name package.xml -print
```

## 3. 编译

每个新终端、脚本或服务启动前，都需要先加载 ROS2 环境：

```bash
source /opt/ros/humble/setup.bash
cd ~/radar_ws
colcon build --packages-select sdr_receiver
source install/setup.bash
```

如果和完整雷达工程一起编译：

```bash
source /opt/ros/humble/setup.bash
cd ~/radar_ws
colcon build
source install/setup.bash
```

当前只测试接收端时，也可以只编译 `sdr_receiver`，不需要雷达主工程存在。

## 4. 赛前 profile 检查

赛前确认并编辑：

```text
~/radar_ws/src/sdr_receiver/config/sdr_profiles/competition_profiles.yaml
```

需要确保当前 BO3 场次、射频前端、红蓝方、目标都有 profile：

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

比赛模式下建议使用 `require_profile:=true`。这样缺少当前 `match_slot/front_end_id/team/target` 时会直接报错等待，不会静默使用其他场次或其他前端的参数。

## 5. SDR 硬件烟雾测试

先确认接收端 SDR 可访问：

```bash
iio_info -u ip:192.168.2.1
```

期望看到：

- `ad9361-phy`
- `cf-ad9361-lpc`
- 命令不超时、不长时间卡住

如果失败，优先检查 USB 网卡、SDR IP、电源、线缆、驱动和网络路由。

## 6. 不接 SDR 的 mock 联调

终端 1，发布模拟裁判上下文：

```bash
source /opt/ros/humble/setup.bash
cd ~/radar_ws
source install/setup.bash
ros2 run sdr_receiver mock_radar_context_publisher
```

终端 2，监听 key：

```bash
source /opt/ros/humble/setup.bash
cd ~/radar_ws
source install/setup.bash
ros2 run sdr_receiver mock_jam_code_subscriber
```

终端 3，启动接收端 mock SDR：

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

这个流程用于验证 ROS2 图、消息生成、参数和裁判上下文状态机，不验证真实解调。

## 7. 比赛模式真实 SDR 启动

先启动雷达工程，或至少确保 `/judge/radar_context` 会被发布。

启动接收端：

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

BO3 第二场或第三场只改：

```bash
-p match_slot:=bo3_game2 -p front_end_id:=front_end_B
```

或：

```bash
-p match_slot:=bo3_game3 -p front_end_id:=front_end_C
```

## 8. 运行时检查

另开一个已加载 ROS2 环境的终端：

```bash
ros2 topic echo /sdr/status
ros2 topic echo /sdr/jam_code
ros2 topic echo /sdr/radar_wireless/raw_frame
ros2 topic list | grep sdr
```

期望状态流：

```text
WaitingContext
JamDecode L1
WaitLevelUpdate
JamDecode L2
WaitLevelUpdate
JamDecode L3
InfoDecode
```

关键约定：

- `self_id=9` 表示红方雷达站。
- `self_id=109` 表示蓝方雷达站。
- `radar_info_raw bit3-4` 表示干扰等级 `1..3`。
- `jam_level=0` 在比赛模式下视为无效上下文。
- 达到 `max_jam_break_level` 时，接收端仍会先解出并发布该最高等级 key，然后直接进入 INFO。

## 9. ROS2 一定要在 terminal 里运行吗

不一定。

ROS2 节点不要求必须运行在一个可见 terminal 中。它只要求进程启动前环境变量准备好：

```bash
source /opt/ros/humble/setup.bash
source ~/radar_ws/install/setup.bash
```

terminal 只是最方便的人工启动方式。比赛时也可以使用：

- `tmux`
- shell 脚本
- `systemd` 服务
- ROS2 launch 文件
- 雷达主程序或其他 supervisor 拉起

比赛现场更推荐 `tmux` 或 `systemd`，比一个普通 terminal 窗口更稳。

## 10. tmux 启动方式

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

不中断程序并退出 tmux：

```text
Ctrl+b，然后按 d
```

重新进入：

```bash
tmux attach -t radar_sdr
```

## 11. systemd 后台服务方式

创建启动脚本：

```bash
mkdir -p ~/radar_ws/scripts
nano ~/radar_ws/scripts/run_sdr_receiver.sh
```

脚本内容，将 `REPLACE_USER` 替换成实际用户名：

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

赋予执行权限：

```bash
chmod +x ~/radar_ws/scripts/run_sdr_receiver.sh
```

创建服务：

```bash
sudo nano /etc/systemd/system/sdr_receiver.service
```

服务内容，将 `REPLACE_USER` 替换成实际用户名：

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

启用并启动：

```bash
sudo systemctl daemon-reload
sudo systemctl enable sdr_receiver.service
sudo systemctl start sdr_receiver.service
```

查看日志：

```bash
journalctl -u sdr_receiver.service -f
```

停止服务：

```bash
sudo systemctl stop sdr_receiver.service
```

## 12. 比赛日前检查清单

赛前确认：

- `iio_info -u ip:192.168.2.1` 通过。
- `match_slot` 与 `front_end_id` 正确。
- `competition_profiles.yaml` 覆盖当前红蓝方和目标。
- 雷达工程发布 `/judge/radar_context`。
- `/sdr/status` 能看到有效 `self_id` 和 `jam_level`。
- 雷达工程已订阅 `/sdr/jam_code`。
- 比赛模式下 `fallback_to_mock:=false`。
- 比赛模式下不使用键盘控制。

紧急停止：

```bash
sudo systemctl stop sdr_receiver.service
```

或在 tmux/terminal 中按 `Ctrl+C`。
