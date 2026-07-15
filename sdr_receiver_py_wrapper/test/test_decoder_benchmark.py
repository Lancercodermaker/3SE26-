from __future__ import annotations

from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
import tempfile

import numpy as np
import pytest

import sdr_receiver_py_wrapper.decoder_benchmark as benchmark_module
from sdr_receiver_py_wrapper.decoder_benchmark import (
    BenchmarkDiagnostics,
    BenchmarkError,
    main,
    run_benchmark,
)
from sdr_receiver_py_wrapper.fixture_manifest import FixtureSpec
from sdr_receiver_py_wrapper.models import DecodedCommand, DecoderStats


EXPECTED_KEY = b"fcYqTC"


def _samples(count: int = 8) -> np.ndarray:
    return np.asarray(
        [complex(index, -index) for index in range(count)],
        dtype=np.complex64,
    )


def _fixture(samples: np.ndarray) -> FixtureSpec:
    return FixtureSpec(
        format="complex64-le",
        sample_rate_hz=4,
        team="BLUE",
        target="L1",
        verification="confirmed",
        sha256=hashlib.sha256(samples.astype("<c8").tobytes()).hexdigest(),
        expected_cmd_id=0x0A06,
        expected_ascii=EXPECTED_KEY.decode("ascii"),
    )


class FakeDecoder:
    def __init__(
        self,
        name: str,
        *,
        mutate: bool = False,
        payload: bytes = EXPECTED_KEY,
        diagnostics: bool = True,
    ) -> None:
        self.decoder_id = name
        self._mutate = mutate
        self._payload = payload
        self._supports_diagnostics = diagnostics
        self.seen: list[tuple[int, int, str, int, bool, bool]] = []
        self._counters = BenchmarkDiagnostics(ac=0, sof=0, crc8=0, crc16=0)
        self._emitted = False
        self.seen_contexts = []

    def reset(self, reason, context) -> None:
        self.reset_context = context

    def decode(self, chunk, context):
        self.seen_contexts.append(
            (
                context.team,
                context.target,
                context.profile,
                context.context_version,
            )
        )
        before = hashlib.sha256(chunk.samples.tobytes()).hexdigest()
        self.seen.append(
            (
                chunk.chunk_id,
                chunk.first_sample_index,
                before,
                context.context_version,
                chunk.samples.flags.owndata,
                chunk.samples.flags.writeable,
            )
        )
        if self._mutate:
            chunk.samples.flags.writeable = True
            chunk.samples[:] = np.complex64(99 + 99j)
        self._counters = BenchmarkDiagnostics(
            ac=self._counters.ac + 1,
            sof=self._counters.sof + 2,
            crc8=self._counters.crc8 + 3,
            crc16=self._counters.crc16 + 4,
        )
        if self._emitted or chunk.chunk_id != 1:
            return []
        self._emitted = True
        return [
            DecodedCommand(
                cmd_id=0x0A06,
                payload=self._payload,
                decoder_id=self.decoder_id,
                profile=context.profile,
                crc8_ok=True,
                crc16_ok=True,
                crc_mode="test-verified",
                first_sample_index=chunk.first_sample_index + 1,
                last_sample_index=chunk.first_sample_index + 1,
                receive_wall_time=chunk.rx_wall_time,
                target=context.target,
                team=context.team,
                context_version=context.context_version,
                evidence={"level": 1},
            )
        ]

    def benchmark_diagnostics(self):
        if not self._supports_diagnostics:
            raise NotImplementedError
        return self._counters

    def stats(self):
        return DecoderStats()


def _run(tmp_path: Path, *plugins: FakeDecoder, samples=None, **kwargs):
    samples = _samples() if samples is None else samples
    iq_path = tmp_path / "RX_BLUE_ganrao_1"
    iq_path.write_bytes(samples.astype("<c8").tobytes())
    registry = {
        plugin.decoder_id: (lambda plugin=plugin: plugin)
        for plugin in plugins
    }
    return run_benchmark(
        iq_path=iq_path,
        fixture_name="RX_BLUE_ganrao_1",
        fixture=_fixture(samples),
        decoder_names=tuple(registry),
        decoder_registry=registry,
        chunk_samples=4,
        cpu_clock_ns=iter(range(0, 1_000_000, 10)).__next__,
        **kwargs,
    )


