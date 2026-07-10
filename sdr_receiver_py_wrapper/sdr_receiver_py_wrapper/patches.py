from __future__ import annotations

from dataclasses import dataclass
import threading
import time
from types import ModuleType
from typing import Callable, Dict, Optional

import numpy as np


INFO_CMD_IDS = frozenset({0x0A01, 0x0A02, 0x0A03, 0x0A04, 0x0A05})
JAM_CMD_ID = 0x0A06


@dataclass(frozen=True)
class JamKeyEvent:
    cmd_id: int
    payload: bytes
    key: bytes
    ascii_code: str
    level: int
    team: str
    target: str
    source: str
    timestamp: float


@dataclass(frozen=True)
class RawFrameEvent:
    cmd_id: int
    payload: bytes
    source: str
    source_target: str
    team: str
    crc8_ok: bool
    crc16_ok: bool
    air_chunk_index: int
    timestamp: float


@dataclass(frozen=True)
class TargetChangeEvent:
    before: str
    after: str
    team: str
    timestamp: float


@dataclass
class PatchCallbacks:
    on_jam_key: Optional[Callable[[JamKeyEvent], None]] = None
    on_raw_frame: Optional[Callable[[RawFrameEvent], None]] = None
    on_target_change: Optional[Callable[[TargetChangeEvent], None]] = None
    on_raw_iq: Optional[Callable[[np.ndarray], None]] = None


