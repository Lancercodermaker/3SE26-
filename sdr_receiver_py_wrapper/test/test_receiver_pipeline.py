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
from sdr_receiver_py_wrapper.device_session import DeviceConnectionError, DeviceSession
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


class ResetTrackingDecoder(RecordingDecoder):
    def __init__(self, decoder_id, **kwargs):
        super().__init__(decoder_id, **kwargs)
        self.reset_calls = []

    def reset(self, reason, context):
        self.reset_calls.append((reason, context))


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
        self.close_calls = 0
        self.rx_calls = 0

    def rx(self):
        self.rx_calls += 1
        return self.samples

    def close(self):
        self.close_calls += 1
        self.closed = True


class SettingCountingBackend(FakeBackend):
    SETTING_NAMES = {
        "sample_rate",
        "rx_lo",
        "rx_rf_bandwidth",
        "gain_control_mode_chan0",
        "rx_hardwaregain_chan0",
    }

    def __init__(self, samples):
        object.__setattr__(self, "setting_writes", {})
        object.__setattr__(self, "fail_next_rx_lo", False)
        super().__init__(samples)

    def __setattr__(self, name, value):
        if name in self.SETTING_NAMES:
            writes = self.setting_writes
            writes[name] = writes.get(name, 0) + 1
            if name == "rx_lo" and self.fail_next_rx_lo:
                object.__setattr__(self, "fail_next_rx_lo", False)
                raise RuntimeError("rx_lo write failed")
        object.__setattr__(self, name, value)


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
    connection_attempts = 0

    def backend_factory():
        nonlocal connection_attempts
        connection_attempts += 1
        if connection_attempts > 1:
            raise RuntimeError("replacement unavailable")
        return backend

    runtime = receiver_node_module.CommonReceiverRuntime(
        backend_factory=backend_factory,
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
    assert "failed to reconnect receiver" in str(runtime.worker_error)
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


def test_common_runtime_second_thread_start_failure_rolls_back_all_resources(
    receiver_node_module,
    monkeypatch,
):
    entered_rx = threading.Event()

    class SignalingBackend(FakeBackend):
        def rx(self):
            entered_rx.set()
            return super().rx()

    real_thread = threading.Thread

    class FailProcessingStartThread(real_thread):
        def start(self):
            if self.name == "sdr-common-processing":
                raise RuntimeError("processing thread start failed")
            return super().start()

    backend = SignalingBackend(np.ones(2, dtype=np.complex64))
    recorder = ClosableMemoryRecorder()
    runtime = receiver_node_module.CommonReceiverRuntime(
        backend_factory=lambda: backend,
        config=receiver_node_module.ReceiverFoundationConfig(),
        primary=RecordingDecoder("improved_v67"),
        output=FakeOutput("improved_v67"),
        recorder=recorder,
        context_provider=make_context,
        radio_settings_provider=radio_settings,
    )
    monkeypatch.setattr(
        receiver_node_module.threading,
        "Thread",
        FailProcessingStartThread,
    )

    try:
        with pytest.raises(RuntimeError, match="processing thread start failed"):
            runtime.start()

        assert entered_rx.wait(timeout=1.0)
        assert runtime.stop_event.is_set()
        assert runtime.acquisition_thread is not None
        runtime.acquisition_thread.join(timeout=1.0)
        assert not runtime.acquisition_thread.is_alive()
        assert runtime.processing_thread is not None
        assert not runtime.processing_thread.is_alive()
        assert backend.close_calls == 1
        assert recorder.closed_reasons == ["common receiver start failed"]
    finally:
        runtime.stop_event.set()
        if runtime.acquisition_thread is not None:
            runtime.acquisition_thread.join(timeout=1.0)
        runtime.close()

    assert backend.close_calls == 1
    assert recorder.closed_reasons == ["common receiver start failed"]


def test_common_runtime_close_timeout_keeps_live_dependencies_for_retry(
    receiver_node_module,
):
    entered_decode = threading.Event()
    release_decode = threading.Event()

    class BlockingDecoder(RecordingDecoder):
        def decode(self, chunk, context):
            entered_decode.set()
            if not release_decode.wait(timeout=2.0):
                raise RuntimeError("test decoder release timed out")
            return super().decode(chunk, context)

    backend = FakeBackend(np.ones(2, dtype=np.complex64))
    recorder = ClosableMemoryRecorder()
    runtime = receiver_node_module.CommonReceiverRuntime(
        backend_factory=lambda: backend,
        config=receiver_node_module.ReceiverFoundationConfig(),
        primary=BlockingDecoder("improved_v67"),
        output=FakeOutput("improved_v67"),
        recorder=recorder,
        context_provider=make_context,
        radio_settings_provider=radio_settings,
    )

    runtime.start()
    assert entered_decode.wait(timeout=1.0)
    try:
        with pytest.raises(TimeoutError, match="worker threads did not stop"):
            runtime.close(timeout_sec=0.01)

        assert runtime.stop_event.is_set()
        assert runtime._closed is False
        assert runtime.processing_thread is not None
        assert runtime.processing_thread.is_alive()
        assert backend.close_calls == 0
        assert recorder.closed_reasons == []
    finally:
        release_decode.set()

    runtime.close(timeout_sec=1.0)
    runtime.close(timeout_sec=1.0)

    assert runtime._closed is True
    assert backend.close_calls == 1
    assert recorder.closed_reasons == ["common receiver stopped"]


def test_common_runtime_concurrent_close_is_idempotent_without_deadlock(
    receiver_node_module,
):
    entered_decode = threading.Event()
    release_decode = threading.Event()
    launch_close = threading.Barrier(3)

    class BlockingDecoder(RecordingDecoder):
        def decode(self, chunk, context):
            entered_decode.set()
            if not release_decode.wait(timeout=2.0):
                raise RuntimeError("test decoder release timed out")
            return super().decode(chunk, context)

    backend = FakeBackend(np.ones(2, dtype=np.complex64))
    recorder = ClosableMemoryRecorder()
    runtime = receiver_node_module.CommonReceiverRuntime(
        backend_factory=lambda: backend,
        config=receiver_node_module.ReceiverFoundationConfig(),
        primary=BlockingDecoder("improved_v67"),
        output=FakeOutput("improved_v67"),
        recorder=recorder,
        context_provider=make_context,
        radio_settings_provider=radio_settings,
    )
    close_errors = []

    def close_runtime():
        launch_close.wait(timeout=1.0)
        try:
            runtime.close(timeout_sec=1.0)
        except BaseException as exc:
            close_errors.append(exc)

    runtime.start()
    assert entered_decode.wait(timeout=1.0)
    closers = [threading.Thread(target=close_runtime) for _ in range(2)]
    for closer in closers:
        closer.start()
    launch_close.wait(timeout=1.0)
    release_decode.set()
    for closer in closers:
        closer.join(timeout=1.0)
        assert not closer.is_alive()

    assert close_errors == []
    assert backend.close_calls == 1
    assert recorder.closed_reasons == ["common receiver stopped"]


def test_common_runtime_cleanup_deadline_preserves_retryable_stopping_state(
    receiver_node_module,
):
    entered_close = threading.Event()
    release_close = threading.Event()

    class BlockingRecorder(ClosableMemoryRecorder):
        def close(self, *, stopped_reason="closed"):
            entered_close.set()
            if not release_close.wait(timeout=2.0):
                raise RuntimeError("recorder release timed out")
            super().close(stopped_reason=stopped_reason)

    backend = FakeBackend(np.ones(2, dtype=np.complex64))
    recorder = BlockingRecorder()
    runtime = receiver_node_module.CommonReceiverRuntime(
        backend_factory=lambda: backend,
        config=receiver_node_module.ReceiverFoundationConfig(),
        primary=RecordingDecoder("improved_v67"),
        output=FakeOutput("improved_v67"),
        recorder=recorder,
        context_provider=make_context,
        radio_settings_provider=radio_settings,
    )

    with pytest.raises(TimeoutError, match="cleanup did not finish"):
        runtime.close(timeout_sec=0.01)

    assert entered_close.wait(timeout=1.0)
    assert runtime._closed is False
    assert runtime.status()["lifecycle"] == "STOPPING"
    assert backend.close_calls == 1
    release_close.set()
    runtime.close(timeout_sec=1.0)

    assert runtime.status()["lifecycle"] == "CLOSED"
    assert backend.close_calls == 1
    assert recorder.closed_reasons == ["common receiver stopped"]


def test_common_runtime_cleanup_error_is_visible_and_failed_resource_retries(
    receiver_node_module,
):
    class FailOnceRecorder(ClosableMemoryRecorder):
        def __init__(self):
            super().__init__()
            self.close_attempts = 0

        def close(self, *, stopped_reason="closed"):
            self.close_attempts += 1
            if self.close_attempts == 1:
                raise RuntimeError("recorder close failed")
            super().close(stopped_reason=stopped_reason)

    backend = FakeBackend(np.ones(2, dtype=np.complex64))
    recorder = FailOnceRecorder()
    runtime = receiver_node_module.CommonReceiverRuntime(
        backend_factory=lambda: backend,
        config=receiver_node_module.ReceiverFoundationConfig(),
        primary=RecordingDecoder("improved_v67"),
        output=FakeOutput("improved_v67"),
        recorder=recorder,
        context_provider=make_context,
        radio_settings_provider=radio_settings,
    )

    with pytest.raises(RuntimeError, match="recorder close failed"):
        runtime.close(timeout_sec=1.0)

    assert runtime.status()["cleanup_error"] == "recorder: recorder close failed"
    assert runtime._closed is False
    runtime.close(timeout_sec=1.0)

    assert runtime._closed is True
    assert backend.close_calls == 1
    assert recorder.close_attempts == 2


def test_common_runtime_failed_device_hook_retries_detached_cleanup(
    receiver_node_module,
    monkeypatch,
):
    class FailOnceCloseBackend(FakeBackend):
        def close(self):
            self.close_calls += 1
            if self.close_calls == 1:
                raise RuntimeError("device hook failed")
            self.closed = True

    backend = FailOnceCloseBackend(np.ones(2, dtype=np.complex64))
    factory_calls = []

    def backend_factory():
        factory_calls.append("connect")
        return backend

    runtime = receiver_node_module.CommonReceiverRuntime(
        backend_factory=backend_factory,
        config=receiver_node_module.ReceiverFoundationConfig(),
        primary=RecordingDecoder("improved_v67"),
        output=FakeOutput("improved_v67"),
        recorder=ClosableMemoryRecorder(),
        context_provider=make_context,
        radio_settings_provider=radio_settings,
    )

    with pytest.raises(RuntimeError, match="failed to close receiver"):
        runtime.close(timeout_sec=1.0)

    assert runtime.status()["lifecycle"] == "STOPPING"
    assert runtime._closed is False
    assert backend.close_calls == 1
    assert runtime.device.stats.closes == 0
    assert runtime.device._backend is None
    assert factory_calls == ["connect"]

    real_thread = receiver_node_module.threading.Thread
    monkeypatch.setattr(
        receiver_node_module.threading,
        "Thread",
        lambda *_args, **_kwargs: pytest.fail(
            "STOPPING runtime attempted to construct a worker"
        ),
    )
    with pytest.raises(RuntimeError, match="stopping"):
        runtime.start()
    monkeypatch.setattr(receiver_node_module.threading, "Thread", real_thread)
    for operation in (
        runtime.acquire_once,
        runtime.process_once,
        lambda: runtime.process_next(timeout_sec=0.0),
    ):
        with pytest.raises(RuntimeError, match="stopping"):
            operation()
    assert runtime.device._backend is None
    assert factory_calls == ["connect"]

    runtime.close(timeout_sec=1.0)

    assert runtime.status()["lifecycle"] == "CLOSED"
    assert runtime._closed is True
    assert backend.close_calls == 2
    assert runtime.device.stats.closes == 1
    assert runtime.device._backend is None
    assert factory_calls == ["connect"]


def test_common_runtime_always_failing_device_hook_remains_retryable(
    receiver_node_module,
):
    class AlwaysFailCloseBackend(FakeBackend):
        def close(self):
            self.close_calls += 1
            raise RuntimeError("device hook always failed")

    backend = AlwaysFailCloseBackend(np.ones(2, dtype=np.complex64))
    runtime = receiver_node_module.CommonReceiverRuntime(
        backend_factory=lambda: backend,
        config=receiver_node_module.ReceiverFoundationConfig(),
        primary=RecordingDecoder("improved_v67"),
        output=FakeOutput("improved_v67"),
        recorder=None,
        context_provider=make_context,
        radio_settings_provider=radio_settings,
    )

    for expected_calls in (1, 2):
        with pytest.raises(RuntimeError, match="device"):
            runtime.close(timeout_sec=1.0)
        assert backend.close_calls == expected_calls
        assert runtime._closed is False
        assert runtime.status()["lifecycle"] == "STOPPING"
        assert runtime.device.stats.closes == 0


def test_common_runtime_stop_event_closes_worker_exit_restart_window(
    receiver_node_module,
    monkeypatch,
):
    factory_calls = []
    backend = FakeBackend(np.ones(2, dtype=np.complex64))

    def backend_factory():
        factory_calls.append("connect")
        return backend

    runtime = receiver_node_module.CommonReceiverRuntime(
        backend_factory=backend_factory,
        config=receiver_node_module.ReceiverFoundationConfig(),
        primary=RecordingDecoder("improved_v67"),
        output=FakeOutput("improved_v67"),
        recorder=None,
        context_provider=make_context,
        radio_settings_provider=radio_settings,
    )
    runtime._lifecycle = "RUNNING"
    runtime._run_active.clear()
    runtime.stop_event.set()
    real_thread = receiver_node_module.threading.Thread
    monkeypatch.setattr(
        receiver_node_module.threading,
        "Thread",
        lambda *_args, **_kwargs: pytest.fail(
            "stopped runtime attempted to construct a worker"
        ),
    )

    with pytest.raises(RuntimeError, match="stopping"):
        runtime.start()
    monkeypatch.setattr(receiver_node_module.threading, "Thread", real_thread)
    for operation in (
        runtime.acquire_once,
        runtime.process_once,
        lambda: runtime.process_next(timeout_sec=0.0),
    ):
        with pytest.raises(RuntimeError, match="stopping"):
            operation()
    assert factory_calls == ["connect"]

    runtime.close()


def test_common_runtime_status_is_rich_truthful_and_json_safe(
    receiver_node_module,
    tmp_path,
):
    recorder = StructuredRecorder(tmp_path, "status", queue_size=2)
    backend = FakeBackend(np.ones(2, dtype=np.complex64))
    runtime = receiver_node_module.CommonReceiverRuntime(
        backend_factory=lambda: backend,
        config=receiver_node_module.ReceiverFoundationConfig(),
        primary=RecordingDecoder("improved_v67"),
        output=FakeOutput("improved_v67"),
        recorder=recorder,
        context_provider=make_context,
        radio_settings_provider=radio_settings,
    )

    status = runtime.status()
    assert status["lifecycle"] == "OPEN"
    assert status["worker_error"] is None
    assert status["cleanup_error"] is None
    assert status["queue"] == {"depth": 0, "capacity": 8}
    assert status["device"]["connects"] == 1
    assert status["acquisition"] == {
        "queue_drops": 0,
        "read_errors": 0,
        "reconnects": 0,
    }
    assert status["recorder"]["enabled"] is True
    assert status["recorder"]["stats"]["chunks_written"] == 0
    assert status["recorder"]["paths"]["iq_path"].endswith("status.c64")
    json.dumps(status, allow_nan=False)

    runtime.close()


def test_common_runtime_worker_failure_auto_closes_once_after_both_exit(
    receiver_node_module,
):
    decode_error = RuntimeError("decode exploded")
    entered_decode = threading.Event()

    class FailingDecoder(RecordingDecoder):
        def decode(self, chunk, context):
            entered_decode.set()
            raise decode_error

    backend = FakeBackend(np.ones(2, dtype=np.complex64))
    recorder = ClosableMemoryRecorder()
    runtime = receiver_node_module.CommonReceiverRuntime(
        backend_factory=lambda: backend,
        config=receiver_node_module.ReceiverFoundationConfig(),
        primary=FailingDecoder("improved_v67"),
        output=FakeOutput("improved_v67"),
        recorder=recorder,
        context_provider=make_context,
        radio_settings_provider=radio_settings,
    )

    runtime.start()
    assert entered_decode.wait(timeout=1.0)
    for worker in (runtime.acquisition_thread, runtime.processing_thread):
        assert worker is not None
        worker.join(timeout=1.0)
        assert not worker.is_alive()

    try:
        assert runtime.worker_error is decode_error
        assert backend.close_calls == 1
        assert recorder.closed_reasons == ["common receiver worker failed"]
    finally:
        runtime.close()
    runtime.close()

    assert backend.close_calls == 1
    assert recorder.closed_reasons == ["common receiver worker failed"]


@pytest.mark.parametrize("operation", ["acquire_once", "process_once", "process_next"])
def test_common_runtime_rejects_manual_queue_operations_while_worker_is_active(
    receiver_node_module,
    operation,
):
    entered_decode = threading.Event()
    release_decode = threading.Event()
    call_lock = threading.Lock()
    calls = 0

    class FirstBlockingDecoder(RecordingDecoder):
        def decode(self, chunk, context):
            nonlocal calls
            with call_lock:
                calls += 1
                call_number = calls
            if call_number == 1:
                entered_decode.set()
                if not release_decode.wait(timeout=2.0):
                    raise RuntimeError("test decoder release timed out")
            return super().decode(chunk, context)

    runtime = receiver_node_module.CommonReceiverRuntime(
        backend_factory=lambda: FakeBackend(np.ones(2, dtype=np.complex64)),
        config=receiver_node_module.ReceiverFoundationConfig(
            acquisition_queue_size=10_000
        ),
        primary=FirstBlockingDecoder("improved_v67"),
        output=FakeOutput("improved_v67"),
        recorder=MemoryRecorder(),
        context_provider=make_context,
        radio_settings_provider=radio_settings,
    )

    runtime.start()
    assert entered_decode.wait(timeout=1.0)
    runtime.stop_event.set()
    assert runtime.acquisition_thread is not None
    runtime.acquisition_thread.join(timeout=1.0)
    assert not runtime.acquisition_thread.is_alive()
    try:
        with pytest.raises(RuntimeError, match="manual queue operation"):
            getattr(runtime, operation)()
    finally:
        release_decode.set()
        runtime.close(timeout_sec=1.0)


def test_common_runtime_serializes_concurrent_manual_snapshot_config_and_read(
    receiver_node_module,
):
    launch = threading.Barrier(3)
    first_snapshot_entered = threading.Event()
    second_snapshot_entered = threading.Event()
    release_first_snapshot = threading.Event()
    snapshot_lock = threading.Lock()
    snapshot_calls = 0

    def snapshot_provider():
        nonlocal snapshot_calls
        with snapshot_lock:
            call_number = snapshot_calls
            snapshot_calls += 1
        if call_number == 0:
            return make_context(), radio_settings()
        ordinal = call_number - 1
        if ordinal == 0:
            first_snapshot_entered.set()
            if not release_first_snapshot.wait(timeout=2.0):
                raise RuntimeError("first snapshot release timed out")
        else:
            second_snapshot_entered.set()
        return (
            replace(
                make_context(context_version=12 + ordinal),
                target_version=4 + ordinal,
            ),
            radio_settings(gain=20 + ordinal),
        )

    runtime = receiver_node_module.CommonReceiverRuntime(
        backend_factory=lambda: FakeBackend(np.ones(2, dtype=np.complex64)),
        config=receiver_node_module.ReceiverFoundationConfig(
            acquisition_queue_size=4
        ),
        primary=RecordingDecoder("improved_v67"),
        output=FakeOutput("improved_v67"),
        recorder=MemoryRecorder(),
        context_provider=lambda: pytest.fail("split context provider used"),
        radio_settings_provider=lambda: pytest.fail("split radio provider used"),
        snapshot_provider=snapshot_provider,
    )
    results = []
    errors = []

    def acquire():
        launch.wait(timeout=1.0)
        try:
            results.append(runtime.acquire_once())
        except BaseException as exc:
            errors.append(exc)

    workers = [threading.Thread(target=acquire) for _ in range(2)]
    for worker in workers:
        worker.start()
    launch.wait(timeout=1.0)
    assert first_snapshot_entered.wait(timeout=1.0)
    try:
        assert second_snapshot_entered.wait(timeout=0.05) is False
    finally:
        release_first_snapshot.set()
    for worker in workers:
        worker.join(timeout=1.0)
        assert not worker.is_alive()
    runtime.close()

    assert errors == []
    assert len(results) == 2
    assert sorted(chunk.context_version for chunk in results) == [12, 13]
    assert sorted(chunk.target_version for chunk in results) == [4, 5]


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


def test_common_runtime_only_configures_when_radio_settings_change(
    receiver_node_module,
):
    backend = SettingCountingBackend(np.ones(2, dtype=np.complex64))
    gain = 20

    def settings():
        return radio_settings(gain=gain)

    runtime = receiver_node_module.CommonReceiverRuntime(
        backend_factory=lambda: backend,
        config=receiver_node_module.ReceiverFoundationConfig(),
        primary=RecordingDecoder("improved_v67"),
        output=FakeOutput("improved_v67"),
        recorder=MemoryRecorder(),
        context_provider=make_context,
        radio_settings_provider=settings,
    )

    runtime.process_once()
    runtime.process_once()
    assert set(backend.setting_writes.values()) == {1}

    gain = 21
    runtime.process_once()
    runtime.close()

    assert set(backend.setting_writes.values()) == {2}


def test_common_runtime_failed_config_does_not_advance_cache_and_retries(
    receiver_node_module,
):
    backend = SettingCountingBackend(np.ones(2, dtype=np.complex64))
    gain = 20
    runtime = receiver_node_module.CommonReceiverRuntime(
        backend_factory=lambda: backend,
        config=receiver_node_module.ReceiverFoundationConfig(),
        primary=RecordingDecoder("improved_v67"),
        output=FakeOutput("improved_v67"),
        recorder=MemoryRecorder(),
        context_provider=make_context,
        radio_settings_provider=lambda: radio_settings(gain=gain),
    )
    original = dict(runtime._applied_radio_settings)
    gain = 22
    backend.fail_next_rx_lo = True

    with pytest.raises(RuntimeError, match="failed to configure receiver"):
        runtime.acquire_once()

    assert runtime._applied_radio_settings == original
    chunk = runtime.acquire_once()
    runtime.close()

    assert chunk is not None
    assert runtime._applied_radio_settings["rx_gain_db"] == 22
    assert backend.setting_writes["rx_lo"] == 3


def test_common_runtime_recovers_reconnect_and_resets_before_next_decode(
    receiver_node_module,
):
    class FirstReadFails(FakeBackend):
        def rx(self):
            raise OSError("link lost")

    failing = FirstReadFails(np.ones(2, dtype=np.complex64))
    healthy = FakeBackend(np.ones(2, dtype=np.complex64))
    backends = iter([failing, healthy])
    decoder = ResetTrackingDecoder("improved_v67")
    recorder = MemoryRecorder()
    runtime = receiver_node_module.CommonReceiverRuntime(
        backend_factory=lambda: next(backends),
        config=receiver_node_module.ReceiverFoundationConfig(),
        primary=decoder,
        output=FakeOutput("improved_v67"),
        recorder=recorder,
        context_provider=make_context,
        radio_settings_provider=radio_settings,
    )

    assert runtime.acquire_once() is None
    result = runtime.process_once()
    runtime.close()

    assert result.chunk.chunk_id == 0
    reasons = [reason for reason, _context in decoder.reset_calls]
    assert reasons == [
        receiver_node_module.ResetReason.STARTUP,
        receiver_node_module.ResetReason.DEVICE_RECONNECT,
    ]
    assert decoder.reset_calls[0][1] == make_context()
    assert runtime._pending_device_reconnect is False
    discontinuity = next(
        payload for kind, payload in recorder.events if kind == "discontinuity"
    )
    assert discontinuity["reason"] == "device_reconnect"
    assert runtime.worker_error is None
    assert runtime.acquisition.stats.reconnects == 1


def test_common_runtime_old_queued_chunk_cannot_consume_reconnect_marker(
    receiver_node_module,
):
    class ScriptedBackend(FakeBackend):
        def __init__(self, responses):
            super().__init__(np.ones(2, dtype=np.complex64))
            self.responses = list(responses)

        def rx(self):
            response = self.responses.pop(0)
            if isinstance(response, BaseException):
                raise response
            return response

    samples = np.ones(2, dtype=np.complex64)
    old_backend = ScriptedBackend([samples, OSError("link lost")])
    new_backend = ScriptedBackend([samples])
    backends = iter([old_backend, new_backend])
    decoder = ResetTrackingDecoder("improved_v67")
    runtime = receiver_node_module.CommonReceiverRuntime(
        backend_factory=lambda: next(backends),
        config=receiver_node_module.ReceiverFoundationConfig(),
        primary=decoder,
        output=FakeOutput("improved_v67"),
        recorder=MemoryRecorder(),
        context_provider=make_context,
        radio_settings_provider=radio_settings,
    )

    old_chunk = runtime.acquire_once()
    assert old_chunk.chunk_id == 0
    assert runtime.acquire_once() is None

    old_result = runtime.process_next(timeout_sec=0.0)
    assert old_result.chunk.chunk_id == 0
    assert [reason for reason, _ in decoder.reset_calls] == [
        receiver_node_module.ResetReason.STARTUP
    ]
    assert runtime._pending_device_reconnect is True

    post_chunk = runtime.acquire_once()
    post_result = runtime.process_next(timeout_sec=0.0)
    runtime.close()

    assert post_chunk.chunk_id == post_result.chunk.chunk_id == 1
    assert [reason for reason, _ in decoder.reset_calls] == [
        receiver_node_module.ResetReason.STARTUP,
        receiver_node_module.ResetReason.DEVICE_RECONNECT,
    ]
    assert runtime._pending_device_reconnect is False


def test_common_runtime_multiple_reconnect_generations_are_not_collapsed(
    receiver_node_module,
):
    class ScriptedBackend(FakeBackend):
        def __init__(self, responses):
            super().__init__(np.ones(2, dtype=np.complex64))
            self.responses = list(responses)

        def rx(self):
            response = self.responses.pop(0)
            if isinstance(response, BaseException):
                raise response
            return response

    samples = np.ones(2, dtype=np.complex64)
    initial = ScriptedBackend([samples, OSError("generation one lost")])
    replacement = ScriptedBackend([OSError("generation two lost")])
    healthy = ScriptedBackend([samples])
    backends = iter([initial, replacement, healthy])
    decoder = ResetTrackingDecoder("improved_v67")
    runtime = receiver_node_module.CommonReceiverRuntime(
        backend_factory=lambda: next(backends),
        config=receiver_node_module.ReceiverFoundationConfig(),
        primary=decoder,
        output=FakeOutput("improved_v67"),
        recorder=MemoryRecorder(),
        context_provider=make_context,
        radio_settings_provider=radio_settings,
    )

    assert runtime.acquire_once().chunk_id == 0
    assert runtime.acquire_once() is None
    assert runtime.acquire_once() is None
    assert runtime.process_next(timeout_sec=0.0).chunk.chunk_id == 0
    assert [reason for reason, _ in decoder.reset_calls] == [
        receiver_node_module.ResetReason.STARTUP
    ]

    assert runtime.acquire_once().chunk_id == 1
    assert runtime.process_next(timeout_sec=0.0).chunk.chunk_id == 1
    runtime.close()

    assert [reason for reason, _ in decoder.reset_calls] == [
        receiver_node_module.ResetReason.STARTUP,
        receiver_node_module.ResetReason.DEVICE_RECONNECT,
        receiver_node_module.ResetReason.DEVICE_RECONNECT,
    ]
    assert runtime._pending_device_reconnect is False


def test_common_runtime_shadow_failure_blocks_later_reconnect_markers(
    receiver_node_module,
):
    class FirstDeviceResetFails(ResetTrackingDecoder):
        def __init__(self, decoder_id):
            super().__init__(decoder_id)
            self.failed = False

        def reset(self, reason, context):
            super().reset(reason, context)
            if (
                reason is receiver_node_module.ResetReason.DEVICE_RECONNECT
                and not self.failed
            ):
                self.failed = True
                raise RuntimeError("generation one shadow reset failed")

    primary = ResetTrackingDecoder("improved_v67")
    shadow = FirstDeviceResetFails("shadow")
    current_context = make_context()
    runtime = receiver_node_module.CommonReceiverRuntime(
        backend_factory=lambda: FakeBackend(np.ones(2, dtype=np.complex64)),
        config=receiver_node_module.ReceiverFoundationConfig(
            decoder_shadow="shadow"
        ),
        primary=primary,
        shadow=shadow,
        output=FakeOutput("improved_v67"),
        recorder=MemoryRecorder(),
        context_provider=lambda: current_context,
        radio_settings_provider=radio_settings,
    )
    runtime.process_once()
    current_context = replace(
        current_context,
        target="JAM_L2_KEY",
        target_version=5,
        team="RED",
        context_version=13,
    )
    runtime.acquisition._next_sample_index += 3
    with runtime.acquisition._state_lock:
        first_chunk_id = runtime.acquisition._next_chunk_id
    runtime._record_reconnect_marker(1, first_chunk_id)
    runtime._record_reconnect_marker(2, first_chunk_id)
    cleared_generations = []
    clear_marker = runtime._clear_reconnect_marker

    def record_clear(marker):
        cleared_generations.append(marker.device_generation)
        clear_marker(marker)

    runtime._clear_reconnect_marker = record_clear

    first_retry = runtime.process_once()

    assert any(
        diagnostic.stage == "shadow_decoder_reset"
        for diagnostic in first_retry.pipeline_result.diagnostic_errors
    )
    assert [reason for reason, _ in primary.reset_calls] == [
        receiver_node_module.ResetReason.STARTUP,
        receiver_node_module.ResetReason.DEVICE_RECONNECT,
        receiver_node_module.ResetReason.TARGET_CHANGE,
        receiver_node_module.ResetReason.CONTEXT_CHANGE,
        receiver_node_module.ResetReason.MANUAL,
    ]
    assert [reason for reason, _ in shadow.reset_calls] == [
        receiver_node_module.ResetReason.STARTUP,
        receiver_node_module.ResetReason.DEVICE_RECONNECT,
        receiver_node_module.ResetReason.TARGET_CHANGE,
        receiver_node_module.ResetReason.CONTEXT_CHANGE,
        receiver_node_module.ResetReason.MANUAL,
    ]
    assert cleared_generations == []
    assert [
        marker.device_generation for marker in runtime._reconnect_markers
    ] == [1, 2]

    runtime.process_once()
    runtime.close()

    assert cleared_generations == [1, 2]
    assert [reason for reason, _ in primary.reset_calls] == [
        receiver_node_module.ResetReason.STARTUP,
        receiver_node_module.ResetReason.DEVICE_RECONNECT,
        receiver_node_module.ResetReason.TARGET_CHANGE,
        receiver_node_module.ResetReason.CONTEXT_CHANGE,
        receiver_node_module.ResetReason.MANUAL,
        receiver_node_module.ResetReason.DEVICE_RECONNECT,
        receiver_node_module.ResetReason.DEVICE_RECONNECT,
    ]
    assert [reason for reason, _ in shadow.reset_calls] == [
        receiver_node_module.ResetReason.STARTUP,
        receiver_node_module.ResetReason.DEVICE_RECONNECT,
        receiver_node_module.ResetReason.TARGET_CHANGE,
        receiver_node_module.ResetReason.CONTEXT_CHANGE,
        receiver_node_module.ResetReason.MANUAL,
        receiver_node_module.ResetReason.DEVICE_RECONNECT,
        receiver_node_module.ResetReason.DEVICE_RECONNECT,
    ]
    assert runtime._reconnect_markers == []


def test_common_runtime_compatibility_pending_assignment_is_idempotent(
    receiver_node_module,
):
    decoder = ResetTrackingDecoder("improved_v67")
    runtime = receiver_node_module.CommonReceiverRuntime(
        backend_factory=lambda: FakeBackend(np.ones(2, dtype=np.complex64)),
        config=receiver_node_module.ReceiverFoundationConfig(),
        primary=decoder,
        output=FakeOutput("improved_v67"),
        recorder=MemoryRecorder(),
        context_provider=make_context,
        radio_settings_provider=radio_settings,
    )

    runtime._pending_device_reconnect = True
    runtime._pending_device_reconnect = True
    runtime.process_once()
    runtime.close()

    assert [reason for reason, _ in decoder.reset_calls] == [
        receiver_node_module.ResetReason.STARTUP,
        receiver_node_module.ResetReason.DEVICE_RECONNECT,
    ]


def test_common_runtime_dropped_first_post_reconnect_chunk_keeps_marker(
    receiver_node_module,
):
    class ScriptedBackend(FakeBackend):
        def __init__(self, responses):
            super().__init__(np.ones(2, dtype=np.complex64))
            self.responses = list(responses)

        def rx(self):
            response = self.responses.pop(0)
            if isinstance(response, BaseException):
                raise response
            return response

    samples = np.ones(2, dtype=np.complex64)
    old_backend = ScriptedBackend([samples, OSError("link lost")])
    new_backend = ScriptedBackend([samples, samples])
    backends = iter([old_backend, new_backend])
    decoder = ResetTrackingDecoder("improved_v67")
    runtime = receiver_node_module.CommonReceiverRuntime(
        backend_factory=lambda: next(backends),
        config=receiver_node_module.ReceiverFoundationConfig(
            acquisition_queue_size=1
        ),
        primary=decoder,
        output=FakeOutput("improved_v67"),
        recorder=MemoryRecorder(),
        context_provider=make_context,
        radio_settings_provider=radio_settings,
    )

    assert runtime.acquire_once().chunk_id == 0
    assert runtime.acquire_once() is None
    assert runtime.acquire_once() is None
    assert runtime.acquisition.stats.queue_drops == 1
    assert runtime.process_next(timeout_sec=0.0).chunk.chunk_id == 0

    assert runtime.acquire_once().chunk_id == 2
    assert runtime.process_next(timeout_sec=0.0).chunk.chunk_id == 2
    runtime.close()

    assert [reason for reason, _ in decoder.reset_calls] == [
        receiver_node_module.ResetReason.STARTUP,
        receiver_node_module.ResetReason.DEVICE_RECONNECT,
        receiver_node_module.ResetReason.MANUAL,
    ]
    assert runtime._pending_device_reconnect is False


def test_common_runtime_shadow_reset_diagnostic_failure_clears_marker(
    receiver_node_module,
):
    recorder = ShadowFailingRecorder(fail_kind="decoder_reset")
    primary = ResetTrackingDecoder("improved_v67")
    shadow = ResetTrackingDecoder("shadow")
    runtime = receiver_node_module.CommonReceiverRuntime(
        backend_factory=lambda: FakeBackend(np.ones(2, dtype=np.complex64)),
        config=receiver_node_module.ReceiverFoundationConfig(
            decoder_shadow="shadow"
        ),
        primary=primary,
        shadow=shadow,
        output=FakeOutput("improved_v67"),
        recorder=recorder,
        context_provider=make_context,
        radio_settings_provider=radio_settings,
    )
    with runtime.acquisition._state_lock:
        first_chunk_id = runtime.acquisition._next_chunk_id
    runtime._record_reconnect_marker(1, first_chunk_id)
    runtime._record_reconnect_marker(2, first_chunk_id)

    result = runtime.process_once()
    runtime.close()

    assert any(
        diagnostic.stage == "shadow_decoder_reset_diagnostic"
        for diagnostic in result.pipeline_result.diagnostic_errors
    )
    expected = [
        receiver_node_module.ResetReason.STARTUP,
        receiver_node_module.ResetReason.DEVICE_RECONNECT,
        receiver_node_module.ResetReason.DEVICE_RECONNECT,
    ]
    assert [reason for reason, _ in primary.reset_calls] == expected
    assert [reason for reason, _ in shadow.reset_calls] == expected
    assert runtime._pending_device_reconnect is False


def test_common_runtime_startup_reset_failure_preserves_pending_reconnect(
    receiver_node_module,
):
    class StartupResetFails(ResetTrackingDecoder):
        def reset(self, reason, context):
            super().reset(reason, context)
            if reason is receiver_node_module.ResetReason.STARTUP:
                raise RuntimeError("startup reset failed")

    primary = StartupResetFails("improved_v67")
    shadow = ResetTrackingDecoder("shadow")
    runtime = receiver_node_module.CommonReceiverRuntime(
        backend_factory=lambda: FakeBackend(np.ones(2, dtype=np.complex64)),
        config=receiver_node_module.ReceiverFoundationConfig(
            decoder_shadow="shadow"
        ),
        primary=primary,
        shadow=shadow,
        output=FakeOutput("improved_v67"),
        recorder=MemoryRecorder(),
        context_provider=make_context,
        radio_settings_provider=radio_settings,
    )
    runtime._pending_device_reconnect = True

    with pytest.raises(RuntimeError, match="startup reset failed"):
        runtime.process_once()
    runtime.close()

    assert [reason for reason, _ in primary.reset_calls] == [
        receiver_node_module.ResetReason.STARTUP
    ]
    assert shadow.reset_calls == []
    assert runtime._pending_device_reconnect is True


def test_common_runtime_device_reset_failure_preserves_pending_reconnect(
    receiver_node_module,
):
    class DeviceResetFails(ResetTrackingDecoder):
        def reset(self, reason, context):
            super().reset(reason, context)
            if reason is receiver_node_module.ResetReason.DEVICE_RECONNECT:
                raise RuntimeError("device reset failed")

    primary = DeviceResetFails("improved_v67")
    shadow = ResetTrackingDecoder("shadow")
    runtime = receiver_node_module.CommonReceiverRuntime(
        backend_factory=lambda: FakeBackend(np.ones(2, dtype=np.complex64)),
        config=receiver_node_module.ReceiverFoundationConfig(
            decoder_shadow="shadow"
        ),
        primary=primary,
        shadow=shadow,
        output=FakeOutput("improved_v67"),
        recorder=MemoryRecorder(),
        context_provider=make_context,
        radio_settings_provider=radio_settings,
    )
    runtime._pending_device_reconnect = True

    with pytest.raises(RuntimeError, match="device reset failed"):
        runtime.process_once()
    runtime.close()

    assert [reason for reason, _ in primary.reset_calls] == [
        receiver_node_module.ResetReason.STARTUP,
        receiver_node_module.ResetReason.DEVICE_RECONNECT,
    ]
    assert [reason for reason, _ in shadow.reset_calls] == [
        receiver_node_module.ResetReason.STARTUP
    ]
    assert runtime._pending_device_reconnect is True


def test_common_runtime_shadow_device_reset_failure_retries_pending_reconnect(
    receiver_node_module,
):
    class ShadowDeviceResetFailsOnce(ResetTrackingDecoder):
        def __init__(self, decoder_id):
            super().__init__(decoder_id)
            self.failed = False

        def reset(self, reason, context):
            super().reset(reason, context)
            if (
                reason is receiver_node_module.ResetReason.DEVICE_RECONNECT
                and not self.failed
            ):
                self.failed = True
                raise RuntimeError("shadow device reset failed")

    primary = ResetTrackingDecoder("improved_v67")
    shadow = ShadowDeviceResetFailsOnce("shadow")
    runtime = receiver_node_module.CommonReceiverRuntime(
        backend_factory=lambda: FakeBackend(np.ones(2, dtype=np.complex64)),
        config=receiver_node_module.ReceiverFoundationConfig(
            decoder_shadow="shadow"
        ),
        primary=primary,
        shadow=shadow,
        output=FakeOutput("improved_v67"),
        recorder=MemoryRecorder(),
        context_provider=make_context,
        radio_settings_provider=radio_settings,
    )
    runtime._pending_device_reconnect = True

    first = runtime.process_once()

    assert first.pipeline_result.diagnostic_errors[0].stage == "shadow_decoder_reset"
    assert runtime._pending_device_reconnect is True

    runtime.process_once()
    runtime.close()

    expected = [
        receiver_node_module.ResetReason.STARTUP,
        receiver_node_module.ResetReason.DEVICE_RECONNECT,
        receiver_node_module.ResetReason.DEVICE_RECONNECT,
    ]
    assert [reason for reason, _ in primary.reset_calls] == expected
    assert [reason for reason, _ in shadow.reset_calls] == expected
    assert runtime._pending_device_reconnect is False


def test_common_runtime_resets_both_decoders_for_context_target_and_gap(
    receiver_node_module,
):
    base = make_context()
    target_changed = replace(base, target="JAM_L2_KEY", target_version=5)
    context_changed = replace(
        target_changed,
        team="RED",
        context_version=13,
    )
    contexts = iter(
        [
            base,
            base,
            target_changed,
            context_changed,
            context_changed,
            context_changed,
        ]
    )
    primary = ResetTrackingDecoder("improved_v67")
    shadow = ResetTrackingDecoder("shadow")
    runtime = receiver_node_module.CommonReceiverRuntime(
        backend_factory=lambda: FakeBackend(np.ones(2, dtype=np.complex64)),
        config=receiver_node_module.ReceiverFoundationConfig(
            decoder_shadow="shadow",
            acquisition_queue_size=1,
        ),
        primary=primary,
        shadow=shadow,
        output=FakeOutput("improved_v67"),
        recorder=MemoryRecorder(),
        context_provider=lambda: next(contexts),
        radio_settings_provider=radio_settings,
    )

    runtime.process_once()
    runtime.process_once()
    runtime.process_once()
    assert runtime.acquire_once() is not None
    assert runtime.acquire_once() is None
    runtime.process_next(timeout_sec=0.0)
    assert runtime.acquire_once() is not None
    runtime.process_next(timeout_sec=0.0)
    runtime.close()

    expected = [
        receiver_node_module.ResetReason.STARTUP,
        receiver_node_module.ResetReason.TARGET_CHANGE,
        receiver_node_module.ResetReason.CONTEXT_CHANGE,
        receiver_node_module.ResetReason.MANUAL,
    ]
    assert [reason for reason, _ in primary.reset_calls] == expected
    assert [reason for reason, _ in shadow.reset_calls] == expected


def test_common_runtime_combines_reconnect_target_context_and_gap_resets(
    receiver_node_module,
):
    current_context = make_context()
    primary = ResetTrackingDecoder("improved_v67")
    shadow = ResetTrackingDecoder("shadow")
    runtime = receiver_node_module.CommonReceiverRuntime(
        backend_factory=lambda: FakeBackend(np.ones(2, dtype=np.complex64)),
        config=receiver_node_module.ReceiverFoundationConfig(
            decoder_shadow="shadow"
        ),
        primary=primary,
        shadow=shadow,
        output=FakeOutput("improved_v67"),
        recorder=MemoryRecorder(),
        context_provider=lambda: current_context,
        radio_settings_provider=radio_settings,
    )

    runtime.process_once()
    current_context = replace(
        current_context,
        target="JAM_L2_KEY",
        target_version=5,
        team="RED",
        context_version=13,
    )
    runtime._pending_device_reconnect = True
    runtime.acquisition._next_sample_index += 3
    runtime.process_once()
    runtime.close()

    expected = [
        receiver_node_module.ResetReason.STARTUP,
        receiver_node_module.ResetReason.DEVICE_RECONNECT,
        receiver_node_module.ResetReason.TARGET_CHANGE,
        receiver_node_module.ResetReason.CONTEXT_CHANGE,
        receiver_node_module.ResetReason.MANUAL,
    ]
    assert [reason for reason, _ in primary.reset_calls] == expected
    assert [reason for reason, _ in shadow.reset_calls] == expected


def test_common_runtime_primary_reset_failure_stops_later_combined_reasons(
    receiver_node_module,
):
    class ContextResetFails(ResetTrackingDecoder):
        def reset(self, reason, context):
            super().reset(reason, context)
            if reason is receiver_node_module.ResetReason.CONTEXT_CHANGE:
                raise RuntimeError("context reset failed")

    current_context = make_context()
    primary = ContextResetFails("improved_v67")
    shadow = ResetTrackingDecoder("shadow")
    runtime = receiver_node_module.CommonReceiverRuntime(
        backend_factory=lambda: FakeBackend(np.ones(2, dtype=np.complex64)),
        config=receiver_node_module.ReceiverFoundationConfig(
            decoder_shadow="shadow"
        ),
        primary=primary,
        shadow=shadow,
        output=FakeOutput("improved_v67"),
        recorder=MemoryRecorder(),
        context_provider=lambda: current_context,
        radio_settings_provider=radio_settings,
    )

    runtime.process_once()
    current_context = replace(
        current_context,
        target="JAM_L2_KEY",
        target_version=5,
        team="RED",
        context_version=13,
    )
    runtime.acquisition._next_sample_index += 3
    with pytest.raises(RuntimeError, match="context reset failed"):
        runtime.process_once()
    runtime.close()

    assert [reason for reason, _ in primary.reset_calls] == [
        receiver_node_module.ResetReason.STARTUP,
        receiver_node_module.ResetReason.TARGET_CHANGE,
        receiver_node_module.ResetReason.CONTEXT_CHANGE,
    ]
    assert [reason for reason, _ in shadow.reset_calls] == [
        receiver_node_module.ResetReason.STARTUP,
        receiver_node_module.ResetReason.TARGET_CHANGE,
    ]


def test_common_runtime_primary_reset_failure_is_fatal_before_shadow_or_decode(
    receiver_node_module,
):
    class ResetFails(ResetTrackingDecoder):
        def reset(self, reason, context):
            super().reset(reason, context)
            raise RuntimeError("primary reset failed")

    primary = ResetFails("improved_v67")
    shadow = ResetTrackingDecoder("shadow")
    runtime = receiver_node_module.CommonReceiverRuntime(
        backend_factory=lambda: FakeBackend(np.ones(2, dtype=np.complex64)),
        config=receiver_node_module.ReceiverFoundationConfig(
            decoder_shadow="shadow"
        ),
        primary=primary,
        shadow=shadow,
        output=FakeOutput("improved_v67"),
        recorder=MemoryRecorder(),
        context_provider=make_context,
        radio_settings_provider=radio_settings,
    )

    with pytest.raises(RuntimeError, match="primary reset failed"):
        runtime.process_once()
    runtime.close()

    assert primary.calls == []
    assert shadow.reset_calls == []
    assert shadow.calls == []


def test_common_runtime_shadow_reset_failure_isolated_and_observable(
    receiver_node_module,
):
    class ShadowResetFails(ResetTrackingDecoder):
        def reset(self, reason, context):
            raise RuntimeError("shadow reset failed")

    primary = ResetTrackingDecoder("improved_v67")
    shadow = ShadowResetFails("shadow")
    runtime = receiver_node_module.CommonReceiverRuntime(
        backend_factory=lambda: FakeBackend(np.ones(2, dtype=np.complex64)),
        config=receiver_node_module.ReceiverFoundationConfig(
            decoder_shadow="shadow"
        ),
        primary=primary,
        shadow=shadow,
        output=FakeOutput("improved_v67"),
        recorder=MemoryRecorder(),
        context_provider=make_context,
        radio_settings_provider=radio_settings,
    )

    result = runtime.process_once()
    runtime.close()

    assert len(result.pipeline_result.primary_commands) == 1
    assert result.pipeline_result.diagnostic_errors[0].stage == "shadow_decoder_reset"


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


def test_common_runtime_connection_failure_does_not_wait_for_blocked_recorder(
    receiver_node_module,
    monkeypatch,
):
    class BlockingRecorder(MemoryRecorder):
        def __init__(self):
            super().__init__()
            self.entered = threading.Event()
            self.release = threading.Event()
            self.finished = threading.Event()
            self.close_attempts = 0
            self.close_thread = None

        def close(self, *, stopped_reason="closed"):
            self.close_attempts += 1
            self.close_thread = threading.current_thread()
            self.entered.set()
            self.release.wait(timeout=2.0)
            self.finished.set()

    monkeypatch.setattr(
        receiver_node_module,
        "CONSTRUCTOR_CLEANUP_WAIT_SEC",
        0.02,
        raising=False,
    )
    recorder = BlockingRecorder()
    returned = threading.Event()
    errors = []

    def construct():
        try:
            receiver_node_module.CommonReceiverRuntime(
                backend_factory=lambda: (_ for _ in ()).throw(
                    OSError("radio unavailable")
                ),
                config=receiver_node_module.ReceiverFoundationConfig(),
                primary=RecordingDecoder("improved_v67"),
                output=FakeOutput("improved_v67"),
                recorder=recorder,
                context_provider=make_context,
                radio_settings_provider=radio_settings,
            )
        except BaseException as exc:
            errors.append(exc)
        finally:
            returned.set()

    constructor_thread = threading.Thread(
        target=construct,
        name="constructor-test",
        daemon=True,
    )
    constructor_thread.start()
    assert recorder.entered.wait(timeout=0.5)
    try:
        assert returned.wait(timeout=0.2), "constructor waited for recorder cleanup"
        assert isinstance(errors[0], DeviceConnectionError)
        assert isinstance(errors[0].__cause__, OSError)
        assert recorder.close_thread.name == "sdr-common-cleanup"
        assert recorder.close_thread.daemon is True
    finally:
        recorder.release.set()
        constructor_thread.join(timeout=1.0)
    assert recorder.finished.wait(timeout=0.5)
    assert recorder.close_attempts == 1


def test_common_runtime_setup_failure_closes_created_device_before_async_recorder(
    receiver_node_module,
    monkeypatch,
):
    class BlockingRecorder(MemoryRecorder):
        def __init__(self):
            super().__init__()
            self.entered = threading.Event()
            self.release = threading.Event()
            self.finished = threading.Event()
            self.close_attempts = 0

        def close(self, *, stopped_reason="closed"):
            self.close_attempts += 1
            self.entered.set()
            self.release.wait(timeout=2.0)
            self.finished.set()

    monkeypatch.setattr(
        receiver_node_module,
        "CONSTRUCTOR_CLEANUP_WAIT_SEC",
        0.02,
        raising=False,
    )
    backend = FakeBackend(np.ones(2, dtype=np.complex64))
    recorder = BlockingRecorder()
    returned = threading.Event()
    errors = []

    def construct():
        try:
            receiver_node_module.CommonReceiverRuntime(
                backend_factory=lambda: backend,
                config=receiver_node_module.ReceiverFoundationConfig(),
                primary=RecordingDecoder("improved_v67"),
                output=FakeOutput("improved_v67"),
                recorder=recorder,
                context_provider=make_context,
                radio_settings_provider=lambda: {},
            )
        except BaseException as exc:
            errors.append(exc)
        finally:
            returned.set()

    constructor_thread = threading.Thread(target=construct, daemon=True)
    constructor_thread.start()
    assert recorder.entered.wait(timeout=0.5)
    try:
        assert backend.closed is True
        assert returned.wait(timeout=0.2), "constructor waited for recorder cleanup"
        assert isinstance(errors[0], ValueError)
        assert "invalid keys" in str(errors[0])
    finally:
        recorder.release.set()
        constructor_thread.join(timeout=1.0)
    assert recorder.finished.wait(timeout=0.5)
    assert recorder.close_attempts == 1
    assert backend.close_calls == 1


def test_common_runtime_constructor_cleanup_error_is_logged_not_raised(
    receiver_node_module,
    caplog,
    capfd,
):
    class RaisingRecorder(MemoryRecorder):
        def close(self, *, stopped_reason="closed"):
            raise RuntimeError("recorder cleanup exploded")

    def fail_connection():
        raise OSError("radio unavailable")

    with caplog.at_level("ERROR", logger=receiver_node_module.__name__):
        with pytest.raises(DeviceConnectionError) as raised:
            receiver_node_module.CommonReceiverRuntime(
                backend_factory=fail_connection,
                config=receiver_node_module.ReceiverFoundationConfig(),
                primary=RecordingDecoder("improved_v67"),
                output=FakeOutput("improved_v67"),
                recorder=RaisingRecorder(),
                context_provider=make_context,
                radio_settings_provider=radio_settings,
            )

    assert isinstance(raised.value.__cause__, OSError)
    emitted = caplog.text + capfd.readouterr().err
    assert "recorder: recorder cleanup exploded" in emitted


def test_common_runtime_constructor_device_cleanup_failure_is_truthfully_logged(
    receiver_node_module,
    caplog,
    capfd,
):
    class CleanupFailsBackend(FakeBackend):
        def close(self):
            self.close_calls += 1
            raise RuntimeError("constructor device cleanup failed")

    backend = CleanupFailsBackend(np.ones(2, dtype=np.complex64))
    with caplog.at_level("ERROR", logger=receiver_node_module.__name__):
        with pytest.raises(ValueError, match="invalid keys"):
            receiver_node_module.CommonReceiverRuntime(
                backend_factory=lambda: backend,
                config=receiver_node_module.ReceiverFoundationConfig(),
                primary=RecordingDecoder("improved_v67"),
                output=FakeOutput("improved_v67"),
                recorder=None,
                context_provider=make_context,
                radio_settings_provider=lambda: {},
            )

    emitted = caplog.text + capfd.readouterr().err
    assert "device: failed to close receiver" in emitted
    assert backend.close_calls == 1


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
    node.foundation_config = receiver_node_module.ReceiverFoundationConfig(
        sdr_uri="ip:10.0.0.7"
    )
    node.adapter = SimpleNamespace(
        module=SimpleNamespace(
            adi=SimpleNamespace(
                Pluto=lambda uri: factory_calls.append(uri) or backend
            )
        ),
        get_core_config_snapshot=lambda: {"rx_buffer_size": 64},
    )

    factory = node._common_backend_factory()
    assert factory_calls == []
    assert factory() is backend
    assert factory_calls == ["ip:10.0.0.7"]
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
    node.foundation_config = receiver_node_module.ReceiverFoundationConfig(
        sdr_uri="ip:must-not-be-used"
    )
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


@pytest.mark.parametrize(
    ("config", "role"),
    [
        (
            lambda module: module.ReceiverFoundationConfig(
                decoder_primary="unknown_primary"
            ),
            "decoder_primary",
        ),
        (
            lambda module: module.ReceiverFoundationConfig(
                decoder_primary="improved_v67",
                decoder_shadow="unknown_shadow",
            ),
            "decoder_shadow",
        ),
    ],
)
def test_node_rejects_unknown_decoder_immediately_even_when_receiver_disabled(
    receiver_node_module,
    monkeypatch,
    config,
    role,
):
    resource_calls = []
    node_type = receiver_node_module.SdrReceiverPyWrapperNode
    monkeypatch.setattr(receiver_node_module.Node, "__init__", lambda *_args: None)
    monkeypatch.setattr(node_type, "_declare_parameters", lambda _self: None)
    monkeypatch.setattr(
        node_type,
        "_load_receiver_foundation_config",
        lambda _self: config(receiver_node_module),
    )

    def get_parameter(_self, name):
        if name == "run_mode":
            return SimpleNamespace(value="competition")
        if name == "start_receiver":
            return SimpleNamespace(value=False)
        raise AssertionError(f"parameter read after decoder config: {name}")

    monkeypatch.setattr(node_type, "get_parameter", get_parameter, raising=False)
    for method in (
        "create_publisher",
        "create_subscription",
        "create_timer",
        "_create_iq_recorder",
        "_configure_receiver_runtime",
    ):
        monkeypatch.setattr(
            node_type,
            method,
            lambda *_args, _method=method, **_kwargs: resource_calls.append(
                _method
            ),
            raising=False,
        )
    monkeypatch.setattr(
        receiver_node_module,
        "ReceiverCoreAdapter",
        lambda *_args, **_kwargs: resource_calls.append("ReceiverCoreAdapter"),
    )
    monkeypatch.setattr(
        receiver_node_module,
        "DeviceSession",
        lambda *_args, **_kwargs: resource_calls.append("DeviceSession"),
    )
    monkeypatch.setattr(
        receiver_node_module.threading,
        "Thread",
        lambda *_args, **_kwargs: resource_calls.append("Thread"),
    )

    with pytest.raises(ValueError, match=role):
        node_type()

    assert resource_calls == []


def test_decoder_registry_accepts_independent_improved_v67_shadow(
    receiver_node_module,
):
    config = receiver_node_module.ReceiverFoundationConfig(
        decoder_primary="improved_v67",
        decoder_shadow="improved_v67",
    )

    receiver_node_module._validate_decoder_registry(config)


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
        "command_event_id": f"{chunk.chunk_id}:primary:0",
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
        "command_event_id": f"{chunk.chunk_id}:primary:0",
        "command_first_sample_index": chunk.first_sample_index,
        "command_last_sample_index": chunk.first_sample_index + chunk.samples.size - 1,
        "context_version": 12,
        "crc16_ok": True,
        "crc8_ok": True,
        "crc_mode": "fake_validated",
        "decoder_id": "primary",
        "level": 1,
        "payload": b"ABC123".hex(),
        "profile": "competition",
        "reason": "accepted",
        "receive_wall_time": chunk.rx_wall_time,
        "target": "JAM_L1_KEY",
        "target_version": 4,
        "team": "BLUE",
    }


