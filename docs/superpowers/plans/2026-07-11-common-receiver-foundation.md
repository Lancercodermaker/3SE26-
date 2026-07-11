# 公共接收底座与融合方案实现计划

> **面向执行代理：** 必须使用 `subagent-driven-development`（推荐）或 `executing-plans`，严格按任务逐项执行，并使用复选框跟踪进度。

**目标：** 建立独占 SDR、连续采集、射频安全、双插件分发、统一校验、ROS 输出和可复盘录波的公共接收底座。

**架构：** 将当前 `receiver_node.py` 中的设备、状态和输出职责逐步提取为小型组件，同时保留现有入口和消息兼容性。采集线程只负责读取和入队，解调与磁盘写入通过有界队列解耦。

**技术栈：** Python 3.10、NumPy、pyadi-iio、ROS 2 Humble、rclpy、pytest、JSONL。

---

## 文件结构

- 创建 `models.py`：`IqChunk`、`DecodedCommand`、`RfMetrics` 等不可变模型。
- 创建 `decoder_api.py`：纯计算插件协议。
- 创建 `device_session.py`：唯一 libiio/pyadi 设备所有者。
- 创建 `rf_safety.py`：ADC 标度和射频状态机。
- 创建 `acquisition.py`：连续采集与有界队列。
- 创建 `structured_recorder.py`：异步 IQ、chunk 和事件记录。
- 创建 `command_validator.py`：命令校验与去重。
- 修改 `receiver_node.py`：组合组件并保留现有 ROS 接口。

### 任务 1：建立公共数据模型和插件协议

**文件：**
- 创建：`sdr_receiver_py_wrapper/sdr_receiver_py_wrapper/models.py`
- 创建：`sdr_receiver_py_wrapper/sdr_receiver_py_wrapper/decoder_api.py`
- 创建：`sdr_receiver_py_wrapper/test/test_decoder_api.py`

- [ ] **步骤 1：编写失败测试**

```python
import numpy as np
from sdr_receiver_py_wrapper.models import IqChunk


def test_iq_chunk_rejects_non_complex64():
    try:
        IqChunk(1, 0, np.ones(8), 2_000_000, 1.0, 10, 434_920_000, 940_000, 20, 1, 1)
    except ValueError as exc:
        assert "complex64" in str(exc)
    else:
        raise AssertionError("expected ValueError")
```

- [ ] **步骤 2：运行测试确认失败**

运行：`python -m pytest test/test_decoder_api.py -v`

预期：模块导入失败。

- [ ] **步骤 3：实现冻结模型和 Protocol**

```python
@dataclass(frozen=True)
class IqChunk:
    chunk_id: int
    first_sample_index: int
    samples: np.ndarray
    sample_rate_hz: int
    rx_wall_time: float
    rx_monotonic_ns: int
    lo_hz: int
    rf_bandwidth_hz: int
    rx_gain_db: int
    target_version: int
    context_version: int

    def __post_init__(self):
        if self.samples.dtype != np.complex64:
            raise ValueError("IqChunk samples must be complex64")


class DecoderPlugin(Protocol):
    decoder_id: str
    def decode(self, chunk: IqChunk, context: DecodeContext) -> list[DecodedCommand]: ...
    def reset(self, reason: ResetReason, context: DecodeContext) -> None: ...
    def stats(self) -> DecoderStats: ...
```

- [ ] **步骤 4：运行测试并提交**

运行：`python -m pytest test/test_decoder_api.py -v`，预期 PASS。

```bash
git add sdr_receiver_py_wrapper/sdr_receiver_py_wrapper/models.py sdr_receiver_py_wrapper/sdr_receiver_py_wrapper/decoder_api.py sdr_receiver_py_wrapper/test/test_decoder_api.py
git commit -m "feat: define receiver data and decoder contracts"
```

### 任务 2：建立唯一设备会话

**文件：**
- 创建：`sdr_receiver_py_wrapper/sdr_receiver_py_wrapper/device_session.py`
- 创建：`sdr_receiver_py_wrapper/test/test_device_session.py`

- [ ] **步骤 1：编写配置快照和重连失败测试**

