"""Strict, immutable contracts for verified IQ fixture manifests."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import re
from types import MappingProxyType
from typing import Mapping


_COMMON_FIELDS = frozenset(
    {"format", "sample_rate_hz", "team", "target", "verification"}
)
_VERIFIED_FIELDS = frozenset({"sha256", "expected_cmd_id", "expected_ascii"})
_SHA256_PATTERN = re.compile(r"[0-9a-f]{64}\Z")
_FIXTURE_NAME_PATTERN = re.compile(
    r"[A-Za-z0-9](?:[A-Za-z0-9_-]|\.(?=[A-Za-z0-9])){0,254}\Z"
)


class FixtureManifestError(ValueError):
    """The fixture manifest cannot be read or violates its schema."""


class _DuplicateJsonKeyError(ValueError):
    pass


@dataclass(frozen=True)
class FixtureSpec:
    """One validated IQ capture and, when confirmed, its expected result."""

    format: str
    sample_rate_hz: int
    team: str
    target: str
    verification: str
    sha256: str | None = None
    expected_cmd_id: int | None = None
    expected_ascii: str | None = None

    @property
    def requires_decode_assertion(self) -> bool:
        """Whether CI may require this fixture to decode to its expectations."""

        return self.verification == "confirmed"


def load_fixture_manifest(path: str | Path) -> Mapping[str, FixtureSpec]:
    """Read and validate a manifest, returning an immutable snapshot."""

    try:
        text = Path(path).read_text(encoding="utf-8")
    except (OSError, UnicodeError, TypeError, ValueError) as exc:
        raise FixtureManifestError(f"cannot read manifest {path!s}: {exc}") from exc

    try:
        raw = json.loads(
            text,
            object_pairs_hook=_object_without_duplicate_keys,
            parse_constant=_reject_nonstandard_json_constant,
        )
    except _DuplicateJsonKeyError as exc:
        raise FixtureManifestError(str(exc)) from exc
    except (json.JSONDecodeError, ValueError) as exc:
        raise FixtureManifestError(f"invalid JSON in manifest: {exc}") from exc

    if type(raw) is not dict:
        raise FixtureManifestError("manifest must be a top-level JSON object")
    if not raw:
        raise FixtureManifestError("manifest must contain at least one fixture")

    validated: dict[str, FixtureSpec] = {}
    for name, entry in raw.items():
        _validate_fixture_name(name)
        validated[name] = _validate_entry(name, entry)
    return MappingProxyType(validated)


def confirmed_fixtures(
    manifest: Mapping[str, FixtureSpec],
) -> Mapping[str, FixtureSpec]:
    """Return an immutable view containing only CI-required decode cases."""

    confirmed = {
        name: entry
        for name, entry in manifest.items()
        if entry.requires_decode_assertion
    }
    return MappingProxyType(confirmed)


def _object_without_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateJsonKeyError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _reject_nonstandard_json_constant(value: str) -> None:
    raise ValueError(f"non-standard JSON constant: {value}")


def _validate_fixture_name(name: object) -> None:
    if type(name) is not str or _FIXTURE_NAME_PATTERN.fullmatch(name) is None:
        raise FixtureManifestError(f"invalid fixture name: {name!r}")


def _validate_entry(name: str, raw: object) -> FixtureSpec:
    if type(raw) is not dict:
        raise FixtureManifestError(f"fixture {name!r} entry must be a JSON object")

    missing_common = _COMMON_FIELDS - raw.keys()
    if missing_common:
        _raise_missing(name, missing_common)

    verification = raw["verification"]
    if type(verification) is not str or verification not in {"candidate", "confirmed"}:
        raise FixtureManifestError(
            f"fixture {name!r} verification must be exactly 'candidate' or 'confirmed'"
        )

    if verification == "candidate":
        forbidden = _VERIFIED_FIELDS & raw.keys()
        if forbidden:
            fields = ", ".join(sorted(forbidden))
            raise FixtureManifestError(
                f"candidate fixture {name!r} must not contain unverified field(s): {fields}"
            )
        allowed = _COMMON_FIELDS
    else:
        missing_verified = _VERIFIED_FIELDS - raw.keys()
        if missing_verified:
            _raise_missing(name, missing_verified)
        allowed = _COMMON_FIELDS | _VERIFIED_FIELDS

    unknown = raw.keys() - allowed
    if unknown:
        fields = ", ".join(sorted(unknown))
        raise FixtureManifestError(f"fixture {name!r} has unknown field(s): {fields}")

    fixture_format = raw["format"]
    if type(fixture_format) is not str or fixture_format != "complex64-le":
        raise FixtureManifestError(
            f"fixture {name!r} format must be exactly 'complex64-le'"
        )

    sample_rate_hz = raw["sample_rate_hz"]
    if (
        type(sample_rate_hz) is not int
        or sample_rate_hz < 1
        or sample_rate_hz > 0x7FFFFFFF
    ):
        raise FixtureManifestError(
            f"fixture {name!r} sample_rate_hz must be an integer from 1 to 2147483647"
        )

    team = raw["team"]
    if type(team) is not str or team not in {"RED", "BLUE"}:
        raise FixtureManifestError(f"fixture {name!r} team must be exactly RED or BLUE")

    target = raw["target"]
    if type(target) is not str or target not in {"L1", "L2", "L3"}:
        raise FixtureManifestError(f"fixture {name!r} target must be exactly L1, L2, or L3")

    if verification == "candidate":
        return FixtureSpec(
            format=fixture_format,
            sample_rate_hz=sample_rate_hz,
            team=team,
            target=target,
            verification=verification,
        )

    sha256 = raw["sha256"]
    if (
        type(sha256) is not str
        or _SHA256_PATTERN.fullmatch(sha256) is None
        or sha256 == "0" * 64
    ):
        raise FixtureManifestError(
            f"fixture {name!r} sha256 must be a canonical, nonzero lowercase SHA-256"
        )

    expected_cmd_id = raw["expected_cmd_id"]
    if (
        type(expected_cmd_id) is not int
        or expected_cmd_id < 0
        or expected_cmd_id > 0xFFFF
    ):
        raise FixtureManifestError(
            f"fixture {name!r} expected_cmd_id must be an integer from 0 to 65535"
        )

    expected_ascii = raw["expected_ascii"]
    if (
        type(expected_ascii) is not str
        or not expected_ascii
        or len(expected_ascii) > 256
        or any(ord(char) < 0x20 or ord(char) > 0x7E for char in expected_ascii)
    ):
        raise FixtureManifestError(
            f"fixture {name!r} expected_ascii must contain 1 to 256 printable ASCII characters"
        )

    return FixtureSpec(
        format=fixture_format,
        sample_rate_hz=sample_rate_hz,
        team=team,
        target=target,
        verification=verification,
        sha256=sha256,
        expected_cmd_id=expected_cmd_id,
        expected_ascii=expected_ascii,
    )


def _raise_missing(name: str, fields: set[str]) -> None:
    formatted = ", ".join(sorted(fields))
    raise FixtureManifestError(
        f"fixture {name!r} is missing required field(s): {formatted}"
    )


__all__ = [
    "FixtureManifestError",
    "FixtureSpec",
    "confirmed_fixtures",
    "load_fixture_manifest",
]
