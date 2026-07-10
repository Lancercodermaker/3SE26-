# SDR Receiver Python Wrapper Requirements

日期: 2026-05-13

## 1. 项目目标

当前目标从“将 Python 接收端完整重构为 C++ ROS2 节点”调整为“保留已通过硬件联调验证的 Python v67 核心接收逻辑，在外层增加 ROS2 wrapper 和 monkey patch 适配层”。

核心原则:

1. 不重写 SDR 采样、2GFSK 解调、Access Code/Header 搜索、bit pool 组包、CRC 修复、L2/L3 rescue、profile calibration 等 Python 核心算法。
2. 比赛模式由 ROS2 裁判上下文驱动，不依赖键盘人工切换。
3. Debug 模式尽量保持原 Python 脚本 `receiving_messages_adaptive_filter_v67_l2cal_20260505_l2rescue_80k_g40.py` 的原始交互逻辑。
4. Ubuntu 22.04 上部署、调试、测试要可复现，不能依赖 Windows 专用操作。
5. 与 radar 工程之间继续使用明确 ROS2 topic 合同，radar 侧负责把 key 发给裁判系统。

## 2. 已知输入材料

- 旧需求文档: `E:\sdr\sdr_receiver_requirements_analysis.md`
- 旧接口文档: `E:\sdr\sdr_receiver_ros2_interface_contract.md`
- 新实现思路: `E:\ROS2 C++ 工程集成 Python 核心逻辑 —— 实现思路文档.txt`
- Python 核心脚本: `C:\Users\Fancy\Downloads\receiving_messages_adaptive_filter_v67_l2cal_20260505_l2rescue_80k_g40.py`
- 当前 C++ 接收端包: `E:\sdr\iq_recevier\sdr_receiver`
- Radar 工程: `C:\Users\Fancy\Downloads\3SE_2026_Radar（复件）\3SE_2026_Radar（复件）`

## 3. 功能需求

### 3.1 Python 核心保留

接收端必须继续使用 Python v67 脚本中的核心算法与参数，包括但不限于:

- Pluto/AD936x `adi.Pluto("ip:192.168.2.1")` 接收链路。
- `fast_demod()` 解调路径。
- `validate_and_parse()` 协议解析路径。
- `STATE`、`TUNE_CFG`、`RADAR_PARAMS`、`FILTER_PARAMS` 等核心状态与配置。
- L1/L2/L3 jam key 解析。
- INFO、L2 rescue、L3 rescue、profile DB、calibration/fallback 逻辑。

允许通过 monkey patch 替换或包裹函数，但必须保持原脚本源码完全零改动。wrapper 只能通过 import、patch、外部配置、外部启动脚本适配原脚本，不允许为了集成 ROS2 去拆分或改写原脚本 `main()`。

### 3.2 运行模式

系统必须支持两种模式:

`run_mode=debug`

- 行为尽量等同原 Python 脚本。
- 保留 dashboard。
- 保留键盘输入: r/b、1..8、c/f、[]、+/-、q。
- 可手动切换 team、target、rescue、gain、calibration。
- ROS2 发布输出可开启，用于旁路观察和联调。
- ROS2 输入不应强制覆盖人工调试，除非显式开启 `debug_accept_ros_control=true`。

`run_mode=competition`

- 禁用键盘交互。
- 不进入原 dashboard 的交互控制路径，可保留只读状态输出或 ROS2 status topic。
- 从 `/judge/radar_context` 或兼容 topic 获取 `self_id`、`radar_info_raw`、`jam_level`、`key_mutable`。
- 根据 `self_id` 自动确定 `TUNE_CFG["TEAM"]`。
- 根据比赛状态机自动控制 `TUNE_CFG["TARGET"]`:
  - 收到有效 L1 上下文后接收 L1 `0x0A06`。
  - key 发布成功后等待裁判系统回传 L2。
  - 收到有效 L2 后接收 L2 `0x0A06`。
  - 收到有效 L3 后接收 L3 `0x0A06`。
  - 达到 `max_jam_break_level` 后，发布该等级 key，然后直接切换 INFO。