```python
def test_device_session_is_only_owner_of_device_settings():
    backend = FakePlutoBackend()
    session = DeviceSession(lambda: backend)
    session.configure(sample_rate=2_000_000, lo_hz=434_920_000, rf_bandwidth=940_000, gain=20)
    assert session.snapshot() == {
        "sample_rate_hz": 2_000_000,
        "lo_hz": 434_920_000,
        "rf_bandwidth_hz": 940_000,
        "rx_gain_db": 20,
    }


def test_read_error_closes_and_reconnects_backend():
    factory = BackendFactory([FailingBackend(), FakePlutoBackend()])
    session = DeviceSession(factory, reconnect_backoff_sec=0.0)
    with pytest.raises(DeviceReadError):
        session.read()
    assert session.reconnect()
    assert session.stats.reconnects == 1
```

- [ ] **步骤 2：运行测试并确认模块不存在**

运行：`python -m pytest test/test_device_session.py -v`

预期：导入 `device_session` 失败。

- [ ] **步骤 3：实现 `DeviceSession`**

设备对象保存在私有成员 `_backend`；公开方法限制为 `connect()`、`configure()`、`set_gain()`、`read()`、`reconnect()`、`snapshot()` 和 `close()`。所有配置写入都加同一把锁，异常统一转换为 `DeviceConnectionError` 或 `DeviceReadError`。

- [ ] **步骤 4：运行测试并提交**

运行：`python -m pytest test/test_device_session.py -v`，预期全部通过。

```bash
git add sdr_receiver_py_wrapper/sdr_receiver_py_wrapper/device_session.py sdr_receiver_py_wrapper/test/test_device_session.py
git commit -m "feat: centralize Pluto ownership in device session"
```

### 任务 3：实现正确 ADC 标度和射频安全状态

**文件：**
- 创建：`sdr_receiver_py_wrapper/sdr_receiver_py_wrapper/rf_safety.py`
- 创建：`sdr_receiver_py_wrapper/test/test_rf_safety.py`

- [ ] **步骤 1：编写削顶不能被判为弱信号的失败测试**

```python
def test_ad9363_clipping_is_not_rf_low():
    samples = np.full(4096, 2047 + 2047j, dtype=np.complex64)
    metrics = measure_rf(samples, code_scale=2048.0)
    assert metrics.rms > 1.0
    assert metrics.clipping_ratio > 0.99
    assert classify_rf(metrics) == RfState.CLIPPED
```

- [ ] **步骤 2：运行并确认失败**

运行：`python -m pytest test/test_rf_safety.py -v`，预期模块导入失败。

- [ ] **步骤 3：实现 `measure_rf()` 和优先级明确的 `classify_rf()`**

判定顺序固定为：断连、削顶、过强、过弱、线性。削顶阈值使用 I/Q 任一分量绝对值不小于 `0.98 * code_scale` 的样本比例。

- [ ] **步骤 4：增加增益决策测试**

```python
def test_clipping_reduces_gain_and_never_increases_it():
    decision = RfSafetyController(min_gain=0, max_gain=50).decide(RfState.CLIPPED, current_gain=40)
    assert decision.new_gain == 34
    assert decision.reason == "clipping_reduce_gain"
```

- [ ] **步骤 5：运行测试并提交**

运行：`python -m pytest test/test_rf_safety.py -v`，预期全部通过。

```bash
git add sdr_receiver_py_wrapper/sdr_receiver_py_wrapper/rf_safety.py sdr_receiver_py_wrapper/test/test_rf_safety.py
git commit -m "feat: add explicit AD9363 RF safety metrics"
```

### 任务 4：实现连续采集和有界队列

**文件：**
- 创建：`sdr_receiver_py_wrapper/sdr_receiver_py_wrapper/acquisition.py`
- 创建：`sdr_receiver_py_wrapper/test/test_acquisition.py`

- [ ] **步骤 1：用假设备编写队列溢出测试**

```python
def test_acquisition_counts_drop_without_blocking_device():
    device = FakeDevice([np.zeros(16, np.complex64)] * 3)
    engine = AcquisitionEngine(device, queue_size=1)
    engine.read_once()
    engine.read_once()
    engine.read_once()
    assert device.read_count == 3
    assert engine.stats.queue_drops == 2
```

- [ ] **步骤 2：确认失败后实现 `read_once()`**

使用 `queue.put_nowait()`；队列满时增加 `queue_drops`，不得等待消费者。每次读取增加 `first_sample_index`，并使用 `time.time()` 与 `time.monotonic_ns()` 标记 chunk。

- [ ] **步骤 3：增加 libiio 异常计数和重连测试**

