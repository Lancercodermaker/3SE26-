"""Validation boundary for decoded production commands."""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
import threading

from .models import DecodedCommand


@dataclass(frozen=True)
class ValidationResult:
    """Immutable outcome consumed by the production ROS publisher."""

    accepted: bool
    reason: str
    ascii_code: str | None = None
    level: int | None = None


@dataclass
class _PendingAuthorization:
    result: ValidationResult
    command: DecodedCommand
    key: tuple[int, bytes, int]
    retain_dedup_after_commit: bool
    publishing: bool = False


class CommandValidator:
    """Validate commands and manage bounded publication transactions.

    ``validate`` is the standalone/plugin path: a committed key remains in a
    bounded de-duplication window. ``reserve_controller_publication`` is the
    legacy path: only an in-flight duplicate is suppressed because the real
    competition controller owns rate limiting and retry policy.
    """

    _MAX_PENDING_AUTHORIZATIONS = 1024
    _MAX_COMMITTED_KEYS = 1024

    def __init__(self) -> None:
        self._committed_keys: OrderedDict[
            tuple[int, bytes, int],
            None,
        ] = OrderedDict()
        self._reserved_keys: set[tuple[int, bytes, int]] = set()
        self._pending_authorizations: dict[int, _PendingAuthorization] = {}
        self._lock = threading.Lock()

    def validate(self, command: DecodedCommand) -> ValidationResult:
        """Validate and reserve with standalone/plugin de-duplication."""

        return self._reserve(command, retain_dedup_after_commit=True)

    def reserve_controller_publication(
        self,
        command: DecodedCommand,
    ) -> ValidationResult:
        """Reserve a controller-approved retry without permanent de-dup."""

        return self._reserve(command, retain_dedup_after_commit=False)

    def _reserve(
        self,
        command: DecodedCommand,
        *,
        retain_dedup_after_commit: bool,
    ) -> ValidationResult:
        result = self.prevalidate(command)
        if not result.accepted:
            return result
        level = result.level
        assert level is not None
        key = (command.cmd_id, command.payload, level)
        with self._lock:
            if key in self._reserved_keys or (
                retain_dedup_after_commit and key in self._committed_keys
            ):
                return ValidationResult(
                    False,
                    "duplicate command: cmd_id/payload/target_level already accepted",
                    ascii_code=result.ascii_code,
                    level=level,
                )
            if (
                len(self._pending_authorizations)
                >= self._MAX_PENDING_AUTHORIZATIONS
            ):
                return ValidationResult(
                    False,
                    "publication authorization capacity reached",
                    ascii_code=result.ascii_code,
                    level=level,
                )
            self._reserved_keys.add(key)
            self._pending_authorizations[id(result)] = _PendingAuthorization(
                result=result,
                command=command,
                key=key,
                retain_dedup_after_commit=retain_dedup_after_commit,
            )
            return result

    def prevalidate(self, command: DecodedCommand) -> ValidationResult:
        """Validate command semantics without reserving publication state."""

        if type(command.crc8_ok) is not bool or command.crc8_ok is not True:
            return ValidationResult(False, "crc8_ok must be exact True")
        if type(command.crc16_ok) is not bool or command.crc16_ok is not True:
            return ValidationResult(False, "crc16_ok must be exact True")
        if type(command.cmd_id) is not int:
            return ValidationResult(False, "cmd_id must be an exact int")
        if not 0 <= command.cmd_id <= 0xFFFF:
            return ValidationResult(
                False,
                "cmd_id must be between 0x0000 and 0xFFFF",
            )
        if command.cmd_id != 0x0A06:
            return ValidationResult(
                False,
                f"unsupported cmd_id: 0x{command.cmd_id:04X}",
            )
        if len(command.payload) != 6:
            return ValidationResult(
                False,
                "0x0A06 payload must be exactly 6 bytes",
            )
        if not all(
            0x30 <= byte <= 0x39
            or 0x41 <= byte <= 0x5A
            or 0x61 <= byte <= 0x7A
            for byte in command.payload
        ):
            return ValidationResult(
                False,
                "0x0A06 payload must contain only ASCII letters or digits",
            )

        if "level" not in command.evidence:
            return ValidationResult(False, "0x0A06 evidence.level is missing")
        level = command.evidence["level"]
        if type(level) is not int:
            return ValidationResult(
                False,
                "0x0A06 evidence.level must be an exact int",
            )
        if level not in (1, 2, 3):
            return ValidationResult(
                False,
                "0x0A06 evidence.level must be between 1 and 3",
            )
        return ValidationResult(
            True,
            "accepted",
            ascii_code=command.payload.decode("ascii"),
            level=level,
        )

    def begin_publish_authorization(
        self,
        command: DecodedCommand,
        result: ValidationResult,
    ) -> bool:
        """Atomically claim an exact reservation without committing it."""

        with self._lock:
            pending = self._pending_authorizations.get(id(result))
            if (
                pending is None
                or pending.result is not result
                or pending.command is not command
                or pending.publishing
            ):
                return False
            pending.publishing = True
            return True

    def commit_publish_authorization(
        self,
        command: DecodedCommand,
        result: ValidationResult,
    ) -> bool:
        """Commit a claimed authorization after successful ROS publication."""

        with self._lock:
            pending = self._matching_pending(command, result)
            if pending is None or not pending.publishing:
                return False
            self._release_pending(result, pending)
            if pending.retain_dedup_after_commit:
                self._committed_keys[pending.key] = None
                self._committed_keys.move_to_end(pending.key)
                while len(self._committed_keys) > self._MAX_COMMITTED_KEYS:
                    self._committed_keys.popitem(last=False)
            return True

    def abort_publish_authorization(
        self,
        command: DecodedCommand,
        result: ValidationResult,
    ) -> bool:
        """Abort a reserved or claimed publication and release its key."""

        with self._lock:
            pending = self._matching_pending(command, result)
            if pending is None:
                return False
            self._release_pending(result, pending)
            return True

    def _matching_pending(
        self,
        command: DecodedCommand,
        result: ValidationResult,
    ) -> _PendingAuthorization | None:
        pending = self._pending_authorizations.get(id(result))
        if (
            pending is None
            or pending.result is not result
            or pending.command is not command
        ):
            return None
        return pending

    def _release_pending(
        self,
        result: ValidationResult,
        pending: _PendingAuthorization,
    ) -> None:
        del self._pending_authorizations[id(result)]
        self._reserved_keys.remove(pending.key)
