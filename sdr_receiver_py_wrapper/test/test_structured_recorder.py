import ast
import json
import os
from pathlib import Path
import threading
from types import MappingProxyType
from types import SimpleNamespace
from typing import Optional

import numpy as np
import pytest

from sdr_receiver_py_wrapper.models import IqChunk, RfMetrics
from sdr_receiver_py_wrapper.structured_recorder import RecorderError, StructuredRecorder


def load_iq_recorder_class(structured_recorder_class):
    receiver_path = (
        Path(__file__).parents[1]
        / "sdr_receiver_py_wrapper"
        / "receiver_node.py"
    )
    tree = ast.parse(receiver_path.read_text(encoding="utf-8"))
    recorder_class = next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "IqRecorder"
    )
    namespace = {
        "IqChunk": IqChunk,
        "Optional": Optional,
        "Path": Path,
        "RfMetrics": RfMetrics,
        "StructuredRecorder": structured_recorder_class,
        "np": np,
        "os": os,
        "threading": threading,
        "time": __import__("time"),
    }
    exec(
        compile(
            ast.fix_missing_locations(ast.Module(body=[recorder_class], type_ignores=[])),
            str(receiver_path),
            "exec",
        ),
        namespace,
    )
    return namespace["IqRecorder"]


class CapturingStructuredRecorder:
    instances = []

    def __init__(self, record_dir, prefix, **kwargs):
        self.record_dir = Path(record_dir)
        self.prefix = prefix
        self.kwargs = kwargs
        self.iq_path = self.record_dir / f"{prefix}.c64"
        self.summary_path = self.record_dir / f"{prefix}.summary.json"
        self.chunks = []
        self.chunk_metadata = []
        self.events = []
        self.close_reason = None
        self.stats = SimpleNamespace(
            chunks_written=0,
            samples_written=0,
            bytes_written=0,
            dropped_chunks=0,
            dropped_events=0,
            worker_error=None,
            closed=False,
        )
        self.instances.append(self)

    def write_chunk(self, chunk, metadata=None):
        self.chunks.append(chunk)
        self.chunk_metadata.append(metadata)
        self.stats.chunks_written += 1
        self.stats.samples_written += chunk.samples.size
        self.stats.bytes_written += chunk.samples.nbytes
        return True

    def write_event(self, kind, payload):
        self.events.append((kind, payload))
        return True

    def close(self, *, stopped_reason="closed"):
        provider = self.kwargs.get("summary_metadata_provider")
        if provider is not None:
            provider()
        self.close_reason = stopped_reason
        self.stats.closed = True


def make_iq_adapter(tmp_path, recorder_class=CapturingStructuredRecorder, **overrides):
    options = {
        "record_dir": str(tmp_path),
        "prefix": "test",
        "max_sec": 0.0,
        "max_bytes": 0,
        "every_n": 1,
        "metadata_provider": lambda: {"target": "HERO"},
    }
    options.update(overrides)
    return load_iq_recorder_class(recorder_class)(**options)


def make_chunk(*, chunk_id=0, first_sample_index=0, sample_count=16):
    samples = np.arange(sample_count, dtype=np.float32).astype(np.complex64)
    samples.setflags(write=False)
    return IqChunk(
        chunk_id=chunk_id,
        first_sample_index=first_sample_index,
        samples=samples,
        sample_rate_hz=2_000_000,
        rx_wall_time=1_700_000_000.25,
        rx_monotonic_ns=123_456_789,
        lo_hz=434_920_000,
        rf_bandwidth_hz=940_000,
        rx_gain_db=20,
        target_version=3,
        context_version=4,
        rf_metrics=RfMetrics(
            rms=0.5,
            peak=1.0,
            clipping_ratio=0.0,
            sample_count=sample_count,
        ),
    )


