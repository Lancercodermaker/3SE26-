"""Strict, immutable contracts for verified IQ fixture manifests."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import re
from types import MappingProxyType
from typing import Iterable, Mapping


MAX_MANIFEST_BYTES = 1_048_576
MAX_FIXTURE_COUNT = 256

_COMMON_FIELDS = frozenset(
    {"format", "sample_rate_hz", "team", "target", "verification"}
)
_VERIFIED_FIELDS = frozenset({"sha256", "expected_cmd_id", "expected_ascii"})
_SHA256_PATTERN = re.compile(r"[0-9a-f]{64}\Z")
_JAM_CODE_PATTERN = re.compile(r"[A-Za-z0-9]{6}\Z")
_FIXTURE_NAME_PATTERN = re.compile(
    r"[A-Za-z0-9](?:[A-Za-z0-9_-]|\.(?=[A-Za-z0-9])){0,254}\Z"
)
_WINDOWS_RESERVED_BASE_NAMES = frozenset(
    {"CON", "PRN", "AUX", "NUL"}
    | {f"COM{index}" for index in range(1, 10)}
    | {f"LPT{index}" for index in range(1, 10)}
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

    def __post_init__(self) -> None:
        _validate_fixture_spec_values(self, "FixtureSpec")

    @property
    def requires_decode_assertion(self) -> bool:
        """Whether CI may require this fixture to decode to its expectations."""

        return self.verification == "confirmed"


def load_fixture_manifest(path: str | Path) -> Mapping[str, FixtureSpec]:
    """Read and validate a manifest, returning an immutable snapshot."""

    try:
        with Path(path).open("rb") as stream:
            encoded = stream.read(MAX_MANIFEST_BYTES + 1)
    except (OSError, TypeError, ValueError) as exc:
        raise FixtureManifestError(f"cannot read manifest {path!s}: {exc}") from None

    if len(encoded) > MAX_MANIFEST_BYTES:
        raise FixtureManifestError(
            f"manifest exceeds {MAX_MANIFEST_BYTES}-byte limit"
        ) from None
    try:
        text = encoded.decode("utf-8", errors="strict")
    except UnicodeError as exc:
        raise FixtureManifestError(f"cannot read manifest {path!s}: {exc}") from None

    try:
        raw = json.loads(
            text,
            object_pairs_hook=_object_without_duplicate_keys,
            parse_constant=_reject_nonstandard_json_constant,
        )
    except _DuplicateJsonKeyError as exc:
        raise FixtureManifestError(str(exc)) from None
    except (json.JSONDecodeError, ValueError, RecursionError) as exc:
        raise FixtureManifestError(f"invalid JSON in manifest: {exc}") from None

    if type(raw) is not dict:
        raise FixtureManifestError("manifest must be a top-level JSON object")
    if not raw:
        raise FixtureManifestError("manifest must contain at least one fixture")
    if len(raw) > MAX_FIXTURE_COUNT:
        raise FixtureManifestError(
            f"manifest fixture count exceeds {MAX_FIXTURE_COUNT}-entry limit"
        )

    _validate_fixture_names(raw.keys())
    validated: dict[str, FixtureSpec] = {}
    for name, entry in raw.items():
        validated[name] = _validate_entry(name, entry)
    return MappingProxyType(validated)


def confirmed_fixtures(
    manifest: Mapping[str, FixtureSpec],
) -> Mapping[str, FixtureSpec]:
    """Revalidate, snapshot, and return only CI-required decode cases."""

    if not isinstance(manifest, Mapping):
        raise FixtureManifestError("manifest must be a mapping of FixtureSpec values")
    if len(manifest) > MAX_FIXTURE_COUNT:
        raise FixtureManifestError(
            f"manifest fixture count exceeds {MAX_FIXTURE_COUNT}-entry limit"
        )
    _validate_fixture_names(manifest.keys())

    confirmed: dict[str, FixtureSpec] = {}
    for name, entry in manifest.items():
        snapshot = _snapshot_fixture_spec(name, entry)
        if snapshot.requires_decode_assertion:
            confirmed[name] = snapshot
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
    base_name = name.split(".", 1)[0].upper()
    if base_name in _WINDOWS_RESERVED_BASE_NAMES:
        raise FixtureManifestError(
            f"fixture name {name!r} uses a Windows reserved device name"
        )


def _validate_fixture_names(names: Iterable[object]) -> None:
    folded_names: dict[str, str] = {}
    for name in names:
        _validate_fixture_name(name)
        folded = name.casefold()
        previous = folded_names.get(folded)
        if previous is not None:
            raise FixtureManifestError(
                "fixture names have a case-insensitive collision: "
                f"{previous!r} and {name!r}"
            )
        folded_names[folded] = name


def _validate_entry(name: str, raw: object) -> FixtureSpec:
    if type(raw) is not dict:
        raise FixtureManifestError(f"fixture {name!r} entry must be a JSON object")

    missing_common = _COMMON_FIELDS - raw.keys()
    if missing_common:
        _raise_missing(name, missing_common)

    verification = raw["verification"]
    _validate_verification(verification, f"fixture {name!r}")

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

    return FixtureSpec(**raw)


def _validate_fixture_spec_values(spec: FixtureSpec, label: str) -> None:
    if type(spec.format) is not str or spec.format != "complex64-le":
        raise FixtureManifestError(f"{label} format must be exactly 'complex64-le'")
    if (
        type(spec.sample_rate_hz) is not int
        or spec.sample_rate_hz < 1
        or spec.sample_rate_hz > 0x7FFFFFFF
    ):
        raise FixtureManifestError(
            f"{label} sample_rate_hz must be an integer from 1 to 2147483647"
        )
    if type(spec.team) is not str or spec.team not in {"RED", "BLUE"}:
        raise FixtureManifestError(f"{label} team must be exactly RED or BLUE")
    if type(spec.target) is not str or spec.target not in {"L1", "L2", "L3"}:
        raise FixtureManifestError(f"{label} target must be exactly L1, L2, or L3")

    _validate_verification(spec.verification, label)
    oracle = (spec.sha256, spec.expected_cmd_id, spec.expected_ascii)
    if spec.verification == "candidate":
        if any(value is not None for value in oracle):
            raise FixtureManifestError(f"candidate {label} must not contain an oracle")
        return
    if any(value is None for value in oracle):
        raise FixtureManifestError(
            f"confirmed {label} must contain sha256, expected_cmd_id, and expected_ascii"
        )

    if (
        type(spec.sha256) is not str
        or _SHA256_PATTERN.fullmatch(spec.sha256) is None
        or spec.sha256 == "0" * 64
    ):
        raise FixtureManifestError(
            f"{label} sha256 must be a canonical, nonzero lowercase SHA-256"
        )
    if (
        type(spec.expected_cmd_id) is not int
        or spec.expected_cmd_id < 0
        or spec.expected_cmd_id > 0xFFFF
    ):
        raise FixtureManifestError(
            f"{label} expected_cmd_id must be an integer from 0 to 65535"
        )
    if spec.expected_cmd_id == 0x0A06 and (
        type(spec.expected_ascii) is not str
        or _JAM_CODE_PATTERN.fullmatch(spec.expected_ascii) is None
    ):
        raise FixtureManifestError(
            f"{label} expected_ascii for 0x0A06 must match [A-Za-z0-9]{{6}}"
        )
    if spec.expected_cmd_id != 0x0A06 and (
        type(spec.expected_ascii) is not str
        or not spec.expected_ascii
        or len(spec.expected_ascii) > 256
        or any(ord(char) < 0x20 or ord(char) > 0x7E for char in spec.expected_ascii)
    ):
        raise FixtureManifestError(
            f"{label} expected_ascii must contain 1 to 256 printable ASCII characters"
        )


def _validate_verification(value: object, label: str) -> None:
    if type(value) is not str or value not in {"candidate", "confirmed"}:
        raise FixtureManifestError(
            f"{label} verification must be exactly 'candidate' or 'confirmed'"
        )


def _snapshot_fixture_spec(name: str, entry: object) -> FixtureSpec:
    if type(entry) is not FixtureSpec:
        raise FixtureManifestError(f"fixture {name!r} must be an exact FixtureSpec")
    try:
        return FixtureSpec(
            format=entry.format,
            sample_rate_hz=entry.sample_rate_hz,
            team=entry.team,
            target=entry.target,
            verification=entry.verification,
            sha256=entry.sha256,
            expected_cmd_id=entry.expected_cmd_id,
            expected_ascii=entry.expected_ascii,
        )
    except (AttributeError, FixtureManifestError) as exc:
        raise FixtureManifestError(str(exc)) from None


def _raise_missing(name: str, fields: set[str]) -> None:
    formatted = ", ".join(sorted(fields))
    raise FixtureManifestError(
        f"fixture {name!r} is missing required field(s): {formatted}"
    )


__all__ = [
    "FixtureManifestError",
    "FixtureSpec",
    "MAX_FIXTURE_COUNT",
    "MAX_MANIFEST_BYTES",
    "confirmed_fixtures",
    "load_fixture_manifest",
]
