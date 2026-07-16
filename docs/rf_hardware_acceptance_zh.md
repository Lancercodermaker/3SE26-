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

占空比必须在 `[0.99, 1.0]`；低于 0.99 或高于 1.0 都拒绝，后者表示时间轴、采样率或记录证据自相矛盾。任何 gap、overlap、duplicate、混合采样率或坏 JSONL 都立即失败。CRC16 数量也没有现成聚合字段。脚本只统计受控录波目录 `.events.jsonl` 中结构完整、位于本窗口和对应 chunk 范围内、`role=primary`、CRC8/CRC16 均为精确布尔 `true`，并且具有同 ID 镜像且明确 `accepted=true` 的生产 validation 命令事件；拒绝或缺少 validation 的命令不计数。

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

当前接收端没有名为 `libiio_timeouts` 的独立诊断计数。脚本在本窗口 `ros2 launch` 的完整 stdout/stderr 中使用同一个分类器（窗口分析与最终发布重建共用）识别明确失败语义，包括 `LIBUSB_ERROR_TIMEOUT`、`ETIMEDOUT`、`errno/error 110`、`-110`、`Connection timed out`、`buffer read timed out`，以及具有 `libiio`/`iio`/`libusb` 上下文的通用 `timed out`。`timeouts=0`、`no ... timeout` 和 timeout threshold/limit/budget/configuration 等说明不计为故障；否定只消除其对应子句，同一行另一子句中的真实 timeout 仍会失败。脚本还强制 acquisition/device read errors 为 0。这是现有接口的诊断边界，不应把“日志计数为 0”解释为驱动内部绝对没有发生过未上报的 timeout。若比赛验收需要驱动级证明，应先在接收端增加结构化 `libiio_timeouts` 计数，再重新执行本规程。

任一测量时长或雷达停止超时偏离默认值，都必须配合 `--allow-short-duration` 用于脚本和台架流程测试。此时最终摘要固定为 `NOT_ELIGIBLE_SHORT_DURATION`，不得作为 30 分钟硬件验收证据。正式验收保留 USB 默认 1800 秒。

每个录波窗口的 `iq_record_max_bytes` 不是固定 16 GiB，而按 `duration × 2,000,000 × 8 × 1.10 + 64 MiB` 计算。执行前还按六组最坏增益窗口数、两条 USB 长稳、bench 闭环（如有）汇总全部窗口，并额外增加 1 GiB 运行余量，使用输出目录所在文件系统的 `statvfs` 可用空间做 `disk_preflight.json` 检查；每个窗口启动前再次检查。空间不足时不得启动 ROS。正式 1800 秒窗口因此不会被 16 GiB 上限提前截断。execute 还在任何 ROS/RF 动作前按 `gain_windows = ceil(max_gain/step) + 1`、`worst_windows = 6 × gain_windows + 2` 计算最坏测量窗口数；末步不能整除时，增益扫描会把越界值钳制到 `max_gain`，因此必须把这个末端窗口计入预算。例如 `step=6,max_gain=70` 时每组 13 窗、总计 80 窗。实现使用“整数商 + 非零余数”的等价公式，不依赖浮点舍入。随后脚本保守预算全部证据文件、目录和安全余量，核对当前 `RLIMIT_NOFILE`，并把同 UID 已占用量加到 inotify watches/instances 需求，同时预算 queued-events；任一限制不足即写入失败的 `resource_preflight.json` 并终止。plan 只报告窗口公式和预检边界，不探测或改变系统资源。分析阶段从持续持有的 FD 对 chunks/events/summary 在语义解析前后各做一次 1 MiB 分块 SHA-256，两个 digest 必须一致才可形成 manifest；IQ 只做一次 O(1) 内存的全量分块哈希，随后由最终 publication 的当前 held-FD 哈希再次绑定。2 MHz complex64 的 1800 秒原始 IQ 约 28.8 GB，操作员应把这些哈希 I/O 计入每窗耗时，脚本不会为提速而跳过。

## 5. ROS 闭环

