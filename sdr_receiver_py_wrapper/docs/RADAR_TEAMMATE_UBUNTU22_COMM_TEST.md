# SDR Wrapper 与雷达主工程联调手册 Ubuntu 22.04

本文面向雷达主工程同学和现场操作同学。目标是在 Ubuntu 22.04 + ROS2 Humble 主机上部署 `sdr_receiver_py_wrapper`，完成通信联调、debug 模式 RF 测试、competition 模式启动、可用信号条件下的 profile 扫描，以及明天模拟赛 `INFO+L1/INFO+L2/INFO+L3` 整场自动录波。

## 0. 先说结论

`sdr_receiver_py_wrapper` 的作用是把已经硬件验证过的 Python v67 接收核心包一层 ROS2 外壳。它不是重写解调器。

解耦边界如下：

- 射频/解调核心：`sdr_receiver_py_wrapper/vendor/receiving_messages_adaptive_filter_v67_l2cal_20260505_l2rescue_80k_g40.py`、`original_receiver_adapter.py`、`patches.py`。
- ROS2 通信与比赛状态机：`receiver_node.py`、`competition_controller.py`、`launch/*.launch.py`、`sdr_receiver/msg/*.msg`。
- 两者通过 callback 连接：Python 核心解出 `0x0A06` key 或 `0x0A01..0x0A05` raw frame 后，patch 层回调 ROS2 node，node 再发布 topic。

本版已整合：

- INFO 和 JAM 都支持 `CRC16/MODBUS` 与 `CRC16/KERMIT ^ 0x3014`。
- JAM RF gate 状态可观测：`rf-classified`、`direct-fallback:*`、`reject:*`。
- debug/competition 均可 `record_iq:=true` 录 `.c64` raw IQ 和同名 `.json` metadata；competition 默认自动录波。
- `adaptive_profile_sweep` 可 `--record-iq`，并输出 `best_profile.yaml`。
- competition 模式会读取 `profile_path`，在状态机切入 INFO 时自动应用 `best_profile.yaml`。

## 1. 固定目录

Ubuntu 雷达主机统一使用这些路径：

```bash
mkdir -p \
  ~/3SE_2026_Radar/src \
  ~/sdr_runtime/venv \
  ~/sdr_runtime/profiles \
  ~/sdr_runtime/profile_sweeps \
  ~/sdr_runtime/logs \
  ~/sdr_iq_records
```

含义：

```text
~/3SE_2026_Radar/src/sdr_receiver                  ROS2 message 包
~/3SE_2026_Radar/src/sdr_receiver_py_wrapper       Python wrapper 包
~/sdr_runtime/venv                           Python venv
~/sdr_runtime/profiles/best_profile.yaml     competition 导入的最佳 profile
~/sdr_runtime/profile_sweeps/                profile 扫描输出
~/sdr_iq_records/                            raw IQ 录波目录
```

## 2. 部署到 Ubuntu

假设 release 包在 Ubuntu 上路径为：

```text
~/Downloads/sdr_wrapper_radar_teammate_20260515
```

复制源码到工作区：

```bash
cd ~/3SE_2026_Radar
mkdir -p src

if [ -d src/sdr_receiver ]; then
  mv src/sdr_receiver src/sdr_receiver.bak.$(date +%Y%m%d_%H%M%S)
fi
if [ -d src/sdr_receiver_py_wrapper ]; then
  mv src/sdr_receiver_py_wrapper src/sdr_receiver_py_wrapper.bak.$(date +%Y%m%d_%H%M%S)
fi

cp -r ~/Downloads/sdr_wrapper_radar_teammate_20260515/src/sdr_receiver src/
cp -r ~/Downloads/sdr_wrapper_radar_teammate_20260515/src/sdr_receiver_py_wrapper src/
```

安装系统依赖：

```bash
sudo apt update
sudo apt install -y \
  python3-venv python3-pip python3-colcon-common-extensions \
  libiio-dev iiod
```

建立 Python venv：

