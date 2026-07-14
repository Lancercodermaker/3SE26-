from __future__ import annotations

import ast
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, FrozenInstanceError
from pathlib import Path

import numpy as np
import pytest

from sdr_receiver_py_wrapper.models import DecodeContext, IqChunk, ResetReason
import sdr_receiver_py_wrapper.upstream_decoder as upstream_decoder
from sdr_receiver_py_wrapper.upstream_decoder import UpstreamDecoder


def make_context(
    team: str = "BLUE",
    target: str = "L1",
    profile: str | None = None,
) -> DecodeContext:
    normalized_team = team.strip().upper()
    normalized_target = target.strip().upper()
    return DecodeContext(
        team=team,
        target=target,
        profile=profile or f"{normalized_team}-{normalized_target}",
        target_version=3,
        context_version=4,
    )


def make_chunk(samples: np.ndarray | None = None) -> IqChunk:
    owned = (
        np.zeros(8, dtype=np.complex64)
        if samples is None
        else np.asarray(samples, dtype=np.complex64).copy()
    )
    owned.flags.writeable = False
    return IqChunk(
        chunk_id=7,
        first_sample_index=100,
        samples=owned,
        sample_rate_hz=2_500_000,
        rx_wall_time=12.5,
        rx_monotonic_ns=99,
        lo_hz=434_920_000,
        rf_bandwidth_hz=940_000,
        rx_gain_db=22,
        target_version=3,
        context_version=4,
    )


@dataclass
class ParsedFrame:
    cmd_id: int
    data: object
    seq: int


class RecordingBackend:
    def __init__(self, frames=()):
        self.frames = frames
        self.decode_calls = []

    def decode(self, *, samples, sample_rate_hz, profile):
        self.decode_calls.append(
            {
                "samples": samples,
                "sample_rate_hz": sample_rate_hz,
                "profile": profile,
            }
        )
        return self.frames


def test_plugin_has_stable_identity_and_no_hardware_or_transport_ownership():
    decoder = UpstreamDecoder()

    assert decoder.decoder_id == "combat_radar_sdr_13b13a6"
    assert callable(decoder.decode)
    assert callable(decoder.reset)
    assert callable(decoder.stats)
    for forbidden in ("sdr", "server_comm", "device", "transport"):
        assert not hasattr(decoder, forbidden)


@pytest.mark.parametrize(
    ("team", "target", "center_freq"),
    [
        ("RED", "L1", 432_200_000),
        ("RED", "L2", 432_500_000),
        ("RED", "L3", 432_800_000),
        ("BLUE", "L1", 434_920_000),
        ("BLUE", "L2", 434_620_000),
        ("BLUE", "L3", 434_320_000),
    ],
)
def test_reset_selects_each_explicit_profile(team, target, center_freq):
    decoder = UpstreamDecoder()

    decoder.reset(ResetReason.TARGET_CHANGE, make_context(team, target))

    assert decoder.active_profile.name == f"{team}-{target}"
    assert decoder.active_profile.team == team
    assert decoder.active_profile.target == target
    assert decoder.active_profile.center_freq == center_freq
    assert decoder.stats().resets == 1


def test_profile_lookup_strips_whitespace_and_normalizes_case():
    decoder = UpstreamDecoder()

    decoder.reset(ResetReason.TARGET_CHANGE, make_context(" blue ", " l1 "))

    assert decoder.active_profile.name == "BLUE-L1"
    assert decoder.active_profile.center_freq == 434_920_000


@pytest.mark.parametrize(
    ("team", "target"),
    [
        ("", "L1"),
        ("GREEN", "L1"),
        ("BLUE", ""),
        ("BLUE", "L4"),
    ],
)
def test_unknown_profile_values_raise_without_defaulting(team, target):
    decoder = UpstreamDecoder()

    with pytest.raises(ValueError, match="unsupported upstream profile"):
        decoder.reset(ResetReason.TARGET_CHANGE, make_context(team, target))

    assert decoder.active_profile is None
    assert decoder.stats().resets == 0
    assert decoder.stats().decode_errors == 1


def test_active_profile_is_immutable():
    decoder = UpstreamDecoder()
    decoder.reset(ResetReason.TARGET_CHANGE, make_context())

    with pytest.raises(FrozenInstanceError):
        decoder.active_profile.center_freq = 1