闭环阶段要求雷达主工程在本次阶段开始前已运行，`--radar-pid` 是该进程的 Linux PID，且 `--radar-log` 指向其当前追加写入的、新建且有界的日志文件。脚本在 execute 参数检查时即要求该文件不超过 64 MiB；锁定 held FD/PID 身份时再次检查，JamCode collector 完成 prelaunch-ready 后、记录 measurement evidence start 前第三次要求当前完整前缀不超过 64 MiB，超限时不会继续做无界全量哈希。脚本锁定 PID 的 `/proc/<pid>/stat` 启动标识，防止 PID 复用，并立即以只读方式持续持有日志文件描述符，要求 `/proc/<pid>/fd` 中确有相同 `device+inode` 的打开文件。随后从同一 held FD 记录本次测量专用的 `radar_evidence_start_size`、前缀 SHA-256、`device+inode` 和单调时钟起点，然后才启动 receiver。最终雷达顺序证据只读取这个新偏移后的字节；偏移前的历史 callback、存储和 phase 2 文本不参与本次闭环判断。初始 held-FD 身份与前缀检查仍贯穿整个阶段，路径替换、轮转、截断或前缀改写都会失败。

`replay` 使用已确认 L1 IQ；`bench` 使用当前完整链路和最终线性增益。两种模式都监视 `/sdr/jam_code`。collector 在启动被测 receiver 前连续确认该话题没有发布者；随后只接受预期节点名（replay 为 `sdr_receiver_py_wrapper_iq_jam_code`，bench 为 `sdr_receiver_py_wrapper_competition`）的唯一 publisher endpoint，并记录其 24-byte GID。绑定后建立 wall/monotonic 双时钟测量纪元，拒绝 DDS source/received timestamp 早于该纪元的消息，并在每次回调和整个采集期持续要求 ROS graph 中只有同一节点、namespace 和 GID。ROS 2 Humble 的 Python subscription 回调元数据不提供逐消息 publisher GID，因此证据明确标记为 `exclusive_expected_node_gid`：它证明测量期 graph 独占绑定，而不是声称 DDS 给出了逐消息身份。启动前遗留 publisher、额外 publisher、GID/节点变化或 graph 消失均失败。该测量纪元内要求恰好出现一次满足以下全部条件的消息：

- `valid=true`；
- `command_id=2566`（`0x0A06`）；
- `level=1`、`team=BLUE`、`target=JAM_L1_KEY`；
- `ascii_code=fcYqTC` 且六个 key 字节完全一致。

此外，`replay` 必须为 `radio_mode=debug`。若回放期间没有权威上下文，则发布值为 `radar_info_raw=0`、`key_mutable=false`；若并行雷达已向 `/judge/radar_context` 发布被仲裁器接受的上下文，则允许非零 raw，但 level 位必须为 L1，且 `key_mutable` 必须与 raw 的 `0x20` 位一致。`bench` 必须为 `radio_mode=competition`，并要求 `radar_info_raw` 的 level/mutable 位与 L1 可变上下文一致、`key_mutable=true`。两种来源的 `rf_state` 只接受 bundled adapter 的 `INIT`、`SATURATED`、`RF_LOW`、`CRC_LOCKED`、`DSP_MARGINAL`、`SEARCHING`，而不是固定伪造为 `linear` 或接受测试专用状态。

JamCode 收集结束后，操作员应让雷达主工程正常退出，使 C++ ofstream 析构并 flush，然后输入 `READY:radar_stopped_log_flushed`。该输入仅是“操作员声明已清洁停止并完成 flush”，不是机器对正常退出原因的证明。脚本不会发送 TERM/KILL；机器只证明同一 PID 在有界时间内消失、持续持有的 inode 大小连续三次稳定、路径仍指向同一 `device+inode`、文件未缩短且起始前缀 SHA-256 未变化。增量从持有的文件描述符读取，并在读取结束再次复核 inode、大小和前缀；任何 PID 复用、超时、rename、rotation、replacement、truncate 或 prefix rewrite 都失败。这样既不会读取替换路径中的伪证据，也不会把用户态缓冲尚未 flush 的状态误判为闭环失败或成功。

