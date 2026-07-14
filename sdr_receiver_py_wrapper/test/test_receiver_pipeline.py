from __future__ import annotations

import ast
from dataclasses import replace
import importlib
import json
from pathlib import Path
import sys
import threading
import time
from types import ModuleType, SimpleNamespace

import numpy as np
import pytest
import yaml

from sdr_receiver_py_wrapper.acquisition import AcquisitionEngine
from sdr_receiver_py_wrapper.command_validator import CommandValidator
from sdr_receiver_py_wrapper.device_session import DeviceSession
from sdr_receiver_py_wrapper.models import (
    DecodedCommand,
    DecodeContext,
    DecoderStats,
    IqChunk,
)
from sdr_receiver_py_wrapper.structured_recorder import StructuredRecorder


ROOT = Path(__file__).resolve().parents[1]


class FakeRosMessage:
    def __init__(self):
        self.header = SimpleNamespace(stamp=None)


@pytest.fixture
def receiver_node_module(monkeypatch):
    rclpy = ModuleType("rclpy")
    rclpy_node = ModuleType("rclpy.node")
    rclpy_node.Node = type("Node", (), {})
    rclpy_qos = ModuleType("rclpy.qos")
    rclpy_qos.HistoryPolicy = SimpleNamespace(KEEP_LAST="keep_last")
    rclpy_qos.QoSProfile = type("QoSProfile", (), {})
    rclpy_qos.ReliabilityPolicy = SimpleNamespace(RELIABLE="reliable")
    std_msgs = ModuleType("std_msgs")
    std_msgs_msg = ModuleType("std_msgs.msg")
    std_msgs_msg.String = FakeRosMessage
    std_msgs_msg.UInt8 = FakeRosMessage
    sdr_receiver = ModuleType("sdr_receiver")
    sdr_receiver_msg = ModuleType("sdr_receiver.msg")
    sdr_receiver_msg.JamCode = FakeRosMessage
    sdr_receiver_msg.RadarContext = FakeRosMessage
    sdr_receiver_msg.RadarWirelessFrame = FakeRosMessage
    for name, module in {
        "rclpy": rclpy,
        "rclpy.node": rclpy_node,
        "rclpy.qos": rclpy_qos,
        "std_msgs": std_msgs,
        "std_msgs.msg": std_msgs_msg,
        "sdr_receiver": sdr_receiver,
        "sdr_receiver.msg": sdr_receiver_msg,
    }.items():
        monkeypatch.setitem(sys.modules, name, module)
    monkeypatch.delitem(
        sys.modules,
        "sdr_receiver_py_wrapper.receiver_node",
        raising=False,
    )
    module = importlib.import_module("sdr_receiver_py_wrapper.receiver_node")
    yield module
    sys.modules.pop("sdr_receiver_py_wrapper.receiver_node", None)


def make_context(*, context_version=12):
    return DecodeContext(
        team="BLUE",
        target="JAM_L1_KEY",
        profile="competition",
        target_version=4,
        context_version=context_version,
    )


def make_chunk(*, chunk_id=8, context_version=12):
    samples = np.array([1 + 2j, 3 + 4j, 5 + 6j], dtype=np.complex64)
    samples.setflags(write=False)
    return IqChunk(
        chunk_id=chunk_id,
        first_sample_index=40,
        samples=samples,
        sample_rate_hz=2_000_000,
        rx_wall_time=123.5,
        rx_monotonic_ns=999,
        lo_hz=2_400_000_000,
        rf_bandwidth_hz=1_500_000,
        rx_gain_db=20,
        target_version=4,
        context_version=context_version,
    )


def make_command(decoder_id, chunk, context, *, payload=b"ABC123"):
    return DecodedCommand(
        cmd_id=0x0A06,
        payload=payload,
        decoder_id=decoder_id,
        profile=context.profile,
        crc8_ok=True,
        crc16_ok=True,
        crc_mode="fake_validated",
        first_sample_index=chunk.first_sample_index,
        last_sample_index=chunk.first_sample_index + chunk.samples.size - 1,
        receive_wall_time=chunk.rx_wall_time,
        target=context.target,
        team=context.team,
        context_version=context.context_version,
        evidence={"level": 1, "nested": {"raw": b"evidence"}},
    )


class RecordingDecoder:
    def __init__(self, decoder_id, *, payload=b"ABC123", error=None):
        self.decoder_id = decoder_id
        self.payload = payload
        self.error = error
        self.calls = []

    def decode(self, chunk, context):
        self.calls.append((chunk, context, chunk.samples.tobytes()))
        if self.error is not None:
            raise self.error
        return [make_command(self.decoder_id, chunk, context, payload=self.payload)]

    def reset(self, _reason, _context):
        return None

    def stats(self):
        return DecoderStats(chunks_processed=len(self.calls))


class FakeOutput:
    def __init__(self, publisher_decoder_id="primary", *, fail_once=False):
        self.publisher_decoder_id = publisher_decoder_id
        self.validator = CommandValidator()
        self.validations = []
        self.published = []
        self.fail_once = fail_once

    def publish(self, command, *, before_commit=None):
        result = self.validator.validate(command)
        self.validations.append((command, result))
        if not result.accepted:
            if before_commit is not None:
                before_commit(result)
            return result
        assert self.validator.begin_publish_authorization(command, result)
        try:
            if before_commit is not None:
                before_commit(result)
        except Exception:
            assert self.validator.abort_publish_authorization(command, result)
            raise
        if self.fail_once:
            self.fail_once = False
            assert self.validator.abort_publish_authorization(command, result)
            raise RuntimeError("output failed")
        self.published.append(command)
        assert self.validator.commit_publish_authorization(command, result)
        return result


