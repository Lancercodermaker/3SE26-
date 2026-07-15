# SDR 分级射频硬件台架验收规程

本文定义可审计的台架操作步骤，不代表任何硬件已经执行或通过验收。仓库不保存台架实测结果；每次运行的证据写入操作员指定的新目录。

## 1. 安全边界

- 只在获得发射许可、使用屏蔽箱或合规衰减链路时执行。
- 脚本默认是 `plan` 模式，只打印计划，不启动 ROS、不连接 SDR、不改变硬件。
- `execute` 必须同时提供固定确认文本，并在每次物理换线前从标准输入输入精确阶段确认。确认只证明操作员同意继续，不能证明实际接线正确。
- 脚本不会安装软件、访问网络、调用 `sudo`、配置发射机或自动切换 LNA、SAW、衰减器和 USB 线。
- 输出目录必须是一个尚不存在的绝对路径；脚本拒绝覆盖已有证据。
- `execute` 必须提供当前雷达主进程 PID。脚本只校验同一用户和 Linux 进程启动标识，从不替操作员停止或强杀雷达进程。
- 任意状态字段、录波事件、命令、日志证据缺失或格式错误均按失败处理。

先确认 ROS 2 Humble 和本工作区已经 source，`ros2` 能找到 `sdr_receiver_py_wrapper`、`sdr_receiver/msg/JamCode`，雷达主工程已启动并将当前运行日志写入一个现有普通文件。执行入口为：

```bash
cd ~/radar_ws
source /opt/ros/humble/setup.bash
source install/setup.bash
bash src/sdr_receiver_py_wrapper/scripts/run_rf_bench.sh plan
```

## 2. 固定元数据与六级矩阵

一次完整运行对全部六组使用相同的 RF 线缆长度、供电描述、发射距离和极化方向。这些字段在 `execute` 中必填，并写入 `run_metadata.json` 和每条 `results.jsonl` 记录。若这些条件发生变化，必须停止并用新的输出目录重新运行，不能把两种条件拼为同一次验收。

严格按以下顺序换线：

1. `sdr_direct`：SDR 直连，不接外置增益级。
2. `sdr_saw`：SDR + SAW。
3. `sdr_lna`：SDR + LNA。
4. `sdr_lna_saw`：SDR + LNA + SAW。
5. `full_chain_10db`：完整链路 + 10 dB 衰减。
6. `full_chain_20db`：完整链路 + 20 dB 衰减。

每组都从 0 dB 接收增益启动。每个测量窗口实际执行的核心命令是：

```bash
ros2 launch sdr_receiver_py_wrapper competition_receiver.launch.py \
  initial_rx_gain:=0 \
  record_iq:=true
```

脚本还会为该窗口设置独立 `iq_record_dir`、安全前缀、`rf_clipping_ratio`、本方 fallback ID 和 `key_retry_limit:=1`。每个增益点通过一次新的 launch 固定增益，避免运行中参数改变与测量窗口交叉。

实际 RF 状态枚举在 `/sdr/status` 中是小写 `linear`、`clipped`、`too_strong`、`too_weak`、`disconnected`；它们分别对应规程中的 `RF_LINEAR`、`RF_CLIPPED` 等状态。状态机规则为：

- 只有本窗口最终状态为 `linear` 才能增加接收增益。
- 一旦出现 `clipped`，立即结束该组合的增益扫描，不再运行更高增益。
- `too_strong`、`too_weak` 或 `disconnected` 都不能作为增加增益的依据；脚本关闭接收进程并判定该组合不可上场。
- 每组至少需要一个 `linear` 窗口，且 CRC16 合法命令累计数必须达到非零阈值；否则停止验收。

RF 判断以本窗口完整 `.events.jsonl` 为权威，而不是只看 1 Hz 状态快照。脚本要求每个录波 chunk 恰好对应一个 `rf_state` 事件；任一事件为 `clipped` 时，该窗口按 `clipped` 处理并禁止更高增益。峰值取所有 chunk 的最大值，剪顶比例取最大值；RMS 同时保存最小值、最大值和按 `sample_count` 加权的均方根，不能把最后一条 RMS 冒充整窗结果。最终允许使用的线性增益窗口本身必须达到 CRC16 非零阈值，不能用后续剪顶窗口的 CRC16 凑数。

