from __future__ import annotations

import ast
import builtins
from concurrent.futures import ThreadPoolExecutor
from dataclasses import FrozenInstanceError
import importlib
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace

import numpy as np
import pytest

from sdr_receiver_py_wrapper.models import DecodeContext, IqChunk, ResetReason
from sdr_receiver_py_wrapper.original_receiver_adapter import ReceiverCoreAdapter
from sdr_receiver_py_wrapper.patches import JamKeyEvent, PatchCallbacks, RawFrameEvent
from sdr_receiver_py_wrapper.v67_decoder import V67Decoder


def make_chunk() -> IqChunk:
    samples = np.zeros(8, dtype=np.complex64)
    samples.flags.writeable = False
    return IqChunk(
        chunk_id=1,
        first_sample_index=100,
        samples=samples,
        sample_rate_hz=2_500_000,
        rx_wall_time=12.5,
        rx_monotonic_ns=99,
        lo_hz=434_920_000,
        rf_bandwidth_hz=940_000,
        rx_gain_db=22,
        target_version=3,
        context_version=4,
    )


def decode_context(team: str, target: str) -> DecodeContext:
    return DecodeContext(team, target, f"{team}-{target}", 3, 4)


def fake_core(*, jam_key: bytes | None = None):
    def demodulate_iq(*, samples, profile, callbacks):
        if jam_key is not None:
            callbacks.on_jam_key(
                JamKeyEvent(
                    cmd_id=0x0A06,
                    payload=jam_key,
                    key=jam_key,
                    ascii_code=jam_key.decode("ascii"),
                    level=1,
                    team=profile["team"],
                    target=profile["target"],
                    source="direct",
                    timestamp=12.6,
                )
            )

    return SimpleNamespace(demodulate_iq=demodulate_iq)


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


def test_events_keep_callback_order_and_common_metadata():
    events = [
        RawFrameEvent(
            cmd_id=0x0A02,
            payload=b"raw-frame",
            source="assembled",
            source_target="STALE",
            team="RED",
            crc8_ok=True,
            crc16_ok=False,
            air_chunk_index=7,
            timestamp=12.7,
        ),
        JamKeyEvent(
            cmd_id=0x0A06,
            payload=b"ABC123trailer",
            key=b"ABC123",
            ascii_code="ABC123",
            level=1,
            team="RED",
            target="STALE",
            source="direct",
            timestamp=12.8,
        ),
    ]

    class OrderedCore:
        def demodulate_iq(self, *, samples, profile, callbacks):
            callbacks.on_raw_frame(events[0])
            callbacks.on_jam_key(events[1])

    commands = V67Decoder(core=OrderedCore()).decode(
        make_chunk(), decode_context("BLUE", "L1")
    )

    assert [command.cmd_id for command in commands] == [0x0A02, 0x0A06]
    raw, jam = commands
    assert raw.payload == b"raw-frame"
    assert (raw.crc8_ok, raw.crc16_ok) == (True, False)
    assert raw.crc_mode == "v67_core_validated"
    assert raw.first_sample_index == 100
    assert raw.last_sample_index == 107
    assert raw.receive_wall_time == 12.5
    assert (raw.profile, raw.team, raw.target, raw.context_version) == (
        "BLUE-L1",
        "BLUE",
        "L1",
        4,
    )
    assert dict(raw.evidence) == {
        "event_type": "raw_frame",
        "source": "assembled",
        "source_target": "STALE",
        "event_team": "RED",
        "air_chunk_index": 7,
        "event_timestamp": 12.7,
    }
    assert jam.payload == b"ABC123"
    assert dict(jam.evidence) == {
        "event_type": "jam_key",
        "source": "direct",
        "source_target": "STALE",
        "event_team": "RED",
        "level": 1,
        "ascii": "ABC123",
        "event_timestamp": 12.8,
    }