```bash
python3 -m venv ~/sdr_runtime/venv --system-site-packages
source ~/sdr_runtime/venv/bin/activate
pip install -U pip setuptools wheel
pip install -r ~/3SE_2026_Radar/src/sdr_receiver_py_wrapper/requirements.txt
```

编译 ROS2 包：

```bash
source /opt/ros/humble/setup.bash
cd ~/3SE_2026_Radar
colcon build --symlink-install --packages-select sdr_receiver sdr_receiver_py_wrapper
source install/setup.bash
```

每次新开终端先执行：

```bash
source /opt/ros/humble/setup.bash
source ~/3SE_2026_Radar/install/setup.bash
source ~/sdr_runtime/venv/bin/activate
export PYTHONPATH=$HOME/sdr_runtime/venv/lib/python3.10/site-packages:$PYTHONPATH
```

## 3. 离线 smoke test

不接 Pluto，只验证 import、patch 和核心回调：

```bash
ros2 run sdr_receiver_py_wrapper offline_smoke_test -- --allow-adi-stub
```

期望看到类似：

```text
import smoke ok
patch smoke ok
weak_soft smoke ok
```

## 4. 通信-only 联调

终端 A：启动 wrapper，但不启动 RF 接收线程。

```bash
ros2 launch sdr_receiver_py_wrapper competition_receiver.launch.py \
  start_receiver:=false \
  import_allow_adi_stub:=true \
  context_topic:=/judge/radar_context \
  enable_fallback_topics:=true \
  fallback_self_id:=9
```

蓝方把 `fallback_self_id:=9` 改成：

```bash
fallback_self_id:=109
```

终端 B：看状态。

```bash
ros2 topic echo /sdr/status
```

终端 C：发布标准 RadarContext。红方 L1：

```bash
ros2 topic pub /judge/radar_context sdr_receiver/msg/RadarContext \
"{self_id: 9, self_color: 2, radar_info_raw: 40, jam_level: 1, key_mutable: true, game_progress: 4, match_time: 420, referee_online: true}" \
-r 5
```

蓝方 L1：

```bash
ros2 topic pub /judge/radar_context sdr_receiver/msg/RadarContext \
"{self_id: 109, self_color: 0, radar_info_raw: 40, jam_level: 1, key_mutable: true, game_progress: 4, match_time: 420, referee_online: true}" \
-r 5
```

`radar_info_raw` 常用值：

```text
L1 key mutable: 40
L2 key mutable: 48
L3 key mutable: 56
```

## 5. 测主工程接收 JamCode

没有 RF 时也可以手动发 fake key，先测雷达主工程订阅：

```bash
ros2 topic pub --once /sdr/jam_code sdr_receiver/msg/JamCode \
"{valid: true, command_id: 2566, level: 1, team: RED, target: JAM_L1_KEY, radio_mode: test, rf_state: RF_TEST, radar_info_raw: 40, key_mutable: true, key: [65, 66, 67, 49, 50, 51], ascii_code: ABC123}"
```

这里的 `team` 是被解调的对方波形阵营，不是我方阵营。例如我方蓝方测试对方红方 key，就保持 `team: RED`；我方红方测试对方蓝方 key，改成 `team: BLUE`。

主工程 `package.xml` 需要依赖：

```xml
<depend>sdr_receiver</depend>
```

主工程 `CMakeLists.txt` 需要：

```cmake
find_package(sdr_receiver REQUIRED)
ament_target_dependencies(your_target rclcpp sdr_receiver)
```

## 6. Pluto 在线检查

确认 Pluto / AD936x 前端在线：

```bash
iio_info -u ip:192.168.2.1 | head
```

若失败，先检查 USB 网卡、IP、供电和 IIO 服务。

## 7. Debug 模式启动

debug 模式保留原 Python dashboard 和键盘调试能力。用 `ros2 launch` 时通常不能转发键盘；若要按 `r/b/1/2/3/+/-/q`，用本节最后的 `ros2 run` 方式。

只测 INFO，红方：

```bash
ros2 launch sdr_receiver_py_wrapper debug_receiver.launch.py \
  initial_team:=RED \
  initial_target:=INFO \
  initial_rx_gain:=40
```

