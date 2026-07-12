import ast
from dataclasses import FrozenInstanceError, fields
import inspect
from pathlib import Path
from typing import get_type_hints

import numpy as np
import pytest

import sdr_receiver_py_wrapper.decoder_api as decoder_api
from sdr_receiver_py_wrapper.decoder_api import DecoderPlugin
from sdr_receiver_py_wrapper.models import (
    DecodedCommand,
    DecodeContext,
    DecoderStats,
    IqChunk,
    ResetReason,
    RfMetrics,
)


def test_iq_chunk_rejects_non_complex64():
    try:
        IqChunk(1, 0, np.ones(8), 2_000_000, 1.0, 10, 434_920_000, 940_000, 20, 1, 1)
    except ValueError as exc:
        assert "complex64" in str(exc)
    else:
        raise AssertionError("expected ValueError")


def _readonly_samples() -> np.ndarray:
    samples = np.ones(8, dtype=np.complex64)
    samples.setflags(write=False)
    return samples


def _iq_chunk(samples: np.ndarray | None = None) -> IqChunk:
    return IqChunk(
        1,
        0,
        _readonly_samples() if samples is None else samples,
        2_000_000,
        1.0,
        10,
        434_920_000,
        940_000,
        20,
        1,
        1,
    )


def _decoded_command() -> DecodedCommand:
    return DecodedCommand(
        cmd_id=0x0A06,
        payload=b"ABC123",
        decoder_id="test-decoder",
        profile="BLUE-L1",
        crc8_ok=True,
        crc16_ok=True,
        crc_mode="kermit-x3014",
        first_sample_index=100,
        last_sample_index=199,
        receive_wall_time=1.5,
        target="L1",
        team="BLUE",
        context_version=7,
        evidence={"sequence": 12},
    )


def test_iq_chunk_preserves_base_field_order_and_appends_optional_rf_metrics():
    assert [field.name for field in fields(IqChunk)] == [
        "chunk_id",
        "first_sample_index",
        "samples",
        "sample_rate_hz",
        "rx_wall_time",
        "rx_monotonic_ns",
        "lo_hz",
        "rf_bandwidth_hz",
        "rx_gain_db",
        "target_version",
        "context_version",
        "rf_metrics",
    ]
    assert _iq_chunk().rf_metrics is None


def test_iq_chunk_accepts_same_read_only_array_without_copying_or_mutating_it():
    samples = _readonly_samples()

    chunk = _iq_chunk(samples)

    assert chunk.samples is samples
    assert not samples.flags.writeable


def test_iq_chunk_rejects_writeable_array_without_changing_caller_flags():
    samples = np.ones(8, dtype=np.complex64)

    with pytest.raises(ValueError, match="read-only"):
        _iq_chunk(samples)

    assert samples.flags.writeable


@pytest.mark.parametrize(
    "value,attribute,replacement",
    [
        (_iq_chunk(), "chunk_id", 2),
        (RfMetrics(0.2, 0.5, 0.0, 8), "rms", 0.3),
        (DecodeContext("BLUE", "L1", "BLUE-L1", 3, 4), "team", "RED"),
        (_decoded_command(), "cmd_id", 0x1234),
        (DecoderStats(), "chunks_processed", 1),
    ],
)
def test_data_models_are_frozen(value: object, attribute: str, replacement: object):
    with pytest.raises(FrozenInstanceError):
        setattr(value, attribute, replacement)


def test_decode_context_expresses_authoritative_versions_and_identity():
    context = DecodeContext("BLUE", "L1", "BLUE-L1", 3, 4)

    assert context.team == "BLUE"
    assert context.target == "L1"
    assert context.profile == "BLUE-L1"
    assert context.target_version == 3
    assert context.context_version == 4


def test_rf_metrics_and_decoder_stats_expose_only_common_measurements_and_counts():
    assert [field.name for field in fields(RfMetrics)] == [
        "rms",
        "peak",
        "clipping_ratio",
        "sample_count",
    ]
    assert [field.name for field in fields(DecoderStats)] == [
        "chunks_processed",
        "samples_processed",
        "commands_emitted",
        "decode_errors",
        "resets",
    ]
    assert DecoderStats() == DecoderStats(0, 0, 0, 0, 0)


def test_decoded_command_has_complete_immutable_evidence_contract():
    source_evidence = {"sequence": 12}
    command = _decoded_command()
    command_from_source = DecodedCommand(
        **{
            **command.__dict__,
            "evidence": source_evidence,
        }
    )

    assert command.payload == b"ABC123"
    assert command.crc8_ok is True
    assert command.crc16_ok is True
    assert command.first_sample_index == 100
    assert command.last_sample_index == 199
    assert command.target == "L1"
    assert command.team == "BLUE"
    assert command.context_version == 7
    with pytest.raises(TypeError):
        command_from_source.evidence["sequence"] = 13
    source_evidence["sequence"] = 99
    assert command_from_source.evidence["sequence"] == 12


def test_decoded_command_requires_immutable_bytes_payload():
    command = _decoded_command()

    with pytest.raises(TypeError, match="bytes"):
        DecodedCommand(**{**command.__dict__, "payload": bytearray(b"ABC123")})


def test_reset_reason_values_are_stable_strings():
    assert {reason.value for reason in ResetReason} == {
        "startup",
        "context_change",
        "target_change",
        "device_reconnect",
        "manual",
    }


def test_decoder_plugin_is_a_protocol_with_decoder_id_attribute():
    assert DecoderPlugin._is_protocol is True
    assert get_type_hints(DecoderPlugin)["decoder_id"] is str


def test_decoder_plugin_method_signatures_are_exact():
    assert list(inspect.signature(DecoderPlugin.decode).parameters) == [
        "self",
        "chunk",
        "context",
    ]
    assert get_type_hints(DecoderPlugin.decode) == {
        "chunk": IqChunk,
        "context": DecodeContext,
        "return": list[DecodedCommand],
    }
    assert list(inspect.signature(DecoderPlugin.reset).parameters) == [
        "self",
        "reason",
        "context",
    ]
    assert get_type_hints(DecoderPlugin.reset) == {
        "reason": ResetReason,
        "context": DecodeContext,
        "return": type(None),
    }
    assert list(inspect.signature(DecoderPlugin.stats).parameters) == ["self"]
    assert get_type_hints(DecoderPlugin.stats) == {"return": DecoderStats}


def test_decoder_api_has_no_device_transport_or_ros_imports():
    tree = ast.parse(Path(decoder_api.__file__).read_text(encoding="utf-8"))
    imported_modules = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    }
    imported_modules.update(
        node.module or ""
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
    )

    imported_roots = {module.split(".", 1)[0] for module in imported_modules}
    assert not imported_roots.intersection({"adi", "pyadi_iio", "rclpy", "socket"})
    assert not any(
        module == "sdr_receiver" or ".msg" in module or module.endswith("_msgs")
        for module in imported_modules
    )
