from __future__ import annotations

import ast
from concurrent.futures import ThreadPoolExecutor
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


_INVALID_VERIFIED_FRAME_CASES = [
    pytest.param({"cmd_id": None}, "cmd_id", id="cmd-none"),
    pytest.param({"cmd_id": True}, "cmd_id", id="cmd-bool"),
    pytest.param({"cmd_id": -1}, "cmd_id", id="cmd-negative"),
    pytest.param({"cmd_id": 0x1_0000}, "cmd_id", id="cmd-large"),
    pytest.param({"seq": None}, "seq", id="seq-none"),
    pytest.param({"seq": True}, "seq", id="seq-bool"),
    pytest.param({"seq": -1}, "seq", id="seq-negative"),
    pytest.param({"seq": 256}, "seq", id="seq-large"),
    pytest.param({"data": None}, "exact bytes", id="data-none"),
    pytest.param({"data": 3}, "exact bytes", id="data-int"),
    pytest.param({"data": "x"}, "exact bytes", id="data-str"),
    pytest.param({"data": [1]}, "exact bytes", id="data-list"),
    pytest.param(
        {"data": bytearray(b"ABC123")},
        "exact bytes",
        id="data-bytearray",
    ),
    pytest.param(
        {"data": memoryview(bytearray(b"ABC123"))},
        "exact bytes",
        id="data-memoryview",
    ),
    pytest.param(
        {"data": type("BytesSubclass", (bytes,), {})(b"ABC123")},
        "exact bytes",
        id="data-bytes-subclass",
    ),
    pytest.param({"data": bytes(257)}, "payload", id="data-large-bytes"),
    pytest.param(
        {"data": bytearray(257)},
        "exact bytes",
        id="data-large-bytearray",
    ),
    pytest.param(
        {"data": memoryview(bytearray(257))},
        "exact bytes",
        id="data-large-memoryview",
    ),
    pytest.param(
        {"data": memoryview(bytearray(4)).cast("B", shape=(2, 2))},
        "exact bytes",
        id="data-multidimensional-memoryview",
    ),
    pytest.param(
        {"data": memoryview(bytearray(range(4)))[::2]},
        "exact bytes",
        id="data-noncontiguous-memoryview",
    ),
    pytest.param(
        {"data": released_memoryview()},
        "exact bytes",
        id="data-released-memoryview",
    ),
    pytest.param({"crc8_ok": False}, "crc8_ok", id="crc8-false"),
    pytest.param({"crc8_ok": 1}, "crc8_ok", id="crc8-int"),
    pytest.param({"crc16_ok": False}, "crc16_ok", id="crc16-false"),
    pytest.param({"crc16_ok": 1}, "crc16_ok", id="crc16-int"),
    pytest.param({"crc_mode": "other"}, "crc_mode", id="crc-mode-value"),
    pytest.param(
        {
            "crc_mode": type("StrSubclass", (str,), {})(
                "kermit-x3014"
            )
        },
        "crc_mode",
        id="crc-mode-subclass",
    ),
]


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


class LockBoundaryProbe:
    """Signals each attempt before delegating to a real non-reentrant lock."""

    def __init__(self):
        self._lock = threading.Lock()
        self._counter_lock = threading.Lock()
        self._boundaries = []

    def boundary(self, index):
        with self._counter_lock:
            while len(self._boundaries) <= index:
                self._boundaries.append(threading.Event())
            return self._boundaries[index]

    def __enter__(self):
        with self._counter_lock:
            index = getattr(self, "_attempts", 0)
            self._attempts = index + 1
            while len(self._boundaries) <= index:
                self._boundaries.append(threading.Event())
            boundary = self._boundaries[index]
        boundary.set()
        self._lock.acquire()
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self._lock.release()


