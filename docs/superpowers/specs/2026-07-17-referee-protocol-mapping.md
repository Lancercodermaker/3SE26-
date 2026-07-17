# RoboMaster 2026 裁判协议与 SDR 闭环映射

**状态：** 现状证据基线
**协议版本：** 《RoboMaster 2026 机甲大师高校系列赛通信协议 V1.3.1（20260519）》
**协议 SHA-256：** `0197a5f97f7171dc74cd5040d4e5872afb92db44a0307e682a4efd3f2688db42`
**代码基线：** `f6a9b405cacbe6f8f26a2a30ba7f8105f1a71750`

本文固定裁判系统、雷达主工程和 SDR 接收端之间与密钥闭环直接相关的协议语义，并如实记录当前代码与 V1.3.1 的差异。它是参考基线和自动化实验场的验收依据，不将尚未由协议或代码证明的行为写成既定事实。

## 1. 三节点边界

```text
裁判系统 -- 常规串口链路/A5 --> 雷达主工程 -- /judge/radar_context --> SDR 接收端
裁判系统 <-- 常规串口链路/A5 -- 雷达主工程 <-- /sdr/jam_code ------- SDR 接收端
```

- 裁判系统向雷达主工程发送 `0x0201` 机器人状态、`0x0001` 比赛状态和 `0x020E` 雷达自主决策信息。
- 雷达主工程将已经解析的权威上下文发布为 `sdr_receiver/msg/RadarContext`；接收端不直接解释裁判串口。
- SDR 接收端从雷达无线链路命令 `0x0A06` 得到六字节密钥，发布 `sdr_receiver/msg/JamCode`；`0x0A06` 是项目内部无线命令，不是本文 PDF 的裁判串口 `cmd_id`。
- 雷达主工程负责把 JamCode 包装进裁判串口 `cmd_id=0x0301`、子内容 ID `0x0121`；接收端不得自行构造裁判 A5 帧。

## 2. A5 串口帧

协议第 4 页（PDF 第 5 页，表 1-2 至 1-4）定义常规串口链路为 115200 baud、8 数据位、1 停止位、无硬件流控、无奇偶校验。整帧为：

| 字节偏移 | 长度 | 字段 | 语义 |
|---:|---:|---|---|
| 0 | 1 | `SOF` | 固定 `0xA5` |
| 1 | 2 | `data_length` | `data` 长度，小端序 |
| 3 | 1 | `seq` | 包序号 |
| 4 | 1 | `CRC8` | 前 5 字节帧头校验 |
| 5 | 2 | `cmd_id` | 命令码，小端序 |
| 7 | n | `data` | 命令数据 |
| 7+n | 2 | `CRC16` | 整包校验，小端写入 |

总长度为 `n + 9`。当前发送实现 `frameInit()` 设置 `SOF/data_length/cmd_id`，`sendFrame()` 单调递增 `seq`，追加 CRC8 和 CRC16 后写串口，与上述布局一致。当前接收实现虽然按 A5、长度和小端 `cmd_id` 切帧，但 `framePreProcess()` 中的 CRC8/CRC16 验证被注释，因而“当前雷达代码已验证入站 CRC”不成立；自动化裁判模拟器必须发送正确 CRC，同时应把恢复入站 CRC 校验列为雷达主工程独立修复项。

代码证据：`src/radar_referee/include/robot_referee/RefereeProtocol.hpp`、`src/radar_referee/include/robot_referee/SendReceive.hpp`、`src/radar_referee/src/SendReceive.cpp`、`src/radar_referee/src/CRC.cpp`。

## 3. `0x020E` 到 `RadarContext`

协议第 22 页（PDF 第 23 页，表 1-24）规定 `0x020E` 数据长 1 字节、服务器以 1 Hz 向己方雷达发送：

| `radar_info` 位 | 协议语义 | 当前代码状态 | ROS 映射 |
|---|---|---|---|
| bit 0-1 | 雷达可触发双倍易伤的机会，0 至 2 | `_vulnerableOpp = raw & 0x03` | 不进入 `RadarContext` |
| bit 2 | 对方是否正在被触发双倍易伤 | `_isVulnerable` 状态转换 | 不进入 `RadarContext` |
| bit 3-4 | 己方加密等级，即对方干扰波难度；开局 1、最高 3 | `_jam_level = (raw >> 3) & 0x03` | `jam_level` |
| bit 5 | 当前是否可以修改己方密钥 | `_key_mutable = (raw & 0x20) == 0x20` | `key_mutable` |
| bit 6-7 | 保留 | 原样保留于 `_radar_info_raw` | `radar_info_raw` |

