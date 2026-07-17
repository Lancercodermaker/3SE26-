# SDR 录波证据清单

日期：2026-07-17
适用范围：`codex/open-source-replacement` 参考基线及后续混合接收器离线回归

## 1. 目的与判定边界

本清单把聊天中提供的录波固化为可寻址、可复验的测试证据。文件名中的队伍或等级只是采集标签；除非有 CRC 合法帧或伴随记录佐证，不把文件名当成协议真值。

这里的“正/负”有两个互不等价的维度：

- **physical-positive**：采集场景声称包含目标队伍的干扰波，可用于检测/解调；仍须用 `verification` 区分已确认与候选。
- **context-negative**：IQ 中可以存在、甚至可以成功解出合法帧，但在给定己方/对方上下文中不得发布 `/sdr/jam_code`。它不是“纯噪声”同义词。
- **fault-sample**：采集链路已知失真或配置错误，用于验证安全失败和诊断，不要求解出密钥。

比赛中信息波和干扰波同时存在，因此 physical-positive 文件也可能混有信息波；“未解出信息波”不能据此证明文件中没有信息波。

## 2. 权威文件清单

用户本轮列出的 13 个路径，以及仓库既有 runtime manifest 额外依赖的 `RX_BLUE_ganrao_1`，均已于 2026-07-17 在当前 Windows 主机逐一确认存在；大小和 SHA256 均由文件内容实算。IQ 文件均不得进入 Git 仓库。

`P-BLUE-L1-ORACLE` 不是用户本轮新列出的路径，但当前仓库 `sdr_receiver_py_wrapper/fixtures/manifest.json` 和参考基线计划依赖它，因此必须与本轮证据一起固化。它是当前唯一带已确认命令和密钥 oracle 的 BLUE 样本。

