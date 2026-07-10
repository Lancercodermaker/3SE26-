# sdr_receiver_py_wrapper

部署文档：

- Ubuntu 22.04 + ROS2 Humble：`docs/DEPLOY_UBUNTU22_PY_WRAPPER.md`
- Windows 离线调试 / Pluto 工具运行：`docs/DEPLOY_WINDOWS_PY_WRAPPER.md`

这是一个面向 ROS2 Humble 的 Python wrapper 包，用于集成已经过硬件联调验证的 SDR 接收端 v67 脚本。

原始 Python 接收脚本通过 `importlib.util.spec_from_file_location()` 动态导入，wrapper 不修改原脚本源码。所有集成都集中在外层 adapter 和 monkey patch 层完成：

- debug 模式尽量保持原脚本 dashboard、键盘控制和手动调试行为不变，同时旁路发布 ROS2 观测 topic。
- competition 模式禁用键盘和 dashboard 交互，由裁判上下文驱动 `TUNE_CFG` 和比赛状态机。
- 输入优先使用 `/judge/radar_context`，也兼容 radar 工程新版 `/match_info` 中直接携带的 `self_id`、`radar_info_raw`、`jam_level`、`key_mutable` 和 `referee_online` 字段。
- 输出 topic 包括 `/sdr/jam_code`、`/sdr/radar_wireless/raw_frame` 和 `/sdr/status`。

本包复用现有 `sdr_receiver` 包中的消息定义，不重新定义接口。v67 原脚本已经随包放在 `sdr_receiver_py_wrapper/vendor/`，作为部署 fallback。`original_script_path` 默认值为 `auto`，adapter 会按以下顺序自动寻找可用脚本：

1. 显式传入的 `original_script_path`
2. 环境变量 `SDR_RECEIVER_ORIGINAL_SCRIPT`
3. 包内 `vendor` 目录中的脚本副本
4. 常见 runtime/source-tree 路径

## 快速运行

```bash
source /opt/ros/humble/setup.bash
source ~/radar_ws/install/setup.bash
source ~/sdr_runtime/venv/bin/activate
export PYTHONPATH=$HOME/sdr_runtime/venv/lib/python3.10/site-packages:$PYTHONPATH

ros2 launch sdr_receiver_py_wrapper debug_receiver.launch.py
ros2 launch sdr_receiver_py_wrapper competition_receiver.launch.py max_jam_break_level:=3
```

`ros2 launch` 通常不会把键盘输入转发给子进程，所以 debug dashboard 可能能显示，但 `r/b/1/2/3/+/q` 等按键不生效。需要交互键盘时，建议直接运行节点：

```bash
ros2 run sdr_receiver_py_wrapper sdr_receiver_py_wrapper_node --ros-args \
  -p run_mode:=debug \
  -p publish_ros_outputs:=true
```

如果要排除 ROS2 wrapper 和 monkey patch 的影响，可以直接运行包内 v67 原脚本：

```bash
ros2 run sdr_receiver_py_wrapper direct_original_receiver
```

这个命令不会启动 ROS2 node，也不会应用任何 patch，只是定位并执行原脚本 `main()`。

如果必须用 `ros2 launch`，可以用启动参数代替键盘先指定队伍、目标和增益：

```bash
ros2 launch sdr_receiver_py_wrapper debug_receiver.launch.py \
  initial_team:=BLUE \
  initial_target:=INFO \
  initial_rx_gain:=40
```

如果 `rf_power_scan` 显示 INFO 最强峰相对 LO 有明显偏移，可以临时加频偏测试。例如 RED_INFO 峰值在 `+127.9 kHz`：

```bash
ros2 launch sdr_receiver_py_wrapper debug_receiver.launch.py \
  initial_team:=RED \
  initial_target:=INFO \
  initial_rx_gain:=73 \
  initial_freq_offset_hz:=128000
```

## IQ file source closed-loop validation

Use a `.c64` IQ recording instead of real `adi.Pluto().rx()` input when you need to run the full ROS2 wrapper, radar project, and referee-system loop without SDR hardware. When `iq_source_path` is empty the node still uses real hardware. When it is set, the wrapper enables the ADI import stub automatically and feeds the original v67 main loop from the IQ file.

If the goal is specifically to let the real radar main project receive a JamCode decoded by the real wrapper from IQ replay, use this entry point:

```bash
ros2 launch sdr_receiver_py_wrapper iq_replay_jam_code.launch.py \
  iq_source_path:=/home/radar/sdr_offline_iq/RX_BLUE_2.c64 \
  iq_source_center_hz:=433920000 \
  iq_source_sample_rate:=2000000 \
  initial_team:=BLUE \
  initial_target:=L1
```

Or:

```bash
~/radar_ws/src/sdr_receiver_py_wrapper/scripts/start_iq_jam_code_replay.sh \
  ~/sdr_offline_iq/RX_BLUE_2.c64 BLUE L1 433920000 2000000
```

