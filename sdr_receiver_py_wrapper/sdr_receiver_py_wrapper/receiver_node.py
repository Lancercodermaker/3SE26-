from __future__ import annotations

import copy
from dataclasses import asdict, dataclass, replace
import json
import logging
import math
import numbers
import os
from pathlib import Path
from queue import Empty
import sys
import threading
import time
from typing import Optional

import numpy as np
import rclpy
from rclpy.node import Node
from rclpy.qos import HistoryPolicy, QoSProfile, ReliabilityPolicy
from std_msgs.msg import String, UInt8

from sdr_receiver.msg import JamCode, RadarContext as RadarContextMsg, RadarWirelessFrame

from .command_validator import CommandValidator, ValidationResult
from .competition_controller import CompetitionController, RadarContext
from .context_arbiter import (
    ContextArbiter,
    Observation,
    format_context_decision_log,
    resolve_context_authority,
    resolve_diagnostic_values,
    resolve_receiver_target,
)
from .original_receiver_adapter import ReceiverCoreAdapter
from .patches import JamKeyEvent, PatchCallbacks, RawFrameEvent, TargetChangeEvent
from .profile_import import AdaptiveProfileLoadError, load_adaptive_profile
from .acquisition import AcquisitionEngine
from .device_session import DeviceSession
from .iq_file_source import IqFilePluto
from .models import DecodedCommand, DecodeContext, IqChunk, ResetReason
from . import rf_safety
from .structured_recorder import StructuredRecorder, _json_snapshot
from .v67_decoder import V67Decoder


DEFAULT_ORIGINAL_SCRIPT = "auto"
PRIMARY_DECODER_ID = "improved_v67"
CONSTRUCTOR_CLEANUP_WAIT_SEC = 0.25
_LOGGER = logging.getLogger(__name__)


class ReceiverPipelineError(RuntimeError):
    """The common receiver pipeline could not preserve its processing contract."""


class PublicationIndeterminateError(RuntimeError):
    """ROS accepted a command whose validator commit could not be proven."""


@dataclass(frozen=True)
class PipelineDiagnosticError:
    stage: str
    reason: str


@dataclass(frozen=True)
class ReceiverPipelineResult:
    primary_commands: tuple[DecodedCommand, ...]
    shadow_commands: tuple[DecodedCommand, ...]
    validation_results: tuple[ValidationResult, ...]
    shadow_error: Optional[str] = None
    diagnostic_errors: tuple[PipelineDiagnosticError, ...] = ()


@dataclass(frozen=True)
class ReceiverFoundationConfig:
    """Validated construction inputs shared by acquisition, RF, and recording."""

    sdr_uri: str = "ip:192.168.2.1"
    decoder_primary: str = PRIMARY_DECODER_ID
    decoder_shadow: str = ""
    acquisition_queue_size: int = 8
    record_queue_size: int = 32
    adc_code_scale: float = 2048.0
    rf_clipping_ratio: float = 0.001
    initial_rx_gain: int = 20

    def __post_init__(self) -> None:
        if not isinstance(self.sdr_uri, str) or not self.sdr_uri.strip():
            raise ValueError("sdr_uri must be a non-empty string")
        if not isinstance(self.decoder_primary, str) or not self.decoder_primary:
            raise ValueError("decoder_primary must be a non-empty string")
        if not isinstance(self.decoder_shadow, str):
            raise TypeError("decoder_shadow must be a string")
        for name in ("acquisition_queue_size", "record_queue_size"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, numbers.Integral) or value <= 0:
                raise ValueError(f"{name} must be a positive integer")
        if (
            isinstance(self.adc_code_scale, bool)
            or not isinstance(self.adc_code_scale, numbers.Real)
            or not math.isfinite(float(self.adc_code_scale))
            or self.adc_code_scale <= 0
        ):
            raise ValueError("adc_code_scale must be a finite positive number")
        if (
            isinstance(self.rf_clipping_ratio, bool)
            or not isinstance(self.rf_clipping_ratio, numbers.Real)
            or not math.isfinite(float(self.rf_clipping_ratio))
            or not 0 < self.rf_clipping_ratio <= 1
        ):
            raise ValueError("rf_clipping_ratio must be in (0, 1]")
        if (
            isinstance(self.initial_rx_gain, bool)
            or not isinstance(self.initial_rx_gain, numbers.Integral)
            or not -1 <= self.initial_rx_gain <= 73
        ):
            raise ValueError("initial_rx_gain must be -1 or between 0 and 73")

    def create_acquisition(self, device, **versions) -> AcquisitionEngine:
        return AcquisitionEngine(
            device,
            queue_size=self.acquisition_queue_size,
            **versions,
        )

    def create_recorder(self, record_dir, prefix, **kwargs) -> StructuredRecorder:
        return StructuredRecorder(
            record_dir,
            prefix,
            queue_size=self.record_queue_size,
            **kwargs,
        )

    def measure_and_classify(self, samples: np.ndarray):
        metrics = rf_safety.measure_rf(samples, code_scale=self.adc_code_scale)
        state = rf_safety.classify_rf(
            metrics,
            clipping_threshold=self.rf_clipping_ratio,
        )
        return metrics, state


@dataclass(frozen=True)
class CommonRuntimeResult:
    chunk: IqChunk
    rf_state: rf_safety.RfState
    pipeline_result: ReceiverPipelineResult


