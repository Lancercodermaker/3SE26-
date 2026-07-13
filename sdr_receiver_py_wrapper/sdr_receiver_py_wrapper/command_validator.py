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


class CommandValidator:
    """Validate and de-duplicate decoded commands for production output.

    Pending publication pairs are retained strongly for identity checks and
    bounded so direct validation clients cannot grow authorization state
    without limit. Accepted de-duplication keys intentionally last for the
    validator instance lifetime.
    """

    _MAX_PENDING_AUTHORIZATIONS = 1024

    def __init__(self) -> None:
        self._accepted_keys: set[tuple[int, bytes, int]] = set()
        self._pending_authorizations: OrderedDict[
            int,
            tuple[ValidationResult, DecodedCommand],
        ] = OrderedDict()
        self._lock = threading.Lock()

    def validate(self, command: DecodedCommand) -> ValidationResult:
        if type(command.crc8_ok) is not bool or command.crc8_ok is not True:
            return ValidationResult(False, "crc8_ok must be exact True")
        if type(command.crc16_ok) is not bool or command.crc16_ok is not True:
            return ValidationResult(False, "crc16_ok must be exact True")
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
        key = (command.cmd_id, command.payload, level)
        with self._lock:
            if key in self._accepted_keys:
                return ValidationResult(
                    False,
                    "duplicate command: cmd_id/payload/target_level already accepted",
                    ascii_code=command.payload.decode("ascii"),
                    level=level,
                )
            self._accepted_keys.add(key)
            result = ValidationResult(
                True,
                "accepted",
                ascii_code=command.payload.decode("ascii"),
                level=level,
            )
            self._pending_authorizations[id(result)] = (result, command)
            while (
                len(self._pending_authorizations)
                > self._MAX_PENDING_AUTHORIZATIONS
            ):
                self._pending_authorizations.popitem(last=False)
            return result

    def consume_publish_authorization(
        self,
        command: DecodedCommand,
        result: ValidationResult,
    ) -> bool:
        """Consume the one-shot authorization attached to an accepted result."""

        with self._lock:
            pending = self._pending_authorizations.get(id(result))
            if (
                pending is None
                or pending[0] is not result
                or pending[1] is not command
            ):
                return False
            del self._pending_authorizations[id(result)]
            return True

    def discard_publish_authorization(
        self,
        command: DecodedCommand,
        result: ValidationResult,
    ) -> bool:
        """Discard an exact pending result when ROS output is disabled."""

        return self.consume_publish_authorization(command, result)
