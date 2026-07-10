from __future__ import annotations

import copy
import importlib.util
import os
from pathlib import Path
import sys
import threading
from types import ModuleType
import types
from typing import Callable, Optional

from .iq_file_source import IqFilePluto
from .patches import PatchCallbacks, PatchManager


class ReceiverCoreLoadError(RuntimeError):
    """Raised when the original receiver script cannot be imported safely."""


ORIGINAL_SCRIPT_FILENAME = (
    "receiving_messages_adaptive_filter_v67_l2cal_20260505_l2rescue_80k_g40.py"
)
AUTO_SCRIPT_PATH_VALUES = {"", "auto", "AUTO", "bundled", "BUNDLED", "default", "DEFAULT"}
PLUTO_RX_HARDWARE_GAIN_MAX = 73


def find_original_script_path(script_path: Optional[str] = None) -> Path:
    """Resolve the original receiver script without depending on one fixed path."""

    requested = str(script_path or "").strip()
    candidates = []
    notes = []

    def add_candidate(value: Optional[str], label: str) -> None:
        if not value:
            return
        expanded = Path(os.path.expandvars(os.path.expanduser(str(value))))
        candidates.append((expanded, label))

    if requested and requested.lower() not in {value.lower() for value in AUTO_SCRIPT_PATH_VALUES}:
        add_candidate(requested, "parameter original_script_path")

    add_candidate(os.environ.get("SDR_RECEIVER_ORIGINAL_SCRIPT"), "SDR_RECEIVER_ORIGINAL_SCRIPT")

    package_dir = Path(__file__).resolve().parent
    repo_package_root = package_dir.parent
    add_candidate(package_dir / "vendor" / ORIGINAL_SCRIPT_FILENAME, "bundled package vendor")
    add_candidate(repo_package_root / ORIGINAL_SCRIPT_FILENAME, "wrapper package root")
    add_candidate(Path.cwd() / ORIGINAL_SCRIPT_FILENAME, "current working directory")
    add_candidate(Path.cwd() / "sdr_receiver_py_wrapper" / "vendor" / ORIGINAL_SCRIPT_FILENAME, "cwd package vendor")

    for base in (
        Path.home() / "sdr_runtime" / "receiver_core",
        Path.home() / "radar_ws" / "src" / "sdr_receiver_py_wrapper" / "sdr_receiver_py_wrapper" / "vendor",
        Path("/opt/sdr_receiver_py_wrapper"),
        Path("/opt/sdr_runtime/receiver_core"),
        Path(r"C:\Users\Fancy\Downloads"),
    ):
        add_candidate(base / ORIGINAL_SCRIPT_FILENAME, f"search dir {base}")

    seen = set()
    for path, label in candidates:
        try:
            normalized = path.resolve()
        except Exception:
            normalized = path.absolute()
        key = str(normalized)
        if key in seen:
            continue
        seen.add(key)
        if normalized.is_file():
            return normalized
        notes.append(f"{label}: {normalized}")

    raise ReceiverCoreLoadError(
        "could not locate original receiver script. Checked:\n  " + "\n  ".join(notes)
    )


def _install_adi_import_stub() -> None:
    if "adi" in sys.modules:
        return

    adi_stub = types.ModuleType("adi")

    class Pluto:  # pragma: no cover - used only for import smoke tests.
        def __init__(self, *args, **kwargs):
            raise RuntimeError("adi import stub cannot open SDR hardware")

    adi_stub.Pluto = Pluto
    sys.modules["adi"] = adi_stub


def load_original_module(
    script_path: Optional[str] = None,
    *,
    module_name: str = "_sdr_receiver_v67_core",
    allow_adi_import_stub: bool = False,
) -> ModuleType:
    path = find_original_script_path(script_path)

    if allow_adi_import_stub:
        _install_adi_import_stub()

    unique_name = f"{module_name}_{abs(hash(str(path.resolve())))}"
    spec = importlib.util.spec_from_file_location(unique_name, str(path))
    if spec is None or spec.loader is None:
        raise ReceiverCoreLoadError(f"cannot create import spec for: {path}")

    module = importlib.util.module_from_spec(spec)
    sys.modules[unique_name] = module
    try:
        spec.loader.exec_module(module)
    except ModuleNotFoundError as exc:
        if exc.name == "adi" and not allow_adi_import_stub:
            raise ReceiverCoreLoadError(
                "missing Python module 'adi'. Install pyadi-iio, or run offline smoke tests "
                "with allow_adi_import_stub enabled."
            ) from exc
        raise

    required = ("main", "validate_and_parse", "TUNE_CFG", "STATE")
    missing = [name for name in required if not hasattr(module, name)]
    if missing:
        raise ReceiverCoreLoadError(f"original receiver script missing required symbols: {missing}")

    return module


