from __future__ import annotations

import ast
from concurrent.futures import ThreadPoolExecutor
from dataclasses import FrozenInstanceError
from pathlib import Path
import threading
from types import MappingProxyType

import numpy as np
import pytest

from sdr_receiver_py_wrapper.command_validator import CommandValidator
from sdr_receiver_py_wrapper.models import DecodeContext, IqChunk, ResetReason
import sdr_receiver_py_wrapper.upstream_decoder as upstream_decoder
from sdr_receiver_py_wrapper.upstream_decoder import (
    UpstreamDecoder,
    VerifiedParsedFrame,
)


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


def verified_frame(
    *,
    cmd_id=0x0A06,
    data=b"ABC123",
    seq=1,
    crc8_ok=True,
    crc16_ok=True,
    crc_mode="kermit-x3014",
):
    return VerifiedParsedFrame(
        cmd_id=cmd_id,
        data=data,
        seq=seq,
        crc8_ok=crc8_ok,
        crc16_ok=crc16_ok,
        crc_mode=crc_mode,
    )


def released_memoryview() -> memoryview:
    value = memoryview(b"released")
    value.release()
    return value


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


def test_verified_parsed_frame_contract_exists_and_is_frozen():
    frame_type = getattr(upstream_decoder, "VerifiedParsedFrame", None)

    assert frame_type is not None
    frame = frame_type(
        cmd_id=0x0A06,
        data=b"ABC123",
        seq=1,
        crc8_ok=True,
        crc16_ok=True,
        crc_mode="kermit-x3014",
    )
    with pytest.raises(FrozenInstanceError):
        frame.seq = 2


@pytest.mark.parametrize(
    ("team", "target", "center_freq", "level"),
    [
        ("RED", "L1", 432_200_000, 1),
        ("RED", "L2", 432_500_000, 2),
        ("RED", "L3", 432_800_000, 3),
        ("BLUE", "L1", 434_920_000, 1),
        ("BLUE", "L2", 434_620_000, 2),
        ("BLUE", "L3", 434_320_000, 3),
    ],
)
def test_reset_selects_each_explicit_profile(team, target, center_freq, level):
    decoder = UpstreamDecoder()

    decoder.reset(ResetReason.TARGET_CHANGE, make_context(team, target))

    assert decoder.active_profile.name == f"{team}-{target}"
    assert decoder.active_profile.team == team
    assert decoder.active_profile.target == target
    assert decoder.active_profile.center_freq == center_freq
    assert decoder.active_profile.level == level
    assert decoder.stats().resets == 1


def test_profile_lookup_table_is_read_only():
    profiles = upstream_decoder._PROFILE_FREQUENCIES

    assert isinstance(profiles, MappingProxyType)
    with pytest.raises(TypeError):
        profiles[("BLUE", "L1")] = (1, 1)


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


@pytest.mark.parametrize(
    ("reason", "context", "message"),
    [
        ("manual", make_context(), "reason"),
        (ResetReason.MANUAL, None, "context"),
        (
            ResetReason.MANUAL,
            DecodeContext(None, "L1", "competition", 3, 4),
            "team",
        ),
        (
            ResetReason.MANUAL,
            DecodeContext("BLUE", 1, "competition", 3, 4),
            "target",
        ),
        (
            ResetReason.MANUAL,
            DecodeContext("BLUE", "L1", None, 3, 4),
            "profile",
        ),
        (
            ResetReason.MANUAL,
            DecodeContext("BLUE", "L1", "   ", 3, 4),
            "profile",
        ),
    ],
)
def test_reset_input_failure_is_counted_and_preserves_profile(
    reason,
    context,
    message,
):
    decoder = UpstreamDecoder()
    decoder.reset(ResetReason.STARTUP, make_context())
    previous = decoder.active_profile

    with pytest.raises((TypeError, ValueError), match=message):
        decoder.reset(reason, context)

    assert decoder.active_profile is previous
    assert decoder.stats().resets == 1
    assert decoder.stats().decode_errors == 1