def test_plugin_has_stable_identity_and_no_hardware_or_transport_ownership():
    decoder = UpstreamDecoder()

    assert decoder.decoder_id == "combat_radar_sdr_13b13a6"
    assert callable(decoder.decode)
    assert callable(decoder.reset)
    assert callable(decoder.stats)
    for forbidden in ("sdr", "server_comm", "device", "transport"):
        assert not hasattr(decoder, forbidden)


def test_verified_parsed_frame_contract_is_truly_immutable():
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
    with pytest.raises(AttributeError):
        frame.seq = 2
    with pytest.raises(AttributeError):
        object.__setattr__(frame, "data", b"detached-from-crc")


@pytest.mark.parametrize(
    "data",
    [
        bytearray(b"ABC123"),
        memoryview(bytearray(b"ABC123")),
        type("BytesSubclass", (bytes,), {})(b"ABC123"),
    ],
)
def test_verified_frame_rejects_mutable_or_subclassed_payload(data):
    with pytest.raises(TypeError, match="exact bytes"):
        verified_frame(data=data)


def test_verified_frame_accepts_exact_immutable_bytes():
    payload = b"ABC123"

    frame = verified_frame(data=payload)

    assert frame.data is payload


@pytest.mark.parametrize(
    ("overrides", "error_type"),
    [
        pytest.param({"crc8_ok": 1}, TypeError, id="crc8-wrong-type"),
        pytest.param({"crc8_ok": False}, ValueError, id="crc8-false"),
        pytest.param({"crc16_ok": 1}, TypeError, id="crc16-wrong-type"),
        pytest.param({"crc16_ok": False}, ValueError, id="crc16-false"),
        pytest.param({"crc_mode": None}, TypeError, id="mode-wrong-type"),
        pytest.param(
            {"crc_mode": type("StrSubclass", (str,), {})("kermit-x3014")},
            TypeError,
            id="mode-subclass",
        ),
        pytest.param(
            {"crc_mode": "other"},
            ValueError,
            id="mode-wrong-value",
        ),
    ],
)
def test_verified_frame_crc_errors_distinguish_type_from_value(
    overrides,
    error_type,
):
    with pytest.raises(error_type):
        verified_frame(**overrides)


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


def test_active_profile_is_truly_immutable_through_getter():
    decoder = UpstreamDecoder()
    decoder.reset(ResetReason.TARGET_CHANGE, make_context())
    external_profile = decoder.active_profile

    with pytest.raises(AttributeError):
        object.__setattr__(external_profile, "center_freq", 1)

    assert decoder.active_profile.center_freq == 434_920_000


def test_malicious_reset_hook_cannot_mutate_committed_profile():
    class Backend:
        mutation_error = None

        def reset(self, *, reason, profile):
            try:
                object.__setattr__(profile, "level", 99)
            except AttributeError as error:
                self.mutation_error = error

    backend = Backend()
    decoder = UpstreamDecoder(backend=backend)

    decoder.reset(ResetReason.STARTUP, make_context("BLUE", "L1"))

    assert isinstance(backend.mutation_error, AttributeError)
    assert decoder.active_profile.name == "BLUE-L1"
    assert decoder.active_profile.level == 1


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


def test_malicious_decode_hook_cannot_mutate_profile_or_jam_metadata():
    class Backend:
        mutation_error = None

        def decode(self, *, samples, sample_rate_hz, profile):
            try:
                object.__setattr__(profile, "name", "RED-L3")
            except AttributeError as error:
                self.mutation_error = error
            return [verified_frame(cmd_id=0x0A06, seq=9)]

    backend = Backend()
    decoder = UpstreamDecoder(backend=backend)
    context = make_context("BLUE", "L2", profile="competition")
    decoder.reset(ResetReason.STARTUP, context)

    command = decoder.decode(make_chunk(), context)[0]
    result = CommandValidator().validate(command)

    assert isinstance(backend.mutation_error, AttributeError)
    assert command.profile == "BLUE-L2"
    assert command.team == "BLUE"
    assert command.target == "L2"
    assert dict(command.evidence) == {"upstream_seq": 9, "level": 2}
    assert result.accepted is True