def test_same_source_metadata_and_private_iq_reach_each_decoder(tmp_path):
    mutator = FakeDecoder("upstream", mutate=True)
    observer = FakeDecoder("improved_v67")

    report = _run(tmp_path, mutator, observer)

    assert report["success"] is False
    assert report["decoders"][0]["status"] == "error"
    assert report["decoders"][1]["status"] == "passed"
    assert mutator.seen == observer.seen[:1]
    assert all(owns and not writeable for *_, owns, writeable in observer.seen)
    assert [entry[:2] for entry in observer.seen] == [(0, 0), (1, 4)]
    expected_hashes = [
        hashlib.sha256(_samples()[start:start + 4].tobytes()).hexdigest()
        for start in (0, 4)
    ]
    assert [entry[2] for entry in observer.seen] == expected_hashes

    result = report["decoders"][1]
    assert result["first_key_time_s"] == pytest.approx(1.25)
    assert result["ac"] == 2
    assert result["sof"] == 4
    assert result["crc8"] == 6
    assert result["crc16"] == 8
    assert result["cpu_time_ns"] == 20
    assert result["peak_queue_depth"] == 1


def test_mismatched_or_duplicate_oracle_results_fail_without_fake_success(
    tmp_path,
):
    wrong = FakeDecoder("upstream", payload=b"WRONG1")
    duplicate = FakeDecoder("improved_v67")
    original_decode = duplicate.decode

    def decode_twice(chunk, context):
        commands = original_decode(chunk, context)
        return commands + commands

    duplicate.decode = decode_twice

    report = _run(tmp_path, wrong, duplicate)

    assert report["success"] is False
    by_name = {entry["decoder"]: entry for entry in report["decoders"]}
    assert by_name["upstream"]["status"] == "mismatch"
    assert by_name["improved_v67"]["status"] == "mismatch"
    assert by_name["upstream"]["oracle_matches"] == 0
    assert by_name["improved_v67"]["oracle_matches"] == 2
    assert by_name["upstream"]["expected_cmd_conflicts"] == 1
    assert by_name["improved_v67"]["expected_cmd_conflicts"] == 0
    assert by_name["upstream"]["mismatch_reasons"] == [
        "oracle_match_count",
        "expected_cmd_conflict",
    ]
    assert by_name["improved_v67"]["mismatch_reasons"] == [
        "oracle_match_count"
    ]


@pytest.mark.parametrize(
    "payloads",
    [
        (EXPECTED_KEY, b"WRONG1"),
        (b"WRONG1", EXPECTED_KEY),
    ],
)
def test_correct_key_plus_conflicting_expected_command_never_passes(
    tmp_path,
    payloads,
):
    conflicted = FakeDecoder("upstream")
    peer = FakeDecoder("improved_v67")
    original_decode = conflicted.decode

    def decode_conflict(chunk, context):
        commands = original_decode(chunk, context)
        if not commands:
            return []
        return [replace(commands[0], payload=payload) for payload in payloads]

    conflicted.decode = decode_conflict

    report = _run(tmp_path, conflicted, peer)

    result = report["decoders"][0]
    assert report["success"] is False
    assert result["status"] == "mismatch"
    assert result["expected_cmd_outputs"] == 2
    assert result["oracle_matches"] == 1
    assert result["expected_cmd_conflicts"] == 1
    assert result["mismatch_reasons"] == ["expected_cmd_conflict"]


def test_diagnostics_are_explicitly_unavailable_not_invented(tmp_path):
    unsupported = FakeDecoder("upstream", diagnostics=False)
    supported = FakeDecoder("improved_v67")

    report = _run(tmp_path, unsupported, supported)

    entry = report["decoders"][0]
    assert entry["diagnostics_status"] == "unavailable"
    assert entry["ac"] is None
    assert entry["sof"] is None
    assert entry["crc8"] is None
    assert entry["crc16"] is None
    assert report["success"] is True


def test_malformed_diagnostics_and_plugin_exceptions_are_reported(tmp_path):
    malformed = FakeDecoder("upstream")
    malformed.benchmark_diagnostics = lambda: {"ac": 1}
    exploding = FakeDecoder("improved_v67")
    exploding.decode = lambda chunk, context: (_ for _ in ()).throw(
        RuntimeError("decoder exploded")
    )

    report = _run(tmp_path, malformed, exploding)

    assert report["success"] is False
    by_name = {entry["decoder"]: entry for entry in report["decoders"]}
    assert by_name["upstream"]["status"] == "error"
    assert by_name["upstream"]["diagnostics_status"] == "invalid"
    assert by_name["improved_v67"]["status"] == "error"
    assert "decoder exploded" in by_name["improved_v67"]["error"]


@pytest.mark.parametrize(
    "bad_result",
    [
        iter(()),
        [object()],
    ],
)
def test_plugin_command_contract_is_bounded_and_exact(tmp_path, bad_result):
    broken = FakeDecoder("upstream")
    peer = FakeDecoder("improved_v67")
    broken.decode = lambda chunk, context: bad_result

    report = _run(tmp_path, broken, peer)

    assert report["success"] is False
    assert report["decoders"][0]["status"] == "error"