@pytest.mark.parametrize("failure_mode", ["noncallable", "getter"])
def test_reset_hook_resolution_failure_is_transactional(failure_mode):
    class Backend:
        mode = "ok"

        @property
        def reset(self):
            if self.mode == "getter":
                raise RuntimeError("reset hook getter failed")
            if self.mode == "noncallable":
                return 3
            return lambda **_keywords: None

    backend = Backend()
    decoder = UpstreamDecoder(backend=backend)
    decoder.reset(ResetReason.STARTUP, make_context())
    previous = decoder.active_profile
    backend.mode = failure_mode

    with pytest.raises((TypeError, RuntimeError), match="reset"):
        decoder.reset(ResetReason.TARGET_CHANGE, make_context("RED", "L2"))

    assert decoder.active_profile is previous
    assert decoder.stats().resets == 1
    assert decoder.stats().decode_errors == 1


def test_reset_normalizes_team_target_and_runtime_profile_text():
    decoder = UpstreamDecoder()
    context = DecodeContext(
        team=" blue ",
        target=" l1 ",
        profile=" competition ",
        target_version=3,
        context_version=4,
    )

    decoder.reset(ResetReason.STARTUP, context)

    assert decoder.active_profile.name == "BLUE-L1"


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


def test_present_noncallable_decode_hook_does_not_fall_back_to_call():
    class Backend:
        decode = 3
        called = False

        def __call__(self, **_keywords):
            self.called = True
            return ()

    backend = Backend()
    decoder = UpstreamDecoder(backend=backend)
    context = make_context(profile="competition")
    decoder.reset(ResetReason.STARTUP, context)

    with pytest.raises(TypeError, match="decode hook"):
        decoder.decode(make_chunk(), context)

    assert backend.called is False
    assert decoder.stats().decode_errors == 1


def test_decode_hook_getter_failure_is_counted():
    class Backend:
        @property
        def decode(self):
            raise RuntimeError("decode hook getter failed")

    decoder = UpstreamDecoder(backend=Backend())
    context = make_context(profile="debug")
    decoder.reset(ResetReason.STARTUP, context)

    with pytest.raises(RuntimeError, match="decode hook getter failed"):
        decoder.decode(make_chunk(), context)

    assert decoder.stats().decode_errors == 1
    assert decoder.stats().chunks_processed == 0


@pytest.mark.parametrize(
    ("context", "message"),
    [
        (None, "DecodeContext"),
        (DecodeContext(None, "L1", "competition", 3, 4), "team"),
        (DecodeContext("BLUE", 1, "competition", 3, 4), "target"),
        (DecodeContext("BLUE", "L1", None, 3, 4), "profile"),
        (DecodeContext("BLUE", "L1", "   ", 3, 4), "profile"),
    ],
)
def test_decode_context_uses_exact_normalized_text_contract(context, message):
    backend = RecordingBackend()
    decoder = UpstreamDecoder(backend=backend)
    decoder.reset(
        ResetReason.STARTUP,
        make_context(profile="competition"),
    )

    with pytest.raises((TypeError, ValueError), match=message):
        decoder.decode(make_chunk(), context)

    assert backend.decode_calls == []
    assert decoder.stats().decode_errors == 1


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
    decoder.decode(chunk, context)

    assert len(backend.decode_calls) == 2
    call = backend.decode_calls[0]
    assert set(call) == {"samples", "sample_rate_hz", "profile"}
    assert call["samples"] is not chunk.samples
    assert call["samples"] is not backend.decode_calls[1]["samples"]
    assert np.array_equal(call["samples"], chunk.samples)
    assert call["samples"].dtype == np.complex64
    assert call["samples"].flags.owndata is True
    assert call["samples"].flags.c_contiguous is True
    assert call["samples"].flags.writeable is False
    assert call["sample_rate_hz"] == chunk.sample_rate_hz
    assert call["profile"] is decoder.active_profile
    assert not isinstance(call["profile"], IqChunk)


