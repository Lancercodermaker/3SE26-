from __future__ import annotations

import argparse
import csv
import json
import math
import os
from pathlib import Path
import tempfile
import time
from typing import Iterable

import numpy as np

from .original_receiver_adapter import ReceiverCoreAdapter
from .patches import PatchCallbacks


PROFILE_ALIASES = {
    "INFO": ("INFO", "", "normal"),
    "INFO_NORMAL": ("INFO", "", "normal"),
    "INFO-L2": ("INFO", "L2", "hist248"),
    "INFO_L2": ("INFO", "L2", "hist248"),
    "INFO+L2": ("INFO", "L2", "hist248"),
    "INFO-L3": ("INFO", "L3", "l3tight"),
    "INFO_L3": ("INFO", "L3", "l3tight"),
    "INFO+L3": ("INFO", "L3", "l3tight"),
}

PLUTO_RX_HARDWARE_GAIN_MAX = 73


class SweepIqRecorder:
    def __init__(self, args) -> None:
        self.record_dir = Path(os.path.expandvars(str(args.iq_record_dir))).expanduser()
        self.prefix = "".join(ch if ch.isalnum() or ch in ("-", "_") else "_" for ch in str(args.iq_record_prefix))
        self.max_sec = float(args.iq_record_max_sec)
        self.max_bytes = int(args.iq_record_max_bytes)
        self.every_n = max(1, int(args.iq_record_every_n))
        self.path = None
        self.meta_path = None
        self.handle = None
        self.start_wall = 0.0
        self.chunks_seen = 0
        self.chunks_written = 0
        self.samples_written = 0
        self.bytes_written = 0
        self.last_peak = 0.0
        self.last_rms = 0.0
        self.stopped_reason = ""

    def write(self, raw_iq) -> None:
        if self.stopped_reason:
            return
        self.chunks_seen += 1
        if (self.chunks_seen - 1) % self.every_n != 0:
            return
        arr = np.asarray(raw_iq, dtype=np.complex64)
        if arr.size == 0:
            return
        now = time.time()
        if self.handle is None:
            self._open(now)
        if self.max_sec > 0.0 and now - self.start_wall >= self.max_sec:
            self.stopped_reason = f"max_sec {self.max_sec:.1f} reached"
            self.close()
            return
        if self.max_bytes > 0 and self.bytes_written + arr.nbytes > self.max_bytes:
            self.stopped_reason = f"max_bytes {self.max_bytes} reached"
            self.close()
            return
        abs_arr = np.abs(arr)
        self.last_peak = float(np.max(abs_arr))
        self.last_rms = float(np.sqrt(np.mean(abs_arr ** 2)))
        self.handle.write(arr.tobytes(order="C"))
        self.handle.flush()
        self.chunks_written += 1
        self.samples_written += int(arr.size)
        self.bytes_written += int(arr.nbytes)

    def close(self) -> None:
        if self.handle is not None:
            self.handle.close()
            self.handle = None
        if self.meta_path is not None:
            self.meta_path.write_text(
                json.dumps(
                    {
                        "format": "numpy.complex64 little-endian interleaved IQ",
                        "iq_path": str(self.path),
                        "created_at": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(self.start_wall or time.time())),
                        "chunks_seen": self.chunks_seen,
                        "chunks_written": self.chunks_written,
                        "samples_written": self.samples_written,
                        "bytes_written": self.bytes_written,
                        "last_peak": self.last_peak,
                        "last_rms": self.last_rms,
                        "every_n": self.every_n,
                        "max_sec": self.max_sec,
                        "max_bytes": self.max_bytes,
                        "stopped_reason": self.stopped_reason,
                    },
                    indent=2,
                    sort_keys=True,
                ),
                encoding="utf-8",
            )

    def _open(self, now: float) -> None:
        self.record_dir.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y%m%d_%H%M%S", time.localtime(now))
        self.path = self.record_dir / f"{self.prefix}_{stamp}.c64"
        self.meta_path = self.record_dir / f"{self.prefix}_{stamp}.json"
        self.start_wall = now
        self.handle = self.path.open("wb")

