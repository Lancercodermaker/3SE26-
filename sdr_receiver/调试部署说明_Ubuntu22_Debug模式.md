# Ubuntu 22.04 Debug 模式部署与调试说明

本文用于在 Ubuntu 22.04 雷达主机上测试当前 `sdr_receiver` 代码，并迭代改进接收端解调效果。Debug 模式目标是验证真实 SDR 接收链路、观察 AC/CRC/key/INFO 命中情况、调 gain/频点/profile，不用于正式比赛上场。

## 1. Debug 模式目标

Debug 模式重点测试：

- 接收端 SDR 是否能正常取样。
- 发射端发出后，接收端能否在 1-2 秒内 AC 命中、CRC16 成功、解析 key 或 INFO。
- L1/L2/L3/JAM 与 INFO 目标是否能分别解调。
- 哪组 `rx_lo / digital_shift / rf_bw / gain / filter` 最稳定。
- 当前代码和另一份 `sdr_receiver_ros2_package_pluto_channel_fix` 的效果对比。

Debug 模式不要求裁判上下文闭环完整，也不要求雷达工程真的把 key 发给裁判系统。

## 2. 硬件连接

当前硬件假设：

- Windows 电脑连接两块 SDR 发射板。
- Ubuntu 22.04 雷达主机连接一块 SDR 接收板。
- 发射端使用需求分析文档中提到的 `SDR_GUI_v3.0` 发射端工程。
- 接收端 SDR 常用地址为 `ip:192.168.2.1`。

Ubuntu 主机先检查 SDR：

```bash
iio_info -u ip:192.168.2.1
```

如果看不到 `ad9361-phy` 或 `cf-ad9361-lpc`，先不要跑 ROS2，优先检查 USB 网卡、IP、线缆、电源、驱动。

## 3. 安装依赖

```bash
sudo apt update
sudo apt install -y \
  build-essential cmake git \
  python3-colcon-common-extensions \
  libiio-dev libiio-utils
```

确认 ROS2 Humble 环境存在：

```bash
source /opt/ros/humble/setup.bash
ros2 --version
```

## 4. 工作区准备

推荐路径：

```bash
mkdir -p ~/radar_ws/src
cd ~/radar_ws/src
```

把当前版本接收端放到：

```text
~/radar_ws/src/sdr_receiver
```

注意目录名是 `sdr_receiver`，不是 `sdr_receciver`。如果写错目录名，后续命令和 profile 路径会很容易对不上。

复制完成后先检查当前工作区到底发现了哪些包：

```bash
cd ~/radar_ws
find src -maxdepth 3 -name package.xml -print
colcon list
ls ~/radar_ws/src/sdr_receiver/package.xml
```

`colcon list` 里必须能看到：

```text
sdr_receiver    src/sdr_receiver    (ros.ament_cmake)
```

如果看到的包名不是 `sdr_receiver`，例如 `sdr_receiver_cpp`，后面的 `--packages-select` 就要改成实际包名；如果没有看到 `package.xml`，说明当前目录不是 ROS2 包根目录，或者拷贝的不是带 ROS2 wrapper 的版本。

如果还要对比另一份代码：

```text
~/radar_ws/src/sdr_receiver_ros2_package_pluto_channel_fix
```

注意：如果两个包的 ROS2 package name 都叫 `sdr_receiver`，不要同时放在同一个 `src` 下编译，否则会包名冲突。建议二选一测试，或放到两个独立 workspace：

```text
~/radar_ws_current/src/sdr_receiver
~/radar_ws_channel_fix/src/sdr_receiver
```

## 5. 编译当前版本

```bash
source /opt/ros/humble/setup.bash
cd ~/radar_ws
colcon build --packages-select sdr_receiver
source install/setup.bash
```

如果编译失败，保存日志：

```bash
colcon build --packages-select sdr_receiver 2>&1 | tee ~/sdr_build_current.log
```

## 6. 没有 tmux 时怎么跑

如果接收端主机只有普通 Linux terminal，没有 `tmux`，也完全可以调试。原则是：

- 需要键盘控制的 `sdr_receiver_node` 必须在前台 terminal 运行。
- 观察 `/sdr/status`、`/sdr/jam_code`、`/sdr/radar_wireless/raw_frame` 最好用第二个 terminal 或第二个 SSH 连接。
- 如果真的只有一个 terminal，就优先看接收端自带 `terminal_ui`，并把日志重定向到文件。
- 后台运行不适合键盘调试，因为 `r/b/1/2/3/4/+/-/q` 不会稳定传给后台进程。

最推荐的纯 terminal 方式：

```text
Terminal 1: 前台运行 sdr_receiver_node，用键盘调 target/gain/team
Terminal 2: ros2 topic echo /sdr/status 或 /sdr/jam_code
Terminal 3: 可选，ros2 bag record
```

如果你是通过 SSH 连 Ubuntu 主机，可以从本机开多个 SSH 窗口，它们等价于多个 terminal。