def test_successful_decode_updates_frozen_stats_snapshot():
    decoder = V67Decoder(core=fake_core(jam_key=b"ABC123"))
    decoder.decode(make_chunk(), decode_context("BLUE", "L1"))

    stats = decoder.stats()
    assert stats.chunks_processed == 1
    assert stats.samples_processed == 8
    assert stats.commands_emitted == 1
    assert stats.decode_errors == 0
    try:
        stats.chunks_processed = 99
    except FrozenInstanceError:
        pass
    else:
        raise AssertionError("DecoderStats snapshot must be frozen")


def test_core_exception_is_counted_and_propagated_without_success_counts():
    event = JamKeyEvent(
        cmd_id=0x0A06,
        payload=b"ABC123",
        key=b"ABC123",
        ascii_code="ABC123",
        level=1,
        team="BLUE",
        target="L1",
        source="direct",
        timestamp=12.6,
    )

    class FailingCore:
        def demodulate_iq(self, *, samples, profile, callbacks):
            callbacks.on_jam_key(event)
            raise RuntimeError("demod failed")

    decoder = V67Decoder(core=FailingCore())
    with pytest.raises(RuntimeError, match="demod failed"):
        decoder.decode(make_chunk(), decode_context("BLUE", "L1"))

    assert decoder.stats().decode_errors == 1
    assert decoder.stats().chunks_processed == 0
    assert decoder.stats().samples_processed == 0
    assert decoder.stats().commands_emitted == 0


def test_reset_uses_only_optional_pure_hook_and_counts_attempt():
    calls = []

    class ResettableCore:
        def demodulate_iq(self, *, samples, profile, callbacks):
            pass

        def reset_decoder(self, *, reason, profile):
            calls.append((reason, profile))

        def set_target(self, *args, **kwargs):
            raise AssertionError("reset must not call hardware/control setters")

    decoder = V67Decoder(core=ResettableCore())
    context = decode_context("BLUE", "L1")
    decoder.reset(ResetReason.TARGET_CHANGE, context)

    assert calls == [
        (
            ResetReason.TARGET_CHANGE,
            {"name": "BLUE-L1", "team": "BLUE", "target": "L1"},
        )
    ]
    assert decoder.stats().resets == 1
    assert decoder.stats().decode_errors == 0


def test_reset_hook_exception_is_counted_and_propagated():
    class FailingResetCore:
        def demodulate_iq(self, *, samples, profile, callbacks):
            pass

        def reset_decoder(self, *, reason, profile):
            raise RuntimeError("reset failed")

    decoder = V67Decoder(core=FailingResetCore())
    with pytest.raises(RuntimeError, match="reset failed"):
        decoder.reset(ResetReason.MANUAL, decode_context("BLUE", "L1"))

    assert decoder.stats().resets == 1
    assert decoder.stats().decode_errors == 1


def test_adapter_demodulates_copied_iq_with_temporary_profile_and_callbacks():
    module = ModuleType("fake_v67")
    module.TUNE_CFG = {"TEAM": "RED", "TARGET": "INFO"}
    module.STATE = {"STATS": {}}
    module.RADAR_PARAMS = {
        "BLUE": {"L1": {"ac": "blue-l1-ac"}},
        "RED": {"INFO": {"ac": "red-info-ac"}},
    }
    module.validate_and_parse = lambda cmd_id, payload, source="direct": True
    demod_inputs = []

    def fast_demod(samples, ac_target):
        demod_inputs.append((samples, ac_target, dict(module.TUNE_CFG)))
        samples[0] = np.complex64(99 + 2j)
        module.validate_and_parse(0x0A06, b"ABC123", source="pure-iq")
        return True

    module.fast_demod = fast_demod
    adapter = ReceiverCoreAdapter()
    adapter.module = module
    samples = np.arange(8, dtype=np.float32).astype(np.complex64)
    before = samples.copy()
    events = []

    adapter.demodulate_iq(
        samples=samples,
        profile={"name": "BLUE-L1", "team": "BLUE", "target": "L1"},
        callbacks=PatchCallbacks(on_jam_key=events.append),
    )

    demod_samples, ac_target, active_cfg = demod_inputs[0]
    assert ac_target == "blue-l1-ac"
    assert active_cfg == {"TEAM": "BLUE", "TARGET": "L1"}
    assert not np.shares_memory(demod_samples, samples)
    np.testing.assert_array_equal(samples, before)
    assert [event.key for event in events] == [b"ABC123"]
    assert module.TUNE_CFG == {"TEAM": "RED", "TARGET": "INFO"}
    assert module.fast_demod is fast_demod


