from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Optional


class AdaptiveProfileLoadError(RuntimeError):
    """Raised when an adaptive sweep profile cannot be loaded."""


def load_adaptive_profile(profile_path: str) -> Optional[dict]:
    """Load the best profile emitted by adaptive_profile_sweep.

    The sweep writes a tiny YAML file so runtime does not need PyYAML. This
    loader also accepts a directory containing best_profile.yaml or the JSON
    summary from the same sweep output directory.
    """

    raw_path = str(profile_path or "").strip()
    if not raw_path:
        return None

    path = Path(os.path.expandvars(os.path.expanduser(raw_path)))
    if path.is_dir():
        path = path / "best_profile.yaml"
    if not path.is_file():
        raise AdaptiveProfileLoadError(f"adaptive profile file does not exist: {path}")

    if path.suffix.lower() == ".json":
        data = json.loads(path.read_text(encoding="utf-8"))
        row = data.get("best") or data.get("adaptive_profile") or data
    else:
        row = _parse_simple_adaptive_profile_yaml(path.read_text(encoding="utf-8"))

    return normalize_adaptive_profile(row, source_path=str(path))


def normalize_adaptive_profile(row: dict, *, source_path: str = "") -> dict:
    if not isinstance(row, dict):
        raise AdaptiveProfileLoadError("adaptive profile content is not a mapping")

    profile_name = _as_text(row.get("profile") or row.get("target") or "INFO").upper()
    profile_name = profile_name.replace("-", "_").replace("+", "_")
    rescue = _as_text(row.get("rescue") or "")
    if rescue.lower() in ("", "normal", "none", "null"):
        rescue = ""
    rescue = rescue.upper()
    if profile_name in ("INFO_L2", "INFO_L2_RESCUE"):
        rescue = rescue or "L2"
    elif profile_name in ("INFO_L3", "INFO_L3_RESCUE"):
        rescue = rescue or "L3"

    supported = ("INFO", "INFO_NORMAL", "INFO_L2", "INFO_L3", "INFO_L2_RESCUE", "INFO_L3_RESCUE")
    if profile_name not in supported:
        raise AdaptiveProfileLoadError(f"unsupported adaptive profile target: {profile_name}")
    if rescue not in ("", "L2", "L3"):
        raise AdaptiveProfileLoadError(f"unsupported adaptive profile rescue mode: {rescue}")

    gain = _as_int(row.get("gain"), field="gain")
    rf_bw = _as_int(row.get("rf_bw_hz") or _khz_to_hz(row.get("rf_bw_khz")), field="rf_bw_hz")
    freq_offset = _as_int(
        row.get("freq_offset_hz") if row.get("freq_offset_hz") is not None else _khz_to_hz(row.get("offset_khz")),
        field="freq_offset_hz",
    )

    team = _as_text(row.get("team") or "").upper()
    if team and team not in ("RED", "BLUE"):
        raise AdaptiveProfileLoadError(f"unsupported adaptive profile team: {team}")

    filter_name = _as_text(row.get("filter") or "normal")
    return {
        "team": team,
        "profile": "INFO" if rescue == "" else f"INFO_{rescue}",
        "target": "INFO",
        "rescue": rescue,
        "filter": filter_name,
        "gain": gain,
        "rf_bw_hz": rf_bw,
        "freq_offset_hz": freq_offset,
        "class": _as_text(row.get("class") or ""),
        "score": _as_float(row.get("score"), default=0.0),
        "source_path": source_path,
    }


def _parse_simple_adaptive_profile_yaml(text: str) -> dict:
    values: dict[str, Any] = {}
    in_profile = False
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped == "adaptive_profile:":
            in_profile = True
            continue
        if not in_profile:
            continue
        if not line.startswith((" ", "\t")):
            break
        if ":" not in stripped:
            continue
        key, value = stripped.split(":", 1)
        values[key.strip()] = _parse_scalar(value.strip())
    if not values:
        raise AdaptiveProfileLoadError("adaptive_profile section is empty or missing")
    return values


def _parse_scalar(value: str):
    if value in ("", "null", "Null", "NULL", "~"):
        return ""
    value = value.strip().strip("'\"")
    try:
        if any(ch in value for ch in (".", "e", "E")):
            return float(value)
        return int(value)
    except ValueError:
        return value


def _as_text(value) -> str:
    return str(value or "").strip()


def _as_int(value, *, field: str) -> int:
    if value is None or value == "":
        raise AdaptiveProfileLoadError(f"adaptive profile missing required field: {field}")
    return int(float(value))


def _as_float(value, *, default: float) -> float:
    if value is None or value == "":
        return default
    return float(value)


def _khz_to_hz(value):
    if value is None or value == "":
        return None
    return int(float(value) * 1000)