class PatchManager:
    """Owns all monkey patches applied to the original receiver module."""

    def __init__(
        self,
        module: ModuleType,
        *,
        run_mode: str,
        callbacks: Optional[PatchCallbacks] = None,
        stop_event: Optional[threading.Event] = None,
        logger: Optional[Callable[[str], None]] = None,
    ) -> None:
        self.module = module
        self.run_mode = run_mode
        self.callbacks = callbacks or PatchCallbacks()
        self.stop_event = stop_event or threading.Event()
        self.logger = logger or (lambda _message: None)
        self.originals: Dict[str, object] = {}
        self._dashboard_last_log = 0.0
        self._applied = False

    def apply(self) -> None:
        if self._applied:
            return
        self._patch_validate_and_parse()
        self._patch_select_tune_target()
        self._patch_filter_iq()
        self._patch_fast_demod()
        if self.run_mode == "competition":
            self._patch_competition_interaction()
        self._applied = True

    def restore(self) -> None:
        for name, original in self.originals.items():
            setattr(self.module, name, original)
        self.originals.clear()
        self._applied = False

    def _remember(self, name: str) -> object:
        original = getattr(self.module, name)
        self.originals.setdefault(name, original)
        return original

    def _patch_validate_and_parse(self) -> None:
        original = self._remember("validate_and_parse")

        def wrapped_validate_and_parse(cmd_id, payload, *args, **kwargs):
            result = original(cmd_id, payload, *args, **kwargs)
            if result:
                source = self._extract_source(args, kwargs)
                self._emit_parse_event(int(cmd_id), payload, source)
            return result

        setattr(self.module, "validate_and_parse", wrapped_validate_and_parse)

    def _patch_select_tune_target(self) -> None:
        if not hasattr(self.module, "select_tune_target"):
            return
        original = self._remember("select_tune_target")

        def wrapped_select_tune_target(target, *args, **kwargs):
            before = self._current_target()
            result = original(target, *args, **kwargs)
            after = self._current_target()
            if before != after and self.callbacks.on_target_change:
                self._safe_callback(
                    self.callbacks.on_target_change,
                    TargetChangeEvent(
                        before=before,
                        after=after,
                        team=self._current_team(),
                        timestamp=time.time(),
                    ),
                )
            return result

        setattr(self.module, "select_tune_target", wrapped_select_tune_target)

    def _patch_filter_iq(self) -> None:
        if not hasattr(self.module, "filter_iq"):
            return
        original = self._remember("filter_iq")
        if hasattr(self.module, "INFO_RESCUE_AC_ERRORS"):
            self._remember("INFO_RESCUE_AC_ERRORS")
        if hasattr(self.module, "INFO_HEADER_MAX_ERRORS"):
            self._remember("INFO_HEADER_MAX_ERRORS")

        def wrapped_filter_iq(rx_data, cfg, *args, **kwargs):
            if isinstance(cfg, dict) and cfg.get("adaptive_notch"):
                try:
                    rx_data = self._adaptive_notch(
                        rx_data,
                        max_bins=int(cfg.get("notch_max_bins", 8)),
                        threshold_db=float(cfg.get("notch_threshold_db", 22.0)),
                    )
                except Exception as exc:
                    stats = getattr(self.module, "STATE", {}).setdefault("STATS", {})
                    stats["LAST_ERROR"] = f"adaptive notch failed: {exc}"
            if isinstance(cfg, dict):
                self._apply_weak_info_limits(cfg)
            return original(rx_data, cfg, *args, **kwargs)

        setattr(self.module, "filter_iq", wrapped_filter_iq)

    def _patch_fast_demod(self) -> None:
        if not hasattr(self.module, "fast_demod"):
            return
        original = self._remember("fast_demod")

        def wrapped_fast_demod(rx_data, ac_target, *args, **kwargs):
            if self.callbacks.on_raw_iq:
                self._safe_callback(self.callbacks.on_raw_iq, np.asarray(rx_data, dtype=np.complex64))
            before = self._stats_snapshot()
            locked = original(rx_data, ac_target, *args, **kwargs)
            try:
                if self._should_run_weak_soft_ac(before):
                    locked = self._weak_soft_acquire(rx_data, ac_target) or locked
            except Exception as exc:
                stats = getattr(self.module, "STATE", {}).setdefault("STATS", {})
                stats["LAST_ERROR"] = f"weak soft AC failed: {exc}"
            return locked

        setattr(self.module, "fast_demod", wrapped_fast_demod)

    def _patch_competition_interaction(self) -> None:
        if hasattr(self.module, "handle_keyboard"):
            self._remember("handle_keyboard")

            def competition_handle_keyboard():
                return not self.stop_event.is_set()

            setattr(self.module, "handle_keyboard", competition_handle_keyboard)

        for name in ("init_dashboard", "restore_terminal"):
            if hasattr(self.module, name):
                self._remember(name)
                setattr(self.module, name, lambda *args, **kwargs: None)

        if hasattr(self.module, "render_dashboard"):
            self._remember("render_dashboard")

            def competition_render_dashboard(locked=False, adc_peak=0.0):
                now = time.time()
                if now - self._dashboard_last_log >= 5.0:
                    self._dashboard_last_log = now
                    stats = getattr(self.module, "STATE", {}).get("STATS", {})
                    self.logger(
                        "competition core status: "
                        f"target={self._current_target()} "
                        f"locked={bool(locked)} adc_peak={float(adc_peak):.3f} "
                        f"rf_state={stats.get('RF_STATE', '')}"
                    )

            setattr(self.module, "render_dashboard", competition_render_dashboard)

    def _emit_parse_event(self, cmd_id: int, payload, source: str) -> None:
        payload_bytes = bytes(payload)
        if cmd_id == JAM_CMD_ID and len(payload_bytes) >= 6:
            key = payload_bytes[:6]
            event = JamKeyEvent(
                cmd_id=cmd_id,
                payload=payload_bytes,
                key=key,
                ascii_code=key.decode("ascii", errors="replace"),
                level=self._current_level(),
                team=self._current_team(),
                target=self._current_target(),
                source=source,
                timestamp=time.time(),
            )
            if self.callbacks.on_jam_key:
                self._safe_callback(self.callbacks.on_jam_key, event)
        elif cmd_id in INFO_CMD_IDS:
            event = RawFrameEvent(
                cmd_id=cmd_id,
                payload=payload_bytes,
                source=source,
                source_target=self._current_target(),
                team=self._current_team(),
                crc8_ok=True,
                crc16_ok=True,
                air_chunk_index=0,
                timestamp=time.time(),
            )
            if self.callbacks.on_raw_frame:
                self._safe_callback(self.callbacks.on_raw_frame, event)

    def _safe_callback(self, callback: Callable, event) -> None:
        try:
            callback(event)
        except Exception as exc:  # pragma: no cover - defensive bridge.
            self.logger(f"patch callback failed: {exc}")

    def _adaptive_notch(self, rx_data, *, max_bins: int, threshold_db: float):
        max_bins = max(0, int(max_bins))
        if max_bins <= 0:
            return rx_data
        x = np.asarray(rx_data, dtype=np.complex64)
        n = len(x)
        if n < 1024:
            return rx_data
        spec = np.fft.fft(x)
        power = np.abs(spec) ** 2
        sample_rate = float(getattr(self.module, "SDR_FS", 2_500_000))
        freqs = np.fft.fftfreq(n, d=1.0 / sample_rate)
        median = float(np.median(power))
        if median <= 0.0:
            return rx_data
        mask = (power > median * (10.0 ** (float(threshold_db) / 10.0))) & (np.abs(freqs) > 5_000.0)
        candidates = np.where(mask)[0]
        if candidates.size == 0:
            return rx_data
        ranked = candidates[np.argsort(power[candidates])[-max_bins:]]
        for index in ranked:
            for delta in (-2, -1, 0, 1, 2):
                spec[(int(index) + delta) % n] = 0.0
        stats = getattr(self.module, "STATE", {}).setdefault("STATS", {})
        stats["ADAPTIVE_NOTCH_BINS"] = int(len(ranked))
        return np.fft.ifft(spec)

    def _should_run_weak_soft_ac(self, before: dict) -> bool:
        target = self._current_target()
        if target != "INFO":
            return False
        if not hasattr(self.module, "get_effective_filter_params"):
            return False
        cfg = self.module.get_effective_filter_params(target)
        if not isinstance(cfg, dict) or not cfg.get("weak_soft_ac"):
            return False
        after = self._stats_snapshot()
        hard_ac_delta = max(0, after.get("AC_RAW", 0) - before.get("AC_RAW", 0))
        crc_delta = max(0, after.get("CRC8", 0) - before.get("CRC8", 0)) + max(
            0, after.get("CRC16", 0) - before.get("CRC16", 0)
        )
        return hard_ac_delta == 0 and crc_delta == 0

    def _weak_soft_acquire(self, rx_data, ac_target) -> bool:
        cfg = self.module.get_effective_filter_params("INFO")
        state = getattr(self.module, "STATE", {})
        stats = state.setdefault("STATS", {})
        sps = int(getattr(self.module, "SPS", 52))
        if sps <= 0:
            return False

        x = np.asarray(rx_data, dtype=np.complex64)
        if len(x) < sps * 220:
            return False
        x = x - np.mean(x)

        if hasattr(self.module, "get_effective_radio_params"):
            digital_shift = int(self.module.get_effective_radio_params().get("digital_shift", 0) or 0)
            if digital_shift:
                n = np.arange(len(x), dtype=np.float64)
                sample_rate = float(getattr(self.module, "SDR_FS", 2_500_000))
                x = x * np.exp(1j * 2.0 * np.pi * digital_shift * n / sample_rate)

        filtered = self.module.filter_iq(x, cfg)
        raw_freq = np.angle(filtered[1:] * np.conj(filtered[:-1]))
        freq = raw_freq - np.median(raw_freq)
        trend_len = int(sps * float(cfg.get("trend_bits", 16)))
        if len(freq) > trend_len * 2:
            freq = freq - self.module.moving_average(freq, trend_len)
        smooth_len = max(5, int(sps * float(cfg.get("smooth_frac", 0.34))))
        smoothed = self.module.moving_average(freq, smooth_len)

        diag = self._weak_soft_ac_diagnostics(smoothed, sps, str(ac_target), cfg)
        records = self._soft_ac_peak_records(
            smoothed,
            sps,
            str(ac_target),
            min_sigma=float(cfg.get("weak_soft_min_sigma", 5.2)),
            peak_limit=int(cfg.get("weak_soft_peak_limit", 8)),
        )
        stats["WEAK_SOFT_PEAK_RECORDS"] = int(len(records))
        if not records:
            stats["WEAK_SOFT_STATUS"] = (
                f"no_peak sigma={diag['best_sigma']:.2f} "
                f"hard_min={diag['hard_min_errors']}"
            )
            return False

        accepted = self._append_weak_soft_payloads(smoothed, sps, str(ac_target), records, cfg)
        if accepted <= 0:
            stats["WEAK_SOFT_STATUS"] = (
                f"no_admit sigma={diag['best_sigma']:.2f} "
                f"hard_min={diag['hard_min_errors']}"
            )
            return False
        stats["WEAK_SOFT_AC"] = int(stats.get("WEAK_SOFT_AC", 0) or 0) + accepted
        stats["WEAK_SOFT_STATUS"] = f"admitted {accepted}"
        stats["LAST_ERROR"] = f"weak soft AC admitted {accepted} payload(s)"
        return True

    def _weak_soft_ac_diagnostics(self, smoothed: np.ndarray, sps: int, ac_target: str, cfg: dict) -> dict:
        stats = getattr(self.module, "STATE", {}).setdefault("STATS", {})
        template = np.array([1.0 if bit == "1" else -1.0 for bit in ac_target], dtype=np.float64)
        ac_len = len(template)
        shift_step = max(1, sps // 32)
        threshold_values = getattr(self.module, "THRESHOLD_K_VALUES", (0.0, 0.35, -0.35, 0.2, -0.2, 0.1, -0.1))
        best = {
            "best_sigma": 0.0,
            "best_corr": 0.0,
            "best_shift": 0,
            "best_polarity": "+",
            "best_symbol_index": -1,
            "hard_min_errors": ac_len,
        }
        for shift in range(0, sps, shift_step):
            symbols = smoothed[shift::sps]
            if len(symbols) < 216:
                continue
            mid = float(np.median(symbols))
            spread = float(np.percentile(symbols, 75) - np.percentile(symbols, 25))
            if spread < 1e-9:
                continue
            z = (symbols - mid) / spread
            for polarity in ("+", "-"):
                z_pol = z if polarity == "+" else -z
                corr = np.correlate(z_pol, template, mode="valid") / float(ac_len)
                if corr.size:
                    idx = int(np.argmax(corr))
                    std = float(np.std(corr)) or 1e-9
                    sigma = float(corr[idx]) / std
                    if sigma > best["best_sigma"]:
                        best.update(
                            {
                                "best_sigma": sigma,
                                "best_corr": float(corr[idx]),
                                "best_shift": int(shift),
                                "best_polarity": polarity,
                                "best_symbol_index": idx,
                            }
                        )
                for _k, threshold in self.module.threshold_grid(symbols, threshold_values):
                    hard = np.where(symbols > threshold, 1.0, -1.0)
                    if polarity == "-":
                        hard = -hard
                    dots = np.correlate(hard, template, mode="valid")
                    if not dots.size:
                        continue
                    errors = ((ac_len - dots) / 2.0).astype(np.int32)
                    best["hard_min_errors"] = min(best["hard_min_errors"], int(np.min(errors)))

        stats["WEAK_SOFT_SIGMA"] = round(float(best["best_sigma"]), 3)
        stats["WEAK_SOFT_CORR"] = round(float(best["best_corr"]), 4)
        stats["WEAK_SOFT_SHIFT"] = int(best["best_shift"])
        stats["WEAK_SOFT_POLARITY"] = str(best["best_polarity"])
        stats["WEAK_SOFT_SYMBOL_INDEX"] = int(best["best_symbol_index"])
        stats["WEAK_SOFT_HARD_MIN_ERRORS"] = int(best["hard_min_errors"])
        stats["WEAK_SOFT_MIN_SIGMA"] = float(cfg.get("weak_soft_min_sigma", 5.2))
        return best

    def _soft_ac_peak_records(
        self,
        smoothed: np.ndarray,
        sps: int,
        ac_target: str,
        *,
        min_sigma: float,
        peak_limit: int,
    ) -> list[dict]:
        template = np.array([1.0 if bit == "1" else -1.0 for bit in ac_target], dtype=np.float64)
        ac_len = len(template)
        records: list[dict] = []
        shift_step = max(1, sps // 32)
        for shift in range(0, sps, shift_step):
            symbols = smoothed[shift::sps]
            if len(symbols) < 216:
                continue
            mid = float(np.median(symbols))
            spread = float(np.percentile(symbols, 75) - np.percentile(symbols, 25))
            if spread < 1e-9:
                continue
            z = (symbols - mid) / spread
            for polarity in ("+", "-"):
                z_pol = z if polarity == "+" else -z
                corr = np.correlate(z_pol, template, mode="valid") / float(ac_len)
                if not corr.size:
                    continue
                idx = int(np.argmax(corr))
                std = float(np.std(corr)) or 1e-9
                sigma = float(corr[idx]) / std
                if sigma < min_sigma:
                    continue
                records.append(
                    {
                        "idx": idx,
                        "shift": int(shift),
                        "polarity": polarity,
                        "sigma": sigma,
                        "corr": float(corr[idx]),
                    }
                )
        records.sort(key=lambda item: (item["sigma"], item["corr"]), reverse=True)
        return records[: max(1, int(peak_limit))]

    def _append_weak_soft_payloads(
        self,
        smoothed: np.ndarray,
        sps: int,
        ac_target: str,
        records: list[dict],
        cfg: dict,
    ) -> int:
        state = getattr(self.module, "STATE", {})
        stats = state.setdefault("STATS", {})
        pools = state.setdefault("BIT_POOLS", {})
        accepted = 0
        max_candidates = int(cfg.get("weak_soft_max_candidates", 2))
        max_ac_errors = int(cfg.get("weak_soft_ac_max_errors", 18))
        max_header_errors = int(cfg.get("weak_soft_header_max_errors", 20))
        threshold_values = getattr(self.module, "THRESHOLD_K_VALUES", (0.0, 0.35, -0.35, 0.2, -0.2, 0.1, -0.1))
        pool_max_bits = int(getattr(self.module, "POOL_MAX_BITS", 2160))
        pool_keep_bits = int(getattr(self.module, "POOL_KEEP_BITS", 1080))

        candidates = []
        for record in records:
            symbols = smoothed[int(record["shift"])::sps]
            if len(symbols) < int(record["idx"]) + 216:
                continue
            for k, threshold in self.module.threshold_grid(symbols, threshold_values):
                if record["polarity"] == "+":
                    bits = "".join("1" if sample > threshold else "0" for sample in symbols)
                else:
                    bits = "".join("0" if sample > threshold else "1" for sample in symbols)
                idx = int(record["idx"])
                ac_bits = bits[idx:idx + 64]
                if len(ac_bits) != 64:
                    continue
                ac_errors = self.module.hamming_distance(ac_bits, ac_target, max_ac_errors)
                if ac_errors > max_ac_errors:
                    continue
                air_header = bits[idx + 64:idx + 96]
                header_errors = (
                    self.module.hamming_distance(air_header, getattr(self.module, "AIR_HEADER", ""), max_header_errors)
                    if len(air_header) == 32
                    else 32
                )
                if header_errors > max_header_errors:
                    continue
                payload = bits[idx + 96:idx + 216]
                if len(payload) != 120:
                    continue
                score = float(record["sigma"]) * 8.0 - float(ac_errors) - float(header_errors) * 0.35
                candidates.append(
                    {
                        "score": score,
                        "payload": payload,
                        "idx": idx,
                        "k": float(k),
                        "shift": int(record["shift"]),
                        "polarity": str(record["polarity"]),
                        "sigma": float(record["sigma"]),
                        "ac_errors": int(ac_errors),
                        "header_errors": int(header_errors),
                    }
                )

        seen = set()
        candidates.sort(key=lambda item: item["score"], reverse=True)
        for candidate in candidates:
            if accepted >= max_candidates:
                break
            dedupe_key = (candidate["payload"], candidate["shift"], candidate["polarity"])
            if dedupe_key in seen:
                continue
            seen.add(dedupe_key)

            pool_key = self.module.make_pool_key("INFO", candidate["polarity"], candidate["shift"], candidate["k"])
            stats["AC_RAW"] = int(stats.get("AC_RAW", 0) or 0) + 1
            stats["AC"] = int(stats.get("AC", 0) or 0) + 1
            stats["SOFT_AC_RAW"] = int(stats.get("SOFT_AC_RAW", 0) or 0) + 1
            stats["SOFT_AC"] = int(stats.get("SOFT_AC", 0) or 0) + 1
            stats["LAST_AC_TIME"] = time.time()
            stats["WEAK_SOFT_SIGMA"] = round(candidate["sigma"], 3)
            stats["WEAK_SOFT_AC_ERRORS"] = candidate["ac_errors"]
            stats["WEAK_SOFT_HEADER_ERRORS"] = candidate["header_errors"]

            new_pool = pools.get(pool_key, "") + candidate["payload"]
            if len(new_pool) > pool_max_bits:
                new_pool = new_pool[-pool_keep_bits:]
            pools[pool_key] = new_pool

            delta = self.module.process_pool(pool_key, source="weak_soft")
            stats["SOFT_SOF"] = int(stats.get("SOFT_SOF", 0) or 0) + int(delta.get("SOF", 0) or 0)
            stats["SOFT_CRC8"] = int(stats.get("SOFT_CRC8", 0) or 0) + int(delta.get("CRC8", 0) or 0)
            stats["SOFT_CRC16"] = int(stats.get("SOFT_CRC16", 0) or 0) + int(delta.get("CRC16", 0) or 0)
            self._update_soft_tracking(pool_key, candidate, delta)
            accepted += 1
        return accepted

    def _update_soft_tracking(self, pool_key, candidate: dict, delta: dict) -> None:
        state = getattr(self.module, "STATE", {})
        track = state.setdefault("TRACK", {})
        now = time.time()
        crc16 = int(delta.get("CRC16", 0) or 0)
        crc8 = int(delta.get("CRC8", 0) or 0)
        if crc16 <= 0 and crc8 <= 0:
            return
        track["TARGET"] = "INFO"
        track["PROFILE"] = {
            "k": candidate["k"],
            "shift": candidate["shift"],
            "polarity": candidate["polarity"],
        }
        track["LOCK_UNTIL"] = max(float(track.get("LOCK_UNTIL", 0.0) or 0.0), now + (1.0 if crc16 else 0.40))
        track["MISS"] = 0
        if crc16:
            track["LAST_CRC16"] = now
        if hasattr(self.module, "prune_pools"):
            self.module.prune_pools(preferred_key=pool_key)

    def _apply_weak_info_limits(self, cfg: dict) -> None:
        state = getattr(self.module, "STATE", {})
        tune_cfg = getattr(self.module, "TUNE_CFG", {})
        if tune_cfg.get("TARGET") != "INFO":
            return
        original_ac = self.originals.get(
            "INFO_RESCUE_AC_ERRORS",
            getattr(self.module, "INFO_RESCUE_AC_ERRORS", 3),
        )
        original_header = self.originals.get(
            "INFO_HEADER_MAX_ERRORS",
            getattr(self.module, "INFO_HEADER_MAX_ERRORS", 3),
        )
        weak_ac = cfg.get("weak_ac_max_errors")
        weak_header = cfg.get("weak_header_max_errors")
        if weak_ac is None and weak_header is None:
            setattr(self.module, "INFO_RESCUE_AC_ERRORS", original_ac)
            setattr(self.module, "INFO_HEADER_MAX_ERRORS", original_header)
            return
        if weak_ac is not None:
            setattr(self.module, "INFO_RESCUE_AC_ERRORS", int(weak_ac))
        if weak_header is not None:
            setattr(self.module, "INFO_HEADER_MAX_ERRORS", int(weak_header))
        stats = state.setdefault("STATS", {})
        stats["WEAK_INFO_LIMITS"] = (
            f"ac<={getattr(self.module, 'INFO_RESCUE_AC_ERRORS', original_ac)} "
            f"hdr<={getattr(self.module, 'INFO_HEADER_MAX_ERRORS', original_header)}"
        )

    def _stats_snapshot(self) -> dict:
        stats = getattr(self.module, "STATE", {}).setdefault("STATS", {})
        return {
            "AC_RAW": int(stats.get("AC_RAW", 0) or 0),
            "CRC8": int(stats.get("CRC8", 0) or 0),
            "CRC16": int(stats.get("CRC16", 0) or 0),
        }

    @staticmethod
    def _extract_source(args, kwargs) -> str:
        if "source" in kwargs:
            return str(kwargs["source"])
        if args:
            return str(args[0])
        return "direct"

    def _current_team(self) -> str:
        cfg = getattr(self.module, "TUNE_CFG", {})
        return str(cfg.get("TEAM", "UNKNOWN"))

    def _current_target(self) -> str:
        cfg = getattr(self.module, "TUNE_CFG", {})
        return str(cfg.get("TARGET", "UNKNOWN"))

    def _current_level(self) -> int:
        target = self._current_target().upper()
        if target == "L1":
            return 1
        if target == "L2":
            return 2
        if target == "L3":
            return 3
        state = getattr(self.module, "STATE", {})
        try:
            return int(state.get("ENCRYPT_LVL", 0))
        except Exception:
            return 0