只测 INFO，蓝方：

```bash
ros2 launch sdr_receiver_py_wrapper debug_receiver.launch.py \
  initial_team:=BLUE \
  initial_target:=INFO \
  initial_rx_gain:=40
```

只测干扰波 L1/L2/L3，红方示例：

```bash
ros2 launch sdr_receiver_py_wrapper debug_receiver.launch.py \
  initial_team:=RED \
  initial_target:=L1 \
  initial_rx_gain:=40

ros2 launch sdr_receiver_py_wrapper debug_receiver.launch.py \
  initial_team:=RED \
  initial_target:=L2 \
  initial_rx_gain:=40

ros2 launch sdr_receiver_py_wrapper debug_receiver.launch.py \
  initial_team:=RED \
  initial_target:=L3 \
  initial_rx_gain:=40
```

蓝方把 `initial_team:=RED` 改成 `initial_team:=BLUE`。

debug 同时录 IQ：

```bash
RUN_TAG=debug_RED_INFO_$(date +%Y%m%d_%H%M%S)

ros2 launch sdr_receiver_py_wrapper debug_receiver.launch.py \
  initial_team:=RED \
  initial_target:=INFO \
  initial_rx_gain:=40 \
  record_iq:=true \
  iq_record_dir:=$HOME/sdr_iq_records \
  iq_record_prefix:=$RUN_TAG \
  iq_record_max_sec:=30 \
  iq_record_max_bytes:=2147483648 \
  iq_record_every_n:=1
```

需要键盘交互时：

```bash
ros2 run sdr_receiver_py_wrapper sdr_receiver_py_wrapper_node --ros-args \
  -p run_mode:=debug \
  -p publish_ros_outputs:=true \
  -p start_receiver:=true
```

## 8. 正式 competition 模式启动

competition 模式禁用键盘交互，由裁判/雷达上下文驱动：

1. 收到有效 `self_id` 判定我方红蓝方。
2. 根据我方阵营自动选择对方波形：我方红方接收蓝方 INFO/JAM，我方蓝方接收红方 INFO/JAM。
3. 收到 `radar_info bit3-4` 判定当前干扰等级。
4. 按当前等级接收并破译对方的 L1/L2/L3 `jam_key`。
5. 解出 key 后发布 `/sdr/jam_code` 给雷达主工程；其中 `team` 字段表示被解调的对方波形阵营，不是我方阵营。
6. 雷达主工程把 key 上报裁判系统；裁判验证无误后，更新发给雷达的 `0x020E` 数据包中 `radar_info bit3-4`，干扰波等级才会提高。
7. wrapper 收到新的 `radar_info bit3-4` 后，才从 L1 切 L2、从 L2 切 L3。
8. 达到 `max_jam_break_level` 后，仍先发布该等级 key；发布最高等级 key 后按既定战术切 INFO。
9. 切入 INFO 时自动应用 `profile_path` 指向的 `best_profile.yaml`。

关键点：官方发射端不会自己自动从 L1 切 L2/L3，等级提升必须依赖“接收端解 key -> 雷达主工程上报 -> 裁判验证 -> 0x020E bit3-4 改变”这个闭环。若 `/sdr/jam_code` 没被主工程接收或没有成功上报裁判，wrapper 会停留在当前等级继续尝试当前等级 key。

阵营映射如下：

```text
我方 RED  self_id=9   -> 接收 BLUE 波形 -> /sdr/jam_code.team=BLUE
我方 BLUE self_id=109 -> 接收 RED 波形  -> /sdr/jam_code.team=RED
```

本版 competition 默认自动录 raw IQ。默认保存目录是：

```text
~/sdr_iq_records
```

默认录波上限为 `900 s` 或 `16 GiB`，先到哪个就停止写文件，但接收/解调线程仍继续运行。按核心默认 `2.5 MS/s`、`complex64` 估算，raw IQ 大约 `20 MB/s`，10 分钟约 `12 GB`，赛前务必确认磁盘空间。

启动命令，我方红方：