雷达新增日志必须按顺序包含：

1. `Received JamCode ... command_id: 0x0A06`（RCLCPP 十六进制）或 `command_id: 0x2566`（当前 ofstream 将十进制 2566 加 `0x` 前缀的既有格式）；
2. `ASCII Key: [fcYqTC]`；
3. `Stored password:`；
4. `key phase 2 start`；
5. `key has send`。

分析器为五类文本分别记录第一次出现的字节偏移，并按上述名称顺序要求偏移严格递增；文件扫描顺序本身不能替代语义顺序，跨行逆序同样失败。

这证明本次 ROS 消息到达 `RefereeControl::wirelessKeyCallback()`、密钥被保存并进入 phase 2 发送路径。缺少真实裁判上下文或串口条件时，phase 2 可能不会出现，此时必须判失败，不能用等待或人工填写代替日志证据。

### 5.1 受控证据与完成边界

- 回放源在输出目录创建后先复制到权限为 `0400` 的私有快照，校验固定 SHA-256 与字节数后，以持续持有且已取消路径名的只读 FD 启动回放；启动前和停止后均重新校验该 FD。原始 `--l1-iq` 路径之后发生的替换或修改不会改变本次回放输入。
- 每个 receiver launch 和 JamCode collector 都分别位于自己的独立 session/process group。新 leader 在创建任何 pipeline、ROS 资源或子进程前先自停为 `SIGSTOP`；父脚本已进入 spawn critical 并保存 provisional PID，随后验证同一 UID 的 PID/start ticks/PGID/SID 和停止态，登记完整身份后才 `SIGCONT`。critical 内到达的 INT/TERM/HUP 只记录 pending，登记完成后按原信号退出并清空已验证组；EXIT 路径也能按 provisional 身份回收。即使 leader 先退出，脚本仍按同一 UID、SID、PGID 识别并停止剩余进程；JamCode 正常等待有 `duration + 5 秒` 的有限 deadline，超时执行 TERM/CONT、必要时 KILL、确认组空并 reap。正常结束以及 EXIT/INT/TERM/HUP 清理都必须确认对应组为空。无法验证身份、出现跨组成员或有进程无法在有界时间退出时，本次窗口失败。
- 每个 `matrix_*` / `stability_*` 窗口目录必须精确包含 `launch.log`、`status.jsonl`、`metrics.json` 和 `iq/` 下以完整 stage 名为前缀的 `.c64`、`.chunks.jsonl`、`.events.jsonl`、`.summary.json` 四件套；这些文件都必须是 `nlink=1` 的受控普通文件。四个 recorder role 的相对路径、后缀和 stem 必须一一对应，四个 `device+inode` 必须互不相同。summary 的文件名、chunk/event/sample/byte 数、零丢弃、关闭时间和停止原因必须与原始文件逐项一致；缺少 finalizer summary 的窗口不是完整证据。
- 每个 competition launch 显式传入 `adc_code_scale=2048.0`。分析器要求所有 chunk metadata、RF event 和 recorder summary 的 scale 都精确等于 2048.0，不能仅以“彼此一致”替代规格值；同时严格校验生产 summary 的 `radio` mapping schema 和精确 JSON 类型，要求顶层 `sample_rate` 等于本次采样率，`radio.rx_gain` / `radio.rx_lo_hz` / `radio.rf_bandwidth_hz` 分别与顶层副本相等，并与最后录波 chunk 的实际 LO、带宽、增益一致，其中 gain 必须等于本窗口请求值。最终线性增益因此来自 receiver 实际应用的运行时快照、summary 与 chunk 元数据的交叉证明，而不是只抄写 CLI 参数。
- recorder summary 中的 `decoder_primary`/`decoder_shadow` 是 decoder 身份的权威来源，`role` 是另一独立维度。summary 还必须明确来自 `common_competition` runtime 和 `competition` run mode；执行环境不得用 `SDR_RECEIVER_ORIGINAL_SCRIPT` 替换 bundled adapter。分析器逐类识别生产端当前会写出的 `rf_state`、`command`、`validation`、`decoder_reset`、`decoder_reset_error`、`decoder_error`、`output_error`、`discontinuity` 和 `recording_stopped`，分别执行 exact schema/type、时间戳、chunk/context、decoder/role 和累计重连计数规则；未知 event kind 直接失败，不能因不参与 CRC 统计而被忽略。primary 命令必须绑定 summary 的 `decoder_primary` 并具有同 ID 镜像 validation；shadow 命令必须绑定非空的 `decoder_shadow` 且不得伪造 primary validation。所有 recorder event 都要通过结构、pairing、chunk/context、decoder 和镜像校验；样本范围完全位于权威测量窗口前后的合法 extra coverage 时，事件记录到 `out_of_window_command_event_ids`，既不计入 CRC16 也不导致 evidence-invalid；跨越窗口边界的命令则 fail-closed。CRC16 的窗口资格只由 IQ 样本范围决定：同步 command/validation 事件即使在 `window_end` 后才完成，也不能把窗内 IQ 重分类为 extra；但事件时间戳必须从所属 chunk 的 `rx_monotonic_ns` 起按顺序出现，并在一个 status 周期加调度余量（当前 `event_processing_lag_sec=1.25`）内完成，任意遥远未来时间戳会失败。CRC16 只统计完全位于测量窗口内、CRC8/CRC16 均为精确 `true`、且被同 ID 生产 validation 明确接受的 primary 命令；accepted validation 仍按生产协议验证合法命令、ASCII 与 level，但 BLUE/L1/`fcYqTC` 只在后续闭环阶段单独强制。孤儿、重复、拒绝、跨 chunk、上下文不一致或 decoder/role 不一致事件均不能计入 CRC，其中结构或配对不一致还会使窗口失败。
- `/sdr/status` 的 queue/read/reconnect/recorder drop 计数在每一条快照中都必须是精确非负整数、累计值不得下降，并且任一时点非零即失败。外部 YAML/JSONL/launch/radar 日志均有文件、行、对象深度和键数上限；超限、别名、重复键或多文档输入按失败处理。launch 输出由独立有界流式记录器限制，超限会终止其专属进程组，但该日志上限不会错误地施加到大容量 IQ 文件。
- `/sdr/jam_code` 必须恰好一条，记录还必须包含绑定 publisher 的 GID/node/namespace、双时钟测量纪元、实际 callback 单调时间和 DDS source/received timestamp；完整 JamCode schema（包括 header、radio mode、RF state、radar info、key mutable 和 key）与 confirmed L1 期望一致，不能用只含部分字段或纪元前消息通过闭环。replay 严格校验 `debug`，并区分无上下文 `0/false` 与存在上下文时的 L1/raw mutable 位一致性；bench 严格校验 `competition` 及 radar level/mutable 位的一致性。
- 闭环判定在读取语义证据前，以 `O_NOFOLLOW` 持续持有每个输入 FD，要求普通文件、`nlink=1` 和受控大小，并在语义校验结束后再次核对 `device+inode+nlink+bytes+SHA-256`。通过时生成 schema v2 的 `closed_loop/result.json`，其中 `evidence_manifest` 精确绑定 JamCode、collector 的 prelaunch/bound ready、雷达日志 identity/本次 evidence start/delta 和 receiver 日志；bench 还必须绑定 `closed_loop.c64` 及 chunks/events/summary 四件套，replay 则绑定输出目录内的 confirmed L1 source identity。result 写出前仍从同一 held FD 复核所有身份与哈希；之后任何原 inode 改写、路径替换、硬链接或字节变化，都会在完成发布阶段与当前递归证据清单不一致，因而不会生成 `completion.json`。