class CommonReceiverRuntime:
    """Own the competition device and drive one common receive pipeline."""

    def __init__(
        self,
        *,
        backend_factory,
        config: ReceiverFoundationConfig,
        primary,
        output,
        context_provider,
        radio_settings_provider,
        snapshot_provider=None,
        shadow=None,
        recorder=None,
    ) -> None:
        self.recorder = recorder
        self.device = None
        self.stop_event = threading.Event()
        self.thread: Optional[threading.Thread] = None
        self.acquisition_thread: Optional[threading.Thread] = None
        self.processing_thread: Optional[threading.Thread] = None
        self.worker_error: Optional[BaseException] = None
        self.last_rf_state: Optional[rf_safety.RfState] = None
        self._closed = False
        self._resources_closed = False
        self._device_close_done = False
        self._pending_device_cleanup = None
        self._recorder_close_done = recorder is None
        self._cleanup_thread: Optional[threading.Thread] = None
        self._cleanup_done = threading.Event()
        self.cleanup_error: Optional[str] = None
        self._lifecycle = "OPEN"
        self._lifecycle_lock = threading.RLock()
        self._io_lock = threading.Lock()
        self._resource_lock = threading.Lock()
        self._worker_state_lock = threading.Lock()
        self._active_workers: set[str] = set()
        self._starting_workers = False
        self._run_active = threading.Event()
        self._shutdown_reason: Optional[str] = None
        self._applied_radio_settings: Optional[dict] = None
        self._pending_device_reconnect = False
        self._last_decode_context: Optional[DecodeContext] = None
        self._expected_next_sample: Optional[int] = None
        self._context_condition = threading.Condition()
        self._chunk_contexts: dict[int, DecodeContext] = {}
        self._error_lock = threading.Lock()
        try:
            if not isinstance(config, ReceiverFoundationConfig):
                raise TypeError("config must be a ReceiverFoundationConfig")
            self.config = config
            self.context_provider = context_provider
            self.radio_settings_provider = radio_settings_provider
            self.snapshot_provider = snapshot_provider
            self.pipeline = ReceiverPipeline(
                primary=primary,
                shadow=shadow,
                output=output,
                recorder=recorder,
                config=config,
            )
            self.device = DeviceSession(backend_factory)
            self.acquisition = config.create_acquisition(self.device)
            if self.snapshot_provider is None:
                self._sync_device_settings()
            else:
                _context, settings = self.snapshot_provider()
                self._sync_device_settings(settings)
        except BaseException:
            try:
                cleanup_done = self._start_cleanup(
                    "common receiver setup failed"
                )
                cleanup_done.wait(timeout=CONSTRUCTOR_CLEANUP_WAIT_SEC)
            except BaseException:
                _LOGGER.exception(
                    "failed to start common receiver constructor cleanup"
                )
            raise

    def process_once(self) -> CommonRuntimeResult:
        with self._lifecycle_lock:
            self._require_manual_queue_operation()
            produced = self._acquire_once()
            if produced is None:
                raise RuntimeError("acquisition queue rejected IQ chunk")
            result = self._process_next(timeout_sec=0.0)
            if result is None or result.chunk.chunk_id != produced.chunk_id:
                raise RuntimeError("acquisition queue order is inconsistent")
            return result

    def acquire_once(self):
        with self._lifecycle_lock:
            self._require_manual_queue_operation()
            return self._acquire_once()

    def _acquire_once(self):
        with self._io_lock:
            if self.snapshot_provider is None:
                context = self.context_provider()
                settings = None
            else:
                context, settings = self.snapshot_provider()
            if not isinstance(context, DecodeContext):
                raise TypeError("context_provider must return a DecodeContext")
            self._sync_device_settings(settings)
            self._set_acquisition_versions(context)
            stats_before = self.acquisition.stats
            produced = self.acquisition.read_once()
            stats_after = self.acquisition.stats
            if produced is None:
                if stats_after.reconnects > stats_before.reconnects:
                    self._pending_device_reconnect = True
                    if self.recorder is not None:
                        accepted = self.recorder.write_event(
                            "discontinuity",
                            {
                                "reason": "device_reconnect",
                                "reconnects": stats_after.reconnects,
                                "read_errors": stats_after.read_errors,
                            },
                        )
                        if accepted is not True:
                            raise ReceiverPipelineError(
                                "failed to record reconnect discontinuity"
                            )
                    return None
                if stats_after.read_errors > stats_before.read_errors:
                    raise RuntimeError("acquisition read failed")
                return None
            with self._context_condition:
                self._chunk_contexts[produced.chunk_id] = context
                self._context_condition.notify_all()
            return produced

    def process_next(self, *, timeout_sec=0.05) -> Optional[CommonRuntimeResult]:
        with self._lifecycle_lock:
            self._require_manual_queue_operation()
            return self._process_next(timeout_sec=timeout_sec)

    def _process_next(self, *, timeout_sec=0.05) -> Optional[CommonRuntimeResult]:
        try:
            queued = self.acquisition._queue.get(timeout=timeout_sec)
        except Empty:
            return None
        try:
            context = self._context_for_chunk(queued.chunk_id, timeout_sec)
            return self._process_queued(queued, context)
        finally:
            self.acquisition._queue.task_done()

    def _process_queued(self, queued, context) -> CommonRuntimeResult:
        metrics, state = self.config.measure_and_classify(queued.samples)
        chunk = replace(queued, rf_metrics=metrics)
        self.last_rf_state = state
        reset_diagnostics = []
        for reason in self._reset_reasons(chunk, context):
            reset_diagnostics.extend(
                self.pipeline.reset_decoders(reason, context, chunk)
            )
        if self.recorder is not None:
            accepted = self.recorder.write_event(
                "rf_state",
                {
                    "chunk_id": chunk.chunk_id,
                    "first_sample_index": chunk.first_sample_index,
                    "last_sample_index": (
                        chunk.first_sample_index + int(chunk.samples.size) - 1
                    ),
                    "target_version": chunk.target_version,
                    "context_version": chunk.context_version,
                    "target": context.target,
                    "team": context.team,
                    "profile": context.profile,
                    "adc_code_scale": self.config.adc_code_scale,
                    "rf_clipping_ratio": self.config.rf_clipping_ratio,
                    "state": state.value,
                    "metrics": metrics.__dict__,
                },
            )
            if accepted is not True:
                raise ReceiverPipelineError("failed to record RF state event")
        result = self.pipeline.process(chunk, context)
        self._last_decode_context = context
        self._expected_next_sample = (
            chunk.first_sample_index + int(chunk.samples.size)
        )
        self._pending_device_reconnect = False
        if reset_diagnostics:
            result = replace(
                result,
                diagnostic_errors=(
                    *reset_diagnostics,
                    *result.diagnostic_errors,
                ),
            )
        return CommonRuntimeResult(
            chunk=chunk,
            rf_state=state,
            pipeline_result=result,
        )

    def _reset_reasons(self, chunk, context) -> tuple[ResetReason, ...]:
        reasons = []
        previous = self._last_decode_context
        if previous is None:
            return (ResetReason.STARTUP,)
        if self._pending_device_reconnect:
            reasons.append(ResetReason.DEVICE_RECONNECT)
        if (
            context.target_version != previous.target_version
            or context.target != previous.target
        ):
            reasons.append(ResetReason.TARGET_CHANGE)
        if (
            context.context_version != previous.context_version
            or context.team != previous.team
            or context.profile != previous.profile
        ):
            reasons.append(ResetReason.CONTEXT_CHANGE)
        if (
            self._expected_next_sample is not None
            and chunk.first_sample_index != self._expected_next_sample
        ):
            reasons.append(ResetReason.MANUAL)
        return tuple(reasons)

    def start(self) -> None:
        with self._lifecycle_lock:
            if (
                self._lifecycle == "STOPPING"
                or self._pending_device_cleanup is not None
                or self.stop_event.is_set()
            ):
                raise RuntimeError(
                    "common receiver runtime is stopping; retry close first"
                )
            if self._closed or self._resources_closed:
                raise RuntimeError("common receiver runtime is closed")
            if self._run_active.is_set():
                return
            self.stop_event.clear()
            self._lifecycle = "STARTING"
            self.worker_error = None
            self._shutdown_reason = None
            self.acquisition_thread = threading.Thread(
                target=self._run_acquisition,
                name="sdr-common-acquisition",
                daemon=True,
            )
            self.processing_thread = threading.Thread(
                target=self._run_processing,
                name="sdr-common-processing",
                daemon=True,
            )
            self.thread = self.processing_thread
            workers = (self.acquisition_thread, self.processing_thread)
            started = []
            with self._worker_state_lock:
                self._starting_workers = True
                self._active_workers.clear()
            self._run_active.set()
            try:
                for worker in workers:
                    with self._worker_state_lock:
                        self._active_workers.add(worker.name)
                    try:
                        worker.start()
                    except BaseException:
                        with self._worker_state_lock:
                            self._active_workers.discard(worker.name)
                        raise
                    started.append(worker)
            except BaseException as exc:
                with self._error_lock:
                    if self._shutdown_reason is None:
                        self._shutdown_reason = "common receiver start failed"
                self.stop_event.set()
                with self._context_condition:
                    self._context_condition.notify_all()
                with self._worker_state_lock:
                    self._starting_workers = False
                for worker in started:
                    worker.join(timeout=3.0)
                if any(worker.is_alive() for worker in started):
                    raise RuntimeError(
                        "common receiver start rollback timed out"
                    ) from exc
                self._run_active.clear()
                cleanup_done = self._start_cleanup(
                    "common receiver start failed"
                )
                cleanup_done.wait(timeout=3.0)
                with self._resource_lock:
                    resources_closed = self._resources_closed
                self._closed = resources_closed
                self._lifecycle = "CLOSED" if resources_closed else "STOPPING"
                raise
            finally:
                should_cleanup = False
                with self._worker_state_lock:
                    self._starting_workers = False
                    if not self._active_workers:
                        self._run_active.clear()
                        should_cleanup = self.stop_event.is_set()
                if should_cleanup:
                    with self._error_lock:
                        reason = (
                            self._shutdown_reason
                            or "common receiver stopped"
                        )
                    self._start_cleanup(reason)
            if not self.stop_event.is_set():
                self._lifecycle = "RUNNING"

    def close(self, *, timeout_sec: float = 3.0) -> None:
        with self._lifecycle_lock:
            if self._closed:
                return
            self.stop_event.set()
            self._lifecycle = "STOPPING"
            with self._error_lock:
                if self._shutdown_reason is None:
                    self._shutdown_reason = "common receiver stopped"
                reason = self._shutdown_reason
            with self._context_condition:
                self._context_condition.notify_all()
            deadline = time.monotonic() + max(0.0, float(timeout_sec))
            current = threading.current_thread()
            workers = tuple(
                worker
                for worker in (self.acquisition_thread, self.processing_thread)
                if worker is not None and worker is not current
            )
            for worker in workers:
                if worker.is_alive():
                    worker.join(timeout=max(0.0, deadline - time.monotonic()))
            alive = [worker.name for worker in workers if worker.is_alive()]
            if alive:
                raise TimeoutError(
                    "common receiver worker threads did not stop: "
                    + ", ".join(alive)
                )
            self._run_active.clear()
            cleanup_done = self._start_cleanup(reason)
            if not cleanup_done.wait(
                timeout=max(0.0, deadline - time.monotonic())
            ):
                raise TimeoutError("common receiver cleanup did not finish")
            with self._resource_lock:
                cleanup_error = self.cleanup_error
                resources_closed = self._resources_closed
            if cleanup_error is not None:
                raise RuntimeError(cleanup_error)
            if not resources_closed:
                raise RuntimeError("common receiver cleanup is incomplete")
            self._closed = True
            self._lifecycle = "CLOSED"

    def status(self) -> dict:
        recorder_status = {"enabled": self.recorder is not None}
        if self.recorder is not None:
            stats = getattr(self.recorder, "stats", None)
            if stats is not None:
                recorder_status["stats"] = _json_snapshot(
                    asdict(stats) if hasattr(stats, "__dataclass_fields__") else stats
                )
            recorder_status["paths"] = {
                name: (None if getattr(self.recorder, name, None) is None else str(
                    getattr(self.recorder, name)
                ))
                for name in ("iq_path", "chunks_path", "events_path", "summary_path")
            }
        return {
            "lifecycle": self._lifecycle,
            "running": any(
                thread is not None and thread.is_alive()
                for thread in (self.acquisition_thread, self.processing_thread)
            ),
            "worker_error": (
                None if self.worker_error is None else str(self.worker_error)
            ),
            "cleanup_error": self.cleanup_error,
            "queue": {
                "depth": self.acquisition._queue.qsize(),
                "capacity": self.acquisition._queue.maxsize,
            },
            "device": asdict(self.device.stats),
            "acquisition": asdict(self.acquisition.stats),
            "recorder": recorder_status,
            "rf_state": (
                None if self.last_rf_state is None else self.last_rf_state.value
            ),
        }

    def _run_acquisition(self) -> None:
        try:
            while not self.stop_event.is_set():
                self._acquire_once()
        except BaseException as exc:
            self._fail_worker(exc)
        finally:
            self._worker_exited()

    def _run_processing(self) -> None:
        try:
            while not self.stop_event.is_set():
                self._process_next()
        except BaseException as exc:
            self._fail_worker(exc)
        finally:
            self._worker_exited()

    def _fail_worker(self, exc) -> None:
        with self._error_lock:
            if self.worker_error is None:
                self.worker_error = exc
                self._shutdown_reason = "common receiver worker failed"
        self.stop_event.set()
        with self._context_condition:
            self._context_condition.notify_all()

    def _worker_exited(self) -> None:
        should_cleanup = False
        with self._worker_state_lock:
            self._active_workers.discard(threading.current_thread().name)
            if not self._starting_workers and not self._active_workers:
                self._run_active.clear()
                should_cleanup = self.stop_event.is_set()
        if should_cleanup:
            with self._error_lock:
                reason = self._shutdown_reason or "common receiver stopped"
            self._lifecycle = "STOPPING"
            self._start_cleanup(reason)

    def _start_cleanup(self, reason: str) -> threading.Event:
        with self._resource_lock:
            if self._resources_closed:
                self._cleanup_done.set()
                return self._cleanup_done
            if self._cleanup_thread is not None and not self._cleanup_done.is_set():
                return self._cleanup_done
            self.cleanup_error = None
            self._cleanup_done = threading.Event()
            self._cleanup_thread = threading.Thread(
                target=self._run_cleanup_attempt,
                args=(reason,),
                name="sdr-common-cleanup",
                daemon=True,
            )
            cleanup_thread = self._cleanup_thread
            cleanup_done = self._cleanup_done
        cleanup_thread.start()
        return cleanup_done

    def _run_cleanup_attempt(self, reason: str) -> None:
        errors = []
        with self._resource_lock:
            close_device = not self._device_close_done and self.device is not None
        if close_device:
            try:
                self._close_device_resource()
            except BaseException as exc:
                errors.append(f"device: {exc}")
            else:
                with self._resource_lock:
                    self._device_close_done = True
        else:
            with self._resource_lock:
                if self.device is None:
                    self._device_close_done = True

        with self._resource_lock:
            close_recorder = (
                None
                if self._recorder_close_done
                else getattr(self.recorder, "close", None)
            )
        if callable(close_recorder):
            try:
                close_recorder(stopped_reason=reason)
            except BaseException as exc:
                errors.append(f"recorder: {exc}")
            else:
                with self._resource_lock:
                    self._recorder_close_done = True
        elif close_recorder is not None:
            errors.append("recorder: close is not callable")
        else:
            with self._resource_lock:
                self._recorder_close_done = True

        error_text = "; ".join(errors)
        with self._resource_lock:
            self.cleanup_error = error_text or None
            self._resources_closed = (
                self._device_close_done and self._recorder_close_done
            )
        try:
            if error_text:
                _LOGGER.error("common receiver cleanup failed: %s", error_text)
        finally:
            with self._resource_lock:
                self._cleanup_done.set()

    def _close_device_resource(self) -> None:
        """Close a detached backend hook exactly once, with retry on failure."""

        device = self.device
        with device._lock:
            pending_cleanup = self._pending_device_cleanup
            if pending_cleanup is not None:
                try:
                    pending_cleanup()
                except BaseException:
                    device._connection_errors += 1
                    raise
                device._closes += 1
                self._pending_device_cleanup = None
                return

            backend = device._backend
            cleanup = (
                None if backend is None else device._find_cleanup(backend)
            )
            try:
                device.close()
            except BaseException:
                if cleanup is not None:
                    self._pending_device_cleanup = cleanup
                raise

    def _context_for_chunk(self, chunk_id, timeout_sec):
        deadline = time.monotonic() + max(0.05, float(timeout_sec))
        with self._context_condition:
            while chunk_id not in self._chunk_contexts:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise RuntimeError("IQ chunk has no matching DecodeContext")
                self._context_condition.wait(timeout=remaining)
            return self._chunk_contexts.pop(chunk_id)

    def _require_manual_queue_operation(self) -> None:
        if self._run_active.is_set():
            raise RuntimeError(
                "manual queue operation is unavailable while runtime workers "
                "are active"
            )
        if (
            self._lifecycle == "STOPPING"
            or self._pending_device_cleanup is not None
            or self.stop_event.is_set()
        ):
            raise RuntimeError(
                "common receiver runtime is stopping; retry close first"
            )
        if self._closed or self._resources_closed:
            raise RuntimeError("common receiver runtime is closed")

    def _set_acquisition_versions(self, context: DecodeContext) -> None:
        with self.acquisition._state_lock:
            self.acquisition._target_version = context.target_version
            self.acquisition._context_version = context.context_version

    def _sync_device_settings(self, settings=None) -> None:
        if settings is None:
            settings = self.radio_settings_provider()
        settings = dict(settings)
        required = {
            "sample_rate_hz",
            "lo_hz",
            "rf_bandwidth_hz",
            "rx_gain_db",
        }
        if set(settings) != required:
            raise ValueError("radio settings provider returned invalid keys")
        if settings == self._applied_radio_settings:
            return
        self.device.configure(
            sample_rate=settings["sample_rate_hz"],
            lo_hz=settings["lo_hz"],
            rf_bandwidth=settings["rf_bandwidth_hz"],
            gain=settings["rx_gain_db"],
        )
        self._applied_radio_settings = settings