每个窗口记录峰值、RMS、剪顶比例、CRC16 数量、增益、RF 状态、状态消息数、请求时长、窗口/status/chunk 覆盖时长、status/chunk 的单向 missing 与额外覆盖、两类独立容差、采集占空比、队列丢弃、读错误、重连、录波丢弃和 libiio timeout 日志计数。

## 3. 执行命令

确认的 L1 正样本为 `RX_BLUE_ganrao_1`，SHA-256 必须为 `8cde16d3fe8230334a9efcb36c81ae105b76b4118f4fe3fc63943aeb791be7cc`，预期命令为 `0x0A06`，预期 key 为 `fcYqTC`。回放模式会在启动 ROS 前重新计算哈希，不匹配即停止。

示例：

```bash
bash src/sdr_receiver_py_wrapper/scripts/run_rf_bench.sh execute \
  --acknowledge I_ACKNOWLEDGE_CONTROLLED_RF_BENCH \
  --out-dir "$HOME/rf_acceptance_runs/2026-07-15_run01" \
  --own-team RED \
  --cable-length-m 0.50 \
  --power-supply 'bench_5V_2A_asset_PSU-01' \
  --tx-distance-m 2.00 \
  --polarization H-H \
  --radar-log "$HOME/radar_logs/current.log" \
  --radar-pid "$(cat "$HOME/radar_logs/current.pid")" \
  --closed-loop-source replay \
  --l1-iq "$HOME/sdr_fixtures/RX_BLUE_ganrao_1.c64"
```

每次提示后，完成对应物理换线和复核，再输入脚本显示的精确文本。例如第一组输入：

```text
READY:sdr_direct
```

随后依次确认：

```text
READY:sdr_saw
READY:sdr_lna
READY:sdr_lna_saw
READY:full_chain_10db
READY:full_chain_20db
READY:usb3_short
READY:usb3_competition_3m
READY:confirmed_blue_l1_fcyqtc_transmitter
READY:closed_loop
READY:radar_stopped_log_flushed
```

`confirmed_blue_l1_fcyqtc_transmitter` 只在 `bench` 闭环源中要求。当前唯一 confirmed 台架源为 BLUE/L1/`fcYqTC`，因此 bench 模式强制 `--own-team RED`；BLUE 本方组合会在启动硬件前拒绝。`replay` 则通过 confirmed L1 文件 SHA-256 自动记录同一来源证据。

标准输入可以来自人工终端，也可以来自已经过双人复核的受控运行器；不匹配、缺行或额外阶段不能绕过确认。

## 4. USB 与写盘长稳

矩阵通过后保持相同主机端口和完整 RF 链路，使用最后一组的最终线性增益：

1. 换为经过验证的短 USB 3 数据线，连续运行 1800 秒。
2. 换为比赛 3 米 USB 数据线，在相同端口重复运行 1800 秒。

两段分别要求：

- 采集占空比 `>= 0.99`；
- `queue_drops == 0`；
- `libiio_timeouts == 0`；
- acquisition/device read errors 均为 0；
- 录波 chunk/event 丢弃均为 0；
- 最终 RF 状态为 `linear`；
- CRC16 合法命令数达到默认非零阈值 1。

任一条件失败时，该 USB 线、主机端口和当前链路组合不可上场。

采集占空比没有现成的 ROS 聚合字段，且 ROS 状态接收时间不能代表 SDR 采集时间。脚本严格解析本窗口唯一 `.chunks.jsonl`：`chunk_id`、样本索引、字节范围必须从 0 连续，采样率必须始终为 2 MHz，`sample_count` 必须大于 0，`rx_monotonic_ns` 必须严格递增。这里的 `rx_monotonic_ns` 是本次 SDR read 的完成时刻；每个 chunk 的区间为 `[rx_monotonic_ns - sample_count / sample_rate_hz, rx_monotonic_ns]`。脚本逐条检查 chunk ID、样本/字节索引连续和完成时刻递增，再由下述占空比拒绝整体时间轴的 gap 或 overlap：

