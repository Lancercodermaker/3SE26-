"""Pure adapter boundary for an optionally supplied frame decoder."""

from __future__ import annotations

from dataclasses import dataclass
import threading

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


_PROFILE_FREQUENCIES = {
    ("RED", "L1"): 432_200_000,
    ("RED", "L2"): 432_500_000,
    ("RED", "L3"): 432_800_000,
    ("BLUE", "L1"): 434_920_000,
    ("BLUE", "L2"): 434_620_000,
    ("BLUE", "L3"): 434_320_000,
}


class UpstreamDecoderUnavailableError(RuntimeError):
    """Raised when decode is requested without a supplied pure backend."""


class UpstreamDecoder:
    """Stateful pure-compute adapter for an external frame-decoder backend."""

    decoder_id = "combat_radar_sdr_13b13a6"

    def __init__(self, *, backend=None) -> None:
        self._backend = backend
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
        try:
            with self._stats_lock:
                profile = self._active_profile
            if profile is None:
                raise RuntimeError("reset must succeed before decode")
            normalized_context = (
                context.team.strip().upper(),
                context.target.strip().upper(),
                context.profile.strip().upper(),
            )
            active_context = (profile.team, profile.target, profile.name)
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
            decode_backend = getattr(self._backend, "decode", None)
            if not callable(decode_backend) and callable(self._backend):
                decode_backend = self._backend
            if not callable(decode_backend):
                raise UpstreamDecoderUnavailableError(
                    "upstream frame-decoder backend is unavailable; "
                    "inject a pure backend when constructing UpstreamDecoder"
                )
            frames = decode_backend(
                samples=chunk.samples,
                sample_rate_hz=chunk.sample_rate_hz,
                profile=profile,
            )
            commands = [
                DecodedCommand(
                    cmd_id=frame.cmd_id,
                    payload=bytes(frame.data),
                    decoder_id=self.decoder_id,
                    profile=profile.name,
                    crc8_ok=True,
                    crc16_ok=True,
                    crc_mode="kermit-x3014",
                    first_sample_index=chunk.first_sample_index,
                    last_sample_index=(
                        chunk.first_sample_index + len(chunk.samples) - 1
                    ),
                    receive_wall_time=chunk.rx_wall_time,
                    target=profile.target,
                    team=profile.team,
                    context_version=context.context_version,
                    evidence={"upstream_seq": frame.seq},
                )
                for frame in frames
            ]
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
        team = context.team.strip().upper()
        target = context.target.strip().upper()
        try:
            center_freq = _PROFILE_FREQUENCIES[(team, target)]
        except KeyError:
            with self._stats_lock:
                self._decode_errors += 1
            raise ValueError(
                "unsupported upstream profile: "
                f"team={team!r}, target={target!r}"
            ) from None
        profile = ActiveProfile(
            name=f"{team}-{target}",
            team=team,
            target=target,
            center_freq=center_freq,
        )
        reset_backend = getattr(self._backend, "reset", None)
        if callable(reset_backend):
            try:
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