CSV_FIELDS = [
    "rank",
    "profile",
    "team",
    "rescue",
    "filter",
    "gain",
    "rf_bw_khz",
    "offset_khz",
    "class",
    "score",
    "adc_rms",
    "ac_raw",
    "ac",
    "sof",
    "crc8",
    "crc16",
    "crc16_fail",
    "frame_reject",
    "soft_ac",
    "soft_sof",
    "soft_crc8",
    "soft_crc16",
    "soft_sigma",
    "soft_hard_min_errors",
    "soft_peak_records",
    "soft_status",
    "crc8_rate",
    "crc16_rate",
    "ac_admit_ratio",
    "rf_state",
    "last_cmd",
    "last_error",
]


def _parse_int_list(text: str) -> list[int]:
    values = []
    for item in str(text).replace(";", ",").split(","):
        item = item.strip()
        if not item:
            continue
        values.append(int(float(item)))
    return values


def _gain_candidates(args, *, warn: bool = True) -> list[int]:
    requested = _parse_int_list(args.gains)
    max_gain = min(int(args.gain_max_override), PLUTO_RX_HARDWARE_GAIN_MAX)
    values = []
    warned = False
    for gain in requested:
        clamped = min(int(gain), max_gain)
        if clamped != gain:
            warned = True
        if clamped not in values:
            values.append(clamped)
    if warned and warn:
        print(
            f"WARN,gain candidates above {max_gain} dB were clamped to {max_gain} dB",
            flush=True,
        )
    return values


def _parse_profile_list(text: str) -> list[tuple[str, str, str, str]]:
    profiles = []
    for raw in str(text).replace(";", ",").split(","):
        name = raw.strip().upper()
        if not name:
            continue
        if name not in PROFILE_ALIASES:
            available = ", ".join(sorted(PROFILE_ALIASES))
            raise ValueError(f"unknown profile {raw!r}; available: {available}")
        target, rescue, default_filter = PROFILE_ALIASES[name]
        profiles.append((name, target, rescue, default_filter))
    return profiles


def _filter_candidates(rescue: str, default_filter: str, args) -> list[str]:
    if not rescue:
        values = [item.strip() for item in args.info_filters.split(",") if item.strip()]
        return values or [default_filter or "normal"]
    if rescue == "L2":
        values = [item.strip() for item in args.l2_filters.split(",") if item.strip()]
    else:
        values = [item.strip() for item in args.l3_filters.split(",") if item.strip()]
    return values or [default_filter]


def _numeric_delta(end: dict, start: dict, key: str) -> int:
    return max(0, int(end.get(key, 0) or 0) - int(start.get(key, 0) or 0))


def _score_stats(stats: dict, dwell_sec: float) -> tuple[str, float, dict]:
    dwell = max(float(dwell_sec), 0.1)
    crc16 = int(stats.get("CRC16", 0) or 0)
    crc8 = int(stats.get("CRC8", 0) or 0)
    sof = int(stats.get("SOF", 0) or 0)
    ac = int(stats.get("AC", 0) or 0)
    ac_raw = int(stats.get("AC_RAW", 0) or 0)
    hdr_drop = int(stats.get("HDR_DROP", 0) or 0)
    crc16_fail = int(stats.get("CRC16_FAIL", 0) or 0)
    frame_reject = int(stats.get("FRAME_REJECT", 0) or 0)
    adc_rms = float(stats.get("ADC_RMS", 0.0) or 0.0)
    demod_ms = float(stats.get("DEMOD_MS", 0.0) or 0.0)

    crc16_rate = crc16 / dwell
    crc8_rate = crc8 / dwell
    crc16_crc8_ratio = crc16 / max(1.0, float(crc8))
    ac_admit_ratio = ac / max(1.0, float(ac_raw))

    if crc16 >= 2 and crc16_rate >= 0.15:
        class_name = "CRC16_LOCK"
    elif crc16 == 1:
        class_name = "CRC16_WEAK"
    elif crc8 > 0 and crc8_rate >= 0.40:
        class_name = "CRC8_STABLE"
    elif sof > 0:
        class_name = "SOF_ONLY"
    elif ac > 0 or ac_raw > 0:
        class_name = "AC_ONLY"
    else:
        class_name = "NO_LOCK"

    score = (
        (crc16_rate * 380.0 if crc16 >= 2 else crc16 * 45.0)
        + math.sqrt(max(0, crc8)) * 22.0
        + math.sqrt(max(0, sof)) * 8.0
        + crc16_crc8_ratio * 55.0
        + ac_admit_ratio * 35.0
        + min(ac, 60) * 0.65
        - hdr_drop * 1.8
        - crc16_fail * (4.5 if crc16 == 0 else 2.5)
        - frame_reject * 5.0
    )
    if crc16 == 1:
        score -= 90.0
    if 0.010 <= adc_rms <= 0.350:
        score += 25.0
    elif adc_rms > 0.350:
        score -= (adc_rms - 0.350) * 120.0
    elif adc_rms < 0.002:
        score -= (0.002 - adc_rms) * 160.0
    if demod_ms > 140.0:
        score -= (demod_ms - 140.0) * 0.05

    metrics = {
        "crc8_rate": crc8_rate,
        "crc16_rate": crc16_rate,
        "crc16_crc8_ratio": crc16_crc8_ratio,
        "ac_admit_ratio": ac_admit_ratio,
    }
    return class_name, round(score, 2), metrics


