# SDR Receiver Python Wrapper Requirements Update 2026-05-20

本文件补充并更正 `E:\sdr\sdr_receiver_python_wrapper_requirements.md`。如旧文档与本文件冲突，以本文件为准。

## 1. 现场信号事实

比赛现场不会单独发送纯净 INFO，也不会单独发送纯净 JAM。现场只会出现三种组合：

- `INFO + L1`
- `INFO + L2`
- `INFO + L3`

正式比赛策略继续保留：开局优先破译 L1/L2/L3，达到配置的最高破译等级后再切 INFO。模拟赛比赛流程与正式比赛完全一致：官方不会自动提高干扰波等级，必须由接收端解出当前等级 `jam_key`，经雷达主工程上报裁判系统，裁判验证无误后更新发给雷达的 `0x020E` 数据包 `radar_info bit3-4`，接收端再依据新的 bit3-4 切到下一等级。模拟赛新增录波策略，主要目标是采集可复盘 IQ 文件，不替代正式比赛状态机。

红蓝方判定需要区分“我方阵营”和“接收阵营”：`self_id` 判定的是我方阵营，但接收端要解调对方的信息/干扰波。映射为：我方 RED(`self_id=9`) 接收 BLUE 波形；我方 BLUE(`self_id=109`) 接收 RED 波形。`/sdr/jam_code.team`、raw frame `team`、录波 metadata 中的 `rx_team` 均表示被解调的对方阵营。

## 2. 明天模拟赛录波策略

模拟赛前只能启动雷达/接收端程序；比赛前官方发射端不会发送任何波形，比赛中雷达必须全自动运行。因此录波策略改为：competition 模式启动后立即自动录 raw IQ，整场比赛只启动一次，不再赛前手动分段抓波。

比赛开始后先录到 `INFO+L1`。若 L1 key 闭环成功，裁判更新 `0x020E radar_info bit3-4` 后继续录到 `INFO+L2`；L2 同理进入 `INFO+L3`。所有阶段保存在同一个连续 `.c64` raw IQ 文件中。

录波 metadata 必须记录：队伍、当前/最后 target、LO、sample rate、RX gain、ADC peak/RMS、是否饱和、录波文件路径与同名 metadata JSON。默认保存目录为 `~/sdr_iq_records`。

## 3. CRC16 适配

由于官方空口结构说明显示 INFO 与 JAM 除数据内容外结构一致，接收端应允许 INFO 与 JAM 均使用两种 CRC16：

- `CRC16/MODBUS`
- `CRC16/KERMIT ^ 0x3014`

`0x0A01..0x0A05` 与 `0x0A06` 均允许两种 CRC16。UI / status / log 必须能区分 `modbus` 与 `kermit-x3014`。保留环境变量开关：`RX_INFO_ALT_CRC16=0` 可关闭 INFO alt CRC，`RX_JAM_ALT_CRC16=0` 可关闭 JAM alt CRC。

## 4. wrapper 集成策略

`E:\sdr\iq_recevier\sdr_receiver_py_wrapper` 是 ROS2 Python wrapper。它的通信层与射频核心解耦：

- 射频/解调核心：`original_receiver_adapter.py`、`patches.py`、`sdr_receiver_py_wrapper/vendor/receiving_messages_adaptive_filter_v67_l2cal_20260505_l2rescue_80k_g40.py`
- ROS2 通信/比赛状态机：`receiver_node.py`、`competition_controller.py`、`launch/*.launch.py`、`sdr_receiver/msg/*.msg`

本次 v67 核心改动应同步到 wrapper 的 `vendor/` 射频核心脚本。默认 `original_script_path:=auto` 必须加载 vendor 版本；如现场要临时指定别的核心，可通过 `SDR_RECEIVER_ORIGINAL_SCRIPT=/path/to/script.py` 或 launch 参数 `original_script_path:=...` 覆盖。

## 5. IQ 录波与 profile 导入

wrapper 必须支持：

- debug/competition 启动时通过 `record_iq:=true` 写 raw IQ `.c64` 文件和同名 `.json` metadata。
- competition 默认自动录 raw IQ；默认目录 `~/sdr_iq_records`，默认上限 900 秒或 16 GiB。
- metadata 记录 sample rate、buffer size、SPS、`own_team`、`rx_team`、target、当前/最后 LO、RF bandwidth、RX gain、ADC RMS、最后 ADC peak/RMS、profile 信息。
- competition 录波文件名前缀必须自动包含 `own_<我方>_vs_<对方>`，例如 `bo3_game1_own_BLUE_vs_RED_*.c64`。
- 启动时可用 `initial_rf_bw_hz` 临时拉宽 INFO 模式模拟带宽，便于录入 `INFO+L1` 这类远邻道组合。
- `adaptive_profile_sweep` 支持 `--record-iq`，用于 sweep 同时保留现场 IQ。
- competition 模式必须读取 `profile_path` 指向的 `best_profile.yaml`；状态机切入 INFO 时自动应用该 profile。

## 6. 联调手册要求

Ubuntu 22.04 联调手册必须覆盖 debug 模式、competition 模式、通信-only 测试、fake `/sdr/jam_code`、`jam_key -> 裁判 -> 0x020E bit3-4` 闭环、赛前 profile sweep 的适用限制、`best_profile.yaml` 导入、模拟赛整场自动录波流程，并且所有路径和 terminal 命令必须写成可直接复制的 Ubuntu 命令。