```text
expected_samples = first_chunk.sample_count
                 + (last.rx_monotonic_ns - first.rx_monotonic_ns)
                   * sample_rate_hz / 1e9
acquisition_duty = sum(sample_count) / expected_samples
```

占空比必须在 `[0.99, 1.0]`；低于 0.99 或高于 1.0 都拒绝，后者表示时间轴、采样率或记录证据自相矛盾。任何 gap、overlap、duplicate、混合采样率或坏 JSONL 都立即失败。CRC16 数量也没有现成聚合字段，脚本只统计受控录波目录 `.events.jsonl` 中结构完整、`kind=command` 且 `payload.crc16_ok=true` 的事件。

占空比只证明已有 chunk 的内部时间轴连续，不能证明录满了请求时长。每次状态采集因此先写入权威 `window_start_monotonic_ns`、`window_end_monotonic_ns` 和 `requested_duration_sec`。chunk 覆盖区间使用上述 read-completion 语义：

```text
first_start = first.rx_monotonic_ns - first.sample_count / sample_rate_hz
last_end = last.rx_monotonic_ns
chunk_coverage = intersection([first_start, last_end], [window_start, window_end])
chunk_head_missing = max(0, first_start - window_start)
chunk_tail_missing = max(0, window_end - last_end)
```

提前开始或延后结束只计入 `chunk_early_extra_sec` / `chunk_late_extra_sec`，不作为失败；例如首尾各多录 0.9 秒但完整覆盖窗口应通过。chunk 容差独立计算为 `min(0.1 秒, 最大 buffer 周期 × 0.5 + 0.002 秒调度余量, 最大 buffer 周期 × 0.9)`，其中 buffer 周期来自 `sample_count / sample_rate_hz`。该容差始终小于一个完整最大 chunk，不能掩盖整块尾部静默；例如提前 0.9 秒但结尾缺 0.9 秒必须失败。

status 使用相同的窗口求交和单向 `head_missing` / `tail_missing` 语义，提前或延后的额外快照不受罚，但容差与 chunk 分开：当前明确状态周期为 1 秒，加 0.25 秒有界调度余量，因此 `status_tolerance_sec=1.25`。最后一条 status 距窗口结束 0.9 秒可以通过，30 秒窗口不会被不合理地压缩到 0.3 秒 status 容差。`metrics.json` 保存两类 tolerance、missing、extra 和求交后的 coverage。窗口单调时钟边界本身必须覆盖完整请求时长；status 与 chunk 分别必须覆盖“请求时长减各自容差”。因而 1800 秒 status 时间跨度不能掩盖只有 2 秒的 IQ chunk，尾部静默同样会失败；status 接收时间不能替代 SDR chunk 的权威覆盖证据。

当前接收端没有名为 `libiio_timeouts` 的独立诊断计数。脚本只在本窗口 `ros2 launch` 的完整 stdout/stderr 中计数同时包含 `libiio`/`iio` 与 `timeout`/`timed out` 的行，并额外强制 acquisition/device read errors 为 0。这是现有接口的诊断边界，不应把“日志计数为 0”解释为驱动内部绝对没有发生过未上报的 timeout。若比赛验收需要驱动级证明，应先在接收端增加结构化 `libiio_timeouts` 计数，再重新执行本规程。

任一测量时长或雷达停止超时偏离默认值，都必须配合 `--allow-short-duration` 用于脚本和台架流程测试。此时最终摘要固定为 `NOT_ELIGIBLE_SHORT_DURATION`，不得作为 30 分钟硬件验收证据。正式验收保留 USB 默认 1800 秒。

每个录波窗口的 `iq_record_max_bytes` 不是固定 16 GiB，而按 `duration × 2,000,000 × 8 × 1.10 + 64 MiB` 计算。执行前还按六组最坏增益窗口数、两条 USB 长稳、bench 闭环（如有）汇总全部窗口，并额外增加 1 GiB 运行余量，使用输出目录所在文件系统的 `statvfs` 可用空间做 `disk_preflight.json` 检查；每个窗口启动前再次检查。空间不足时不得启动 ROS。正式 1800 秒窗口因此不会被 16 GiB 上限提前截断。

