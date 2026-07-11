# 雷达主工程上下文证据话题实现计划

> **面向执行代理：** 必须使用 `subagent-driven-development`（推荐）或 `executing-plans`，严格按任务逐项执行，并使用复选框跟踪进度。

**目标：** 让雷达主工程使用已有字段发布唯一权威话题 `/judge/radar_context`，并使解析端只由该话题驱动比赛状态。

**架构：** 雷达主工程直接复用已有 `sdr_receiver/msg/RadarContext`，不修改 `vision_interface`，也不新增裁判帧序号或底层时间戳。解析端保留 `/match_info` 和 `/judge/radar_info` 订阅用于诊断，但不允许其改变比赛目标。

**技术栈：** ROS 2 Humble、C++17、rclcpp、Python 3、rclpy、pytest、colcon。

---

## 文件结构

- 修改雷达主工程 `src/radar_referee/include/robot_referee/RefereeControl.hpp`：声明权威上下文 publisher。
- 修改雷达主工程 `src/radar_referee/src/RefereeControl.cpp`：创建 publisher，并在现有上下文更新点发布已有字段。
- 修改 `sdr_receiver_py_wrapper/sdr_receiver_py_wrapper/receiver_node.py`：区分控制来源和诊断来源。
- 创建 `sdr_receiver_py_wrapper/sdr_receiver_py_wrapper/context_arbiter.py`：实施来源、ID、赛段和等级稳定性策略。
- 创建 `sdr_receiver_py_wrapper/test/test_context_arbiter.py`：覆盖日志中出现的异常序列。

### 任务 1：用测试锁定上下文仲裁规则

**文件：**
- 创建：`sdr_receiver_py_wrapper/test/test_context_arbiter.py`
- 创建：`sdr_receiver_py_wrapper/sdr_receiver_py_wrapper/context_arbiter.py`

- [ ] **步骤 1：编写失败测试**

```python
from sdr_receiver_py_wrapper.context_arbiter import ContextArbiter, Observation


def obs(level, *, source="/judge/radar_context", self_id=9, progress=4, t=0.0):
    return Observation(source, self_id, 2, 0x20 | (level << 3), level, True, progress, 400, t)


def test_diagnostic_source_cannot_override_authority():
    arbiter = ContextArbiter("/judge/radar_context", stable_count=3, stable_sec=1.0)
    assert arbiter.observe(obs(1, t=0.0)).accepted
    result = arbiter.observe(obs(3, source="/match_info", t=5.0))
    assert not result.accepted
    assert result.reason == "diagnostic_source"
    assert arbiter.accepted_level == 1


def test_invalid_self_id_never_flips_team():
    arbiter = ContextArbiter("/judge/radar_context", stable_count=3, stable_sec=1.0)
    arbiter.observe(obs(1, self_id=9, t=0.0))
    result = arbiter.observe(obs(1, self_id=176, t=1.0))
    assert not result.accepted
    assert arbiter.own_team == "RED"


def test_prematch_l3_is_logged_but_does_not_retune():
    arbiter = ContextArbiter("/judge/radar_context", stable_count=3, stable_sec=1.0)
    arbiter.observe(obs(1, progress=2, t=0.0))
    for t in (1.0, 2.0, 3.0, 4.0):
        result = arbiter.observe(obs(3, progress=2, t=t))
    assert not result.target_changed
    assert arbiter.accepted_level is None
```

- [ ] **步骤 2：确认测试因模块不存在而失败**

运行：`python -m pytest test/test_context_arbiter.py -v`

预期：收集阶段失败，提示 `No module named 'sdr_receiver_py_wrapper.context_arbiter'`。

- [ ] **步骤 3：实现最小数据模型和拒绝规则**