def test_frame_validation_uses_one_immutable_snapshot(monkeypatch):
    frame = verified_frame(data=b"CRC-BOUND", crc8_ok=True)
    reads = {"data": 0, "crc8_ok": 0}

    def changing_data(_frame):
        reads["data"] += 1
        return b"CRC-BOUND" if reads["data"] == 1 else b"DETACHED"

    def changing_crc8(_frame):
        reads["crc8_ok"] += 1
        return True if reads["crc8_ok"] == 1 else False

    monkeypatch.setattr(
        VerifiedParsedFrame,
        "data",
        property(changing_data),
        raising=False,
    )
    monkeypatch.setattr(
        VerifiedParsedFrame,
        "crc8_ok",
        property(changing_crc8),
        raising=False,
    )
    decoder = UpstreamDecoder(backend=RecordingBackend([frame]))
    context = make_context(profile="competition")
    decoder.reset(ResetReason.STARTUP, context)

    command = decoder.decode(make_chunk(), context)[0]

    assert reads == {"data": 0, "crc8_ok": 0}
    assert command.payload == b"CRC-BOUND"
    assert command.crc8_ok is True


def test_multiple_parsed_frames_are_converted_with_complete_metadata():
    backend = RecordingBackend(
        [
            verified_frame(cmd_id=0x0A06, data=b"ABC123", seq=41),
            verified_frame(
                cmd_id=0x0A02,
                data=b"payload",
                seq=42,
            ),
        ]
    )
    decoder = UpstreamDecoder(backend=backend)
    context = make_context("BLUE", "L1")
    chunk = make_chunk()
    decoder.reset(ResetReason.TARGET_CHANGE, context)

    commands = decoder.decode(chunk, context)

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
    ("overrides", "message"),
    _INVALID_VERIFIED_FRAME_CASES,
)
def test_verified_frame_constructor_rejects_invalid_fields(overrides, message):
    with pytest.raises((TypeError, ValueError), match=message):
        verified_frame(**overrides)


def test_verified_frame_cannot_be_forged_with_object_allocator():
    with pytest.raises(TypeError):
        object.__new__(VerifiedParsedFrame)


@pytest.mark.parametrize("forged_kind", ["plain-tuple", "subclass"])
def test_decode_defensively_rejects_forged_frame_type(forged_kind):
    values = (0x0A06, b"ABC123", 1, True, True, "kermit-x3014")
    if forged_kind == "plain-tuple":
        frame = values
    else:
        class FrameSubclass(VerifiedParsedFrame):
            pass

        frame = FrameSubclass(
            cmd_id=values[0],
            data=values[1],
            seq=values[2],
            crc8_ok=values[3],
            crc16_ok=values[4],
            crc_mode=values[5],
        )
    backend = RecordingBackend(
        [verified_frame(cmd_id=1, data=b"valid-first", seq=1), frame]
    )
    decoder = UpstreamDecoder(backend=backend)
    context = make_context(profile="competition")
    decoder.reset(ResetReason.STARTUP, context)

    with pytest.raises(TypeError, match="exact VerifiedParsedFrame"):
        decoder.decode(make_chunk(), context)

    stats = decoder.stats()
    assert stats.decode_errors == 1
    assert stats.chunks_processed == 0
    assert stats.samples_processed == 0
    assert stats.commands_emitted == 0


def test_mutated_payload_in_frame_impostor_cannot_reuse_verified_crc_claim():
    mutable_payload = bytearray(b"ABC123")
    frame = (0x0A06, mutable_payload, 1, True, True, "kermit-x3014")
    mutable_payload[:] = b"XXXXXX"
    decoder = UpstreamDecoder(backend=RecordingBackend([frame]))
    context = make_context(profile="competition")
    decoder.reset(ResetReason.STARTUP, context)

    with pytest.raises(TypeError, match="exact VerifiedParsedFrame"):
        decoder.decode(make_chunk(), context)

    stats = decoder.stats()
    assert stats.decode_errors == 1
    assert stats.chunks_processed == 0
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