@pytest.mark.parametrize("crc_field", ["crc8_ok", "crc16_ok"])
def test_expected_payload_with_failed_crc_is_an_oracle_conflict(
    tmp_path,
    crc_field,
):
    broken = FakeDecoder("upstream")
    peer = FakeDecoder("improved_v67")
    original_decode = broken.decode

    def invalid_metadata(chunk, context):
        commands = original_decode(chunk, context)
        return [replace(commands[0], **{crc_field: False})] if commands else []

    broken.decode = invalid_metadata

    report = _run(tmp_path, broken, peer)

    assert report["success"] is False
    result = report["decoders"][0]
    assert result["status"] == "mismatch"
    assert result["oracle_matches"] == 0
    assert result["expected_cmd_conflicts"] == 1
    assert result["mismatch_reasons"] == [
        "oracle_match_count",
        "expected_cmd_conflict",
    ]


def test_unrelated_diagnostic_commands_do_not_conflict_with_oracle(tmp_path):
    decoder = FakeDecoder("upstream")
    peer = FakeDecoder("improved_v67")
    original_decode = decoder.decode

    def decode_with_diagnostic(chunk, context):
        commands = original_decode(chunk, context)
        if not commands:
            return []
        diagnostic = replace(
            commands[0],
            cmd_id=0x0100,
            payload=b"diagnostic",
            crc8_ok=False,
            crc16_ok=False,
        )
        return [diagnostic, commands[0]]

    decoder.decode = decode_with_diagnostic

    report = _run(tmp_path, decoder, peer)

    result = report["decoders"][0]
    assert report["success"] is True
    assert result["status"] == "passed"
    assert result["expected_cmd_outputs"] == 1
    assert result["oracle_matches"] == 1
    assert result["expected_cmd_conflicts"] == 0
    assert result["mismatch_reasons"] == []


@pytest.mark.parametrize("level", [None, 2, True])
def test_expected_command_must_pass_production_validation_for_fixture_level(
    tmp_path,
    level,
):
    decoder = FakeDecoder("upstream")
    peer = FakeDecoder("improved_v67")
    original_decode = decoder.decode

    def decode_bad_evidence(chunk, context):
        commands = original_decode(chunk, context)
        if not commands:
            return []
        evidence = {} if level is None else {"level": level}
        return [replace(commands[0], evidence=evidence)]

    decoder.decode = decode_bad_evidence

    result = _run(tmp_path, decoder, peer)["decoders"][0]

    assert result["status"] == "mismatch"
    assert result["oracle_matches"] == 0
    assert result["expected_cmd_conflicts"] == 1


def test_source_is_snapshotted_before_factory_can_replace_path(tmp_path):
    samples = _samples()
    iq_path = tmp_path / "RX_BLUE_ganrao_1"
    iq_path.write_bytes(samples.tobytes())
    original_hash = hashlib.sha256(samples.tobytes()).hexdigest()
    first = FakeDecoder("upstream")
    second = FakeDecoder("improved_v67")

    def replacing_factory():
        replacement = np.full(32, np.complex64(88 + 77j))
        iq_path.write_bytes(replacement.tobytes())
        return first

    report = run_benchmark(
        iq_path=iq_path,
        fixture_name=iq_path.name,
        fixture=_fixture(samples),
        decoder_names=("upstream", "improved_v67"),
        decoder_registry={
            "upstream": replacing_factory,
            "improved_v67": lambda: second,
        },
        chunk_samples=4,
        cpu_clock_ns=iter(range(0, 1_000_000, 10)).__next__,
    )

    assert report["success"] is True
    assert report["iq_sha256"] == original_hash
    assert all(entry[2] != hashlib.sha256(iq_path.read_bytes()).hexdigest()
               for entry in second.seen)
    assert [entry[2] for entry in first.seen] == [
        hashlib.sha256(samples[start:start + 4].tobytes()).hexdigest()
        for start in (0, 4)
    ]


def test_exposed_metadata_and_samples_cannot_poison_peer(tmp_path):
    attacker = FakeDecoder("upstream")
    observer = FakeDecoder("improved_v67")

    def malicious_decode(chunk, context):
        object.__setattr__(context, "team", "RED")
        object.__setattr__(chunk, "first_sample_index", 999)
        chunk.samples.flags.writeable = True
        chunk.samples[:] = np.complex64(55 + 44j)
        return []

    attacker.decode = malicious_decode

    report = _run(tmp_path, attacker, observer)

    assert report["success"] is False
    assert report["decoders"][0]["status"] == "error"
    assert "mutated" in report["decoders"][0]["error"]
    assert observer.seen_contexts == [
        ("BLUE", "L1", "BLUE-L1", 1),
        ("BLUE", "L1", "BLUE-L1", 1),
    ]
    assert [entry[:2] for entry in observer.seen] == [(0, 0), (1, 4)]


