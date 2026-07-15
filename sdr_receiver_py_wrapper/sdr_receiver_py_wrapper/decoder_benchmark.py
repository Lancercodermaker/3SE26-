"""Deterministic offline comparison of pure IQ decoder plugins."""

from __future__ import annotations

import argparse
from collections import deque
from contextlib import contextmanager
from dataclasses import dataclass, field
import hashlib
from importlib import metadata as importlib_metadata
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
from .command_validator import CommandValidator
from .models import DecodedCommand, DecodeContext, IqChunk, ResetReason


_BYTES_PER_COMPLEX64 = 8
_DEFAULT_CHUNK_SAMPLES = 262_144
_MAX_CHUNK_SAMPLES = 16_777_216
_MAX_COMMANDS_PER_CHUNK = 256
_MAX_COUNTER = (1 << 63) - 1
_DECODER_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}\Z")
MAX_IQ_BYTES = 2_147_483_648
DECODER_ENTRY_POINT_GROUP = "sdr_receiver_py_wrapper.decoder_plugins"


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


DEFAULT_DECODER_REGISTRY = MappingProxyType({})


@dataclass(frozen=True)
class _TrustedContext:
    team: str
    target: str
    profile: str
    target_version: int
    context_version: int
    level: int

    def expose(self) -> DecodeContext:
        return DecodeContext(
            team=self.team,
            target=self.target,
            profile=self.profile,
            target_version=self.target_version,
            context_version=self.context_version,
        )


@dataclass(frozen=True)
class _ChunkTruth:
    chunk_id: int
    first_sample_index: int
    sample_count: int
    sample_rate_hz: int
    rx_wall_time: float
    rx_monotonic_ns: int
    target_version: int
    context_version: int


@dataclass(frozen=True)
class _QueuedChunk:
    chunk: IqChunk
    context: DecodeContext
    private_samples: np.ndarray
    source_samples: np.ndarray
    truth: _ChunkTruth


@dataclass
class _DecoderRun:
    name: str
    plugin: object | None = None
    decoder_id: str | None = None
    queue: deque[_QueuedChunk] = field(default_factory=deque)
    peak_queue_depth: int = 0
    cpu_time_ns: int = 0
    expected_cmd_outputs: int = 0
    expected_cmd_conflicts: int = 0
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
    decoder_registry: Mapping[str, Callable[[], object]] | None = None,
    chunk_samples: int = _DEFAULT_CHUNK_SAMPLES,
    cpu_clock_ns: Callable[[], int] = time.process_time_ns,
) -> dict[str, object]:
    """Compare plugins over one verified file and isolated IQ snapshots."""

    fixture = _confirmed_fixture(fixture_name, fixture)
    source_path = _validated_iq_path(iq_path, fixture_name)
    names = _selected_decoder_names(decoder_names)
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

    trusted_context = _TrustedContext(
        team=fixture.team,
        target=fixture.target,
        profile=f"{fixture.team}-{fixture.target}",
        target_version=1,
        context_version=1,
        level=int(fixture.target[1]),
    )
    expected_payload = _expected_payload(fixture)
    with _verified_iq_snapshot(
        source_path,
        fixture.sha256,
        chunk_samples,
    ) as (snapshot, source_hash, sample_count):
        registry = (
            _entry_point_registry(names)
            if decoder_registry is None
            else decoder_registry
        )
        factories = _selected_factories(names, registry)
        runs = [
            _construct_decoder(name, factory, trusted_context)
            for name, factory in zip(names, factories)
        ]
        plugin_ids = [
            run.decoder_id
            for run in runs
            if run.decoder_id is not None
        ]
        if len(plugin_ids) != len(set(plugin_ids)):
            raise BenchmarkError(
                "selected plugins must expose unique decoder_id values"
            )
        _replay_snapshot(
            snapshot,
            runs,
            fixture=fixture,
            trusted_context=trusted_context,
            expected_payload=expected_payload,
            chunk_samples=chunk_samples,
            cpu_clock_ns=cpu_clock_ns,
        )
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


def _selected_decoder_names(
    decoder_names: Sequence[str],
) -> tuple[str, ...]:
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
    return names


def _selected_factories(
    names: tuple[str, ...],
    registry: Mapping[str, Callable[[], object]],
) -> tuple[Callable[[], object], ...]:
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
    return factories


