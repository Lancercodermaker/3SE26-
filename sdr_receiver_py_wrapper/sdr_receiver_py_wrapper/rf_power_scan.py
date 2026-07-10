from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import time

import numpy as np


DEFAULT_FREQS_HZ = {
    "RED_INFO": 433_200_000,
    "BLUE_INFO": 433_920_000,
    "RED_L1": 432_200_000,
    "RED_L2": 432_500_000,
    "RED_L3": 432_800_000,
    "BLUE_L1": 434_920_000,
    "BLUE_L2": 434_620_000,
    "BLUE_L3": 434_320_000,
}


def _dbfs(power: float) -> float:
    return 10.0 * math.log10(max(float(power), 1e-18))


def _parse_frequency(value: str) -> int:
    text = value.strip().lower().replace("_", "")
    scale = 1.0
    if text.endswith("mhz"):
        scale = 1e6
        text = text[:-3]
    elif text.endswith("khz"):
        scale = 1e3
        text = text[:-3]
    elif text.endswith("hz"):
        text = text[:-2]
    return int(float(text) * scale)


def _frequency_list(args) -> list[tuple[str, int]]:
    if args.red_info:
        return [("RED_INFO", DEFAULT_FREQS_HZ["RED_INFO"])]
    if args.blue_info:
        return [("BLUE_INFO", DEFAULT_FREQS_HZ["BLUE_INFO"])]
    if args.all_known:
        return list(DEFAULT_FREQS_HZ.items())
    if args.freq:
        return [(args.label or "CUSTOM", _parse_frequency(args.freq))]
    return [("RED_INFO", DEFAULT_FREQS_HZ["RED_INFO"])]


def _measure(sdr, sample_rate: int, buffer_size: int, dwell: int, span_hz: float) -> dict:
    best = None
    rms_values = []
    for _ in range(max(1, dwell)):
        samples = np.asarray(sdr.rx(), dtype=np.complex64)
        samples = samples - np.mean(samples)
        rms_raw = float(np.sqrt(np.mean(np.abs(samples) ** 2)))
        peak_raw = float(np.max(np.abs(samples)))
        rms = rms_raw / 2048.0
        peak = peak_raw / 2048.0
        rms_values.append(rms)

        n = min(len(samples), buffer_size)
        window = np.hanning(n).astype(np.float32)
        spec = np.abs(np.fft.fftshift(np.fft.fft(samples[:n] * window))) ** 2
        freqs = np.fft.fftshift(np.fft.fftfreq(n, d=1.0 / sample_rate))
        mask = np.abs(freqs) <= span_hz / 2.0
        if not np.any(mask):
            mask = slice(None)
        idx_rel = int(np.argmax(spec[mask]))
        masked_freqs = freqs[mask]
        masked_spec = spec[mask]
        peak_power = float(masked_spec[idx_rel])
        median_power = float(np.median(masked_spec))
        candidate = {
            "rms": rms,
            "peak": peak,
            "peak_offset_hz": float(masked_freqs[idx_rel]),
            "peak_power_db": _dbfs(peak_power),
            "median_power_db": _dbfs(median_power),
            "snr_like_db": 10.0 * math.log10(max(peak_power, 1e-18) / max(median_power, 1e-18)),
        }
        if best is None or candidate["snr_like_db"] > best["snr_like_db"]:
            best = candidate
        time.sleep(0.02)
    assert best is not None
    best["rms_avg"] = float(np.mean(rms_values))
    return best


def _load_baseline(path: str) -> dict:
    if not path:
        return {}
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return {item["label"]: item for item in data.get("results", [])}