```bash
ros2 launch sdr_receiver_py_wrapper competition_receiver.launch.py \
  max_jam_break_level:=3 \
  context_topic:=/judge/radar_context \
  enable_fallback_topics:=true \
  fallback_self_id:=9 \
  profile_path:=$HOME/sdr_runtime/profiles/best_profile.yaml \
  record_iq:=true \
  iq_record_dir:=$HOME/sdr_iq_records \
  iq_record_prefix:=competition \
  iq_record_max_sec:=900 \
  iq_record_max_bytes:=17179869184 \
  iq_record_every_n:=1
```

我方蓝方只改 `fallback_self_id`，录波文件和 metadata 会自动判定为 `own_BLUE_vs_RED`：

```bash
fallback_self_id:=109
```

若现场误接收怀疑来自 INFO alt CRC，可临时关闭：

```bash
export RX_INFO_ALT_CRC16=0
```

若要关闭 JAM alt CRC：

```bash
export RX_JAM_ALT_CRC16=0
```

正常情况下不要关闭它们。

## 9. 赛前 profile sweep

`adaptive_profile_sweep` 会测试一组 INFO/INFO-L2/INFO-L3 参数，按 CRC16/CRC8/SOF/AC 等指标打分，输出：

重要限制：明天模拟赛在比赛开始前，官方发射端不会发送任何 INFO/JAM 波。因此明天赛前不能依赖官方发射端做现场 profile sweep。下面的 sweep 流程只适用于有自备发射端、历史录波回放、训练场持续发波，或之后正式安排了可控测试窗口的情况。明天模拟赛只做第 10 节的自动录波和 competition 启动。

profile 的 `team` 也必须按“要接收的对方波形”来选：我方蓝方扫/导入 RED profile，我方红方扫/导入 BLUE profile。

```text
adaptive_profile_sweep.csv
adaptive_profile_sweep.json
best_profile.yaml
```

有充足时间时，红方全量小 sweep：

```bash
RUN_TAG=profile_RED_$(date +%Y%m%d_%H%M%S)
SWEEP_DIR=$HOME/sdr_runtime/profile_sweeps/$RUN_TAG
mkdir -p "$SWEEP_DIR" "$HOME/sdr_runtime/profiles" "$HOME/sdr_iq_records"

ros2 run sdr_receiver_py_wrapper adaptive_profile_sweep -- \
  --team RED \
  --profiles INFO,INFO_L2,INFO_L3 \
  --gains 24,30,36,40,50,60,70,73 \
  --rf-bws 420000,540000,660000,760000 \
  --offsets-hz 0,80000,-80000,150000,-150000 \
  --info-filters normal,loose3 \
  --l2-filters hist248,hist255,wide263 \
  --l3-filters l3tight,l3cur \
  --settle-sec 0.35 \
  --dwell-sec 1.2 \
  --out-dir "$SWEEP_DIR" \
  --record-iq \
  --iq-record-dir "$HOME/sdr_iq_records" \
  --iq-record-prefix "${RUN_TAG}_sweep" \
  --iq-record-max-sec 240 \
  --iq-record-max-bytes 8589934592

cp "$SWEEP_DIR/best_profile.yaml" "$HOME/sdr_runtime/profiles/best_profile.yaml"
cat "$HOME/sdr_runtime/profiles/best_profile.yaml"
```

蓝方把 `--team RED` 改成：

```bash
--team BLUE
```

如果正式策略最高只破到 L3，且现场时间很短，优先只扫 `INFO_L3`：

```bash
RUN_TAG=profile_L3_RED_$(date +%Y%m%d_%H%M%S)
SWEEP_DIR=$HOME/sdr_runtime/profile_sweeps/$RUN_TAG
mkdir -p "$SWEEP_DIR" "$HOME/sdr_runtime/profiles" "$HOME/sdr_iq_records"

ros2 run sdr_receiver_py_wrapper adaptive_profile_sweep -- \
  --team RED \
  --profiles INFO_L3 \
  --gains 24,30,36,40,50,60,70,73 \
  --rf-bws 540000,660000,760000 \
  --offsets-hz 0,80000,-80000,150000,-150000 \
  --l3-filters l3tight,l3cur \
  --settle-sec 0.25 \
  --dwell-sec 1.0 \
  --out-dir "$SWEEP_DIR"

cp "$SWEEP_DIR/best_profile.yaml" "$HOME/sdr_runtime/profiles/best_profile.yaml"
cat "$HOME/sdr_runtime/profiles/best_profile.yaml"
```