def _create_decoder_plugin(decoder_id: str, core):
    _require_decoder_available(decoder_id)
    return V67Decoder(core=core)


def _require_decoder_available(decoder_id: str) -> None:
    if decoder_id != PRIMARY_DECODER_ID:
        raise ValueError(f"unavailable decoder: {decoder_id!r}")


def _validate_decoder_registry(config: ReceiverFoundationConfig) -> None:
    if not isinstance(config, ReceiverFoundationConfig):
        raise TypeError("config must be a ReceiverFoundationConfig")
    try:
        _require_decoder_available(config.decoder_primary)
    except ValueError as exc:
        raise ValueError(
            f"decoder_primary is unavailable: {config.decoder_primary!r}"
        ) from exc
    if config.decoder_shadow:
        try:
            _require_decoder_available(config.decoder_shadow)
        except ValueError as exc:
            raise ValueError(
                f"decoder_shadow is unavailable: {config.decoder_shadow!r}"
            ) from exc


def _normalize_competition_target(target: object) -> Optional[str]:
    value = str(target or "").strip().upper()
    aliases = {
        "L1": "L1",
        "L2": "L2",
        "L3": "L3",
        "JAM_L1_KEY": "L1",
        "JAM_L2_KEY": "L2",
        "JAM_L3_KEY": "L3",
        "INFO": "INFO",
    }
    return aliases.get(value)


class NodeCommandOutput:
    """Adapt the pipeline output contract to the existing sole JamCode gate."""

    def __init__(self, node) -> None:
        self._node = node
        self.publisher_decoder_id = node.primary_decoder_id

    def publish(self, command: DecodedCommand, *, before_commit=None) -> ValidationResult:
        return self._node._handle_competition_decoded_command(
            command,
            before_publish=before_commit,
        )


class ReceiverPipeline:
    """Give primary and shadow decoders the same immutable input evidence."""

    def __init__(
        self,
        *,
        primary,
        output,
        shadow=None,
        recorder=None,
        config: ReceiverFoundationConfig | None = None,
    ) -> None:
        self._check_decoder(primary, "primary")
        if shadow is not None:
            self._check_decoder(shadow, "shadow")
        if not callable(getattr(output, "publish", None)) or not isinstance(
            getattr(output, "publisher_decoder_id", None), str
        ):
            raise TypeError("output must expose publisher_decoder_id and publish(command)")
        if output.publisher_decoder_id != primary.decoder_id:
            raise ValueError("output publisher_decoder_id must match the primary decoder")
        if recorder is not None and (
            not callable(getattr(recorder, "write_chunk", None))
            or not callable(getattr(recorder, "write_event", None))
        ):
            raise TypeError("recorder must expose write_chunk and write_event")
        if config is not None and not isinstance(config, ReceiverFoundationConfig):
            raise TypeError("config must be a ReceiverFoundationConfig")
        self.primary = primary
        self.shadow = shadow
        self.output = output
        self.recorder = recorder
        self.config = config or ReceiverFoundationConfig()

    @staticmethod
    def _check_decoder(decoder, role: str) -> None:
        if (
            not isinstance(getattr(decoder, "decoder_id", None), str)
            or not decoder.decoder_id
            or not all(
                callable(getattr(decoder, method, None))
                for method in ("decode", "reset", "stats")
            )
        ):
            raise TypeError(f"{role} decoder is not protocol compatible")

    def reset_decoders(self, reason, context, chunk):
        payload = {
            "reason": reason.value,
            "chunk_id": chunk.chunk_id,
            "context_version": context.context_version,
            "target_version": context.target_version,
            "target": context.target,
            "team": context.team,
            "profile": context.profile,
        }
        try:
            self.primary.reset(reason, context)
        except Exception as exc:
            self._record_diagnostic(
                "decoder_reset_error",
                {**payload, "role": "primary", "error": str(exc)},
            )
            raise
        self._record_event(
            "decoder_reset",
            {**payload, "role": "primary"},
        )

        diagnostics = []
        if self.shadow is not None:
            try:
                self.shadow.reset(reason, context)
                diagnostic_error = self._record_diagnostic(
                    "decoder_reset",
                    {**payload, "role": "shadow"},
                )
            except Exception as exc:
                diagnostic_error = self._record_diagnostic(
                    "decoder_reset_error",
                    {**payload, "role": "shadow", "error": str(exc)},
                )
                reason_text = f"shadow reset failed: {exc}"
                if diagnostic_error is not None:
                    reason_text += f"; {diagnostic_error}"
                diagnostics.append(
                    PipelineDiagnosticError(
                        stage="shadow_decoder_reset",
                        reason=reason_text,
                    )
                )
            else:
                if diagnostic_error is not None:
                    diagnostics.append(
                        PipelineDiagnosticError(
                            stage="shadow_decoder_reset_diagnostic",
                            reason=f"shadow {diagnostic_error}",
                        )
                    )
        return tuple(diagnostics)

    def process(
        self,
        chunk: IqChunk,
        context: DecodeContext,
    ) -> ReceiverPipelineResult:
        if not isinstance(chunk, IqChunk):
            raise TypeError("chunk must be an IqChunk")
        if not isinstance(context, DecodeContext):
            raise TypeError("context must be a DecodeContext")
        if chunk.context_version != context.context_version:
            raise ValueError("chunk and DecodeContext context_version must match")
        if chunk.target_version != context.target_version:
            raise ValueError("chunk and DecodeContext target_version must match")
        if self.recorder is not None:
            accepted = self.recorder.write_chunk(
                chunk,
                metadata={
                    "target": context.target,
                    "team": context.team,
                    "profile": context.profile,
                    "target_version": context.target_version,
                    "context_version": context.context_version,
                    "decoder_primary": self.primary.decoder_id,
                    "decoder_shadow": (
                        "" if self.shadow is None else self.shadow.decoder_id
                    ),
                    "adc_code_scale": self.config.adc_code_scale,
                },
            )
            if accepted is not True:
                raise ReceiverPipelineError("failed to record IQ chunk")

        primary_commands: tuple[DecodedCommand, ...] = ()
        primary_failure = None
        try:
            primary_commands = self._decode(
                self.primary,
                chunk,
                context,
            )
        except Exception as exc:
            primary_failure = (exc, exc.__traceback__)
            self._record_diagnostic(
                "decoder_error",
                self._error_payload("primary", self.primary.decoder_id, chunk, exc),
            )

        primary_event_ids = tuple(
            self._command_event_id(chunk, "primary", ordinal)
            for ordinal in range(len(primary_commands))
        )
        for command, command_event_id in zip(
            primary_commands,
            primary_event_ids,
        ):
            self._record_command(
                command,
                "primary",
                chunk,
                context,
                command_event_id,
            )

        shadow_commands: tuple[DecodedCommand, ...] = ()
        shadow_error = None
        diagnostic_errors = []
        if self.shadow is not None:
            try:
                shadow_commands = self._decode(
                    self.shadow,
                    chunk,
                    context,
                )
            except Exception as exc:
                shadow_error = str(exc)
                diagnostic_error = self._record_diagnostic(
                    "decoder_error",
                    self._error_payload("shadow", self.shadow.decoder_id, chunk, exc),
                )
                if diagnostic_error is not None:
                    diagnostic_errors.append(
                        PipelineDiagnosticError(
                            stage="shadow_decoder_diagnostic",
                            reason=f"shadow {diagnostic_error}",
                        )
                    )

        shadow_event_ids = tuple(
            self._command_event_id(chunk, "shadow", ordinal)
            for ordinal in range(len(shadow_commands))
        )

        if primary_failure is not None:
            for command, command_event_id in zip(
                shadow_commands,
                shadow_event_ids,
            ):
                self._record_diagnostic(
                    "command",
                    self._command_payload(
                        command,
                        "shadow",
                        chunk,
                        context,
                        command_event_id,
                    ),
                )
            error, traceback = primary_failure
            raise error.with_traceback(traceback)

        for command, command_event_id in zip(
            shadow_commands,
            shadow_event_ids,
        ):
            diagnostic_error = self._record_diagnostic(
                "command",
                self._command_payload(
                    command,
                    "shadow",
                    chunk,
                    context,
                    command_event_id,
                ),
            )
            if diagnostic_error is not None:
                diagnostic_errors.append(
                    PipelineDiagnosticError(
                        stage="shadow_command_recording",
                        reason=f"shadow {diagnostic_error}",
                    )
                )

        validation_results = []
        for command, command_event_id in zip(
            primary_commands,
            primary_event_ids,
        ):
            validation_recorded = False

            def record_validation(
                validation,
                item=command,
                event_id=command_event_id,
            ):
                nonlocal validation_recorded
                self._record_validation(
                    item,
                    validation,
                    chunk,
                    context,
                    event_id,
                )
                validation_recorded = True

            try:
                result = self.output.publish(
                    command,
                    before_commit=record_validation,
                )
            except Exception as exc:
                self._record_diagnostic(
                    "output_error",
                    {
                        "chunk_id": chunk.chunk_id,
                        "decoder_id": command.decoder_id,
                        "error": str(exc),
                    },
                )
                raise
            if not isinstance(result, ValidationResult):
                raise TypeError("output publish must return a ValidationResult")
            if not validation_recorded:
                if result.accepted:
                    raise ReceiverPipelineError(
                        "accepted output skipped validation prepare callback"
                    )
                record_validation(result)
            validation_results.append(result)

        return ReceiverPipelineResult(
            primary_commands=primary_commands,
            shadow_commands=shadow_commands,
            validation_results=tuple(validation_results),
            shadow_error=shadow_error,
            diagnostic_errors=tuple(diagnostic_errors),
        )

    @staticmethod
    def _decode(decoder, chunk, context) -> tuple[DecodedCommand, ...]:
        commands = decoder.decode(chunk, context)
        if not isinstance(commands, list):
            raise TypeError("decoder decode must return a list")
        if not all(isinstance(command, DecodedCommand) for command in commands):
            raise TypeError("decoder results must be DecodedCommand instances")
        if not all(command.decoder_id == decoder.decoder_id for command in commands):
            raise ValueError("decoded command decoder_id must match its decoder")
        for command in commands:
            ReceiverPipeline._validate_command_correlation(
                command,
                chunk,
                context,
            )
        return tuple(commands)

    @staticmethod
    def _validate_command_correlation(command, chunk, context) -> None:
        if command.context_version != context.context_version:
            raise ValueError("decoded command context_version does not match context")
        chunk_last_sample = chunk.first_sample_index + int(chunk.samples.size) - 1
        if (
            command.first_sample_index < chunk.first_sample_index
            or command.first_sample_index > command.last_sample_index
            or command.last_sample_index > chunk_last_sample
        ):
            raise ValueError("decoded command sample range is not correlated to chunk")
        for name in ("target", "team", "profile"):
            if getattr(command, name) != getattr(context, name):
                raise ValueError(f"decoded command {name} does not match context")

    @staticmethod
    def _error_payload(role, decoder_id, chunk, exc):
        return {
            "chunk_id": chunk.chunk_id,
            "decoder_id": decoder_id,
            "error": str(exc),
            "role": role,
        }

    @staticmethod
    def _command_event_id(chunk, role, ordinal) -> str:
        return f"{chunk.chunk_id}:{role}:{ordinal}"

    def _record_command(
        self,
        command,
        role,
        chunk,
        context,
        command_event_id,
    ) -> None:
        self._record_event(
            "command",
            self._command_payload(
                command,
                role,
                chunk,
                context,
                command_event_id,
            ),
        )

    @staticmethod
    def _command_payload(
        command,
        role,
        chunk,
        context,
        command_event_id,
    ):
        chunk_last_sample = chunk.first_sample_index + int(chunk.samples.size) - 1
        return {
            "command_event_id": command_event_id,
            "role": role,
            "chunk_id": chunk.chunk_id,
            "chunk_first_sample_index": chunk.first_sample_index,
            "chunk_last_sample_index": chunk_last_sample,
            "target_version": chunk.target_version,
            "context_version": context.context_version,
            "target": context.target,
            "team": context.team,
            "profile": context.profile,
            "decoder_id": command.decoder_id,
            "cmd_id": command.cmd_id,
            "payload": command.payload,
            "crc8_ok": command.crc8_ok,
            "crc16_ok": command.crc16_ok,
            "crc_mode": command.crc_mode,
            "receive_wall_time": command.receive_wall_time,
            "first_sample_index": command.first_sample_index,
            "last_sample_index": command.last_sample_index,
            "evidence": command.evidence,
        }

    def _record_validation(
        self,
        command,
        validation,
        chunk,
        context,
        command_event_id,
    ) -> None:
        chunk_last_sample = chunk.first_sample_index + int(chunk.samples.size) - 1
        self._record_event(
            "validation",
            {
                "command_event_id": command_event_id,
                "chunk_id": chunk.chunk_id,
                "chunk_first_sample_index": chunk.first_sample_index,
                "chunk_last_sample_index": chunk_last_sample,
                "target_version": chunk.target_version,
                "context_version": context.context_version,
                "target": context.target,
                "team": context.team,
                "profile": context.profile,
                "decoder_id": command.decoder_id,
                "cmd_id": command.cmd_id,
                "payload": command.payload,
                "crc8_ok": command.crc8_ok,
                "crc16_ok": command.crc16_ok,
                "crc_mode": command.crc_mode,
                "receive_wall_time": command.receive_wall_time,
                "command_first_sample_index": command.first_sample_index,
                "command_last_sample_index": command.last_sample_index,
                "accepted": validation.accepted,
                "reason": validation.reason,
                "ascii_code": validation.ascii_code,
                "level": validation.level,
            },
        )

    def _record_event(self, kind, payload) -> None:
        if self.recorder is not None and self.recorder.write_event(kind, payload) is not True:
            raise ReceiverPipelineError(f"failed to record {kind} event")

    def _record_diagnostic(self, kind, payload) -> Optional[str]:
        if self.recorder is None:
            return None
        try:
            accepted = self.recorder.write_event(kind, payload)
        except Exception as exc:
            # Preserve the primary stage failure; diagnostics are best effort here.
            return f"{kind} event failed: {exc}"
        if accepted is not True:
            return f"{kind} event was rejected"
        return None