def test_reset_calls_optional_pure_backend_hook_before_committing_profile():
    calls = []

    class Backend:
        def reset(self, *, reason, profile):
            calls.append((reason, profile))

    decoder = UpstreamDecoder(backend=Backend())
    decoder.reset(ResetReason.TARGET_CHANGE, make_context())

    assert calls == [(ResetReason.TARGET_CHANGE, decoder.active_profile)]
    assert decoder.stats().resets == 1
    assert decoder.stats().decode_errors == 0


def test_backend_reset_failure_preserves_previous_profile_transactionally():
    class Backend:
        fail = False

        def reset(self, *, reason, profile):
            if self.fail:
                raise RuntimeError("backend reset failed")

    backend = Backend()
    decoder = UpstreamDecoder(backend=backend)
    decoder.reset(ResetReason.STARTUP, make_context("BLUE", "L1"))
    previous = decoder.active_profile
    backend.fail = True

    with pytest.raises(RuntimeError, match="backend reset failed"):
        decoder.reset(
            ResetReason.TARGET_CHANGE,
            make_context("RED", "L2"),
        )

    assert decoder.active_profile is previous
    assert decoder.stats().resets == 1
    assert decoder.stats().decode_errors == 1


def test_decode_before_reset_fails_without_calling_backend():
    backend = RecordingBackend()
    decoder = UpstreamDecoder(backend=backend)

    with pytest.raises(RuntimeError, match="reset.*before decode"):
        decoder.decode(make_chunk(), make_context())

    assert backend.decode_calls == []
    assert decoder.stats().decode_errors == 1
    assert decoder.stats().chunks_processed == 0


def test_missing_backend_fails_with_clear_configuration_error():
    decoder = UpstreamDecoder()
    decoder.reset(ResetReason.STARTUP, make_context())

    with pytest.raises(RuntimeError, match="backend is unavailable"):
        decoder.decode(make_chunk(), make_context())

    assert decoder.stats().decode_errors == 1
    assert decoder.stats().chunks_processed == 0


@pytest.mark.parametrize(
    ("samples", "message"),
    [
        (np.zeros(0, dtype=np.complex64), "must not be empty"),
        (np.array([complex(np.nan, 0.0)], dtype=np.complex64), "finite"),
        (np.array([complex(0.0, np.inf)], dtype=np.complex64), "finite"),
    ],
)
def test_invalid_iq_is_rejected_before_backend(samples, message):
    backend = RecordingBackend()
    decoder = UpstreamDecoder(backend=backend)
    decoder.reset(ResetReason.STARTUP, make_context())

    with pytest.raises(ValueError, match=message):
        decoder.decode(make_chunk(samples), make_context())

    assert backend.decode_calls == []
    stats = decoder.stats()
    assert stats.decode_errors == 1
    assert stats.chunks_processed == 0
    assert stats.samples_processed == 0
    assert stats.commands_emitted == 0


def test_backend_receives_only_iq_and_necessary_pure_values():
    backend = RecordingBackend()
    decoder = UpstreamDecoder(backend=backend)
    context = make_context()
    chunk = make_chunk()
    decoder.reset(ResetReason.STARTUP, context)

    decoder.decode(chunk, context)

    assert len(backend.decode_calls) == 1
    call = backend.decode_calls[0]
    assert set(call) == {"samples", "sample_rate_hz", "profile"}
    assert call["samples"] is chunk.samples
    assert call["samples"].flags.writeable is False
    assert call["sample_rate_hz"] == chunk.sample_rate_hz
    assert call["profile"] is decoder.active_profile
    assert not isinstance(call["profile"], IqChunk)


def test_multiple_parsed_frames_are_converted_with_complete_metadata():
    mutable_data = bytearray(b"ABC123")
    backend = RecordingBackend(
        [
            ParsedFrame(cmd_id=0x0A06, data=mutable_data, seq=41),
            ParsedFrame(cmd_id=0x0A02, data=memoryview(b"payload"), seq=42),
        ]
    )
    decoder = UpstreamDecoder(backend=backend)
    context = make_context("BLUE", "L1")
    chunk = make_chunk()
    decoder.reset(ResetReason.TARGET_CHANGE, context)

    commands = decoder.decode(chunk, context)
    mutable_data[:] = b"XXXXXX"

    assert [command.cmd_id for command in commands] == [0x0A06, 0x0A02]
    assert [command.payload for command in commands] == [b"ABC123", b"payload"]
    assert [dict(command.evidence) for command in commands] == [
        {"upstream_seq": 41},
        {"upstream_seq": 42},
    ]
    for command in commands:
        assert command.decoder_id == "combat_radar_sdr_13b13a6"
        assert command.profile == "BLUE-L1"
        assert command.crc8_ok is True
        assert command.crc16_ok is True
        assert command.crc_mode == "kermit-x3014"
        assert command.first_sample_index == 100
        assert command.last_sample_index == 107
        assert command.receive_wall_time == 12.5
        assert command.target == "L1"
        assert command.team == "BLUE"
        assert command.context_version == 4
    stats = decoder.stats()
    assert stats.chunks_processed == 1
    assert stats.samples_processed == 8
    assert stats.commands_emitted == 2
    assert stats.decode_errors == 0


