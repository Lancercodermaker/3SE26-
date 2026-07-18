# WSL2 本机三节点全自动闭环设计

**状态：** 用户已逐节冻结，等待书面规格复审
**日期：** 2026-07-18
**仓库：** `Lancercodermaker/3SE26-`
**基线提交：** `01b7c4f`

## 1. 目标

在现有 `Ubuntu-22.04` WSL2 中建立可重复、可自动判定的三节点闭环：

```text
RefereeScenarioDriver
  ⇄ Linux PTY / RoboMaster 0xA5 裁判协议
真实 RefereeControl 协议切片
  ⇄ ROS2 /judge/radar_context 与 /sdr/jam_code
解析波接收端
```

第一阶段必须证明官方录波经过真实接收端、ROS2 和真实雷达裁判通信代码后，能形成符合 V1.3.1 的裁判密钥验证闭环。它不要求在 WSL2 中运行 YOLO、工业相机、GPU 推理或完整视觉坐标链路。

官方 RoboMasterEngine、Pluto 实时接收、真实裁判硬件和官方发射端属于更高层验收，不阻塞第一阶段软件结论。

## 2. 架构与节点边界

### 2.1 Windows 宿主

Windows 只负责：

- 启停 WSL 自动化入口；
- 保存大型官方录波；
- 展示和归档报告；
- 运行自制发射端；
- 高级阶段运行 RoboMasterEngine；
- 必要时提供 Pluto 网络或 USB 入口。

Windows 不运行第一阶段的 ROS2 接收端或雷达节点，避免 Windows/WSL DDS 发现和依赖差异进入核心链路。

### 2.2 WSL2 Ubuntu 22.04

现有发行版已核验为 Ubuntu 22.04.5 LTS、WSL2 内核 `6.6.87.2-microsoft-standard-WSL2`，ROS2 Humble、Git 和 Python 3 已存在。WSL2 运行：

- PTY 裁判模拟器 `RefereeScenarioDriver`；
- 真实 `RefereeControl` 协议切片；
- 解析波接收端；
- ROS2 Humble；
- 场景编排、断言和证据生成。

### 2.3 真实雷达协议切片

协议切片必须直接复用生产代码：

- `RefereeControl`；
- `SendReceive`；
- `RefereeProtocol`；
- CRC8/CRC16；
- `RadarContext` publisher；
- `JamCode` subscriber；
- `sendKey()`。

集成入口只运行串口接收、ROS2 spin、密钥发送和 RadarContext 发布。它不运行位置、预警、机器人信息、事件广播、YOLO、工业相机、目标检测或坐标生成。不得复制简化版 `RefereeControl`；测试入口与生产入口必须链接同一份协议源文件。

Docker 不进入运行时三节点链路，只允许用于纯软件构建和单元测试。

## 3. 解耦和修改边界

解析波端只依赖：

- `/judge/radar_context`；
- `/sdr/jam_code`；
- `RadarContext`；
- `JamCode`；
- IQ 文件或 Pluto 输入；
- 比赛阶段、等级、上下文新鲜度和发布门。

解析波端不得解析或生成裁判 A5 帧，不得知道 `0x020E` bit 布局、`0x0301/0x0121`、PTY 或 `RefereeControl` 私有状态，也不得依赖雷达日志文本、WSL 特有路径或某个雷达版本。

雷达生产代码修改预算限定为三项：

1. 恢复并加固裁判入站 CRC8/CRC16 验证；
2. 把密钥验证修正为 V1.3.1 类型 2；
3. 让串口路径可配置，生产默认仍为 `/dev/ttyUSB0`。

集成入口、PTY裁判模拟器和编排器属于独立测试工具，不进入解析波核心。雷达与解析波保持 ROS2 合同解耦；未来雷达升级只要消息语义、topic和 QoS 不变，解析波无需修改。

## 4. 裁判协议修正

### 4.1 入站 CRC

当前 `framePreProcess()` 的 CRC 代码被注释并无条件返回 true。修正后必须：

1. 找到 `0xA5` 后确认至少有五字节帧头；
2. 对完整五字节帧头验证 CRC8；
3. CRC8 通过后才读取小端 `data_length`；
4. 对长度实施协议上限；
5. 收齐 `data_length + 9` 字节后验证全帧 CRC16；
6. 两个 CRC 都通过后才解析 `cmd_id`；
7. 错误帧后从后续字节重新同步，而不是丢弃整批数据。