def _entry_point_registry(
    names: tuple[str, ...],
) -> Mapping[str, Callable[[], object]]:
    try:
        discovered = importlib_metadata.entry_points()
        if hasattr(discovered, "select"):
            grouped = discovered.select(group=DECODER_ENTRY_POINT_GROUP)
        else:
            grouped = discovered.get(DECODER_ENTRY_POINT_GROUP, ())
        entry_points = tuple(islice(iter(grouped), 257))
    except Exception as exc:
        raise BenchmarkError(
            "cannot discover decoder entry points: "
            f"{_safe_error_text(exc)}"
        ) from None
    if len(entry_points) > 256:
        raise BenchmarkError("decoder entry-point count exceeds 256")

    selected: dict[str, object] = {}
    for entry_point in entry_points:
        try:
            name = entry_point.name
        except Exception as exc:
            raise BenchmarkError(
                "decoder entry point has no readable name: "
                f"{_safe_error_text(exc)}"
            ) from None
        if name not in names:
            continue
        if name in selected:
            raise BenchmarkError(
                f"duplicate decoder entry-point provider: {name!r}"
            )
        selected[name] = entry_point

    factories: dict[str, Callable[[], object]] = {}
    for name in names:
        entry_point = selected.get(name)
        if entry_point is None:
            raise BenchmarkError(
                f"decoder {name!r} is unavailable; install an approved "
                f"{DECODER_ENTRY_POINT_GROUP!r} provider"
            )
        try:
            factory = entry_point.load()
        except Exception as exc:
            raise BenchmarkError(
                f"cannot load decoder provider {name!r}: "
                f"{_safe_error_text(exc)}"
            ) from None
        if not callable(factory):
            raise BenchmarkError(
                f"decoder provider {name!r} must load a callable factory"
            )
        factories[name] = factory
    return MappingProxyType(factories)


def _construct_decoder(
    name: str,
    factory: Callable[[], object],
    trusted_context: _TrustedContext,
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
        context = trusted_context.expose()
        reset_result = reset(ResetReason.STARTUP, context)
        if reset_result is not None:
            raise TypeError("plugin reset must return None")
        if not _context_matches(context, trusted_context):
            raise RuntimeError("plugin mutated its reset context")
    except Exception as exc:
        run.fail(exc)
    return run


@contextmanager
def _verified_iq_snapshot(
    path: Path,
    expected_hash: str | None,
    read_samples: int,
) -> object:
    if expected_hash is None:
        raise BenchmarkError("confirmed fixture is missing SHA-256")
    digest = hashlib.sha256()
    snapshot = None
    try:
        snapshot = tempfile.TemporaryFile(mode="w+b")
        with path.open("rb") as stream:
            opened = os.fstat(stream.fileno())
            _validate_open_file(opened, path)
            total = 0
            while True:
                raw = stream.read(read_samples * _BYTES_PER_COMPLEX64)
                if not raw:
                    break
                if len(raw) % _BYTES_PER_COMPLEX64:
                    raise BenchmarkError(
                        "IQ file size must be a multiple of 8 bytes"
                    )
                samples = np.frombuffer(raw, dtype="<c8")
                if (
                    not np.isfinite(samples.real).all()
                    or not np.isfinite(samples.imag).all()
                ):
                    raise BenchmarkError(
                        "IQ samples must contain only finite values"
                    )
                digest.update(raw)
                written = snapshot.write(raw)
                if written != len(raw):
                    raise OSError("short write while creating IQ snapshot")
                total += len(raw)
            finished = os.fstat(stream.fileno())
        if (
            not _same_file_snapshot(opened, finished)
            or total != opened.st_size
        ):
            raise BenchmarkError("IQ file changed during snapshot creation")
        actual = digest.hexdigest()
        if actual != expected_hash:
            raise BenchmarkError(
                "IQ SHA-256 does not match the confirmed manifest"
            )
        snapshot.flush()
        snapshot.seek(0)
    except OSError as exc:
        if snapshot is not None:
            snapshot.close()
        raise BenchmarkError(
            f"cannot snapshot IQ file: {_safe_error_text(exc)}"
        ) from None
    except Exception:
        if snapshot is not None:
            snapshot.close()
        raise
    try:
        yield snapshot, actual, opened.st_size // _BYTES_PER_COMPLEX64
    finally:
        try:
            snapshot.close()
        except OSError as exc:
            raise BenchmarkError(
                "cannot close private IQ snapshot: "
                f"{_safe_error_text(exc)}"
            ) from None


def _validate_open_file(file_stat: os.stat_result, path: Path) -> None:
    if not stat.S_ISREG(file_stat.st_mode):
        raise BenchmarkError(f"IQ source must be a regular file: {path.name}")
    if file_stat.st_size == 0:
        raise BenchmarkError(
            "IQ source must contain at least one complex64 sample"
        )
    if file_stat.st_size > MAX_IQ_BYTES:
        raise BenchmarkError(
            f"IQ source exceeds the {MAX_IQ_BYTES}-byte size limit"
        )
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


def _replay_snapshot(
    snapshot,
    runs: list[_DecoderRun],
    *,
    fixture: FixtureSpec,
    trusted_context: _TrustedContext,
    expected_payload: bytes,
    chunk_samples: int,
    cpu_clock_ns: Callable[[], int],
) -> None:
    try:
        snapshot.seek(0)
        first_sample_index = 0
        chunk_id = 0
        while True:
            raw = snapshot.read(chunk_samples * _BYTES_PER_COMPLEX64)
            if not raw:
                break
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
                trusted_context=trusted_context,
            )
            for run in runs:
                _drain_one(
                    run,
                    fixture=fixture,
                    trusted_context=trusted_context,
                    expected_payload=expected_payload,
                    cpu_clock_ns=cpu_clock_ns,
                )
            first_sample_index += len(source)
            chunk_id += 1
    except OSError as exc:
        raise BenchmarkError(
            f"cannot replay private IQ snapshot: {_safe_error_text(exc)}"
        ) from None