def test_empty_backend_result_is_a_successful_chunk():
    decoder = UpstreamDecoder(backend=RecordingBackend())
    context = make_context()
    decoder.reset(ResetReason.STARTUP, context)

    assert decoder.decode(make_chunk(), context) == []

    stats = decoder.stats()
    assert stats.chunks_processed == 1
    assert stats.samples_processed == 8
    assert stats.commands_emitted == 0
    assert stats.decode_errors == 0


def test_backend_decode_error_is_counted_and_propagated():
    class Backend:
        def decode(self, *, samples, sample_rate_hz, profile):
            raise RuntimeError("backend decode failed")

    decoder = UpstreamDecoder(backend=Backend())
    context = make_context()
    decoder.reset(ResetReason.STARTUP, context)

    with pytest.raises(RuntimeError, match="backend decode failed"):
        decoder.decode(make_chunk(), context)

    stats = decoder.stats()
    assert stats.decode_errors == 1
    assert stats.chunks_processed == 0
    assert stats.samples_processed == 0
    assert stats.commands_emitted == 0


@pytest.mark.parametrize(
    "context",
    [
        make_context("RED", "L1"),
        make_context("BLUE", "L2"),
        make_context("BLUE", "L1", profile="stale-profile"),
    ],
)
def test_decode_rejects_context_that_does_not_match_active_profile(context):
    backend = RecordingBackend()
    decoder = UpstreamDecoder(backend=backend)
    decoder.reset(ResetReason.STARTUP, make_context("BLUE", "L1"))

    with pytest.raises(ValueError, match="does not match active profile"):
        decoder.decode(make_chunk(), context)

    assert backend.decode_calls == []
    assert decoder.stats().decode_errors == 1


def test_plain_callable_can_be_injected_as_the_pure_decode_backend():
    calls = []

    def backend(*, samples, sample_rate_hz, profile):
        calls.append((samples, sample_rate_hz, profile))
        return [ParsedFrame(cmd_id=9, data=b"ok", seq=5)]

    decoder = UpstreamDecoder(backend=backend)
    context = make_context()
    decoder.reset(ResetReason.STARTUP, context)

    commands = decoder.decode(make_chunk(), context)

    assert [command.payload for command in commands] == [b"ok"]
    assert len(calls) == 1


def test_stats_updates_are_thread_safe_under_concurrent_decode_calls():
    class StatelessBackend:
        def decode(self, *, samples, sample_rate_hz, profile):
            return ()

    decoder = UpstreamDecoder(backend=StatelessBackend())
    context = make_context()
    chunk = make_chunk()
    decoder.reset(ResetReason.STARTUP, context)

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(
            pool.map(lambda _index: decoder.decode(chunk, context), range(64))
        )

    assert results == [[] for _index in range(64)]
    stats = decoder.stats()
    assert stats.chunks_processed == 64
    assert stats.samples_processed == 64 * 8
    assert stats.commands_emitted == 0
    assert stats.decode_errors == 0
    assert stats.resets == 1


def test_source_has_no_hardware_ros_network_or_tuning_dependencies():
    source_path = Path(upstream_decoder.__file__)
    tree = ast.parse(source_path.read_text(encoding="utf-8"))
    imported_roots = set()
    attributes = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_roots.update(
                alias.name.partition(".")[0] for alias in node.names
            )
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported_roots.add(node.module.partition(".")[0])
        elif isinstance(node, ast.Attribute):
            attributes.add(node.attr)

    assert imported_roots.isdisjoint({"adi", "rclpy", "socket"})
    assert attributes.isdisjoint(
        {
            "sdr",
            "server_comm",
            "device",
            "transport",
            "lo_hz",
            "rx_gain_db",
        }
    )
    source = source_path.read_text(encoding="utf-8")
    assert "combat_radar_sdr.checkout" not in source
    assert "fetch_upstream" not in source
