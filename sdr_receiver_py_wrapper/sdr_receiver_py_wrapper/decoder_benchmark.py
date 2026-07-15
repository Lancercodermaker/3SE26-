"""Deterministic offline comparison of pure IQ decoder plugins."""

from __future__ import annotations

import argparse
from collections import deque
from dataclasses import dataclass, field
import hashlib
from itertools import islice
import json
import math
import os
from pathlib import Path
import re
import stat
import tempfile
import time
from types import MappingProxyType
from typing import Callable, Mapping, Sequence

import numpy as np

from .fixture_manifest import (
    FixtureManifestError,
    FixtureSpec,
    confirmed_fixtures,
    load_fixture_manifest,
)
from .models import DecodedCommand, DecodeContext, IqChunk, ResetReason


_BYTES_PER_COMPLEX64 = 8
_DEFAULT_CHUNK_SAMPLES = 262_144
_MAX_CHUNK_SAMPLES = 16_777_216
_MAX_COMMANDS_PER_CHUNK = 256
_MAX_COUNTER = (1 << 63) - 1
_DECODER_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}\Z")


class BenchmarkError(ValueError):
    """The benchmark configuration or IQ source is invalid."""


@dataclass(frozen=True)
class BenchmarkDiagnostics:
    """Trusted cumulative protocol-stage counters exposed by a plugin."""

    ac: int
    sof: int
    crc8: int
    crc16: int

    def __post_init__(self) -> None:
        for name in ("ac", "sof", "crc8", "crc16"):
            value = getattr(self, name)
            if type(value) is not int or not 0 <= value <= _MAX_COUNTER:
                raise ValueError(
                    f"benchmark diagnostic {name} must be an exact "
                    "nonnegative int"
                )


def _missing_upstream_backend():
    raise RuntimeError(
        "upstream production decoder backend is unavailable; inject an "
        "audited "
        "backend through decoder_registry"
    )


def _missing_v67_backend():
    raise RuntimeError(
        "improved_v67 production decoder core is unavailable; inject an "
        "audited "
        "backend through decoder_registry"
    )


DEFAULT_DECODER_REGISTRY = MappingProxyType(
    {
        "upstream": _missing_upstream_backend,
        "improved_v67": _missing_v67_backend,
    }
)


@dataclass
class _DecoderRun:
    name: str
    plugin: object | None = None
    decoder_id: str | None = None
    queue: deque[tuple[IqChunk, DecodeContext]] = field(default_factory=deque)
    peak_queue_depth: int = 0
    cpu_time_ns: int = 0
    oracle_matches: int = 0
    first_key_time_s: float | None = None
    error: str | None = None
    diagnostics_status: str = "unavailable"
    diagnostics: BenchmarkDiagnostics | None = None

    def fail(self, error: object) -> None:
        if self.error is None:
            self.error = _safe_error_text(error)