| ID | 绝对路径 | 字节数 | SHA256 | 格式与采样率 | 分类 | 场景语义与验证状态 |
|---|---|---:|---|---|---|---|
| P-BLUE-L1 | `E:\录波\raw_data_1_本场己方为红方\raw_data.bin` | 10,323,755,008 | `f51ca8ff90574a4a50d2cc9661ca4b32fe01d72d84626ac20888db25e3c4c366` | 内容符合小端 float32 交织 IQ（每复样点 8 字节）；无伴随元数据，采样率没有权威记录 | physical-positive / candidate | 己方 RED，对方 BLUE，用户标注 BLUE L1；包含信息波的可能性未知。只作候选 L1，不得写死期望密钥或时长。 |
| F-BO3 | `E:\录波\bo3_match_own_RED_vs_BLUE_20260524_111243.c64` | 4,614,400,000 | `6634d251288d8e412048ffb5704b69249eeeaf29b0408930dada2d177d14d282` | little-endian `numpy.complex64`，1,000,000 sps，576,800,000 复样点（576.8 s） | fault-sample | 己方 RED、接收 BLUE；ADC RMS 归一化错误诱发高增益/削顶，且官方发送 L1 时接收 LO/目标被错设为 L3。要求安全不误报并产生过载/无 CRC 诊断，不要求解密钥。 |
| P-BLUE-L1-ORACLE | `C:\Users\Fancy\Downloads\RX_BLUE_ganrao_1` | 119,799,808 | `8cde16d3fe8230334a9efcb36c81ae105b76b4118f4fe3fc63943aeb791be7cc` | little-endian `complex64`，2,000,000 sps，约 7.487488 s | physical-positive / confirmed | 仓库既有 runtime manifest 额外依赖；已确认可恢复合法 `0x0A06` 和六字节 ASCII 密钥 `fcYqTC`。它是参考基线的强解码 oracle，不是本轮用户新增录波。 |
| P-BLUE-L2 | `C:\Users\Fancy\Downloads\RX_BLUE_ganrao_2` | 120,848,384 | `4c058c3ad0fa78c00fe4bbfb412f13d15ebf14d4fa2759681a5886ddb67e46e6` | little-endian `complex64`，2,000,000 sps，约 7.553024 s | physical-positive / candidate | 用户标注 BLUE L2；需求文档也只把 L2 视为候选。成为强断言前须确认实际命令、密钥、CRC 模式和频偏。 |
| P-BLUE-L3 | `C:\Users\Fancy\Downloads\RX_BLUE_ganrao_3` | 146,415,616 | `1cafdaf46d451f97753088ed8c7d170dcfd78139e9e6971be19e374e9f79bfd0` | little-endian `complex64`，2,000,000 sps，约 9.150976 s | physical-positive / candidate | 用户标注 BLUE L3；需求文档也只把 L3 视为候选。成为强断言前须确认实际命令、密钥、CRC 模式和频偏。 |
| C-RED-RAW2 | `C:\Users\Fancy\Downloads\RX_RED (2)` | 294,912,000 | `584bd88de2e2fa47dbbdf04aaca3567e2508cf50b3b13ad556f19ed69bf365ee` | 内容符合 little-endian `complex64`；无伴随元数据，采样率没有权威记录 | context-negative / candidate | 己方 RED 时，RED 方向不是对方目标；即使出现合法 RED 帧也必须被上下文层抑制。没有伴随协议真值，不断言“无合法帧”。 |
| C-RED-L1 | `C:\Users\Fancy\Downloads\RX_RED_ganrao_1` | 136,052,736 | `81f6553ac4125d4803cf9cd734ff503b89d1d48f44bb443ca13683b71bb540cb` | 内容符合 little-endian `complex64`；无伴随元数据，采样率没有权威记录 | context-negative / candidate | 用户标注 RED L1；用于己方 RED 上下文的反向/误队伍抑制，不作为纯噪声样本。 |
| C-RED-L2 | `C:\Users\Fancy\Downloads\RX_RED_ganrao_2` | 127,541,248 | `bc8a45838abffa790589a168f737847b2ad2be1af49b255c0924e40ec7bb0c46` | 内容符合 little-endian `complex64`；无伴随元数据，采样率没有权威记录 | context-negative / candidate | 用户标注 RED L2；用于己方 RED 上下文的反向/误队伍抑制，不把文件名等级升级为协议真值。 |
| C-RED-L3 | `C:\Users\Fancy\Downloads\RX_RED_ganrao_3` | 136,314,880 | `7c4c5fe6afb6a96ef88a7442d50c4e70f132dde1772c384a33e588b02c81b52c` | 内容符合 little-endian `complex64`；无伴随元数据，采样率没有权威记录 | context-negative / candidate | 用户标注 RED L3；用于己方 RED 上下文的反向/误队伍抑制，不把文件名等级升级为协议真值。 |
| C-RED-L1-6S | `C:\Users\Fancy\Downloads\disturb_rx_red_level1_20260522_0848_6s.fc32` | 48,000,000 | `c84e36c4445231b8acc75ef8fdaa833e684e5721c62296deb03db2117771920d` | little-endian `complex64`，1,000,000 sps，6.0 s | context-negative / confirmed-physical | 用户标注己方 RED 场景中的 RED L1。既有离线结果恢复出多条 CRC 合法 `0x0A06`、ASCII `JzMyK0`，所以它明确证明“物理可解码但上下文不得授权输出”。 |
| C-RED-RAW | `C:\Users\Fancy\Downloads\RX_RED` | 145,489,920 | `0a36d52264d1edf55199af9de4dfd987ff1ed49a82440d7b9082cd7dbd561996` | 内容符合 little-endian `complex64`；无伴随元数据，采样率没有权威记录 | context-negative / candidate | 己方 RED 上下文的反向队伍候选样本；没有伴随协议真值，不得断言无帧或固定等级。 |

## 3. 伴随证据

### 3.1 BO3 采集元数据

`E:\录波\bo3_match_own_RED_vs_BLUE_20260524_111243.json`

- 字节数：2,158
- SHA256：`f7aa92d7aefac1d422e8b7c21d8bec0049d45ff4605ac02409f16e1503bcf925`
- 声明格式：`numpy.complex64 little-endian interleaved IQ`
- `sample_rate=1000000`、`own_team=RED`、`rx_team=BLUE`
- `target=L3`、`rx_lo_hz=434360000`、`rx_gain=64`
- `samples_written=576800000`，`last_peak=434.0138`，扫描结果 `NO_CRC16`

这些字段支持 F-BO3 的采集故障定位；它们不支持把该文件当作 BLUE L3 正样本。

### 3.2 RED L1 六秒录波证据包