def test_sidecar_correlates_two_identical_commands_with_unique_event_ids(
    receiver_node_module,
    tmp_path,
):
    class DuplicateCommandDecoder(RecordingDecoder):
        def decode(self, chunk, context):
            command = make_command(self.decoder_id, chunk, context)
            return [command, command]

    chunk = make_chunk()
    recorder = StructuredRecorder(tmp_path, "duplicate_commands", queue_size=8)
    pipeline = receiver_node_module.ReceiverPipeline(
        primary=DuplicateCommandDecoder("primary"),
        output=FakeOutput("primary"),
        recorder=recorder,
    )

    pipeline.process(chunk, make_context())
    recorder.close(stopped_reason="test complete")

    events = read_json_lines(recorder.events_path)
    commands = [item["payload"] for item in events if item["kind"] == "command"]
    validations = [
        item["payload"] for item in events if item["kind"] == "validation"
    ]
    command_ids = [item["command_event_id"] for item in commands]
    validation_ids = [item["command_event_id"] for item in validations]

    assert command_ids == [f"{chunk.chunk_id}:primary:0", f"{chunk.chunk_id}:primary:1"]
    assert validation_ids == command_ids
    assert len(set(command_ids)) == 2
    for command_event, validation_event in zip(commands, validations):
        assert validation_event["command_event_id"] == command_event["command_event_id"]
        assert validation_event["crc8_ok"] == command_event["crc8_ok"] is True
        assert validation_event["crc16_ok"] == command_event["crc16_ok"] is True
        assert validation_event["crc_mode"] == command_event["crc_mode"]
        assert validation_event["receive_wall_time"] == command_event[
            "receive_wall_time"
        ]