必须覆盖 CRC8 错误、CRC16 错误、截断、超长、噪声后合法帧和多个连续帧。

### 4.2 密钥验证

当前空密钥 phase 1 和未定义的 `password_cmd=3` 均删除。收到合法 JamCode 后只允许发送一次：

```text
cmd_id       = 0x0301
data_cmd_id  = 0x0121
receiver_id  = 0x8080
user_data[0] = 单调递增 radar_cmd
user_data[1] = 2
user_data[2..7] = 六字节 ASCII 字母或数字密钥
```

十秒内不得重复验证；同一等级、同一密钥不得重复发送。串口写成功不等于裁判接受，只有随后 `0x020E bit 3-4` 等级提升才是成功凭证。

## 5. 双阶段比赛状态机

### 5.1 赛前阶段

默认时间线：

```text
裁判离线
→ 串口上线
→ 赛前准备
→ L3 自检约 5 秒
→ 切回 L1
→ 倒计时
→ 正式比赛开始
```

五秒是现场日志观测默认值，同时测试 3–8 秒参数范围。赛前 L3 自检期间允许 IQ、CRC 候选、频谱和诊断采集，但严格禁止发布 JamCode 和调用 `sendKey()`。

L3→L1 时必须清除解码器状态、旧候选、延迟结果和旧上下文。比赛开始时只能使用裁判最后确认的 L1 上下文。赛前 L3 和赛中 L3 在证据中必须区分。

发布门必须同时满足：

```text
referee_online
&& game_progress == 正式比赛
&& key_mutable
&& context 未过期
&& 解码 profile 与当前等级一致
```

### 5.2 赛中阶段

```text
L1 + K1
→ 解码并发送类型 2 验证
→ 裁判接受并提升 L2、生成 K2
→ 清除 K1 状态
→ 解码并验证 K2
→ 裁判接受并提升 L3、生成 K3
→ 清除 K1/K2 状态
→ 比赛结束
→ 禁止继续发布和发送
```

完整比赛模拟指与 SDR 密钥闭环相关的七分钟生命周期，不模拟视觉、伤害、金币、飞镖等无关机制。提供压缩逻辑赛、七分钟实时赛和多局长稳赛。

协议完整赛可注入确定的 K1/K2/K3；官方录波赛只有 confirmed oracle 可作强断言；自制发射赛单独标记为 synthetic evidence。不得把 candidate 录波中的候选密钥写成真值。

## 6. 模拟器一致性

快速合同模式由 `RadarContractSimulator` 直接读取场景并发布 RadarContext。真实雷达在环模式由 `RefereeScenarioDriver` 读取同一场景、通过 PTY 发送 A5 帧，再由真实 RefereeControl 发布 RadarContext。

两种模式必须产生一致的 ROS2 上下文序列：

```text
场景预期状态
= RadarContractSimulator 发布状态
= 真实 RefereeControl 解析后发布状态
```

PTY裁判模拟器必须：

- 生成正确 CRC 的比赛状态与 `0x020E`；
- 注入 CRC8、CRC16、长度、截断和噪声故障；
- 校验雷达回包的 `0x0301/0x0121`、发送者、接收者、类型和密钥；
- 拒绝类型 3、空密钥、非 ASCII 密钥和十秒内重复验证；
- 接受后提升 `0x020E` 等级；
- 保存全部原始字节、时间戳和判定原因。

## 7. WSL 环境与安全边界

复用现有 `Ubuntu-22.04`，不重装。首次实施顺序：

```text
只读 doctor
→ 导出 WSL 快照并记录 SHA256
→ 创建普通用户 sdrdev
→ 设置默认用户
→ 经批准安装缺失依赖
→ 构建独立工作区
→ 运行基线测试
```

构建和测试全部以 `sdrdev` 运行。只有安装 apt 包、创建用户和系统配置时显式使用 sudo。自动化不得删除 WSL、快照、用户目录、录波、历史证据或 Git 分支，不得自动修改 Windows 网络、防火墙和代理，也不得以 root 运行三节点。