@dataclass(frozen=True)
class _PendingRfTransition:
    target: str
    reason: str
    team: Optional[str]


class IqRecorder:
    """Compatibility adapter from legacy raw arrays to structured recording."""

    def __init__(
        self,
        *,
        record_dir: str,
        prefix: str,
        max_sec: float,
        max_bytes: int,
        every_n: int,
        metadata_provider,
        prefix_provider=None,
        chunk_metadata_provider=None,
        record_queue_size: int = 32,
        adc_code_scale: float = 2048.0,
    ) -> None:
        self.record_dir = Path(os.path.expandvars(str(record_dir))).expanduser()
        self.prefix = self._sanitize_prefix(prefix or "sdr_iq")
        self.prefix_provider = prefix_provider
        self.max_sec = float(max_sec)
        self.max_bytes = int(max_bytes)
        self.every_n = max(1, int(every_n))
        self.metadata_provider = metadata_provider
        self.record_queue_size = int(record_queue_size)
        self.adc_code_scale = float(adc_code_scale)
        self.chunk_metadata_provider = (
            chunk_metadata_provider if chunk_metadata_provider is not None else lambda: {}
        )
        self.lock = threading.RLock()
        self._close_lock = threading.Lock()
        self.path: Optional[Path] = None
        self.meta_path: Optional[Path] = None
        self._recorder: Optional[StructuredRecorder] = None
        self.start_wall = 0.0
        self.last_wall = 0.0
        self.chunks_seen = 0
        self._next_chunk_id = 0
        self._next_sample_index = 0
        self._accepted_bytes = 0
        self._last_target: Optional[str] = None
        self._target_version = 0
        self._cached_sample_rate_hz: Optional[int] = None
        self._finalizer_thread: Optional[threading.Thread] = None
        self._finalizer_error: Optional[BaseException] = None
        self._closed = False
        self.last_peak = 0.0
        self.last_rms = 0.0
        self.stopped_reason = ""

    def write(self, raw_iq: np.ndarray) -> None:
        with self.lock:
            if self._closed:
                raise RuntimeError("IQ recorder is closed")
            if self.stopped_reason:
                return
            self.chunks_seen += 1
            raw_view = np.asarray(raw_iq)
            sample_count = int(raw_view.size)
            chunk_id = self._next_chunk_id
            first_sample_index = self._next_sample_index
            self._next_chunk_id += 1
            self._next_sample_index += sample_count
            if (self.chunks_seen - 1) % self.every_n != 0:
                return
            if sample_count == 0:
                return
            now = time.time()
            monotonic_ns = time.monotonic_ns()
            if self._recorder is None:
                self._create_recorder(now)
            if self.start_wall and self.max_sec > 0.0 and now - self.start_wall >= self.max_sec:
                self._stop_accepting(f"max_sec {self.max_sec:.1f} reached")
                return
            next_bytes = sample_count * np.dtype(np.complex64).itemsize
            if self.max_bytes > 0 and self._accepted_bytes + next_bytes > self.max_bytes:
                self._stop_accepting(f"max_bytes {self.max_bytes} reached")
                return

            arr = np.asarray(raw_iq, dtype=np.complex64).reshape(-1).copy(order="C")
            arr.setflags(write=False)
            metadata = self._chunk_metadata_snapshot()
            sample_rate_hz = self._metadata_int(
                metadata, "sample_rate_hz", "sample_rate", default=0
            )
            if sample_rate_hz:
                self._cached_sample_rate_hz = sample_rate_hz
            elif self._cached_sample_rate_hz is not None:
                sample_rate_hz = self._cached_sample_rate_hz
            target = str(metadata.get("target") or "UNKNOWN")
            if target != self._last_target:
                self._target_version += 1
                self._last_target = target
            chunk = IqChunk(
                chunk_id=chunk_id,
                first_sample_index=first_sample_index,
                samples=arr,
                sample_rate_hz=sample_rate_hz,
                rx_wall_time=now,
                rx_monotonic_ns=monotonic_ns,
                lo_hz=self._metadata_int(
                    metadata, "lo_hz", "rx_lo_hz", default=0
                ),
                rf_bandwidth_hz=self._metadata_int(
                    metadata, "rf_bandwidth_hz", default=0
                ),
                rx_gain_db=self._metadata_int(
                    metadata, "rx_gain_db", "rx_gain", default=0
                ),
                target_version=self._target_version,
                context_version=self._metadata_int(
                    metadata, "context_version", default=0
                ),
                rf_metrics=None,
            )
            accepted = self._recorder.write_chunk(
                chunk,
                metadata={**metadata, "target": target},
            )
            if accepted:
                self._accepted_bytes += next_bytes
            self.last_wall = now

    def close(self) -> None:
        with self._close_lock:
            with self.lock:
                if self._closed:
                    error = self._finalizer_error
                    if error is not None:
                        raise error
                    return
                self._closed = True
                recorder = self._recorder
                finalizer = self._finalizer_thread
                stopped_reason = self.stopped_reason or "closed"
            if finalizer is not None:
                finalizer.join()
            elif recorder is not None:
                try:
                    recorder.close(stopped_reason=stopped_reason)
                except BaseException as exc:
                    with self.lock:
                        self._finalizer_error = exc
                    raise
            with self.lock:
                if self._finalizer_error is not None:
                    raise self._finalizer_error

    def status(self) -> dict:
        with self.lock:
            stats = None if self._recorder is None else self._recorder.stats
            latest_rf_metrics = (
                None if stats is None else getattr(stats, "latest_rf_metrics", None)
            )
            return {
                "enabled": True,
                "path": None if self.path is None else str(self.path),
                "metadata_path": None if self.meta_path is None else str(self.meta_path),
                "chunks_seen": self.chunks_seen,
                "chunks_written": 0 if stats is None else stats.chunks_written,
                "samples_written": 0 if stats is None else stats.samples_written,
                "bytes_written": 0 if stats is None else stats.bytes_written,
                "dropped_chunks": 0 if stats is None else stats.dropped_chunks,
                "dropped_events": 0 if stats is None else stats.dropped_events,
                "worker_error": None if stats is None else stats.worker_error,
                "finalizer_error": None
                if self._finalizer_error is None
                else str(self._finalizer_error),
                "last_peak": (
                    self.last_peak
                    if latest_rf_metrics is None
                    else latest_rf_metrics.peak
                ),
                "last_rms": (
                    self.last_rms
                    if latest_rf_metrics is None
                    else latest_rf_metrics.rms
                ),
                "stopped_reason": self.stopped_reason,
            }

    def _create_recorder(self, now: float) -> None:
        stamp = time.strftime("%Y%m%d_%H%M%S", time.localtime(now))
        prefix = self.prefix
        if self.prefix_provider is not None:
            try:
                prefix = self._sanitize_prefix(self.prefix_provider() or self.prefix)
            except Exception:
                prefix = self.prefix
        recording_prefix = (
            f"{prefix}_{stamp}_{time.time_ns()}_{os.getpid()}_{id(self):x}"
        )
        self._recorder = StructuredRecorder(
            self.record_dir,
            recording_prefix,
            queue_size=self.record_queue_size,
            summary_metadata={
                "every_n": self.every_n,
                "max_sec": self.max_sec,
                "max_bytes": self.max_bytes,
                "adc_code_scale": self.adc_code_scale,
            },
            summary_metadata_provider=self.metadata_provider,
        )
        self.path = self._recorder.iq_path
        self.meta_path = self._recorder.summary_path
        self.start_wall = now
        self.last_wall = now

    @staticmethod
    def _sanitize_prefix(prefix: str) -> str:
        return "".join(ch if ch.isalnum() or ch in ("-", "_") else "_" for ch in str(prefix or "sdr_iq"))

    def _chunk_metadata_snapshot(self) -> dict:
        try:
            return dict(self.chunk_metadata_provider() or {})
        except Exception as exc:
            return {"metadata_error": str(exc)}

    @staticmethod
    def _metadata_int(metadata: dict, *keys: str, default: int) -> int:
        radio = metadata.get("radio")
        for key in keys:
            value = metadata.get(key)
            if value is None and isinstance(radio, dict):
                value = radio.get(key)
            if value is not None:
                try:
                    return int(value)
                except (TypeError, ValueError, OverflowError):
                    continue
        return default

    def _stop_accepting(self, reason: str) -> None:
        self.stopped_reason = reason
        if self._recorder is not None:
            self._recorder.write_event("recording_stopped", {"reason": reason})
            self._finalizer_thread = threading.Thread(
                target=self._finalize,
                args=(self._recorder, reason),
                name="iq-recorder-finalizer",
                daemon=True,
            )
            self._finalizer_thread.start()

    def _finalize(self, recorder: StructuredRecorder, reason: str) -> None:
        try:
            recorder.close(stopped_reason=reason)
        except BaseException as exc:
            with self.lock:
                self._finalizer_error = exc


def _radar_info_to_level(raw: int) -> int:
    return (int(raw) >> 3) & 0x03


def _radar_info_to_key_mutable(raw: int) -> bool:
    return ((int(raw) >> 5) & 0x01) != 0


