"""Pure adapter boundary for an optionally supplied frame decoder."""

from __future__ import annotations

from dataclasses import dataclass
import threading
from types import MappingProxyType

import numpy as np

from .models import (
    DecodedCommand,
    DecodeContext,
    DecoderStats,
    IqChunk,
    ResetReason,
)


@dataclass(frozen=True)
class ActiveProfile:
    """Normalized RF profile metadata selected by a decoder reset."""

    name: str
    team: str
    target: str
    center_freq: int
    level: int


@dataclass(frozen=True)
class VerifiedParsedFrame:
    """Backend-owned claim that an upstream parser verified one frame."""

    cmd_id: int
    data: object
    seq: int
    crc8_ok: bool
    crc16_ok: bool
    crc_mode: str


_PROFILE_FREQUENCIES = MappingProxyType(
    {
        ("RED", "L1"): (432_200_000, 1),
        ("RED", "L2"): (432_500_000, 2),
        ("RED", "L3"): (432_800_000, 3),
        ("BLUE", "L1"): (434_920_000, 1),
        ("BLUE", "L2"): (434_620_000, 2),
        ("BLUE", "L3"): (434_320_000, 3),
    }
)
_MISSING = object()
_MAX_PAYLOAD_BYTES = 256
_MAX_FRAMES_PER_CHUNK = 64


class UpstreamDecoderUnavailableError(RuntimeError):
    """Raised when decode is requested without a supplied pure backend."""


def _validated_frame_fields(
    frame,
) -> tuple[int, bytes, int, bool, bool, str]:
    if type(frame) is not VerifiedParsedFrame:
        raise TypeError("backend frames must be exact VerifiedParsedFrame")
    if type(frame.crc8_ok) is not bool or frame.crc8_ok is not True:
        raise TypeError("verified frame crc8_ok must be exact True")
    if type(frame.crc16_ok) is not bool or frame.crc16_ok is not True:
        raise TypeError("verified frame crc16_ok must be exact True")
    if type(frame.crc_mode) is not str or frame.crc_mode != "kermit-x3014":
        raise ValueError(
            "verified frame crc_mode must be exact 'kermit-x3014'"
        )

    cmd_id = frame.cmd_id
    if type(cmd_id) is not int:
        raise TypeError("frame cmd_id must be an exact int")
    if not 0 <= cmd_id <= 0xFFFF:
        raise ValueError("frame cmd_id must be in range 0..65535")

    seq = frame.seq
    if type(seq) is not int:
        raise TypeError("frame seq must be an exact int")
    if not 0 <= seq <= 0xFF:
        raise ValueError("frame seq must be in range 0..255")

    data = frame.data
    if type(data) in (bytes, bytearray):
        if len(data) > _MAX_PAYLOAD_BYTES:
            raise ValueError("frame data payload exceeds 256 bytes")
        payload = bytes(data)
    elif type(data) is memoryview:
        try:
            ndim = data.ndim
            itemsize = data.itemsize
            data_format = data.format
            contiguous = data.c_contiguous
            payload_size = data.nbytes
        except ValueError as error:
            raise ValueError("frame data memoryview is unavailable") from error
        if (
            ndim != 1
            or itemsize != 1
            or data_format not in ("B", "b", "c")
            or not contiguous
        ):
            raise ValueError(
                "frame data memoryview must be one-dimensional bytes"
            )
        if payload_size > _MAX_PAYLOAD_BYTES:
            raise ValueError("frame data payload exceeds 256 bytes")
        payload = data.tobytes()
    else:
        raise TypeError(
            "frame data must be bytes, bytearray, or memoryview"
        )
    return (
        cmd_id,
        payload,
        seq,
        frame.crc8_ok,
        frame.crc16_ok,
        frame.crc_mode,
    )


def _normalized_text(
    value,
    field_name: str,
    *,
    allow_empty: bool = False,
) -> str:
    if type(value) is not str:
        raise TypeError(f"context {field_name} must be an exact str")
    normalized = value.strip().upper()
    if not normalized and not allow_empty:
        raise ValueError(f"context {field_name} must not be empty")
    return normalized