自动化入口分为 `doctor`、`bootstrap-plan`、`bootstrap-apply`、`build`、`run-scenario`、`run-suite`、`collect` 和 `restore-guide`。系统级操作必须先输出 dry-run 并由用户批准。

每个测试使用唯一 ROS_DOMAIN_ID、PTY和证据目录。所有进程有超时、退出码和身份受控的清理。失败保留现场，不自动清理后重试。

WSL至少保留初始现状、依赖完成和第一阶段通过三份快照。当前 localhost 代理警告进入 doctor；离线闭环不因此失败，只有联网安装或访问 Windows 服务时才成为阻塞。

## 8. 测试层级与完成条件

### L0：纯单元测试

验证 CRC、A5 重同步、类型2密钥、十秒限制、赛前发布门、等级切换清理以及延迟/重复/过期结果拒绝。

### L1：快速 ROS2 合同测试

使用 RadarContractSimulator 验证赛前 L3→L1、比赛发布门、上下文超时、裁判离线和比赛结束。

### L2：真实雷达协议切片在环

运行真实 RefereeControl、PTY裁判模拟器和接收端，校验完整 A5 字节流、故障帧、类型2回包、等级提升和模拟/真实上下文序列一致性。

### L3：官方录波完整闭环

- confirmed oracle：强断言；
- candidate：只诊断；
- context-negative：允许物理解码但零发布；
- fault-sample：安全失败并给出原因。

L0–L3 全部通过即为第一阶段软件完成。

### L4：实时硬件扩展

Pluto、自制发射端、集成 SAW+LNA、软件增益和 433 MHz 天线属于扩展层。L4失败不撤销软件结论，但必须单独报告。

## 9. 证据格式

每个场景生成不可变目录：

```text
run.json
doctor.json
scenario.yaml
processes.json
ros_topics.json
serial_rx.bin
serial_tx.bin
radar_context.jsonl
jam_code.jsonl
receiver.log
radar.log
referee_simulator.log
assertions.json
artifact_sha256.json
```

证据绑定代码 commit、场景哈希、录波哈希、WSL/ROS2版本、进程身份、退出码和时间线。通过条件必须来自机器断言，不以日志观感代替。

## 10. 失败处理和额度保全

失败分为代码、环境、设备和证据四类。环境问题先生成 bootstrap plan；设备失败不影响 L0–L3；证据不完整时即使功能看似正常也判失败。

自动化只允许停止自己启动的进程、删除自己创建且身份验证通过的临时PTY并重跑同一场景。其他恢复操作生成精确人工说明。

为降低后续 Agent 消耗：

- 支持单场景重跑和从失败层继续；
- 缓存绑定身份的大文件哈希和 doctor 结果；
- 自动生成一页摘要和失败窗口；
- 每个任务生成短交接摘要；
- 生成独立迁移说明，包含 commit、分支、worktree、决策、已通过层级、下一命令和风险；
- 任何 Agent 不需要阅读完整对话或全部日志才能继续。

## 11. Git 回滚与实施门禁

当前 `main` 与 `origin/main` 一致；本地 `codex/hybrid-receiver` 包含尚未形成完整远端回滚点的实现历史。代码修改前必须：

```text
提交本设计
→ 创建 codex/pre-wsl-integration-snapshot-20260718
→ 运行基线验证
→ 推送快照到 GitHub
→ 核对本地与远端 SHA
→ 创建 codex/wsl-protocol-integration 独立 worktree
→ 才允许修改代码
```

快照分支推送后不再追加开发提交。禁止修改 main、force-push、删除原远端分支或改写快照历史。后续每个任务使用独立实现子代理，依次通过需求符合性审查、代码质量审查、测试和提交门禁。

## 12. 方案3与未来扩展

第一阶段使用单 WSL2 方案。完整 Ubuntu 22.04 VM 作为第二阶段本机准生产目标，复用相同依赖清单、场景、PTY模拟器、断言、release和证据格式，仅替换外层 doctor、文件传输和启动适配。

RoboMasterEngine作为更高层裁判节点接入，不改变解析波 ROS2 接口。完整 YOLO/工业相机雷达验收是独立项目，不回填到本阶段完成条件。
