from __future__ import annotations

from dataclasses import FrozenInstanceError
import json
from pathlib import Path
from types import MappingProxyType

import pytest

from sdr_receiver_py_wrapper.fixture_manifest import (
    FixtureManifestError,
    FixtureSpec,
    confirmed_fixtures,
    load_fixture_manifest,
)


MANIFEST_PATH = Path(__file__).parents[1] / "fixtures" / "manifest.json"
L1_SHA256 = "8cde16d3fe8230334a9efcb36c81ae105b76b4118f4fe3fc63943aeb791be7cc"


def common_entry(*, target: str = "L1", verification: str = "candidate") -> dict:
    return {
        "format": "complex64-le",
        "sample_rate_hz": 2_000_000,
        "team": "BLUE",
        "target": target,
        "verification": verification,
    }


def confirmed_entry() -> dict:
    entry = common_entry(verification="confirmed")
    entry.update(
        {
            "sha256": L1_SHA256,
            "expected_cmd_id": 0x0A06,
            "expected_ascii": "fcYqTC",
        }
    )
    return entry


def write_manifest(tmp_path: Path, payload: object) -> Path:
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_shipped_manifest_registers_confirmed_l1_and_candidate_l2_l3():
    manifest = load_fixture_manifest(MANIFEST_PATH)

    assert set(manifest) == {
        "RX_BLUE_ganrao_1",
        "RX_BLUE_ganrao_2",
        "RX_BLUE_ganrao_3",
    }
    l1 = manifest["RX_BLUE_ganrao_1"]
    assert l1 == FixtureSpec(
        format="complex64-le",
        sample_rate_hz=2_000_000,
        team="BLUE",
        target="L1",
        verification="confirmed",
        sha256=L1_SHA256,
        expected_cmd_id=0x0A06,
        expected_ascii="fcYqTC",
    )
    assert manifest["RX_BLUE_ganrao_2"].target == "L2"
    assert manifest["RX_BLUE_ganrao_3"].target == "L3"
    assert manifest["RX_BLUE_ganrao_2"].verification == "candidate"
    assert manifest["RX_BLUE_ganrao_3"].verification == "candidate"
    assert manifest["RX_BLUE_ganrao_2"].expected_ascii is None
    assert manifest["RX_BLUE_ganrao_3"].expected_ascii is None


def test_only_confirmed_fixtures_are_required_decode_cases():
    manifest = load_fixture_manifest(MANIFEST_PATH)

    required = confirmed_fixtures(manifest)

    assert isinstance(required, MappingProxyType)
    assert tuple(required) == ("RX_BLUE_ganrao_1",)
    assert required["RX_BLUE_ganrao_1"].requires_decode_assertion is True
    assert manifest["RX_BLUE_ganrao_2"].requires_decode_assertion is False


def test_loaded_manifest_and_entries_are_immutable(tmp_path: Path):
    manifest = load_fixture_manifest(
        write_manifest(tmp_path, {"capture": confirmed_entry()})
    )

    assert isinstance(manifest, MappingProxyType)
    with pytest.raises(TypeError):
        manifest["other"] = manifest["capture"]
    with pytest.raises(FrozenInstanceError):
        manifest["capture"].team = "RED"


@pytest.mark.parametrize(
    "payload",
    [[], None, "manifest", 1, True],
)
def test_manifest_root_must_be_an_object(tmp_path: Path, payload: object):
    path = write_manifest(tmp_path, payload)

    with pytest.raises(FixtureManifestError, match="top-level JSON object"):
        load_fixture_manifest(path)


def test_manifest_must_not_be_empty(tmp_path: Path):
    with pytest.raises(FixtureManifestError, match="at least one fixture"):
        load_fixture_manifest(write_manifest(tmp_path, {}))


@pytest.mark.parametrize(
    "name",
    [
        "",
        ".",
        "..",
        "folder/capture",
        r"folder\\capture",
        "bad\x00name",
        "bad\nname",
        "capture:1",
        "capture name",
        "capture.",
    ],
)
def test_fixture_names_must_be_safe_file_names(tmp_path: Path, name: str):
    with pytest.raises(FixtureManifestError, match="fixture name"):
        load_fixture_manifest(write_manifest(tmp_path, {name: common_entry()}))


def test_duplicate_json_keys_are_rejected(tmp_path: Path):
    path = tmp_path / "manifest.json"
    path.write_text(
        '{"capture":{"format":"complex64-le","format":"complex64-le",'
        '"sample_rate_hz":2000000,"team":"BLUE","target":"L2",'
        '"verification":"candidate"}}',
        encoding="utf-8",
    )

    with pytest.raises(FixtureManifestError, match="duplicate JSON key.*format"):
        load_fixture_manifest(path)


