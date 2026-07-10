# RoboMaster Radar SDR Competition RF Auto Scan Architecture

## 现有链路

雷达主工程 `radar_referee`：

1. 发布 `/match_info`，其中包含 `self_id`、`jam_level`、`key_mutable`、`radar_info_raw`、`referee_online` 等比赛上下文。
2. 订阅 `/sdr/jam_code`。
3. 收到 `JamCode` 后记录 `Received JamCode` 并更新雷达业务侧 key。

wrapper `sdr_receiver_py_wrapper`：

1. `receiver_node.py` 运行 ROS2 节点。
2. `CompetitionController` 根据裁判上下文决定当前应该接收对方 RED/BLUE 的 L1/L2/L3/INFO。
3. `OriginalReceiverAdapter` 封装 v67 原始接收机，提供临时设置 team、target、gain、frequency offset、profile 的能力。
4. v67 负责真实 SDR 配置、解调、CRC 校验、key 解码。
5. wrapper 将解出的 key 发布到 `/sdr/jam_code`。

自动 RF 扫描应插入 wrapper 内部，不改雷达主工程，不改 ROS 消息定义，不改 v67 解包协议。

## 模块划分

建议新增模块：

```text
sdr_receiver_py_wrapper/rf_auto_scan_controller.py
```

职责：

1. 保存自动扫描状态机。
2. 根据 stats 判断是否触发扫描。
3. 生成分层候选 gain/offset 队列。
4. 记录每个候选开始和结束时的 stats。
5. 计算 delta、score、result。
6. 判断 lock/no_lock/cancel。
7. 生成结构化日志事件。

建议在 `receiver_node.py` 中增加：

```text
self.rf_auto_scan = RfAutoScanController(...)
self.rf_scan_timer = self.create_timer(rf_scan_tick_sec, self._tick_rf_auto_scan)
```

`receiver_node.py` 只做协调：

1. 获取当前 adapter stats。
2. 获取当前 radio snapshot。
3. 获取当前 competition state。
4. 调用 controller tick。
5. 根据 controller 输出调用 adapter 设置 gain/offset。
6. 将 controller 状态并入 `/sdr/status`。

`original_receiver_adapter.py` 建议增加：

```text
set_frequency_offset(team, target, offset_hz)
set_jam_radio_candidate(team, target, gain, offset_hz)
```

其中 `set_frequency_offset(..., 0)` 必须真实恢复中心频点。现有 `apply_frequency_offset(..., 0)` 会直接 return，不适合自动扫描。

## 状态机

建议状态：

```text
DISABLED
IDLE
ARMED
SCANNING
LOCKED
COOLDOWN
NO_LOCK
```

状态含义：

`DISABLED`：参数关闭，完全不运行。

`IDLE`：比赛模式正常监听，未触发扫描。

`ARMED`：发现可能故障，等待短暂稳定窗口，避免刚切 target/team 就误触发。

`SCANNING`：正在逐个尝试 gain/offset 候选。

`LOCKED`：某个候选产生 `CRC16 delta > 0`，保留该参数。

`COOLDOWN`：扫描刚结束或刚切换配置，短时间内不重复触发。

`NO_LOCK`：完整候选队列都没有 CRC16，记录失败并等待下一次触发窗口。

## 主流程

每 `rf_scan_tick_sec` 调用一次 tick：

```text
1. 如果 disabled，返回。
2. 读取 run_mode、target、own_team、rx_team、competition state。
3. 如果不是 competition 或 target 不在 L1/L2/L3，reset 到 IDLE。
4. 如果 team/target 变化，清除旧 lock 和扫描队列。
5. 读取 protocol stats 和 radio snapshot。
6. 更新 CRC16 最近增长时间。
7. 如果 IDLE/NO_LOCK/COOLDOWN 且触发条件成立，进入 SCANNING。
8. 如果 SCANNING 且没有当前候选，启动下一个候选。
9. 如果 SCANNING 且当前候选 dwell 满 1s，计算 delta。
10. 如果 CRC16_delta > 0，进入 LOCKED。
11. 如果候选耗尽，进入 NO_LOCK。
12. 输出需要执行的 adapter action 和需要写入的 log event。
```

## 候选应用

候选包含：

```text
team
target
gain
offset_hz
stage
```

adapter 应用顺序建议：

```text
1. set_team(rx_team)
2. set_target(target)
3. set_frequency_offset(rx_team, target, offset_hz)
4. set_manual_gain(target, gain)
```

如果实现 `set_jam_radio_candidate(...)`，则由 adapter 在一个锁内完成以上操作，减少中间状态。

注意：v67 在配置变化后会重置 bit pool、同步状态和部分协议统计，这是预期行为。自动扫描评价必须基于候选本地 delta。

