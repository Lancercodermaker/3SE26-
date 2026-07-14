from __future__ import annotations

from collections.abc import Iterator, Mapping
from dataclasses import FrozenInstanceError
import json
from pathlib import Path
from types import MappingProxyType

import pytest

from sdr_receiver_py_wrapper.fixture_manifest import (
    FixtureManifestError,
    FixtureSpec,
    MAX_FIXTURE_COUNT,
    MAX_MANIFEST_BYTES,
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


class AdversarialMapping(Mapping):
    def __init__(self, items):
        self._items = items
        self.items_calls = 0
        self.len_calls = 0
        self.keys_calls = 0

    def __getitem__(self, key):
        raise AssertionError("confirmed_fixtures must consume only items()")

    def __iter__(self) -> Iterator:
        raise AssertionError("confirmed_fixtures must consume only items()")

    def __len__(self) -> int:
        self.len_calls += 1
        return 1

    def keys(self):
        self.keys_calls += 1
        return ("safe",)

    def items(self):
        self.items_calls += 1
        if callable(self._items):
            return self._items()
        return iter(self._items)


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


def test_manifest_read_is_bounded_before_utf8_or_json_decode(tmp_path: Path):
    path = tmp_path / "manifest.json"
    path.write_bytes(b" " * (MAX_MANIFEST_BYTES + 1))

    with pytest.raises(FixtureManifestError, match="exceeds.*byte limit") as caught:
        load_fixture_manifest(path)

    assert caught.value.__cause__ is None


def test_deep_json_recursion_is_a_stable_manifest_error(tmp_path: Path):
    path = tmp_path / "manifest.json"
    path.write_text(
        '{"capture":' + "[" * 1_000 + "0" + "]" * 1_000 + "}",
        encoding="utf-8",
    )

    with pytest.raises(FixtureManifestError, match="invalid JSON") as caught:
        load_fixture_manifest(path)

    assert caught.value.__cause__ is None


def test_fixture_count_is_bounded_before_entries_are_constructed(tmp_path: Path):
    payload = {
        f"capture_{index}": common_entry()
        for index in range(MAX_FIXTURE_COUNT + 1)
    }

    with pytest.raises(FixtureManifestError, match="fixture count.*limit"):
        load_fixture_manifest(write_manifest(tmp_path, payload))


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


@pytest.mark.parametrize(
    "name",
    [
        "CON",
        "con.json",
        "PRN",
        "aux.capture",
        "NUL",
        "com1",
        "COM9.bin",
        "lpt1",
        "LPT9.capture",
    ],
)
def test_fixture_names_reject_windows_reserved_devices_on_every_os(
    tmp_path: Path, name: str
):
    with pytest.raises(FixtureManifestError, match="reserved device"):
        load_fixture_manifest(write_manifest(tmp_path, {name: common_entry()}))


def test_fixture_names_must_be_unique_under_casefold(tmp_path: Path):
    payload = {"Capture": common_entry(), "capture": common_entry(target="L2")}

    with pytest.raises(FixtureManifestError, match="case-insensitive collision"):
        load_fixture_manifest(write_manifest(tmp_path, payload))


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


@pytest.mark.parametrize("escaped_key", [r"\ud800", r"bad\u000akey"])
def test_duplicate_json_key_errors_are_ascii_safe(
    tmp_path: Path, escaped_key: str
):
    path = tmp_path / "manifest.json"
    path.write_text(
        '{"capture":{"format":"complex64-le",'
        f'"{escaped_key}":1,"{escaped_key}":2}}',
        encoding="utf-8",
    )

    with pytest.raises(FixtureManifestError, match="duplicate JSON key") as caught:
        load_fixture_manifest(path)

    encoded = str(caught.value).encode("utf-8")
    assert b"\\u" in encoded or b"\\n" in encoded
    assert b"\n" not in encoded


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


@pytest.mark.parametrize("field", ["\ud800", "bad\nfield"])
def test_unknown_field_errors_are_ascii_safe(tmp_path: Path, field: str):
    entry = common_entry()
    entry[field] = "value"

    with pytest.raises(FixtureManifestError, match="unknown field") as caught:
        load_fixture_manifest(write_manifest(tmp_path, {"capture": entry}))

    encoded = str(caught.value).encode("utf-8")
    assert b"\\u" in encoded or b"\\n" in encoded
    assert b"\n" not in encoded


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


@pytest.mark.parametrize(
    "bad_key",
    ["abc", "abcdefg", "ABC!23", "AB 123", "密钥AB12"],
)
def test_confirmed_0a06_requires_exact_six_ascii_alphanumeric_key(
    tmp_path: Path, bad_key: str
):
    entry = confirmed_entry()
    entry["expected_ascii"] = bad_key

    with pytest.raises(FixtureManifestError, match=r"0x0A06.*\[A-Za-z0-9\]\{6\}"):
        load_fixture_manifest(write_manifest(tmp_path, {"capture": entry}))


def test_confirmed_0a06_accepts_exact_six_ascii_alphanumeric_key(tmp_path: Path):
    entry = confirmed_entry()
    entry["expected_ascii"] = "aB09Zx"

    manifest = load_fixture_manifest(write_manifest(tmp_path, {"capture": entry}))

    assert manifest["capture"].expected_ascii == "aB09Zx"


def test_other_commands_keep_general_printable_ascii_contract(tmp_path: Path):
    entry = confirmed_entry()
    entry["expected_cmd_id"] = 0x0102
    entry["expected_ascii"] = "free-form !"

    manifest = load_fixture_manifest(write_manifest(tmp_path, {"capture": entry}))

    assert manifest["capture"].expected_ascii == "free-form !"


@pytest.mark.parametrize(
    "overrides",
    [
        {"format": "complex64-be"},
        {"sample_rate_hz": True},
        {"sample_rate_hz": 0},
        {"team": "blue"},
        {"target": "INFO"},
        {"verification": "verified"},
    ],
)
def test_fixture_spec_direct_construction_enforces_common_invariants(
    overrides: dict[str, object],
):
    values = common_entry()
    values.update(overrides)

    with pytest.raises(FixtureManifestError):
        FixtureSpec(**values)


def test_fixture_spec_direct_confirmed_requires_complete_valid_oracle():
    incomplete = common_entry(verification="confirmed")
    with pytest.raises(FixtureManifestError, match="confirmed"):
        FixtureSpec(**incomplete)

    invalid = confirmed_entry()
    invalid["expected_ascii"] = "!!!!!"
    with pytest.raises(FixtureManifestError, match="0x0A06"):
        FixtureSpec(**invalid)

    assert FixtureSpec(**confirmed_entry()).expected_ascii == "fcYqTC"


@pytest.mark.parametrize(
    ("field", "bad_value"),
    [
        ("sha256", "not-a-sha256"),
        ("expected_cmd_id", True),
        ("expected_cmd_id", 0x10000),
    ],
)
def test_fixture_spec_direct_confirmed_rejects_invalid_oracle_types_and_ranges(
    field: str, bad_value: object
):
    values = confirmed_entry()
    values[field] = bad_value

    with pytest.raises(FixtureManifestError, match=field):
        FixtureSpec(**values)


@pytest.mark.parametrize("oracle", ["sha256", "expected_cmd_id", "expected_ascii"])
def test_fixture_spec_direct_candidate_forbids_oracle(oracle: str):
    values = common_entry()
    values[oracle] = confirmed_entry()[oracle]

    with pytest.raises(FixtureManifestError, match="candidate"):
        FixtureSpec(**values)


def test_confirmed_fixtures_revalidates_and_snapshots_entries():
    original = FixtureSpec(**confirmed_entry())

    required = confirmed_fixtures({"capture": original})

    assert required["capture"] == original
    assert required["capture"] is not original


def test_confirmed_fixtures_uses_one_items_snapshot_not_lying_len_or_keys():
    original = FixtureSpec(**confirmed_entry())
    manifest = AdversarialMapping([("CON", original)])

    with pytest.raises(FixtureManifestError, match="reserved device"):
        confirmed_fixtures(manifest)

    assert manifest.items_calls == 1
    assert manifest.len_calls == 0
    assert manifest.keys_calls == 0


def test_confirmed_fixtures_rejects_257_items_despite_lying_len():
    candidate = FixtureSpec(**common_entry())
    manifest = AdversarialMapping(
        [(f"capture_{index}", candidate) for index in range(MAX_FIXTURE_COUNT + 1)]
    )

    with pytest.raises(FixtureManifestError, match="fixture count.*limit"):
        confirmed_fixtures(manifest)

    assert manifest.items_calls == 1
    assert manifest.len_calls == 0
    assert manifest.keys_calls == 0


def test_confirmed_fixtures_bounds_an_infinite_items_iterator():
    candidate = FixtureSpec(**common_entry())

    def guarded_infinite_items():
        for index in range(MAX_FIXTURE_COUNT + 2):
            if index > MAX_FIXTURE_COUNT:
                raise AssertionError("items iterator was consumed past the bound")
            yield (f"capture_{index}", candidate)

    manifest = AdversarialMapping(guarded_infinite_items)

    with pytest.raises(FixtureManifestError, match="fixture count.*limit"):
        confirmed_fixtures(manifest)

    assert manifest.items_calls == 1


def test_confirmed_fixtures_rejects_non_tuple_items():
    candidate = FixtureSpec(**common_entry())
    manifest = AdversarialMapping([["capture", candidate]])

    with pytest.raises(FixtureManifestError, match="2-tuple"):
        confirmed_fixtures(manifest)


def test_confirmed_fixtures_normalizes_mapping_protocol_failures():
    def broken_items():
        raise RuntimeError("bad\ud800\nprotocol")

    manifest = AdversarialMapping(broken_items)

    with pytest.raises(FixtureManifestError, match="mapping items") as caught:
        confirmed_fixtures(manifest)

    encoded = str(caught.value).encode("utf-8")
    assert b"\n" not in encoded


def test_confirmed_fixtures_rejects_non_fixture_spec_values():
    with pytest.raises(FixtureManifestError, match="FixtureSpec"):
        confirmed_fixtures({"capture": object()})


def test_confirmed_fixtures_rejects_frozen_dataclass_bypass():
    forged = FixtureSpec(**confirmed_entry())
    object.__setattr__(forged, "expected_ascii", "!!!!!")

    with pytest.raises(FixtureManifestError, match="0x0A06"):
        confirmed_fixtures({"capture": forged})


def test_confirmed_fixtures_rejects_partially_forged_object_new_instance():
    forged = object.__new__(FixtureSpec)
    object.__setattr__(forged, "format", "complex64-le")

    with pytest.raises(FixtureManifestError, match="FixtureSpec"):
        confirmed_fixtures({"capture": forged})


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
