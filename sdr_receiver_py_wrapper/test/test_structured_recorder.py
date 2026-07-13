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
    assert not recorder.write_event("dropped-event", {"value": 1})
    release_worker.set()
    recorder.close()

    events = [
        json.loads(line)
        for line in (tmp_path / "overflow.events.jsonl").read_text().splitlines()
    ]
    overflow = next(event for event in events if event["kind"] == "recorder_queue_overflow")
    assert overflow["payload"] == {
        "dropped_chunks": 0,
        "dropped_events": 1,
        "total_drops": 1,
    }
    summary = json.loads((tmp_path / "overflow.summary.json").read_text())
    assert summary["dropped_chunks"] == 0
    assert summary["dropped_events"] == 1
    assert summary["queue_overflows"] == 1


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

    class FakeStructuredRecorder:
        instances = []

        def __init__(self, record_dir, prefix, *, summary_metadata):
            self.record_dir = Path(record_dir)
            self.prefix = prefix
            self.summary_metadata = summary_metadata
            self.iq_path = self.record_dir / f"{prefix}.c64"
            self.summary_path = self.record_dir / f"{prefix}.summary.json"
            self.chunks = []
            self.events = []
            self.close_reason = None
            self.stats = SimpleNamespace(
                chunks_written=0,
                samples_written=0,
                bytes_written=0,
                dropped_chunks=0,
                dropped_events=0,
                worker_error=None,
            )
            self.instances.append(self)

        def write_chunk(self, chunk):
            self.chunks.append(chunk)
            self.stats.chunks_written += 1
            self.stats.samples_written += chunk.samples.size
            self.stats.bytes_written += chunk.samples.nbytes
            return True

        def write_event(self, kind, payload):
            self.events.append((kind, payload))
            return True

        def close(self, *, stopped_reason="closed"):
            self.close_reason = stopped_reason

    namespace = {
        "IqChunk": IqChunk,
        "Optional": Optional,
        "Path": Path,
        "RfMetrics": RfMetrics,
        "StructuredRecorder": FakeStructuredRecorder,
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
    compatibility_class = namespace["IqRecorder"]
    recorder = compatibility_class(
        record_dir=str(tmp_path),
        prefix="base",
        max_sec=0.0,
        max_bytes=0,
        every_n=1,
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

    delegate = FakeStructuredRecorder.instances[0]
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
    assert chunk.target_version == 7
    assert chunk.context_version == 8
    assert delegate.summary_metadata["target"] == "HERO"
    assert delegate.prefix.startswith("dynamic_")
    assert delegate.close_reason == "closed"
    assert recorder.status()["chunks_written"] == 1


def test_receiver_metadata_provider_exposes_explicit_chunk_versions():
    receiver_path = (
        Path(__file__).parents[1]
        / "sdr_receiver_py_wrapper"
        / "receiver_node.py"
    )
    tree = ast.parse(receiver_path.read_text(encoding="utf-8"))
    node_class = next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "SdrReceiverPyWrapperNode"
    )
    provider = next(
        node
        for node in node_class.body
        if isinstance(node, ast.FunctionDef) and node.name == "_iq_record_metadata"
    )
    returned_mapping = next(
        node.value
        for node in ast.walk(provider)
        if isinstance(node, ast.Return) and isinstance(node.value, ast.Dict)
    )
    keys = {
        key.value
        for key in returned_mapping.keys
        if isinstance(key, ast.Constant) and isinstance(key.value, str)
    }

    assert {"target_version", "context_version"} <= keys
