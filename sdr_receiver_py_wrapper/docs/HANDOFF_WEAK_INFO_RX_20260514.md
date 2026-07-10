# SDR Receiver 弱 INFO 接收 Debug 交接包

日期：2026-05-14

## 下一棒推荐 Skill

推荐使用：`embedded-dev-workflow`

推荐阶段：Stage 4 Review And Debug

原因：

- 当前不是从零实现阶段，而是硬件-软件联合调试阶段。
- 需要持续做“现象 -> 假设 -> 小步代码改造 -> Ubuntu/Pluto 实测 -> 回填结论”的闭环。
- 如果下一轮明确要求继续写代码，可在同一 Skill 下进入 Stage 3 Full Implementation 的局部实现。

备选 Skill：

- `code-review-coach`：仅当用户要求专门 review 当前 wrapper 风险时使用。
- `study-note-generator`：仅当用户要把本轮工作整理成学习笔记时使用。

## 当前工程路径

Windows 开发机：

```text
E:\sdr
E:\sdr\iq_recevier\sdr_receiver
E:\sdr\iq_recevier\sdr_receiver_py_wrapper
```

Ubuntu 测试机：

```text
~/radar_ws
~/radar_ws/src/sdr_receiver
~/radar_ws/src/sdr_receiver_py_wrapper
~/sdr_runtime/venv
```

当前安装包：

```text
E:\sdr\iq_recevier\sdr_receiver_py_wrapper\dist\sdr_receiver_py_wrapper-0.1.0.tar.gz
```

## 约束条件

1. 原 v67 Python 接收脚本零改动。
2. 只能通过 wrapper、import、monkey patch、旁路工具、部署脚本修改行为。
3. ROS2 目标环境：Ubuntu 22.04 + ROS2 Humble + Python 3.10。
4. Pluto/AD936x 接收端硬件增益上限是 `73 dB`，不能把 80 当成真实 RX gain。
5. INFO 官方发射功率/增益很低，当前场地弱 INFO 在协议层非常难进 `AC_RAW`。

## 已实现 wrapper 能力

ROS2 wrapper 包：`sdr_receiver_py_wrapper`

关键模块：

- `original_receiver_adapter.py`
  - 自动寻找原 v67 脚本。
  - 使用 `importlib.util.spec_from_file_location()` 导入。
  - 不触发原脚本 `main()` 自动执行。
  - 支持临时设置 `team/target/gain/rf_bw/freq_offset/filter profile`。

- `patches.py`
  - patch `validate_and_parse` 发布 jam key 和 raw frame。
  - competition 模式禁用 dashboard/keyboard。
  - patch `filter_iq`，支持显式 `_notch` filter 的自适应窄带 notch。
  - 支持显式 `loose10` 弱 INFO filter 时临时放宽普通 INFO AC/header 搜索门限。

- `adaptive_profile_sweep.py`
  - 自动扫 `gain/rf_bw/offset/info_filter`。
  - 依赖原 v67 主循环协议统计：`AC_RAW/AC/SOF/CRC8/CRC16`。
  - 输出 CSV/JSON/best YAML。

- `weak_info_probe.py`
  - 不启动原 v67 主循环，直接抓 SDR IQ。
  - 做 soft access-code 相关、hard AC 最小错误数、带内频谱评分。
  - 用于 `adaptive_profile_sweep` 全部 `NO_LOCK/AC_RAW=0` 时判断候选方向。
  - 输出 `suggested_adaptive_profile_sweep.sh`。

## 最新代码支持的 INFO filter

普通候选：

```text
normal
loose3
loose4
wide_loose3
tight_loose3
```

弱门限候选：

```text
loose10
wide_loose10
tight_loose10
```

带自适应 notch 后缀：

```text
normal_notch
loose3_notch
wide_loose3_notch
tight_loose3_notch
loose10_notch
wide_loose10_notch
tight_loose10_notch
```

注意：`loose10` 族只应作为弱信号验证工具，不能直接默认塞进比赛主线，除非后续 CRC 表现稳定。

## 已知实测事实

1. L1/L2/L3 干扰波都能破，说明 SDR、pyadi、基础 demod、CRC16、0x0A06 路径可用。
2. INFO 发射端最初被怀疑没发射，但示波器频谱确认：INFO 发射端可以正常发射。
3. 官方低功率 INFO 下，接收端协议层很弱。
4. 发射端 INFO gain 设为 `-5` 时，接收端可以解出 SOF，效果明显变好。
5. 发射端 INFO gain 设为 `-60` 官方参数时，`adaptive_profile_sweep` 基本全 `NO_LOCK`。
6. Pluto/AD936x RX gain 最高为 `73 dB`，更高会被锁回 73。

## 最新测试结果

`adaptive_profile_sweep` 普通协议层扫描：

```text
BEST,INFO,gain=70,rf_bw_khz=420,offset_khz=0,class=NO_LOCK,score=-3.89
```

更新后再次测试：