def test_sidecar_correlates_controller_early_rejection_without_prepare_callback(
    receiver_node_module,
):
    class EarlyRejectingOutput:
        publisher_decoder_id = "primary"

        def publish(self, _command, *, before_commit=None):
            return receiver_node_module.ValidationResult(
                False,
                "controller target rejected before audit",
                ascii_code="ABC123",
                level=1,
            )

    recorder = MemoryRecorder()
    pipeline = receiver_node_module.ReceiverPipeline(
        primary=RecordingDecoder("primary"),
        output=EarlyRejectingOutput(),
        recorder=recorder,
    )

    result = pipeline.process(make_chunk(), make_context())

    command_event = next(
        payload for kind, payload in recorder.events if kind == "command"
    )
    validation_event = next(
        payload for kind, payload in recorder.events if kind == "validation"
    )
    assert result.validation_results[0].accepted is False
    assert validation_event["command_event_id"] == command_event["command_event_id"]


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
    "sdr_uri": "ip:192.168.2.1",
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
        {"sdr_uri": ""},
        {"sdr_uri": 7},
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


def test_common_controller_rejects_target_before_mutation_reservation_or_audit(
    receiver_node_module,
):
    node = object.__new__(receiver_node_module.SdrReceiverPyWrapperNode)
    node.primary_decoder_id = "improved_v67"
    node.command_validator = CommandValidator()
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
    pending_transition_attempts = []
    node._retry_pending_rf_transition_locked = lambda: (
        pending_transition_attempts.append("retry") or True
    )
    node._set_receiver_target_or_profile = lambda *_args, **_kwargs: None
    published = []
    node._publish_validated_jam_code = lambda *_args: published.append("ROS")
    audit = []
    before = node.controller.status_snapshot()
    command = replace(
        make_command("improved_v67", make_chunk(), make_context()),
        target="INFO",
    )

    result = receiver_node_module.NodeCommandOutput(node).publish(
        command,
        before_commit=audit.append,
    )

    assert result.accepted is False
    assert "target" in result.reason
    assert node.controller.status_snapshot() == before
    assert node.command_validator._reserved_keys == set()
    assert node.command_validator._pending_authorizations == {}
    assert list(node.command_validator._committed_keys) == []
    assert pending_transition_attempts == []
    assert audit == []
    assert published == []