@pytest.mark.parametrize(
    "text",
    ["{", "[] trailing", '{"capture": NaN}'],
)
def test_invalid_json_is_reported_as_manifest_error(tmp_path: Path, text: str):
    path = tmp_path / "manifest.json"
    path.write_text(text, encoding="utf-8")

    with pytest.raises(FixtureManifestError, match="invalid JSON"):
        load_fixture_manifest(path)


def test_fixture_entry_must_be_an_object(tmp_path: Path):
    with pytest.raises(FixtureManifestError, match="entry must be a JSON object"):
        load_fixture_manifest(write_manifest(tmp_path, {"capture": []}))


def test_unknown_fields_are_rejected(tmp_path: Path):
    entry = common_entry()
    entry["unreviewed"] = "value"

    with pytest.raises(FixtureManifestError, match="unknown field.*unreviewed"):
        load_fixture_manifest(write_manifest(tmp_path, {"capture": entry}))


@pytest.mark.parametrize(
    ("field", "bad_value"),
    [
        ("format", "complex64-be"),
        ("format", 1),
        ("sample_rate_hz", 0),
        ("sample_rate_hz", -1),
        ("sample_rate_hz", True),
        ("sample_rate_hz", 2.0e6),
        ("team", "blue"),
        ("team", "GREEN"),
        ("target", "l1"),
        ("target", "INFO"),
        ("verification", "verified"),
        ("verification", []),
    ],
)
def test_common_fields_use_exact_types_and_domains(
    tmp_path: Path, field: str, bad_value: object
):
    entry = common_entry()
    entry[field] = bad_value

    with pytest.raises(FixtureManifestError, match=field):
        load_fixture_manifest(write_manifest(tmp_path, {"capture": entry}))


@pytest.mark.parametrize("missing", ["format", "sample_rate_hz", "team", "target", "verification"])
def test_common_fields_are_required(tmp_path: Path, missing: str):
    entry = common_entry()
    del entry[missing]

    with pytest.raises(FixtureManifestError, match=f"missing required field.*{missing}"):
        load_fixture_manifest(write_manifest(tmp_path, {"capture": entry}))


@pytest.mark.parametrize("missing", ["sha256", "expected_cmd_id", "expected_ascii"])
def test_confirmed_entries_require_hash_command_and_ascii(tmp_path: Path, missing: str):
    entry = confirmed_entry()
    del entry[missing]

    with pytest.raises(FixtureManifestError, match=f"missing required field.*{missing}"):
        load_fixture_manifest(write_manifest(tmp_path, {"capture": entry}))


@pytest.mark.parametrize(
    ("field", "bad_value"),
    [
        ("sha256", "0" * 63),
        ("sha256", "G" * 64),
        ("sha256", "A" * 64),
        ("sha256", "0" * 64),
        ("sha256", 1),
        ("expected_cmd_id", -1),
        ("expected_cmd_id", 0x10000),
        ("expected_cmd_id", True),
        ("expected_cmd_id", "2566"),
        ("expected_ascii", ""),
        ("expected_ascii", "line\nbreak"),
        ("expected_ascii", "密钥"),
        ("expected_ascii", 123),
    ],
)
def test_confirmed_values_use_canonical_types_and_ranges(
    tmp_path: Path, field: str, bad_value: object
):
    entry = confirmed_entry()
    entry[field] = bad_value

    with pytest.raises(FixtureManifestError, match=field):
        load_fixture_manifest(write_manifest(tmp_path, {"capture": entry}))


@pytest.mark.parametrize("forbidden", ["sha256", "expected_cmd_id", "expected_ascii"])
def test_candidate_entries_must_not_claim_unverified_results(
    tmp_path: Path, forbidden: str
):
    entry = common_entry()
    entry[forbidden] = confirmed_entry()[forbidden]

    with pytest.raises(FixtureManifestError, match=f"candidate.*{forbidden}"):
        load_fixture_manifest(write_manifest(tmp_path, {"capture": entry}))


def test_os_and_encoding_errors_have_a_stable_domain_error(tmp_path: Path):
    with pytest.raises(FixtureManifestError, match="cannot read manifest"):
        load_fixture_manifest(tmp_path / "missing.json")

    path = tmp_path / "manifest.json"
    path.write_bytes(b"\xff")
    with pytest.raises(FixtureManifestError, match="cannot read manifest"):
        load_fixture_manifest(path)
