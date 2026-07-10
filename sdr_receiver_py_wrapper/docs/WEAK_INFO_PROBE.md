# weak_info_probe 弱 INFO 旁路探测工具

`weak_info_probe` 用于官方低功率 INFO 场景下的自动化诊断。它不启动原 v67 主循环，也不修改原脚本源码，而是直接抓取 SDR IQ，并在软件侧做：

- 宽搜/窄带候选扫描：`gain + rf_bw + freq_offset`
- LO 偏移后的数字回正
- INFO 非对称滤波候选：`normal/loose3/wide_loose3/tight_loose3`
- 可选自适应窄带杂散 notch
- soft access-code 相关评分
- hard access-code 最小错误数统计
- 带内频谱能量相对噪声评分

这个工具的目标不是直接发布 ROS2 topic，而是在 `adaptive_profile_sweep` 全部 `NO_LOCK`、`AC_RAW=0` 时，给出哪个方向更像有效 INFO 信号。

## 推荐起步命令

```bash
ros2 run sdr_receiver_py_wrapper weak_info_probe -- \
  --team RED \
  --gains 70,73 \
  --rf-bws 220000,300000,420000,540000 \
  --offsets-hz 0,-80000,80000,-150000,150000,-250000,250000 \
  --info-filters normal,loose3 \
  --captures 3
```

如果怀疑有强窄带杂散影响 FM 鉴频，可以加 notch 候选：

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

## 输出字段

终端会输出 CSV：

- `gain`：实际接收端增益，超过 73 dB 的候选会被裁剪。
- `rf_bw_khz`：SDR RF bandwidth。
- `offset_khz`：LO 偏移，同时软件数字回正。
- `filter`：INFO 滤波候选。
- `notch`：是否启用自适应窄带 notch。
- `adc_rms/adc_peak`：ADC 幅度。
- `band_snr_db`：INFO 滤波带内平均能量相对带外噪声的粗略 SNR。
- `peak_offset_khz/peak_snr_db`：INFO 滤波带内最强峰。
- `soft_corr/soft_sigma/soft_margin`：soft AC 相关指标。
- `hard_min_errors`：硬判后 AC 最小汉明错误数，越低越像 INFO。
- `hard_hits_le3`：AC 错误数小于等于 3 的候选数量。
- `score`：综合排序分。

结束时会输出：

```text
BEST,...
OUT_DIR,/tmp/weak_info_probe/...
```

输出目录中包含：

- `weak_info_probe.csv`
- `weak_info_probe.json`
- `best_weak_info_probe.yaml`
- `suggested_adaptive_profile_sweep.sh`

## 如何解读

如果 `adaptive_profile_sweep` 全部 `NO_LOCK`，但 `weak_info_probe` 里某些候选出现：

- `soft_sigma` 明显高于其他候选
- `hard_min_errors` 明显下降
- `band_snr_db` 或 `peak_snr_db` 同时更好

说明这些候选更接近真实 INFO，应优先把对应 `rf_bw/offset/filter` 带回 `adaptive_profile_sweep` 做协议层验证。

如果 `BEST` 里 `notch=on`，可以在协议层 sweep 里使用对应的 `_notch` filter 名称，例如 `wide_loose3_notch`。wrapper 会通过 monkey patch 在原 `filter_iq()` 前加自适应窄带 notch，不会修改原 v67 源码。

如果 `hard_min_errors` 约为 10-12，`suggested_adaptive_profile_sweep.sh` 会把候选映射到 `loose10/wide_loose10/tight_loose10` 族。它们只在显式指定时放宽普通 INFO 的 AC/header 搜索门限，适合弱信号验证；不要把它们作为默认比赛参数，除非后续实测 CRC 表现稳定。

如果所有候选的 `hard_min_errors` 都很高、`soft_sigma` 没有明显尖峰、`band_snr_db` 也没有方向性，说明当前 ADC 里的 INFO 仍然太弱，软件侧很难稳定恢复。

## 后续接入方向

第一阶段只用 `weak_info_probe` 做旁路诊断。若它能稳定找到候选，下一步可以：

1. 把 `BEST` 候选自动转成 `adaptive_profile_sweep` 的小范围参数。
2. 在原 wrapper 中增加低频 `weak_info_acquire` rescue scan。
3. 只在 `AC_RAW=0` 且长时间 `RF_LOW/NO_LOCK` 时启用，避免干扰正常比赛主线。
