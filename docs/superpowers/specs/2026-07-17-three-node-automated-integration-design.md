# 三节点自动化集成与安全部署设计

**状态：** 已批准设计基线
**日期：** 2026-07-17
**适用仓库：** `Lancercodermaker/3SE26-`
**关联基线：** `codex/open-source-replacement`、`codex/hybrid-receiver`

## 1. 目标与边界

本设计把比赛链路固定为三个可替换、可独立验证的节点：

1. 裁判系统：真实裁判硬件、官方 RoboMasterEngine 或协议模拟器；
2. 雷达主工程：真实 `radar_referee/RefereeControl`；
3. 解析波接收端：`sdr_receiver_py_wrapper`，输入为 IQ 文件或 PlutoSDR。

裁判系统与雷达主工程通过 0xA5 裁判串口协议通信；雷达主工程与接收端通过 ROS 2 通信。模拟器只追求协议与行为合同等效，不宣称等效于官方射频波形、真实串口电气特性或现场网络时序。

官方 RoboMasterEngine 是高级集成验收目标，不阻塞 `codex/open-source-replacement` 软件参考基线完成。

## 2. 数据流和所有权

```text
裁判系统/RefereeScenarioDriver
  | 0xA5 串口入站：比赛状态、0x020E radar_info
  v
真实雷达主工程 RefereeControl
  | ROS 2 /judge/radar_context : sdr_receiver/msg/RadarContext
  v
解析波接收端
  | ROS 2 /sdr/jam_code : sdr_receiver/msg/JamCode
  v
真实雷达主工程 RefereeControl
  | 0xA5 串口出站：密钥阶段响应
  v
裁判系统/RefereeScenarioDriver
```

职责约束：

- 裁判系统拥有比赛阶段、己方加密等级和密钥可修改状态；
- 雷达主工程负责解析/生成裁判协议帧、发布 `RadarContext`、接收 `JamCode`、保存密钥并执行阶段响应；
- 接收端只负责采集、解码、上下文仲裁和发布 `JamCode`，不生成发往裁判系统的 0xA5 帧；
- 纯 `RadarContractSimulator` 只能测试 ROS 合同；只有真实 `RefereeControl` 在环才能证明密码存储和阶段响应代码路径。

协议审计发现，当前 `RefereeControl::sendKey()` 的第二阶段使用了 V1.3.1 未定义的验证类型 `3`，而协议 `0x0301/0x0121` 规定破解验证类型为 `2`。因此当前日志只能证明代码路径被调用，不能证明赛事引擎接受了密钥。该雷达侧修复应作为三节点集成的独立后续任务；它不阻塞接收端参考基线，但高级闭环验收不得在修复前通过。

## 3. 两级自动化实验场

### 3.1 Level 1：Windows + WSL 本机闭环

Level 1 提供两种互补模式：

- 快速合同模式：`RadarContractSimulator` 发布 `RadarContext` 并检查 `JamCode`，用于接收端快速回归；
- 真实雷达在环模式：WSL 运行真实雷达主工程，`RefereeScenarioDriver` 通过 PTY/虚拟串口向 `RefereeControl` 发送裁判帧并验证其回包。

输入按证据强度分层：

1. 官方赛场录波的只读离线回放；
2. PlutoSDR 实时接收配合自制发射脚本；
3. 将来可获得的官方发射链路。

编排器必须完成环境预检、唯一 `ROS_DOMAIN_ID` 分配、构建、启动顺序、场景时间线、机器断言、超时清理和证据归档。产物至少包括录波身份、ROS bag、裁判串口帧、节点日志、JSON 结果和退出状态。

### 3.2 Level 2：远端 Ubuntu 22.04 准生产实验场

Level 2 通过 SSH 执行固定动作，而不是向自动化代理开放任意远端 shell。测试闸门按顺序为：

1. 只读 doctor；
2. 单元测试和构建；
3. ROS 通信测试；
4. 官方录波回放；
5. PTY 裁判模拟器 + 真实雷达主工程；
6. PlutoSDR 实时接收；
7. 可用时接入 RoboMasterEngine 或真实裁判系统。

任一闸门失败即停止后续动作，保留现场并回收证据。

## 4. 远端 SSH 安全模型

### 4.1 身份和权限

- 使用专用账号 `sdr-deploy`，默认无通用 `sudo`，不加入具有系统管理能力的组；
- SSH key 使用 `authorized_keys` 的 forced-command，固定调用由 root 管理、位于 release 目录之外的 `/usr/local/libexec/sdr-deploy-dispatcher`；待部署仓库和 release 无权修改该入口；
- dispatcher 不调用 `sh -c`，只接受 `doctor`、`stage`、`build`、`test`、`activate`、`stop`、`collect`、`rollback` 的固定 argv schema；拒绝额外参数、路径穿越、绝对用户路径、环境变量注入和 shell 元字符；
- 禁止部署程序修改 `/etc`、系统 ROS 安装、网络配置、其他用户目录和非项目服务；
- `sdr-deploy` 只能写 root 预建的 staging 和活动 run 临时目录；不能写 `releases`、`current`、dispatcher、已封存证据或审计日志；
- `activate`、`rollback` 和封存动作只能调用 root 管理的最小特权 helper。`sudoers` 精确允许 helper 的绝对路径和固定子命令，不允许解释器、编辑器或任意参数。

### 4.2 不可变发布

每个版本部署到独立目录：

```text
/opt/sdr-receiver/
  staging/<upload-id>/             # sdr-deploy 可写
  releases/<full-commit-sha>/
  current -> releases/<validated-sha>/
/var/lib/sdr-receiver/runs/<run-id>/
/var/log/sdr-receiver/audit.jsonl
```