def _enqueue_private_chunks(
    runs: list[_DecoderRun],
    source: np.ndarray,
    *,
    chunk_id: int,
    first_sample_index: int,
    fixture: FixtureSpec,
    trusted_context: _TrustedContext,
) -> None:
    for run in runs:
        if run.error is not None:
            continue
        private = np.array(source, dtype=np.complex64, order="C", copy=True)
        private.flags.writeable = False
        sample_time = first_sample_index / fixture.sample_rate_hz
        truth = _ChunkTruth(
            chunk_id=chunk_id,
            first_sample_index=first_sample_index,
            sample_count=len(source),
            sample_rate_hz=fixture.sample_rate_hz,
            rx_wall_time=sample_time,
            rx_monotonic_ns=int(sample_time * 1_000_000_000),
            target_version=trusted_context.target_version,
            context_version=trusted_context.context_version,
        )
        chunk = IqChunk(
            chunk_id=truth.chunk_id,
            first_sample_index=truth.first_sample_index,
            samples=private,
            sample_rate_hz=truth.sample_rate_hz,
            rx_wall_time=truth.rx_wall_time,
            rx_monotonic_ns=truth.rx_monotonic_ns,
            lo_hz=0,
            rf_bandwidth_hz=0,
            rx_gain_db=0,
            target_version=truth.target_version,
            context_version=truth.context_version,
        )
        run.queue.append(
            _QueuedChunk(
                chunk=chunk,
                context=trusted_context.expose(),
                private_samples=private,
                source_samples=source,
                truth=truth,
            )
        )
        run.peak_queue_depth = max(run.peak_queue_depth, len(run.queue))


def _drain_one(
    run: _DecoderRun,
    *,
    fixture: FixtureSpec,
    trusted_context: _TrustedContext,
    expected_payload: bytes,
    cpu_clock_ns: Callable[[], int],
) -> None:
    if run.error is not None or not run.queue or run.plugin is None:
        run.queue.clear()
        return
    queued = run.queue.popleft()
    try:
        started = _clock_value(cpu_clock_ns)
        try:
            commands = run.plugin.decode(queued.chunk, queued.context)
        finally:
            finished = _clock_value(cpu_clock_ns)
            if finished < started:
                raise BenchmarkError("CPU clock moved backwards")
            run.cpu_time_ns += finished - started
            _assert_exposed_contract(queued, trusted_context)
        _consume_commands(
            run,
            commands,
            truth=queued.truth,
            trusted_context=trusted_context,
            fixture=fixture,
            expected_payload=expected_payload,
        )
    except Exception as exc:
        run.fail(exc)


def _context_matches(
    context: DecodeContext,
    trusted: _TrustedContext,
) -> bool:
    return (
        type(context) is DecodeContext
        and context.team == trusted.team
        and context.target == trusted.target
        and context.profile == trusted.profile
        and context.target_version == trusted.target_version
        and context.context_version == trusted.context_version
    )


def _assert_exposed_contract(
    queued: _QueuedChunk,
    trusted_context: _TrustedContext,
) -> None:
    chunk = queued.chunk
    truth = queued.truth
    if not _context_matches(queued.context, trusted_context):
        raise RuntimeError("plugin mutated its decode context")
    if (
        type(chunk) is not IqChunk
        or chunk.chunk_id != truth.chunk_id
        or chunk.first_sample_index != truth.first_sample_index
        or chunk.sample_rate_hz != truth.sample_rate_hz
        or chunk.rx_wall_time != truth.rx_wall_time
        or chunk.rx_monotonic_ns != truth.rx_monotonic_ns
        or chunk.lo_hz != 0
        or chunk.rf_bandwidth_hz != 0
        or chunk.rx_gain_db != 0
        or chunk.target_version != truth.target_version
        or chunk.context_version != truth.context_version
        or chunk.rf_metrics is not None
    ):
        raise RuntimeError("plugin mutated its exposed IQ chunk metadata")
    samples = chunk.samples
    if (
        samples is not queued.private_samples
        or type(samples) is not np.ndarray
        or samples.dtype != np.complex64
        or samples.ndim != 1
        or len(samples) != truth.sample_count
        or not samples.flags.c_contiguous
        or not samples.flags.owndata
        or samples.base is not None
        or samples.flags.writeable
        or not np.array_equal(samples, queued.source_samples)
    ):
        raise RuntimeError("plugin mutated its exposed IQ sample buffer")