如果只能开一个 terminal，就这样运行并保存输出：

```bash
source /opt/ros/humble/setup.bash
cd ~/radar_ws
source install/setup.bash

ros2 run sdr_receiver sdr_receiver_node --ros-args \
  -p run_mode:=debug \
  -p auto_context_control:=false \
  -p use_real_sdr:=true \
  -p fallback_to_mock:=false \
  -p sdr_uri:=ip:192.168.2.1 \
  -p team:=RED \
  -p target:=L1 \
  -p terminal_ui:=true \
  -p publish_debug:=true \
  -p require_profile:=false \
  2>&1 | tee "$LOG_DIR/receiver_debug.log"
```

停止时按：

```text
Ctrl+C
```

## 7. 建立日志目录

```bash
mkdir -p ~/sdr_debug_logs/$(date +%Y%m%d_%H%M%S)
LOG_DIR=$(ls -dt ~/sdr_debug_logs/* | head -1)
echo "$LOG_DIR"
```

记录环境：

```bash
{
  date -Is
  hostname
  ip -br addr
  source /opt/ros/humble/setup.bash
  ros2 --version || true
  iio_info -u ip:192.168.2.1 | head -100 || true
} 2>&1 | tee "$LOG_DIR/baseline.txt"
```

## 8. 不接裁判上下文的 Debug 启动

如果只想手动切换目标、测试解调，建议关闭自动裁判上下文控制：

```bash
source /opt/ros/humble/setup.bash
cd ~/radar_ws
source install/setup.bash

ros2 run sdr_receiver sdr_receiver_node --ros-args \
  -p run_mode:=debug \
  -p auto_context_control:=false \
  -p use_real_sdr:=true \
  -p fallback_to_mock:=false \
  -p sdr_uri:=ip:192.168.2.1 \
  -p team:=RED \
  -p target:=L1 \
  -p terminal_ui:=true \
  -p publish_debug:=true \
  -p require_profile:=false
```

手动键盘控制：

```text
r / b     切红方/蓝方
1         INFO
2         L1
3         L2
4         L3
5         INFO L3 rescue
6         INFO L2 rescue
7         L2 direct preset
8         L3 direct preset
+ / -     调增益
q         退出
```

如果终端不响应键盘，确认它是前台交互终端，不要在普通后台脚本里使用键盘调试。

## 9. 使用 mock 裁判上下文的 Debug 启动

如果想测试裁判上下文状态机，但仍用 debug 模式：

终端 1：

```bash
source /opt/ros/humble/setup.bash
cd ~/radar_ws
source install/setup.bash
ros2 run sdr_receiver mock_radar_context_publisher --ros-args \
  -p self_id:=9 \
  -p start_level:=1 \
  -p max_level:=3
```

终端 2：

```bash
source /opt/ros/humble/setup.bash
cd ~/radar_ws
source install/setup.bash
ros2 run sdr_receiver mock_jam_code_subscriber
```

终端 3：

```bash
source /opt/ros/humble/setup.bash
cd ~/radar_ws
source install/setup.bash
ros2 run sdr_receiver sdr_receiver_node --ros-args \
  -p run_mode:=debug \
  -p auto_context_control:=true \
  -p use_real_sdr:=true \
  -p fallback_to_mock:=false \
  -p require_profile:=false \
  -p terminal_ui:=true
```

## 10. 运行时观察

另开终端：

```bash
source /opt/ros/humble/setup.bash
cd ~/radar_ws
source install/setup.bash
```

查看状态：

```bash
ros2 topic echo /sdr/status
```

查看 key：

```bash
ros2 topic echo /sdr/jam_code
```

查看 INFO/raw frame：

```bash
ros2 topic echo /sdr/radar_wireless/raw_frame
```

查看 topic：

```bash
ros2 topic list | grep sdr
```

录包：

```bash
ros2 bag record \
  /sdr/status \
  /sdr/jam_code \
  /sdr/radar_wireless/raw_frame \
  -o "$LOG_DIR/sdr_debug_bag"
```

如果只有一个 terminal，无法同时前台键盘调试和 `ros2 topic echo`。这时先以前台接收端为主；需要抓取 topic 时，可以短时间后台录包：

```bash
timeout 60 ros2 bag record \
  /sdr/status \
  /sdr/jam_code \
  /sdr/radar_wireless/raw_frame \
  -o "$LOG_DIR/sdr_debug_bag_60s" &
```

然后继续在前台接收端窗口观察 `terminal_ui`。后台录包结束后会自动退出。

## 11. 发射端配合

Windows 发射端建议使用 `SDR_GUI_v3.0`：

```text
E:\sdr\iq_transmitter\SDR_GUI_v3.0
```

建议先分开测：

1. 只开 L1 JAM 发射，接收端 `target:=L1`。
2. 只开 L2 JAM 发射，接收端 `target:=L2`。
3. 只开 L3 JAM 发射，接收端 `target:=L3`。
4. 只开 INFO 发射，接收端 `target:=INFO`。
5. 最后再双发射源并行：一路 JAM，一路 INFO。