def test_iq_adapter_hot_path_uses_light_metadata_and_no_rf_reductions(
    tmp_path,
    monkeypatch,
):
    CapturingStructuredRecorder.instances.clear()
    calls = {"summary": 0, "chunk": 0}

    def summary_metadata():
        calls["summary"] += 1
        return {"profile": {"large": "payload"}}

    def chunk_metadata():
        calls["chunk"] += 1
        return {
            "sample_rate_hz": 2_000_000,
            "target": "HERO",
            "context_version": 4,
        }

    recorder = make_iq_adapter(
        tmp_path,
        prefix="hot",
        metadata_provider=summary_metadata,
        chunk_metadata_provider=chunk_metadata,
    )

    for name in ("abs", "max", "mean", "count_nonzero"):
        monkeypatch.setattr(
            np,
            name,
            lambda *args, _name=name, **kwargs: (_ for _ in ()).throw(
                AssertionError(f"unexpected producer reduction: {_name}")
            ),
        )
    recorder.write(np.ones(32, dtype=np.complex64))

    delegate = CapturingStructuredRecorder.instances[-1]
    assert calls == {"summary": 0, "chunk": 1}
    assert delegate.chunks[0].rf_metrics is None
    assert delegate.chunk_metadata[0]["target"] == "HERO"
    recorder.close()
    assert calls == {"summary": 1, "chunk": 1}


def test_iq_adapter_every_n_preserves_real_sample_gaps_without_copying_skips(
    tmp_path,
):
    CapturingStructuredRecorder.instances.clear()
    recorder = make_iq_adapter(
        tmp_path,
        prefix="stride",
        every_n=2,
    )
    arrays = [np.full(4, value, dtype=np.complex64) for value in (1, 2, 3)]

    for raw in arrays:
        recorder.write(raw)

    chunks = CapturingStructuredRecorder.instances[-1].chunks
    assert [chunk.chunk_id for chunk in chunks] == [0, 2]
    assert [chunk.first_sample_index for chunk in chunks] == [0, 8]
    np.testing.assert_array_equal(chunks[1].samples, arrays[2])


def test_iq_adapter_limit_finalizes_asynchronously_and_surfaces_errors(tmp_path):
    class FailingFinalizer(CapturingStructuredRecorder):
        finalized = threading.Event()

        def close(self, *, stopped_reason="closed"):
            self.finalized.set()
            raise OSError("final fsync failed")

    FailingFinalizer.instances.clear()
    FailingFinalizer.finalized.clear()
    recorder = make_iq_adapter(
        tmp_path,
        FailingFinalizer,
        prefix="limit",
        max_bytes=32,
    )
    recorder.write(np.ones(4, dtype=np.complex64))

    recorder.write(np.ones(4, dtype=np.complex64))

    assert FailingFinalizer.finalized.wait(timeout=3.0)
    with pytest.raises(OSError, match="final fsync failed"):
        recorder.close()
    assert "final fsync failed" in recorder.status()["finalizer_error"]


@pytest.mark.parametrize(
    "prefix",
    ["", "../escape", "a/b", "a\\b", ".", "..", "bad:name"],
)
def test_structured_recorder_rejects_unsafe_prefixes_before_starting_worker(
    tmp_path,
    monkeypatch,
    prefix,
):
    monkeypatch.setattr(threading.Thread, "start", lambda self: None)
    with pytest.raises(ValueError, match="safe filename component"):
        StructuredRecorder(tmp_path, prefix)


def test_structured_recorder_never_overwrites_existing_capture(tmp_path):
    first = StructuredRecorder(tmp_path, "collision")
    first.write_chunk(make_chunk(chunk_id=1))
    first.close()
    original_iq = (tmp_path / "collision.c64").read_bytes()

    second = StructuredRecorder(tmp_path, "collision")
    with pytest.raises(RecorderError, match="worker failed"):
        second.close()

    assert (tmp_path / "collision.c64").read_bytes() == original_iq


def test_partial_exclusive_open_cleans_only_new_empty_files(tmp_path):
    existing = tmp_path / "partial.chunks.jsonl"
    existing.write_text("preserve evidence\n", encoding="utf-8")
    recorder = StructuredRecorder(tmp_path, "partial")

    with pytest.raises(RecorderError, match="worker failed"):
        recorder.close()

    assert existing.read_text(encoding="utf-8") == "preserve evidence\n"
    assert not (tmp_path / "partial.c64").exists()