@pytest.mark.parametrize(
    ("samples", "error", "message"),
    [
        ([1 + 2j], TypeError, "numpy.ndarray"),
        (np.zeros(4, dtype=np.complex128), ValueError, "complex64"),
        (np.zeros((2, 2), dtype=np.complex64), ValueError, "one-dimensional"),
    ],
)
def test_adapter_rejects_iq_outside_exact_array_contract(samples, error, message):
    module = ModuleType("validation_v67")
    module.TUNE_CFG = {"TEAM": "RED", "TARGET": "INFO"}
    module.STATE = {"STATS": {}}
    module.RADAR_PARAMS = {"BLUE": {"L1": {"ac": "ac"}}}
    module.validate_and_parse = lambda cmd_id, payload, source="direct": True
    module.fast_demod = lambda samples, ac_target: False
    adapter = ReceiverCoreAdapter()
    adapter.module = module

    with pytest.raises(error, match=message):
        adapter.demodulate_iq(
            samples=samples,
            profile={"name": "BLUE-L1", "team": "BLUE", "target": "L1"},
            callbacks=PatchCallbacks(),
        )


@pytest.mark.parametrize(
    ("profile", "error", "message"),
    [
        ({"team": "BLUE", "target": "L1"}, ValueError, "exactly"),
        (
            {"name": "BLUE-L1", "team": "BLUE", "target": "L1", "gain": 22},
            ValueError,
            "exactly",
        ),
        ({"name": 12, "team": "BLUE", "target": "L1"}, TypeError, "strings"),
        ({"name": "GREEN-L1", "team": "GREEN", "target": "L1"}, ValueError, "team"),
        ({"name": "BLUE-X", "team": "BLUE", "target": "X"}, ValueError, "target"),
    ],
)
def test_adapter_rejects_profiles_outside_exact_contract(profile, error, message):
    module = ModuleType("profile_v67")
    module.TUNE_CFG = {"TEAM": "RED", "TARGET": "INFO"}
    module.STATE = {"STATS": {}}
    module.RADAR_PARAMS = {"BLUE": {"L1": {"ac": "ac"}}}
    module.validate_and_parse = lambda cmd_id, payload, source="direct": True
    module.fast_demod = lambda samples, ac_target: False
    adapter = ReceiverCoreAdapter()
    adapter.module = module

    with pytest.raises(error, match=message):
        adapter.demodulate_iq(
            samples=np.zeros(4, dtype=np.complex64),
            profile=profile,
            callbacks=PatchCallbacks(),
        )


def test_adapter_restores_persistent_callbacks_after_success_and_exception():
    module = ModuleType("callback_v67")
    module.TUNE_CFG = {"TEAM": "RED", "TARGET": "INFO"}
    module.STATE = {"STATS": {}}
    module.RADAR_PARAMS = {
        "RED": {"INFO": {"ac": "info-ac"}},
        "BLUE": {"L1": {"ac": "jam-ac"}},
    }
    module.validate_and_parse = lambda cmd_id, payload, source="direct": True
    fail = [False]

    def fast_demod(samples, ac_target):
        if fail[0]:
            raise RuntimeError("core boom")
        module.validate_and_parse(0x0A06, b"ABC123", source="pure")

    module.fast_demod = fast_demod
    adapter = ReceiverCoreAdapter()
    adapter.module = module
    persistent_events = []
    adapter.apply_patches(
        run_mode="offline",
        callbacks=PatchCallbacks(on_jam_key=persistent_events.append),
    )
    pure_events = []
    kwargs = {
        "samples": np.zeros(4, dtype=np.complex64),
        "profile": {"name": "BLUE-L1", "team": "BLUE", "target": "L1"},
        "callbacks": PatchCallbacks(on_jam_key=pure_events.append),
    }

    adapter.demodulate_iq(**kwargs)
    assert len(pure_events) == 1
    assert persistent_events == []
    module.validate_and_parse(0x0A06, b"DEF456", source="persistent")
    assert [event.key for event in persistent_events] == [b"DEF456"]

    fail[0] = True
    with pytest.raises(RuntimeError, match="core boom"):
        adapter.demodulate_iq(**kwargs)
    assert module.TUNE_CFG == {"TEAM": "RED", "TARGET": "INFO"}
    module.validate_and_parse(0x0A06, b"GHI789", source="persistent")
    assert [event.key for event in persistent_events] == [b"DEF456", b"GHI789"]


