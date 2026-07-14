from __future__ import annotations

import ast
from dataclasses import replace
import importlib
import json
from pathlib import Path
import sys
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

    def publish(self, command):
        result = self.validator.validate(command)
        self.validations.append((command, result))
        if not result.accepted:
            return result
        assert self.validator.begin_publish_authorization(command, result)
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


class RaisingDiagnosticRecorder(MemoryRecorder):
    def __init__(self, failing_kind):
        super().__init__()
        self.failing_kind = failing_kind

    def write_event(self, kind, payload):
        if kind == self.failing_kind:
            raise RuntimeError("diagnostic recorder failed")
        return super().write_event(kind, payload)


class FakeBackend:
    def __init__(self, samples):
        self.samples = samples

    def rx(self):
        return self.samples


def read_json_lines(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


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
    events = read_json_lines(recorder.events_path)
    command_event = next(
        item for item in events if item["kind"] == "command"
    )["payload"]
    assert command_event == {
        "cmd_id": 0x0A06,
        "context_version": 12,
        "decoder_id": "primary",
        "evidence": {"level": 1, "nested": {"raw": "65766964656e6365"}},
        "first_sample_index": chunk.first_sample_index,
        "last_sample_index": chunk.first_sample_index + chunk.samples.size - 1,
        "payload": b"ABC123".hex(),
        "role": "primary",
    }
    validation_event = next(
        item for item in events if item["kind"] == "validation"
    )["payload"]
    assert validation_event == {
        "accepted": True,
        "ascii_code": "ABC123",
        "cmd_id": 0x0A06,
        "decoder_id": "primary",
        "level": 1,
        "payload": b"ABC123".hex(),
        "reason": "accepted",
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
        {"first_sample_index": 43, "last_sample_index": 43},
        {"last_sample_index": 43},
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


def test_node_output_adapter_reuses_primary_gate(receiver_node_module):
    calls = []
    node = SimpleNamespace(
        primary_decoder_id="primary",
        _handle_decoded_command=lambda command: calls.append(command) or "accepted",
    )
    output = receiver_node_module.NodeCommandOutput(node)
    command = make_command("primary", make_chunk(), make_context())

    assert output.publisher_decoder_id == "primary"
    assert output.publish(command) == "accepted"
    assert calls == [command]