def test_iq_adapter_uses_collision_resistant_prefixes(tmp_path, monkeypatch):
    CapturingStructuredRecorder.instances.clear()
    monkeypatch.setattr(__import__("time"), "time", lambda: 1_700_000_000.0)
    recorders = [
        make_iq_adapter(
            tmp_path,
            prefix="same",
        )
        for _ in range(2)
    ]

    for recorder in recorders:
        recorder.write(np.ones(1, dtype=np.complex64))

    assert len({item.prefix for item in CapturingStructuredRecorder.instances}) == 2


def test_destroy_node_propagates_recorder_error_after_destroying_base():
    receiver_path = (
        Path(__file__).parents[1] / "sdr_receiver_py_wrapper" / "receiver_node.py"
    )
    tree = ast.parse(receiver_path.read_text(encoding="utf-8"))
    original_class = next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "SdrReceiverPyWrapperNode"
    )
    destroy_method = next(
        node
        for node in original_class.body
        if isinstance(node, ast.FunctionDef) and node.name == "destroy_node"
    )

    class FakeNode:
        destroyed = False

        def destroy_node(self):
            FakeNode.destroyed = True
            return True

    test_class = ast.ClassDef(
        name="TestNode",
        bases=[ast.Name(id="FakeNode", ctx=ast.Load())],
        keywords=[],
        body=[destroy_method],
        decorator_list=[],
    )
    namespace = {"FakeNode": FakeNode}
    exec(
        compile(
            ast.fix_missing_locations(ast.Module(body=[test_class], type_ignores=[])),
            str(receiver_path),
            "exec",
        ),
        namespace,
    )
    instance = namespace["TestNode"]()
    instance.adapter = SimpleNamespace(stop=lambda: None, restore_patches=lambda: None)
    instance.iq_recorder = SimpleNamespace(
        close=lambda: (_ for _ in ()).throw(RecorderError("fsync failed"))
    )

    with pytest.raises(RecorderError, match="fsync failed"):
        instance.destroy_node()
    assert FakeNode.destroyed


def test_target_generation_and_sidecar_target_follow_actual_changes(tmp_path):
    CapturingStructuredRecorder.instances.clear()
    targets = iter(["HERO", "HERO", "INFANTRY"])
    recorder = make_iq_adapter(
        tmp_path,
        prefix="targets",
        chunk_metadata_provider=lambda: {"target": next(targets)},
    )
    for _ in range(3):
        recorder.write(np.ones(2, dtype=np.complex64))

    delegate = CapturingStructuredRecorder.instances[-1]
    assert [chunk.target_version for chunk in delegate.chunks] == [1, 1, 2]
    assert [metadata["target"] for metadata in delegate.chunk_metadata] == [
        "HERO",
        "HERO",
        "INFANTRY",
    ]


def test_nonfinite_values_are_encoded_as_strict_auditable_json(tmp_path):
    chunk = make_chunk(sample_count=2)
    object.__setattr__(
        chunk,
        "rf_metrics",
        RfMetrics(
            rms=float("nan"),
            peak=float("inf"),
            clipping_ratio=float("-inf"),
            sample_count=2,
        ),
    )
    provider_threads = []

    def summary_provider():
        provider_threads.append(threading.current_thread())
        return {"calibration": np.float64(float("nan"))}

    recorder = StructuredRecorder(
        tmp_path,
        "finite",
        summary_metadata_provider=summary_provider,
    )
    recorder.write_chunk(chunk, metadata={"noise": float("inf")})
    recorder.write_event("metrics", {"value": float("-inf")})
    recorder.close()

    def reject_constant(value):
        raise AssertionError(f"non-standard JSON constant: {value}")

    chunk_json = json.loads(
        (tmp_path / "finite.chunks.jsonl").read_text(),
        parse_constant=reject_constant,
    )
    event_json = json.loads(
        (tmp_path / "finite.events.jsonl").read_text(),
        parse_constant=reject_constant,
    )
    summary_json = json.loads(
        (tmp_path / "finite.summary.json").read_text(),
        parse_constant=reject_constant,
    )
    assert chunk_json["rf_metrics"]["rms"] == "NaN"
    assert chunk_json["rf_metrics"]["peak"] == "Infinity"
    assert chunk_json["metadata"]["noise"] == "Infinity"
    assert event_json["payload"]["value"] == "-Infinity"
    assert summary_json["calibration"] == "NaN"
    assert len(provider_threads) == 1
    assert provider_threads[0] is not threading.main_thread()