雷达在同一次 `0x020E` 分支中先更新 `_radar_info_raw`、`_jam_level`、`_key_mutable`，再调用 `publishRadarContext()`，因此这三个字段属于同一裁判帧。其余上下文字段来自：

- `self_id`：`0x0201 robot_id`；当前代码只把 9 识别为红方雷达、109 识别为蓝方雷达。
- `self_color`：9 映射为 2，109 映射为 0，其他 ID 映射为 -1。
- `game_progress`、`match_time`：来自 `0x0001`；仅比赛阶段 4 使用剩余时间，否则 `match_time=-200`。
- `referee_online`：当前实现仅依据 `self_id` 是否为 9/109，不是串口超时探测结果。

当前 `/judge/radar_context` 只在收到 `0x020E` 后发布；此前收到的 `0x0001` 或 `0x0201` 不会单独触发发布。代码证据：`RefereeControl::executeCommand()` 与 `RefereeControl::publishRadarContext()`，消息证据：`sdr_receiver/msg/RadarContext.msg`。

## 4. `JamCode` 到裁判上报

### 4.1 ROS 输入门槛

雷达订阅 `/sdr/jam_code`。当前 `wirelessKeyCallback()` 仅检查：

1. `msg.valid == true`；
2. `msg.key.size() == 6`（固定数组在正常 ROS 消息中恒为 6）。

通过后把六字节复制到 `radar_cmd.password_1..6` 并设置 `_password_updated=true`。当前雷达回调不再次检查 `command_id==0x0A06`、ASCII 字母或数字、队伍、等级、目标、`key_mutable`、上下文新鲜度或重复提交间隔；这些字段只被记录或完全未用于门控。因此参考基线必须依赖接收端的唯一发布门，雷达侧后续加固不能被本映射误称为已实现。

### 4.2 V1.3.1 规定的上报格式

协议第 22-23 页（PDF 第 23-24 页，表 1-25）定义 `cmd_id=0x0301` 数据头：

| 偏移 | 长度 | 内容 |
|---:|---:|---|
| 0 | 2 | 子内容 ID `0x0121` |
| 2 | 2 | `sender_id`，须与自身 ID 匹配 |
| 4 | 2 | `receiver_id`；当前代码使用裁判端 `0x8080` |
| 6 | x | 子内容数据 |

协议第 30 页（PDF 第 31 页，表 1-34）把 `0x0121` 子内容定义为 8 字节：

| 子内容偏移 | 长度 | 语义 |
|---:|---:|---|
| 0 | 1 | 双倍易伤确认计数，必须单调递增且每次只增加 1 |
| 1 | 1 | 密钥指令类型：`1` 更新己方加密密钥；`2` 验证雷达破解的对方密钥 |
| 2 | 6 | 六个 ASCII 字母或数字组成的密钥 |

类型 1 仅在开局和对方破解成功导致己方加密等级提高时有效；类型 2 每次更新验证密钥后的 10 秒内再次更新无效。`0x0301` 总 `data_length` 因而应为 6 字节交互头加 8 字节 `0x0121` 内容，即 14 字节，A5 整帧长度 23 字节。

协议内部存在一处版面矛盾：PDF 第 24 页的子内容索引表把 `0x0121` 内容长度写成 1，而 PDF 第 31 页的专用表明确给出偏移 0 的 1 字节加偏移 1 的 7 字节。闭环实现采用专用表的 8 字节布局，因为只有它能够承载协议定义的六字节密钥。

### 4.3 当前 `sendKey()` 与协议的差异

当前代码确实发送外层 `0x0301`、子内容 `0x0121`、接收者 `0x8080`，并调用 `frameInit(..., 6 + 8, 0x0301)`；布局长度与专用表一致。但当前两阶段值不符合 V1.3.1：