def _sort_key(row: dict) -> tuple:
    class_rank = {
        "CRC16_LOCK": 5,
        "CRC8_STABLE": 4,
        "CRC16_WEAK": 3,
        "SOF_ONLY": 2,
        "AC_ONLY": 1,
        "NO_LOCK": 0,
    }.get(str(row.get("class", "NO_LOCK")), 0)
    return (
        class_rank,
        int(row.get("crc16", 0) or 0),
        int(row.get("crc8", 0) or 0),
        int(row.get("sof", 0) or 0),
        -int(row.get("crc16_fail", 0) or 0),
        float(row.get("score", 0.0) or 0.0),
    )


def _candidate_count(args) -> int:
    count = 0
    for _name, _target, rescue, default_filter in _parse_profile_list(args.profiles):
        count += (
            len(_gain_candidates(args, warn=False))
            * len(_parse_int_list(args.rf_bws))
            * len(_parse_int_list(args.offsets_hz))
            * len(_filter_candidates(rescue, default_filter, args))
        )
    return count


def _candidate_iter(args) -> Iterable[dict]:
    gains = _gain_candidates(args)
    rf_bws = _parse_int_list(args.rf_bws)
    offsets = _parse_int_list(args.offsets_hz)
    for profile_name, target, rescue, default_filter in _parse_profile_list(args.profiles):
        for filter_name in _filter_candidates(rescue, default_filter, args):
            for rf_bw in rf_bws:
                for offset_hz in offsets:
                    for gain in gains:
                        yield {
                            "profile": profile_name,
                            "target": target,
                            "rescue": rescue,
                            "filter": filter_name,
                            "gain": int(gain),
                            "rf_bw": int(rf_bw),
                            "offset_hz": int(offset_hz),
                        }