def test_reset_context_mutation_is_detected_without_reaching_peer(tmp_path):
    attacker = FakeDecoder("upstream")
    observer = FakeDecoder("improved_v67")

    def malicious_reset(reason, context):
        object.__setattr__(context, "target", "L3")

    attacker.reset = malicious_reset

    report = _run(tmp_path, attacker, observer)

    assert report["decoders"][0]["status"] == "error"
    assert "reset context" in report["decoders"][0]["error"]
    assert report["decoders"][1]["status"] == "passed"


def test_diagnostics_are_snapshotted_before_later_plugin_mutates_alias(
    tmp_path,
):
    shared = BenchmarkDiagnostics(ac=1, sof=2, crc8=3, crc16=4)
    first = FakeDecoder("upstream")
    second = FakeDecoder("improved_v67")
    first.benchmark_diagnostics = lambda: shared

    def mutate_shared():
        object.__setattr__(shared, "ac", 999)
        return BenchmarkDiagnostics(ac=5, sof=6, crc8=7, crc16=8)

    second.benchmark_diagnostics = mutate_shared

    report = _run(tmp_path, first, second)

    assert report["success"] is True
    assert report["decoders"][0]["ac"] == 1
    assert report["decoders"][0]["sof"] == 2
    assert report["decoders"][1]["ac"] == 5


@pytest.mark.parametrize(
    "samples, message",
    [
        (np.asarray([], dtype=np.complex64), "at least one"),
        (np.asarray([complex(float("nan"), 0)], dtype=np.complex64), "finite"),
        (np.asarray([complex(float("inf"), 0)], dtype=np.complex64), "finite"),
        (
            np.asarray([complex(0, -float("inf"))], dtype=np.complex64),
            "finite",
        ),
    ],
)
def test_invalid_iq_is_rejected_before_any_factory(tmp_path, samples, message):
    iq_path = tmp_path / "RX_BLUE_ganrao_1"
    iq_path.write_bytes(samples.tobytes())
    calls = []

    with pytest.raises(BenchmarkError, match=message):
        run_benchmark(
            iq_path=iq_path,
            fixture_name=iq_path.name,
            fixture=_fixture(samples),
            decoder_names=("upstream", "improved_v67"),
            decoder_registry={
                "upstream": lambda: calls.append("upstream"),
                "improved_v67": lambda: calls.append("improved_v67"),
            },
        )

    assert calls == []


def test_rejects_unconfirmed_hash_mismatch_and_misaligned_iq(tmp_path):
    first = FakeDecoder("upstream")
    second = FakeDecoder("improved_v67")
    samples = _samples()
    iq_path = tmp_path / "RX_BLUE_ganrao_1"
    iq_path.write_bytes(samples.tobytes())
    common = dict(
        iq_path=iq_path,
        fixture_name="RX_BLUE_ganrao_1",
        decoder_names=("upstream", "improved_v67"),
        decoder_registry={
            "upstream": lambda: first,
            "improved_v67": lambda: second,
        },
    )

    with pytest.raises(BenchmarkError, match="confirmed"):
        run_benchmark(
            fixture=replace(
                _fixture(samples),
                verification="candidate",
                sha256=None,
                expected_cmd_id=None,
                expected_ascii=None,
            ),
            **common,
        )
    with pytest.raises(BenchmarkError, match="SHA-256"):
        run_benchmark(
            fixture=replace(_fixture(samples), sha256="1" * 64),
            **common,
        )
    iq_path.write_bytes(b"123")
    with pytest.raises(BenchmarkError, match="multiple of 8"):
        run_benchmark(fixture=_fixture(samples), **common)


def test_fixture_name_must_match_the_actual_iq_basename(tmp_path):
    first = FakeDecoder("upstream")
    second = FakeDecoder("improved_v67")
    samples = _samples()
    iq_path = tmp_path / "renamed.c64"
    iq_path.write_bytes(samples.tobytes())

    with pytest.raises(BenchmarkError, match="basename"):
        run_benchmark(
            iq_path=iq_path,
            fixture_name="RX_BLUE_ganrao_1",
            fixture=_fixture(samples),
            decoder_names=("upstream", "improved_v67"),
            decoder_registry={
                "upstream": lambda: first,
                "improved_v67": lambda: second,
            },
        )


