# 自适应 Profile Sweep 调试说明

`adaptive_profile_sweep` 是一个自动化调试工具，用来减少人工反复调 `gain`、`rf_bw` 和 `freq_offset` 的工作量。它不修改原 v67 Python 源码，也不改变现有 competition 主线；工具通过 wrapper adapter 临时设置接收 profile，然后用协议层统计结果打分。

## 设计目标

- 不依赖某个固定环境杂散，例如 `+128 kHz`。
- 不把单独 INFO 参数强行套到 `INFO-L2` 或 `INFO-L3`。
- 每个目标 profile 独立扫描、独立评分。
- 用 `AC/SOF/CRC8/CRC16/raw frame` 这类协议层指标作为主要判断依据，而不是只看频谱峰值。
- 输出 CSV、JSON 和一个最佳 profile YAML，方便后续人工复盘或接入 competition 低频 rescue scan。

## 推荐用法

只扫 RED 单独 INFO：

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

扫 RED 的 `INFO-L2` 和 `INFO-L3` rescue：

```bash
ros2 run sdr_receiver_py_wrapper adaptive_profile_sweep -- \
  --team RED \
  --profiles INFO_L2,INFO_L3 \
  --gains 40,50,60,70,73 \
  --rf-bws 300000,420000,540000,660000,760000 \
  --offsets-hz 0,-80000,80000,-150000,150000,-250000,250000 \
  --dwell-sec 2.0
```

快速冒烟，只跑前 5 个候选：

```bash
ros2 run sdr_receiver_py_wrapper adaptive_profile_sweep -- \
  --team RED \
  --profiles INFO \
  --limit 5
```

## 输出解释

终端会持续输出 CSV 行，字段包括：

- `profile`：`INFO`、`INFO_L2` 或 `INFO_L3`
- `gain`：接收端手动增益
- `rf_bw_khz`：SDR 接收 RF bandwidth
- `offset_khz`：LO/profile 频偏候选
- `class`：锁定分类
- `score`：综合评分
- `ac_raw/ac/sof/crc8/crc16`：协议层统计增量
- `adc_rms`：当前 ADC RMS
- `rf_state`：原脚本 RF 诊断状态
- `filter`：普通 INFO 为 `normal/loose3/wide_loose3/tight_loose3` 等；`INFO-L2/INFO-L3` 为对应 rescue filter

`class` 的优先级大致为：

```text
CRC16_LOCK > CRC8_STABLE > CRC16_WEAK > SOF_ONLY > AC_ONLY > NO_LOCK
```

工具结束后会输出：

```text
BEST,...
OUT_DIR,/tmp/adaptive_profile_sweep/...
```

输出目录里包含：

- `adaptive_profile_sweep.csv`
- `adaptive_profile_sweep.json`
- `best_profile.yaml`

## 注意事项

1. 这个工具会独占 SDR 接收端，运行时不要同时启动 `debug_receiver.launch.py` 或 `competition_receiver.launch.py`。
2. 单独 INFO sweep 主要用于诊断场地链路裕量，不建议直接作为比赛破 `INFO-L2/INFO-L3` 的唯一依据。
3. 真正比赛需要按当前目标 profile 独立选参：`INFO`、`INFO-L2`、`INFO-L3` 应分开评分。
4. `rf_bw` 降低可能提升弱信号抗噪能力，但过窄会切掉有效频偏，所以必须让协议层统计来决定优劣。
5. 如果发射功率很低，建议先用较短候选列表和较长 `--dwell-sec` 验证是否能稳定出现 `SOF/CRC8/CRC16`。
6. Pluto/AD936x 接收端硬件增益上限是 `73 dB`。本工具默认 `--gain-max-override 73`，不会尝试把原脚本运行时上限提高到硬件不可用范围。如果误传 `76/80` 等候选，工具会裁剪为 `73` 并去重，避免误以为正在测试更高硬件增益。
7. 普通 INFO offset sweep 使用“LO 偏移 + 数字回正 + INFO filter”的临时 profile，不会永久改写原脚本频点。可用 `--info-filters normal,loose3,wide_loose3,tight_loose3` 扩展普通 INFO 滤波候选。
8. 如果 `weak_info_probe` 的最佳候选为 `notch=on`，可在本工具中使用 `_notch` 后缀，例如 `wide_loose3_notch`。wrapper 会在原脚本 `filter_iq()` 前做自适应窄带 notch。
9. 如果 `weak_info_probe` 显示 `hard_min_errors` 约为 10-12，可尝试 `loose10/wide_loose10/tight_loose10` 及其 `_notch` 版本。这些候选只在显式指定时放宽普通 INFO 的 AC/header 搜索门限，仍由 header、CRC8、CRC16 继续把关。

## 后续接入 competition 的建议

第一阶段先只用本工具离线/调试自动扫参。确认评分有效后，再把同一套 scorer 接入 competition：

- CRC16 稳定时不扫。
- 只有 AC/SOF、没有 CRC 时小范围微调。
- 长时间无 AC 时进入短窗口 rescue scan。
- 每轮 scan 设置严格时间预算。
- 只有新 profile 分数明显更好时才切换，避免频繁抖动。

## weak_soft fallback

If `loose10` / `wide_loose10_notch` still reports `AC_RAW=0`, use the explicit
soft-AC fallback profiles instead of widening the default competition path:

```bash
ros2 run sdr_receiver_py_wrapper adaptive_profile_sweep -- \
  --team RED \
  --profiles INFO \
  --gains 70,73 \
  --rf-bws 220000,420000 \
  --offsets-hz 0,150000 \
  --info-filters weak_soft,wide_weak_soft_notch \
  --dwell-sec 12.0
```

`weak_soft` first lets the original v67 hard AC path run. Only when that path
produces no hard `AC_RAW` / CRC activity does the wrapper use a soft AC peak to
append the following 120 payload bits into the original `BIT_POOLS`. `SOF`,
`CRC8`, `CRC16`, and `validate_and_parse()` are still handled by the original
protocol code. Sweep CSV output includes `soft_ac`, `soft_sof`, `soft_crc8`,
`soft_crc16`, and `soft_sigma` so the fallback can be separated from hard-path
hits.