`best_profile.yaml` 应类似：

```yaml
adaptive_profile:
  team: RED
  profile: INFO_L3
  rescue: L3
  filter: l3tight
  gain: 40
  rf_bw_hz: 660000
  freq_offset_hz: 80000
  class: CRC8_STABLE
  score: 123.45
```

competition 导入方式就是第 8 节里的：

```bash
profile_path:=$HOME/sdr_runtime/profiles/best_profile.yaml
```

## 10. 明天模拟赛整场自动录波流程

纠正后的现场约束：

```text
比赛前官方发射端不会发送任何东西。
只有现场比赛开始后，才会从当前裁判等级对应的 INFO+L1 开始发送。
官方不会自动提高干扰等级；必须由接收端解出 jam_key，经雷达主工程上报裁判并验证成功后，裁判才会更新 0x020E 的 radar_info bit3-4，让官方发射端进入 INFO+L2、INFO+L3。
雷达程序只能在比赛前启动一次，比赛中必须全自动运行。
```

因此明天不要在赛前等待官方波形做 profile sweep，也不要指望分三次手动抓 `INFO+L1/INFO+L2/INFO+L3`。正确策略是：赛前启动 competition 模式，接收端一启动就开始写 raw IQ；赛前无信号阶段录到的是底噪，比赛开始后先录到 `INFO+L1`，若 L1 key 闭环成功，再继续在同一个连续录波文件里录到 `INFO+L2`，之后同理录到 `INFO+L3`。

闭环链路如下：

```text
官方发 INFO+L1
  -> SDR wrapper 解出 L1 jam_key
  -> wrapper 发布 /sdr/jam_code
  -> 雷达主工程把 key 发给裁判系统
  -> 裁判验证成功
  -> 雷达收到新的 0x020E，radar_info bit3-4 变为 L2
  -> wrapper 切到 L2 接收
  -> 重复直到 L3；L3 key 发布后切 INFO
```

### 10.1 默认保存位置

默认录波目录：

```text
~/sdr_iq_records
```

默认文件名：

```text
~/sdr_iq_records/competition_YYYYMMDD_HHMMSS.c64
~/sdr_iq_records/competition_YYYYMMDD_HHMMSS.json
```

competition 模式会按 `self_id` 自动把文件名补成“我方 vs 对方”。例如我方蓝方接收红方波形，文件名类似：

```text
~/sdr_iq_records/competition_own_BLUE_vs_RED_YYYYMMDD_HHMMSS.c64
~/sdr_iq_records/competition_own_BLUE_vs_RED_YYYYMMDD_HHMMSS.json
```

若把 `iq_record_prefix:=bo3_match`，则文件名类似：

```text
~/sdr_iq_records/bo3_match_own_BLUE_vs_RED_YYYYMMDD_HHMMSS.c64
~/sdr_iq_records/bo3_match_own_BLUE_vs_RED_YYYYMMDD_HHMMSS.json
```

`.c64` 是 `numpy.complex64` little-endian raw IQ；同名 `.json` 是 metadata，包含 sample rate、当前/最后 LO、RF bandwidth、RX gain、ADC peak/RMS、我方 `own_team`、接收对方 `rx_team`、target、profile 路径等信息。

competition 模式默认 `record_iq:=true`，程序启动后会创建录波器；第一次收到 SDR IQ buffer 时开始写 `.c64` 文件。默认目录是：

```text
~/sdr_iq_records
```

启动脚本会在 `ros2 launch` 前自动创建这个目录；录波器初始化时也会再次确保目录存在。若需要改目录，可在启动前设置：

```bash
export SDR_IQ_RECORD_DIR=/path/to/records
```

### 10.2 每场 BO3 开始前的终端命令