```text
BEST,INFO,gain=73,rf_bw_khz=220,offset_khz=0,class=NO_LOCK,score=-3.92
OUT_DIR,/tmp/adaptive_profile_sweep/20260514_200553
```

结论：原协议层硬 AC 门限仍吃不进弱 INFO，`AC_RAW` 仍为 0。

`weak_info_probe` 第一次：

```text
BEST,gain=70,rf_bw_khz=220,offset_khz=150,filter=loose3,notch=off,score=126.513,soft_sigma=5.77,hard_min_errors=10
OUT_DIR,/tmp/weak_info_probe/20260514_194519
```

`weak_info_probe` 第二次：

```text
BEST,gain=73,rf_bw_khz=420,offset_khz=0,filter=wide_loose3,notch=on,score=129.444,soft_sigma=6.53,hard_min_errors=11
OUT_DIR,/tmp/weak_info_probe/20260514_194818
```

结论：

- IQ 中已经能看到类似 INFO AC 的 soft 相关结构。
- 但 hard AC 错误数约 10-11，比原脚本普通 INFO 可接受门限高很多。
- 下一步应验证 `loose10/wide_loose10_notch` 是否能让协议层至少出现 `AC_RAW/AC/SOF`。

## 已完成验证

Windows 本地：

- `py_compile` 通过。
- `weak_info_probe --help` 正常。
- 合成 AC soft correlation smoke test 通过。
- `wide_loose10_notch` patch smoke 通过：
  - `adaptive_notch=True`
  - `weak_ac_max_errors=10`
  - `weak_header_max_errors=8`
  - restore 后原脚本常量恢复。

未能在 Windows 本地验证：

- Pluto SDR 实机采样。
- ROS2 Humble colcon 完整构建。
- 实际 INFO `-60` 解码效果。

## 下一步建议命令

先更新最新安装包，然后跑弱门限协议层验证：

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

如果出现 `AC_RAW > 0` 但 `CRC8/CRC16` 仍然为 0：

- 缩小到对应候选。
- 提高 dwell 到 12-20 秒。
- 记录 `AC_RAW/AC/HDR_DROP/SOF/CRC8/CRC16/CRC16_FAIL`。
- 下一步实现多帧 soft 合并或 CRC 驱动候选恢复。

如果仍然 `AC_RAW=0`：

- 说明 hard AC 仍吃不进。
- 下一步应把 `weak_info_probe` 的 soft AC peak 直接接入 bit pool，不再依赖硬 AC 门限。

## 可能的下一步实现路线

路线 A：弱门限验证

1. 用 `loose10/wide_loose10_notch` 做协议层长 dwell。
2. 如果出现 `AC_RAW/AC/SOF`，继续调 header 限制和 CRC 恢复。

路线 B：soft AC 直接入池

1. 在 wrapper patch 中新增 soft AC candidate path。
2. 用 soft correlation peak 定位 AC。
3. 从峰后抽取 payload bits，写入原脚本 `BIT_POOLS`。
4. 复用原脚本 `process_pool()` 做 SOF/CRC8/CRC16。
5. 只在显式 `weak_soft` filter 时启用。

路线 C：多帧软合并

1. 缓存多个 soft AC 附近 payload。
2. 对每个 bit 维护 soft confidence。
3. 用 CRC8/CRC16 驱动候选筛选。
4. 对接近成功的帧做小范围 bit flip/list decode。

建议优先级：

```text
A 弱门限验证 -> B soft AC 直接入池 -> C 多帧软合并
```

## 风险提示

- 放宽 AC/header 门限会增加误报风险，必须用 CRC8/CRC16 或多帧一致性压误报。
- `_notch` 是自适应窄带 notch，只适合强窄带杂散明显时使用。
- `loose10` 族不应默认进入比赛，除非弱功率实测下 CRC 稳定。
- 如果 `-60` 下 ADC 中有效信号仍低到 soft peak 不稳定，纯软件无法无限补偿链路预算。

## 2026-05-14 wrapper update: weak_soft

Added explicit normal-INFO filter profiles:

```text
weak_soft
wide_weak_soft
tight_weak_soft
weak_soft_notch
wide_weak_soft_notch
tight_weak_soft_notch
```

These profiles do not modify the v67 source file and are not defaults. They
patch `fast_demod()` in the wrapper. The original hard AC path runs first; only
when it produces no hard `AC_RAW` / CRC activity does the wrapper use a soft AC
correlation peak to append the following 120 payload bits into the original
`BIT_POOLS`. The original `process_pool()` still owns SOF, CRC8, CRC16, and
`validate_and_parse()`.

Suggested next command if `loose10/wide_loose10_notch` still gives `AC_RAW=0`:

```bash
ros2 run sdr_receiver_py_wrapper adaptive_profile_sweep -- \
  --team RED \
  --profiles INFO \
  --gains 70,73 \
  --rf-bws 220000,420000 \
  --offsets-hz 0,150000 \
  --info-filters weak_soft,wide_weak_soft_notch \
  --dwell-sec 12.0
```