`stage` 只能创建唯一 staging 目录。测试通过后，root helper 逐项复核 commit、清单、普通文件类型和 SHA-256，再以 no-replace 语义提升为 root 持有且 `sdr-deploy` 只读的 release。`activate` 由 helper 原子切换 root 持有的 `current`；部署账号无权覆盖、篡改或删除任何 release。上传内容绑定 Git commit 和 SHA-256 清单。主机差异进入版本化配置文件，禁止把用户名和绝对路径写进源码。

### 4.3 审批与审计

- 所有写操作先生成 dry-run 计划，列出创建、启动、停止和切换动作；
- 安装系统包、修改设备权限、修改 `/etc`、使用 `sudo`、删除 release 或改变网络必须由人工批准；
- 每次运行记录操作者、commit、配置哈希、命令、开始/结束时间、退出码和产物哈希；
- dispatcher 将审计记录追加到 root 持有、部署账号只读的日志，证据封存时由 root helper 将 run 目录改为 root 持有和只读；
- 自动化不得持有可绕过 forced-command 的通用 SSH 凭据。

### 4.4 失败和恢复

构建或测试失败时不切换 `current`。激活后健康检查失败时，编排器通过 helper 停止新进程并切回最近一次通过验证的 release。随后 helper 封存失败目录和日志，使其变为 root 持有、部署账号只读；自动化不得清理 release、封存证据或审计日志。

磁盘达到管理员预设水位时，dispatcher 拒绝新的 `stage` 并报告可回收对象，但不执行删除。管理员依据审计清单人工选择保留策略，并使用不授予自动化的独立维护命令删除。只有驱动、USB/串口权限、网络、磁盘、系统包或主机管理员配置问题需要人工介入；普通软件失败由回滚和诊断报告处理。dispatcher/helper 安装或升级本身也必须由管理员在独立维护窗口人工完成。

## 5. 虚拟机和 Docker 分工

不使用单个 Docker 容器宣称完整复刻比赛链路。

- Docker：纯软件单元测试、IQ 回放、ROS 合同模拟、可重复构建和部署脚本测试；
- Ubuntu 22.04 VM/WSL：真实雷达主工程、真实接收端、PTY 裁判模拟器；
- Windows 主机或 Windows VM：官方 `RoboMasterEngine.exe`；
- Ubuntu 裸机：最终 PlutoSDR、USB、串口和运行时序验收。

RoboMasterEngine 是 Windows Unity GUI 应用，并要求外网、账号登录、本地 IP 检测和局域网。Windows 容器不作为支持目标。若置于 Windows VM，必须使用桥接网络，并单独验证局域网发现、登录和串口/USB 转发。Pluto 和真实串口优先连接宿主机或 Ubuntu 裸机，避免多层 USB 转发成为不可诊断变量。

## 5.1 当前可执行硬件矩阵

现有 SAW 与 LNA 集成在同一块板上，不能拆分；当前没有外置衰减器；短 USB 线可用于本地测试并规避有源 5 m USB 线变量；天线为 433 MHz 八木或吸盘天线。软件优先通过 Pluto 增益配置处理过强输入。本文明确取代 2026-07-10 架构中“无外置增益、逐个加入 SAW/LNA/衰减器”的不可执行矩阵。

当前验收顺序为：官方录波回放；Pluto 直连短 USB 的低增益安全启动；集成 SAW+LNA 板的分级软件增益测试；433 MHz 八木/吸盘天线对比。无衰减器和无法拆分 SAW/LNA 不阻塞软件参考基线，也不得要求用户为了基线验收临时采购硬件。动态范围和现场 RF 等效性保持为高级硬件验收项。

## 6. 场景与断言

同一份版本化场景文件必须能驱动 Level 1 和 Level 2。每个场景包含：己方颜色、入站裁判帧、上下文时间线、IQ 输入身份、预期或禁止的 `JamCode`、预期雷达回包、超时和证据目录。

最低场景集：

- RED 己方、BLUE L1/L2/L3 干扰波，允许对应合法密钥；
- RED 己方、RED 波形，解码器可发现物理合法帧但上下文仲裁禁止发布；
- 0x020E 等级变化后旧上下文失效；
- `key_mutable=0` 时不得产生阶段写入；
- 削顶、LO 错设、空文件、截断文件和元数据不一致必须失败关闭；
- 一个场景中 `JamCode` 数量、字节、等级、队伍和时间窗必须精确匹配；
- 真实雷达在环时必须同时证明 callback、密钥存储和裁判回包顺序。

## 7. 证据与可重复性

录波文件不复制进 Git。Git 中保存清单、哈希、格式、上下文标签和获取说明。每次运行结果必须绑定：

- 代码完整 commit；
- 配置和场景 SHA-256；
- IQ 文件身份与读取范围；
- 解码器 ID 和上游版本；
- ROS topic/type/QoS；
- 串口收发原始字节；
- 主机 doctor 报告；
- 所有断言和退出状态。

官方录波、自制发射端和官方赛事引擎产生的证据分开统计，不得用合成发射成功替代官方波形回归。

## 8. 验收边界

软件参考基线完成必须证明：离线解码确定性、上下文拒绝、ROS 合同和可重复测试。它不依赖 RoboMasterEngine 登录成功、Pluto 实时链路或远端 Ubuntu 权限。

高级集成验收逐层增加：真实雷达在环、远端 Ubuntu、Pluto、官方赛事引擎、真实裁判硬件和官方发射链路。低层通过不得被表述为高层等效。