- competition 模式下 `jam_level == 0` 视为无效上下文，等待下一帧有效 `1..3`。

### 3.3 ROS2 接口

优先沿用旧接口文档中的 topic 语义:

输入:

- `/judge/radar_context`: 推荐，字段等同 `sdr_receiver/msg/RadarContext`。
- `/match_info` + `/judge/radar_info`: 兼容方案。

输出:

- `/sdr/jam_code`: `0x0A06` key。
- `/sdr/radar_wireless/raw_frame`: INFO 或 jam 原始 RM payload。
- `/sdr/radar_wireless/position`
- `/sdr/radar_wireless/hp`
- `/sdr/radar_wireless/projectile`
- `/sdr/radar_wireless/gold_occupation`
- `/sdr/radar_wireless/buff`
- `/sdr/status`: wrapper 与 Python 核心状态快照。

短期实现可以先发布 `/sdr/jam_code`、`/sdr/radar_wireless/raw_frame`、`/sdr/status` 三类关键 topic；结构化 INFO topic 可在 raw_frame 验证稳定后补齐。

### 3.4 Radar 工程边界

Radar 工程需要提供或中转裁判上下文:

- `self_id`: 来自 `0x0201 robot_status`。
- `radar_info_raw`: 来自 `0x020E radar_info`。
- `jam_level = (radar_info_raw >> 3) & 0x03`。
- `key_mutable = ((radar_info_raw >> 5) & 0x01) != 0`。
- `game_progress`、`match_time`、`referee_online` 用于状态观测。

Radar 工程需要订阅 `/sdr/jam_code`，并由 radar_referee 侧负责将 key 组帧发送给裁判系统。SDR wrapper 不直接写裁判串口。

### 3.5 部署与可测试性

目标部署环境:

- Ubuntu 22.04
- ROS2 Humble
- Python 3.10
- pyadi-iio / libiio
- SDR 前端通过 `ip:192.168.2.1` 访问

必须提供:

- `requirements.txt` 或等价安装说明。
- ROS2 Python package 或 launch 文件。
- mock radar context publisher。
- jam code/raw frame/status subscriber。
- 无 SDR 的 dry-run/mock 模式。
- 可在 Windows 上做离线导入检查和核心函数测试，但比赛运行以 Ubuntu 22.04 为准。

## 4. 非功能需求

- 核心算法与 ROS2 wrapper 解耦，原 Python 核心不引入 `rclpy`。
- monkey patch 必须集中管理，记录 patch 目标、原因、启用条件、回退方式。
- 比赛模式必须减少现场人工输入，所有关键参数来自 launch/YAML/ROS2 context。
- 出错时保持当前安全状态，不做大范围自动扫频。
- 日志必须能回答三个问题: 当前目标是什么、为什么切换、最近解出了什么。
- wrapper 崩溃不能污染原脚本文件；调试模式下应尽可能恢复终端状态。

## 5. 已冻结设计决策

1. 原 Python 脚本必须完全零改动。实现方式冻结为 import + monkey patch + wrapper 外壳。
2. Competition 模式默认关闭 micro-tune，但架构必须预留可配置接口。具体是否开启、调参范围和超时策略由硬件联调结果决定。
3. Radar 工程接口优先使用 `/judge/radar_context` 与 `RadarContext.msg`；wrapper 同时保留 `/match_info` + `/judge/radar_info` fallback，避免 radar 侧短期改动阻塞联调。
4. INFO 第一版强制实现 `/sdr/radar_wireless/raw_frame`。`0x0A01..0x0A05` 结构化 topic 放到第二阶段，在 raw payload 与 radar 侧解析对齐后再冻结字段。
5. 比赛部署环境冻结为 Ubuntu 22.04 + ROS2 Humble + Python 3.10。Python venv 使用 `--system-site-packages`，确保能访问系统 ROS2 的 `rclpy`。