def run_benchmark(
    *,
    iq_path: str | Path,
    fixture_name: str,
    fixture: FixtureSpec,
    decoder_names: Sequence[str],
    decoder_registry: Mapping[str, Callable[[], object]],
    chunk_samples: int = _DEFAULT_CHUNK_SAMPLES,
    cpu_clock_ns: Callable[[], int] = time.process_time_ns,
) -> dict[str, object]:
    """Compare plugins over one verified file and isolated IQ snapshots."""

    fixture = _confirmed_fixture(fixture_name, fixture)
    source_path = _validated_iq_path(iq_path, fixture_name)
    names, factories = _selected_factories(decoder_names, decoder_registry)
    if (
        type(chunk_samples) is not int
        or not 1 <= chunk_samples <= _MAX_CHUNK_SAMPLES
    ):
        raise BenchmarkError(
            "chunk_samples must be an exact int from 1 to "
            f"{_MAX_CHUNK_SAMPLES}"
        )
    if not callable(cpu_clock_ns):
        raise BenchmarkError("cpu_clock_ns must be callable")

    source_hash, sample_count = _verify_source(
        source_path,
        fixture.sha256,
        chunk_samples,
    )

    context = DecodeContext(
        team=fixture.team,
        target=fixture.target,
        profile=f"{fixture.team}-{fixture.target}",
        target_version=1,
        context_version=1,
    )
    runs = [
        _construct_decoder(name, factory, context)
        for name, factory in zip(names, factories)
    ]
    plugin_ids = [run.decoder_id for run in runs if run.decoder_id is not None]
    if len(plugin_ids) != len(set(plugin_ids)):
        raise BenchmarkError(
            "selected plugins must expose unique decoder_id values"
        )

    expected_payload = _expected_payload(fixture)
    replay_hash = hashlib.sha256()
    with source_path.open("rb") as stream:
        opened = os.fstat(stream.fileno())
        _validate_open_file(opened, source_path)
        first_sample_index = 0
        chunk_id = 0
        while True:
            raw = stream.read(chunk_samples * _BYTES_PER_COMPLEX64)
            if not raw:
                break
            if len(raw) % _BYTES_PER_COMPLEX64:
                raise BenchmarkError(
                    "IQ file size must be a multiple of 8 bytes"
                )
            replay_hash.update(raw)
            source = np.frombuffer(raw, dtype="<c8").astype(
                np.complex64,
                copy=True,
            )
            source.flags.writeable = False
            _enqueue_private_chunks(
                runs,
                source,
                chunk_id=chunk_id,
                first_sample_index=first_sample_index,
                fixture=fixture,
                context=context,
            )
            for run in runs:
                _drain_one(
                    run,
                    fixture=fixture,
                    expected_payload=expected_payload,
                    cpu_clock_ns=cpu_clock_ns,
                )
            first_sample_index += len(source)
            chunk_id += 1
        finished = os.fstat(stream.fileno())
    if not _same_file_snapshot(opened, finished):
        raise BenchmarkError("IQ file changed while it was being replayed")
    if replay_hash.hexdigest() != source_hash:
        raise BenchmarkError("IQ file changed after SHA-256 verification")

    for run in runs:
        _collect_diagnostics(run)
    results = [_result(run) for run in runs]
    success = all(result["status"] == "passed" for result in results)
    return {
        "decoders": results,
        "fixture": fixture_name,
        "iq_sha256": source_hash,
        "sample_count": sample_count,
        "sample_rate_hz": fixture.sample_rate_hz,
        "success": success,
    }


def _confirmed_fixture(name: str, fixture: FixtureSpec) -> FixtureSpec:
    try:
        confirmed = confirmed_fixtures({name: fixture})
    except FixtureManifestError as exc:
        raise BenchmarkError(str(exc)) from None
    if name not in confirmed:
        raise BenchmarkError("benchmark fixture must be confirmed")
    return confirmed[name]


def _validated_iq_path(path: str | Path, fixture_name: str) -> Path:
    try:
        source = Path(path)
    except (TypeError, ValueError) as exc:
        raise BenchmarkError(
            f"invalid IQ path: {_safe_error_text(exc)}"
        ) from None
    if source.name != fixture_name:
        raise BenchmarkError(
            "IQ path basename must exactly match the fixture name"
        )
    return source


def _selected_factories(
    decoder_names: Sequence[str],
    registry: Mapping[str, Callable[[], object]],
) -> tuple[tuple[str, ...], tuple[Callable[[], object], ...]]:
    if isinstance(decoder_names, (str, bytes)):
        raise BenchmarkError("decoder_names must be a sequence of names")
    try:
        names = tuple(islice(iter(decoder_names), 17))
    except (TypeError, RuntimeError) as exc:
        raise BenchmarkError(
            f"cannot read decoder names: {_safe_error_text(exc)}"
        ) from None
    if len(names) < 2:
        raise BenchmarkError("at least two decoders are required")
    if len(names) > 16:
        raise BenchmarkError("at most 16 decoders may be compared")
    for name in names:
        if type(name) is not str or _DECODER_NAME.fullmatch(name) is None:
            raise BenchmarkError(
                "decoder names must be exact safe identifiers"
            )
    if len(names) != len(set(names)):
        raise BenchmarkError("decoder names must be unique")
    try:
        factories = tuple(registry[name] for name in names)
    except (KeyError, TypeError):
        unavailable = next(
            (name for name in names if name not in registry),
            "unknown",
        )
        raise BenchmarkError(
            f"decoder {unavailable!r} is unavailable"
        ) from None
    if any(not callable(factory) for factory in factories):
        raise BenchmarkError("every selected decoder factory must be callable")
    return names, factories