class MemoryRecorder:
    def __init__(self, *, reject_chunk=False, reject_event=False):
        self.chunks = []
        self.events = []
        self.reject_chunk = reject_chunk
        self.reject_event = reject_event

    def write_chunk(self, chunk, metadata=None):
        self.chunks.append((chunk, metadata))
        return not self.reject_chunk

    def write_event(self, kind, payload):
        self.events.append((kind, payload))
        return not self.reject_event


class ClosableMemoryRecorder(MemoryRecorder):
    def __init__(self):
        super().__init__()
        self.closed_reasons = []

    def close(self, *, stopped_reason="closed"):
        self.closed_reasons.append(stopped_reason)


class RaisingDiagnosticRecorder(MemoryRecorder):
    def __init__(self, failing_kind):
        super().__init__()
        self.failing_kind = failing_kind

    def write_event(self, kind, payload):
        if kind == self.failing_kind:
            raise RuntimeError("diagnostic recorder failed")
        return super().write_event(kind, payload)


class ShadowFailingRecorder(MemoryRecorder):
    def __init__(self, *, fail_kind, raises=False):
        super().__init__()
        self.fail_kind = fail_kind
        self.raises = raises

    def write_event(self, kind, payload):
        is_shadow = payload.get("role") == "shadow"
        if kind == self.fail_kind and is_shadow:
            if self.raises:
                raise RuntimeError("shadow recorder failed")
            return False
        return super().write_event(kind, payload)


class FakeBackend:
    def __init__(self, samples):
        self.samples = samples
        self.closed = False
        self.rx_calls = 0

    def rx(self):
        self.rx_calls += 1
        return self.samples

    def close(self):
        self.closed = True


def radio_settings(*, gain=20):
    return {
        "sample_rate_hz": 2_000_000,
        "lo_hz": 2_400_000_000,
        "rf_bandwidth_hz": 1_500_000,
        "rx_gain_db": gain,
    }


