"""Immutable data contracts shared by receiver components."""

from dataclasses import dataclass
from enum import Enum
from types import MappingProxyType
from typing import Mapping

import numpy as np
import numpy.typing as npt


@dataclass(frozen=True)
class RfMetrics:
    """Measurements needed to classify one IQ chunk's RF condition."""

    rms: float
    peak: float
    clipping_ratio: float
    sample_count: int


@dataclass(frozen=True)
class IqChunk:
    """A zero-copy view of one contiguous block of received IQ samples.

    ``samples`` must be a read-only ``complex64`` array. Requiring the producer
    to make the array read-only avoids both a high-throughput copy and a hidden
    mutation of the caller's array flags.
    """

    chunk_id: int
    first_sample_index: int
    samples: npt.NDArray[np.complex64]
    sample_rate_hz: int
    rx_wall_time: float
    rx_monotonic_ns: int
    lo_hz: int
    rf_bandwidth_hz: int
    rx_gain_db: int
    target_version: int
    context_version: int
    rf_metrics: RfMetrics | None = None

    def __post_init__(self) -> None:
        if self.samples.dtype != np.complex64:
            raise ValueError("IqChunk samples must be complex64")
        if self.samples.flags.writeable:
            raise ValueError("IqChunk samples must be read-only")


@dataclass(frozen=True)
class DecodeContext:
    """Authoritative team, target, and profile snapshot for decoding."""

    team: str
    target: str
    profile: str
    target_version: int
    context_version: int


@dataclass(frozen=True)
class DecodedCommand:
    """A decoder result with the evidence needed for later validation."""

    cmd_id: int
    payload: bytes
    decoder_id: str
    profile: str
    crc8_ok: bool
    crc16_ok: bool
    crc_mode: str
    first_sample_index: int
    last_sample_index: int
    receive_wall_time: float
    target: str
    team: str
    context_version: int
    evidence: Mapping[str, object]

    def __post_init__(self) -> None:
        if not isinstance(self.payload, bytes):
            raise TypeError("DecodedCommand payload must be bytes")
        object.__setattr__(
            self,
            "evidence",
            MappingProxyType(dict(self.evidence)),
        )


@dataclass(frozen=True)
class DecoderStats:
    """Immutable snapshot of common decoder counters."""

    chunks_processed: int = 0
    samples_processed: int = 0
    commands_emitted: int = 0
    decode_errors: int = 0
    resets: int = 0


class ResetReason(str, Enum):
    """Stable reasons a decoder must discard state."""

    STARTUP = "startup"
    CONTEXT_CHANGE = "context_change"
    TARGET_CHANGE = "target_change"
    DEVICE_RECONNECT = "device_reconnect"
    MANUAL = "manual"
