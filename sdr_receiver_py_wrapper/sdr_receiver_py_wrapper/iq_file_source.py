from __future__ import annotations

import os
from pathlib import Path
import time
from typing import Callable, Optional

import numpy as np


class IqFilePluto:
    """Small Pluto-compatible source that replays little-endian complex64 IQ files."""

    def __init__(
        self,
        path: str,
        *,
        loop: bool = True,
        throttle: bool = True,
        center_hz: float = 0.0,
        start_offset_sec: float = 0.0,
        logger: Optional[Callable[[str], None]] = None,
    ) -> None:
        self.path = Path(os.path.expandvars(str(path))).expanduser()
        self.loop = bool(loop)
        self.throttle = bool(throttle)
        self.center_hz = float(center_hz)
        self.logger = logger or (lambda _message: None)

        if not self.path.is_file():
            raise FileNotFoundError(f"IQ source file not found: {self.path}")

        self._samples = np.memmap(self.path, dtype=np.dtype("<c8"), mode="r")
        if self._samples.size <= 0:
            raise ValueError(f"IQ source file is empty: {self.path}")

        self._sample_rate = 2_500_000.0
        self.rx_buffer_size = 160_000
        self.gain_control_mode_chan0 = "manual"
        self.rx_hardwaregain_chan0 = 0
        self.rx_rf_bandwidth = 0
        self.rx_lo = 0
        self.tx_lo = 0
        self.tx_hardwaregain_chan0 = 0
        self.filter = ""

        self._index = 0
        self._absolute_sample = 0
        self._last_rx_wall: Optional[float] = None
        self._eof_reported = False
        self._pending_start_offset_sec: Optional[float] = float(start_offset_sec)
        self.seek_seconds(start_offset_sec)
        self.logger(
            "IQ file source enabled: "
            f"path={self.path} samples={self._samples.size} loop={self.loop} "
            f"throttle={self.throttle} center_hz={self.center_hz:.0f}"
        )

    @property
    def samples_total(self) -> int:
        return int(self._samples.size)

    @property
    def position(self) -> int:
        return int(self._index)

    @property
    def sample_rate(self) -> int:
        return int(round(self._sample_rate))

    @sample_rate.setter
    def sample_rate(self, value) -> None:
        self._sample_rate = max(1.0, float(value))
        if self._pending_start_offset_sec is not None:
            offset = self._pending_start_offset_sec
            self._pending_start_offset_sec = None
            self.seek_seconds(offset)

    def seek_seconds(self, offset_sec: float) -> None:
        sample_rate = max(1.0, float(self._sample_rate))
        start = int(round(max(0.0, float(offset_sec)) * sample_rate))
        self._index = min(start, max(0, self._samples.size - 1))
        self._absolute_sample = self._index

    def rx(self) -> np.ndarray:
        block_size = max(1, int(getattr(self, "rx_buffer_size", 160_000) or 160_000))
        self._throttle(block_size)
        raw = self._read_block(block_size)
        shifted = self._apply_virtual_lo(raw)
        self._absolute_sample += int(raw.size)
        return shifted

    def close(self) -> None:
        mmap_obj = getattr(getattr(self, "_samples", None), "_mmap", None)
        if mmap_obj is not None:
            mmap_obj.close()

    def __del__(self) -> None:
        try:
            self.close()
        except Exception:
            pass

    def _read_block(self, block_size: int) -> np.ndarray:
        if self._index >= self._samples.size:
            if self.loop:
                self._index = 0
            else:
                self._report_eof_once()
                return np.zeros(block_size, dtype=np.complex64)

        remaining = int(self._samples.size - self._index)
        take = min(block_size, remaining)
        chunks = [np.asarray(self._samples[self._index : self._index + take], dtype=np.complex64)]
        self._index += take

        while sum(chunk.size for chunk in chunks) < block_size:
            needed = block_size - sum(chunk.size for chunk in chunks)
            if not self.loop:
                self._report_eof_once()
                chunks.append(np.zeros(needed, dtype=np.complex64))
                break
            self._index = 0
            take = min(needed, int(self._samples.size))
            chunks.append(np.asarray(self._samples[:take], dtype=np.complex64))
            self._index = take

        if len(chunks) == 1:
            return chunks[0]
        return np.concatenate(chunks).astype(np.complex64, copy=False)

    def _apply_virtual_lo(self, raw: np.ndarray) -> np.ndarray:
        if not self.center_hz or not getattr(self, "rx_lo", 0):
            return np.asarray(raw, dtype=np.complex64)
        sample_rate = float(getattr(self, "sample_rate", 0) or 0)
        if sample_rate <= 0.0:
            return np.asarray(raw, dtype=np.complex64)
        delta_hz = float(getattr(self, "rx_lo", 0)) - self.center_hz
        if not delta_hz:
            return np.asarray(raw, dtype=np.complex64)
        n = np.arange(raw.size, dtype=np.float64) + float(self._absolute_sample)
        return (
            np.asarray(raw, dtype=np.complex64)
            * np.exp(-1j * 2.0 * np.pi * delta_hz * n / sample_rate)
        ).astype(np.complex64)

    def _throttle(self, block_size: int) -> None:
        if not self.throttle:
            return
        sample_rate = float(getattr(self, "sample_rate", 0) or 0)
        if sample_rate <= 0.0:
            return
        now = time.perf_counter()
        if self._last_rx_wall is None:
            self._last_rx_wall = now
            return
        target_interval = float(block_size) / sample_rate
        elapsed = now - self._last_rx_wall
        if elapsed < target_interval:
            time.sleep(target_interval - elapsed)
        self._last_rx_wall = time.perf_counter()

    def _report_eof_once(self) -> None:
        if self._eof_reported:
            return
        self._eof_reported = True
        self.logger(f"IQ file source reached EOF and loop is disabled: {self.path}")