@pytest.mark.parametrize(
    "decoder_names, message",
    [
        (("upstream",), "at least two"),
        (("upstream", "upstream"), "unique"),
        (("upstream", "unknown"), "unavailable"),
    ],
)
def test_decoder_selection_must_be_exact_unique_and_available(
    tmp_path, decoder_names, message
):
    samples = _samples()
    iq_path = tmp_path / "RX_BLUE_ganrao_1"
    iq_path.write_bytes(samples.tobytes())
    registry = {"upstream": lambda: FakeDecoder("upstream")}

    with pytest.raises(BenchmarkError, match=message):
        run_benchmark(
            iq_path=iq_path,
            fixture_name="RX_BLUE_ganrao_1",
            fixture=_fixture(samples),
            decoder_names=decoder_names,
            decoder_registry=registry,
        )


def test_cli_writes_deterministic_json_and_returns_nonzero_on_mismatch(
    tmp_path,
):
    samples = _samples()
    iq_path = tmp_path / "RX_BLUE_ganrao_1"
    iq_path.write_bytes(samples.tobytes())
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "RX_BLUE_ganrao_1": {
                    "format": "complex64-le",
                    "sample_rate_hz": 4,
                    "team": "BLUE",
                    "target": "L1",
                    "verification": "confirmed",
                    "sha256": _fixture(samples).sha256,
                    "expected_cmd_id": 0x0A06,
                    "expected_ascii": "fcYqTC",
                }
            }
        ),
        encoding="utf-8",
    )
    out_path = tmp_path / "result.json"
    registry = {
        "upstream": lambda: FakeDecoder("upstream"),
        "improved_v67": lambda: FakeDecoder("improved_v67", payload=b"WRONG1"),
    }

    exit_code = main(
        [
            "--iq",
            "RX_BLUE_ganrao_1",
            "--manifest",
            str(manifest_path),
            "--decoders",
            "upstream,improved_v67",
            "--out",
            str(out_path),
            "--chunk-samples",
            "4",
        ],
        decoder_registry=registry,
        cpu_clock_ns=iter(range(0, 1_000_000, 10)).__next__,
    )

    assert exit_code == 1
    raw = out_path.read_bytes()
    assert raw.endswith(b"\n")
    assert json.loads(raw)["success"] is False
    assert raw == json.dumps(
        json.loads(raw), sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode() + b"\n"


def test_cli_returns_nonzero_when_correct_key_has_expected_cmd_conflict(
    tmp_path,
):
    samples = _samples()
    iq_path = tmp_path / "RX_BLUE_ganrao_1"
    iq_path.write_bytes(samples.tobytes())
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "RX_BLUE_ganrao_1": {
                    "format": "complex64-le",
                    "sample_rate_hz": 4,
                    "team": "BLUE",
                    "target": "L1",
                    "verification": "confirmed",
                    "sha256": _fixture(samples).sha256,
                    "expected_cmd_id": 0x0A06,
                    "expected_ascii": "fcYqTC",
                }
            }
        ),
        encoding="utf-8",
    )
    out_path = tmp_path / "result.json"
    conflicted = FakeDecoder("upstream")
    original_decode = conflicted.decode

    def decode_conflict(chunk, context):
        commands = original_decode(chunk, context)
        if not commands:
            return []
        return [commands[0], replace(commands[0], payload=b"WRONG1")]

    conflicted.decode = decode_conflict
    registry = {
        "upstream": lambda: conflicted,
        "improved_v67": lambda: FakeDecoder("improved_v67"),
    }

    exit_code = main(
        [
            "--iq",
            str(iq_path),
            "--manifest",
            str(manifest_path),
            "--decoders",
            "upstream,improved_v67",
            "--out",
            str(out_path),
            "--chunk-samples",
            "4",
        ],
        decoder_registry=registry,
        cpu_clock_ns=iter(range(0, 1_000_000, 10)).__next__,
    )

    assert exit_code == 1
    report = json.loads(out_path.read_text(encoding="utf-8"))
    assert report["success"] is False
    assert report["decoders"][0]["expected_cmd_conflicts"] == 1


def test_default_cli_does_not_pretend_missing_production_backends_work(
    tmp_path,
):
    samples = _samples()
    iq_path = tmp_path / "RX_BLUE_ganrao_1"
    iq_path.write_bytes(samples.tobytes())
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "RX_BLUE_ganrao_1": {
                    "format": "complex64-le",
                    "sample_rate_hz": 4,
                    "team": "BLUE",
                    "target": "L1",
                    "verification": "confirmed",
                    "sha256": _fixture(samples).sha256,
                    "expected_cmd_id": 0x0A06,
                    "expected_ascii": "fcYqTC",
                }
            }
        ),
        encoding="utf-8",
    )
    out_path = tmp_path / "result.json"

    exit_code = main(
        [
            "--iq",
            str(iq_path),
            "--manifest",
            str(manifest_path),
            "--decoders",
            "upstream,improved_v67",
            "--out",
            str(out_path),
        ]
    )

    assert exit_code != 0
    assert json.loads(out_path.read_text(encoding="utf-8"))["success"] is False