def test_malicious_backend_cannot_mutate_shared_chunk_samples():
    class MutatingBackend:
        def decode(self, *, samples, sample_rate_hz, profile):
            samples.flags.writeable = True
            samples[:] = np.complex64(9 + 4j)
            return ()

    chunk = make_chunk(np.array([1 + 2j, 3 + 4j], dtype=np.complex64))
    before = chunk.samples.tobytes()
    decoder = UpstreamDecoder(backend=MutatingBackend())
    context = make_context(profile="competition")
    decoder.reset(ResetReason.STARTUP, context)

    assert decoder.decode(chunk, context) == []

    assert chunk.samples.tobytes() == before
    assert chunk.samples.flags.writeable is False


def test_multiple_parsed_frames_are_converted_with_complete_metadata():
    mutable_data = bytearray(b"ABC123")
    backend = RecordingBackend(
        [
            verified_frame(cmd_id=0x0A06, data=mutable_data, seq=41),
            verified_frame(
                cmd_id=0x0A02,
                data=memoryview(b"payload"),
                seq=42,
            ),
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
        {"upstream_seq": 41, "level": 1},
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


@pytest.mark.parametrize(
    ("frame", "message"),
    [
        (verified_frame(cmd_id=None, data=b"x", seq=1), "cmd_id"),
        (verified_frame(cmd_id=True, data=b"x", seq=1), "cmd_id"),
        (verified_frame(cmd_id=-1, data=b"x", seq=1), "cmd_id"),
        (verified_frame(cmd_id=0x1_0000, data=b"x", seq=1), "cmd_id"),
        (verified_frame(cmd_id=1, data=b"x", seq=None), "seq"),
        (verified_frame(cmd_id=1, data=b"x", seq=True), "seq"),
        (verified_frame(cmd_id=1, data=b"x", seq=-1), "seq"),
        (verified_frame(cmd_id=1, data=b"x", seq=256), "seq"),
        (verified_frame(cmd_id=1, data=None, seq=1), "data"),
        (verified_frame(cmd_id=1, data=3, seq=1), "data"),
        (verified_frame(cmd_id=1, data="x", seq=1), "data"),
        (verified_frame(cmd_id=1, data=[1], seq=1), "data"),
        (
            verified_frame(
                cmd_id=1,
                data=memoryview(np.array([True], dtype=np.bool_)),
                seq=1,
            ),
            "data",
        ),
    ],
)
def test_invalid_frame_fields_reject_the_entire_chunk(frame, message):
    backend = RecordingBackend(
        [verified_frame(cmd_id=1, data=b"valid-first", seq=1), frame]
    )
    decoder = UpstreamDecoder(backend=backend)
    context = make_context(profile="competition")
    decoder.reset(ResetReason.STARTUP, context)

    with pytest.raises((TypeError, ValueError), match=message):
        decoder.decode(make_chunk(), context)

    stats = decoder.stats()
    assert stats.decode_errors == 1
    assert stats.chunks_processed == 0
    assert stats.samples_processed == 0
    assert stats.commands_emitted == 0


def test_unverified_frame_is_rejected_without_reading_properties():
    class BadFrame:
        property_read = False

        @property
        def cmd_id(self):
            self.property_read = True
            raise RuntimeError("unverified property must not be read")

    frame = BadFrame()
    decoder = UpstreamDecoder(backend=RecordingBackend([frame]))
    context = make_context(profile="debug")
    decoder.reset(ResetReason.STARTUP, context)

    with pytest.raises(TypeError, match="VerifiedParsedFrame"):
        decoder.decode(make_chunk(), context)

    assert frame.property_read is False
    stats = decoder.stats()
    assert stats.decode_errors == 1
    assert stats.chunks_processed == 0
    assert stats.samples_processed == 0
    assert stats.commands_emitted == 0


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"crc8_ok": False}, "crc8_ok"),
        ({"crc8_ok": 1}, "crc8_ok"),
        ({"crc16_ok": False}, "crc16_ok"),
        ({"crc16_ok": 1}, "crc16_ok"),
        ({"crc_mode": "other"}, "crc_mode"),
    ],
)
def test_verified_frame_crc_claim_must_be_exact(overrides, message):
    values = {
        "cmd_id": 0x0A06,
        "data": b"ABC123",
        "seq": 1,
        "crc8_ok": True,
        "crc16_ok": True,
        "crc_mode": "kermit-x3014",
    }
    values.update(overrides)
    decoder = UpstreamDecoder(
        backend=RecordingBackend([VerifiedParsedFrame(**values)])
    )
    context = make_context(profile="competition")
    decoder.reset(ResetReason.STARTUP, context)

    with pytest.raises((TypeError, ValueError), match=message):
        decoder.decode(make_chunk(), context)

    stats = decoder.stats()
    assert stats.decode_errors == 1
    assert stats.chunks_processed == 0
    assert stats.commands_emitted == 0