def _clock_value(clock: Callable[[], int]) -> int:
    value = clock()
    if type(value) is not int or value < 0:
        raise BenchmarkError("CPU clock must return an exact nonnegative int")
    return value


def _consume_commands(
    run: _DecoderRun,
    commands: object,
    *,
    truth: _ChunkTruth,
    trusted_context: _TrustedContext,
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
        _validate_command(
            command,
            run,
            truth,
            trusted_context,
        )
        if command.cmd_id == fixture.expected_cmd_id:
            run.expected_cmd_outputs += 1
            production_result = CommandValidator().prevalidate(command)
            oracle_match = (
                production_result.accepted is True
                and production_result.level == trusted_context.level
                and command.payload == expected_payload
                and command.crc8_ok is True
                and command.crc16_ok is True
            )
            if oracle_match:
                run.oracle_matches += 1
                if run.first_key_time_s is None:
                    run.first_key_time_s = (
                        command.first_sample_index / fixture.sample_rate_hz
                    )
            else:
                run.expected_cmd_conflicts += 1


def _validate_command(
    command: object,
    run: _DecoderRun,
    truth: _ChunkTruth,
    trusted_context: _TrustedContext,
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
    if command.profile != trusted_context.profile:
        raise ValueError("command profile does not match benchmark context")
    if (
        command.team != trusted_context.team
        or command.target != trusted_context.target
    ):
        raise ValueError(
            "command team/target does not match benchmark context"
        )
    if command.context_version != trusted_context.context_version:
        raise ValueError(
            "command context_version does not match benchmark context"
        )
    if type(command.crc8_ok) is not bool:
        raise ValueError("command crc8_ok must be an exact bool")
    if type(command.crc16_ok) is not bool:
        raise ValueError("command crc16_ok must be an exact bool")
    if type(command.crc_mode) is not str or not command.crc_mode:
        raise ValueError("command crc_mode must be a nonempty exact str")
    chunk_last = truth.first_sample_index + truth.sample_count - 1
    if (
        type(command.first_sample_index) is not int
        or type(command.last_sample_index) is not int
        or not truth.first_sample_index
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
        or command.receive_wall_time != truth.rx_wall_time
    ):
        raise ValueError(
            "command receive_wall_time must match the trusted source chunk"
        )


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
    try:
        ac = diagnostics.ac
        sof = diagnostics.sof
        crc8 = diagnostics.crc8
        crc16 = diagnostics.crc16
        run.diagnostics = BenchmarkDiagnostics(
            ac=ac,
            sof=sof,
            crc8=crc8,
            crc16=crc16,
        )
    except Exception as exc:
        run.diagnostics_status = "invalid"
        run.fail(exc)
        return
    run.diagnostics_status = "available"


def _result(run: _DecoderRun) -> dict[str, object]:
    diagnostics = run.diagnostics
    mismatch_reasons = []
    if run.oracle_matches != 1:
        mismatch_reasons.append("oracle_match_count")
    if run.expected_cmd_conflicts != 0:
        mismatch_reasons.append("expected_cmd_conflict")
    if run.error is not None:
        status = "error"
    elif not mismatch_reasons:
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
        "expected_cmd_conflicts": run.expected_cmd_conflicts,
        "expected_cmd_outputs": run.expected_cmd_outputs,
        "first_key_time_s": run.first_key_time_s,
        "mismatch_reasons": mismatch_reasons,
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
        if _supports_directory_fsync():
            _fsync_directory(parent)
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


def _supports_directory_fsync() -> bool:
    return os.name == "posix" and hasattr(os, "O_DIRECTORY")


def _fsync_directory(parent: Path) -> None:
    descriptor = os.open(
        parent,
        os.O_RDONLY | getattr(os, "O_DIRECTORY", 0),
    )
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


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
    out_path = Path(args.out)
    try:
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
        protected = {
            iq_path.resolve(strict=False),
            manifest_path.resolve(strict=False),
        }
        if out_path.resolve(strict=False) in protected:
            raise BenchmarkError(
                "output path must not replace the IQ or manifest input"
            )
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
            decoder_registry=decoder_registry,
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