def test_console_discovers_audited_entry_point_factories_end_to_end(
    tmp_path,
    monkeypatch,
):
    samples = _samples()
    iq_path = tmp_path / "RX_BLUE_ganrao_1"
    iq_path.write_bytes(samples.tobytes())
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                iq_path.name: {
                    "format": "complex64-le",
                    "sample_rate_hz": 4,
                    "team": "BLUE",
                    "target": "L1",
                    "verification": "confirmed",
                    "sha256": _fixture(samples).sha256,
                    "expected_cmd_id": 0x0A06,
                    "expected_ascii": "fcYqTC",
                }
            }
        ),
        encoding="utf-8",
    )
    out_path = tmp_path / "result.json"

    class EntryPoint:
        def __init__(self, name, factory):
            self.name = name
            self.group = benchmark_module.DECODER_ENTRY_POINT_GROUP
            self._factory = factory

        def load(self):
            return self._factory

    class EntryPoints(tuple):
        def select(self, *, group):
            return EntryPoints(ep for ep in self if ep.group == group)

    providers = EntryPoints(
        (
            EntryPoint("upstream", lambda: FakeDecoder("upstream")),
            EntryPoint(
                "improved_v67",
                lambda: FakeDecoder("improved_v67"),
            ),
        )
    )
    monkeypatch.setattr(
        benchmark_module.importlib_metadata,
        "entry_points",
        lambda: providers,
    )

    exit_code = main(
        [
            "--iq",
            str(iq_path),
            "--manifest",
            str(manifest_path),
            "--decoders",
            "upstream,improved_v67",
            "--out",
            str(out_path),
            "--chunk-samples",
            "4",
        ],
        cpu_clock_ns=iter(range(0, 1_000_000, 10)).__next__,
    )

    assert exit_code == 0
    assert json.loads(out_path.read_text(encoding="utf-8"))["success"] is True


def test_duplicate_entry_point_provider_is_a_stable_cli_error(
    tmp_path,
    monkeypatch,
):
    samples = _samples()
    iq_path = tmp_path / "RX_BLUE_ganrao_1"
    iq_path.write_bytes(samples.tobytes())
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(
        json.dumps({iq_path.name: _fixture_entry(samples)}),
        encoding="utf-8",
    )
    out_path = tmp_path / "result.json"

    class EntryPoint:
        name = "upstream"
        group = "sdr_receiver_py_wrapper.decoder_plugins"

        def load(self):
            return lambda: FakeDecoder("upstream")

    class EntryPoints(tuple):
        def select(self, *, group):
            return self

    monkeypatch.setattr(
        benchmark_module.importlib_metadata,
        "entry_points",
        lambda: EntryPoints((EntryPoint(), EntryPoint())),
    )

    exit_code = main(
        [
            "--iq", str(iq_path),
            "--manifest", str(manifest_path),
            "--decoders", "upstream,improved_v67",
            "--out", str(out_path),
        ]
    )

    assert exit_code == 2
    report = json.loads(out_path.read_text(encoding="utf-8"))
    assert report["success"] is False
    assert "duplicate" in report["error"]


def _fixture_entry(samples):
    fixture = _fixture(samples)
    return {
        "format": fixture.format,
        "sample_rate_hz": fixture.sample_rate_hz,
        "team": fixture.team,
        "target": fixture.target,
        "verification": fixture.verification,
        "sha256": fixture.sha256,
        "expected_cmd_id": fixture.expected_cmd_id,
        "expected_ascii": fixture.expected_ascii,
    }


def test_iq_size_limit_is_checked_before_factory(tmp_path, monkeypatch):
    samples = _samples()
    iq_path = tmp_path / "RX_BLUE_ganrao_1"
    iq_path.write_bytes(samples.tobytes())
    calls = []
    monkeypatch.setattr(benchmark_module, "MAX_IQ_BYTES", 8)

    with pytest.raises(BenchmarkError, match="size limit"):
        run_benchmark(
            iq_path=iq_path,
            fixture_name=iq_path.name,
            fixture=_fixture(samples),
            decoder_names=("upstream", "improved_v67"),
            decoder_registry={
                "upstream": lambda: calls.append(1),
                "improved_v67": lambda: calls.append(2),
            },
        )

    assert calls == []