下面命令每一场开赛前运行一次。BO3 三场都运行同一条短命令，不需要改 `MATCH_TAG` 或任何 launch 参数。每场结束后按 `Ctrl+C` 停止，下一场开赛前再次运行完全相同的命令。录波文件用时间戳自动区分，不会覆盖。

现场优先使用脚本启动，避免长段 `ros2 launch ... \` 在终端粘贴时被截断、丢反斜杠或插入 `df -h` 输出。

如果我方是蓝方，运行：

```bash
bash ~/3SE_2026_Radar/src/sdr_receiver_py_wrapper/scripts/start_competition_bo3.sh BLUE
```

如果我方是红方，运行：

```bash
bash ~/3SE_2026_Radar/src/sdr_receiver_py_wrapper/scripts/start_competition_bo3.sh RED
```

脚本内部会自动完成这些事：

- source ROS2、workspace 和 `~/sdr_runtime/venv`。
- 我方蓝方时接收 `RX_TEAM=RED`、`fallback_self_id=109`；我方红方时接收 `RX_TEAM=BLUE`、`fallback_self_id=9`。
- 优先使用 `~/sdr_runtime/profiles/best_profile_${RX_TEAM}.yaml`，其次使用 `~/sdr_runtime/profiles/best_profile.yaml`。
- 找不到 profile 文件时不传 `profile_path` 参数。不要手动传 `profile_path:=` 或 `profile_path:=""`，ROS2 会报 `malformed launch argument 'profile_path:='`。
- 创建 `~/sdr_iq_records` 和 `~/sdr_runtime/profiles`，打印磁盘空间，并用 `bo3_match` 前缀录 900 秒或最多 16 GiB 的 `.c64` IQ。

运行成功时会先看到录波目录和 `df -h` 磁盘空间输出，然后看到类似这些日志：

```text
IQ record dir: /home/sen/sdr_iq_records
```

随后是：

```text
Using adaptive profile: /home/sen/sdr_runtime/profiles/best_profile_RED.yaml
```

或者没有 profile 时：

```text
No adaptive profile found; launching without profile_path.
```

接着会看到：

```text
Launching competition receiver: own=BLUE rx=RED fallback_self_id=109 match=bo3_match
[INFO] ... fallback /match_info subscription enabled
[INFO] ... fallback /judge/radar_info subscription enabled
[INFO] ... sdr_receiver_py_wrapper ready: mode=competition ...
```

如果赛前还没有官方发波，`AC/SOF/CRC16` 长时间为 0 是正常现象；只要没有 Pluto/ADI/ROS 异常，保持运行即可。

协议字节序补充：实测官方信号源每字节内 bit 为 `MSB-first`；多字节字段按裁判系统手册使用小端。当前接收核心也是这个方向：先按 8 bit `MSB-first` 还原字节，再对 `data_len/cmd_id/crc16` 使用小端解析。现场讨论 `MSB-first/LSB-first` 时不要把每个字节反过来。

启动后不要因为赛前没有 AC/SOF/CRC16 就退出；这是正常的，因为官方发射端赛前没有发波。只要终端没有报 Pluto/ADI/ROS 异常，保持程序运行到整场比赛结束。

脚本里的 `RX_TEAM` 是要解调的对方阵营：我方蓝方解红方，我方红方解蓝方。录波文件会自动变成类似：

```text
~/sdr_iq_records/bo3_match_own_BLUE_vs_RED_YYYYMMDD_HHMMSS.c64
~/sdr_iq_records/bo3_match_own_BLUE_vs_RED_YYYYMMDD_HHMMSS.json
```

比赛中若一直停在 L1 或 L2，优先看闭环是否走通：

```bash
ros2 topic echo /sdr/jam_code
ros2 topic echo /judge/radar_context
ros2 topic echo /judge/radar_info
```

期望现象：

```text
/sdr/jam_code 出现 level=1 后，雷达主工程应上报裁判；
裁判通过后，/judge/radar_context.jam_level 或 /judge/radar_info 的 bit3-4 应从 1 变 2；
之后 wrapper 才会切到 L2。L2 到 L3 同理。
```

### 10.3 比赛结束后检查文件

```bash
ls -lh ~/sdr_iq_records | tail -20
```

检查最近一个 `.c64` 是否非空：

```bash
python3 - <<'PY'
import glob
import json
import numpy as np
from pathlib import Path