def test_recorder_writes_iq_chunk_and_event_sidecars(tmp_path):
    recorder = StructuredRecorder(tmp_path, "case")
    recorder.write_chunk(make_chunk(chunk_id=7, first_sample_index=112))
    recorder.write_event("context_rejected", {"reason": "invalid_radar_id"})
    recorder.close()

    assert (tmp_path / "case.c64").stat().st_size == 16 * 8
    chunk = json.loads(
        (tmp_path / "case.chunks.jsonl").read_text().splitlines()[0]
    )
    assert chunk["chunk_id"] == 7
    assert chunk["first_sample_index"] == 112


def test_chunk_sidecar_contains_replay_metadata_and_offsets(tmp_path):
    first = make_chunk(chunk_id=3, first_sample_index=20, sample_count=3)
    second = make_chunk(chunk_id=4, first_sample_index=23, sample_count=2)
    recorder = StructuredRecorder(tmp_path, "replay")

    assert recorder.write_chunk(first)
    assert recorder.write_chunk(second)
    recorder.close()

    raw = np.fromfile(tmp_path / "replay.c64", dtype="<c8")
    np.testing.assert_array_equal(raw, np.concatenate((first.samples, second.samples)))
    lines = [
        json.loads(line)
        for line in (tmp_path / "replay.chunks.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert lines[0] == {
        "byte_length": 24,
        "byte_offset": 0,
        "chunk_id": 3,
        "context_version": 4,
        "first_sample_index": 20,
        "lo_hz": 434_920_000,
        "metadata": {},
        "rf_bandwidth_hz": 940_000,
        "rf_metrics": {
            "clipping_ratio": 0.0,
            "peak": 1.0,
            "rms": 0.5,
            "sample_count": 3,
        },
        "rx_gain_db": 20,
        "rx_monotonic_ns": 123_456_789,
        "rx_wall_time": 1_700_000_000.25,
        "sample_count": 3,
        "sample_rate_hz": 2_000_000,
        "target_version": 3,
        "target": None,
    }
    assert lines[1]["byte_offset"] == 24
    assert lines[1]["byte_length"] == 16


def test_event_payload_is_snapshotted_and_json_safe_at_enqueue(tmp_path):
    payload = {
        "nested": {"values": [np.int64(5)]},
        "mapping": MappingProxyType({"path": Path("capture/input.c64")}),
    }
    recorder = StructuredRecorder(tmp_path, "snapshot")

    recorder.write_event("decision", payload)
    payload["nested"]["values"][0] = 99
    payload["nested"]["values"].append(100)
    recorder.close()

    event = json.loads((tmp_path / "snapshot.events.jsonl").read_text().splitlines()[0])
    assert event["kind"] == "decision"
    assert event["payload"] == {
        "mapping": {"path": os.fspath(Path("capture/input.c64"))},
        "nested": {"values": [5]},
    }
    assert isinstance(event["wall_time"], float)
    assert isinstance(event["monotonic_ns"], int)


def test_queue_overflow_is_nonblocking_counted_and_auditable(tmp_path, monkeypatch):
    worker_entered_open = threading.Event()
    release_worker = threading.Event()
    original_open = Path.open

    def delayed_open(path, *args, **kwargs):
        if threading.current_thread() is not threading.main_thread():
            worker_entered_open.set()
            assert release_worker.wait(timeout=3.0)
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", delayed_open)
    recorder = StructuredRecorder(tmp_path, "overflow", queue_size=1)
    assert worker_entered_open.wait(timeout=3.0)

    assert recorder.write_chunk(make_chunk())
    assert not recorder.write_chunk(make_chunk(chunk_id=7, first_sample_index=112))
    assert not recorder.write_chunk(make_chunk(chunk_id=9, first_sample_index=144))
    assert not recorder.write_event("context_rejected", {})
    assert not recorder.write_event("context_rejected", {})
    assert not recorder.write_event("decode_error", {})
    for index in range(20):
        assert not recorder.write_event(f"other-{index}", {})
    release_worker.set()
    recorder.close()

    events = [
        json.loads(line)
        for line in (tmp_path / "overflow.events.jsonl").read_text().splitlines()
    ]
    overflow = next(event for event in events if event["kind"] == "recorder_queue_overflow")
    payload = overflow["payload"]
    assert payload["dropped_chunks"] == 2
    assert payload["dropped_events"] == 23
    assert payload["total_drops"] == 25
    assert payload["dropped_chunk_range"] == {
        "first_chunk_id": 7,
        "last_chunk_id": 9,
        "first_sample_index": 112,
        "last_sample_index_exclusive": 160,
    }
    assert payload["dropped_chunk_ranges"] == [
        {
            "first_chunk_id": 7,
            "last_chunk_id": 7,
            "first_sample_index": 112,
            "last_sample_index_exclusive": 128,
        },
        {
            "first_chunk_id": 9,
            "last_chunk_id": 9,
            "first_sample_index": 144,
            "last_sample_index_exclusive": 160,
        },
    ]
    assert payload["dropped_event_kinds"]["context_rejected"] == 2
    assert payload["dropped_event_kinds"]["decode_error"] == 1
    assert payload["dropped_event_kinds"]["__other__"] == 6
    assert len(payload["dropped_event_kinds"]) == 17
    summary = json.loads((tmp_path / "overflow.summary.json").read_text())
    assert summary["dropped_chunks"] == 2
    assert summary["dropped_events"] == 23
    assert summary["queue_overflows"] == 25


def test_close_drains_is_idempotent_and_rejects_later_writes(tmp_path):
    recorder = StructuredRecorder(tmp_path, "drain", queue_size=8)
    for chunk_id in range(8):
        assert recorder.write_chunk(
            make_chunk(chunk_id=chunk_id, first_sample_index=chunk_id * 16)
        )

    recorder.close()
    recorder.close()

    assert recorder.stats.chunks_written == 8
    assert recorder.stats.closed
    with pytest.raises(RecorderError, match="closed"):
        recorder.write_event("too-late", {})


def test_close_waits_for_slow_accepted_writes_without_a_total_timeout(
    tmp_path,
    monkeypatch,
):
    worker_entered_open = threading.Event()
    release_worker = threading.Event()
    original_open = Path.open

    def controlled_open(path, *args, **kwargs):
        if threading.current_thread() is not threading.main_thread():
            worker_entered_open.set()
            assert release_worker.wait(timeout=3.0)
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", controlled_open)
    recorder = StructuredRecorder(tmp_path, "slow-close")
    assert worker_entered_open.wait(timeout=3.0)
    assert recorder.write_chunk(make_chunk(chunk_id=11))
    real_join = recorder._worker.join
    join_timeouts = []

    def controlled_join(timeout=None):
        join_timeouts.append(timeout)
        if timeout is not None:
            return
        release_worker.set()
        real_join()

    monkeypatch.setattr(recorder._worker, "join", controlled_join)
    try:
        recorder.close()
    finally:
        release_worker.set()
        real_join(timeout=3.0)

    assert join_timeouts == [None]
    assert recorder.stats.closed
    assert recorder.stats.chunks_written == 1
    summary = json.loads((tmp_path / "slow-close.summary.json").read_text())
    assert summary["chunks_written"] == 1
    assert summary["stopped_reason"] == "closed"


def test_file_operations_and_single_flush_happen_only_on_worker(tmp_path, monkeypatch):
    original_open = Path.open
    operation_threads = []
    flush_counts = {}

    class TrackedHandle:
        def __init__(self, handle, path):
            self._handle = handle
            self._path = path

        def write(self, value):
            operation_threads.append(threading.current_thread())
            return self._handle.write(value)

        def flush(self):
            operation_threads.append(threading.current_thread())
            flush_counts[self._path] = flush_counts.get(self._path, 0) + 1
            return self._handle.flush()

        def fileno(self):
            return self._handle.fileno()

        def close(self):
            return self._handle.close()

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc_value, traceback):
            self.close()

    def tracked_open(path, *args, **kwargs):
        operation_threads.append(threading.current_thread())
        return TrackedHandle(original_open(path, *args, **kwargs), path.name)

    monkeypatch.setattr(Path, "open", tracked_open)
    recorder = StructuredRecorder(tmp_path, "io-boundary")
    for chunk_id in range(4):
        recorder.write_chunk(make_chunk(chunk_id=chunk_id))
    recorder.write_event("queued", {"ok": True})
    recorder.close()

    assert operation_threads
    assert all(thread is not threading.main_thread() for thread in operation_threads)
    assert flush_counts == {
        "io-boundary.c64": 1,
        "io-boundary.chunks.jsonl": 1,
        "io-boundary.events.jsonl": 1,
        "io-boundary.summary.json": 1,
    }