## Delta 统计

每个候选启动时记录：

```text
start_stats = adapter.get_protocol_stats_snapshot()
start_radio = adapter.get_current_radio_snapshot()
start_time = monotonic()
```

候选结束时记录：

```text
end_stats = adapter.get_protocol_stats_snapshot()
end_radio = adapter.get_current_radio_snapshot()
end_time = monotonic()
```

delta 计算：

```text
delta[key] = max(0, int(end_stats[key]) - int(start_stats[key]))
```

核心字段：

```text
AC_RAW
AC
SOF
CRC8
CRC16
CRC16_FAIL
FRAME_REJECT
ADC_RMS
RF_STATE
LAST_CRC16_CMD
LAST_CRC16_MODE
```

锁定判据：

```text
CRC16_delta > 0
```

score 只用于复盘和未来优化，不作为第一版锁定条件替代 CRC16。

## 日志架构

建议新增轻量 logger：

```text
RfAutoScanJsonlLogger
```

职责：

1. 初始化时创建目录。
2. 打开独立 `.jsonl` 文件。
3. 每次事件写一行 JSON，并 flush。
4. 提供 `path` 给 ROS log、`/sdr/status` 和 IQ metadata。

不建议只写普通 ROS log，因为赛后 wrapper 输出可能不完整，且不方便与 IQ 录波按时间对齐。

日志路径建议：

```text
~/sdr_runtime/rf_scan_logs/rf_scan_<match_slot>_<front_end_id>_<YYYYMMDD_HHMMSS>.jsonl
```

启动时打印：

```text
RF_AUTO_SCAN log_path=/home/sen/sdr_runtime/rf_scan_logs/rf_scan_bo3_match_front_end_A_20260523_120000.jsonl
```

## 与 IQ 录波的关系

自动扫描不控制 IQ 录波开关。`record_iq:=true` 仍由原有 `IqRecorder` 管理。

影响是：同一 `.c64` 文件中可能包含多个 gain/offset 阶段。现有 IQ metadata 只能表达当前/最终 radio snapshot，不适合作为扫参时间线。因此：

1. IQ 录波继续写。
2. 扫描日志独立记录每个候选的时间、gain、offset、rx_lo、rf_bw。
3. IQ metadata 可额外加入 `rf_auto_scan_log_path`，方便赛后找到对应日志。

## 与 CompetitionController 的边界

不要在扫描中重置 `CompetitionController`。

原因：

1. 已解出的旧 key 需要继续按 `key_publish_min_interval_sec` 重复发布。
2. `key_retry_limit=-1` 的行为必须保留。
3. 扫描只改变 RF 接收参数，不改变比赛状态机。

当 `CompetitionController` 因裁判上下文切换到新 target 或新 team 时，RF scan controller 应 reset：

```text
clear active candidate
clear lock
clear tried set
return to IDLE/ARMED
```

## 与 v67 vendor 的边界

不建议把第一版自动扫描直接写进 v67 vendor 文件。

原因：

1. v67 内部 calibration 目前更偏 INFO rescue/L2/L3 profile。
2. vendor 内部日志路径、比赛状态、ROS status 都不够贴合本需求。
3. wrapper 层已有 adapter，可以不改协议解包核心。
4. wrapper 层更容易做单元测试和赛后可观测性。

可以复用的思想：

1. `adaptive_profile_sweep.py` 的候选测量方式。
2. v67 calibration 的 `score_calibration_stats` 思路。
3. v67 的 `RF_STATE`、`ADC_RMS`、`LAST_CRC16_TIME` 诊断字段。

不直接复用的部分：

1. 不扫 `rf_bw`。
2. 不使用 INFO rescue filter。
3. 不保存 best profile。
4. 不依赖 vendor 内部 `CAL_PROFILE` 做 JAM L1/L2/L3 扫描。

## 预计改动文件

核心改动：

```text
src/sdr_receiver_py_wrapper/sdr_receiver_py_wrapper/rf_auto_scan_controller.py
src/sdr_receiver_py_wrapper/sdr_receiver_py_wrapper/receiver_node.py
src/sdr_receiver_py_wrapper/sdr_receiver_py_wrapper/original_receiver_adapter.py
src/sdr_receiver_py_wrapper/launch/competition_receiver.launch.py
src/sdr_receiver_py_wrapper/config/competition_receiver.yaml
```

部署脚本改动：

```text
src/sdr_receiver_py_wrapper/scripts/start_competition_bo3.sh
```

测试文件：

```text
src/sdr_receiver_py_wrapper/test/test_rf_auto_scan_controller.py
```

可选文档：

```text
src/sdr_receiver_py_wrapper/docs/RF_AUTO_SCAN.md
```