def read_json_lines(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_common_runtime_single_step_uses_real_device_acquisition_and_pipeline(
    receiver_node_module,
):
    backend = FakeBackend(np.array([2048 + 0j, 0 + 0j], dtype=np.complex64))
    contexts = iter(
        [
            make_context(context_version=12),
            replace(make_context(context_version=13), target_version=5),
        ]
    )
    primary = RecordingDecoder("improved_v67")
    shadow = RecordingDecoder("shadow")
    output = FakeOutput("improved_v67")
    recorder = MemoryRecorder()
    config = receiver_node_module.ReceiverFoundationConfig(
        decoder_shadow="shadow",
        acquisition_queue_size=3,
        record_queue_size=5,
        adc_code_scale=2048.0,
        rf_clipping_ratio=0.001,
        initial_rx_gain=20,
    )
    runtime = receiver_node_module.CommonReceiverRuntime(
        backend_factory=lambda: backend,
        config=config,
        primary=primary,
        shadow=shadow,
        output=output,
        recorder=recorder,
        context_provider=lambda: next(contexts),
        radio_settings_provider=radio_settings,
    )

    first = runtime.process_once()
    second = runtime.process_once()
    runtime.close()

    assert first.chunk.chunk_id == 0
    assert first.chunk.first_sample_index == 0
    assert first.chunk.context_version == 12
    assert first.chunk.target_version == 4
    assert second.chunk.chunk_id == 1
    assert second.chunk.first_sample_index == first.chunk.samples.size
    assert second.chunk.context_version == 13
    assert second.chunk.target_version == 5
    assert first.chunk.rf_metrics is not None
    assert first.rf_state.value == "clipped"
    assert runtime.acquisition._queue.maxsize == 3
    assert primary.calls[0][0] is shadow.calls[0][0] is first.chunk
    assert primary.calls[0][1] is shadow.calls[0][1]
    assert len(output.published) == 1
    assert backend.sample_rate == 2_000_000
    assert backend.rx_lo == 2_400_000_000
    assert backend.rx_rf_bandwidth == 1_500_000
    assert backend.rx_hardwaregain_chan0 == 20
    assert backend.closed is True
    rf_event = next(payload for kind, payload in recorder.events if kind == "rf_state")
    assert rf_event["target"] == "JAM_L1_KEY"
    assert rf_event["team"] == "BLUE"
    assert rf_event["profile"] == "competition"
    assert rf_event["adc_code_scale"] == 2048.0
    assert rf_event["rf_clipping_ratio"] == 0.001


def test_common_runtime_worker_stops_and_exposes_failure(receiver_node_module):
    class FailingBackend(FakeBackend):
        def rx(self):
            time.sleep(0.005)
            raise RuntimeError("backend exploded")

    backend = FailingBackend(np.ones(2, dtype=np.complex64))
    runtime = receiver_node_module.CommonReceiverRuntime(
        backend_factory=lambda: backend,
        config=receiver_node_module.ReceiverFoundationConfig(),
        primary=RecordingDecoder("improved_v67"),
        output=FakeOutput("improved_v67"),
        recorder=MemoryRecorder(),
        context_provider=make_context,
        radio_settings_provider=radio_settings,
    )

    runtime.start()
    deadline = time.monotonic() + 1.0
    while runtime.worker_error is None and time.monotonic() < deadline:
        time.sleep(0.005)
    runtime.close()

    assert isinstance(runtime.worker_error, RuntimeError)
    assert "acquisition read failed" in str(runtime.worker_error)
    assert runtime.thread is not None and not runtime.thread.is_alive()
    assert backend.closed is True


def test_common_runtime_acquisition_queue_buffers_while_decoder_is_slow(
    receiver_node_module,
):
    entered = threading.Event()
    release = threading.Event()

    class BlockingDecoder(RecordingDecoder):
        def decode(self, chunk, context):
            self.calls.append((chunk, context, chunk.samples.tobytes()))
            entered.set()
            if not release.wait(timeout=1.0):
                raise RuntimeError("test decoder release timed out")
            return [make_command(self.decoder_id, chunk, context)]

    backend = FakeBackend(np.ones(2, dtype=np.complex64))
    primary = BlockingDecoder("improved_v67")
    runtime = receiver_node_module.CommonReceiverRuntime(
        backend_factory=lambda: backend,
        config=receiver_node_module.ReceiverFoundationConfig(
            acquisition_queue_size=2
        ),
        primary=primary,
        output=FakeOutput("improved_v67"),
        recorder=MemoryRecorder(),
        context_provider=make_context,
        radio_settings_provider=radio_settings,
    )

    runtime.start()
    try:
        assert entered.wait(timeout=1.0)
        deadline = time.monotonic() + 1.0
        while runtime.acquisition._queue.qsize() < 2 and time.monotonic() < deadline:
            time.sleep(0.005)
        assert runtime.acquisition._queue.qsize() == 2
        assert backend.rx_calls >= 3
    finally:
        release.set()
        runtime.close()

    assert runtime.acquisition_thread is not None
    assert runtime.processing_thread is not None
    assert not runtime.acquisition_thread.is_alive()
    assert not runtime.processing_thread.is_alive()


def test_common_runtime_uses_atomic_context_and_radio_snapshot(receiver_node_module):
    backend = FakeBackend(np.ones(2, dtype=np.complex64))
    snapshots = []

    def snapshot_provider():
        snapshots.append("snapshot")
        return make_context(), radio_settings(gain=30)

    def forbidden_provider():
        raise AssertionError("split provider must not be used")

    runtime = receiver_node_module.CommonReceiverRuntime(
        backend_factory=lambda: backend,
        config=receiver_node_module.ReceiverFoundationConfig(),
        primary=RecordingDecoder("improved_v67"),
        output=FakeOutput("improved_v67"),
        recorder=MemoryRecorder(),
        context_provider=forbidden_provider,
        radio_settings_provider=forbidden_provider,
        snapshot_provider=snapshot_provider,
    )

    result = runtime.process_once()
    runtime.close()

    assert snapshots == ["snapshot", "snapshot"]
    assert result.chunk.context_version == 12
    assert backend.rx_hardwaregain_chan0 == 30


def test_common_runtime_invalid_pipeline_does_not_connect_device(receiver_node_module):
    factory_calls = []
    recorder = ClosableMemoryRecorder()

    with pytest.raises(ValueError, match="publisher_decoder_id"):
        receiver_node_module.CommonReceiverRuntime(
            backend_factory=lambda: factory_calls.append("connect") or FakeBackend(
                np.ones(2, dtype=np.complex64)
            ),
            config=receiver_node_module.ReceiverFoundationConfig(),
            primary=RecordingDecoder("improved_v67"),
            output=FakeOutput("wrong_decoder"),
            recorder=recorder,
            context_provider=make_context,
            radio_settings_provider=radio_settings,
        )

    assert factory_calls == []
    assert recorder.closed_reasons == ["common receiver setup failed"]


def test_common_runtime_connection_failure_closes_recorder(receiver_node_module):
    recorder = ClosableMemoryRecorder()

    def fail_connection():
        raise RuntimeError("connect failed")

    with pytest.raises(RuntimeError, match="failed to connect receiver"):
        receiver_node_module.CommonReceiverRuntime(
            backend_factory=fail_connection,
            config=receiver_node_module.ReceiverFoundationConfig(),
            primary=RecordingDecoder("improved_v67"),
            output=FakeOutput("improved_v67"),
            recorder=recorder,
            context_provider=make_context,
            radio_settings_provider=radio_settings,
        )

    assert recorder.closed_reasons == ["common receiver setup failed"]


def test_common_runtime_configuration_failure_closes_device(receiver_node_module):
    backend = FakeBackend(np.ones(2, dtype=np.complex64))

    with pytest.raises(ValueError, match="invalid keys"):
        receiver_node_module.CommonReceiverRuntime(
            backend_factory=lambda: backend,
            config=receiver_node_module.ReceiverFoundationConfig(),
            primary=RecordingDecoder("improved_v67"),
            output=FakeOutput("improved_v67"),
            recorder=MemoryRecorder(),
            context_provider=make_context,
            radio_settings_provider=lambda: {},
        )

    assert backend.closed is True


def test_node_runtime_mode_selects_common_only_for_competition(receiver_node_module):
    calls = []
    callbacks = object()
    common_runtime = SimpleNamespace(start=lambda: calls.append("common.start"))
    adapter = SimpleNamespace(
        apply_patches=lambda **_kwargs: calls.append("legacy.apply"),
        start=lambda: calls.append("legacy.start"),
    )

    competition = object.__new__(receiver_node_module.SdrReceiverPyWrapperNode)
    competition.run_mode = "competition"
    competition.start_receiver = True
    competition.adapter = adapter
    competition.common_runtime = common_runtime
    competition._start_receiver_runtime(callbacks)
    assert calls == ["common.start"]

    calls.clear()
    debug = object.__new__(receiver_node_module.SdrReceiverPyWrapperNode)
    debug.run_mode = "debug"
    debug.start_receiver = True
    debug.adapter = adapter
    debug.common_runtime = None
    debug._start_receiver_runtime(callbacks)
    assert calls == ["legacy.apply", "legacy.start"]


def test_node_configures_common_runtime_only_when_competition_is_started(
    receiver_node_module,
):
    calls = []
    runtime = SimpleNamespace(start=lambda: calls.append("start"))
    node = object.__new__(receiver_node_module.SdrReceiverPyWrapperNode)
    node.run_mode = "competition"
    node.start_receiver = False
    node.common_runtime = None
    node._build_common_runtime = lambda: calls.append("build") or runtime

    node._configure_receiver_runtime(object())
    assert calls == []
    assert node.common_runtime is None

    node.start_receiver = True
    node._configure_receiver_runtime(object())
    assert calls == ["build", "start"]
    assert node.common_runtime is runtime


def test_node_backend_factory_selects_hardware_without_connecting_early(
    receiver_node_module,
):
    backend = FakeBackend(np.ones(2, dtype=np.complex64))
    factory_calls = []
    node = object.__new__(receiver_node_module.SdrReceiverPyWrapperNode)
    node.iq_source_path = ""
    node.adapter = SimpleNamespace(
        module=SimpleNamespace(
            adi=SimpleNamespace(
                Pluto=lambda: factory_calls.append("Pluto") or backend
            )
        ),
        get_core_config_snapshot=lambda: {"rx_buffer_size": 64},
    )

    factory = node._common_backend_factory()
    assert factory_calls == []
    assert factory() is backend
    assert factory_calls == ["Pluto"]
    assert backend.rx_buffer_size == 64


def test_node_backend_factory_selects_iq_file_source(
    receiver_node_module,
    tmp_path,
):
    path = tmp_path / "source.c64"
    np.array([1 + 2j, 3 + 4j], dtype="<c8").tofile(path)
    values = {
        "iq_source_loop": False,
        "iq_source_throttle": False,
        "iq_source_center_hz": 2_400_000_000.0,
        "iq_source_start_offset_sec": 0.0,
    }
    node = object.__new__(receiver_node_module.SdrReceiverPyWrapperNode)
    node.iq_source_path = str(path)
    node.get_parameter = lambda name: SimpleNamespace(value=values[name])
    node.adapter = SimpleNamespace(
        get_core_config_snapshot=lambda: {"rx_buffer_size": 2},
    )
    node._log_from_patch = lambda _message: None

    backend = node._common_backend_factory()()
    try:
        assert backend.path == path
        assert backend.loop is False
        assert backend.throttle is False
        assert backend.rx_buffer_size == 2
    finally:
        backend.close()


def test_node_common_context_tracks_authoritative_versions(receiver_node_module):
    snapshots = iter(
        [
            {"team": "BLUE", "target": "INFO"},
            {"team": "BLUE", "target": "INFO"},
            {"team": "BLUE", "target": "JAM_L1_KEY"},
        ]
    )
    node = object.__new__(receiver_node_module.SdrReceiverPyWrapperNode)
    node._controller_lock = threading.RLock()
    node.adapter = SimpleNamespace(
        get_current_radio_snapshot=lambda: next(snapshots)
    )
    node.context_arbiter = SimpleNamespace(context_version=12)
    node.run_mode = "competition"
    node._common_target_key = None
    node._common_target_version = 0

    first = node._common_decode_context()
    second = node._common_decode_context()
    node.context_arbiter.context_version = 13
    third = node._common_decode_context()

    assert first.target_version == second.target_version == 1
    assert third.target_version == 2
    assert first.context_version == second.context_version == 12
    assert third.context_version == 13
    assert third.team == "BLUE"
    assert third.target == "JAM_L1_KEY"
    assert third.profile == "competition"


def test_node_status_exposes_common_worker_failure(receiver_node_module):
    node = object.__new__(receiver_node_module.SdrReceiverPyWrapperNode)
    node.common_runtime = SimpleNamespace(
        status=lambda: {
            "running": False,
            "worker_error": "decode exploded",
            "rf_state": "clipped",
        }
    )

    assert node._common_runtime_status() == {
        "running": False,
        "worker_error": "decode exploded",
        "rf_state": "clipped",
    }
    node.common_runtime = None
    assert node._common_runtime_status() == {"enabled": False}


@pytest.mark.parametrize("decoder_id", ["upstream", "shadow_v2", ""])
def test_runtime_decoder_registry_rejects_unavailable_plugins(
    receiver_node_module,
    decoder_id,
):
    with pytest.raises(ValueError, match="unavailable decoder"):
        receiver_node_module._create_decoder_plugin(decoder_id, object())


def test_node_rejects_unknown_shadow_before_loading_shadow_core(
    receiver_node_module,
    monkeypatch,
):
    shadow_adapter_calls = []
    monkeypatch.setattr(
        receiver_node_module,
        "ReceiverCoreAdapter",
        lambda *_args, **_kwargs: shadow_adapter_calls.append("load shadow"),
    )
    node = object.__new__(receiver_node_module.SdrReceiverPyWrapperNode)
    node.foundation_config = receiver_node_module.ReceiverFoundationConfig(
        decoder_shadow="unavailable_shadow"
    )
    node.adapter = object()

    with pytest.raises(ValueError, match="unavailable decoder"):
        node._build_common_runtime()

    assert shadow_adapter_calls == []


def test_device_acquisition_pipeline_records_and_publishes_once(
    receiver_node_module,
    tmp_path,
):
    backend_samples = np.array([10 + 20j, 30 + 40j], dtype=np.complex64)
    session = DeviceSession(lambda: FakeBackend(backend_samples))
    session.configure(
        sample_rate=2_000_000,
        lo_hz=2_400_000_000,
        rf_bandwidth=1_500_000,
        gain=20,
    )
    acquisition = AcquisitionEngine(
        session,
        queue_size=8,
        target_version=4,
        context_version=12,
    )
    chunk = acquisition.read_once()
    assert acquisition.get_nowait() is chunk
    recorder = StructuredRecorder(tmp_path, "pipeline", queue_size=32)
    primary = RecordingDecoder("primary")
    output = FakeOutput()
    pipeline = receiver_node_module.ReceiverPipeline(
        primary=primary,
        output=output,
        recorder=recorder,
    )

    result = pipeline.process(chunk, make_context())
    recorder.close(stopped_reason="test complete")

    assert result.primary_commands == tuple(command for command, _ in output.validations)
    assert len(output.validations) == 1
    assert len(output.published) == 1
    np.testing.assert_array_equal(np.fromfile(recorder.iq_path, dtype="<c8"), chunk.samples)
    chunk_sidecar = read_json_lines(recorder.chunks_path)[0]
    assert chunk_sidecar["chunk_id"] == chunk.chunk_id
    assert chunk_sidecar["first_sample_index"] == chunk.first_sample_index
    assert chunk_sidecar["sample_count"] == chunk.samples.size
    assert chunk_sidecar["context_version"] == chunk.context_version
    assert chunk_sidecar["metadata"]["team"] == "BLUE"
    assert chunk_sidecar["metadata"]["profile"] == "competition"
    assert chunk_sidecar["metadata"]["decoder_primary"] == "primary"
    assert chunk_sidecar["metadata"]["decoder_shadow"] == ""
    events = read_json_lines(recorder.events_path)
    command_event = next(
        item for item in events if item["kind"] == "command"
    )["payload"]
    assert command_event == {
        "cmd_id": 0x0A06,
        "chunk_first_sample_index": chunk.first_sample_index,
        "chunk_id": chunk.chunk_id,
        "chunk_last_sample_index": chunk.first_sample_index + chunk.samples.size - 1,
        "context_version": 12,
        "crc16_ok": True,
        "crc8_ok": True,
        "crc_mode": "fake_validated",
        "decoder_id": "primary",
        "evidence": {"level": 1, "nested": {"raw": "65766964656e6365"}},
        "first_sample_index": chunk.first_sample_index,
        "last_sample_index": chunk.first_sample_index + chunk.samples.size - 1,
        "payload": b"ABC123".hex(),
        "profile": "competition",
        "receive_wall_time": chunk.rx_wall_time,
        "role": "primary",
        "target": "JAM_L1_KEY",
        "target_version": 4,
        "team": "BLUE",
    }
    validation_event = next(
        item for item in events if item["kind"] == "validation"
    )["payload"]
    assert validation_event == {
        "accepted": True,
        "ascii_code": "ABC123",
        "chunk_first_sample_index": chunk.first_sample_index,
        "chunk_id": chunk.chunk_id,
        "chunk_last_sample_index": chunk.first_sample_index + chunk.samples.size - 1,
        "cmd_id": 0x0A06,
        "command_first_sample_index": chunk.first_sample_index,
        "command_last_sample_index": chunk.first_sample_index + chunk.samples.size - 1,
        "context_version": 12,
        "decoder_id": "primary",
        "level": 1,
        "payload": b"ABC123".hex(),
        "profile": "competition",
        "reason": "accepted",
        "target": "JAM_L1_KEY",
        "target_version": 4,
        "team": "BLUE",
    }


def test_primary_and_shadow_receive_same_objects_but_only_primary_outputs(
    receiver_node_module,
):
    chunk = make_chunk()
    context = make_context()
    primary = RecordingDecoder("primary")
    shadow = RecordingDecoder("shadow", payload=b"SHD123")
    output = FakeOutput()
    recorder = MemoryRecorder()
    pipeline = receiver_node_module.ReceiverPipeline(
        primary=primary,
        shadow=shadow,
        output=output,
        recorder=recorder,
    )

    result = pipeline.process(chunk, context)

    assert primary.calls[0][0] is chunk
    assert shadow.calls[0][0] is chunk
    assert primary.calls[0][1] is context
    assert shadow.calls[0][1] is context
    assert primary.calls[0][2] == shadow.calls[0][2] == chunk.samples.tobytes()
    assert pipeline.output.publisher_decoder_id == "primary"
    assert [item.payload for item in output.published] == [b"ABC123"]
    assert [item.payload for item in result.shadow_commands] == [b"SHD123"]
    assert [payload["role"] for kind, payload in recorder.events if kind == "command"] == [
        "primary",
        "shadow",
    ]


def test_invalid_and_duplicate_primary_are_recorded_but_not_republished(
    receiver_node_module,
):
    output = FakeOutput()
    recorder = MemoryRecorder()
    pipeline = receiver_node_module.ReceiverPipeline(
        primary=RecordingDecoder("primary"),
        output=output,
        recorder=recorder,
    )

    pipeline.process(make_chunk(chunk_id=1), make_context())
    pipeline.process(make_chunk(chunk_id=2), make_context())
    pipeline.primary = RecordingDecoder("primary", payload=b"BAD!!!")
    pipeline.process(make_chunk(chunk_id=3), make_context())

    assert len(output.published) == 1
    reasons = [
        payload["reason"]
        for kind, payload in recorder.events
        if kind == "validation"
    ]
    assert reasons[0] == "accepted"
    assert "duplicate command" in reasons[1]
    assert "ASCII letters or digits" in reasons[2]


def test_recorder_chunk_rejection_stops_before_decode_or_output(receiver_node_module):
    primary = RecordingDecoder("primary")
    output = FakeOutput()
    pipeline = receiver_node_module.ReceiverPipeline(
        primary=primary,
        output=output,
        recorder=MemoryRecorder(reject_chunk=True),
    )

    with pytest.raises(receiver_node_module.ReceiverPipelineError, match="record IQ chunk"):
        pipeline.process(make_chunk(), make_context())

    assert primary.calls == []
    assert output.validations == []


def test_primary_exception_propagates_after_diagnostic_event(receiver_node_module):
    recorder = MemoryRecorder()
    shadow = RecordingDecoder("shadow")
    pipeline = receiver_node_module.ReceiverPipeline(
        primary=RecordingDecoder("primary", error=RuntimeError("primary failed")),
        shadow=shadow,
        output=FakeOutput(),
        recorder=recorder,
    )

    with pytest.raises(RuntimeError, match="primary failed"):
        pipeline.process(make_chunk(), make_context())

    primary_error_event = next(
        payload
        for kind, payload in recorder.events
        if kind == "decoder_error" and payload["role"] == "primary"
    )
    assert primary_error_event["error"] == "primary failed"
    assert len(shadow.calls) == 1


def test_shadow_exception_is_recorded_and_isolated(receiver_node_module):
    output = FakeOutput()
    recorder = MemoryRecorder()
    pipeline = receiver_node_module.ReceiverPipeline(
        primary=RecordingDecoder("primary"),
        shadow=RecordingDecoder("shadow", error=RuntimeError("shadow failed")),
        output=output,
        recorder=recorder,
    )

    result = pipeline.process(make_chunk(), make_context())

    assert len(output.published) == 1
    assert result.shadow_commands == ()
    assert result.shadow_error == "shadow failed"
    assert ("decoder_error", {
        "chunk_id": 8,
        "decoder_id": "shadow",
        "error": "shadow failed",
        "role": "shadow",
    }) in recorder.events


def test_output_exception_propagates_records_error_and_leaks_no_reservation(
    receiver_node_module,
):
    output = FakeOutput(fail_once=True)
    recorder = MemoryRecorder()
    shadow = RecordingDecoder("shadow")
    pipeline = receiver_node_module.ReceiverPipeline(
        primary=RecordingDecoder("primary"),
        shadow=shadow,
        output=output,
        recorder=recorder,
    )

    with pytest.raises(RuntimeError, match="output failed"):
        pipeline.process(make_chunk(), make_context())

    assert recorder.events[-1][0] == "output_error"
    assert len(shadow.calls) == 1
    retry = output.publish(make_command("primary", make_chunk(), make_context()))
    assert retry.accepted is True


def test_command_event_rejection_stops_before_production_output(receiver_node_module):
    output = FakeOutput()
    pipeline = receiver_node_module.ReceiverPipeline(
        primary=RecordingDecoder("primary"),
        output=output,
        recorder=MemoryRecorder(reject_event=True),
    )

    with pytest.raises(receiver_node_module.ReceiverPipelineError, match="command event"):
        pipeline.process(make_chunk(), make_context())

    assert output.validations == []
    assert output.published == []


def test_validation_event_failure_aborts_before_production_output(receiver_node_module):
    output = FakeOutput()
    pipeline = receiver_node_module.ReceiverPipeline(
        primary=RecordingDecoder("primary"),
        output=output,
        recorder=RaisingDiagnosticRecorder("validation"),
    )

    with pytest.raises(RuntimeError, match="diagnostic recorder failed"):
        pipeline.process(make_chunk(), make_context())

    assert output.published == []
    retry = output.publish(make_command("primary", make_chunk(), make_context()))
    assert retry.accepted is True


@pytest.mark.parametrize("stage", ["primary", "output"])
def test_diagnostic_recorder_failure_does_not_mask_root_exception(
    receiver_node_module,
    stage,
):
    primary_error = RuntimeError("primary failed") if stage == "primary" else None
    output = FakeOutput(fail_once=stage == "output")
    pipeline = receiver_node_module.ReceiverPipeline(
        primary=RecordingDecoder("primary", error=primary_error),
        output=output,
        recorder=RaisingDiagnosticRecorder(
            "decoder_error" if stage == "primary" else "output_error"
        ),
    )

    expected = "primary failed" if stage == "primary" else "output failed"
    with pytest.raises(RuntimeError, match=expected):
        pipeline.process(make_chunk(), make_context())


def test_shadow_recording_failure_does_not_mask_primary_exception(
    receiver_node_module,
):
    pipeline = receiver_node_module.ReceiverPipeline(
        primary=RecordingDecoder("primary", error=RuntimeError("primary failed")),
        shadow=RecordingDecoder("shadow"),
        output=FakeOutput(),
        recorder=MemoryRecorder(reject_event=True),
    )

    with pytest.raises(RuntimeError, match="primary failed"):
        pipeline.process(make_chunk(), make_context())


@pytest.mark.parametrize("raises", [False, True])
def test_shadow_command_recording_failure_is_observable_but_primary_outputs(
    receiver_node_module,
    raises,
):
    output = FakeOutput()
    recorder = ShadowFailingRecorder(fail_kind="command", raises=raises)
    pipeline = receiver_node_module.ReceiverPipeline(
        primary=RecordingDecoder("primary"),
        shadow=RecordingDecoder("shadow", payload=b"SHD123"),
        output=output,
        recorder=recorder,
    )

    result = pipeline.process(make_chunk(), make_context())

    assert len(output.published) == 1
    assert result.shadow_error is None
    assert len(result.diagnostic_errors) == 1
    assert result.diagnostic_errors[0].stage == "shadow_command_recording"
    assert "shadow" in result.diagnostic_errors[0].reason


def test_shadow_decoder_diagnostic_drop_is_exposed(receiver_node_module):
    output = FakeOutput()
    recorder = ShadowFailingRecorder(fail_kind="decoder_error")
    pipeline = receiver_node_module.ReceiverPipeline(
        primary=RecordingDecoder("primary"),
        shadow=RecordingDecoder("shadow", error=RuntimeError("shadow failed")),
        output=output,
        recorder=recorder,
    )

    result = pipeline.process(make_chunk(), make_context())

    assert len(output.published) == 1
    assert result.shadow_error == "shadow failed"
    assert len(result.diagnostic_errors) == 1
    assert result.diagnostic_errors[0].stage == "shadow_decoder_diagnostic"


@pytest.mark.parametrize(
    ("chunk", "context", "message"),
    [
        (make_chunk(context_version=11), make_context(context_version=12), "context_version"),
        (make_chunk(), replace(make_context(), target_version=5), "target_version"),
    ],
)
def test_pipeline_rejects_mismatched_chunk_context(
    receiver_node_module,
    chunk,
    context,
    message,
):
    pipeline = receiver_node_module.ReceiverPipeline(
        primary=RecordingDecoder("primary"),
        output=FakeOutput(),
    )
    with pytest.raises(ValueError, match=message):
        pipeline.process(chunk, context)


@pytest.mark.parametrize(
    "change",
    [
        {"context_version": 99},
        {"first_sample_index": 39},
        {"first_sample_index": 43, "last_sample_index": 43},
        {"last_sample_index": 43},
        {"target": "JAM_L2_KEY"},
        {"team": "RED"},
        {"profile": "wrong_profile"},
    ],
)
def test_pipeline_rejects_uncorrelated_primary_command(
    receiver_node_module,
    change,
):
    class UncorrelatedDecoder(RecordingDecoder):
        def decode(self, chunk, context):
            return [replace(make_command(self.decoder_id, chunk, context), **change)]

    output = FakeOutput()
    pipeline = receiver_node_module.ReceiverPipeline(
        primary=UncorrelatedDecoder("primary"),
        output=output,
    )

    with pytest.raises((ValueError, RuntimeError), match="command"):
        pipeline.process(make_chunk(), make_context())

    assert output.validations == []


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"primary": object(), "output": FakeOutput()}, "primary decoder"),
        ({"primary": RecordingDecoder("primary"), "shadow": object(), "output": FakeOutput()}, "shadow decoder"),
        ({"primary": RecordingDecoder("primary"), "output": object()}, "output"),
        ({"primary": RecordingDecoder("primary"), "output": FakeOutput(), "recorder": object()}, "recorder"),
    ],
)
def test_pipeline_rejects_incompatible_collaborators(receiver_node_module, kwargs, message):
    with pytest.raises(TypeError, match=message):
        receiver_node_module.ReceiverPipeline(**kwargs)