def _write_results(path: str, args, results: list[dict]) -> None:
    if not path:
        return
    payload = {
        "created_at": time.time(),
        "gain": int(args.gain),
        "sample_rate": int(args.sample_rate),
        "buffer_size": int(args.buffer_size),
        "rf_bw": int(args.rf_bw),
        "span_hz": float(args.span_hz),
        "results": results,
    }
    Path(path).write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Measure rough SDR power around INFO/JAM frequencies.")
    parser.add_argument("--uri", default="ip:192.168.2.1")
    parser.add_argument("--sample-rate", type=int, default=2_500_000)
    parser.add_argument("--buffer-size", type=int, default=160_000)
    parser.add_argument("--gain", type=int, default=60)
    parser.add_argument("--rf-bw", type=int, default=1_500_000)
    parser.add_argument("--dwell", type=int, default=4, help="RX buffers per frequency")
    parser.add_argument("--span-hz", type=float, default=1_500_000.0)
    parser.add_argument("--freq", help="Custom center frequency, for example 433.2MHz")
    parser.add_argument("--label", default="")
    parser.add_argument("--red-info", action="store_true")
    parser.add_argument("--blue-info", action="store_true")
    parser.add_argument("--all-known", action="store_true")
    parser.add_argument("--save-json", default="", help="Save scan results as baseline/comparison JSON.")
    parser.add_argument("--baseline-json", default="", help="Compare against a previous --save-json result.")
    args = parser.parse_args()

    import adi

    sdr = adi.Pluto(args.uri)
    sdr.sample_rate = int(args.sample_rate)
    sdr.rx_buffer_size = int(args.buffer_size)
    sdr.gain_control_mode_chan0 = "manual"
    sdr.rx_hardwaregain_chan0 = int(args.gain)
    try:
        sdr.rx_rf_bandwidth = int(args.rf_bw)
    except Exception:
        pass
    try:
        sdr.filter = ""
    except Exception:
        pass

    baseline = _load_baseline(args.baseline_json)
    results = []
    header = [
        "label",
        "center_mhz",
        "gain",
        "rms_avg",
        "rms_last",
        "adc_peak",
        "peak_offset_khz",
        "peak_power_db",
        "median_power_db",
        "snr_like_db",
    ]
    if baseline:
        header.extend(["delta_rms_avg", "delta_peak_db", "delta_snr_like_db"])
    print(",".join(header))
    for label, freq_hz in _frequency_list(args):
        sdr.rx_lo = int(freq_hz)
        time.sleep(0.08)
        result = _measure(
            sdr,
            sample_rate=int(args.sample_rate),
            buffer_size=int(args.buffer_size),
            dwell=int(args.dwell),
            span_hz=float(args.span_hz),
        )
        row = {
            "label": label,
            "center_hz": int(freq_hz),
            "center_mhz": freq_hz / 1e6,
            "gain": int(args.gain),
            "rms_avg": result["rms_avg"],
            "rms_last": result["rms"],
            "adc_peak": result["peak"],
            "peak_offset_khz": result["peak_offset_hz"] / 1e3,
            "peak_power_db": result["peak_power_db"],
            "median_power_db": result["median_power_db"],
            "snr_like_db": result["snr_like_db"],
        }
        base = baseline.get(label)
        if base:
            row["delta_rms_avg"] = row["rms_avg"] - float(base.get("rms_avg", 0.0))
            row["delta_peak_db"] = row["peak_power_db"] - float(base.get("peak_power_db", 0.0))
            row["delta_snr_like_db"] = row["snr_like_db"] - float(base.get("snr_like_db", 0.0))
        results.append(row)

        values = [
            label,
            f"{row['center_mhz']:.6f}",
            str(row["gain"]),
            f"{row['rms_avg']:.6f}",
            f"{row['rms_last']:.6f}",
            f"{row['adc_peak']:.6f}",
            f"{row['peak_offset_khz']:.1f}",
            f"{row['peak_power_db']:.2f}",
            f"{row['median_power_db']:.2f}",
            f"{row['snr_like_db']:.2f}",
        ]
        if base:
            values.extend(
                [
                    f"{row['delta_rms_avg']:.6f}",
                    f"{row['delta_peak_db']:.2f}",
                    f"{row['delta_snr_like_db']:.2f}",
                ]
            )
        print(",".join(values))

    _write_results(args.save_json, args, results)


if __name__ == "__main__":
    main()