```python
@dataclass(frozen=True)
class Observation:
    source: str
    self_id: int
    self_color: int
    radar_info_raw: int
    jam_level: int
    key_mutable: bool
    game_progress: int
    match_time: int
    received_monotonic: float


@dataclass(frozen=True)
class Decision:
    accepted: bool
    target_changed: bool
    reason: str
    level: int | None
    target: str | None


class ContextArbiter:
    def __init__(self, authority, stable_count=3, stable_sec=1.0):
        self.authority = authority
        self.stable_count = stable_count
        self.stable_sec = stable_sec
        self.own_team = None
        self.accepted_level = None

    def observe(self, value):
        if value.source != self.authority:
            return Decision(False, False, "diagnostic_source", self.accepted_level, None)
        if value.self_id not in (9, 109):
            return Decision(False, False, "invalid_radar_id", self.accepted_level, None)
        self.own_team = "RED" if value.self_id == 9 else "BLUE"
        if value.game_progress != 4:
            return Decision(False, False, "prematch_observation", self.accepted_level, None)
        return Decision(True, False, "initial_context", self.accepted_level, None)
```

- [ ] **步骤 4：运行测试并确认通过**

运行：`python -m pytest test/test_context_arbiter.py -v`

预期：3 项测试全部通过。

- [ ] **步骤 5：提交**

```bash
git add sdr_receiver_py_wrapper/sdr_receiver_py_wrapper/context_arbiter.py sdr_receiver_py_wrapper/test/test_context_arbiter.py
git commit -m "feat: add authoritative radar context arbitration"
```

### 任务 2：实现比赛中等级稳定性窗口和回退

**文件：**
- 修改：`sdr_receiver_py_wrapper/sdr_receiver_py_wrapper/context_arbiter.py`
- 修改：`sdr_receiver_py_wrapper/test/test_context_arbiter.py`

- [ ] **步骤 1：添加失败测试**

```python
def test_level_requires_count_and_duration_then_can_return_lower():
    arbiter = ContextArbiter("/judge/radar_context", stable_count=3, stable_sec=1.0)
    assert not arbiter.observe(obs(1, t=0.0)).target_changed
    assert not arbiter.observe(obs(1, t=0.4)).target_changed
    assert arbiter.observe(obs(1, t=1.1)).target == "L1"
    assert not arbiter.observe(obs(3, t=2.0)).target_changed
    assert not arbiter.observe(obs(3, t=2.4)).target_changed
    assert arbiter.observe(obs(3, t=3.1)).target == "L3"
    arbiter.observe(obs(1, t=4.0))
    arbiter.observe(obs(1, t=4.4))
    assert arbiter.observe(obs(1, t=5.1)).target == "L1"
```

- [ ] **步骤 2：运行单测并确认因缺少 `target` 或窗口逻辑失败**

运行：`python -m pytest test/test_context_arbiter.py::test_level_requires_count_and_duration_then_can_return_lower -v`

预期：FAIL，且失败点位于等级稳定性断言。

- [ ] **步骤 3：实现候选等级计数、起始时间和双向稳定切换**

```python
if value.jam_level not in (1, 2, 3):
    return Decision(False, False, "invalid_level", self.accepted_level, None)
if value.jam_level != self._candidate_level:
    self._candidate_level = value.jam_level
    self._candidate_count = 1
    self._candidate_since = value.received_monotonic
else:
    self._candidate_count += 1
stable = self._candidate_count >= self.stable_count and (
    value.received_monotonic - self._candidate_since >= self.stable_sec
)
if not stable:
    return Decision(False, False, "level_not_stable", self.accepted_level, None)
changed = self.accepted_level != value.jam_level
self.accepted_level = value.jam_level
return Decision(True, changed, "stable_level", self.accepted_level, f"L{self.accepted_level}")
```

- [ ] **步骤 4：运行全部仲裁测试**

运行：`python -m pytest test/test_context_arbiter.py -v`

预期：全部通过。

- [ ] **步骤 5：提交**

```bash
git add sdr_receiver_py_wrapper/sdr_receiver_py_wrapper/context_arbiter.py sdr_receiver_py_wrapper/test/test_context_arbiter.py
git commit -m "feat: debounce official jam level transitions"
```