def _construct_decoder(
    name: str,
    factory: Callable[[], object],
    context: DecodeContext,
) -> _DecoderRun:
    run = _DecoderRun(name=name)
    try:
        plugin = factory()
        decoder_id = getattr(plugin, "decoder_id")
        if (
            type(decoder_id) is not str
            or _DECODER_NAME.fullmatch(decoder_id) is None
        ):
            raise TypeError(
                "plugin decoder_id must be an exact safe identifier"
            )
        decode = getattr(plugin, "decode")
        reset = getattr(plugin, "reset")
        if not callable(decode) or not callable(reset):
            raise TypeError("plugin decode and reset hooks must be callable")
        run.plugin = plugin
        run.decoder_id = decoder_id
        reset_result = reset(ResetReason.STARTUP, context)
        if reset_result is not None:
            raise TypeError("plugin reset must return None")
    except Exception as exc:
        run.fail(exc)
    return run


def _verify_source(
    path: Path,
    expected_hash: str | None,
    read_samples: int,
) -> tuple[str, int]:
    if expected_hash is None:
        raise BenchmarkError("confirmed fixture is missing SHA-256")
    digest = hashlib.sha256()
    try:
        with path.open("rb") as stream:
            opened = os.fstat(stream.fileno())
            _validate_open_file(opened, path)
            while True:
                raw = stream.read(read_samples * _BYTES_PER_COMPLEX64)
                if not raw:
                    break
                digest.update(raw)
            finished = os.fstat(stream.fileno())
    except OSError as exc:
        raise BenchmarkError(
            f"cannot read IQ file: {_safe_error_text(exc)}"
        ) from None
    if not _same_file_snapshot(opened, finished):
        raise BenchmarkError("IQ file changed during SHA-256 verification")
    if opened.st_size % _BYTES_PER_COMPLEX64:
        raise BenchmarkError("IQ file size must be a multiple of 8 bytes")
    actual = digest.hexdigest()
    if actual != expected_hash:
        raise BenchmarkError(
            "IQ SHA-256 does not match the confirmed manifest"
        )
    return actual, opened.st_size // _BYTES_PER_COMPLEX64


def _validate_open_file(file_stat: os.stat_result, path: Path) -> None:
    if not stat.S_ISREG(file_stat.st_mode):
        raise BenchmarkError(f"IQ source must be a regular file: {path.name}")
    if file_stat.st_size < 0 or file_stat.st_size % _BYTES_PER_COMPLEX64:
        raise BenchmarkError("IQ file size must be a multiple of 8 bytes")


def _same_file_snapshot(before: os.stat_result, after: os.stat_result) -> bool:
    return (
        before.st_dev,
        before.st_ino,
        before.st_size,
        before.st_mtime_ns,
    ) == (
        after.st_dev,
        after.st_ino,
        after.st_size,
        after.st_mtime_ns,
    )


def _enqueue_private_chunks(
    runs: list[_DecoderRun],
    source: np.ndarray,
    *,
    chunk_id: int,
    first_sample_index: int,
    fixture: FixtureSpec,
    context: DecodeContext,
) -> None:
    for run in runs:
        if run.error is not None:
            continue
        private = np.array(source, dtype=np.complex64, order="C", copy=True)
        private.flags.writeable = False
        sample_time = first_sample_index / fixture.sample_rate_hz
        chunk = IqChunk(
            chunk_id=chunk_id,
            first_sample_index=first_sample_index,
            samples=private,
            sample_rate_hz=fixture.sample_rate_hz,
            rx_wall_time=sample_time,
            rx_monotonic_ns=int(sample_time * 1_000_000_000),
            lo_hz=0,
            rf_bandwidth_hz=0,
            rx_gain_db=0,
            target_version=context.target_version,
            context_version=context.context_version,
        )
        run.queue.append((chunk, context))
        run.peak_queue_depth = max(run.peak_queue_depth, len(run.queue))


def _drain_one(
    run: _DecoderRun,
    *,
    fixture: FixtureSpec,
    expected_payload: bytes,
    cpu_clock_ns: Callable[[], int],
) -> None:
    if run.error is not None or not run.queue or run.plugin is None:
        run.queue.clear()
        return
    chunk, context = run.queue.popleft()
    try:
        started = _clock_value(cpu_clock_ns)
        try:
            commands = run.plugin.decode(chunk, context)
        finally:
            finished = _clock_value(cpu_clock_ns)
            if finished < started:
                raise BenchmarkError("CPU clock moved backwards")
            run.cpu_time_ns += finished - started
        _consume_commands(
            run,
            commands,
            chunk=chunk,
            context=context,
            fixture=fixture,
            expected_payload=expected_payload,
        )
    except Exception as exc:
        run.fail(exc)