| 路径 | 字节数 | SHA256 | 证据角色 |
|---|---:|---|---|
| `C:\Users\Fancy\Downloads\disturb_rx_red_level1_20260522_0848_6s.json` | 611 | `94949057405c4aa594287ce2425e6751cf9333f0b3497523bc557407bbfe3744` | 采集元数据：`complex64_le_interleaved`、1 Msps、Pluto `ip:192.168.2.1` |
| `C:\Users\Fancy\Downloads\disturb_rx_red_level1_20260522_0848_6s.air_scan.json` | 68,225 | `2a50224b6f30f09e0cbad592730fded5c2b0ff2935f552241d501b30bdb75542` | 多窗口/多频偏离线扫描记录；属于派生证据，不是 IQ 真值 |
| `C:\Users\Fancy\Downloads\disturb_rx_red_level1_20260522_0848_6s.offline_v67_RED_L1.json` | 12,748 | `f237f4a34c3640b321a851940690dd8e97601309ca4e1f168ff37aa716b71a30` | v67 离线输出，含合法 `cmd_id=2566 (0x0A06)`、等级 1、`JzMyK0`；属于可重算派生证据 |

派生 JSON 只能证明“该版本解码器曾给出什么结果”。参考基线必须重新从 C-RED-L1-6S IQ 计算，并独立校验 CRC 和 payload，不能把 JSON 直接当作解码输入。

## 4. 参考基线的使用规则

1. 每次回归先按 SHA256 校验；缺失或哈希不符时测试必须明确失败，禁止静默跳过或改找同名文件。
2. 绝对路径只属于本机证据定位。自动化应通过外部 manifest/path-map 注入根目录，不把这些路径编译进接收器。
3. physical-positive/candidate 只要求运行完成、记录诊断和候选结果；在获得可信密钥/CRC 真值前，不得要求“必须发布某六字节密码”。
4. C-RED-L1-6S 至少承担两类独立断言：纯解码层应可复核合法 RED L1 帧；己方 RED 的完整上下文链路必须零次发布该 RED 密钥。
5. context-negative 的核心断言是“授权输出为零”，不是“CRC 合法帧为零”。解码器和上下文仲裁器的统计必须分开保存。
6. F-BO3 的核心断言是无伪造 JamCode、进程不崩溃、诊断明确；不得将“解不出密钥”记为算法回归。
7. P-BLUE-L1 没有权威采样率。首次用于定量回归前必须由采集来源或信号分析确定采样率，并以外部测试配置显式给出；在此之前只能做非权威探索，不得猜测为 1 Msps 或 2 Msps。
8. 自制发射端产生的数据应标为 `synthetic`，不得混入本清单的官方现场录波通过率。

## 5. 可重复完整性校验

在 Windows PowerShell 中运行：

```powershell
$paths = @(
  'E:\录波\raw_data_1_本场己方为红方\raw_data.bin',
  'E:\录波\bo3_match_own_RED_vs_BLUE_20260524_111243.c64',
  'E:\录波\bo3_match_own_RED_vs_BLUE_20260524_111243.json',
  'C:\Users\Fancy\Downloads\RX_BLUE_ganrao_1',
  'C:\Users\Fancy\Downloads\RX_BLUE_ganrao_2',
  'C:\Users\Fancy\Downloads\RX_BLUE_ganrao_3',
  'C:\Users\Fancy\Downloads\RX_RED (2)',
  'C:\Users\Fancy\Downloads\RX_RED_ganrao_1',
  'C:\Users\Fancy\Downloads\RX_RED_ganrao_2',
  'C:\Users\Fancy\Downloads\RX_RED_ganrao_3',
  'C:\Users\Fancy\Downloads\disturb_rx_red_level1_20260522_0848_6s.fc32',
  'C:\Users\Fancy\Downloads\disturb_rx_red_level1_20260522_0848_6s.air_scan.json',
  'C:\Users\Fancy\Downloads\disturb_rx_red_level1_20260522_0848_6s.json',
  'C:\Users\Fancy\Downloads\disturb_rx_red_level1_20260522_0848_6s.offline_v67_RED_L1.json',
  'C:\Users\Fancy\Downloads\RX_RED'
)
Get-FileHash -Algorithm SHA256 -LiteralPath $paths | Format-Table Path, Hash -AutoSize
```

哈希覆盖原始文件字节，不覆盖路径、修改时间或聊天标签；移动文件不会改变身份，任何字节级改动都会改变身份。
