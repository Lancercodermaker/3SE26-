from __future__ import annotations

import json
import os
from pathlib import Path
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
from .models import DecodedCommand, IqChunk
from .structured_recorder import StructuredRecorder, _json_snapshot


DEFAULT_ORIGINAL_SCRIPT = "auto"
PRIMARY_DECODER_ID = "improved_v67"


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
    ) -> None:
        self.record_dir = Path(os.path.expandvars(str(record_dir))).expanduser()
        self.prefix = self._sanitize_prefix(prefix or "sdr_iq")
        self.prefix_provider = prefix_provider
        self.max_sec = float(max_sec)
        self.max_bytes = int(max_bytes)
        self.every_n = max(1, int(every_n))
        self.metadata_provider = metadata_provider
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
            summary_metadata={
                "every_n": self.every_n,
                "max_sec": self.max_sec,
                "max_bytes": self.max_bytes,
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
        self.iq_recorder = self._create_iq_recorder()
        self.latest_context: Optional[RadarContext] = None
        self._fallback_msg_self_id = 0
        self._fallback_self_color = -1
        self._fallback_game_progress = 0
        self._fallback_match_time = 0
        self._fallback_radar_info_raw = 0
        self._fallback_jam_level = None
        self._fallback_key_mutable = None
        self._fallback_referee_online = None

        self.controller = CompetitionController(
            max_jam_break_level=int(self.get_parameter("max_jam_break_level").value),
            key_publish_min_interval_sec=float(
                self.get_parameter("key_publish_min_interval_sec").value
            ),
            key_retry_limit=int(self.get_parameter("key_retry_limit").value),
        )
        self.primary_decoder_id = PRIMARY_DECODER_ID
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
        self.adapter = ReceiverCoreAdapter(original_script_path, logger=self._log_from_patch)
        self.adapter.load(allow_adi_import_stub=self.import_allow_adi_stub or bool(self.iq_source_path))
        self.adapter.apply_patches(run_mode=self.run_mode, callbacks=callbacks)
        if self.iq_source_path:
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

        if self.start_receiver:
            self.adapter.start()

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
        result = False
        try:
            self.adapter.stop()
            self.adapter.restore_patches()
            if self.iq_recorder is not None:
                self.iq_recorder.close()
        finally:
            result = super().destroy_node()
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
        self.declare_parameter("initial_team", "")
        self.declare_parameter("initial_target", "")
        self.declare_parameter("initial_rx_gain", -1)
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
        if not decision.accepted:
            return

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
            decision = self.controller.handle_jam_key(level=event.level, key=event.key)
            for warning in decision.warnings:
                self.get_logger().warn(warning)
            if decision.publish and self.publish_ros_outputs:
                self._handle_decoded_command(
                    self._decoded_command_from_legacy_event(event, decision.level)
                )
            if decision.target:
                self._set_receiver_target_or_profile(
                    decision.target,
                    reason=decision.reason,
                    team=self.controller.rx_team,
                )
            elif decision.reason:
                self.get_logger().debug(decision.reason)
            return

        if self.publish_ros_outputs:
            self._handle_decoded_command(
                self._decoded_command_from_legacy_event(event, event.level)
            )

    def _decoded_command_from_legacy_event(
        self,
        event: JamKeyEvent,
        level: int,
    ) -> DecodedCommand:
        """Bridge the legacy callback into the common immutable contract."""

        context_arbiter = getattr(self, "context_arbiter", None)
        return DecodedCommand(
            cmd_id=event.cmd_id,
            payload=bytes(event.key),
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
    ) -> ValidationResult:
        """Gate one decoder command into the sole production Jam publisher."""

        if command.decoder_id != self.primary_decoder_id:
            result = ValidationResult(
                False,
                f"decoder_id {command.decoder_id!r} is not primary decoder "
                f"{self.primary_decoder_id!r}",
            )
            self.get_logger().debug(result.reason)
            return result

        result = self.command_validator.validate(command)
        if result.accepted:
            if self.publish_ros_outputs:
                self._publish_validated_jam_code(command, result)
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
        self.get_logger().info(
            f"receiver core target changed {event.before}->{event.after} team={event.team}"
        )

    def _on_raw_iq(self, raw_iq: np.ndarray) -> None:
        if self.iq_recorder is not None:
            self.iq_recorder.write(raw_iq)

    def _set_receiver_target_or_profile(self, target: str, *, reason: str, team: Optional[str]) -> None:
        target_upper = str(target).upper()
        if target_upper == "INFO" and self.run_mode == "competition" and self.profile_config:
            applied = self._apply_info_profile(team=team)
            if applied:
                self.get_logger().info(f"receiver INFO profile applied: {reason}")
                return
        self.adapter.set_target(target_upper)
        self.get_logger().info(f"receiver target set to {target_upper}: {reason}")

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
        if not self.command_validator.consume_publish_authorization(command, result):
            raise ValueError(
                "Jam publisher requires a fresh validated command result"
            )
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
        self.get_logger().info(
            f"published jam code level={msg.level} team={msg.team} "
            f"target={msg.target} key={msg.ascii_code}"
        )

    def _publish_status(self) -> None:
        adapter_status = {}
        try:
            adapter_status = self.adapter.get_stats_snapshot()
        except Exception as exc:
            adapter_status = {"adapter_error": str(exc)}

        status = {
            "run_mode": self.run_mode,
            "own_team": self._current_own_team(),
            "rx_team": self._current_team(),
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
            "iq_recording": {"enabled": False}
            if self.iq_recorder is None
            else self.iq_recorder.status(),
            "competition": self.controller.status_snapshot(),
            "core": adapter_status,
            "receiver_thread_exception": None
            if self.adapter.receiver_exception is None
            else str(self.adapter.receiver_exception),
        }
        msg = String()
        msg.data = _json_dumps(status)
        self.status_pub.publish(msg)

    def _current_team(self, fallback: str = "UNKNOWN") -> str:
        if hasattr(self, "controller") and self.controller.rx_team:
            return self.controller.rx_team
        own_team = self._current_own_team("")
        if own_team in ("RED", "BLUE"):
            return self._opponent_team(own_team)
        return fallback or "UNKNOWN"

    def _current_own_team(self, fallback: str = "UNKNOWN") -> str:
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
    node = SdrReceiverPyWrapperNode()
    try:
        rclpy.spin(node)
    finally:
        node.destroy_node()
        rclpy.shutdown()


def _json_safe(value):
    return _json_snapshot(value)


def _json_dumps(value) -> str:
    return json.dumps(_json_safe(value), sort_keys=True, allow_nan=False)


if __name__ == "__main__":
    main()