def _clock_value(clock: Callable[[], int]) -> int:
    value = clock()
    if type(value) is not int or value < 0:
        raise BenchmarkError("CPU clock must return an exact nonnegative int")
    return value


def _consume_commands(
    run: _DecoderRun,
    commands: object,
    *,
    chunk: IqChunk,
    context: DecodeContext,
    fixture: FixtureSpec,
    expected_payload: bytes,
) -> None:
    if type(commands) is not list:
        raise TypeError("plugin decode must return an exact list")
    if len(commands) > _MAX_COMMANDS_PER_CHUNK:
        raise ValueError(
            "plugin may return at most "
            f"{_MAX_COMMANDS_PER_CHUNK} commands per chunk"
        )
    for command in commands:
        _validate_command(command, run, chunk, context)
        if (
            command.cmd_id == fixture.expected_cmd_id
            and command.payload == expected_payload
        ):
            run.oracle_matches += 1
            if run.first_key_time_s is None:
                run.first_key_time_s = (
                    command.first_sample_index / fixture.sample_rate_hz
                )


def _validate_command(
    command: object,
    run: _DecoderRun,
    chunk: IqChunk,
    context: DecodeContext,
) -> None:
    if type(command) is not DecodedCommand:
        raise TypeError("plugin results must be exact DecodedCommand values")
    if type(command.cmd_id) is not int or not 0 <= command.cmd_id <= 0xFFFF:
        raise ValueError("command cmd_id must be an exact uint16")
    if type(command.payload) is not bytes or len(command.payload) > 256:
        raise ValueError(
            "command payload must be exact bytes of at most 256 bytes"
        )
    if command.decoder_id != run.decoder_id:
        raise ValueError("command decoder_id does not match its plugin")
    if command.profile != context.profile:
        raise ValueError("command profile does not match benchmark context")
    if command.team != context.team or command.target != context.target:
        raise ValueError(
            "command team/target does not match benchmark context"
        )
    if command.context_version != context.context_version:
        raise ValueError(
            "command context_version does not match benchmark context"
        )
    if type(command.crc8_ok) is not bool or command.crc8_ok is not True:
        raise ValueError("command crc8_ok must be exact True")
    if type(command.crc16_ok) is not bool or command.crc16_ok is not True:
        raise ValueError("command crc16_ok must be exact True")
    if type(command.crc_mode) is not str or not command.crc_mode:
        raise ValueError("command crc_mode must be a nonempty exact str")
    chunk_last = chunk.first_sample_index + len(chunk.samples) - 1
    if (
        type(command.first_sample_index) is not int
        or type(command.last_sample_index) is not int
        or not chunk.first_sample_index
        <= command.first_sample_index
        <= command.last_sample_index
        <= chunk_last
    ):
        raise ValueError(
            "command sample range must be inside the source chunk"
        )
    if (
        type(command.receive_wall_time) not in (int, float)
        or not math.isfinite(command.receive_wall_time)
    ):
        raise ValueError("command receive_wall_time must be finite")


def _collect_diagnostics(run: _DecoderRun) -> None:
    if run.plugin is None:
        return
    hook = getattr(run.plugin, "benchmark_diagnostics", None)
    if hook is None:
        return
    if not callable(hook):
        run.diagnostics_status = "invalid"
        run.fail(TypeError("benchmark_diagnostics must be callable"))
        return
    try:
        diagnostics = hook()
    except NotImplementedError:
        return
    except Exception as exc:
        run.diagnostics_status = "invalid"
        run.fail(exc)
        return
    if type(diagnostics) is not BenchmarkDiagnostics:
        run.diagnostics_status = "invalid"
        run.fail(
            TypeError(
                "benchmark_diagnostics must return BenchmarkDiagnostics"
            )
        )
        return
    run.diagnostics = diagnostics
    run.diagnostics_status = "available"


def _result(run: _DecoderRun) -> dict[str, object]:
    diagnostics = run.diagnostics
    if run.error is not None:
        status = "error"
    elif run.oracle_matches == 1:
        status = "passed"
    else:
        status = "mismatch"
    result: dict[str, object] = {
        "ac": None if diagnostics is None else diagnostics.ac,
        "cpu_time_ns": run.cpu_time_ns,
        "crc16": None if diagnostics is None else diagnostics.crc16,
        "crc8": None if diagnostics is None else diagnostics.crc8,
        "decoder": run.name,
        "decoder_id": run.decoder_id,
        "diagnostics_status": run.diagnostics_status,
        "first_key_time_s": run.first_key_time_s,
        "oracle_matches": run.oracle_matches,
        "peak_queue_depth": run.peak_queue_depth,
        "sof": None if diagnostics is None else diagnostics.sof,
        "status": status,
    }
    if run.error is not None:
        result["error"] = run.error
    return result


