# 可复制到下一个对话的提示词

你是 Codex，请继续帮我 debug SDR Receiver Python wrapper 和 INFO 接收问题。请先阅读并遵守当前仓库里的文档和代码，不要修改原 v67 Python 接收脚本源码，只能通过 wrapper、import、monkey patch、外部工具或部署脚本调整。

工作区信息：

- Windows 仓库：`E:\sdr`
- wrapper 包：`E:\sdr\iq_recevier\sdr_receiver_py_wrapper`
- 消息包：`E:\sdr\iq_recevier\sdr_receiver`
- Ubuntu 工作区：`~/radar_ws`
- Ubuntu wrapper 路径：`~/radar_ws/src/sdr_receiver_py_wrapper`
- venv：`~/sdr_runtime/venv`
- 原 v67 脚本已随 wrapper 放在 `sdr_receiver_py_wrapper/vendor/`
- 当前安装包：`E:\sdr\iq_recevier\sdr_receiver_py_wrapper\dist\sdr_receiver_py_wrapper-0.1.0.tar.gz`

必须保持的架构约束：

1. 原 v67 Python 接收脚本零改动。
2. 使用 `importlib.util.spec_from_file_location()` 导入原脚本。
3. wrapper 通过 adapter + monkey patch 集成。
4. Debug 模式尽量保持原脚本 dashboard/键盘行为。
5. Competition 模式由 ROS2 裁判上下文驱动。
6. ROS2 输出至少保留 `/sdr/jam_code`、`/sdr/radar_wireless/raw_frame`、`/sdr/status`。

已经实现：

- `original_receiver_adapter.py`
- `patches.py`
- `competition_controller.py`
- `receiver_node.py`
- `direct_original_receiver.py`
- `rf_power_scan.py`
- debug/competition launch
- 中文 README 和 Ubuntu 部署文档

Ubuntu 环境注意：

```bash
cd ~/radar_ws
source ~/sdr_runtime/venv/bin/activate
source /opt/ros/humble/setup.bash
source ~/radar_ws/install/setup.bash
export PYTHONPATH=$HOME/sdr_runtime/venv/lib/python3.10/site-packages:$PYTHONPATH
```

之前遇到并解决过：

- `ros2: command not found`：调整 source 顺序。
- `ModuleNotFoundError: No module named 'sdr_receiver'`：缺少 `sdr_receiver` 消息包。
- `ModuleNotFoundError: No module named 'adi'`：安装 `pyadi-iio` 并注入 venv site-packages 到 `PYTHONPATH`。
- `ros2 launch` 下键盘不可用：launch 不转发 stdin，使用 `ros2 run` 或 launch 初始参数。

当前关键问题：

L1/L2/L3 干扰波都能破译，说明 SDR、pyadi、2GFSK、CRC16 和 0x0A06 解析链路基本正常。但 INFO 无法解调：

- RED-INFO 下 `AC=0`
- `SOF=0`
- `CRC8=0`
- `CRC16=0`
- `cmd=0x0000`
- 增益加到 73 后 ADC/RMS 有一点示数，但仍无 AC
- direct runner 直接跑原 v67 脚本也一样失败，说明 wrapper 基本排除

RF 差分扫描结果：

TX off：

```text
RED_INFO  rms_avg=0.003145  adc_peak=0.011435  peak_offset_khz=127.9  snr_like_db=35.73
BLUE_INFO rms_avg=0.003177  adc_peak=0.011741  peak_offset_khz=127.9  snr_like_db=35.41
RED_L1    rms_avg=0.003138  adc_peak=0.010368  peak_offset_khz=-592.1 snr_like_db=33.95
RED_L2    rms_avg=0.003107  adc_peak=0.011880  peak_offset_khz=-188.3 snr_like_db=30.78
RED_L3    rms_avg=0.003110  adc_peak=0.010895  peak_offset_khz=527.9  snr_like_db=34.87
```

TX on 对比 baseline：

```text
RED_INFO  delta_rms_avg=-0.000023  delta_peak_db=0.09   delta_snr_like_db=0.22
BLUE_INFO delta_rms_avg= 0.000019  delta_peak_db=0.48   delta_snr_like_db=0.52
RED_L1    delta_rms_avg=-0.000014  delta_peak_db=0.26   delta_snr_like_db=0.19
RED_L2    delta_rms_avg= 0.000001  delta_peak_db=0.16   delta_snr_like_db=0.18
RED_L3    delta_rms_avg= 0.000003  delta_peak_db=-1.19  delta_snr_like_db=-1.17
```

结论：

- INFO 开关前后 `RED_INFO` 差分几乎为 0。
- 之前看到的 `+127.9 kHz` 强峰在 TX off 时也存在，不是 INFO 信号。
- RX 侧没有看到 INFO 发射端开关带来的有效变化。

请下一步帮我：

1. 基于以上结论继续定位 INFO 接收失败。
2. 优先设计能够确认 INFO 发射端 RF 是否真正进入 RX 的测试。
3. 必要时补充更可靠的 SDR 诊断工具，例如固定频点长时间采样、保存 IQ、对 TX off/on 做频谱差分、或者输出 CSV/PNG。
4. 不要再假设发射端一定没问题，也不要简单重复“检查频点”，要根据已有差分数据推进。
5. 如果需要改 wrapper，请修改 `E:\sdr\iq_recevier\sdr_receiver_py_wrapper` 并重新生成 tar.gz。

当前最值得验证的方向：

- INFO 发射端实际天线口功率是否有输出。
- INFO TX/RX 路径是否和 L1/L2/L3 路径不同。
- 官方 `-60 dBm` INFO 功率在当前距离/天线/前端下是否低于解调门限。
- 当前 v67 脚本是否与之前硬件联调成功脚本完全一致。
- 如果确认 RF 已进入 RX，再查 INFO Access Code、GFSK 参数、air framing 与滤波参数差异。