## 6. 证据结构与判读

每次新输出目录包含：

- `run_metadata.json`：固定台架条件、阈值、持续时间和是否具备正式验收资格；
- `disk_preflight.json`：同文件系统可用字节、最坏计划所需字节和预检结果；
- `resource_preflight.json`：最坏窗口数、证据文件/目录预算、当前与所需 `RLIMIT_NOFILE`、同 UID inotify 占用、watches/instances/queued-events 限制和预检结果；
- `audit.jsonl`：操作员阶段确认、窗口开始/结束和停止原因；
- `results.jsonl`：每个增益和长稳窗口的稳定、可解析指标，以及六组显式 `combination_summary`（最终线性增益、对应峰值/RMS/剪顶比例和 CRC16）；
- `matrix_*`、`stability_*` 子目录：launch 日志、归一化状态、IQ/事件和窗口指标；
- `closed_loop/`：JamCode、receiver 日志、`radar_log_identity.json`（阶段初始 PID/start ticks、device/inode、大小和前缀 SHA-256）、`radar_evidence_start.json`（collector prelaunch-ready 后的本次测量偏移、前缀、identity 与单调时钟起点）、从持续持有 FD 且仅从本次偏移读取的雷达日志增量，以及闭环结果；
- `acceptance_summary.json`：流程汇总。
- `completion.json`：仅在全部窗口、六组组合和闭环均通过，且最后一条 audit 已同步落盘后原子发布。每窗分析从一次性 `O_NOFOLLOW` 打开的持续持有 FD 解析 chunks/events/summary；打开后立即封存为 `0400` 并保存四件套的 `device+inode+size+mtime+ctime` 基线。chunks/events/summary 在解析前先计算 baseline SHA-256，解析后从同一 held FD 重算且必须一致，随后才把语义指标与 digest 放入 manifest；IQ 的一次分析哈希在完成发布时仍由当前 held-FD 全量哈希独立复核。这样即使同一 inode、同一长度内容被原地改写，也不能使解析语义和最终哈希脱钩。分析器还核对 IQ 大小并把四件套的 `device+inode+bytes+SHA-256` manifest 同时写入 `metrics.json` 和 `results.jsonl`。完成发布时要求 `results.jsonl`、`audit.jsonl` 仍是输出目录创建时锁定的 inode，递归拒绝符号链接或特殊文件，把输出目录中全部证据（所有 matrix/stability 的 status、launch、metrics、四件套与 IQ，以及闭环 Jam/GID 纪元、receiver、雷达 identity/delta/result，bench 模式还包括闭环 IQ）封存为只读，并重新记录和比对每窗 recorder manifest。发布器不直接信任先前生成的 `acceptance_summary.json`：它会从当前 held/read-only 内容重新解析每个 window 的 `passed`、六个唯一且 fieldable 的组合、两条稳定性窗口、闭环 result 的 exact schema/source/evidence manifest、以及 audit 的 `closed_loop_complete` 和末尾 `run_complete`，再生成期望摘要并与文件逐字段完全比较。`completion.json` schema v2 的 `artifacts` 为完整相对路径清单，每项含 device、inode、nlink、字节数和 SHA-256，不再只覆盖三个聚合文件。