def _expected_payload(fixture: FixtureSpec) -> bytes:
    assert fixture.expected_ascii is not None
    try:
        return fixture.expected_ascii.encode("ascii", errors="strict")
    except UnicodeError:
        raise BenchmarkError(
            "confirmed expected_ascii must be ASCII"
        ) from None


def _safe_error_text(error: object) -> str:
    try:
        rendered = str(error)
    except Exception:
        rendered = type(error).__name__
    rendered = rendered.encode(
        "unicode_escape",
        errors="backslashreplace",
    ).decode("ascii")
    return rendered[:512]


def _canonical_json(report: Mapping[str, object]) -> bytes:
    try:
        return json.dumps(
            report,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8") + b"\n"
    except (TypeError, ValueError, RecursionError) as exc:
        raise BenchmarkError(
            "benchmark report is not JSON-safe: "
            f"{_safe_error_text(exc)}"
        ) from None


def _write_report(path: Path, report: Mapping[str, object]) -> None:
    encoded = _canonical_json(report)
    parent = path.parent
    if not parent.is_dir():
        raise BenchmarkError("output parent directory does not exist")
    temporary: str | None = None
    try:
        descriptor, temporary = tempfile.mkstemp(
            prefix=f".{path.name}.",
            dir=parent,
        )
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        temporary = None
    except OSError as exc:
        raise BenchmarkError(
            f"cannot write benchmark report: {_safe_error_text(exc)}"
        ) from None
    finally:
        if temporary is not None:
            try:
                os.unlink(temporary)
            except OSError:
                pass


def _parse_decoder_names(raw: str) -> tuple[str, ...]:
    names = tuple(raw.split(","))
    if any(not name or name != name.strip() for name in names):
        raise BenchmarkError("--decoders must be comma-separated exact names")
    return names


def _argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--iq", required=True)
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--decoders", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument(
        "--chunk-samples",
        type=int,
        default=_DEFAULT_CHUNK_SAMPLES,
    )
    return parser


def main(
    argv: Sequence[str] | None = None,
    *,
    decoder_registry: Mapping[str, Callable[[], object]] | None = None,
    cpu_clock_ns: Callable[[], int] = time.process_time_ns,
) -> int:
    """Run the command-line benchmark; 0 means every decoder matched once."""

    args = _argument_parser().parse_args(argv)
    manifest_path = Path(args.manifest)
    iq_argument = Path(args.iq)
    if iq_argument.is_absolute():
        iq_path = iq_argument
    else:
        cwd_candidate = Path.cwd() / iq_argument
        iq_path = (
            cwd_candidate
            if cwd_candidate.exists()
            else manifest_path.parent / iq_argument
        )
    out_path = Path(args.out)
    protected = {
        iq_path.resolve(strict=False),
        manifest_path.resolve(strict=False),
    }
    if out_path.resolve(strict=False) in protected:
        return 2
    try:
        manifest = load_fixture_manifest(manifest_path)
        fixture_name = iq_path.name
        try:
            fixture = manifest[fixture_name]
        except KeyError:
            raise BenchmarkError(
                "IQ basename is not an exact fixture name in the manifest"
            ) from None
        report = run_benchmark(
            iq_path=iq_path,
            fixture_name=fixture_name,
            fixture=fixture,
            decoder_names=_parse_decoder_names(args.decoders),
            decoder_registry=(
                DEFAULT_DECODER_REGISTRY
                if decoder_registry is None
                else decoder_registry
            ),
            chunk_samples=args.chunk_samples,
            cpu_clock_ns=cpu_clock_ns,
        )
        _write_report(out_path, report)
        return 0 if report["success"] is True else 1
    except (
        BenchmarkError,
        FixtureManifestError,
        OSError,
        TypeError,
        ValueError,
    ) as exc:
        try:
            _write_report(
                out_path,
                {"error": _safe_error_text(exc), "success": False},
            )
        except BenchmarkError:
            pass
        return 2


__all__ = [
    "BenchmarkDiagnostics",
    "BenchmarkError",
    "DEFAULT_DECODER_REGISTRY",
    "main",
    "run_benchmark",
]