假设备第一次抛出 `OSError`、第二次返回 IQ；断言 `read_errors=1`、`reconnects=1`，并成功生成下一 chunk。

- [ ] **步骤 4：运行测试并提交**

运行：`python -m pytest test/test_acquisition.py -v`，预期全部通过。

```bash
git add sdr_receiver_py_wrapper/sdr_receiver_py_wrapper/acquisition.py sdr_receiver_py_wrapper/test/test_acquisition.py
git commit -m "feat: add nonblocking continuous IQ acquisition"
```

### 任务 5：实现异步可复盘录波

**文件：**
- 创建：`sdr_receiver_py_wrapper/sdr_receiver_py_wrapper/structured_recorder.py`
- 创建：`sdr_receiver_py_wrapper/test/test_structured_recorder.py`
- 修改：`sdr_receiver_py_wrapper/sdr_receiver_py_wrapper/receiver_node.py`

- [ ] **步骤 1：编写临时目录录波测试**

```python
def test_recorder_writes_iq_chunk_and_event_sidecars(tmp_path):
    recorder = StructuredRecorder(tmp_path, "case")
    recorder.write_chunk(make_chunk(chunk_id=7, first_sample_index=112))
    recorder.write_event("context_rejected", {"reason": "invalid_radar_id"})
    recorder.close()
    assert (tmp_path / "case.c64").stat().st_size == 16 * 8
    chunk = json.loads((tmp_path / "case.chunks.jsonl").read_text().splitlines()[0])
    assert chunk["chunk_id"] == 7
    assert chunk["first_sample_index"] == 112
```

- [ ] **步骤 2：实现后台写入线程和三个输出文件**

禁止每个 chunk 调用 `flush()`；正常关闭时统一 flush、fsync 并写 `.summary.json`。队列溢出写入事件并增加计数。

- [ ] **步骤 3：将现有 `IqRecorder` 替换为兼容适配调用**

保留现有 launch 参数名称，避免部署脚本同时失效；删除采集回调中的同步写盘路径。

- [ ] **步骤 4：运行测试并提交**

运行：`python -m pytest test/test_structured_recorder.py test/test_iq_file_source.py -v`，预期全部通过。

```bash
git add sdr_receiver_py_wrapper/sdr_receiver_py_wrapper/structured_recorder.py sdr_receiver_py_wrapper/sdr_receiver_py_wrapper/receiver_node.py sdr_receiver_py_wrapper/test/test_structured_recorder.py
git commit -m "feat: record replayable IQ and event metadata"
```

### 任务 6：将现有 v67 封装为纯解调插件

**文件：**
- 创建：`sdr_receiver_py_wrapper/sdr_receiver_py_wrapper/v67_decoder.py`
- 创建：`sdr_receiver_py_wrapper/test/test_v67_decoder.py`
- 修改：`sdr_receiver_py_wrapper/sdr_receiver_py_wrapper/original_receiver_adapter.py`

- [ ] **步骤 1：编写插件边界失败测试**

```python
def test_v67_plugin_does_not_own_device_or_ros():
    decoder = V67Decoder(core=fake_core())
    assert decoder.decoder_id == "improved_v67"
    assert not hasattr(decoder, "sdr")
    assert not hasattr(decoder, "publisher")


def test_v67_event_is_converted_to_decoded_command():
    decoder = V67Decoder(core=fake_core(jam_key=b"ABC123"))
    commands = decoder.decode(make_chunk(), decode_context("BLUE", "L1"))
    assert commands[0].cmd_id == 0x0A06
    assert commands[0].payload == b"ABC123"
```

- [ ] **步骤 2：运行测试并确认模块不存在**

运行：`python -m pytest test/test_v67_decoder.py -v`

预期：导入 `v67_decoder` 失败。

- [ ] **步骤 3：将 `ReceiverCoreAdapter` 的设备控制与纯 IQ 解调入口分开**

新增只接收 `np.ndarray`、profile 和回调的解调方法；`V67Decoder.decode()` 将 `JamKeyEvent`、`RawFrameEvent` 转换为公共 `DecodedCommand`。不得从插件调用 `set_target()`、`set_manual_gain()` 或 ROS publisher。

- [ ] **步骤 4：运行插件及旧适配器回归测试**

运行：`python -m pytest test/test_v67_decoder.py test/test_profile_import.py -v`

预期：全部通过。

- [ ] **步骤 5：提交**