def test_post_ros_commit_failure_quarantines_publication_without_rollback(
    receiver_node_module,
):
    class CommitFailingValidator(CommandValidator):
        def commit_publish_authorization(self, _command, _result):
            return False

    node = object.__new__(receiver_node_module.SdrReceiverPyWrapperNode)
    node.primary_decoder_id = "improved_v67"
    node.command_validator = CommitFailingValidator()
    node.controller = receiver_node_module.CompetitionController(
        key_publish_min_interval_sec=0.0
    )
    node.controller.update_context(
        receiver_node_module.RadarContext(
            self_id=9,
            self_color=2,
            radar_info_raw=0,
            jam_level=3,
            key_mutable=True,
        )
    )
    node._controller_lock = threading.RLock()
    node._pending_rf_transition = None
    node.publication_indeterminate = None
    node.publish_ros_outputs = True
    node.run_mode = "competition"
    node.latest_context = None
    node.adapter = SimpleNamespace(get_stats_snapshot=lambda: {})
    node.get_clock = lambda: SimpleNamespace(
        now=lambda: SimpleNamespace(to_msg=lambda: "stamp")
    )
    node.get_logger = lambda: SimpleNamespace(
        debug=lambda _message: None,
        warn=lambda _message: None,
        info=lambda _message: None,
    )
    node._retry_pending_rf_transition_locked = lambda: True
    transitions = []
    node._set_receiver_target_or_profile = (
        lambda target, **_kwargs: transitions.append(target)
    )
    published = []
    node.jam_code_pub = SimpleNamespace(publish=published.append)
    command = replace(
        make_command("improved_v67", make_chunk(), make_context()),
        target="JAM_L3_KEY",
        evidence={"level": 3},
    )

    with pytest.raises(
        receiver_node_module.PublicationIndeterminateError,
        match="could not be committed",
    ):
        receiver_node_module.NodeCommandOutput(node).publish(command)

    assert len(published) == 1
    assert node.controller.status_snapshot()["published_key_counts"] == {"3": 1}
    assert node.controller.desired_target == "INFO"
    assert transitions == ["INFO"]
    assert node.command_validator._pending_authorizations == {}

    result = receiver_node_module.NodeCommandOutput(node).publish(command)
    assert result.accepted is False
    assert "indeterminate" in result.reason
    assert len(published) == 1

    result = node._handle_decoded_command(command)
    assert result.accepted is False
    assert "indeterminate" in result.reason
    assert len(published) == 1