def test_pipeline_rejects_wrong_input_contracts(receiver_node_module):
    pipeline = receiver_node_module.ReceiverPipeline(
        primary=RecordingDecoder("primary"),
        output=FakeOutput(),
    )
    with pytest.raises(TypeError, match="IqChunk"):
        pipeline.process(object(), make_context())
    with pytest.raises(TypeError, match="DecodeContext"):
        pipeline.process(make_chunk(), object())


EXPECTED_DEFAULTS = {
    "decoder_primary": "improved_v67",
    "decoder_shadow": "",
    "acquisition_queue_size": 8,
    "record_queue_size": 32,
    "adc_code_scale": 2048.0,
    "rf_clipping_ratio": 0.001,
    "initial_rx_gain": 20,
}


def _node_declared_defaults():
    tree = ast.parse((ROOT / "sdr_receiver_py_wrapper" / "receiver_node.py").read_text())
    defaults = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        if (
            node.func.attr == "declare_parameter"
            and len(node.args) >= 2
            and isinstance(node.args[0], ast.Constant)
            and isinstance(node.args[1], ast.Constant)
        ):
            defaults[ast.literal_eval(node.args[0])] = ast.literal_eval(node.args[1])
    return defaults


def _launch_defaults_and_forwarding():
    tree = ast.parse((ROOT / "launch" / "competition_receiver.launch.py").read_text())
    defaults = {}
    forwarded = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = getattr(node.func, "id", None)
        if name == "DeclareLaunchArgument" and node.args:
            key = ast.literal_eval(node.args[0])
            keyword = next(item for item in node.keywords if item.arg == "default_value")
            if isinstance(keyword.value, ast.Constant):
                defaults[key] = keyword.value.value
        if name == "LaunchConfiguration" and node.args and isinstance(node.args[0], ast.Constant):
            forwarded.add(node.args[0].value)
    return defaults, forwarded