@pytest.mark.parametrize(
    "data",
    [
        bytes(257),
        bytearray(257),
        memoryview(bytearray(257)),
        memoryview(bytearray(4)).cast("B", shape=(2, 2)),
        memoryview(bytearray(range(4)))[::2],
        released_memoryview(),
    ],
)
def test_payload_resource_and_memoryview_contract_is_bounded(data):
    decoder = UpstreamDecoder(
        backend=RecordingBackend([verified_frame(data=data)])
    )
    context = make_context(profile="competition")
    decoder.reset(ResetReason.STARTUP, context)

    with pytest.raises(ValueError):
        decoder.decode(make_chunk(), context)

    stats = decoder.stats()
    assert stats.decode_errors == 1
    assert stats.chunks_processed == 0
    assert stats.commands_emitted == 0


def test_frame_iterable_is_bounded_without_overconsumption():
    class FrameStream:
        yielded = 0

        def __iter__(self):
            while True:
                self.yielded += 1
                if self.yielded > 65:
                    raise AssertionError("decoder over-consumed frame stream")
                yield verified_frame(cmd_id=1, data=b"x", seq=1)

    stream = FrameStream()
    decoder = UpstreamDecoder(backend=RecordingBackend(stream))
    context = make_context(profile="debug")
    decoder.reset(ResetReason.STARTUP, context)

    with pytest.raises(ValueError, match="at most 64"):
        decoder.decode(make_chunk(), context)

    assert stream.yielded == 65
    stats = decoder.stats()
    assert stats.decode_errors == 1
    assert stats.chunks_processed == 0
    assert stats.commands_emitted == 0


def test_jam_command_from_verified_frame_is_accepted_by_validator():
    decoder = UpstreamDecoder(
        backend=RecordingBackend(
            [verified_frame(cmd_id=0x0A06, data=b"ABC123", seq=9)]
        )
    )
    context = make_context("BLUE", "L2", profile="competition")
    decoder.reset(ResetReason.STARTUP, context)

    command = decoder.decode(make_chunk(), context)[0]
    result = CommandValidator().validate(command)

    assert result.accepted is True
    assert result.level == 2
    assert dict(command.evidence) == {"upstream_seq": 9, "level": 2}


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


@pytest.mark.parametrize("runtime_profile", ["competition", "debug"])
def test_runtime_profile_is_distinct_from_internal_rf_profile(runtime_profile):
    backend = RecordingBackend(
        [verified_frame(cmd_id=1, data=b"ok", seq=1)]
    )
    decoder = UpstreamDecoder(backend=backend)
    context = make_context("BLUE", "L1", profile=runtime_profile)
    decoder.reset(ResetReason.STARTUP, context)

    commands = decoder.decode(make_chunk(), context)

    assert decoder.active_profile.name == "BLUE-L1"
    assert commands[0].profile == "BLUE-L1"
    assert context.profile == runtime_profile


