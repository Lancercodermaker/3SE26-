# 可复制到新对话的提示词

你是 Codex，请继续 SDR Receiver Python wrapper 的弱 INFO 接收调试。请使用 `embedded-dev-workflow`，当前阶段为 Stage 4 Review And Debug；如果需要继续改代码，则进入局部 Full Implementation。

当前目标：

在不修改原 v67 Python 接收脚本源码的前提下，继续提高官方低功率 INFO 的软件接收能力。

工作区：

- Windows 仓库：`E:\sdr`
- wrapper 包：`E:\sdr\iq_recevier\sdr_receiver_py_wrapper`
- ROS2 message 包：`E:\sdr\iq_recevier\sdr_receiver`
- Ubuntu 工作区：`~/radar_ws`
- Ubuntu venv：`~/sdr_runtime/venv`
- 当前安装包：`E:\sdr\iq_recevier\sdr_receiver_py_wrapper\dist\sdr_receiver_py_wrapper-0.1.0.tar.gz`

必须遵守：

1. 原 v67 Python 接收脚本零改动。
2. 只能通过 import、monkey patch、wrapper、旁路工具和部署脚本修改行为。
3. Pluto/AD936x 接收端硬件增益最高是 `73 dB`，不要再使用 80 作为有效 RX gain。
4. INFO 官方低功率下非常弱，当前目标是从软件侧尽量榨出弱信号。

当前已实现：

- `original_receiver_adapter.py`
- `patches.py`
- `competition_controller.py`
- `receiver_node.py`
- `adaptive_profile_sweep.py`
- `weak_info_probe.py`
- `rf_power_scan.py`
- `rf_iq_diff_capture.py`

最新关键改动：

- `weak_info_probe` 可以直接抓 IQ，输出 soft AC 相关、hard AC 最小错误数、带内能量评分。
- `adaptive_profile_sweep` 支持普通 INFO 的 `LO offset + 数字回正 + filter profile`。
- `patches.py` 支持 `_notch` filter，在原脚本 `filter_iq()` 前做自适应窄带 notch。
- `loose10/wide_loose10/tight_loose10` 及其 `_notch` 版本会显式放宽普通 INFO 的 AC/header 搜索门限，但仍由 header/CRC8/CRC16 把关。

最新实测：

`adaptive_profile_sweep`：

```text
BEST,INFO,gain=73,rf_bw_khz=220,offset_khz=0,class=NO_LOCK,score=-3.92
OUT_DIR,/tmp/adaptive_profile_sweep/20260514_200553
```

`weak_info_probe`：

```text
BEST,gain=70,rf_bw_khz=220,offset_khz=150,filter=loose3,notch=off,score=126.513,soft_sigma=5.77,hard_min_errors=10
OUT_DIR,/tmp/weak_info_probe/20260514_194519
```

```text
BEST,gain=73,rf_bw_khz=420,offset_khz=0,filter=wide_loose3,notch=on,score=129.444,soft_sigma=6.53,hard_min_errors=11
OUT_DIR,/tmp/weak_info_probe/20260514_194818
```

判断：

- IQ 中存在类似 INFO AC 的 soft 相关结构。
- 原硬判 AC 门限吃不进去，协议层仍 `NO_LOCK`。
- 下一步先验证 `loose10/wide_loose10_notch` 是否能让协议层出现 `AC_RAW/AC/SOF`。

请先阅读：

- `docs/HANDOFF_WEAK_INFO_RX_20260514.md`
- `docs/WEAK_INFO_PROBE.md`
- `docs/ADAPTIVE_PROFILE_SWEEP.md`
- `sdr_receiver_py_wrapper/weak_info_probe.py`
- `sdr_receiver_py_wrapper/adaptive_profile_sweep.py`
- `sdr_receiver_py_wrapper/patches.py`
- `sdr_receiver_py_wrapper/original_receiver_adapter.py`

建议用户先在 Ubuntu 跑：

```bash
ros2 run sdr_receiver_py_wrapper adaptive_profile_sweep -- \
  --team RED \
  --profiles INFO \
  --gains 70,73 \
  --rf-bws 220000,420000 \
  --offsets-hz 0,150000 \
  --info-filters loose10,wide_loose10_notch \
  --dwell-sec 8.0
```

根据结果继续：

- 如果 `AC_RAW > 0`：继续围绕该候选做长 dwell，并观察 `HDR_DROP/SOF/CRC8/CRC16`。
- 如果 `AC_RAW=0`：实现 soft AC 直接入池，不再依赖硬 AC 门限。
- 如果出现 `SOF/CRC8` 但无 `CRC16`：实现多帧软合并或 CRC 驱动候选恢复。

输出要求：

- 先说明判断，不要盲目大改。
- 如果改代码，保持原 v67 源码零改动。
- 修改后运行 `py_compile` 和相关 smoke test。
- 重新生成 `dist/sdr_receiver_py_wrapper-0.1.0.tar.gz`。