def test_common_defaults_match_node_yaml_and_launch_and_are_forwarded():
    assert {key: _node_declared_defaults()[key] for key in EXPECTED_DEFAULTS} == EXPECTED_DEFAULTS
    yaml_values = yaml.safe_load((ROOT / "config" / "competition_receiver.yaml").read_text())[
        "sdr_receiver_py_wrapper_competition"
    ]["ros__parameters"]
    assert {key: yaml_values[key] for key in EXPECTED_DEFAULTS} == EXPECTED_DEFAULTS
    launch_defaults, forwarded = _launch_defaults_and_forwarding()
    assert {
        key: type(default)(launch_defaults[key]) for key, default in EXPECTED_DEFAULTS.items()
    } == EXPECTED_DEFAULTS
    assert EXPECTED_DEFAULTS.keys() <= forwarded


def test_node_reads_common_parameters_into_live_foundation_config(
    receiver_node_module,
    tmp_path,
):
    values = dict(EXPECTED_DEFAULTS)
    values.update(
        decoder_primary="primary",
        decoder_shadow="shadow",
        acquisition_queue_size=3,
        record_queue_size=5,
        adc_code_scale=4096.0,
        rf_clipping_ratio=0.02,
        initial_rx_gain=30,
    )
    node = object.__new__(receiver_node_module.SdrReceiverPyWrapperNode)
    node.get_parameter = lambda name: SimpleNamespace(value=values[name])

    config = node._load_receiver_foundation_config()

    assert config.decoder_primary == "primary"
    assert config.decoder_shadow == "shadow"
    acquisition = config.create_acquisition(object())
    assert acquisition._queue.maxsize == 3
    recorder = config.create_recorder(tmp_path, "configured")
    try:
        assert recorder._queue.maxsize == 5
    finally:
        recorder.close()
    metrics, state = config.measure_and_classify(
        np.array([4096 + 0j], dtype=np.complex64)
    )
    assert metrics.peak == 1.0
    assert state.value == "clipped"
    node.foundation_config = config
    node.primary_decoder_id = "primary"
    node._handle_decoded_command = lambda command: FakeOutput("primary").publish(command)
    pipeline = node.create_receiver_pipeline(
        primary=RecordingDecoder("primary"),
        shadow=RecordingDecoder("shadow"),
        recorder=MemoryRecorder(),
    )
    assert pipeline.config is config
    assert pipeline.primary.decoder_id == config.decoder_primary
    assert pipeline.shadow.decoder_id == config.decoder_shadow


