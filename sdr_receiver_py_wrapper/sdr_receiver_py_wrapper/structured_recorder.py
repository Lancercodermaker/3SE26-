"""Asynchronous, replayable IQ and event recording."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import Enum
import json
import numbers
import os
from pathlib import Path
from queue import Full, Queue
import threading
import time
from typing import Mapping

import numpy as np

from .models import IqChunk


class RecorderError(RuntimeError):
    """Raised when the recorder's background writer fails."""


@dataclass(frozen=True)
class RecorderStats:
    chunks_written: int = 0
    events_written: int = 0
    samples_written: int = 0
    bytes_written: int = 0
    dropped_chunks: int = 0
    dropped_events: int = 0
    closed: bool = False
    worker_error: str | None = None


class StructuredRecorder:
    """Record immutable IQ chunks without blocking producers on disk I/O."""

    _STOP = object()

    def __init__(
        self,
        record_dir: str | Path,
        prefix: str,
        *,
        queue_size: int = 256,
        summary_metadata: Mapping[str, object] | None = None,
    ) -> None:
        if isinstance(queue_size, bool) or not isinstance(queue_size, numbers.Integral) or queue_size <= 0:
            raise ValueError("queue_size must be a positive integer")

        self.record_dir = Path(record_dir)
        self.prefix = str(prefix)
        self.iq_path = self.record_dir / f"{self.prefix}.c64"
        self.chunks_path = self.record_dir / f"{self.prefix}.chunks.jsonl"
        self.events_path = self.record_dir / f"{self.prefix}.events.jsonl"
        self.summary_path = self.record_dir / f"{self.prefix}.summary.json"
        self._summary_metadata = _json_snapshot(summary_metadata or {})
        self._queue: Queue[object] = Queue(maxsize=int(queue_size))
        self._lock = threading.Lock()
        self._accepting = True
        self._closed = False
        self._worker_error: BaseException | None = None
        self._chunks_written = 0
        self._events_written = 0
        self._samples_written = 0
        self._bytes_written = 0
        self._dropped_chunks = 0
        self._dropped_events = 0
        self._stopped_reason = "closed"
        self._started_wall_time = time.time()
        self._worker = threading.Thread(
            target=self._run,
            name=f"structured-recorder-{self.prefix}",
            daemon=True,
        )
        self._worker.start()

    def write_chunk(self, chunk: IqChunk) -> bool:
        if not isinstance(chunk, IqChunk):
            raise TypeError("chunk must be an IqChunk")
        return self._enqueue(("chunk", chunk), dropped_kind="chunk")

    def write_event(self, kind: str, payload: Mapping[str, object]) -> bool:
        if not isinstance(kind, str) or not kind:
            raise ValueError("event kind must be a non-empty string")
        if not isinstance(payload, Mapping):
            raise TypeError("event payload must be a mapping")
        item = (
            "event",
            {
                "kind": kind,
                "payload": _json_snapshot(payload),
                "wall_time": time.time(),
                "monotonic_ns": time.monotonic_ns(),
            },
        )
        return self._enqueue(item, dropped_kind="event")

    def _enqueue(self, item: object, *, dropped_kind: str) -> bool:
        with self._lock:
            self._raise_worker_error_locked()
            if not self._accepting:
                raise RecorderError("recorder is closed")
            try:
                self._queue.put_nowait(item)
            except Full:
                if dropped_kind == "chunk":
                    self._dropped_chunks += 1
                else:
                    self._dropped_events += 1
                return False
            return True

    @property
    def stats(self) -> RecorderStats:
        with self._lock:
            return RecorderStats(
                chunks_written=self._chunks_written,
                events_written=self._events_written,
                samples_written=self._samples_written,
                bytes_written=self._bytes_written,
                dropped_chunks=self._dropped_chunks,
                dropped_events=self._dropped_events,
                closed=self._closed,
                worker_error=None if self._worker_error is None else str(self._worker_error),
            )

    def close(self, *, stopped_reason: str = "closed") -> None:
        with self._lock:
            if self._closed:
                self._raise_worker_error_locked()
                return
            self._accepting = False
            self._stopped_reason = str(stopped_reason)

        while True:
            if not self._worker.is_alive():
                break
            try:
                self._queue.put(self._STOP, timeout=0.05)
                break
            except Full:
                continue
        self._worker.join(timeout=5.0)
        if self._worker.is_alive():
            raise RecorderError("recorder worker did not stop")
        with self._lock:
            self._closed = True
            self._raise_worker_error_locked()

    def _raise_worker_error_locked(self) -> None:
        if self._worker_error is not None:
            raise RecorderError("recorder worker failed") from self._worker_error

    def _run(self) -> None:
        iq_handle = chunks_handle = events_handle = None
        try:
            self.record_dir.mkdir(parents=True, exist_ok=True)
            iq_handle = self.iq_path.open("wb")
            chunks_handle = self.chunks_path.open("w", encoding="utf-8", newline="\n")
            events_handle = self.events_path.open("w", encoding="utf-8", newline="\n")
            byte_offset = 0
            while True:
                item = self._queue.get()
                try:
                    if item is self._STOP:
                        break
                    item_kind, value = item
                    if item_kind == "chunk":
                        chunk = value
                        raw = chunk.samples.astype("<c8", copy=False).tobytes(order="C")
                        iq_handle.write(raw)
                        metadata = {
                            "chunk_id": chunk.chunk_id,
                            "first_sample_index": chunk.first_sample_index,
                            "sample_rate_hz": chunk.sample_rate_hz,
                            "rx_wall_time": chunk.rx_wall_time,
                            "rx_monotonic_ns": chunk.rx_monotonic_ns,
                            "lo_hz": chunk.lo_hz,
                            "rf_bandwidth_hz": chunk.rf_bandwidth_hz,
                            "rx_gain_db": chunk.rx_gain_db,
                            "target_version": chunk.target_version,
                            "context_version": chunk.context_version,
                            "rf_metrics": None if chunk.rf_metrics is None else asdict(chunk.rf_metrics),
                            "sample_count": int(chunk.samples.size),
                            "byte_offset": byte_offset,
                            "byte_length": len(raw),
                        }
                        chunks_handle.write(_json_line(metadata))
                        byte_offset += len(raw)
                        with self._lock:
                            self._chunks_written += 1
                            self._samples_written += int(chunk.samples.size)
                            self._bytes_written += len(raw)
                    else:
                        events_handle.write(_json_line(value))
                        with self._lock:
                            self._events_written += 1
                finally:
                    self._queue.task_done()

            with self._lock:
                dropped_chunks = self._dropped_chunks
                dropped_events = self._dropped_events
            if dropped_chunks or dropped_events:
                events_handle.write(
                    _json_line(
                        {
                            "kind": "recorder_queue_overflow",
                            "payload": {
                                "dropped_chunks": dropped_chunks,
                                "dropped_events": dropped_events,
                                "total_drops": dropped_chunks + dropped_events,
                            },
                            "wall_time": time.time(),
                            "monotonic_ns": time.monotonic_ns(),
                        }
                    )
                )
                with self._lock:
                    self._events_written += 1

            for handle in (iq_handle, chunks_handle, events_handle):
                handle.flush()
                os.fsync(handle.fileno())
            self._write_summary(self._stopped_reason)
        except BaseException as exc:
            with self._lock:
                self._worker_error = exc
        finally:
            for handle in (iq_handle, chunks_handle, events_handle):
                if handle is not None:
                    try:
                        handle.close()
                    except Exception:
                        pass

    def _write_summary(self, stopped_reason: str) -> None:
        with self._lock:
            payload = {
                **self._summary_metadata,
                "format": "numpy.complex64 little-endian interleaved IQ",
                "files": {
                    "iq": self.iq_path.name,
                    "chunks": self.chunks_path.name,
                    "events": self.events_path.name,
                    "summary": self.summary_path.name,
                },
                "chunks_written": self._chunks_written,
                "events_written": self._events_written,
                "samples_written": self._samples_written,
                "bytes_written": self._bytes_written,
                "dropped_chunks": self._dropped_chunks,
                "dropped_events": self._dropped_events,
                "queue_overflows": self._dropped_chunks + self._dropped_events,
                "started_wall_time": self._started_wall_time,
                "closed_wall_time": time.time(),
                "stopped_reason": stopped_reason,
            }
        with self.summary_path.open("w", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())


def _json_line(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"


def _json_snapshot(value: object) -> object:
    if value is None or type(value) in (str, bool, int, float):
        return value
    if isinstance(value, np.generic):
        return _json_snapshot(value.item())
    if isinstance(value, numbers.Integral):
        return int(value)
    if isinstance(value, numbers.Real):
        return float(value)
    if isinstance(value, Enum):
        return _json_snapshot(value.value)
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, bytes):
        return value.hex()
    if isinstance(value, Mapping):
        return {str(key): _json_snapshot(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_snapshot(item) for item in value]
    return str(value)