```bash
git add sdr_receiver_py_wrapper/sdr_receiver_py_wrapper/v67_decoder.py sdr_receiver_py_wrapper/sdr_receiver_py_wrapper/original_receiver_adapter.py sdr_receiver_py_wrapper/test/test_v67_decoder.py
git commit -m "refactor: expose v67 receiver as pure decoder plugin"
```

### 任务 7：实现统一命令校验和唯一 ROS 输出

**文件：**
- 创建：`sdr_receiver_py_wrapper/sdr_receiver_py_wrapper/command_validator.py`
- 创建：`sdr_receiver_py_wrapper/test/test_command_validator.py`
- 修改：`sdr_receiver_py_wrapper/sdr_receiver_py_wrapper/receiver_node.py`

- [ ] **步骤 1：编写 `0x0A06` 语义测试**

```python
@pytest.mark.parametrize("payload", [b"ABC12", b"ABC12!", b"1234567"])
def test_validator_rejects_invalid_jam_payload(payload):
    assert not CommandValidator().validate(command(payload)).accepted

def test_validator_accepts_six_alphanumeric_bytes():
    result = CommandValidator().validate(command(b"fcYqTC"))
    assert result.accepted
    assert result.ascii_code == "fcYqTC"
```

- [ ] **步骤 2：实现校验器和去重键**

去重键固定为 `(cmd_id, payload, target_level)`；CRC8、CRC16 必须均为真。拒绝结果保留明确原因，不进入 ROS publisher。

- [ ] **步骤 3：将 `_publish_jam_code()` 置于校验器之后**

插件只能返回 `DecodedCommand`；只有主插件、校验通过且未被去重的结果可以调用现有 publisher。

- [ ] **步骤 4：运行测试并提交**

运行：`python -m pytest test/test_command_validator.py test/test_competition_controller.py -v`，预期全部通过。

```bash
git add sdr_receiver_py_wrapper/sdr_receiver_py_wrapper/command_validator.py sdr_receiver_py_wrapper/sdr_receiver_py_wrapper/receiver_node.py sdr_receiver_py_wrapper/test/test_command_validator.py
git commit -m "feat: validate decoded commands before ROS output"
```

### 任务 8：组合公共底座并完成回归

**文件：**
- 修改：`sdr_receiver_py_wrapper/sdr_receiver_py_wrapper/receiver_node.py`
- 修改：`sdr_receiver_py_wrapper/config/competition_receiver.yaml`
- 修改：`sdr_receiver_py_wrapper/launch/competition_receiver.launch.py`
- 创建：`sdr_receiver_py_wrapper/test/test_receiver_pipeline.py`

- [ ] **步骤 1：使用假设备和假插件编写端到端测试**

输入一个 IQ chunk，让假插件返回 `DecodedCommand(0x0A06, b"ABC123")`，断言校验器接收一次、ROS sink 接收一次、录波 sidecar 包含相同 chunk 和 command 事件。

- [ ] **步骤 2：增加双插件同源影子测试**

```python
def test_primary_and_shadow_receive_identical_chunk_and_context():
    primary, shadow = RecordingDecoder("primary"), RecordingDecoder("shadow")
    pipeline = ReceiverPipeline(primary=primary, shadow=shadow, output=FakeOutput())
    chunk, context = make_chunk(chunk_id=8), decode_context(version=12)
    pipeline.process(chunk, context)
    assert primary.seen == shadow.seen == [(8, 12, hash(chunk.samples.tobytes()))]
    assert pipeline.output.publisher_decoder_id == "primary"
```

- [ ] **步骤 3：增加公共底座参数**

```yaml
decoder_primary: improved_v67
decoder_shadow: ""
acquisition_queue_size: 8
record_queue_size: 32
adc_code_scale: 2048.0
rf_clipping_ratio: 0.001
initial_rx_gain: 20
```

- [ ] **步骤 4：运行全部 Python 测试**

运行：`python -m pytest test -v`

预期：全部通过，无 warning 被当作错误。

- [ ] **步骤 5：运行 ROS 构建和测试**

```bash
colcon build --packages-select sdr_receiver sdr_receiver_py_wrapper
colcon test --packages-select sdr_receiver_py_wrapper
colcon test-result --verbose
```

预期：构建成功，测试失败数为 0。

- [ ] **步骤 6：提交**

```bash
git add sdr_receiver_py_wrapper
git commit -m "feat: assemble common SDR receiver foundation"
```