class SdrReceiverPyWrapperNode(Node):
    def __init__(self) -> None:
        super().__init__("sdr_receiver_py_wrapper")

        self._declare_parameters()
        self.run_mode = str(self.get_parameter("run_mode").value).lower()
        if self.run_mode not in ("debug", "competition"):
            raise ValueError("run_mode must be 'debug' or 'competition'")
        self.foundation_config = self._load_receiver_foundation_config()
        _validate_decoder_registry(self.foundation_config)

        self.publish_ros_outputs = bool(self.get_parameter("publish_ros_outputs").value)
        self.debug_accept_ros_control = bool(self.get_parameter("debug_accept_ros_control").value)
        self.start_receiver = bool(self.get_parameter("start_receiver").value)
        self.import_allow_adi_stub = bool(self.get_parameter("import_allow_adi_stub").value)
        self.iq_source_path = str(self.get_parameter("iq_source_path").value).strip()
        self.context_authority_topic, used_legacy_context_topic = (
            resolve_context_authority(
                self.get_parameter("context_authority_topic").value,
                self.get_parameter("context_topic").value,
            )
        )
        if used_legacy_context_topic:
            self.get_logger().warn(
                "context_topic is deprecated; use context_authority_topic"
            )
        # Backward-compatible attribute only; exactly one authority subscription is created.
        self.context_topic = self.context_authority_topic
        self.enable_fallback_topics = bool(self.get_parameter("enable_fallback_topics").value)
        self.fallback_self_id = int(self.get_parameter("fallback_self_id").value)
        self.context_arbiter = ContextArbiter(
            self.context_authority_topic,
            stable_count=int(self.get_parameter("context_stable_count").value),
            stable_sec=float(self.get_parameter("context_stable_sec").value),
            lock_team_after_start=bool(
                self.get_parameter("lock_team_after_start").value
            ),
        )
        self.profile_config = self._load_profile_config(str(self.get_parameter("profile_path").value))
        self.iq_recorder = (
            self._create_iq_recorder() if self.run_mode == "debug" else None
        )
        self.common_runtime: Optional[CommonReceiverRuntime] = None
        self._common_shadow_adapter = None
        self._common_target_key = None
        self._common_target_version = 0
        self.latest_context: Optional[RadarContext] = None
        self._fallback_msg_self_id = 0
        self._fallback_self_color = -1
        self._fallback_game_progress = 0
        self._fallback_match_time = 0
        self._fallback_radar_info_raw = 0
        self._fallback_jam_level = None
        self._fallback_key_mutable = None
        self._fallback_referee_online = None

        self._controller_lock = threading.RLock()
        self._pending_rf_transition: Optional[_PendingRfTransition] = None
        self.publication_indeterminate: Optional[str] = None
        self.controller = CompetitionController(
            max_jam_break_level=int(self.get_parameter("max_jam_break_level").value),
            key_publish_min_interval_sec=float(
                self.get_parameter("key_publish_min_interval_sec").value
            ),
            key_retry_limit=int(self.get_parameter("key_retry_limit").value),
        )
        self.primary_decoder_id = self.foundation_config.decoder_primary
        self.command_validator = CommandValidator()

        qos = QoSProfile(
            history=HistoryPolicy.KEEP_LAST,
            depth=5,
            reliability=ReliabilityPolicy.RELIABLE,
        )
        self.jam_code_pub = self.create_publisher(JamCode, "/sdr/jam_code", qos)
        self.raw_frame_pub = self.create_publisher(
            RadarWirelessFrame, "/sdr/radar_wireless/raw_frame", qos
        )
        self.status_pub = self.create_publisher(String, "/sdr/status", 10)

        self.context_sub = self.create_subscription(
            RadarContextMsg,
            self.context_authority_topic,
            self._on_radar_context,
            qos,
        )
        self._fallback_match_sub = None
        self._fallback_radar_info_sub = None
        if self.enable_fallback_topics:
            self._setup_fallback_subscriptions(qos)

        callbacks = PatchCallbacks(
            on_jam_key=self._on_jam_key,
            on_raw_frame=self._on_raw_frame,
            on_target_change=self._on_target_change,
            on_raw_iq=self._on_raw_iq if self.iq_recorder is not None else None,
        )
        original_script_path = str(self.get_parameter("original_script_path").value)
        self.original_script_path = original_script_path
        self.adapter = ReceiverCoreAdapter(original_script_path, logger=self._log_from_patch)
        self.adapter.load(allow_adi_import_stub=self.import_allow_adi_stub or bool(self.iq_source_path))
        if self.iq_source_path and self.run_mode == "debug":
            self.adapter.configure_iq_file_source(
                path=self.iq_source_path,
                loop=bool(self.get_parameter("iq_source_loop").value),
                throttle=bool(self.get_parameter("iq_source_throttle").value),
                center_hz=float(self.get_parameter("iq_source_center_hz").value),
                start_offset_sec=float(self.get_parameter("iq_source_start_offset_sec").value),
                sample_rate_hz=int(self.get_parameter("iq_source_sample_rate").value),
            )
        self._apply_initial_receiver_settings()

        status_period = float(self.get_parameter("status_period_sec").value)
        self.status_timer = self.create_timer(status_period, self._publish_status)

        self._configure_receiver_runtime(callbacks)

        self.get_logger().info(
            "sdr_receiver_py_wrapper ready: "
            f"mode={self.run_mode} publish_ros_outputs={self.publish_ros_outputs} "
            f"context_authority_topic={self.context_authority_topic} "
            f"start_receiver={self.start_receiver}"
        )
        if self.run_mode == "debug" and not sys.stdin.isatty():
            self.get_logger().warn(
                "debug keyboard is not connected to a TTY. "
                "ros2 launch usually does not forward stdin; use ros2 run/direct executable "
                "for interactive keyboard control, or set initial_team/initial_target/initial_rx_gain."
            )

    def destroy_node(self) -> bool:
        errors = []
        common_runtime = getattr(self, "common_runtime", None)
        if common_runtime is not None:
            # Any runtime cleanup failure leaves core/device ownership live.
            # Defer every downstream teardown phase until a retry succeeds.
            common_runtime.close()
        adapter = getattr(self, "adapter", None)
        if adapter is not None and getattr(self, "run_mode", "debug") == "debug":
            try:
                adapter.stop()
            except BaseException as exc:
                errors.append(("adapter stop", exc))
        if adapter is not None:
            try:
                adapter.restore_patches()
            except BaseException as exc:
                errors.append(("adapter restore", exc))
        iq_recorder = getattr(self, "iq_recorder", None)
        if iq_recorder is not None:
            try:
                iq_recorder.close()
            except BaseException as exc:
                errors.append(("IQ recorder", exc))
        result = False
        try:
            result = super().destroy_node()
        except BaseException as exc:
            errors.append(("ROS node", exc))
        if errors:
            if len(errors) == 1:
                raise errors[0][1]
            raise RuntimeError(
                "; ".join(f"{phase}: {exc}" for phase, exc in errors)
            )
        return result

    def _declare_parameters(self) -> None:
        self.declare_parameter("run_mode", "debug")
        self.declare_parameter("original_script_path", DEFAULT_ORIGINAL_SCRIPT)
        self.declare_parameter("publish_ros_outputs", True)
        self.declare_parameter("debug_accept_ros_control", False)
        self.declare_parameter("start_receiver", True)
        self.declare_parameter("import_allow_adi_stub", False)
        self.declare_parameter("iq_source_path", "")
        self.declare_parameter("iq_source_loop", True)
        self.declare_parameter("iq_source_throttle", True)
        self.declare_parameter("iq_source_center_hz", 0.0)
        self.declare_parameter("iq_source_start_offset_sec", 0.0)
        self.declare_parameter("iq_source_sample_rate", 0)
        self.declare_parameter("context_authority_topic", "")
        self.declare_parameter("context_stable_count", 3)
        self.declare_parameter("context_stable_sec", 1.0)
        self.declare_parameter("lock_team_after_start", True)
        self.declare_parameter("context_topic", "")
        self.declare_parameter("enable_fallback_topics", True)
        self.declare_parameter("fallback_self_id", 0)
        self.declare_parameter("max_jam_break_level", 3)
        self.declare_parameter("key_publish_min_interval_sec", 0.5)
        self.declare_parameter("key_retry_limit", -1)
        self.declare_parameter("status_period_sec", 1.0)
        self.declare_parameter("enable_micro_tune", False)
        self.declare_parameter("micro_tune_max_hz", 0.0)
        self.declare_parameter("micro_tune_step_hz", 0.0)
        self.declare_parameter("micro_tune_timeout_sec", 0.0)
        self.declare_parameter("profile_path", "")
        self.declare_parameter("match_slot", "bo3_game1")
        self.declare_parameter("front_end_id", "front_end_A")
        self.declare_parameter("sdr_uri", "ip:192.168.2.1")
        self.declare_parameter("decoder_primary", "improved_v67")
        self.declare_parameter("decoder_shadow", "")
        self.declare_parameter("acquisition_queue_size", 8)
        self.declare_parameter("record_queue_size", 32)
        self.declare_parameter("adc_code_scale", 2048.0)
        self.declare_parameter("rf_clipping_ratio", 0.001)
        self.declare_parameter("initial_team", "")
        self.declare_parameter("initial_target", "")
        self.declare_parameter("initial_rx_gain", 20)
        self.declare_parameter("initial_rf_bw_hz", 0)
        self.declare_parameter("initial_freq_offset_hz", 0)
        self.declare_parameter("initial_info_filter", "")
        self.declare_parameter("initial_info_l2_rescue", False)
        self.declare_parameter("initial_info_l3_rescue", False)
        self.declare_parameter("record_iq", False)
        self.declare_parameter("iq_record_dir", str(Path.home() / "sdr_iq_records"))
        self.declare_parameter("iq_record_prefix", "sdr_capture")
        self.declare_parameter("iq_record_max_sec", 0.0)
        self.declare_parameter("iq_record_max_bytes", 0)
        self.declare_parameter("iq_record_every_n", 1)

    def _load_receiver_foundation_config(self) -> ReceiverFoundationConfig:
        return ReceiverFoundationConfig(
            sdr_uri=self.get_parameter("sdr_uri").value,
            decoder_primary=self.get_parameter("decoder_primary").value,
            decoder_shadow=self.get_parameter("decoder_shadow").value,
            acquisition_queue_size=self.get_parameter("acquisition_queue_size").value,
            record_queue_size=self.get_parameter("record_queue_size").value,
            adc_code_scale=self.get_parameter("adc_code_scale").value,
            rf_clipping_ratio=self.get_parameter("rf_clipping_ratio").value,
            initial_rx_gain=self.get_parameter("initial_rx_gain").value,
        )

    def create_receiver_pipeline(self, *, primary, shadow=None, recorder=None):
        """Compose injected plugins without changing the legacy hardware loop."""

        if getattr(primary, "decoder_id", None) != self.foundation_config.decoder_primary:
            raise ValueError("primary decoder does not match decoder_primary")
        expected_shadow = self.foundation_config.decoder_shadow
        actual_shadow = "" if shadow is None else getattr(shadow, "decoder_id", None)
        if actual_shadow != expected_shadow:
            raise ValueError("shadow decoder does not match decoder_shadow")
        return ReceiverPipeline(
            primary=primary,
            shadow=shadow,
            output=NodeCommandOutput(self),
            recorder=recorder,
            config=self.foundation_config,
        )

    def _start_receiver_runtime(self, callbacks) -> None:
        if self.run_mode == "competition":
            if self.start_receiver:
                if self.common_runtime is None:
                    raise RuntimeError("competition common runtime is not configured")
                self.common_runtime.start()
            return
        self.adapter.apply_patches(run_mode=self.run_mode, callbacks=callbacks)
        if self.start_receiver:
            self.adapter.start()

    def _configure_receiver_runtime(self, callbacks) -> None:
        if self.run_mode == "competition" and self.start_receiver:
            self.common_runtime = self._build_common_runtime()
        self._start_receiver_runtime(callbacks)

    def _build_common_runtime(self) -> CommonReceiverRuntime:
        if self.foundation_config.decoder_shadow:
            _require_decoder_available(self.foundation_config.decoder_shadow)
        primary = _create_decoder_plugin(
            self.foundation_config.decoder_primary,
            self.adapter,
        )
        shadow = None
        if self.foundation_config.decoder_shadow:
            self._common_shadow_adapter = ReceiverCoreAdapter(
                self.original_script_path,
                logger=self._log_from_patch,
            )
            self._common_shadow_adapter.load(
                allow_adi_import_stub=(
                    self.import_allow_adi_stub or bool(self.iq_source_path)
                )
            )
            shadow = _create_decoder_plugin(
                self.foundation_config.decoder_shadow,
                self._common_shadow_adapter,
            )
        return CommonReceiverRuntime(
            backend_factory=self._common_backend_factory(),
            config=self.foundation_config,
            primary=primary,
            shadow=shadow,
            output=NodeCommandOutput(self),
            recorder=self._create_common_recorder(),
            context_provider=self._common_decode_context,
            radio_settings_provider=self._common_radio_settings,
            snapshot_provider=self._common_runtime_snapshot,
        )

    def _common_backend_factory(self):
        rx_buffer_size = int(
            self.adapter.get_core_config_snapshot().get("rx_buffer_size", 0) or 0
        )
        if self.iq_source_path:
            def create_file_backend():
                backend = IqFilePluto(
                    self.iq_source_path,
                    loop=bool(self.get_parameter("iq_source_loop").value),
                    throttle=bool(self.get_parameter("iq_source_throttle").value),
                    center_hz=float(
                        self.get_parameter("iq_source_center_hz").value
                    ),
                    start_offset_sec=float(
                        self.get_parameter("iq_source_start_offset_sec").value
                    ),
                    logger=self._log_from_patch,
                )
                if rx_buffer_size > 0:
                    backend.rx_buffer_size = rx_buffer_size
                return backend

            return create_file_backend

        module = self.adapter.module
        pluto_factory = getattr(getattr(module, "adi", None), "Pluto", None)
        if not callable(pluto_factory):
            raise RuntimeError("receiver core has no callable adi.Pluto backend")

        def create_hardware_backend():
            backend = pluto_factory(self.foundation_config.sdr_uri)
            if rx_buffer_size > 0:
                backend.rx_buffer_size = rx_buffer_size
            return backend

        return create_hardware_backend

    def _common_radio_settings(self) -> dict:
        config = self.adapter.get_core_config_snapshot()
        radio = self.adapter.get_current_radio_snapshot()
        source_rate = int(self.get_parameter("iq_source_sample_rate").value)
        return {
            "sample_rate_hz": source_rate or int(config.get("sample_rate", 0) or 0),
            "lo_hz": int(radio.get("rx_lo_hz", 0) or 0),
            "rf_bandwidth_hz": int(radio.get("rf_bandwidth_hz", 0) or 0),
            "rx_gain_db": int(
                radio.get("rx_gain", self.foundation_config.initial_rx_gain)
            ),
        }

    def _common_decode_context(self) -> DecodeContext:
        with self._get_controller_lock():
            radio = self.adapter.get_current_radio_snapshot()
            team = str(radio.get("team") or "RED").upper()
            target = str(radio.get("target") or "INFO").upper()
            target_key = (team, target)
            if target_key != self._common_target_key:
                self._common_target_version += 1
                self._common_target_key = target_key
            return DecodeContext(
                team=team,
                target=target,
                profile=self.run_mode,
                target_version=self._common_target_version,
                context_version=int(
                    getattr(self.context_arbiter, "context_version", 0)
                ),
            )

    def _common_runtime_snapshot(self):
        with self._get_controller_lock():
            return self._common_decode_context(), self._common_radio_settings()

    def _create_common_recorder(self):
        if not bool(self.get_parameter("record_iq").value):
            return None
        stamp = f"{time.time_ns()}_{os.getpid()}"
        prefix = f"{self._iq_record_prefix()}_common_{stamp}"
        return self.foundation_config.create_recorder(
            str(self.get_parameter("iq_record_dir").value),
            prefix,
            summary_metadata={
                "runtime": "common_competition",
                "decoder_primary": self.foundation_config.decoder_primary,
                "decoder_shadow": self.foundation_config.decoder_shadow,
                "adc_code_scale": self.foundation_config.adc_code_scale,
                "rf_clipping_ratio": self.foundation_config.rf_clipping_ratio,
            },
            summary_metadata_provider=self._iq_record_metadata,
        )

    def _common_runtime_status(self) -> dict:
        common_runtime = getattr(self, "common_runtime", None)
        if common_runtime is None:
            return {"enabled": False}
        return common_runtime.status()

    def _setup_fallback_subscriptions(self, qos: QoSProfile) -> None:
        match_info_type = self._resolve_match_info_type()
        if match_info_type is not None:
            self._fallback_match_sub = self.create_subscription(
                match_info_type,
                "/match_info",
                self._on_match_info,
                qos,
            )
            self.get_logger().info("fallback /match_info subscription enabled")
        else:
            self.get_logger().warn(
                "vision_interface.msg.MatchInfo is not importable; "
                "fallback self_id requires /judge/radar_context or fallback_self_id"
            )

        self._fallback_radar_info_sub = self.create_subscription(
            UInt8,
            "/judge/radar_info",
            self._on_radar_info,
            qos,
        )
        self.get_logger().info("fallback /judge/radar_info subscription enabled")

    def _load_profile_config(self, raw_path: str) -> dict:
        try:
            profile = load_adaptive_profile(raw_path)
        except AdaptiveProfileLoadError as exc:
            self.get_logger().warn(f"profile_path ignored: {exc}")
            return {}

        if profile is None:
            return {}
        self.get_logger().info(f"loaded adaptive profile: {profile.get('source_path', raw_path)}")
        return profile

    def _create_iq_recorder(self) -> Optional[IqRecorder]:
        if not bool(self.get_parameter("record_iq").value):
            return None
        return IqRecorder(
            record_dir=str(self.get_parameter("iq_record_dir").value),
            prefix=str(self.get_parameter("iq_record_prefix").value),
            max_sec=float(self.get_parameter("iq_record_max_sec").value),
            max_bytes=int(self.get_parameter("iq_record_max_bytes").value),
            every_n=int(self.get_parameter("iq_record_every_n").value),
            metadata_provider=self._iq_record_metadata,
            prefix_provider=self._iq_record_prefix,
            chunk_metadata_provider=self._iq_chunk_metadata,
            record_queue_size=self.foundation_config.record_queue_size,
            adc_code_scale=self.foundation_config.adc_code_scale,
        )

    def _iq_chunk_metadata(self) -> dict:
        sample_rate_hz = getattr(self, "_iq_record_sample_rate_hz", None)
        if sample_rate_hz is None:
            config = self.adapter.get_core_config_snapshot()
            sample_rate_hz = int(config.get("sample_rate", 0) or 0)
            self._iq_record_sample_rate_hz = sample_rate_hz
        radio = self.adapter.get_current_radio_snapshot()
        return {
            "sample_rate_hz": sample_rate_hz,
            "lo_hz": radio.get("rx_lo_hz", 0),
            "rf_bandwidth_hz": radio.get("rf_bandwidth_hz", 0),
            "rx_gain_db": radio.get("rx_gain", 0),
            "target": radio.get("target") or "UNKNOWN",
            "adc_code_scale": self.foundation_config.adc_code_scale,
            "rf_clipping_ratio": self.foundation_config.rf_clipping_ratio,
            "context_version": int(
                getattr(self.context_arbiter, "context_version", 0)
            ),
        }

    def _iq_record_metadata(self) -> dict:
        status = self.adapter.get_stats_snapshot()
        config = self.adapter.get_core_config_snapshot()
        radio = self.adapter.get_current_radio_snapshot()
        own_team = self._current_own_team()
        rx_team = self._current_team()
        return {
            **config,
            "run_mode": self.run_mode,
            "own_team": own_team,
            "rx_team": rx_team,
            "team": rx_team,
            "core_team": status.get("team"),
            "target": status.get("target"),
            "context_version": int(
                getattr(self.context_arbiter, "context_version", 0)
            ),
            "radio": radio,
            "rx_lo_hz": radio.get("rx_lo_hz"),
            "rf_bandwidth_hz": radio.get("rf_bandwidth_hz"),
            "rx_gain": status.get("rx_gain"),
            "gain_ceiling": status.get("gain_ceiling"),
            "adc_rms": status.get("adc_rms"),
            "rf_state": status.get("rf_state"),
            "profile_path": str(self.get_parameter("profile_path").value),
            "profile": self.profile_config,
        }

    def _iq_record_prefix(self) -> str:
        base = str(self.get_parameter("iq_record_prefix").value or "sdr_iq").strip() or "sdr_iq"
        if self.run_mode != "competition":
            return base
        own_team = self._current_own_team()
        rx_team = self._current_team()
        if own_team in ("RED", "BLUE") and rx_team in ("RED", "BLUE"):
            return f"{base}_own_{own_team}_vs_{rx_team}"
        return f"{base}_AUTO"

    @staticmethod
    def _resolve_match_info_type():
        try:
            from vision_interface.msg import MatchInfo

            return MatchInfo
        except Exception:
            return None

    def _on_radar_context(self, msg: RadarContextMsg) -> None:
        observation = Observation(
            source=self.context_authority_topic,
            self_id=int(msg.self_id),
            self_color=int(msg.self_color),
            radar_info_raw=int(msg.radar_info_raw),
            jam_level=int(msg.jam_level),
            key_mutable=bool(msg.key_mutable),
            game_progress=int(msg.game_progress),
            match_time=int(msg.match_time),
            received_monotonic=time.monotonic(),
        )
        self._observe_context(observation, referee_online=bool(msg.referee_online))

    def _apply_initial_receiver_settings(self) -> None:
        team = str(self.get_parameter("initial_team").value).strip().upper()
        target = str(self.get_parameter("initial_target").value).strip().upper()
        gain = int(self.get_parameter("initial_rx_gain").value)
        rf_bw_hz = int(self.get_parameter("initial_rf_bw_hz").value)
        freq_offset_hz = int(self.get_parameter("initial_freq_offset_hz").value)
        info_filter = str(self.get_parameter("initial_info_filter").value).strip()
        info_l2_rescue = bool(self.get_parameter("initial_info_l2_rescue").value)
        info_l3_rescue = bool(self.get_parameter("initial_info_l3_rescue").value)

        if team:
            self.adapter.set_team(team)
            self.get_logger().info(f"initial receiver team set to {team}")
        offset_team = team or self.adapter.get_stats_snapshot().get("team") or "RED"
        offset_target = target or self.adapter.get_stats_snapshot().get("target") or "INFO"
        if freq_offset_hz:
            self.adapter.apply_frequency_offset(str(offset_team), str(offset_target), freq_offset_hz)
            self.get_logger().info(
                f"initial frequency offset set to {offset_team}-{offset_target} {freq_offset_hz} Hz"
            )
        if target:
            if target == "INFO" and (rf_bw_hz > 0 or info_filter or gain >= 0 or freq_offset_hz):
                rescue = "L2" if info_l2_rescue else ("L3" if info_l3_rescue else "")
                self.adapter.set_radio_profile(
                    team=str(offset_team),
                    target="INFO",
                    gain=gain if gain >= 0 else None,
                    rf_bw=rf_bw_hz if rf_bw_hz > 0 else None,
                    freq_offset_hz=freq_offset_hz,
                    rescue=rescue,
                    filter_name=info_filter,
                )
            else:
                self.adapter.set_target(
                    target,
                    info_l2_rescue=info_l2_rescue,
                    info_l3_rescue=info_l3_rescue,
                )
            self.get_logger().info(f"initial receiver target set to {target}")
        if gain >= 0:
            gain_target = target or self.adapter.get_stats_snapshot().get("target") or "INFO"
            self.adapter.set_manual_gain(str(gain_target), gain)
            self.get_logger().info(f"initial receiver gain set to {gain_target}={gain}")

    def _on_match_info(self, msg) -> None:
        self._fallback_msg_self_id = int(getattr(msg, "self_id", 0))
        self._fallback_self_color = int(getattr(msg, "self_color", -1))
        self._fallback_game_progress = int(getattr(msg, "game_progress", 0))
        self._fallback_match_time = int(getattr(msg, "match_time", 0))

        has_radar_context_fields = any(
            hasattr(msg, name)
            for name in ("self_id", "radar_info_raw", "jam_level", "key_mutable", "referee_online")
        )
        if has_radar_context_fields:
            raw = int(getattr(msg, "radar_info_raw", self._fallback_radar_info_raw)) & 0xFF
            jam_level = int(getattr(msg, "jam_level", _radar_info_to_level(raw)))
            key_mutable = bool(getattr(msg, "key_mutable", _radar_info_to_key_mutable(raw)))
            referee_online = bool(
                getattr(msg, "referee_online", self._fallback_match_time != -200)
            )
            self._fallback_radar_info_raw = raw
            self._fallback_jam_level = jam_level
            self._fallback_key_mutable = key_mutable
            self._fallback_referee_online = referee_online
            self._observe_context(
                Observation(
                    source="/match_info",
                    self_id=self._fallback_self_id(),
                    self_color=self._fallback_self_color,
                    radar_info_raw=raw,
                    jam_level=jam_level,
                    key_mutable=key_mutable,
                    game_progress=self._fallback_game_progress,
                    match_time=self._fallback_match_time,
                    received_monotonic=time.monotonic(),
                ),
                referee_online=referee_online,
            )
            return

        self._publish_fallback_context_if_ready(source="/match_info")

    def _on_radar_info(self, msg: UInt8) -> None:
        self._fallback_radar_info_raw = int(msg.data) & 0xFF
        self._fallback_jam_level = _radar_info_to_level(self._fallback_radar_info_raw)
        self._fallback_key_mutable = _radar_info_to_key_mutable(self._fallback_radar_info_raw)
        self._fallback_referee_online = True
        self._publish_fallback_context_if_ready(source="/judge/radar_info")

    def _publish_fallback_context_if_ready(self, *, source: str) -> None:
        self_id = self._fallback_self_id()
        raw = self._fallback_radar_info_raw
        jam_level, key_mutable, referee_online = resolve_diagnostic_values(
            radar_info_raw=raw,
            jam_level=self._fallback_jam_level,
            key_mutable=self._fallback_key_mutable,
            referee_online=self._fallback_referee_online,
            match_time=self._fallback_match_time,
        )
        observation = Observation(
            source=source,
            self_id=self_id,
            self_color=self._fallback_self_color,
            radar_info_raw=raw,
            jam_level=jam_level,
            key_mutable=key_mutable,
            game_progress=self._fallback_game_progress,
            match_time=self._fallback_match_time,
            received_monotonic=time.monotonic(),
        )
        self._observe_context(
            observation,
            referee_online=referee_online,
        )

    def _fallback_self_id(self) -> int:
        if self.fallback_self_id > 0:
            return self.fallback_self_id
        if self._fallback_msg_self_id > 0:
            return self._fallback_msg_self_id
        if self._fallback_self_color == 2:
            return 9
        if self._fallback_self_color == 0:
            return 109
        return 0

    def _observe_context(
        self, observation: Observation, *, referee_online: bool
    ) -> None:
        decision = self.context_arbiter.observe(observation)
        self.get_logger().info(format_context_decision_log(observation, decision))
        context = RadarContext(
            self_id=observation.self_id,
            self_color=observation.self_color,
            radar_info_raw=observation.radar_info_raw,
            jam_level=observation.jam_level,
            key_mutable=observation.key_mutable,
            game_progress=observation.game_progress,
            match_time=observation.match_time,
            referee_online=referee_online,
            source=observation.source,
        )
        with self._get_controller_lock():
            self._retry_pending_rf_transition_locked()
            if not decision.accepted:
                return

            self.latest_context = context
            if self.run_mode != "competition" and not self.debug_accept_ros_control:
                return

            controller_decision = self.controller.update_context(context)
            for warning in controller_decision.warnings:
                self.get_logger().warn(warning)
            if controller_decision.team:
                self.adapter.set_team(controller_decision.team)
                self.get_logger().info(
                    f"receiver RF team set to opponent {controller_decision.team} "
                    f"(own={self.controller.own_team}): {controller_decision.reason}"
                )
            if decision.accepted and decision.target_changed:
                target = resolve_receiver_target(
                    decision,
                    controller_decision.target,
                )
                if target:
                    self._set_receiver_target_or_profile(
                        target,
                        reason=controller_decision.reason,
                        team=controller_decision.team or self.controller.rx_team,
                    )

    def _on_jam_key(self, event: JamKeyEvent) -> None:
        if self.run_mode == "competition":
            self._handle_competition_decoded_command(
                self._decoded_command_from_legacy_event(event, event.level)
            )
            return

        if self.publish_ros_outputs:
            self._handle_decoded_command(
                self._decoded_command_from_legacy_event(event, event.level)
            )

    def _handle_competition_decoded_command(
        self,
        command: DecodedCommand,
        *,
        before_publish=None,
    ) -> ValidationResult:
        """Apply the controller transaction before the sole ROS command gate."""

        indeterminate = getattr(self, "publication_indeterminate", None)
        if indeterminate is not None:
            result = ValidationResult(
                False,
                "publication state is indeterminate; command output is quarantined: "
                + indeterminate,
            )
            if before_publish is not None:
                before_publish(result)
            self.get_logger().debug(result.reason)
            return result

        if command.decoder_id != self.primary_decoder_id:
            result = ValidationResult(
                False,
                f"decoder_id {command.decoder_id!r} is not primary decoder "
                f"{self.primary_decoder_id!r}",
            )
            if before_publish is not None:
                before_publish(result)
            self.get_logger().debug(result.reason)
            return result

        with self._get_controller_lock():
            prevalidation = self.command_validator.prevalidate(command)
            if not prevalidation.accepted:
                if before_publish is not None:
                    before_publish(prevalidation)
                self.get_logger().debug(prevalidation.reason)
                return prevalidation

            approved_target = f"L{prevalidation.level}"
            command_target = _normalize_competition_target(command.target)
            active_level = getattr(
                self.controller,
                "active_level",
                prevalidation.level,
            )
            desired_target = getattr(
                self.controller,
                "desired_target",
                approved_target,
            )
            controller_target = _normalize_competition_target(
                desired_target
            )
            pending_transition = getattr(
                self,
                "_pending_rf_transition",
                None,
            )
            pending_target = _normalize_competition_target(
                getattr(pending_transition, "target", None)
            )
            desired_target_authorized = (
                controller_target == approved_target
                or (
                    pending_transition is not None
                    and pending_target == controller_target
                )
            )
            if (
                active_level != prevalidation.level
                or command_target != approved_target
                or not desired_target_authorized
            ):
                result = ValidationResult(
                    False,
                    "decoded command target is not authorized by the active "
                    f"controller target: command={command.target!r}, "
                    f"active_level={active_level}, "
                    f"desired_target={desired_target!r}, "
                    f"evidence_level={prevalidation.level}",
                    ascii_code=prevalidation.ascii_code,
                    level=prevalidation.level,
                )
                self.get_logger().debug(result.reason)
                return result

            self._retry_pending_rf_transition_locked()
            controller_snapshot = self._snapshot_jam_key_controller_state()
            publication_committed = False
            publication_error = None
            try:
                decision = self.controller.handle_jam_key(
                    level=prevalidation.level,
                    key=command.payload,
                )
                for warning in decision.warnings:
                    self.get_logger().warn(warning)

                if decision.publish:
                    if not self.publish_ros_outputs:
                        self._restore_jam_key_controller_state(controller_snapshot)
                        result = ValidationResult(
                            False,
                            "ROS output disabled; controller key decision aborted",
                            ascii_code=prevalidation.ascii_code,
                            level=prevalidation.level,
                        )
                        if before_publish is not None:
                            before_publish(result)
                        self.get_logger().debug(result.reason)
                        return result
                    approved_command = command
                    if decision.level != prevalidation.level:
                        approved_command = replace(
                            command,
                            target=f"JAM_L{decision.level}_KEY",
                            evidence={
                                **dict(command.evidence),
                                "level": decision.level,
                            },
                        )
                    if before_publish is None:
                        result = self._handle_controller_decoded_command(
                            approved_command
                        )
                    else:
                        result = self._handle_controller_decoded_command(
                            approved_command,
                            before_publish=before_publish,
                        )
                    if not result.accepted:
                        self._restore_jam_key_controller_state(controller_snapshot)
                        self.get_logger().debug(result.reason)
                        return result
                    publication_committed = True
                else:
                    result = ValidationResult(
                        False,
                        decision.reason or "competition controller rejected command",
                        ascii_code=prevalidation.ascii_code,
                        level=prevalidation.level,
                    )
                    if before_publish is not None:
                        before_publish(result)
            except PublicationIndeterminateError as exc:
                # ROS publication cannot be rolled back.  Keep the controller
                # mutation, complete its RF transition, then fail-stop output.
                publication_committed = True
                publication_error = exc
                result = prevalidation
            except Exception:
                if not publication_committed:
                    self._restore_jam_key_controller_state(controller_snapshot)
                raise

            if decision.target:
                transition = _PendingRfTransition(
                    target=decision.target,
                    reason=decision.reason,
                    team=self.controller.rx_team,
                )
                try:
                    self._set_receiver_target_or_profile(
                        transition.target,
                        reason=transition.reason,
                        team=transition.team,
                    )
                except Exception:
                    if publication_committed:
                        self._pending_rf_transition = transition
                    raise
                self._pending_rf_transition = None
            elif decision.reason:
                self.get_logger().debug(decision.reason)
            if publication_error is not None:
                raise publication_error
            return result

    def _get_controller_lock(self):
        lock = getattr(self, "_controller_lock", None)
        if lock is None:
            lock = self.__dict__.setdefault(
                "_controller_lock",
                threading.RLock(),
            )
        return lock

    def _retry_pending_rf_transition_locked(self) -> bool:
        pending = getattr(self, "_pending_rf_transition", None)
        if pending is None:
            return True
        try:
            self._set_receiver_target_or_profile(
                pending.target,
                reason=pending.reason,
                team=pending.team,
            )
        except Exception:
            return False
        if self._pending_rf_transition is pending:
            self._pending_rf_transition = None
        return True

    def _snapshot_jam_key_controller_state(self) -> dict[str, object]:
        """Snapshot exactly the fields mutated by ``handle_jam_key``."""

        fields = (
            "state",
            "completed_level",
            "desired_target",
            "published_keys",
        )
        return {
            name: copy.deepcopy(getattr(self.controller, name))
            for name in fields
            if hasattr(self.controller, name)
        }

    def _restore_jam_key_controller_state(
        self,
        snapshot: dict[str, object],
    ) -> None:
        for name, value in snapshot.items():
            setattr(self.controller, name, value)

    def _decoded_command_from_legacy_event(
        self,
        event: JamKeyEvent,
        level: int,
    ) -> DecodedCommand:
        """Bridge the legacy callback into the common immutable contract."""

        context_arbiter = getattr(self, "context_arbiter", None)
        return DecodedCommand(
            cmd_id=event.cmd_id,
            payload=bytes(event.payload),
            decoder_id=self.primary_decoder_id,
            profile=self.run_mode,
            crc8_ok=True,
            crc16_ok=True,
            crc_mode="legacy_v67_validated",
            first_sample_index=0,
            last_sample_index=0,
            receive_wall_time=event.timestamp,
            target=(
                f"JAM_L{level}_KEY" if level in (1, 2, 3) else event.target
            ),
            team=self._current_team(event.team),
            context_version=int(
                getattr(context_arbiter, "context_version", 0)
            ),
            evidence={
                "event_type": "jam_key",
                "source": event.source,
                "source_target": event.target,
                "event_team": event.team,
                "level": level,
                "ascii": event.ascii_code,
                "event_timestamp": event.timestamp,
            },
        )

    def _handle_decoded_command(
        self,
        command: DecodedCommand,
        *,
        before_publish=None,
    ) -> ValidationResult:
        """Gate one decoder command into the sole production Jam publisher."""

        indeterminate = getattr(self, "publication_indeterminate", None)
        if indeterminate is not None:
            result = ValidationResult(
                False,
                "publication state is indeterminate; command output is quarantined: "
                + indeterminate,
            )
            if before_publish is not None:
                before_publish(result)
            self.get_logger().debug(result.reason)
            return result

        if command.decoder_id != self.primary_decoder_id:
            result = ValidationResult(
                False,
                f"decoder_id {command.decoder_id!r} is not primary decoder "
                f"{self.primary_decoder_id!r}",
            )
            self.get_logger().debug(result.reason)
            return result

        result = self.command_validator.validate(command)
        if before_publish is not None:
            try:
                before_publish(result)
            except Exception:
                if result.accepted:
                    self.command_validator.abort_publish_authorization(
                        command,
                        result,
                    )
                raise
        if result.accepted:
            if self.publish_ros_outputs:
                self._publish_validated_jam_code(command, result)
            else:
                self.command_validator.abort_publish_authorization(
                    command,
                    result,
                )
        else:
            self.get_logger().debug(result.reason)
        return result

    def _handle_controller_decoded_command(
        self,
        command: DecodedCommand,
        *,
        before_publish=None,
    ) -> ValidationResult:
        """Publish a command whose retry policy is owned by the controller."""

        indeterminate = getattr(self, "publication_indeterminate", None)
        if indeterminate is not None:
            result = ValidationResult(
                False,
                "publication state is indeterminate; command output is quarantined: "
                + indeterminate,
            )
            if before_publish is not None:
                before_publish(result)
            self.get_logger().debug(result.reason)
            return result

        if command.decoder_id != self.primary_decoder_id:
            result = ValidationResult(
                False,
                f"decoder_id {command.decoder_id!r} is not primary decoder "
                f"{self.primary_decoder_id!r}",
            )
            self.get_logger().debug(result.reason)
            return result

        result = self.command_validator.reserve_controller_publication(command)
        if before_publish is not None:
            try:
                before_publish(result)
            except Exception:
                if result.accepted:
                    self.command_validator.abort_publish_authorization(
                        command,
                        result,
                    )
                raise
        if result.accepted:
            if self.publish_ros_outputs:
                self._publish_validated_jam_code(command, result)
            else:
                self.command_validator.abort_publish_authorization(
                    command,
                    result,
                )
        else:
            self.get_logger().debug(result.reason)
        return result

    def _on_raw_frame(self, event: RawFrameEvent) -> None:
        if not self.publish_ros_outputs:
            return
        msg = RadarWirelessFrame()
        msg.header.stamp = self.get_clock().now().to_msg()
        msg.cmd_id = int(event.cmd_id) & 0xFFFF
        msg.payload_raw = list(event.payload)
        msg.crc8_ok = bool(event.crc8_ok)
        msg.crc16_ok = bool(event.crc16_ok)
        msg.air_chunk_index = int(event.air_chunk_index) & 0xFF
        msg.source_target = str(event.source_target)
        msg.team = self._current_team(event.team)
        self.raw_frame_pub.publish(msg)

    def _on_target_change(self, event: TargetChangeEvent) -> None:
        try:
            self.get_logger().info(
                f"receiver core target changed {event.before}->{event.after} "
                f"team={event.team}"
            )
        except Exception:
            # The RF mutation already happened inside the receiver core.
            pass

    def _on_raw_iq(self, raw_iq: np.ndarray) -> None:
        if self.iq_recorder is not None:
            self.iq_recorder.write(raw_iq)

    def _set_receiver_target_or_profile(self, target: str, *, reason: str, team: Optional[str]) -> None:
        target_upper = str(target).upper()
        if target_upper == "INFO" and self.run_mode == "competition" and self.profile_config:
            applied = self._apply_info_profile(team=team)
            if applied:
                try:
                    self.get_logger().info(
                        f"receiver INFO profile applied: {reason}"
                    )
                except Exception:
                    pass
                return
        self.adapter.set_target(target_upper)
        try:
            self.get_logger().info(
                f"receiver target set to {target_upper}: {reason}"
            )
        except Exception:
            pass

    def _apply_info_profile(self, *, team: Optional[str]) -> bool:
        profile = self.profile_config
        if not profile:
            return False
        team_name = str(team or profile.get("team") or self.controller.rx_team or "RED").upper()
        profile_team = str(profile.get("team") or "").upper()
        if profile_team and profile_team != team_name:
            self.get_logger().warn(
                f"profile team {profile_team} differs from active team {team_name}; using active team"
            )
        rescue = str(profile.get("rescue") or "").upper()
        if rescue in ("", "NONE", "NORMAL"):
            rescue = ""
        self.adapter.set_radio_profile(
            team=team_name,
            target="INFO",
            gain=int(profile.get("gain", 40)),
            rf_bw=int(profile.get("rf_bw_hz", 540000)),
            freq_offset_hz=int(profile.get("freq_offset_hz", 0)),
            rescue=rescue,
            filter_name=str(profile.get("filter") or ""),
        )
        return True

    def _publish_validated_jam_code(
        self,
        command: DecodedCommand,
        result: ValidationResult,
    ) -> None:
        indeterminate = getattr(self, "publication_indeterminate", None)
        if indeterminate is not None:
            self.command_validator.abort_publish_authorization(command, result)
            raise PublicationIndeterminateError(
                "command output is quarantined: " + indeterminate
            )
        if command.decoder_id != self.primary_decoder_id:
            self.command_validator.abort_publish_authorization(
                command,
                result,
            )
            raise ValueError(
                f"Jam publisher requires primary decoder "
                f"{self.primary_decoder_id!r}"
            )
        if not self.command_validator.begin_publish_authorization(command, result):
            raise ValueError(
                "Jam publisher requires a fresh validated command result"
            )
        try:
            context = self.latest_context
            msg = JamCode()
            msg.header.stamp = self.get_clock().now().to_msg()
            msg.valid = True
            msg.command_id = int(command.cmd_id) & 0xFFFF
            msg.level = int(result.level) & 0xFF
            msg.team = self._current_team(command.team)
            msg.target = command.target
            msg.radio_mode = self.run_mode
            stats = self.adapter.get_stats_snapshot()
            msg.rf_state = str(stats.get("rf_state") or "")
            msg.radar_info_raw = int(context.radar_info_raw) & 0xFF if context else 0
            msg.key_mutable = bool(context.key_mutable) if context else False
            msg.key = list(command.payload)
            msg.ascii_code = str(result.ascii_code)
            self.jam_code_pub.publish(msg)
        except Exception:
            self.command_validator.abort_publish_authorization(command, result)
            raise
        if not self.command_validator.commit_publish_authorization(command, result):
            self.command_validator.abort_publish_authorization(command, result)
            message = "Jam publisher transaction could not be committed"
            self.publication_indeterminate = message
            raise PublicationIndeterminateError(message)
        try:
            self.get_logger().info(
                f"published jam code level={msg.level} team={msg.team} "
                f"target={msg.target} key={msg.ascii_code}"
            )
        except Exception:
            # ROS publication is irreversible; logging is post-commit only.
            pass

    def _publish_status(self) -> None:
        adapter_status = {}
        try:
            adapter_status = self.adapter.get_stats_snapshot()
        except Exception as exc:
            adapter_status = {"adapter_error": str(exc)}

        with self._get_controller_lock():
            own_team = self._current_own_team()
            rx_team = self._current_team()
            competition_status = self.controller.status_snapshot()

        common_status = self._common_runtime_status()
        iq_recording = (
            common_status.get("recorder", {"enabled": False})
            if self.run_mode == "competition"
            else (
                {"enabled": False}
                if self.iq_recorder is None
                else self.iq_recorder.status()
            )
        )
        status = {
            "run_mode": self.run_mode,
            "own_team": own_team,
            "rx_team": rx_team,
            "publish_ros_outputs": self.publish_ros_outputs,
            "debug_accept_ros_control": self.debug_accept_ros_control,
            "start_receiver": self.start_receiver,
            "iq_source": {
                "enabled": bool(self.iq_source_path),
                "path": self.iq_source_path,
                "loop": bool(self.get_parameter("iq_source_loop").value),
                "throttle": bool(self.get_parameter("iq_source_throttle").value),
                "center_hz": float(self.get_parameter("iq_source_center_hz").value),
                "start_offset_sec": float(self.get_parameter("iq_source_start_offset_sec").value),
                "sample_rate": int(self.get_parameter("iq_source_sample_rate").value),
            },
            "original_script_path": self.adapter.get_resolved_script_path(),
            "micro_tune": {
                "enabled": bool(self.get_parameter("enable_micro_tune").value),
                "max_hz": float(self.get_parameter("micro_tune_max_hz").value),
                "step_hz": float(self.get_parameter("micro_tune_step_hz").value),
                "timeout_sec": float(self.get_parameter("micro_tune_timeout_sec").value),
            },
            "profile": {
                "path": str(self.get_parameter("profile_path").value),
                "loaded": bool(self.profile_config),
                "config": self.profile_config,
            },
            "iq_recording": iq_recording,
            "competition": competition_status,
            "publication_indeterminate": getattr(
                self, "publication_indeterminate", None
            ),
            "common_runtime": common_status,
            "core": adapter_status,
            "receiver_thread_exception": None
            if self.adapter.receiver_exception is None
            else str(self.adapter.receiver_exception),
        }
        msg = String()
        msg.data = _json_dumps(status)
        self.status_pub.publish(msg)

    def _current_team(self, fallback: str = "UNKNOWN") -> str:
        with self._get_controller_lock():
            if hasattr(self, "controller") and self.controller.rx_team:
                return self.controller.rx_team
            own_team = self._current_own_team("")
            if own_team in ("RED", "BLUE"):
                return self._opponent_team(own_team)
            return fallback or "UNKNOWN"

    def _current_own_team(self, fallback: str = "UNKNOWN") -> str:
        with self._get_controller_lock():
            if hasattr(self, "controller") and self.controller.own_team:
                return self.controller.own_team
            context = self.latest_context
            if context is not None:
                if context.self_id == 9 or context.self_color == 2:
                    return "RED"
                if context.self_id == 109 or context.self_color == 0:
                    return "BLUE"
            fallback_self_id = getattr(self, "fallback_self_id", 0)
            if fallback_self_id == 9:
                return "RED"
            if fallback_self_id == 109:
                return "BLUE"
            fallback_color = getattr(self, "_fallback_self_color", -1)
            if fallback_color == 2:
                return "RED"
            if fallback_color == 0:
                return "BLUE"
            return fallback or "UNKNOWN"

    @staticmethod
    def _opponent_team(own_team: str) -> str:
        return "BLUE" if own_team == "RED" else "RED"

    def _log_from_patch(self, message: str) -> None:
        self.get_logger().info(message)


def main(args=None) -> None:
    rclpy.init(args=args)
    node = None
    try:
        node = SdrReceiverPyWrapperNode()
        rclpy.spin(node)
    finally:
        try:
            if node is not None:
                node.destroy_node()
        finally:
            rclpy.shutdown()


def _json_safe(value):
    return _json_snapshot(value)


def _json_dumps(value) -> str:
    return json.dumps(_json_safe(value), sort_keys=True, allow_nan=False)


if __name__ == "__main__":
    main()