@pytest.mark.parametrize(
    ("outer_operation", "inner_operation"),
    [
        ("decode", "decode"),
        ("decode", "reset"),
        ("reset", "decode"),
        ("reset", "reset"),
    ],
)
def test_same_thread_reentrant_operation_fails_fast_and_outer_completes(
    outer_operation,
    inner_operation,
):
    nested_errors = []
    context = make_context(profile="competition")

    class Backend:
        armed = False

        def invoke_nested(self):
            try:
                if inner_operation == "decode":
                    decoder.decode(make_chunk(), context)
                else:
                    decoder.reset(ResetReason.MANUAL, context)
            except RuntimeError as error:
                nested_errors.append(error)

        def decode(self, *, samples, sample_rate_hz, profile):
            if self.armed:
                self.invoke_nested()
            return ()

        def reset(self, *, reason, profile):
            if self.armed:
                self.invoke_nested()

    backend = Backend()
    decoder = UpstreamDecoder(backend=backend)
    decoder.reset(ResetReason.STARTUP, context)
    baseline = decoder.stats()
    backend.armed = True

    if outer_operation == "decode":
        assert decoder.decode(make_chunk(), context) == []
    else:
        decoder.reset(ResetReason.MANUAL, context)

    assert len(nested_errors) == 1
    assert "reentrant" in str(nested_errors[0]).lower()
    assert outer_operation in str(nested_errors[0])
    assert inner_operation in str(nested_errors[0])
    stats = decoder.stats()
    assert stats.decode_errors == baseline.decode_errors + 1
    assert stats.chunks_processed == baseline.chunks_processed + (
        outer_operation == "decode"
    )
    assert stats.resets == baseline.resets + (outer_operation == "reset")


def test_decode_operations_do_not_reenter_backend():
    first_entered = threading.Event()
    release_first = threading.Event()
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
    lock_probe = LockBoundaryProbe()
    decoder._operation_lock = lock_probe

    def second_decode():
        return decoder.decode(make_chunk(), context)

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(decoder.decode, make_chunk(), context)
        assert first_entered.wait(2.0)
        second = pool.submit(second_decode)
        assert lock_probe.boundary(1).wait(2.0)
        assert second_entered.is_set() is False
        release_first.set()
        assert first.result(timeout=2.0) == []
        assert second.result(timeout=2.0) == []


def test_decode_and_reset_are_serialized_with_consistent_profile():
    decode_entered = threading.Event()
    release_decode = threading.Event()
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
    lock_probe = LockBoundaryProbe()
    decoder._operation_lock = lock_probe
    backend.block_decode = True

    def reset_to_new_profile():
        decoder.reset(ResetReason.TARGET_CHANGE, new_context)

    with ThreadPoolExecutor(max_workers=2) as pool:
        decoding = pool.submit(decoder.decode, make_chunk(), old_context)
        assert decode_entered.wait(2.0)
        resetting = pool.submit(reset_to_new_profile)
        assert lock_probe.boundary(1).wait(2.0)
        assert reset_entered.is_set() is False
        release_decode.set()
        commands = decoding.result(timeout=2.0)
        resetting.result(timeout=2.0)

    assert commands[0].payload == b"old-profile"
    assert commands[0].profile == "BLUE-L1"
    assert decoder.active_profile.name == "RED-L2"


def test_reset_operations_commit_in_call_order_without_reentry():
    first_entered = threading.Event()
    release_first = threading.Event()
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
    lock_probe = LockBoundaryProbe()
    decoder._operation_lock = lock_probe

    def second_reset():
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
        assert lock_probe.boundary(1).wait(2.0)
        assert second_entered.is_set() is False
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