def test_v67_plugin_source_has_no_device_ros_io_or_thread_creation():
    source_path = Path(sys.modules[V67Decoder.__module__].__file__)
    tree = ast.parse(source_path.read_text(encoding="utf-8"))
    forbidden_imports = {"adi", "pyadi", "rclpy", "socket", "receiver_node"}
    imported_roots = set()
    calls = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported_roots.add(node.module.split(".")[0])
        elif isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name):
                calls.add(node.func.id)
            elif isinstance(node.func, ast.Attribute):
                calls.add(node.func.attr)

    assert imported_roots.isdisjoint(forbidden_imports)
    assert calls.isdisjoint({"Thread", "open", "socket", "create_connection"})


def test_v67_plugin_import_does_not_attempt_forbidden_dependencies(monkeypatch):
    attempted = []
    real_import = builtins.__import__

    def tracking_import(name, *args, **kwargs):
        attempted.append(name)
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", tracking_import)
    monkeypatch.delitem(sys.modules, "sdr_receiver_py_wrapper.v67_decoder", raising=False)
    importlib.import_module("sdr_receiver_py_wrapper.v67_decoder")

    forbidden = ("adi", "pyadi", "rclpy", "socket", "receiver_node")
    assert not any(any(part in name.split(".") for part in forbidden) for name in attempted)


def test_decode_passes_only_iq_and_authoritative_profile_to_pure_entry():
    seen = []

    class BoundaryCore:
        def demodulate_iq(self, *, samples, profile, callbacks):
            seen.append((samples, profile, callbacks))

        def __getattr__(self, name):
            if name in {
                "set_target",
                "set_manual_gain",
                "set_team",
                "set_radio_profile",
                "publisher",
            }:
                raise AssertionError(f"decoder crossed pure boundary through {name}")
            raise AttributeError(name)

    chunk = make_chunk()
    V67Decoder(core=BoundaryCore()).decode(
        chunk, decode_context("BLUE", "L1")
    )

    assert seen[0][0] is chunk.samples
    assert seen[0][1] == {
        "name": "BLUE-L1",
        "team": "BLUE",
        "target": "L1",
    }
    assert isinstance(seen[0][2], PatchCallbacks)


def test_stats_snapshot_is_consistent_under_concurrent_decodes():
    decoder = V67Decoder(core=fake_core())
    chunk = make_chunk()
    context = decode_context("BLUE", "L1")

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(lambda _index: decoder.decode(chunk, context), range(100)))

    stats = decoder.stats()
    assert stats.chunks_processed == 100
    assert stats.samples_processed == 800
    assert stats.commands_emitted == 0
    assert stats.decode_errors == 0


def test_same_callback_event_object_is_not_emitted_twice():
    jam_event = JamKeyEvent(
        cmd_id=0x0A06,
        payload=b"ABC123",
        key=b"ABC123",
        ascii_code="ABC123",
        level=1,
        team="BLUE",
        target="L1",
        source="direct",
        timestamp=12.6,
    )

    class DuplicateCore:
        def demodulate_iq(self, *, samples, profile, callbacks):
            callbacks.on_jam_key(jam_event)
            callbacks.on_jam_key(jam_event)

    decoder = V67Decoder(core=DuplicateCore())
    commands = decoder.decode(make_chunk(), decode_context("BLUE", "L1"))

    assert [command.payload for command in commands] == [b"ABC123"]
    assert decoder.stats().commands_emitted == 1