def test_plain_callable_can_be_injected_as_the_pure_decode_backend():
    calls = []

    def backend(*, samples, sample_rate_hz, profile):
        calls.append((samples, sample_rate_hz, profile))
        return [verified_frame(cmd_id=9, data=b"ok", seq=5)]

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


def test_decode_operations_do_not_reenter_backend():
    first_entered = threading.Event()
    release_first = threading.Event()
    second_started = threading.Event()
    second_entered = threading.Event()

    class Backend:
        calls = 0

        def decode(self, *, samples, sample_rate_hz, profile):
            self.calls += 1
            if self.calls == 1:
                first_entered.set()
                assert release_first.wait(2.0)
            else:
                second_entered.set()
            return ()

    decoder = UpstreamDecoder(backend=Backend())
    context = make_context(profile="competition")
    decoder.reset(ResetReason.STARTUP, context)

    def second_decode():
        second_started.set()
        return decoder.decode(make_chunk(), context)

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(decoder.decode, make_chunk(), context)
        assert first_entered.wait(2.0)
        second = pool.submit(second_decode)
        assert second_started.wait(2.0)
        assert not second_entered.wait(0.1)
        release_first.set()
        assert first.result(timeout=2.0) == []
        assert second.result(timeout=2.0) == []


def test_decode_and_reset_are_serialized_with_consistent_profile():
    decode_entered = threading.Event()
    release_decode = threading.Event()
    reset_started = threading.Event()
    reset_entered = threading.Event()

    class Backend:
        block_decode = False

        def reset(self, *, reason, profile):
            if self.block_decode:
                reset_entered.set()

        def decode(self, *, samples, sample_rate_hz, profile):
            decode_entered.set()
            assert release_decode.wait(2.0)
            return [
                verified_frame(cmd_id=0x0A02, data=b"old-profile", seq=7)
            ]

    backend = Backend()
    decoder = UpstreamDecoder(backend=backend)
    old_context = make_context("BLUE", "L1", profile="competition")
    new_context = make_context("RED", "L2", profile="competition")
    decoder.reset(ResetReason.STARTUP, old_context)
    backend.block_decode = True

    def reset_to_new_profile():
        reset_started.set()
        decoder.reset(ResetReason.TARGET_CHANGE, new_context)

    with ThreadPoolExecutor(max_workers=2) as pool:
        decoding = pool.submit(decoder.decode, make_chunk(), old_context)
        assert decode_entered.wait(2.0)
        resetting = pool.submit(reset_to_new_profile)
        assert reset_started.wait(2.0)
        assert not reset_entered.wait(0.1)
        release_decode.set()
        commands = decoding.result(timeout=2.0)
        resetting.result(timeout=2.0)

    assert commands[0].payload == b"old-profile"
    assert commands[0].profile == "BLUE-L1"
    assert decoder.active_profile.name == "RED-L2"


def test_reset_operations_commit_in_call_order_without_reentry():
    first_entered = threading.Event()
    release_first = threading.Event()
    second_started = threading.Event()
    second_entered = threading.Event()

    class Backend:
        calls = []

        def reset(self, *, reason, profile):
            self.calls.append(profile.name)
            if len(self.calls) == 1:
                first_entered.set()
                assert release_first.wait(2.0)
            else:
                second_entered.set()

    backend = Backend()
    decoder = UpstreamDecoder(backend=backend)

    def second_reset():
        second_started.set()
        decoder.reset(
            ResetReason.TARGET_CHANGE,
            make_context("RED", "L2", profile="competition"),
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(
            decoder.reset,
            ResetReason.STARTUP,
            make_context("BLUE", "L1", profile="competition"),
        )
        assert first_entered.wait(2.0)
        second = pool.submit(second_reset)
        assert second_started.wait(2.0)
        assert not second_entered.wait(0.1)
        release_first.set()
        first.result(timeout=2.0)
        second.result(timeout=2.0)

    assert backend.calls == ["BLUE-L1", "RED-L2"]
    assert decoder.active_profile.name == "RED-L2"
    assert decoder.stats().resets == 2


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