### 任务 3：雷达主工程发布已有上下文

**文件：**
- 修改：`src/radar_referee/include/robot_referee/RefereeControl.hpp`
- 修改：`src/radar_referee/src/RefereeControl.cpp`

- [ ] **步骤 1：在头文件声明 publisher**

```cpp
rclcpp::Publisher<sdr_receiver::msg::RadarContext>::SharedPtr _radarContextPub;
void publishRadarContext();
```

- [ ] **步骤 2：在构造函数创建 publisher**

```cpp
_radarContextPub = this->create_publisher<sdr_receiver::msg::RadarContext>(
    "/judge/radar_context", rclcpp::QoS(10).reliable());
```

- [ ] **步骤 3：使用已有成员构造原子消息**

```cpp
void RefereeControl::publishRadarContext()
{
    sdr_receiver::msg::RadarContext msg;
    msg.header.stamp = this->get_clock()->now();
    msg.self_id = _self_ID;
    msg.self_color = _self_ID == 9 ? 2 : (_self_ID == 109 ? 0 : -1);
    msg.radar_info_raw = _radar_info_raw;
    msg.jam_level = _jam_level;
    msg.key_mutable = _key_mutable;
    msg.game_progress = _game_progress;
    msg.match_time = _game_progress == 4 ? static_cast<int16_t>(_stage_remain_time) : -200;
    msg.referee_online = _self_ID == 9 || _self_ID == 109;
    _radarContextPub->publish(msg);
}
```

- [ ] **步骤 4：在现有 `radar_info_t` 解析完成后调用发布函数**

在 `_radar_info_raw`、`_jam_level` 和 `_key_mutable` 完成同一帧赋值后调用 `publishRadarContext();`，禁止在赋值前发布。

- [ ] **步骤 5：构建并检查话题**

运行：

```bash
colcon build --packages-select sdr_receiver radar_referee
source install/setup.bash
ros2 topic info /judge/radar_context -v
```

预期：构建成功，话题类型为 `sdr_receiver/msg/RadarContext`，publisher 数量为 1。

- [ ] **步骤 6：提交雷达主工程改动**

```bash
git add src/radar_referee/include/robot_referee/RefereeControl.hpp src/radar_referee/src/RefereeControl.cpp
git commit -m "feat: publish authoritative radar context"
```

### 任务 4：解析端接入仲裁器并记录诊断来源

**文件：**
- 修改：`sdr_receiver_py_wrapper/sdr_receiver_py_wrapper/receiver_node.py`
- 修改：`sdr_receiver_py_wrapper/config/competition_receiver.yaml`
- 测试：`sdr_receiver_py_wrapper/test/test_context_arbiter.py`

- [ ] **步骤 1：增加参数**

```yaml
context_authority_topic: /judge/radar_context
context_stable_count: 3
context_stable_sec: 1.0
lock_team_after_start: true
```

- [ ] **步骤 2：让所有回调先生成 `Observation`，再调用 `ContextArbiter.observe()`**

只有 `decision.accepted and decision.target_changed` 时才调用现有 `_set_receiver_target_or_profile()`。所有结果写入结构化日志，字段包括 `source`、原始值、`accepted`、`reason` 和上下文版本。

- [ ] **步骤 3：运行 Python 回归测试**

运行：`python -m pytest test -v`

预期：现有测试和新增仲裁测试全部通过。

- [ ] **步骤 4：运行 ROS 手工冲突验证**

先向权威话题连续发布 L1，再向 `/match_info` 或 `/judge/radar_info` 发布 L3。预期接收机保持 L1，日志出现 `diagnostic_source`，且不调用切频。

- [ ] **步骤 5：提交**

```bash
git add sdr_receiver_py_wrapper/sdr_receiver_py_wrapper/receiver_node.py sdr_receiver_py_wrapper/config/competition_receiver.yaml sdr_receiver_py_wrapper/test/test_context_arbiter.py
git commit -m "feat: route receiver context through arbiter"
```