This mode still uses the v67 demod/packet parser and publishes the decoded key on `/sdr/jam_code`, but it does not wait for `/judge/radar_context` before publishing. Start the radar main project normally so it subscribes `/sdr/jam_code`.
The v67 dashboard also prints `JAM_CODE L1/L2/L3` near the top of the screen, so a successful IQ replay is visible even when the terminal is too short to show the old bottom `JAM` line.

On Windows, where ROS2 wrapper nodes are usually unavailable, you can still validate the wrapper adapter path without Pluto or ROS2:

```powershell
cd E:\sdr\iq_recevier\sdr_receiver_py_wrapper
python -m sdr_receiver_py_wrapper.wrapper_iq_replay `
  E:\sdr\field_replay\other_teams_downloads\other_teams_offline_validation_package_20260521\iq\RX_BLUE_2.c64 `
  --team BLUE --target L1 --center-hz 433920000 --sample-rate 2000000 `
  --expect-key fcYqTC --no-throttle
```

This command runs `ReceiverCoreAdapter + wrapper patches + IqFilePluto + bundled v67`, so it is a wrapper-level IQ replay check rather than only the standalone field replay tool.

```bash
ros2 launch sdr_receiver_py_wrapper competition_receiver.launch.py \
  iq_source_path:=/home/radar/sdr_offline_iq/RX_BLUE_2.c64 \
  iq_source_loop:=true \
  iq_source_throttle:=true \
  iq_source_sample_rate:=2000000 \
  iq_source_center_hz:=433920000 \
  initial_team:=BLUE \
  initial_target:=L1
```

For the radar workspace deployment, the shorter dedicated entry point is:

```bash
ros2 launch sdr_receiver_py_wrapper iq_replay_receiver.launch.py \
  iq_source_path:=/home/radar/sdr_offline_iq/RX_BLUE_2.c64 \
  iq_source_center_hz:=433920000 \
  iq_source_sample_rate:=2000000 \
  initial_team:=BLUE \
  initial_target:=L1
```

Or run the helper script from the source tree:

```bash
~/radar_ws/src/sdr_receiver_py_wrapper/scripts/start_iq_replay_receiver.sh \
  ~/sdr_offline_iq/RX_BLUE_2.c64 BLUE L1 433920000 2000000
```

To smoke-test the no-Pluto closed loop with the mock referee context and a mock radar-side JamCode subscriber:

```bash
ros2 launch sdr_receiver_py_wrapper iq_replay_closed_loop.launch.py \
  iq_source_path:=/home/radar/sdr_offline_iq/RX_BLUE_2.c64 \
  iq_source_center_hz:=433920000 \
  iq_source_sample_rate:=2000000 \
  initial_team:=BLUE \
  initial_target:=L1
```

Or:

```bash
~/radar_ws/src/sdr_receiver_py_wrapper/scripts/start_iq_closed_loop_smoke.sh \
  ~/sdr_offline_iq/RX_BLUE_2.c64 BLUE L1 433920000 2000000
```

This smoke path verifies `IQ file -> wrapper demod -> /sdr/jam_code -> radar-side subscriber -> mock judge context update`. In the real radar project, replace the mock JamCode subscriber with the radar main launch that subscribes `/sdr/jam_code`.

Key parameters:

- `iq_source_path`: little-endian NumPy `complex64` IQ file path.
- `iq_source_loop`: loop at EOF; useful for long closed-loop validation.
- `iq_source_throttle`: replay at approximately real-time speed from `sample_rate/rx_buffer_size`.
- `iq_source_sample_rate`: IQ file sample rate. Use `2000000` for `RX_BLUE_2.c64` and `RX_RED_2.c64`.
- `iq_source_center_hz`: RF center used when the file was recorded. If set, the file source digitally shifts samples as v67 changes `rx_lo`, approximating hardware LO retune behavior.
- `iq_source_start_offset_sec`: start replay from this file offset.

This mode replays the recorded RF scene. It is excellent for algorithm regression and ROS/referee closed-loop checks, but final bring-up still needs real hardware validation for timing, gain, antennas, temperature drift, and live interference.

In competition mode, `key_retry_limit` defaults to `-1`, so the same decoded key is not capped by count. Repeated publication is still paced by `key_publish_min_interval_sec`.

## RF 功率扫描

当 L1/L2/L3 能破译但 INFO 完全没有 AC/SOF 时，可以先不跑解调，直接扫描各已知频点的 IQ 功率：

```bash
ros2 run sdr_receiver_py_wrapper rf_power_scan -- --all-known --gain 60
ros2 run sdr_receiver_py_wrapper rf_power_scan -- --red-info --gain 73
ros2 run sdr_receiver_py_wrapper rf_power_scan -- --freq 433.2MHz --label RED_INFO --gain 73
```

如果 RED_INFO 的 `rms_avg` 和 `snr_like_db` 明显低于 L1/L2/L3，说明问题在 RF 能量或频点附近；如果 RED_INFO 有明显峰值但解调仍然 `AC=0`，再看 Access Code、调制参数、滤波参数和脚本版本差异。

## 离线检查

不启动 ROS2、不连接 SDR 硬件时，可以运行：

```bash
python -m sdr_receiver_py_wrapper.offline_smoke_test --allow-adi-stub
```

该检查会验证：

- 能导入原 v67 脚本，且不会自动执行 `main()`
- monkey patch 层能捕获 fake `0x0A06` key

## 生成源码安装包

```bash
python setup.py sdist
```

生成的安装包位于 `dist/` 目录，可复制到 Ubuntu 22.04 + ROS2 Humble 机器上解包部署。


## 自适应 Profile Sweep

当 INFO 在低发射功率下容易受环境噪声影响时，不建议人工逐个尝试 `gain/rf_bw/freq_offset`。可以使用自动 sweep 工具按协议层结果评分：

```bash
ros2 run sdr_receiver_py_wrapper adaptive_profile_sweep -- \
  --team RED \
  --profiles INFO \
  --gains 40,50,60,70,73 \
  --rf-bws 160000,220000,300000,420000,540000 \
  --offsets-hz 0,-80000,80000,-150000,150000,-250000,250000 \
  --info-filters normal,loose3 \
  --dwell-sec 2.0