def _measure_candidate(adapter: ReceiverCoreAdapter, candidate: dict, args) -> dict:
    adapter.set_radio_profile(
        team=args.team,
        target=candidate["target"],
        gain=candidate["gain"],
        rf_bw=candidate["rf_bw"],
        freq_offset_hz=candidate["offset_hz"],
        rescue=candidate["rescue"],
        filter_name=candidate["filter"],
    )
    time.sleep(max(0.0, float(args.settle_sec)))
    start = adapter.get_protocol_stats_snapshot()
    time.sleep(max(0.1, float(args.dwell_sec)))
    end = adapter.get_protocol_stats_snapshot()

    stats = {
        "ADC_RMS": float(end.get("ADC_RMS", 0.0) or 0.0),
        "AC_RAW": _numeric_delta(end, start, "AC_RAW"),
        "AC": _numeric_delta(end, start, "AC"),
        "HDR_DROP": _numeric_delta(end, start, "HDR_DROP"),
        "SOF": _numeric_delta(end, start, "SOF"),
        "CRC8": _numeric_delta(end, start, "CRC8"),
        "CRC16": _numeric_delta(end, start, "CRC16"),
        "CRC16_FAIL": _numeric_delta(end, start, "CRC16_FAIL"),
        "FRAME_REJECT": _numeric_delta(end, start, "FRAME_REJECT"),
        "FRAME_PENDING": _numeric_delta(end, start, "FRAME_PENDING"),
        "SOFT_AC": _numeric_delta(end, start, "SOFT_AC"),
        "SOFT_SOF": _numeric_delta(end, start, "SOFT_SOF"),
        "SOFT_CRC8": _numeric_delta(end, start, "SOFT_CRC8"),
        "SOFT_CRC16": _numeric_delta(end, start, "SOFT_CRC16"),
        "WEAK_SOFT_SIGMA": float(end.get("WEAK_SOFT_SIGMA", 0.0) or 0.0),
        "WEAK_SOFT_HARD_MIN_ERRORS": int(end.get("WEAK_SOFT_HARD_MIN_ERRORS", 0) or 0),
        "WEAK_SOFT_PEAK_RECORDS": int(end.get("WEAK_SOFT_PEAK_RECORDS", 0) or 0),
        "WEAK_SOFT_STATUS": str(end.get("WEAK_SOFT_STATUS", "")),
        "DEMOD_MS": float(end.get("DEMOD_MS", 0.0) or 0.0),
        "LOOP_MS": float(end.get("LOOP_MS", 0.0) or 0.0),
    }
    class_name, score, metrics = _score_stats(stats, float(args.dwell_sec))
    return {
        "profile": candidate["profile"],
        "team": args.team,
        "rescue": candidate["rescue"] or "normal",
        "filter": candidate["filter"] or "normal",
        "gain": candidate["gain"],
        "rf_bw_khz": int(candidate["rf_bw"] / 1000),
        "offset_khz": int(candidate["offset_hz"] / 1000),
        "class": class_name,
        "score": score,
        "adc_rms": stats["ADC_RMS"],
        "ac_raw": stats["AC_RAW"],
        "ac": stats["AC"],
        "sof": stats["SOF"],
        "crc8": stats["CRC8"],
        "crc16": stats["CRC16"],
        "crc16_fail": stats["CRC16_FAIL"],
        "frame_reject": stats["FRAME_REJECT"],
        "soft_ac": stats["SOFT_AC"],
        "soft_sof": stats["SOFT_SOF"],
        "soft_crc8": stats["SOFT_CRC8"],
        "soft_crc16": stats["SOFT_CRC16"],
        "soft_sigma": stats["WEAK_SOFT_SIGMA"],
        "soft_hard_min_errors": stats["WEAK_SOFT_HARD_MIN_ERRORS"],
        "soft_peak_records": stats["WEAK_SOFT_PEAK_RECORDS"],
        "soft_status": stats["WEAK_SOFT_STATUS"],
        "crc8_rate": metrics["crc8_rate"],
        "crc16_rate": metrics["crc16_rate"],
        "ac_admit_ratio": metrics["ac_admit_ratio"],
        "rf_state": str(end.get("RF_STATE", "")),
        "last_cmd": str(end.get("LAST_CRC16_CMD", "")),
        "last_error": str(end.get("LAST_ERROR", "")),
    }


def _format_value(value) -> str:
    if isinstance(value, float):
        return f"{value:.4f}"
    return str(value)


def _print_row(row: dict) -> None:
    print(",".join(_format_value(row.get(field, "")) for field in CSV_FIELDS), flush=True)