语义重建通过后，发布器还会执行独立的最终发布屏障：重新枚举并精确比对全部文件和目录路径，以 `O_NOFOLLOW` 打开所有证据并持续持有 FD，要求普通文件、`nlink=1`、`0400`、device/inode 和字节数仍与第一遍 manifest 一致；随后按反向路径顺序从 held FD 计算第二遍全量 SHA-256，并在每个文件哈希前后核对 size、mtime 和 ctime。私有完成标记以 `O_EXCL` 创建并同步落盘，同时记录其完整字节、SHA-256、device/inode、nlink、mode、size、mtime 和 ctime；该 marker FD 在整个发布阶段保持打开。发布器再次以 `O_NOFOLLOW` 从私有路径打开 marker，要求路径 FD 与 held marker 完全一致，然后核对所有证据 held FD、根目录、逐级路径身份和目录成员，按从深到浅顺序同步证据目录，并在 held marker FD 仍打开时原子改名。改名后还会从 `completion.json` 路径重新打开并核对同一 inode、大小、模式和哈希，确认私有路径已消失，再同步根目录；任一步不一致都会删除错误的私有或最终 marker 并失败。第一遍校验结束后发生的原 inode 改写、缺失、新增、路径替换或 marker 路径替换因此不会得到完成标记。台架运行期间仍应保证输出目录仅由本脚本写入；该屏障用于关闭正常发布窗口，不把并发写入受控证据当作受支持用法。