## 5. ROS 闭环

闭环阶段要求雷达主工程在本次阶段开始前已运行，`--radar-pid` 是该进程的 Linux PID，且 `--radar-log` 指向其当前追加写入的日志文件。脚本在开始时锁定 PID 的 `/proc/<pid>/stat` 启动标识，防止 PID 复用。闭环一开始，脚本就以只读方式持有日志文件描述符，并要求 `/proc/<pid>/fd` 中确有相同 `device+inode` 的打开文件；随后记录起始大小和起始前缀 SHA-256。脚本只从这个持续持有的 inode 读取闭环后新增字节，不会按路径重新打开日志，因此旧日志、路径替换或轮转文件不能充当本次证据。

`replay` 使用已确认 L1 IQ；`bench` 使用当前完整链路和最终线性增益。两种模式都监视 `/sdr/jam_code`，要求本次窗口恰好出现一次满足以下全部条件的消息：

- `valid=true`；
- `command_id=2566`（`0x0A06`）；
- `level=1`、`team=BLUE`、`target=L1`；
- `ascii_code=fcYqTC` 且六个 key 字节完全一致。

JamCode 收集结束后，操作员应让雷达主工程正常退出，使 C++ ofstream 析构并 flush，然后输入 `READY:radar_stopped_log_flushed`。该输入仅是“操作员声明已清洁停止并完成 flush”，不是机器对正常退出原因的证明。脚本不会发送 TERM/KILL；机器只证明同一 PID 在有界时间内消失、持续持有的 inode 大小连续三次稳定、路径仍指向同一 `device+inode`、文件未缩短且起始前缀 SHA-256 未变化。增量从持有的文件描述符读取，并在读取结束再次复核 inode、大小和前缀；任何 PID 复用、超时、rename、rotation、replacement、truncate 或 prefix rewrite 都失败。这样既不会读取替换路径中的伪证据，也不会把用户态缓冲尚未 flush 的状态误判为闭环失败或成功。

雷达新增日志必须按顺序包含：

1. `Received JamCode ... command_id: 0x0A06`（RCLCPP 十六进制）或 `command_id: 0x2566`（当前 ofstream 将十进制 2566 加 `0x` 前缀的既有格式）；
2. `ASCII Key: [fcYqTC]`；
3. `Stored password:`；
4. `key phase 2 start`；
5. `key has send`。

这证明本次 ROS 消息到达 `RefereeControl::wirelessKeyCallback()`、密钥被保存并进入 phase 2 发送路径。缺少真实裁判上下文或串口条件时，phase 2 可能不会出现，此时必须判失败，不能用等待或人工填写代替日志证据。

## 6. 证据结构与判读

每次新输出目录包含：

- `run_metadata.json`：固定台架条件、阈值、持续时间和是否具备正式验收资格；
- `disk_preflight.json`：同文件系统可用字节、最坏计划所需字节和预检结果；
- `audit.jsonl`：操作员阶段确认、窗口开始/结束和停止原因；
- `results.jsonl`：每个增益和长稳窗口的稳定、可解析指标，以及六组显式 `combination_summary`（最终线性增益、对应峰值/RMS/剪顶比例和 CRC16）；
- `matrix_*`、`stability_*` 子目录：launch 日志、归一化状态、IQ/事件和窗口指标；
- `closed_loop/`：JamCode、receiver 日志、`radar_log_identity.json`（PID/start ticks、device/inode、起始大小和前缀 SHA-256）、从持续持有 FD 读取的本阶段雷达日志增量，以及闭环结果；
- `acceptance_summary.json`：流程汇总。

脚本成功结束仅表示所有机器可检查的规程条件满足。它明确写入 `hardware_acceptance_claimed_by_script=false`，最终硬件放行仍需负责人核对接线照片、资产编号、发射授权、原始证据和本规程未能直接观测的驱动边界。不得手工修改 JSON/JSONL 后声称通过；需要更正元数据时应使用新的输出目录重跑。