- 所谓 `phase 1` 在比赛阶段 4 首次进入时设置 `password_cmd=2`，却不填六字节密钥。这会在线上被解释为“验证六个零字节”，不是协议定义的握手阶段。
- 收到 JamCode 后所谓 `phase 2` 设置 `password_cmd=3` 并填写六字节密钥；V1.3.1 未定义类型 3。破解对方密钥的正确类型是 2。
- 注释中的 10 秒等待条件已被禁用；线程每秒调用 `sendKey()`，而协议规定类型 2 验证后的 10 秒内再次更新无效。

因此，当前日志出现 `key phase 2 start` 和 `key has send`只能证明 ROS 回调、密钥存储及串口发送代码路径被执行，不能证明裁判系统接受了密钥。V1.3.1 合规闭环应直接形成类型 2、六字节密钥的 `0x0121` 内容，并以随后收到的 `0x020E bit 3-4` 等级提升作为裁判接受的权威证据。修复 `sendKey()` 属于雷达主工程任务，不应夹带进 `codex/open-source-replacement` 接收端参考基线。

代码证据：`RefereeControl::wirelessKeyCallback()`、`RefereeControl::sendKey()`、`src/radar_referee/src/node_main.cpp`。

## 5. 可自动断言的闭环

协议模拟和真实雷达在环测试必须按下列顺序留存原始帧、ROS 消息和时间戳：

1. 模拟裁判发送有效 CRC 的 `0x0201`、比赛阶段 4 的 `0x0001` 和 L1 的 `0x020E`。
2. 雷达发布字段一致的 `/judge/radar_context`；至少逐字节核对 `radar_info_raw`、`jam_level` 和 `key_mutable`。
3. 接收端只在其验证门通过后发布一条 `command_id=0x0A06`、六字节 ASCII 密钥的 `/sdr/jam_code`。
4. 雷达发出 CRC 正确、`cmd_id=0x0301`、子内容 `0x0121`、类型 2、相同六字节密钥的 23 字节帧。
5. 模拟裁判校验发送者、接收者、类型、密钥和 10 秒限制；接受后发送 bit 3-4 提升的下一帧 `0x020E`。
6. 雷达发布新等级 `RadarContext`，接收端切换到下一目标等级。

步骤 4 在修复当前 `sendKey()` 前应明确失败；自动化不得把类型 3加入模拟器白名单来掩盖协议差异。纯 ROS `RadarContractSimulator` 可以覆盖步骤 2、3、6，但只有真实雷达主工程加虚拟串口裁判模拟器才能覆盖步骤 1、4、5。

## 6. 本文未覆盖的范围

以下内容不是密钥闭环的组成部分，本映射不为其正确性背书：

- 雷达无线链路 `0x0A01..0x0A06` 的空口调制、频点和完整载荷协议；正式通信 PDF 未定义这些项目内部命令。
- `0x0305` 小地图数据、`0x0308` 自定义信息、雷达向其他机器人发送的 `0x0200..0x02FF` 自定义子内容。
- 官方赛事引擎的账号、联网、局域网发现和 GUI 行为；它属于高级集成验收层，不阻塞参考基线。
- 实体裁判硬件、官方发射链路、USB/Pluto 时序和 RF 性能；这些必须由后续硬件验收提供证据。
- 串口断线检测、帧重传、乱序、半帧跨读取和恶意长度防护；当前代码未提供足够证据证明这些行为已受控。

## 7. 证据索引

- 协议 PDF：第 4 页 A5 帧；第 7 页命令索引中的 `0x020E/0x0301`；第 22 页 `0x020E` 与 `0x0301` 头；第 23 页子内容索引；第 30 页 `0x0121` 专用定义（页码均指印刷页，PDF 页码分别加 1）。
- 帧定义与解析：`src/radar_referee/include/robot_referee/RefereeProtocol.hpp`、`src/radar_referee/include/robot_referee/SendReceive.hpp`、`src/radar_referee/src/SendReceive.cpp`。
- 上下文与上报：`src/radar_referee/src/RefereeControl.cpp`、`src/radar_referee/src/node_main.cpp`。
- ROS 消息：`sdr_receiver/msg/RadarContext.msg`、`sdr_receiver/msg/JamCode.msg`。
