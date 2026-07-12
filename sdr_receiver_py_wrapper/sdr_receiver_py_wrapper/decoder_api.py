"""Pure-compute contract implemented by receiver decoder plugins."""

from typing import Protocol

from .models import (
    DecodedCommand,
    DecodeContext,
    DecoderStats,
    IqChunk,
    ResetReason,
)


class DecoderPlugin(Protocol):
    """Structural interface for a stateful IQ decoder."""

    decoder_id: str

    def decode(
        self,
        chunk: IqChunk,
        context: DecodeContext,
    ) -> list[DecodedCommand]: ...

    def reset(self, reason: ResetReason, context: DecodeContext) -> None: ...

    def stats(self) -> DecoderStats: ...