def test_working_set_budget_is_checked_before_factory(tmp_path, monkeypatch):
    samples = _samples()
    iq_path = tmp_path / "RX_BLUE_ganrao_1"
    iq_path.write_bytes(samples.tobytes())
    calls = []
    monkeypatch.setattr(
        benchmark_module,
        "MAX_BENCHMARK_WORKING_SET_BYTES",
        31,
    )

    with pytest.raises(BenchmarkError, match="working-set budget"):
        run_benchmark(
            iq_path=iq_path,
            fixture_name=iq_path.name,
            fixture=_fixture(samples),
            decoder_names=("upstream", "improved_v67"),
            decoder_registry={
                "upstream": lambda: calls.append(1),
                "improved_v67": lambda: calls.append(2),
            },
            chunk_samples=1,
        )

    assert calls == []


@pytest.mark.parametrize(
    "field,value",
    [
        ("context_version", True),
        ("receive_wall_time", 0),
        ("decoder_id", type("Text", (str,), {})("upstream")),
        ("profile", type("Text", (str,), {})("BLUE-L1")),
        ("team", type("Text", (str,), {})("BLUE")),
        ("target", type("Text", (str,), {})("L1")),
        ("crc_mode", type("Text", (str,), {})("test-verified")),
    ],
)
def test_command_scalar_fields_require_exact_types(tmp_path, field, value):
    decoder = FakeDecoder("upstream")
    peer = FakeDecoder("improved_v67")
    original_decode = decoder.decode

    def forged_decode(chunk, context):
        commands = original_decode(chunk, context)
        return [replace(commands[0], **{field: value})] if commands else []

    decoder.decode = forged_decode

    report = _run(tmp_path, decoder, peer)

    assert report["success"] is False
    assert report["decoders"][0]["status"] == "error"
    assert field in report["decoders"][0]["error"]


def test_snapshot_preserves_primary_and_close_failures(tmp_path, monkeypatch):
    samples = _samples(1)
    iq_path = tmp_path / "RX_BLUE_ganrao_1"
    iq_path.write_bytes(samples.tobytes())

    class BrokenSnapshot:
        def write(self, raw):
            raise OSError("primary-write")

        def close(self):
            raise OSError("cleanup-close")

    monkeypatch.setattr(
        benchmark_module.tempfile,
        "TemporaryFile",
        lambda **kwargs: BrokenSnapshot(),
    )

    with pytest.raises(BenchmarkError) as failure:
        with benchmark_module._verified_iq_snapshot(
            iq_path,
            _fixture(samples).sha256,
            1,
        ):
            pytest.fail("invalid snapshot must not be yielded")

    message = str(failure.value)
    assert "primary-write" in message
    assert "cleanup-close" in message


@pytest.mark.parametrize("protected", ["iq", "manifest"])
def test_cli_never_writes_error_report_over_inputs(tmp_path, protected):
    samples = _samples()
    iq_path = tmp_path / "RX_BLUE_ganrao_1"
    iq_path.write_bytes(samples.tobytes())
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(
        json.dumps({iq_path.name: _fixture_entry(samples)}),
        encoding="utf-8",
    )
    before_iq = iq_path.read_bytes()
    before_manifest = manifest_path.read_bytes()
    out = iq_path if protected == "iq" else manifest_path

    exit_code = main(
        [
            "--iq", str(iq_path),
            "--manifest", str(manifest_path),
            "--decoders", "upstream,improved_v67",
            "--out", str(out),
        ]
    )

    assert exit_code == 2
    assert iq_path.read_bytes() == before_iq
    assert manifest_path.read_bytes() == before_manifest


def test_cli_protects_resolved_relative_input_alias(tmp_path, monkeypatch):
    samples = _samples()
    iq_path = tmp_path / "RX_BLUE_ganrao_1"
    iq_path.write_bytes(samples.tobytes())
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(
        json.dumps({iq_path.name: _fixture_entry(samples)}),
        encoding="utf-8",
    )
    before = iq_path.read_bytes()
    monkeypatch.chdir(tmp_path)

    exit_code = main(
        [
            "--iq", iq_path.name,
            "--manifest", manifest_path.name,
            "--decoders", "upstream,improved_v67",
            "--out", f"./{iq_path.name}",
        ]
    )

    assert exit_code == 2
    assert iq_path.read_bytes() == before


def test_cli_protects_existing_hardlink_alias(tmp_path):
    samples = _samples()
    iq_path = tmp_path / "RX_BLUE_ganrao_1"
    iq_path.write_bytes(samples.tobytes())
    alias = tmp_path / "result.json"
    os.link(iq_path, alias)
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(
        json.dumps({iq_path.name: _fixture_entry(samples)}),
        encoding="utf-8",
    )
    before = iq_path.read_bytes()

    exit_code = main(
        [
            "--iq", str(iq_path),
            "--manifest", str(manifest_path),
            "--decoders", "upstream,improved_v67",
            "--out", str(alias),
        ]
    )

    assert exit_code == 2
    assert iq_path.read_bytes() == before
    assert alias.read_bytes() == before