class ReceiverCoreAdapter:
    """Thread-safe adapter for the original v67 receiver module globals."""

    def __init__(
        self,
        script_path: Optional[str] = None,
        *,
        logger: Optional[Callable[[str], None]] = None,
    ) -> None:
        self.script_path = script_path
        self.resolved_script_path: Optional[Path] = None
        self.logger = logger or (lambda _message: None)
        self.lock = threading.RLock()
        self.stop_event = threading.Event()
        self.module: Optional[ModuleType] = None
        self.patch_manager: Optional[PatchManager] = None
        self.receiver_thread: Optional[threading.Thread] = None
        self.receiver_exception: Optional[BaseException] = None
        self.iq_source_config: Optional[dict] = None

    def load(self, *, allow_adi_import_stub: bool = False) -> ModuleType:
        with self.lock:
            if self.module is None:
                resolved = find_original_script_path(self.script_path)
                self.resolved_script_path = resolved
                self.logger(f"loading original SDR receiver script: {resolved}")
                self.module = load_original_module(
                    str(resolved),
                    allow_adi_import_stub=allow_adi_import_stub,
                )
            return self.module

    def get_resolved_script_path(self) -> Optional[str]:
        return None if self.resolved_script_path is None else str(self.resolved_script_path)

    def apply_patches(self, *, run_mode: str, callbacks: PatchCallbacks) -> None:
        module = self._require_module()
        with self.lock:
            if self.patch_manager is not None:
                self.patch_manager.restore()
            self.patch_manager = PatchManager(
                module,
                run_mode=run_mode,
                callbacks=callbacks,
                stop_event=self.stop_event,
                logger=self.logger,
            )
            self.patch_manager.apply()

    def configure_iq_file_source(
        self,
        *,
        path: str,
        loop: bool = True,
        throttle: bool = True,
        center_hz: float = 0.0,
        start_offset_sec: float = 0.0,
        sample_rate_hz: int = 0,
    ) -> None:
        """Replace adi.Pluto with a file-backed source when the core starts."""

        if not str(path).strip():
            self.iq_source_config = None
            return
        self.iq_source_config = {
            "path": str(path),
            "loop": bool(loop),
            "throttle": bool(throttle),
            "center_hz": float(center_hz),
            "start_offset_sec": float(start_offset_sec),
            "sample_rate_hz": int(sample_rate_hz),
        }

    def restore_patches(self) -> None:
        with self.lock:
            if self.patch_manager is not None:
                self.patch_manager.restore()
                self.patch_manager = None

    def set_team(self, team: str) -> None:
        module = self._require_module()
        team_upper = team.upper()
        if team_upper not in ("RED", "BLUE"):
            raise ValueError(f"invalid team: {team}")
        with self.lock:
            module.TUNE_CFG["TEAM"] = team_upper
            self._mark_dirty_locked()

    def set_target(
        self,
        target: str,
        *,
        info_l2_rescue: bool = False,
        info_l3_rescue: bool = False,
    ) -> None:
        module = self._require_module()
        core_target = self._normalize_target(target)
        with self.lock:
            if hasattr(module, "select_tune_target"):
                module.select_tune_target(
                    core_target,
                    info_l3_rescue=bool(info_l3_rescue),
                    info_l2_rescue=bool(info_l2_rescue),
                )
            else:
                module.TUNE_CFG["TARGET"] = core_target
                self._mark_dirty_locked()

    def set_manual_gain(self, target: str, gain: int) -> None:
        module = self._require_module()
        core_target = self._normalize_target(target)
        with self.lock:
            gain_min = int(getattr(module, "RX_GAIN_MIN", 0))
            gain_max = int(getattr(module, "RX_GAIN_MAX", 100))
            clamped_gain = int(max(gain_min, min(gain_max, int(gain))))
            state = getattr(module, "STATE", {})
            state.setdefault("MANUAL_RX_GAINS", {})[core_target] = clamped_gain
            stats = state.setdefault("STATS", {})
            stats["GAIN_CEILING"] = clamped_gain
            stats["LAST_ERROR"] = f"wrapper initial gain {core_target}={clamped_gain}"
            self._mark_dirty_locked()

    def set_rx_gain_max_override(self, max_gain: int) -> None:
        """Set the imported core gain ceiling, capped to the Pluto/AD936x hardware limit."""

        module = self._require_module()
        with self.lock:
            current = int(getattr(module, "RX_GAIN_MAX", 73))
            requested_raw = int(max_gain)
            requested = min(requested_raw, PLUTO_RX_HARDWARE_GAIN_MAX)
            if requested_raw > PLUTO_RX_HARDWARE_GAIN_MAX:
                state = getattr(module, "STATE", {})
                stats = state.setdefault("STATS", {})
                stats["LAST_ERROR"] = (
                    f"wrapper ignored RX_GAIN_MAX override {requested_raw}; "
                    f"Pluto/AD936x hardware max is {PLUTO_RX_HARDWARE_GAIN_MAX}"
                )
                return
            if requested <= current:
                return
            setattr(module, "RX_GAIN_MAX", requested)
            state = getattr(module, "STATE", {})
            stats = state.setdefault("STATS", {})
            stats["LAST_ERROR"] = f"wrapper RX_GAIN_MAX override {current}->{requested}"
            self._mark_dirty_locked()

    def set_radio_profile(
        self,
        *,
        team: str,
        target: str = "INFO",
        gain: Optional[int] = None,
        rf_bw: Optional[int] = None,
        freq_offset_hz: int = 0,
        rescue: Optional[str] = None,
        filter_name: str = "",
    ) -> dict:
        """Apply one temporary radio profile without changing the original script file."""

        module = self._require_module()
        team_upper = team.upper()
        core_target = self._normalize_target(target)
        if team_upper not in ("RED", "BLUE"):
            raise ValueError(f"invalid team: {team}")
        rescue_upper = (rescue or "").upper()
        if rescue_upper in ("", "NONE", "NORMAL", "INFO"):
            rescue_upper = ""
        if rescue_upper and rescue_upper not in ("L2", "L3"):
            raise ValueError(f"invalid rescue profile: {rescue}")

        with self.lock:
            params = getattr(module, "RADAR_PARAMS", None)
            if not isinstance(params, dict):
                raise ReceiverCoreLoadError("original receiver has no RADAR_PARAMS dict")
            if team_upper not in params or core_target not in params[team_upper]:
                raise ValueError(f"cannot configure unknown target {team_upper}-{core_target}")

            state = getattr(module, "STATE", {})
            stats = state.setdefault("STATS", {})
            entry = params[team_upper][core_target]
            base_freq = self._base_radio_value(entry, "freq")
            base_gain = int(entry.get("gain", 40))
            base_rf_bw = int(entry.get("rf_bw", 540_000))
            profile_gain = int(gain if gain is not None else base_gain)
            profile_rf_bw = int(rf_bw if rf_bw is not None else base_rf_bw)
            profile_freq = int(base_freq) + int(freq_offset_hz)
            entry["freq"] = int(base_freq)

            module.TUNE_CFG["TEAM"] = team_upper
            module.TUNE_CFG["TARGET"] = core_target

            if rescue_upper:
                filter_params = self._rescue_filter_params(module, rescue_upper, filter_name)
                filter_label = filter_name or ("hist248" if rescue_upper == "L2" else "l3tight")
                profile = {
                    "rescue": rescue_upper,
                    "offset": int(freq_offset_hz),
                    "gain": profile_gain,
                    "rf_bw": profile_rf_bw,
                    "filter_name": filter_label,
                    "filter_params": dict(filter_params),
                    "label": (
                        f"wrapper_{team_upper}_info_l{rescue_upper[-1]}_"
                        f"{int(freq_offset_hz / 1000)}k_g{profile_gain}_bw{int(profile_rf_bw / 1000)}"
                    ),
                }
                state["INFO_L2_RESCUE"] = rescue_upper == "L2"
                state["INFO_L3_RESCUE"] = rescue_upper == "L3"
                state["CAL_PROFILE"] = profile
            else:
                filter_label = filter_name or "normal"
                filter_params = self._normal_filter_params(module, core_target, filter_label)
                state["INFO_L2_RESCUE"] = False
                state["INFO_L3_RESCUE"] = False
                entry["freq"] = int(base_freq)
                state.setdefault("MANUAL_RX_GAINS", {})[core_target] = profile_gain
                profile = {
                    "rescue": None,
                    "offset": int(freq_offset_hz),
                    "gain": profile_gain,
                    "rf_bw": profile_rf_bw,
                    "filter_name": filter_label,
                    "filter_params": filter_params,
                    "label": (
                        f"wrapper_{team_upper}_info_{filter_label}_"
                        f"{int(freq_offset_hz / 1000)}k_g{profile_gain}_bw{int(profile_rf_bw / 1000)}"
                    ),
                }
                state["CAL_PROFILE"] = profile

            stats["GAIN_CEILING"] = profile_gain
            stats["LAST_ERROR"] = f"wrapper sweep profile {profile['label']}"
            self._mark_dirty_locked()
            return copy.deepcopy(profile)

    def apply_frequency_offset(self, team: str, target: str, offset_hz: int) -> None:
        module = self._require_module()
        team_upper = team.upper()
        core_target = self._normalize_target(target)
        if not offset_hz:
            return
        with self.lock:
            params = getattr(module, "RADAR_PARAMS", None)
            if not isinstance(params, dict):
                raise ReceiverCoreLoadError("original receiver has no RADAR_PARAMS dict")
            if team_upper not in params or core_target not in params[team_upper]:
                raise ValueError(f"cannot offset unknown target {team_upper}-{core_target}")
            entry = params[team_upper][core_target]
            base_key = "_wrapper_base_freq"
            if base_key not in entry:
                entry[base_key] = int(entry["freq"])
            entry["freq"] = int(entry[base_key]) + int(offset_hz)
            state = getattr(module, "STATE", {})
            stats = state.setdefault("STATS", {})
            stats["LAST_ERROR"] = (
                f"wrapper freq offset {team_upper}-{core_target}={int(offset_hz)}Hz "
                f"lo={entry['freq'] / 1e6:.6f}MHz"
            )
            self._mark_dirty_locked()

    def get_protocol_stats_snapshot(self) -> dict:
        module = self._require_module()
        with self.lock:
            state = getattr(module, "STATE", {})
            stats = state.get("STATS", {})
            keys = (
                "ADC_RMS",
                "AC_RAW",
                "AC",
                "HDR_DROP",
                "SOF",
                "CRC8",
                "CRC16",
                "CRC16_ALT",
                "CRC16_FAIL",
                "CRC16_FIX",
                "LEN_DROP",
                "CMD_DROP",
                "FRAME_REJECT",
                "FRAME_PENDING",
                "ASM_CHUNKS",
                "ASM_CRC16",
                "SOFT_AC_RAW",
                "SOFT_AC",
                "SOFT_SOF",
                "SOFT_CRC8",
                "SOFT_CRC16",
                "WEAK_SOFT_SIGMA",
                "WEAK_SOFT_CORR",
                "WEAK_SOFT_HARD_MIN_ERRORS",
                "WEAK_SOFT_PEAK_RECORDS",
                "WEAK_SOFT_MIN_SIGMA",
                "WEAK_SOFT_AC_ERRORS",
                "WEAK_SOFT_HEADER_ERRORS",
                "WEAK_SOFT_STATUS",
                "DEMOD_MS",
                "LOOP_MS",
                "RX_MS",
                "RF_STATE",
                "LAST_CRC16_CMD",
                "LAST_CRC16_MODE",
                "LAST_CFG_LOG",
                "LAST_ERROR",
                "LAST_DATA_CHANGE",
                "LAST_CFG_TIME",
            )
            return {key: copy.deepcopy(stats.get(key, 0)) for key in keys}

    def get_core_config_snapshot(self) -> dict:
        module = self._require_module()
        with self.lock:
            return {
                "sample_rate": int(getattr(module, "SDR_FS", 0) or 0),
                "rx_buffer_size": int(getattr(module, "RX_BUFFER_SIZE", 0) or 0),
                "sps": int(getattr(module, "SPS", 0) or 0),
                "symbol_rate": float(getattr(module, "SYMBOL_RATE", 0.0) or 0.0),
            }

    def get_current_radio_snapshot(self) -> dict:
        module = self._require_module()
        with self.lock:
            cfg = getattr(module, "TUNE_CFG", {})
            team = cfg.get("TEAM")
            target = cfg.get("TARGET")
            params = {}
            get_effective = getattr(module, "get_effective_radio_params", None)
            if callable(get_effective):
                params = dict(get_effective(team, target))
            else:
                radar_params = getattr(module, "RADAR_PARAMS", {})
                params = dict(radar_params.get(team, {}).get(target, {}))
                if params and "freq" in params:
                    params["base_freq"] = int(params["freq"])
                    params["rx_lo"] = int(params["freq"])
                    params["lo_offset"] = 0
                    params["digital_shift"] = 0
            return {
                "team": team,
                "target": target,
                "base_freq_hz": int(params.get("base_freq", params.get("freq", 0)) or 0),
                "rx_lo_hz": int(params.get("rx_lo", params.get("freq", 0)) or 0),
                "lo_offset_hz": int(params.get("lo_offset", 0) or 0),
                "digital_shift_hz": int(params.get("digital_shift", 0) or 0),
                "rf_bandwidth_hz": int(params.get("rf_bw", 0) or 0),
                "rx_gain": int(params.get("gain", 0) or 0),
                "mode": str(params.get("mode", "")),
            }

    def get_stats_snapshot(self) -> dict:
        module = self._require_module()
        with self.lock:
            state = getattr(module, "STATE", {})
            cfg = getattr(module, "TUNE_CFG", {})
            stats = state.get("STATS", {})
            return {
                "team": cfg.get("TEAM"),
                "target": cfg.get("TARGET"),
                "encrypt_level": state.get("ENCRYPT_LVL"),
                "jam_keys": copy.deepcopy(state.get("JAM_KEYS", {})),
                "jam_key_counts": copy.deepcopy(state.get("JAM_KEYS_CNT", {})),
                "rf_state": stats.get("RF_STATE"),
                "rf_advice": stats.get("RF_ADVICE"),
                "last_error": stats.get("LAST_ERROR"),
                "last_crc16_cmd": stats.get("LAST_CRC16_CMD"),
                "last_crc16_mode": stats.get("LAST_CRC16_MODE"),
                "last_frame_source": stats.get("LAST_FRAME_SOURCE"),
                "last_frame_seq": stats.get("LAST_FRAME_SEQ"),
                "crc16_alt": stats.get("CRC16_ALT"),
                "jam_rf_gate_mode": stats.get("JAM_RF_GATE_MODE"),
                "jam_rf_gate_reason": stats.get("JAM_RF_GATE_REASON"),
                "loop_ms": stats.get("LOOP_MS"),
                "rx_ms": stats.get("RX_MS"),
                "demod_ms": stats.get("DEMOD_MS"),
                "adc_rms": stats.get("ADC_RMS"),
                "rx_gain": stats.get("RX_GAIN"),
                "gain_ceiling": stats.get("GAIN_CEILING"),
                "iq_source": None if self.iq_source_config is None else copy.deepcopy(self.iq_source_config),
            }

    def start(self) -> None:
        module = self._require_module()
        with self.lock:
            if self.receiver_thread is not None and self.receiver_thread.is_alive():
                return
            self.stop_event.clear()
            self.receiver_exception = None
            self.receiver_thread = threading.Thread(
                target=self._run_main,
                args=(module,),
                name="sdr_receiver_v67_core",
                daemon=True,
            )
            self.receiver_thread.start()

    def stop(self, *, timeout_sec: float = 3.0) -> None:
        self.stop_event.set()
        thread = self.receiver_thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=timeout_sec)

    def _run_main(self, module: ModuleType) -> None:
        try:
            self._install_iq_file_source_if_requested(module)
            module.main()
        except BaseException as exc:  # pragma: no cover - hardware path.
            self.receiver_exception = exc
            self.logger(f"receiver core exited with exception: {exc}")

    def _install_iq_file_source_if_requested(self, module: ModuleType) -> None:
        config = self.iq_source_config
        if not config:
            return
        self._apply_iq_source_sample_rate(module, int(config.get("sample_rate_hz", 0) or 0))

        adi_module = getattr(module, "adi", None)
        if adi_module is None:
            adi_module = types.SimpleNamespace()
            setattr(module, "adi", adi_module)

        def pluto_factory(*_args, **_kwargs):
            return IqFilePluto(
                config["path"],
                loop=bool(config["loop"]),
                throttle=bool(config["throttle"]),
                center_hz=float(config["center_hz"]),
                start_offset_sec=float(config["start_offset_sec"]),
                logger=self.logger,
            )

        setattr(adi_module, "Pluto", pluto_factory)
        self.logger(
            "receiver core will use IQ file source instead of hardware: "
            f"{config['path']}"
        )

    def _apply_iq_source_sample_rate(self, module: ModuleType, sample_rate_hz: int) -> None:
        if sample_rate_hz <= 0:
            return
        old_rate = int(getattr(module, "SDR_FS", sample_rate_hz) or sample_rate_hz)
        setattr(module, "SDR_FS", int(sample_rate_hz))
        tx_sample_rate = float(getattr(module, "TX_SAMPLE_RATE", 1_000_000.0))
        tx_sps = float(getattr(module, "TX_SPS", 52.0))
        symbol_rate = tx_sample_rate / tx_sps if tx_sps else float(getattr(module, "SYMBOL_RATE", 0.0) or 0.0)
        if symbol_rate > 0.0:
            setattr(module, "SPS", int(round(float(sample_rate_hz) / symbol_rate)))
        stats = getattr(module, "STATE", {}).setdefault("STATS", {})
        stats["LAST_ERROR"] = f"wrapper IQ source sample rate {old_rate}->{int(sample_rate_hz)}"
        self.logger(f"IQ source sample rate override: {old_rate}->{int(sample_rate_hz)}")

    def _require_module(self) -> ModuleType:
        if self.module is None:
            raise ReceiverCoreLoadError("receiver core has not been loaded")
        return self.module

    def _mark_dirty_locked(self) -> None:
        module = self._require_module()
        if hasattr(module, "mark_sdr_config_dirty"):
            module.mark_sdr_config_dirty()
        elif hasattr(module, "LAST_SDR_CFG"):
            module.LAST_SDR_CFG["KEY"] = None

    @staticmethod
    def _base_radio_value(entry: dict, key: str) -> int:
        base_key = f"_wrapper_base_{key}"
        if base_key not in entry:
            entry[base_key] = int(entry[key])
        return int(entry[base_key])

    @staticmethod
    def _rescue_filter_params(module: ModuleType, rescue: str, filter_name: str) -> dict:
        if rescue == "L2":
            profiles = getattr(module, "INFO_L2_RESCUE_FILTER_PROFILES", {})
            default_name = "hist248"
        else:
            profiles = getattr(module, "INFO_L3_RESCUE_FILTER_PROFILES", {})
            default_name = "l3tight"
        selected_name = filter_name or default_name
        if selected_name not in profiles:
            available = ", ".join(sorted(str(name) for name in profiles)) or "none"
            raise ValueError(
                f"unknown {rescue} filter profile {selected_name!r}; available: {available}"
            )
        return dict(profiles[selected_name])

    @staticmethod
    def _normal_filter_params(module: ModuleType, target: str, filter_name: str) -> dict:
        profiles = getattr(module, "FILTER_PARAMS", {})
        if target not in profiles:
            raise ValueError(f"unknown normal filter target: {target}")
        base = dict(profiles[target])
        name = (filter_name or "normal").lower()
        notch = False
        if name.endswith("_notch"):
            notch = True
            name = name[: -len("_notch")]
        if name == "normal":
            tuned = base
            if notch:
                tuned = dict(tuned)
                tuned.update({"adaptive_notch": True, "notch_max_bins": 8, "notch_threshold_db": 22.0})
            return tuned
        if target != "INFO":
            raise ValueError(f"normal filter variant {filter_name!r} is only supported for INFO")

        variants = {
            "loose3": {"max_ac_errors": 3},
            "loose4": {"max_ac_errors": 4},
            "loose10": {
                "max_ac_errors": 10,
                "weak_ac_max_errors": 10,
                "weak_header_max_errors": 8,
            },
            "wide_loose3": {
                "pass_low": -315_000.0,
                "pass_high": 360_000.0,
                "stop_low": -365_000.0,
                "stop_high": 455_000.0,
                "max_ac_errors": 3,
            },
            "wide_loose10": {
                "pass_low": -315_000.0,
                "pass_high": 360_000.0,
                "stop_low": -365_000.0,
                "stop_high": 455_000.0,
                "max_ac_errors": 10,
                "weak_ac_max_errors": 10,
                "weak_header_max_errors": 8,
            },
            "tight_loose3": {
                "pass_low": -220_000.0,
                "pass_high": 280_000.0,
                "stop_low": -260_000.0,
                "stop_high": 340_000.0,
                "max_ac_errors": 3,
            },
            "tight_loose10": {
                "pass_low": -220_000.0,
                "pass_high": 280_000.0,
                "stop_low": -260_000.0,
                "stop_high": 340_000.0,
                "max_ac_errors": 10,
                "weak_ac_max_errors": 10,
                "weak_header_max_errors": 8,
            },
            "weak_soft": {
                "max_ac_errors": 10,
                "weak_ac_max_errors": 10,
                "weak_header_max_errors": 8,
                "weak_soft_ac": True,
                "weak_soft_min_sigma": 5.2,
                "weak_soft_ac_max_errors": 18,
                "weak_soft_header_max_errors": 20,
                "weak_soft_max_candidates": 2,
            },
            "wide_weak_soft": {
                "pass_low": -315_000.0,
                "pass_high": 360_000.0,
                "stop_low": -365_000.0,
                "stop_high": 455_000.0,
                "max_ac_errors": 10,
                "weak_ac_max_errors": 10,
                "weak_header_max_errors": 8,
                "weak_soft_ac": True,
                "weak_soft_min_sigma": 5.2,
                "weak_soft_ac_max_errors": 18,
                "weak_soft_header_max_errors": 20,
                "weak_soft_max_candidates": 2,
            },
            "tight_weak_soft": {
                "pass_low": -220_000.0,
                "pass_high": 280_000.0,
                "stop_low": -260_000.0,
                "stop_high": 340_000.0,
                "max_ac_errors": 10,
                "weak_ac_max_errors": 10,
                "weak_header_max_errors": 8,
                "weak_soft_ac": True,
                "weak_soft_min_sigma": 5.2,
                "weak_soft_ac_max_errors": 18,
                "weak_soft_header_max_errors": 20,
                "weak_soft_max_candidates": 2,
            },
        }
        if name not in variants:
            available = ", ".join(["normal", *sorted(variants), "normal_notch", *[f"{item}_notch" for item in sorted(variants)]])
            raise ValueError(f"unknown INFO filter profile {filter_name!r}; available: {available}")
        tuned = dict(base)
        tuned.update(variants[name])
        if notch:
            tuned.update({"adaptive_notch": True, "notch_max_bins": 8, "notch_threshold_db": 22.0})
        return tuned

    @staticmethod
    def _normalize_target(target: str) -> str:
        normalized = target.upper()
        aliases = {
            "JAM_L1_KEY": "L1",
            "JAM_L2_KEY": "L2",
            "JAM_L3_KEY": "L3",
            "INFO_UNDER_L1": "INFO",
            "INFO_UNDER_L2": "INFO",
            "INFO_UNDER_L3": "INFO",
        }
        normalized = aliases.get(normalized, normalized)
        if normalized not in ("INFO", "L1", "L2", "L3"):
            raise ValueError(f"invalid receiver target: {target}")
        return normalized