paths = sorted(glob.glob(str(Path.home() / 'sdr_iq_records' / '*.c64')))
if not paths:
    raise SystemExit('no c64 files found')
path = paths[-1]
x = np.fromfile(path, dtype=np.complex64, count=160000)
meta_path = path[:-4] + '.json'
print('iq_file=', path)
print('samples_checked=', len(x))
print('max_abs=', float(np.max(np.abs(x))) if len(x) else 0.0)
print('rms=', float(np.sqrt(np.mean(np.abs(x) ** 2))) if len(x) else 0.0)
if Path(meta_path).exists():
    meta = json.loads(Path(meta_path).read_text())
    print('metadata=', meta_path)
    print('last_peak=', meta.get('last_peak'), 'last_rms=', meta.get('last_rms'))
PY
```

ADC 判断：

```text
last_peak >= 0.92 或 dashboard 显示 SAT：饱和，下一段 RX_GAIN 降 6 到 10 dB
last_rms < 0.02 且没有 AC/SOF：偏弱，下一段 RX_GAIN 升 6 dB
last_peak 0.2 到 0.8：通常可接受
```

注意：整场自动模式下，接收端会按裁判回传的 `0x020E radar_info bit3-4` 在 L1/L2/L3/INFO 之间切换；这个切换不是官方自动发生，而是依赖 `/sdr/jam_code` 上报闭环成功。`.c64` 是连续录波。`.json` 记录最后一次关闭时的状态和最近 ADC 统计；若要复盘每次切换对应的 LO 和 target，保留启动终端日志，里面会有 `[CFG] ... lo=...` 和 target 切换信息。

## 11. topic 观察

常用观察命令：

```bash
ros2 topic echo /sdr/status
ros2 topic echo /sdr/jam_code
ros2 topic echo /sdr/radar_wireless/raw_frame
```

只看 topic 是否存在：

```bash
ros2 topic list | grep -E 'sdr|judge|match_info'
```

## 12. 常见问题

### ModuleNotFoundError: adi

通信-only 模式使用：

```bash
start_receiver:=false
import_allow_adi_stub:=true
```

真实 RF 模式必须安装 `pyadi-iio` 并能 `iio_info -u ip:192.168.2.1`。

### wrapper 不随 /match_info 改变状态

当前主工程的 `/match_info` 可能没有 `radar_info_raw/jam_level/key_mutable`。需要额外发布：

```bash
ros2 topic pub /judge/radar_info std_msgs/msg/UInt8 "{data: 40}" -r 1
```

更推荐主工程直接发布 `/judge/radar_context`。

### profile_path 没生效

确认文件存在：

```bash
ls -lh ~/sdr_runtime/profiles/best_profile.yaml
cat ~/sdr_runtime/profiles/best_profile.yaml
```

确认 competition 已切入 INFO 后再看 `/sdr/status`，profile 只在切入 INFO 时应用，不影响 L1/L2/L3 key 破译。

### AMENT_TRACE_SETUP_FILES 未绑定变量

如果看到：

```text
/opt/ros/humble/setup.bash: 行 8: AMENT_TRACE_SETUP_FILES: 未绑定的变量
```

说明旧版 `start_competition_bo3.sh` 开了 `set -u`，而 ROS2 的 `setup.bash` 不兼容这个模式。更新脚本后再运行；临时修复可把脚本第二行改成：

```bash
set -eo pipefail
```

### 录波文件过大

`2.5 MS/s`、`complex64` 约 20 MB/s。12 秒约 240 MB。正式赛 480 秒约 9.6 GB。赛前检查磁盘：

```bash
df -h ~
```

如果空间紧张，可调大抽样间隔：

```bash
iq_record_every_n:=2
```

这会每两个 RX buffer 写一个，文件约减半，但不再是完整连续录波。