def test_destroy_node_timeout_preserves_runtime_and_skips_unsafe_teardown(
    receiver_node_module,
    monkeypatch,
):
    calls = []
    monkeypatch.setattr(
        receiver_node_module.Node,
        "destroy_node",
        lambda _self: calls.append("super") or True,
        raising=False,
    )
    node = object.__new__(receiver_node_module.SdrReceiverPyWrapperNode)
    node.common_runtime = SimpleNamespace(
        close=lambda: (_ for _ in ()).throw(TimeoutError("still running"))
    )
    node.run_mode = "competition"
    node.adapter = SimpleNamespace(
        stop=lambda: calls.append("stop"),
        restore_patches=lambda: calls.append("restore"),
    )
    node.iq_recorder = SimpleNamespace(close=lambda: calls.append("recorder"))

    with pytest.raises(TimeoutError, match="still running"):
        node.destroy_node()

    assert calls == []


def test_destroy_node_device_cleanup_error_defers_all_other_teardown(
    receiver_node_module,
    monkeypatch,
):
    calls = []

    class RetryRuntime:
        def __init__(self):
            self.attempts = 0

        def close(self):
            self.attempts += 1
            calls.append("runtime")
            if self.attempts == 1:
                raise RuntimeError("device cleanup failed")

    monkeypatch.setattr(
        receiver_node_module.Node,
        "destroy_node",
        lambda _self: calls.append("super") or True,
        raising=False,
    )
    node = object.__new__(receiver_node_module.SdrReceiverPyWrapperNode)
    node.common_runtime = RetryRuntime()
    node.run_mode = "competition"
    node.adapter = SimpleNamespace(
        stop=lambda: calls.append("stop"),
        restore_patches=lambda: calls.append("restore"),
    )
    node.iq_recorder = SimpleNamespace(close=lambda: calls.append("recorder"))

    with pytest.raises(RuntimeError, match="device cleanup failed"):
        node.destroy_node()

    assert calls == ["runtime"]
    assert node.destroy_node() is True
    assert calls == ["runtime", "runtime", "restore", "recorder", "super"]


def test_main_shuts_rclpy_down_when_node_construction_fails(
    receiver_node_module,
    monkeypatch,
):
    calls = []
    monkeypatch.setattr(
        receiver_node_module.rclpy,
        "init",
        lambda **_kwargs: calls.append("init"),
        raising=False,
    )
    monkeypatch.setattr(
        receiver_node_module.rclpy,
        "shutdown",
        lambda: calls.append("shutdown"),
        raising=False,
    )
    monkeypatch.setattr(
        receiver_node_module,
        "SdrReceiverPyWrapperNode",
        lambda: (_ for _ in ()).throw(RuntimeError("construction failed")),
    )

    with pytest.raises(RuntimeError, match="construction failed"):
        receiver_node_module.main()

    assert calls == ["init", "shutdown"]
