"""Validation boundary for decoded production commands."""

from __future__ import annotations

from dataclasses import dataclass, field

from .models import DecodedCommand


@dataclass(frozen=True)
class ValidationResult:
    """Immutable outcome consumed by the production ROS publisher."""

    accepted: bool
    reason: str
    ascii_code: str | None = None
    level: int | None = None
    _dedup_key: tuple[int, bytes, int] | None = field(
        default=None,
        repr=False,
        compare=False,
    )
    _publish_authorization: object | None = field(
        default=None,
        repr=False,
        compare=False,
    )


class CommandValidator:
    """Validate and de-duplicate decoded commands for production output."""

    def __init__(self) -> None:
        self._accepted_keys: set[tuple[int, bytes, int]] = set()
        self._publish_authorizations: set[object] = set()

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
        if key in self._accepted_keys:
            return ValidationResult(
                False,
                "duplicate command: cmd_id/payload/target_level already accepted",
                ascii_code=command.payload.decode("ascii"),
                level=level,
            )
        self._accepted_keys.add(key)
        authorization = object()
        self._publish_authorizations.add(authorization)
        return ValidationResult(
            True,
            "accepted",
            ascii_code=command.payload.decode("ascii"),
            level=level,
            _dedup_key=key,
            _publish_authorization=authorization,
        )

    def consume_publish_authorization(
        self,
        command: DecodedCommand,
        result: ValidationResult,
    ) -> bool:
        """Consume the one-shot authorization attached to an accepted result."""

        authorization = result._publish_authorization
        if (
            result.accepted is not True
            or authorization is None
            or authorization not in self._publish_authorizations
            or result.level is None
            or result._dedup_key
            != (command.cmd_id, command.payload, result.level)
        ):
            return False
        self._publish_authorizations.remove(authorization)
        return True
