"""Pure decoder plugin for the improved v67 receiver core."""

from __future__ import annotations

import threading

from .models import DecodedCommand, DecodeContext, DecoderStats, IqChunk, ResetReason
from .patches import JamKeyEvent, PatchCallbacks, RawFrameEvent


class V67Decoder:
    """Convert v67 demodulator events into the common decoder contract."""

    decoder_id = "improved_v67"

    def __init__(self, *, core) -> None:
        self._core = core
        self._stats_lock = threading.Lock()
        self._chunks_processed = 0
        self._samples_processed = 0
        self._commands_emitted = 0
        self._decode_errors = 0
        self._resets = 0

    def decode(
        self,
        chunk: IqChunk,
        context: DecodeContext,
    ) -> list[DecodedCommand]:
        commands: list[DecodedCommand] = []
        seen_events: list[object] = []
        seen_event_ids: set[int] = set()

        def first_observation(event: object) -> bool:
            event_id = id(event)
            if event_id in seen_event_ids:
                return False
            seen_event_ids.add(event_id)
            seen_events.append(event)
            return True

        def on_jam_key(event: JamKeyEvent) -> None:
            if not first_observation(event):
                return
            commands.append(
                DecodedCommand(
                    cmd_id=event.cmd_id,
                    payload=bytes(event.key[:6]),
                    decoder_id=self.decoder_id,
                    profile=context.profile,
                    crc8_ok=True,
                    crc16_ok=True,
                    crc_mode="v67_core_validated",
                    first_sample_index=chunk.first_sample_index,
                    last_sample_index=chunk.first_sample_index + len(chunk.samples) - 1,
                    receive_wall_time=chunk.rx_wall_time,
                    target=context.target,
                    team=context.team,
                    context_version=context.context_version,
                    evidence={
                        "event_type": "jam_key",
                        "source": event.source,
                        "source_target": event.target,
                        "event_team": event.team,
                        "level": event.level,
                        "ascii": event.ascii_code,
                        "event_timestamp": event.timestamp,
                    },
                )
            )

        def on_raw_frame(event: RawFrameEvent) -> None:
            if not first_observation(event):
                return
            commands.append(
                DecodedCommand(
                    cmd_id=event.cmd_id,
                    payload=bytes(event.payload),
                    decoder_id=self.decoder_id,
                    profile=context.profile,
                    crc8_ok=event.crc8_ok,
                    crc16_ok=event.crc16_ok,
                    crc_mode="v67_core_validated",
                    first_sample_index=chunk.first_sample_index,
                    last_sample_index=chunk.first_sample_index + len(chunk.samples) - 1,
                    receive_wall_time=chunk.rx_wall_time,
                    target=context.target,
                    team=context.team,
                    context_version=context.context_version,
                    evidence={
                        "event_type": "raw_frame",
                        "source": event.source,
                        "source_target": event.source_target,
                        "event_team": event.team,
                        "air_chunk_index": event.air_chunk_index,
                        "event_timestamp": event.timestamp,
                    },
                )
            )

        try:
            self._core.demodulate_iq(
                samples=chunk.samples,
                profile=self._profile(context),
                callbacks=PatchCallbacks(
                    on_jam_key=on_jam_key,
                    on_raw_frame=on_raw_frame,
                ),
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

    def stats(self) -> DecoderStats:
        with self._stats_lock:
            return DecoderStats(
                chunks_processed=self._chunks_processed,
                samples_processed=self._samples_processed,
                commands_emitted=self._commands_emitted,
                decode_errors=self._decode_errors,
                resets=self._resets,
            )

    def reset(self, reason: ResetReason, context: DecodeContext) -> None:
        with self._stats_lock:
            self._resets += 1
        reset_decoder = getattr(self._core, "reset_decoder", None)
        if not callable(reset_decoder):
            return
        try:
            reset_decoder(
                reason=reason,
                profile=self._profile(context),
            )
        except Exception:
            with self._stats_lock:
                self._decode_errors += 1
            raise

    @staticmethod
    def _profile(context: DecodeContext) -> dict[str, str]:
        return {
            "name": context.profile,
            "team": context.team,
            "target": context.target,
        }