def test_worker_error_is_reported_without_close_hanging(tmp_path, monkeypatch):
    def broken_open(path, *args, **kwargs):
        raise OSError("disk unavailable")

    monkeypatch.setattr(Path, "open", broken_open)
    recorder = StructuredRecorder(tmp_path, "broken")

    with pytest.raises(RecorderError, match="worker failed") as error:
        recorder.close()

    assert isinstance(error.value.__cause__, OSError)
    assert "disk unavailable" in str(error.value.__cause__)


def test_receiver_iq_recorder_delegates_without_synchronous_disk_calls():
    receiver_path = (
        Path(__file__).parents[1]
        / "sdr_receiver_py_wrapper"
        / "receiver_node.py"
    )
    source = receiver_path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    recorder_class = next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "IqRecorder"
    )
    called_attributes = {
        call.func.attr
        for call in ast.walk(recorder_class)
        if isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute)
    }

    assert "write_chunk" in called_attributes
    assert not ({"open", "flush", "fsync", "write_text"} & called_attributes)
    assert "from .structured_recorder import" in source


def test_receiver_iq_recorder_preserves_compatibility_and_snapshots_raw_iq(tmp_path):
    CapturingStructuredRecorder.instances.clear()
    recorder = make_iq_adapter(
        tmp_path,
        prefix="base",
        metadata_provider=lambda: {
            "sample_rate_hz": 2_000_000,
            "rx_lo_hz": 434_920_000,
            "rf_bandwidth_hz": 940_000,
            "rx_gain": 20,
            "target_version": 7,
            "context_version": 8,
            "target": "HERO",
        },
        prefix_provider=lambda: "dynamic",
    )
    reused_device_buffer = np.asarray([1 + 2j, 3 + 4j], dtype=np.complex64)
    expected = reused_device_buffer.copy()

    recorder.write(reused_device_buffer)
    reused_device_buffer[:] = 99 + 100j
    recorder.close()

    delegate = CapturingStructuredRecorder.instances[0]
    chunk = delegate.chunks[0]
    np.testing.assert_array_equal(chunk.samples, expected)
    assert chunk.samples.flags.owndata
    assert chunk.samples.base is None
    assert not chunk.samples.flags.writeable
    assert chunk.chunk_id == 0
    assert chunk.first_sample_index == 0
    assert chunk.sample_rate_hz == 2_000_000
    assert chunk.lo_hz == 434_920_000
    assert chunk.rf_bandwidth_hz == 940_000
    assert chunk.rx_gain_db == 20
    assert chunk.target_version == 1
    assert chunk.context_version == 8
    assert delegate.prefix.startswith("dynamic_")
    assert delegate.close_reason == "closed"
    assert recorder.status()["chunks_written"] == 1


def test_iq_adapter_repeated_close_preserves_first_finalize_error(tmp_path):
    class FailingClose(CapturingStructuredRecorder):
        def close(self, *, stopped_reason="closed"):
            raise OSError("fsync failed")

    FailingClose.instances.clear()
    recorder = make_iq_adapter(
        tmp_path,
        FailingClose,
        prefix="close-error",
    )
    recorder.write(np.ones(1, dtype=np.complex64))

    with pytest.raises(OSError, match="fsync failed"):
        recorder.close()
    with pytest.raises(OSError, match="fsync failed"):
        recorder.close()
