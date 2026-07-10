# RoboMaster Radar SDR Competition RF Auto Scan Requirements

## 背景

现场比赛录波中出现过 `rf_state == RF_LOW`、`CRC16 == 0`、雷达主工程没有收到 `/sdr/jam_code` 的情况；但赛后使用同一接收端和己方发射端测试可以解出 `jam_key`。这说明 ROS 主链路和硬件基础路径不是唯一问题，比赛失败更可能与接收端前端射频参数有关，例如接收增益偏低、中心频点存在小偏差、现场链路余量不足。

本需求是在现有 `sdr_receiver_py_wrapper` competition 模式中增加一个面向比赛的自动 RF 参数扫描机制，使无人值守比赛场景下，wrapper 能在长时间没有 CRC16 或 RF 低电平时自动尝试更合适的接收 gain/小频偏，并在找到 CRC16 后锁定参数，继续向雷达主工程发布真实 wrapper 解码出的 `JamCode`。

## 目标

1. 在比赛模式中自动发现更可用的接收 gain/小频偏组合。
2. 当 `CRC16` 超过 3 秒未增长，或 `rf_state == RF_LOW` 时自动触发扫描。
3. 扫描范围覆盖 L1/L2/L3 干扰波。
4. 每个候选参数驻留 1 秒。
5. 找到 `CRC16 delta > 0` 的候选后立即锁定该参数，直到队伍或目标等级变化。
6. 扫描日志独立于普通 ROS 日志和 IQ 录波元数据，自动创建，并在启动日志和 `/sdr/status` 中明确显示完整路径。
7. 保持默认 IQ 录波继续开启，不因扫描而停止录波。
8. 扫描参数只在本次运行内临时生效，不写回源码、不持久化 profile。

## 非目标

1. 暂不扫描 `rf_bw`。
2. 暂不做长期自学习或跨比赛持久化。
3. 暂不修改官方/v67 数据包解析逻辑。
4. 暂不修改雷达主工程 `radar_referee` 的 `/sdr/jam_code` 消费逻辑。
5. 暂不把 INFO 作为有效游戏状态数据，除非未来真实运行产生 `INFO CRC16 > 0`。
6. 暂不把默认 L1/L2/L3 gain 直接全局调高作为唯一修复。

## 触发条件

自动扫描只允许在以下条件全部满足时启动：

1. wrapper 处于 `run_mode == competition`。
2. 当前接收目标为 `L1`、`L2` 或 `L3`。
3. 已有有效比赛上下文，能确定 `own_team` 和 `rx_team`。
4. 当前未处于已锁定且 CRC16 持续增长的正常状态。
5. 满足以下任一故障条件：
   - `CRC16` 计数超过 `3.0s` 未增长。
   - `rf_state == RF_LOW`。

不应在以下情况触发：

1. `target == INFO`。
2. debug 模式。
3. no-Pluto IQ replay 默认路径，除非显式开启测试参数。
4. 目标或队伍刚切换后的短暂稳定期内，避免刚配置就误判。

## 扫描候选

固定候选 gain：

```text
22, 30, 36, 40, 44, 50, 56
```

固定候选频偏：

```text
-80000, -40000, 0, 40000, 80000 Hz
```

分层优先级：

1. 第一层：offset `0`，gain `36,44,50,56`。
2. 第二层：offset `0`，gain `22,30,40`。
3. 第三层：offset `-80000,-40000,40000,80000`，gain `44,50,56`。
4. 第四层：全组合兜底，跳过已经尝试过的候选。

每档驻留时间为 `1.0s`。如果候选期间产生 `CRC16 delta > 0`，立即判定为可用并锁定。

## 统计口径

扫描评价必须使用每个候选的局部增量，而不是直接使用全局累计值。

原因：v67 在切换 gain、offset、target 或 team 时会重配 SDR，并重置部分协议统计和跟踪状态，包括 `AC`、`SOF`、`CRC8`、`CRC16`、`LAST_CRC16_TIME` 等。因此全局统计不是稳定的全程累计值。

每个候选应记录：

```text
start_stats = candidate_start 时刻的协议统计
end_stats   = candidate_end 时刻的协议统计
delta       = max(0, end_stats - start_stats)
```

关键判据：

```text
CRC16_delta > 0 => LOCK
CRC8/SOF/AC delta > 0 => 可作为排序和复盘参考
RF_LOW/SATURATED => 可作为失败原因和后续跳过策略参考
```

