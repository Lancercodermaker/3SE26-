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
    """One contiguous block of IQ samples with transferred buffer ownership.

    The producer must transfer an exclusive, owning, one-dimensional C-order
    ``complex64`` array whose write flag is already disabled. This keeps the
    handoff zero-copy without changing caller-owned flags. NumPy cannot prevent
    a former owner from deliberately enabling writes again, so the producer
    must retain no aliases, must never restore write access, and must not reuse
    the buffer for the lifetime of this chunk. Acquisition code is responsible
    for copying inputs that cannot meet that ownership contract.
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
        if not isinstance(self.samples, np.ndarray):
            raise TypeError("IqChunk samples must be a numpy.ndarray")
        if self.samples.dtype != np.complex64:
            raise ValueError("IqChunk samples must be complex64")
        if self.samples.ndim != 1:
            raise ValueError("IqChunk samples must be one-dimensional")
        if not self.samples.flags.c_contiguous:
            raise ValueError("IqChunk samples must be C-contiguous")
        if not self.samples.flags.owndata or self.samples.base is not None:
            raise ValueError("IqChunk samples must own their backing buffer")
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
    """A decoder result with recursively frozen validation evidence.

    Evidence accepts JSON-like scalar values, mappings, lists, and tuples;
    mappings and sequences are recursively snapshotted. Recorders must convert
    the resulting read-only mappings and tuples explicitly for JSON output.
    Large or custom mutable objects, including arrays, are rejected instead of
    being copied implicitly.
    """

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
        if not isinstance(self.evidence, Mapping):
            raise TypeError("DecodedCommand evidence must be a mapping")
        object.__setattr__(
            self,
            "evidence",
            _freeze_evidence(self.evidence),
        )


def _freeze_evidence(value: object) -> object:
    if value is None or isinstance(value, (bool, int, float, str, bytes)):
        return value
    if isinstance(value, Mapping):
        frozen: dict[str, object] = {}
        for key, nested_value in value.items():
            if not isinstance(key, str):
                raise TypeError("DecodedCommand evidence mapping keys must be str")
            frozen[key] = _freeze_evidence(nested_value)
        return MappingProxyType(frozen)
    if isinstance(value, (list, tuple)):
        return tuple(_freeze_evidence(item) for item in value)
    raise TypeError(
        "unsupported evidence value type: "
        f"{type(value).__name__}"
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
