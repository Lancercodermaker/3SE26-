# SDR Receiver Python Wrapper 后续 Debug 交接文档

日期：2026-05-13

## 1. 当前工程位置

Windows 开发机：

```text
E:\sdr\iq_recevier\sdr_receiver_py_wrapper
E:\sdr\iq_recevier\sdr_receiver
```

Ubuntu 测试机：

```text
~/radar_ws/src/sdr_receiver_py_wrapper/
~/radar_ws/src/sdr_receiver/
~/sdr_runtime/venv/
```

wrapper 安装包：

```text
E:\sdr\iq_recevier\sdr_receiver_py_wrapper\dist\sdr_receiver_py_wrapper-0.1.0.tar.gz
```

## 2. 已实现内容

新增 ROS2 Python wrapper 包 `sdr_receiver_py_wrapper`：

- 原 v67 Python 接收脚本零改动，放在 `sdr_receiver_py_wrapper/vendor/`。
- 使用 `importlib.util.spec_from_file_location()` 动态导入原脚本。
- `original_script_path=auto` 自动搜索：
  - 显式参数
  - `SDR_RECEIVER_ORIGINAL_SCRIPT`
  - 包内 `vendor`
  - 常见 runtime/source-tree 路径
- `patches.py` 集中 monkey patch：
  - `validate_and_parse`
  - `handle_keyboard`
  - `init_dashboard`
  - `render_dashboard`
  - `restore_terminal`
  - `select_tune_target`
- `CompetitionController` 已实现 L1/L2/L3 -> INFO 状态机。
- ROS2 输出：
  - `/sdr/jam_code`
  - `/sdr/radar_wireless/raw_frame`
  - `/sdr/status`
- 输入：
  - 优先 `/judge/radar_context`
  - fallback `/match_info`
  - fallback `/judge/radar_info`
- 支持新版 radar `/match_info` 字段：

```text
int8 self_color        # 0 蓝 id=109，2 红 id=9
uint8 self_id
int16 match_time
uint8 radar_info_raw
uint8[16] robot_hp
uint8[5] marks
uint8 jam_level
bool key_mutable
bool referee_online
uint8 ultimate
uint32 eventtype
```

## 3. Ubuntu 运行环境问题与解决

出现过 `ros2: command not found`，原因是 venv 激活后 PATH/环境顺序混乱。

推荐运行顺序：

```bash
cd ~/radar_ws
source ~/sdr_runtime/venv/bin/activate
source /opt/ros/humble/setup.bash
source ~/radar_ws/install/setup.bash
export PYTHONPATH=$HOME/sdr_runtime/venv/lib/python3.10/site-packages:$PYTHONPATH
```

出现过：

```text
ModuleNotFoundError: No module named 'adi'
```

已通过安装 `pyadi-iio` 解决：

```bash
pip install pyadi-iio
python -c "import adi; print(adi.__file__)"
```

注意：即使 venv 的 `python` 能 import `adi`，ROS2 node 仍可能看不到，因此需要上面的 `PYTHONPATH` 注入。

## 4. 已发现的运行现象

### 4.1 键盘问题

`ros2 launch` 启动 debug 模式时，dashboard 能显示，但 `r/b/1/2/3/+/q` 等按键不生效。

原因：`ros2 launch` 通常不把 stdin 转发给子进程。

替代：

```bash
ros2 run sdr_receiver_py_wrapper sdr_receiver_py_wrapper_node --ros-args \
  -p run_mode:=debug \
  -p publish_ros_outputs:=true
```

或者用 launch 参数代替键盘：

```bash
ros2 launch sdr_receiver_py_wrapper debug_receiver.launch.py \
  initial_team:=RED \
  initial_target:=INFO \
  initial_rx_gain:=73
```

### 4.2 L1/L2/L3 干扰波可破译

测试结果：

- RED-L1 能 CRC_LOCKED，`cmd:0x0a06`
- RED-L2 能 CRC_LOCKED，`cmd:0x0a06`
- RED-L3 能 CRC_LOCKED，`cmd:0x0a06`
- key 命中数持续增加

说明：

- SDR 硬件接收链路可用
- pyadi-iio/libiio 可用
- 原 v67 脚本的基本采样、2GFSK、CRC16、0x0A06 解析链路可用
- wrapper 不是主要阻塞点

### 4.3 INFO 无法解调

现象：

- RED-INFO 下 `AC=0`
- `SOF=0`
- `CRC8=0`
- `CRC16=0`
- `cmd=0x0000`
- ADC/RMS 很低
- 增益从 40 拉到 73 后 ADC/RMS 有所上升，但仍无 AC

已确认：

- 发射端声称在发红方 INFO
- 发射端频点配置声称无误
- direct runner 直接跑原 v67 脚本，不经过 ROS2 wrapper 和 monkey patch，INFO 仍无法解调

结论：

wrapper 基本排除。问题更可能在当前 RX 侧没有实际看到 INFO 发射端带来的有效 RF 变化，或者 INFO 物理层/Access Code/调制参数与当前 v67 假设不一致。

## 5. RF Power Scan 工具与结果