@pytest.mark.parametrize(
    "changes",
    [
        {"decoder_primary": ""},
        {"decoder_shadow": 3},
        {"acquisition_queue_size": 0},
        {"acquisition_queue_size": True},
        {"record_queue_size": -1},
        {"adc_code_scale": 0.0},
        {"adc_code_scale": float("inf")},
        {"rf_clipping_ratio": 0.0},
        {"rf_clipping_ratio": 1.1},
        {"initial_rx_gain": -2},
        {"initial_rx_gain": 74},
        {"initial_rx_gain": 20.5},
    ],
)
def test_foundation_config_rejects_invalid_values(receiver_node_module, changes):
    values = dict(EXPECTED_DEFAULTS)
    values.update(changes)
    with pytest.raises((TypeError, ValueError)):
        receiver_node_module.ReceiverFoundationConfig(**values)


def test_node_output_adapter_reuses_competition_controller_gate(receiver_node_module):
    calls = []
    audit = []

    def competition_gate(command, **kwargs):
        calls.append(command)
        kwargs["before_publish"]("validated")
        return "accepted"

    node = SimpleNamespace(
        primary_decoder_id="primary",
        _handle_competition_decoded_command=competition_gate,
        _handle_decoded_command=lambda *_args, **_kwargs: pytest.fail(
            "common output bypassed CompetitionController"
        ),
    )
    output = receiver_node_module.NodeCommandOutput(node)
    command = make_command("primary", make_chunk(), make_context())

    assert output.publisher_decoder_id == "primary"
    assert output.publish(command, before_commit=audit.append) == "accepted"
    assert calls == [command]
    assert audit == ["validated"]