每轮记录：

```text
测试时间
发射端模式
发射端 key 或 INFO 场景
接收端 target
team
rx_lo
digital_shift
rf_bw
gain
首次 AC 时间
首次 CRC16 时间
首次 key/raw_frame 时间
60 秒内成功次数
```

## 12. 1-2 秒效果判定

建议用固定变化 payload 判断延迟：

- JAM key 每隔 5 秒变化一次，例如 `L1A001`、`L1A002`。
- INFO 中金币数或某个坐标每隔 5 秒变化一次。

接收端观察：

```bash
ros2 topic echo /sdr/jam_code
ros2 topic echo /sdr/radar_wireless/raw_frame
```

判定标准：

- 发射端变化后，接收端 1-2 秒内出现对应新 key 或新 INFO payload。
- 连续 60 秒内无长时间丢锁。
- 偶发单次失败可以接受，但不能频繁超过 2 秒才恢复。

## 13. 调参顺序

每次只改一个变量，避免不知道是哪项起作用。

推荐顺序：

1. `team` 是否正确。
2. `target` 是否对应发射端。
3. `rx_lo` 或 `digital_shift`。
4. `gain`。
5. `rf_bw`。
6. filter 参数。
7. rescue 模式。

常见现象：

```text
AC 完全没有：
  频点/team/target/access code/发射端模式优先检查。

AC 有但 CRC16 没有：
  频偏、gain、rf_bw、digital_shift、滤波参数优先检查。

CRC16 有但 key 不发布：
  确认当前 target 是 L1/L2/L3，不是 INFO。

INFO raw_frame 有但结构化 topic 不合理：
  检查 payload 字段长度和小端解析。

一会儿锁一会儿丢：
  降低 gain、防止 ADC 饱和，或缩窄/调整 rf_bw。
```

## 14. 将好参数写回 profile

当某组参数稳定后，写回：

```text
~/radar_ws/src/sdr_receiver/config/sdr_profiles/competition_profiles.yaml
```

示例：

```yaml
profile_sets:
  bo3_game1:
    front_end_A:
      RED:
        JAM_L2_KEY:
          rx_lo: 432580000
          digital_shift: 80000
          rf_bw: 660000
          gain: 40
          filter: hist248
```

写回后重新启动节点验证：

```bash
ros2 run sdr_receiver sdr_receiver_node --ros-args \
  -p run_mode:=debug \
  -p auto_context_control:=true \
  -p use_real_sdr:=true \
  -p fallback_to_mock:=false \
  -p require_profile:=true \
  -p profile_path:=src/sdr_receiver/config/sdr_profiles/competition_profiles.yaml \
  -p match_slot:=bo3_game1 \
  -p front_end_id:=front_end_A
```

## 15. 对比另一份接收端代码

如果另一份代码 package name 也叫 `sdr_receiver`，建议独立 workspace：

```bash
mkdir -p ~/radar_ws_channel_fix/src
cp -a /path/to/sdr_receiver_ros2_package_pluto_channel_fix ~/radar_ws_channel_fix/src/sdr_receiver

source /opt/ros/humble/setup.bash
cd ~/radar_ws_channel_fix
colcon build
source install/setup.bash
```

对比时保持发射端场景、SDR、天线距离、增益初始值一致，只换接收端代码。

建议记录成表：

```text
版本 | target | gain | rx_lo | rf_bw | 首次 key/INFO | 60s CRC16 次数 | 备注
当前版本
pluto_channel_fix
```

## 16. 保存本轮调试结果

建议每轮结束保存：

```bash
cp ~/radar_ws/src/sdr_receiver/config/sdr_profiles/competition_profiles.yaml \
  "$LOG_DIR/competition_profiles_after_test.yaml"

ros2 topic echo /sdr/status --once > "$LOG_DIR/final_status.txt" || true
```

手写总结：

```bash
nano "$LOG_DIR/TEST_SUMMARY.md"
```

建议内容：

```text
测试日期:
接收端代码版本:
发射端场景:
硬件连接:
最佳 L1:
最佳 L2:
最佳 L3:
最佳 INFO:
是否达到 1-2 秒:
下一步:
```

## 17. Debug 到 Competition 的切换

Debug 模式调通后，比赛模式只保留必要参数：

```bash
ros2 run sdr_receiver sdr_receiver_node --ros-args \
  -p run_mode:=competition \
  -p use_real_sdr:=true \
  -p fallback_to_mock:=false \
  -p require_profile:=true \
  -p profile_path:=src/sdr_receiver/config/sdr_profiles/competition_profiles.yaml \
  -p match_slot:=bo3_game1 \
  -p front_end_id:=front_end_A \
  -p max_jam_break_level:=3
```

比赛模式不使用键盘控制，不允许 fallback 到 mock SDR。