新增工具：

```bash
ros2 run sdr_receiver_py_wrapper rf_power_scan -- --all-known --gain 60
```

后续修正后，输出为和 dashboard 一致的归一化 ADC/RMS，并支持 baseline 差分。

### 5.1 TX off baseline

关闭 INFO 发射端：

```bash
ros2 run sdr_receiver_py_wrapper rf_power_scan -- --all-known --gain 60 --save-json /tmp/info_off.json
```

结果摘要：

```text
RED_INFO  rms_avg=0.003145  adc_peak=0.011435  peak_offset_khz=127.9  snr_like_db=35.73
BLUE_INFO rms_avg=0.003177  adc_peak=0.011741  peak_offset_khz=127.9  snr_like_db=35.41
RED_L1    rms_avg=0.003138  adc_peak=0.010368  peak_offset_khz=-592.1 snr_like_db=33.95
RED_L2    rms_avg=0.003107  adc_peak=0.011880  peak_offset_khz=-188.3 snr_like_db=30.78
RED_L3    rms_avg=0.003110  adc_peak=0.010895  peak_offset_khz=527.9  snr_like_db=34.87
```

### 5.2 TX on comparison

打开 INFO 发射端后：

```bash
ros2 run sdr_receiver_py_wrapper rf_power_scan -- --all-known --gain 60 --baseline-json /tmp/info_off.json
```

结果摘要：

```text
RED_INFO  delta_rms_avg=-0.000023  delta_peak_db=0.09   delta_snr_like_db=0.22
BLUE_INFO delta_rms_avg= 0.000019  delta_peak_db=0.48   delta_snr_like_db=0.52
RED_L1    delta_rms_avg=-0.000014  delta_peak_db=0.26   delta_snr_like_db=0.19
RED_L2    delta_rms_avg= 0.000001  delta_peak_db=0.16   delta_snr_like_db=0.18
RED_L3    delta_rms_avg= 0.000003  delta_peak_db=-1.19  delta_snr_like_db=-1.17
```

判读：

- INFO 开关前后 `RED_INFO` 几乎无变化。
- 之前看到的 `+127.9 kHz` 强峰在 TX off 时也存在，是固定杂散/本振泄漏/环境固定信号，不是 INFO 发射端。
- `initial_freq_offset_hz:=128000` 测试 AC 仍为 0，符合该判读。

当前最强结论：

```text
RX 侧没有看到 INFO 发射端开关带来的有效变化。
```

## 6. 当前最可能原因

优先级从高到低：

1. INFO 发射端实际 RF 没有从天线口有效输出。
2. TX/RX 之间 INFO 链路路径有问题，例如射频开关、衰减、线缆、天线、距离、遮挡。
3. INFO 发射功率为官方 `-60 dBm`，现场链路预算太低，当前接收条件下低于解调门限。
4. 发射端 GUI/脚本虽然显示红方 INFO，但实际硬件输出未切换到 INFO 或输出链路未启用。
5. INFO 物理层参数与当前 v67 脚本假设不一致，包括 Access Code、调制参数、切片方式。
6. 当前 wrapper 包内 v67 脚本不是之前硬件联调成功的那份脚本，或运行参数存在差异。

## 7. 下一步建议

1. 用频谱仪或另一台已知可用 SDR/工具直接测 TX 天线口附近是否有 433.200 MHz INFO 信号。
2. 把 INFO TX 与 RX 拉近，或短距离耦合/小衰减直连测试，确认 RX 侧 `delta_rms_avg` 是否能明显变化。
3. 与 L1/L2/L3 发射源使用同一条 RF 路径做对照，排除 INFO 发射链路独立故障。
4. 回退到之前硬件联调成功的原 Python 脚本，在同一 Ubuntu/同一 SDR/同一位置跑 INFO，确认是否脚本版本差异。
5. 若 RF 差分确认 INFO 进来了但仍 AC=0，再进一步比对 INFO Access Code、Header、GFSK 参数和发射端 air framing。

## 8. 常用命令

环境：

```bash
cd ~/radar_ws
source ~/sdr_runtime/venv/bin/activate
source /opt/ros/humble/setup.bash
source ~/radar_ws/install/setup.bash
export PYTHONPATH=$HOME/sdr_runtime/venv/lib/python3.10/site-packages:$PYTHONPATH
```

重建：

```bash
colcon build --packages-select sdr_receiver sdr_receiver_py_wrapper
source ~/radar_ws/install/setup.bash
```

debug 指定 INFO：

```bash
ros2 launch sdr_receiver_py_wrapper debug_receiver.launch.py \
  initial_team:=RED \
  initial_target:=INFO \
  initial_rx_gain:=73
```

direct runner：

```bash
ros2 run sdr_receiver_py_wrapper direct_original_receiver
```

TX off/on 差分：

```bash
ros2 run sdr_receiver_py_wrapper rf_power_scan -- --all-known --gain 60 --save-json /tmp/info_off.json
ros2 run sdr_receiver_py_wrapper rf_power_scan -- --all-known --gain 60 --baseline-json /tmp/info_off.json
```

