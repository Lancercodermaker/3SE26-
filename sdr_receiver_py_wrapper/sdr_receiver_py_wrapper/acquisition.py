"""Nonblocking transfer of device IQ reads into a bounded queue."""

import time
from dataclasses import dataclass
from queue import Full, Queue

import numpy as np

from .device_session import DeviceReadError
from .models import IqChunk


@dataclass(frozen=True)
class AcquisitionStats:
    """Immutable snapshot of acquisition counters."""

    queue_drops: int = 0
    read_errors: int = 0
    reconnects: int = 0


class AcquisitionEngine:
    """Read device buffers without waiting for queue consumers."""

    def __init__(
        self,
        device,
        queue_size,
        *,
        target_version=0,
        context_version=0,
    ):
        if queue_size <= 0:
            raise ValueError("queue_size must be positive")
        self._device = device
        self._queue = Queue(maxsize=queue_size)
        self._queue_drops = 0
        self._read_errors = 0
        self._reconnects = 0
        self._next_chunk_id = 0
        self._next_sample_index = 0
        self._target_version = target_version
        self._context_version = context_version

    @property
    def stats(self):
        return AcquisitionStats(
            queue_drops=self._queue_drops,
            read_errors=self._read_errors,
            reconnects=self._reconnects,
        )

    def read_once(self):
        try:
            samples = self._device.read()
        except (OSError, DeviceReadError):
            self._read_errors += 1
            if self._device.reconnect() is True:
                self._reconnects += 1
            return None
        try:
            if not isinstance(samples, np.ndarray):
                raise TypeError("device IQ must be a numpy.ndarray")
            if samples.dtype != np.complex64:
                raise ValueError("device IQ must be complex64")
            if samples.ndim != 1:
                raise ValueError("device IQ must be one-dimensional")
        except (TypeError, ValueError):
            self._read_errors += 1
            raise
        owned_samples = np.array(samples, copy=True, order="C")
        owned_samples.setflags(write=False)
        snapshot = self._device.snapshot()
        chunk = IqChunk(
            chunk_id=self._next_chunk_id,
            first_sample_index=self._next_sample_index,
            samples=owned_samples,
            sample_rate_hz=snapshot["sample_rate_hz"],
            rx_wall_time=time.time(),
            rx_monotonic_ns=time.monotonic_ns(),
            lo_hz=snapshot["lo_hz"],
            rf_bandwidth_hz=snapshot["rf_bandwidth_hz"],
            rx_gain_db=snapshot["rx_gain_db"],
            target_version=self._target_version,
            context_version=self._context_version,
        )
        self._next_chunk_id += 1
        self._next_sample_index += len(owned_samples)
        try:
            self._queue.put_nowait(chunk)
        except Full:
            self._queue_drops += 1
            return None
        return chunk

    def get_nowait(self):
        return self._queue.get_nowait()