```

如果要针对 `INFO-L2/INFO-L3` rescue 扫描，不要直接复用单独 INFO 的结果，应该分 profile 运行：

```bash
ros2 run sdr_receiver_py_wrapper adaptive_profile_sweep -- \
  --team RED \
  --profiles INFO_L2,INFO_L3 \
  --gains 40,50,60,70,73 \
  --rf-bws 300000,420000,540000,660000,760000 \
  --offsets-hz 0,-80000,80000,-150000,150000,-250000,250000 \
  --dwell-sec 2.0
```

工具输出 `AC/SOF/CRC8/CRC16` 等协议层增量、综合评分、最佳候选和输出目录。Pluto/AD936x 接收端硬件增益上限是 `73 dB`，工具会把更高的误传候选裁剪到 73。普通 INFO offset sweep 使用“LO 偏移 + 数字回正”的临时 profile，不会永久改写原脚本频点。详细说明见 `docs/ADAPTIVE_PROFILE_SWEEP.md`。

## 弱 INFO 旁路探测

如果 `adaptive_profile_sweep` 全部 `NO_LOCK` 且 `AC_RAW=0`，说明协议层统计已经没有可用信息。这时可以先运行 IQ 旁路探测工具，用 soft access-code 相关、硬判 AC 最小错误数和带内频谱能量给候选参数排序：

```bash
ros2 run sdr_receiver_py_wrapper weak_info_probe -- \
  --team RED \
  --gains 70,73 \
  --rf-bws 220000,300000,420000,540000 \
  --offsets-hz 0,-80000,80000,-150000,150000,-250000,250000 \
  --info-filters normal,loose3 \
  --captures 3
```

如果怀疑强窄带杂散污染 FM 鉴频，可加入 notch 候选：

```bash
ros2 run sdr_receiver_py_wrapper weak_info_probe -- \
  --team RED \
  --gains 73 \
  --rf-bws 160000,220000,300000,420000,540000 \
  --offsets-hz 0,-80000,80000,-150000,150000,-250000,250000 \
  --info-filters normal,loose3,wide_loose3,tight_loose3 \
  --notch-modes off,on \
  --captures 4
```

详细说明见 `docs/WEAK_INFO_PROBE.md`。


## TX 开关差分扫描

如果关闭 INFO 发射端和开启 INFO 发射端时扫描结果几乎一样，说明看到的峰值大概率是接收链固定杂散，不是有效 INFO 信号。用下面两步做差分：

```bash
# 1. 关闭 INFO 发射端，保存底噪/杂散 baseline
ros2 run sdr_receiver_py_wrapper rf_power_scan -- --all-known --gain 60 --save-json /tmp/info_off.json

# 2. 打开 INFO 发射端，再和 baseline 对比
ros2 run sdr_receiver_py_wrapper rf_power_scan -- --all-known --gain 60 --baseline-json /tmp/info_off.json
```

重点看 `delta_rms_avg`、`delta_peak_db` 和 `delta_snr_like_db`。如果 RED_INFO 这些 delta 仍接近 0，而 dashboard 也 `AC=0`，说明 RX 侧没有看到 INFO 开关带来的有效变化；这时应优先查 TX 输出链路、实际天线口功率、射频开关/衰减、天线距离和前端路径，不要继续盲目调解调参数。