在 execute 的最终发布屏障内，脚本还使用目标 Ubuntu/Linux 的 inotify 接口建立一次发布事务监视。精确路径集合确定后，每个 artifact 的 held inode 都监视内容、属性、写关闭、移动和删除事件，每个证据目录都监视成员创建、删除、移入、移出、属性变化以及目录自身失效；监视一直保持到 final marker 身份/哈希复核和根目录同步结束。发布前与发布后分别清空并检查事件队列；任何 artifact 事件、非预期目录成员事件、队列 overflow、未知或失效 watch 都会删除错误 marker 并失败。根目录只允许且严格核对本次事务自己的 `.completion.private` 创建、同 cookie 的 private→`completion.json` 移动以及最终写 FD 关闭序列。若运行平台不支持所需 inotify 操作，execute 会在发布完成标记前失败；`plan` 不进入该发布路径。事务 watcher 成功关闭后发生的修改属于发布后变化，后续校验会通过 `completion.json` 中的完整 artifact 身份和 SHA-256 显示不一致，因此已发布的证据目录仍不得继续写入。

完成发布还会对每个窗口重新要求完整的 58 字段 production metrics exact schema，不接受只保留 `passed`、RF 状态和若干汇总数的缩减指标。所有布尔、整数、浮点、字符串、列表和映射都按精确 JSON 类型验证，浮点必须 finite；2 MHz 采样率、2048.0 ADC scale、剪顶阈值、请求时长、status/chunk 容差与 coverage、占空比、RF/CRC/event 数、全部错误计数、增益、stage、组合和 recorder manifest 必须分别与当前 `run_metadata.json`、`results.jsonl`、归一化 status、launch 日志、recorder summary 及递归 manifest 一致。任一窗口缺少 `launch.log` / `status.jsonl`、四件套没有使用 stage stem、后缀或 role 对应错误、四个 role 复用同一 inode，或当前大小/SHA-256 与分析时 manifest 不同，都不会发布 `completion.json`。

完成发布器不会把六条孤立的 `combination_summary` 当作矩阵已经执行。它从当前只读 `run_metadata.json`、`results.jsonl`、`audit.jsonl`、每窗 `metrics.json` 和 recorder manifest 重建完整证据图：六个组合的名称、序号和顺序必须与本规程一致；每组必须从 `matrix_0N_<id>_gain_00` 开始，后续增益严格按本次 `step_db` 和 `max_db` 演进；只有前窗 `linear` 才能出现下一窗，`clipped` 必须成为该组终止窗，不能缺级、重复、乱序或在剪顶后继续。每条 exact-schema `combination_summary` 必须绑定最后一个合法线性窗的增益、峰值、RMS、剪顶比例和 CRC16，以及全扫描 CRC16 累计；相关 metrics 和四件套 manifest 必须仍与当前文件完全相同。全部 matrix 窗口加两条 stability 窗口必须与 results、metrics、manifest 一一对应，固定 RF 元数据、请求时长和最终增益必须与 run metadata 一致；audit 中每组的操作员确认、`combination_start`、逐窗 start/complete 和 `combination_complete` 也必须按同一结果顺序出现。仅有两条 stability 窗口、缺少任一 0 dB 起始窗、增益断档、summary 脱离最后线性窗或 audit 顺序不一致时都不会发布完成标记。

只有存在可校验的 `completion.json` 才表示脚本到达完整结束边界。没有该标记的目录一律视为中断或不完整运行，不得在原目录续跑、补写或人工发布标记；应保留现场用于诊断，并使用一个全新的输出目录从头执行。

脚本成功结束仅表示所有机器可检查的规程条件满足。它明确写入 `hardware_acceptance_claimed_by_script=false`，最终硬件放行仍需负责人核对接线照片、资产编号、发射授权、原始证据和本规程未能直接观测的驱动边界。不得手工修改 JSON/JSONL 后声称通过；需要更正元数据时应使用新的输出目录重跑。
