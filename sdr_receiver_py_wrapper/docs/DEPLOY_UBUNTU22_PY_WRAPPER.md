# Ubuntu 22.04 SDR Python Wrapper 部署说明

目标运行环境：

- Ubuntu 22.04
- ROS2 Humble
- Python 3.10
- `pyadi-iio`
- libiio 能访问 SDR 前端 `ip:192.168.2.1`

## 1. 安装系统依赖

```bash
sudo apt update
sudo apt install -y ros-humble-desktop python3-venv python3-pip libiio-dev iiod
```

## 2. 创建运行 venv

必须使用 `--system-site-packages`，这样 venv 里才能访问系统 ROS2 提供的 `rclpy`。

```bash
python3 -m venv ~/sdr_runtime/venv --system-site-packages
source ~/sdr_runtime/venv/bin/activate
pip install -U pip
pip install -r ~/radar_ws/src/sdr_receiver_py_wrapper/requirements.txt
```

如果 `ros2 launch` 启动节点时报 `ModuleNotFoundError: No module named 'adi'`，说明节点运行时没有吃到 venv 的 site-packages。运行前加入：

```bash
export PYTHONPATH=$HOME/sdr_runtime/venv/lib/python3.10/site-packages:$PYTHONPATH
```

本 wrapper 已经在 `sdr_receiver_py_wrapper/vendor/` 内置一份已验证的 v67 原脚本。正常情况下不需要额外复制脚本。若想覆盖包内脚本，可以设置：

```bash
export SDR_RECEIVER_ORIGINAL_SCRIPT=/path/to/receiving_messages_adaptive_filter_v67_l2cal_20260505_l2rescue_80k_g40.py
```

## 3. 构建工作区

推荐放置方式：

```text
~/radar_ws/src/sdr_receiver/
~/radar_ws/src/sdr_receiver_py_wrapper/
~/radar_ws/src/3SE_2026_Radar/
```

构建：

```bash
source /opt/ros/humble/setup.bash
cd ~/radar_ws
colcon build --packages-select sdr_receiver sdr_receiver_py_wrapper
source install/setup.bash
```

## 4. Debug 模式

```bash
source ~/sdr_runtime/venv/bin/activate
source /opt/ros/humble/setup.bash
source ~/radar_ws/install/setup.bash
export PYTHONPATH=$HOME/sdr_runtime/venv/lib/python3.10/site-packages:$PYTHONPATH

ros2 launch sdr_receiver_py_wrapper debug_receiver.launch.py
```

注意：`ros2 launch` 经常不会把终端键盘输入转发给节点，所以 dashboard 可能能显示，但 `r/b/1/2/3/+/q` 等按键不生效。需要键盘交互时，使用 `ros2 run` 直接启动：

```bash
ros2 run sdr_receiver_py_wrapper sdr_receiver_py_wrapper_node --ros-args \
  -p run_mode:=debug \
  -p publish_ros_outputs:=true
```

如果怀疑 wrapper 或 monkey patch 影响了原脚本行为，可以运行完全直通版本：

```bash
ros2 run sdr_receiver_py_wrapper direct_original_receiver
```

这个命令会自动定位包内 v67 脚本，然后直接执行原脚本 `main()`，不启动 ROS2 node，也不应用任何 monkey patch。

如果必须使用 `ros2 launch`，可以通过参数代替键盘设置初始状态。例如测试蓝方 INFO 并把增益提高到 40：

```bash
ros2 launch sdr_receiver_py_wrapper debug_receiver.launch.py \
  initial_team:=BLUE \
  initial_target:=INFO \
  initial_rx_gain:=40
```

如果 `rf_power_scan` 显示 INFO 最强峰相对 LO 有明显偏移，可以用 `initial_freq_offset_hz` 临时修正接收 LO。你这次 RED_INFO 的峰值偏移约为 `+127.9 kHz`，建议直接测试：

```bash
ros2 launch sdr_receiver_py_wrapper debug_receiver.launch.py \
  initial_team:=RED \
  initial_target:=INFO \
  initial_rx_gain:=73 \
  initial_freq_offset_hz:=128000
```

再做对照测试：

```bash
ros2 launch sdr_receiver_py_wrapper debug_receiver.launch.py \
  initial_team:=RED \
  initial_target:=INFO \
  initial_rx_gain:=73 \
  initial_freq_offset_hz:=-128000
```

常用初始参数：

```text
initial_team:=RED 或 BLUE
initial_target:=INFO 或 L1 或 L2 或 L3
initial_rx_gain:=40
initial_freq_offset_hz:=128000
initial_info_l2_rescue:=true
initial_info_l3_rescue:=true
```

## 5. INFO 无法解调时的 RF 诊断

如果 L1/L2/L3 都能破译，而 INFO 完全没有 AC/SOF，先不要只盯 dashboard。建议直接扫描 RF 功率：