def _write_outputs(out_dir: Path, args, results: list[dict]) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    ranked = sorted(results, key=_sort_key, reverse=True)
    for index, row in enumerate(ranked, start=1):
        row["rank"] = index

    csv_path = out_dir / "adaptive_profile_sweep.csv"
    with csv_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        writer.writeheader()
        writer.writerows(ranked)

    summary = {
        "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "args": vars(args),
        "best": ranked[0] if ranked else None,
        "results": ranked,
    }
    (out_dir / "adaptive_profile_sweep.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True),
        encoding="utf-8",
    )

    if ranked:
        best = ranked[0]
        yaml_text = "\n".join(
            [
                "adaptive_profile:",
                f"  team: {best['team']}",
                f"  profile: {best['profile']}",
                f"  rescue: {best['rescue']}",
                f"  filter: {best['filter']}",
                f"  gain: {best['gain']}",
                f"  rf_bw_hz: {int(best['rf_bw_khz']) * 1000}",
                f"  freq_offset_hz: {int(best['offset_khz']) * 1000}",
                f"  class: {best['class']}",
                f"  score: {best['score']}",
                f"  adc_rms: {best['adc_rms']:.6f}",
                f"  ac: {best['ac']}",
                f"  sof: {best['sof']}",
                f"  crc8: {best['crc8']}",
                f"  crc16: {best['crc16']}",
                "",
            ]
        )
        (out_dir / "best_profile.yaml").write_text(yaml_text, encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Automated protocol-scored sweep for INFO/INFO-L2/INFO-L3 receiver profiles."
    )
    parser.add_argument("--team", choices=["RED", "BLUE"], default="RED")
    parser.add_argument("--profiles", default="INFO", help="Comma list: INFO,INFO_L2,INFO_L3")
    parser.add_argument("--gains", default="40,50,60,70,73")
    parser.add_argument("--rf-bws", default="160000,220000,300000,420000,540000")
    parser.add_argument("--offsets-hz", default="0,-80000,80000,-150000,150000,-250000,250000")
    parser.add_argument("--l2-filters", default="hist248,hist255,wide263")
    parser.add_argument("--l3-filters", default="l3tight,l3cur")
    parser.add_argument("--info-filters", default="normal,loose3")
    parser.add_argument("--dwell-sec", type=float, default=2.0)
    parser.add_argument("--settle-sec", type=float, default=0.45)
    parser.add_argument("--original-script-path", default="auto")
    parser.add_argument("--out-dir", default="")
    parser.add_argument("--limit", type=int, default=0, help="Stop after N candidates; useful for smoke tests.")
    parser.add_argument(
        "--gain-max-override",
        type=int,
        default=73,
        help="Runtime gain ceiling. Pluto/AD936x hardware max is 73 dB.",
    )
    parser.add_argument("--import-allow-adi-stub", action="store_true")
    parser.add_argument("--record-iq", action="store_true")
    parser.add_argument("--iq-record-dir", default=str(Path.home() / "sdr_iq_records"))
    parser.add_argument("--iq-record-prefix", default="adaptive_sweep")
    parser.add_argument("--iq-record-max-sec", type=float, default=0.0)
    parser.add_argument("--iq-record-max-bytes", type=int, default=0)
    parser.add_argument("--iq-record-every-n", type=int, default=1)
    args = parser.parse_args()

    total = _candidate_count(args)
    if args.limit > 0:
        total = min(total, int(args.limit))
    stamp = time.strftime("%Y%m%d_%H%M%S")
    out_dir = Path(args.out_dir or Path(tempfile.gettempdir()) / "adaptive_profile_sweep" / stamp)

    adapter = ReceiverCoreAdapter(args.original_script_path, logger=lambda message: print(f"LOG,{message}", flush=True))
    adapter.load(allow_adi_import_stub=bool(args.import_allow_adi_stub))
    if int(args.gain_max_override) > 0:
        adapter.set_rx_gain_max_override(int(args.gain_max_override))
    recorder = SweepIqRecorder(args) if args.record_iq else None
    adapter.apply_patches(
        run_mode="competition",
        callbacks=PatchCallbacks(on_raw_iq=recorder.write if recorder is not None else None),
    )
    adapter.start()

    print(",".join(CSV_FIELDS), flush=True)
    results = []
    try:
        for index, candidate in enumerate(_candidate_iter(args), start=1):
            if args.limit > 0 and index > args.limit:
                break
            if adapter.receiver_exception is not None:
                raise RuntimeError(f"receiver core exited: {adapter.receiver_exception}")
            row = _measure_candidate(adapter, candidate, args)
            row["rank"] = index
            results.append(row)
            _print_row(row)
            print(f"PROGRESS,{index}/{total}", flush=True)
    finally:
        adapter.stop()
        adapter.restore_patches()
        if recorder is not None:
            recorder.close()

    _write_outputs(out_dir, args, results)
    ranked = sorted(results, key=_sort_key, reverse=True)
    if ranked:
        best = ranked[0]
        print(
            "BEST,"
            f"{best['profile']},gain={best['gain']},rf_bw_khz={best['rf_bw_khz']},"
            f"offset_khz={best['offset_khz']},class={best['class']},score={best['score']}",
            flush=True,
        )
    print(f"OUT_DIR,{out_dir}", flush=True)


if __name__ == "__main__":
    main()