def test_common_controller_gate_rolls_back_before_publish_audit_failure(
    receiver_node_module,
):
    class ControllerOnlyValidator(CommandValidator):
        def validate(self, _command):
            pytest.fail("common competition path used standalone validation")

    node = object.__new__(receiver_node_module.SdrReceiverPyWrapperNode)
    node.primary_decoder_id = "improved_v67"
    node.command_validator = ControllerOnlyValidator()
    node.controller = receiver_node_module.CompetitionController(
        key_publish_min_interval_sec=0.0
    )
    node.controller.update_context(
        receiver_node_module.RadarContext(
            self_id=9,
            self_color=2,
            radar_info_raw=0,
            jam_level=1,
            key_mutable=True,
        )
    )
    node._controller_lock = threading.RLock()
    node._pending_rf_transition = None
    node.publish_ros_outputs = True
    node.get_logger = lambda: SimpleNamespace(
        debug=lambda _message: None,
        warn=lambda _message: None,
    )
    node._retry_pending_rf_transition_locked = lambda: True
    node._set_receiver_target_or_profile = lambda *_args, **_kwargs: None
    published = []

    def publish_validated(command, result):
        assert node.command_validator.begin_publish_authorization(command, result)
        published.append(command)
        assert node.command_validator.commit_publish_authorization(command, result)

    node._publish_validated_jam_code = publish_validated
    output = receiver_node_module.NodeCommandOutput(node)
    command = make_command("improved_v67", make_chunk(), make_context())

    def fail_audit(_result):
        raise RuntimeError("validation audit unavailable")

    with pytest.raises(RuntimeError, match="validation audit unavailable"):
        output.publish(command, before_commit=fail_audit)

    assert published == []
    assert node.controller.published_keys == {}

    result = output.publish(command, before_commit=lambda validation: None)

    assert result.accepted is True
    assert published == [command]
    assert node.controller.status_snapshot()["published_key_counts"] == {"1": 1}