```bash
ros2 run sdr_receiver_py_wrapper rf_power_scan -- --red-info --gain 73
ros2 run sdr_receiver_py_wrapper rf_power_scan -- --all-known --gain 60
```

输出字段含义：

```text
rms_avg          多个 RX buffer 的平均 IQ RMS
adc_peak         当前 buffer 最大幅度
peak_offset_khz  最强频谱峰相对当前 LO 的偏移
snr_like_db      最强峰相对频谱中位数的粗略强度
```

判读方式：

- 如果 RED_INFO 的 `rms_avg/snr_like_db` 接近底噪，而 L1/L2/L3 明显更高，说明 INFO 这一路 RF 能量没有进入 RX，继续查 TX 功率、天线、线缆、距离、前端滤波或实际发射频点。
- 如果 RED_INFO 有明显峰值，但 dashboard 仍然 `AC=0`，说明 RF 存在，下一步查 INFO Access Code、调制参数、滤波参数、频偏和脚本版本差异。

若你已确认发射端正在发红方 INFO 且频点无误，建议按以下隔离顺序做：

1. 用 `direct_original_receiver` 跑同一份 v67 原脚本，手动切到 `RED-INFO`，观察是否能出 AC/SOF/CRC16。
2. 若 direct runner 能解，而 wrapper 不能解，保留 direct 和 wrapper 两份 dashboard 截图，对比 `[CFG]` 行中的 `lo/shift/gain/rf_bw/filter/mode`。
3. 若 direct runner 也不能解，则用同一台 SDR、同一根线、同一位置回退到你之前硬件联调确认过能解 INFO 的脚本版本，确认是否是当前 v67 副本或运行环境差异。

## 6. Competition 模式

```bash
ros2 launch sdr_receiver_py_wrapper competition_receiver.launch.py \
  max_jam_break_level:=3 \
  match_slot:=bo3_game1 \
  front_end_id:=front_end_A \
  enable_micro_tune:=false
```

主输入 topic：

```text
/judge/radar_context  sdr_receiver/msg/RadarContext
```

fallback 输入 topic：

```text
/match_info           vision_interface/msg/MatchInfo
/judge/radar_info     std_msgs/msg/UInt8
```

当前 radar 侧新版 `/match_info` 可以直接包含完整上下文：

```text
int8 self_color        # 0 是蓝方 id=109，2 是红方 id=9
uint8 self_id          # 自身 id
int16 match_time       # 比赛时间；未开始为倒计时负数，结束为 -100，裁判系统离线为 -200
uint8 radar_info_raw   # 0x020E 原始数据
uint8[16] robot_hp     # 参考 0x0003
uint8[5] marks
uint8 jam_level        # 己方加密等级，1..3
bool key_mutable       # 当前是否可以修改密钥
bool referee_online    # 是否连接裁判系统
uint8 ultimate
uint32 eventtype
```

输出 topic：

```text
/sdr/jam_code                  sdr_receiver/msg/JamCode
/sdr/radar_wireless/raw_frame  sdr_receiver/msg/RadarWirelessFrame
/sdr/status                    std_msgs/msg/String JSON
```

## 7. 从生成的安装包部署

在 Windows 开发机生成源码安装包：

```bash
cd E:/sdr/iq_recevier/sdr_receiver_py_wrapper
python setup.py sdist
```

将 `dist/` 下生成的 tar.gz 文件复制到 Ubuntu 机器，然后解包到 `~/radar_ws/src/`：

```bash
cd ~/radar_ws/src
tar -xzf /path/to/sdr_receiver_py_wrapper-0.1.0.tar.gz
```

解包后的目录就是正常的 ROS2 `ament_python` 包。和 `sdr_receiver` 一起构建：

```bash
source /opt/ros/humble/setup.bash
cd ~/radar_ws
colcon build --packages-select sdr_receiver sdr_receiver_py_wrapper
source install/setup.bash
```


## TX 开关差分扫描

如果关闭 INFO 发射端和开启 INFO 发射端时扫描结果几乎一样，说明看到的峰值大概率是接收链固定杂散，不是有效 INFO 信号。用下面两步做差分：

```bash
# 1. 关闭 INFO 发射端，保存底噪/杂散 baseline
ros2 run sdr_receiver_py_wrapper rf_power_scan -- --all-known --gain 60 --save-json /tmp/info_off.json

# 2. 打开 INFO 发射端，再和 baseline 对比
ros2 run sdr_receiver_py_wrapper rf_power_scan -- --all-known --gain 60 --baseline-json /tmp/info_off.json
```

重点看 `delta_rms_avg`、`delta_peak_db` 和 `delta_snr_like_db`。如果 RED_INFO 这些 delta 仍接近 0，而 dashboard 也 `AC=0`，说明 RX 侧没有看到 INFO 开关带来的有效变化；这时应优先查 TX 输出链路、实际天线口功率、射频开关/衰减、天线距离和前端路径，不要继续盲目调解调参数。