## IQ 录波要求

自动扫描不得关闭或中断默认 IQ 录波。

扫描期间，同一个 `.c64` 文件可能包含多个射频参数阶段。例如：

```text
00:00-00:01 gain=36 offset=0
00:01-00:02 gain=44 offset=0
00:02-00:03 gain=50 offset=40000
```

现有 `.json` 录波元数据通常只能描述当前或最终射频状态，不能完整表达整段录波中的参数变化。因此必须依赖独立扫描日志还原时间线。

## 独立扫描日志

默认日志目录建议：

```text
~/sdr_runtime/rf_scan_logs
```

默认日志文件名建议：

```text
rf_scan_<match_slot>_<front_end_id>_<YYYYMMDD_HHMMSS>.jsonl
```

要求：

1. 启动时自动创建目录。
2. 启动 ROS 日志明确打印完整路径。
3. `/sdr/status` 中包含 `rf_auto_scan.log_path`。
4. 每个事件一行 JSON，便于赛后 grep、脚本分析和与 `.c64` 时间对齐。
5. 日志不能依赖普通 stdout 截屏，必须落盘。

建议事件类型：

```text
startup
trigger
candidate_start
candidate_result
lock
no_lock
cancel
target_change_reset
team_change_reset
```

每条候选结果至少包含：

```text
timestamp
monotonic_sec
event
own_team
rx_team
target
stage
gain
offset_hz
rf_bw_hz
rx_lo_hz
adc_rms
rf_state
ac_delta
sof_delta
crc8_delta
crc16_delta
crc16_fail_delta
score
result
log_path
```

## ROS 状态要求

`/sdr/status` 中增加 `rf_auto_scan` 字段，建议包含：

```json
{
  "enabled": true,
  "active": false,
  "locked": true,
  "log_path": "/home/sen/sdr_runtime/rf_scan_logs/rf_scan_bo3_match_front_end_A_20260523_120000.jsonl",
  "trigger_reason": "crc16_stale",
  "locked_gain": 44,
  "locked_offset_hz": 0,
  "last_result": "LOCK",
  "last_crc16_growth_age_sec": 0.4
}
```

## 参数要求

competition launch 增加以下参数：

```text
enable_rf_auto_scan
rf_scan_log_dir
rf_scan_dwell_sec
rf_scan_crc_stale_sec
rf_scan_tick_sec
rf_scan_gains
rf_scan_offsets_hz
```

建议默认值：

```text
enable_rf_auto_scan=true
rf_scan_log_dir=~/sdr_runtime/rf_scan_logs
rf_scan_dwell_sec=1.0
rf_scan_crc_stale_sec=3.0
rf_scan_tick_sec=0.2
rf_scan_gains=22,30,36,40,44,50,56
rf_scan_offsets_hz=-80000,-40000,0,40000,80000
```

`start_competition_bo3.sh` 建议支持环境变量覆盖：

```text
SDR_RF_AUTO_SCAN
SDR_RF_SCAN_LOG_DIR
SDR_RF_SCAN_DWELL_SEC
SDR_RF_SCAN_CRC_STALE_SEC
SDR_RF_SCAN_GAINS
SDR_RF_SCAN_OFFSETS_HZ
```

## 成功标准

1. 比赛模式下，`RF_LOW` 或 `CRC16` 超过 3 秒未增长时能自动启动扫描。
2. 扫描过程中 `/sdr/status` 能看到扫描状态和日志路径。
3. 独立扫描日志自动创建，且记录每个候选的 start/result/lock。
4. 找到 `CRC16 delta > 0` 后立即锁定参数，不继续无意义扫描。
5. 锁定后 wrapper 能继续发布 `/sdr/jam_code`。
6. 雷达主工程日志出现 `Received JamCode`。
7. IQ 录波仍然存在，且扫描日志能解释录波中每段参数变化。

## 失败标准

1. 扫描没有独立落盘日志。
2. 扫描在 INFO/debug/默认 IQ replay 中误触发。
3. `offset=0` 无法恢复，导致扫过非零频偏后无法回到中心频点。
4. 扫描清空或破坏 `CompetitionController` 中已发布 key 的重复发布逻辑。
5. 队伍或目标等级切换后继续沿用旧锁定参数。
6. 发现 CRC16 后仍持续全扫描，浪费比赛时间。