def test_cli_protects_symlink_alias_when_supported(tmp_path):
    samples = _samples()
    iq_path = tmp_path / "RX_BLUE_ganrao_1"
    iq_path.write_bytes(samples.tobytes())
    alias = tmp_path / "result.json"
    try:
        alias.symlink_to(iq_path)
    except OSError:
        pytest.skip("symlink creation is not permitted")
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(
        json.dumps({iq_path.name: _fixture_entry(samples)}),
        encoding="utf-8",
    )
    before = iq_path.read_bytes()

    exit_code = main(
        [
            "--iq", str(iq_path),
            "--manifest", str(manifest_path),
            "--decoders", "upstream,improved_v67",
            "--out", str(alias),
        ]
    )

    assert exit_code == 2
    assert iq_path.read_bytes() == before


@pytest.mark.parametrize("factory_fails", [False, True])
def test_output_transaction_survives_provider_parent_redirection(
    tmp_path,
    factory_fails,
):
    samples = _samples()
    inputs = tmp_path / "inputs"
    inputs.mkdir()
    iq_path = inputs / "RX_BLUE_ganrao_1"
    iq_path.write_bytes(samples.tobytes())
    manifest_path = inputs / "manifest.json"
    manifest_path.write_text(
        json.dumps({iq_path.name: _fixture_entry(samples)}),
        encoding="utf-8",
    )
    safe_parent = tmp_path / "safe"
    safe_parent.mkdir()
    moved_parent = tmp_path / "safe-pinned"
    out_path = safe_parent / "result.json"
    before_iq = iq_path.read_bytes()
    before_manifest = manifest_path.read_bytes()
    first = FakeDecoder("upstream")

    def redirecting_factory():
        try:
            safe_parent.rename(moved_parent)
            safe_parent.symlink_to(inputs, target_is_directory=True)
        except OSError:
            # Windows parent HANDLE intentionally blocks rename/reparse swap.
            pass
        if factory_fails:
            raise RuntimeError("provider failed after redirection attempt")
        return first

    exit_code = main(
        [
            "--iq", str(iq_path),
            "--manifest", str(manifest_path),
            "--decoders", "upstream,improved_v67",
            "--out", str(out_path),
            "--chunk-samples", "4",
        ],
        decoder_registry={
            "upstream": redirecting_factory,
            "improved_v67": lambda: FakeDecoder("improved_v67"),
        },
        cpu_clock_ns=iter(range(0, 1_000_000, 10)).__next__,
    )

    assert iq_path.read_bytes() == before_iq
    assert manifest_path.read_bytes() == before_manifest
    fixed_report = moved_parent / "result.json"
    report_path = fixed_report if fixed_report.exists() else out_path
    assert report_path.is_file()
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert exit_code == (1 if factory_fails else 0)
    assert report["success"] is (not factory_fails)


@pytest.mark.parametrize("cancel_type", [KeyboardInterrupt, SystemExit])
def test_snapshot_closes_and_preserves_cancellation(
    tmp_path,
    monkeypatch,
    cancel_type,
):
    samples = _samples(1)
    iq_path = tmp_path / "RX_BLUE_ganrao_1"
    iq_path.write_bytes(samples.tobytes())
    real_temporary_file = tempfile.TemporaryFile
    closed = []

    class Snapshot:
        def __init__(self):
            self._stream = real_temporary_file(mode="w+b")

        def __getattr__(self, name):
            return getattr(self._stream, name)

        def close(self):
            self._stream.close()
            closed.append(True)
            raise OSError("cancel-cleanup")

    monkeypatch.setattr(
        benchmark_module.tempfile,
        "TemporaryFile",
        lambda **kwargs: Snapshot(),
    )
    cancellation = cancel_type("cancel-now")

    with pytest.raises(cancel_type) as raised:
        with benchmark_module._verified_iq_snapshot(
            iq_path,
            _fixture(samples).sha256,
            1,
        ):
            raise cancellation

    assert raised.value is cancellation
    assert closed == [True]
    assert "cancel-cleanup" in repr(raised.value.args)


def test_setup_registers_decoder_benchmark_console_script():
    setup_text = (Path(__file__).parents[1] / "setup.py").read_text(
        encoding="utf-8"
    )
    assert (
        "decoder_benchmark = sdr_receiver_py_wrapper.decoder_benchmark:main"
        in setup_text
    )