class UpstreamDecoder:
    """Stateful pure-compute adapter for an external frame-decoder backend."""

    decoder_id = "combat_radar_sdr_13b13a6"

    def __init__(self, *, backend=None) -> None:
        self._backend = backend
        self._operation_lock = threading.Lock()
        self._stats_lock = threading.Lock()
        self._active_profile: ActiveProfile | None = None
        self._chunks_processed = 0
        self._samples_processed = 0
        self._commands_emitted = 0
        self._decode_errors = 0
        self._resets = 0

    @property
    def active_profile(self) -> ActiveProfile | None:
        with self._stats_lock:
            return self._active_profile

    def decode(
        self,
        chunk: IqChunk,
        context: DecodeContext,
    ) -> list[DecodedCommand]:
        with self._operation_lock:
            return self._decode_locked(chunk, context)

    def _decode_locked(
        self,
        chunk: IqChunk,
        context: DecodeContext,
    ) -> list[DecodedCommand]:
        try:
            with self._stats_lock:
                profile = self._active_profile
            if profile is None:
                raise RuntimeError("reset must succeed before decode")
            if type(context) is not DecodeContext:
                raise TypeError(
                    "decode context must be an exact DecodeContext"
                )
            team = _normalized_text(context.team, "team", allow_empty=True)
            target = _normalized_text(
                context.target,
                "target",
                allow_empty=True,
            )
            _normalized_text(context.profile, "profile")
            normalized_context = (
                team,
                target,
            )
            active_context = (profile.team, profile.target)
            if normalized_context != active_context:
                raise ValueError(
                    "decode context does not match active profile"
                )
            if chunk.samples.size == 0:
                raise ValueError("IQ chunk samples must not be empty")
            if (
                not np.isfinite(chunk.samples.real).all()
                or not np.isfinite(chunk.samples.imag).all()
            ):
                raise ValueError(
                    "IQ chunk samples must contain only finite values"
                )
            decode_backend = getattr(self._backend, "decode", _MISSING)
            if decode_backend is _MISSING:
                if callable(self._backend):
                    decode_backend = self._backend
                else:
                    raise UpstreamDecoderUnavailableError(
                        "upstream frame-decoder backend is unavailable; "
                        "inject a pure backend when constructing "
                        "UpstreamDecoder"
                    )
            elif not callable(decode_backend):
                raise TypeError("backend decode hook must be callable")
            backend_samples = np.array(
                chunk.samples,
                dtype=np.complex64,
                order="C",
                copy=True,
            )
            backend_samples.flags.writeable = False
            frames = decode_backend(
                samples=backend_samples,
                sample_rate_hz=chunk.sample_rate_hz,
                profile=profile,
            )
            commands = []
            for frame_index, frame in enumerate(frames):
                if frame_index >= _MAX_FRAMES_PER_CHUNK:
                    raise ValueError(
                        "backend may return at most 64 frames per chunk"
                    )
                (
                    cmd_id,
                    payload,
                    seq,
                    crc8_ok,
                    crc16_ok,
                    crc_mode,
                ) = _validated_frame_fields(frame)
                evidence = {"upstream_seq": seq}
                if cmd_id == 0x0A06:
                    evidence["level"] = profile.level
                commands.append(
                    DecodedCommand(
                        cmd_id=cmd_id,
                        payload=payload,
                        decoder_id=self.decoder_id,
                        profile=profile.name,
                        crc8_ok=crc8_ok,
                        crc16_ok=crc16_ok,
                        crc_mode=crc_mode,
                        first_sample_index=chunk.first_sample_index,
                        last_sample_index=(
                            chunk.first_sample_index + len(chunk.samples) - 1
                        ),
                        receive_wall_time=chunk.rx_wall_time,
                        target=profile.target,
                        team=profile.team,
                        context_version=context.context_version,
                        evidence=evidence,
                    )
                )
        except Exception:
            with self._stats_lock:
                self._decode_errors += 1
            raise
        with self._stats_lock:
            self._chunks_processed += 1
            self._samples_processed += len(chunk.samples)
            self._commands_emitted += len(commands)
        return commands

    def reset(self, reason: ResetReason, context: DecodeContext) -> None:
        with self._operation_lock:
            self._reset_locked(reason, context)

    def _reset_locked(
        self,
        reason: ResetReason,
        context: DecodeContext,
    ) -> None:
        try:
            if not isinstance(reason, ResetReason):
                raise TypeError("reset reason must be a ResetReason")
            if type(context) is not DecodeContext:
                raise TypeError("reset context must be an exact DecodeContext")
            team = _normalized_text(context.team, "team", allow_empty=True)
            target = _normalized_text(
                context.target,
                "target",
                allow_empty=True,
            )
            _normalized_text(context.profile, "profile")
            try:
                center_freq, level = _PROFILE_FREQUENCIES[(team, target)]
            except KeyError:
                raise ValueError(
                    "unsupported upstream profile: "
                    f"team={team!r}, target={target!r}"
                ) from None
            profile = ActiveProfile(
                name=f"{team}-{target}",
                team=team,
                target=target,
                center_freq=center_freq,
                level=level,
            )
            reset_backend = getattr(self._backend, "reset", _MISSING)
            if reset_backend is not _MISSING:
                if not callable(reset_backend):
                    raise TypeError("backend reset hook must be callable")
                reset_backend(reason=reason, profile=profile)
        except Exception:
            with self._stats_lock:
                self._decode_errors += 1
            raise
        with self._stats_lock:
            self._active_profile = profile
            self._resets += 1

    def stats(self) -> DecoderStats:
        with self._stats_lock:
            return DecoderStats(
                chunks_processed=self._chunks_processed,
                samples_processed=self._samples_processed,
                commands_emitted=self._commands_emitted,
                decode_errors=self._decode_errors,
                resets=self._resets,
            )
