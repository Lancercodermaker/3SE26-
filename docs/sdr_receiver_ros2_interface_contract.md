# SDR Receiver ROS2 Interface Contract

日期: 2026-05-12

本文是 SDR C++ 接收端与雷达工程之间的接口契约草案。雷达工程由队友修改；本任务只实现接收端、mock 联调工具和文档化接口。

## 1. 范围边界

- SDR 接收端负责从空口解调 `0x0A06` key 和 `0x0A01..0x0A05` INFO 内容。
- 雷达工程负责从裁判系统解析 `0x0201 robot_status` 和 `0x020E radar_info`，并把上下文发布给 SDR 接收端。
- 雷达工程负责把 SDR 接收端发布的 key 组帧发送给裁判系统。
- 裁判系统密钥发送帧格式、sub command、user_data 字段布局由雷达工程侧根据官方协议实现。

## 2. 输入: RadarContext

推荐 topic:

```text
/judge/radar_context
```

推荐消息: `vision_interface/msg/RadarContext`

```text
std_msgs/Header header
uint8 self_id
int8 self_color
uint8 radar_info_raw
uint8 jam_level
bool key_mutable
uint8 game_progress
int16 match_time
bool referee_online
```

字段语义:

- `self_id`: `0x0201 robot_status` 中的本机 ID。红方雷达站为 `9`，蓝方雷达站为 `109`。
- `self_color`: `0` 表示 BLUE，`2` 表示 RED，`-1` 表示 unknown。
- `radar_info_raw`: `0x020E radar_info` 原始 1 字节。
- `jam_level`: `(radar_info_raw >> 3) & 0x03`。有效比赛值为 `1..3`，`0` 表示无效或未知。
- `key_mutable`: `((radar_info_raw >> 5) & 0x01) != 0`。
- `game_progress`、`match_time`、`referee_online`: 用于状态观测和热启动判断。

发布策略:

- QoS: `reliable`、`keep_last=5`。
- 解析到 `0x0201`、`0x020E`、`0x0001` 后立即发布。
- 建议 5-10 Hz 周期重发最近上下文，避免 SDR 接收端晚启动后长时间等待。

最低成本替代方案:

```text
/match_info        vision_interface/msg/MatchInfo
/judge/radar_info  std_msgs/msg/UInt8
```

长期仍建议使用单一 `RadarContext`，减少多 topic 状态不同步风险。

## 3. 输出: JamCode

topic:

```text
/sdr/jam_code
```

推荐消息: `sdr_receiver/msg/JamCode`

```text
std_msgs/Header header
bool valid
uint8 level
uint8[6] key
string ascii_code
string team
string target
uint8 radar_info_raw
bool key_mutable
```

字段语义:

- `valid`: true 表示本条 key 通过接收端解调与校验。
- `level`: `1..3`。
- `key`: `0x0A06` payload 中的 6 字节 key。
- `ascii_code`: 同一 key 的 ASCII 展示值。
- `team`: `RED` 或 `BLUE`。
- `target`: `JAM_L1_KEY`、`JAM_L2_KEY` 或 `JAM_L3_KEY`。
- `radar_info_raw`、`key_mutable`: 发布 key 时接收端看到的最新裁判上下文快照。

限频:

- 同一 key 建议每 500 ms 最多发布一次。
- 重试次数由参数 `key_retry_limit` 控制。
- 当 `level < max_jam_break_level` 时，接收端发布 key 后等待下一帧 `0x020E` 等级上升。
- 当 `level == max_jam_break_level` 时，接收端发布最高等级 key 后直接进入 INFO 解调。

## 4. 输出: INFO Raw Frame

topic:

```text
/sdr/radar_wireless/raw_frame
```

推荐消息: `sdr_receiver/msg/RadarWirelessFrame`

```text
std_msgs/Header header
uint16 cmd_id
uint8[] payload_raw
bool crc8_ok
bool crc16_ok
uint8 air_chunk_index
string source_target
string team
```

字段语义:

- `cmd_id`: `0x0A01..0x0A06`。INFO 阶段重点是 `0x0A01..0x0A05`。
- `payload_raw`: RM frame 中对应 cmd 的 payload 原始字节，不包含 cmd_id 和 CRC。
- `crc8_ok`、`crc16_ok`: 接收端校验结果。
- `air_chunk_index`: 空口 15 字节分片的局部序号或调试索引；无法稳定确定时可置 0。
- `source_target`: `INFO_UNDER_L1`、`INFO_UNDER_L2`、`INFO_UNDER_L3` 或 `JAM_Lx_KEY`。
- `team`: `RED` 或 `BLUE`。

该 raw topic 是联调保底接口。即使结构化字段还在对齐，雷达工程也能先读取 `cmd_id + payload_raw`。

## 5. 输出: INFO Structured Topics

接收端应继续提供或补齐结构化 topic，供雷达工程直接消费:

```text
/sdr/radar_wireless/position
/sdr/radar_wireless/hp
/sdr/radar_wireless/projectile
/sdr/radar_wireless/gold_occupation
/sdr/radar_wireless/buff
```

这些 topic 分别对应 `0x0A01..0x0A05` 的解析结果。具体字段优先复用现有 `sdr_receiver` 雏形或雷达工程已有消息；若字段暂未对齐，先以 `/sdr/radar_wireless/raw_frame` 完成联调闭环。

## 6. Mock 联调工具

接收端任务需要提供:

- `mock_radar_context_publisher`: 手动或脚本化发布 `/judge/radar_context`。
- `mock_jam_code_subscriber`: 打印 `/sdr/jam_code`，验证 key、level、team、target。
- `mock_raw_frame_subscriber`: 打印 `/sdr/radar_wireless/raw_frame`，验证 `0x0A01..0x0A05` payload。

推荐脚本场景:

1. 发布 `self_id=9`、`radar_info_raw` 对应 L1，确认接收端进入 RED/L1。
2. 接收 `/sdr/jam_code level=1` 后，发布 L2 上下文。
3. 接收 `/sdr/jam_code level=2` 后，发布 L3 上下文。
4. 接收 `/sdr/jam_code level=3` 后，不再发布 L4，确认接收端直接进入 INFO。
5. 验证 `/sdr/radar_wireless/raw_frame` 出现 `0x0A01..0x0A05`。

## 7. 竞争模式异常约定

- 未收到有效 `self_id` 前，competition 模式不进入自动闭环。
- `jam_level == 0` 时，competition 模式记录 warning 并等待下一帧有效 `1..3`。
- 缺失当前 `match_slot/front_end_id/team/target` profile 时，competition 模式应报错并保持等待，不静默加载其他场次或其他前端 profile。
- 是否允许 competition 模式 micro-tune 由硬件联调后决定，默认关闭。