雷达主工程 `radar_referee` 原则上不改。

## 风险与缓解

风险：扫描期间重配 SDR 会短时间错过帧。

缓解：只在 `CRC16` 已经 3 秒不增长或 `RF_LOW` 时触发。

风险：高 gain 导致饱和。

缓解：记录 `SATURATED` 和 `ADC_RMS`，后续可跳过更高 gain；第一版先日志化，不复杂化决策。

风险：offset 无法恢复 0。

缓解：新增显式 `set_frequency_offset(..., 0)`，测试覆盖。

风险：同一 `.c64` 中多参数混合导致赛后误判。

缓解：独立 JSONL 扫描日志记录完整时间线，并在 `/sdr/status` 和 IQ metadata 中写 log path。

风险：队伍/目标切换后沿用旧 lock。

缓解：controller 检测 `(rx_team, target)` key 变化并 reset。

## 验证方案

### 1. 静态检查

```bash
cd /home/sen/3SE_2026_Radar
colcon build --packages-select sdr_receiver_py_wrapper
python3 -m compileall src/sdr_receiver_py_wrapper/sdr_receiver_py_wrapper
```

通过标准：

```text
build 成功
compileall 无语法错误
```

### 2. 单元测试

建议覆盖：

1. `RF_LOW` 触发扫描。
2. `CRC16` 超过 3 秒未增长触发扫描。
3. `CRC16_delta > 0` 立即 lock。
4. 队伍变化 reset lock。
5. 目标 L1 -> L2 reset lock。
6. INFO 不触发。
7. debug 模式不触发。
8. 候选顺序符合分层设计。
9. `offset=0` 能恢复中心频点。
10. 日志目录和日志文件自动创建。

命令：

```bash
cd /home/sen/3SE_2026_Radar
colcon test --packages-select sdr_receiver_py_wrapper
colcon test-result --verbose
```

### 3. 无 Pluto IQ replay 回归

正样本：

```bash
/home/sen/3SE_2026_Radar/src/sdr_receiver_py_wrapper/scripts/start_iq_jam_code_replay.sh \
  /home/sen/sdr_iq_records/RX_BLUE_ganrao_1 BLUE L1 433920000 2000000
```

预期：

```text
能发布 /sdr/jam_code
默认不触发 RF auto scan
雷达主工程日志出现 Received JamCode
```

失败现场录波可作为触发路径测试。如果显式开启 replay scan，则预期生成扫描日志，但不要求能 lock，因为离线文件的 gain/offset 不一定能改变历史录波结果。

### 4. 真 Pluto L1 台架测试

启动：

```bash
/home/sen/3SE_2026_Radar/src/sdr_receiver_py_wrapper/scripts/start_competition_bo3.sh RED
```

条件：

```text
own RED => 接收 opponent BLUE
裁判上下文 jam_level=1
发射端发送 BLUE-L1
```

先人为制造弱接收条件，例如拉开距离、降低发射链路、衰减或低初始 gain。

预期：

```text
3 秒无 CRC16 或 RF_LOW 后自动扫描
扫描日志出现 trigger/candidate_result
某档 CRC16_delta > 0 后 lock
/sdr/jam_code 发布真实 key
radar_referee 出现 Received JamCode
```

### 5. L2/L3 台架测试

重复 L1 流程，将裁判上下文切到 L2/L3。

预期：

```text
target 变化后旧 lock 清除
重新扫描当前等级
lock 后发布对应 level 的 JamCode
```

### 6. 饱和场景测试

把发射端靠近接收天线或提高输入强度。

预期：

```text
日志中能看到 SATURATED 或 ADC_RMS 偏高
程序不崩溃
不会错误持久化坏参数
```

### 7. 赛后复盘验证

赛后收集：

```text
wrapper 普通日志
rf_scan JSONL 日志
IQ .c64
IQ .json metadata
radar_referee 日志
```

检查：

```text
rf_scan 日志路径是否在启动日志和 /sdr/status 中出现
IQ metadata 是否能找到 rf_auto_scan_log_path
Received JamCode 时间是否落在 scan lock 之后
lock 候选的 gain/offset 是否与当时 rx_lo/rf_state 一致
```

## 实现顺序建议

1. 先实现纯 Python `RfAutoScanController` 和单元测试。
2. 再补 adapter 的 `set_frequency_offset(..., 0)` 能力。
3. 接入 `receiver_node.py` timer 和 `/sdr/status`。
4. 接入独立 JSONL 日志。
5. 接入 launch 参数和 `start_competition_bo3.sh` 环境变量。
6. 最后做真 Pluto 台架验证。

这个顺序能让大部分逻辑在没有硬件时先被测试覆盖，真正上场前只剩 SDR 重配和现场信号链路需要验证。
