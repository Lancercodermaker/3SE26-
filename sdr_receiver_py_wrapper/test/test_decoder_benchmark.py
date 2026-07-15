from __future__ import annotations

from dataclasses import replace
import hashlib
import json
from pathlib import Path

import numpy as np
import pytest

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

    def reset(self, reason, context) -> None:
        self.reset_context = context

    def decode(self, chunk, context):
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

    assert report["success"] is True
    assert mutator.seen == observer.seen
    assert all(owns and not writeable for *_, owns, writeable in observer.seen)
    assert [entry[:2] for entry in observer.seen] == [(0, 0), (1, 4)]
    expected_hashes = [
        hashlib.sha256(_samples()[start:start + 4].tobytes()).hexdigest()
        for start in (0, 4)
    ]
    assert [entry[2] for entry in observer.seen] == expected_hashes

    by_name = {entry["decoder"]: entry for entry in report["decoders"]}
    for decoder in ("upstream", "improved_v67"):
        result = by_name[decoder]
        assert result["status"] == "passed"
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


def test_command_metadata_and_crc_claims_must_be_consistent(tmp_path):
    broken = FakeDecoder("upstream")
    peer = FakeDecoder("improved_v67")
    original_decode = broken.decode

    def invalid_metadata(chunk, context):
        commands = original_decode(chunk, context)
        return [replace(commands[0], crc16_ok=False)] if commands else []

    broken.decode = invalid_metadata

    report = _run(tmp_path, broken, peer)

    assert report["success"] is False
    assert report["decoders"][0]["status"] == "error"
    assert "crc16_ok" in report["decoders"][0]["error"]


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


def test_setup_registers_decoder_benchmark_console_script():
    setup_text = (Path(__file__).parents[1] / "setup.py").read_text(
        encoding="utf-8"
    )
    assert (
        "decoder_benchmark = sdr_receiver_py_wrapper.decoder_benchmark:main"
        in setup_text
    )
