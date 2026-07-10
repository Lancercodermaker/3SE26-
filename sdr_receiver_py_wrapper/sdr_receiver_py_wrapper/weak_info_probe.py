from __future__ import annotations

import argparse
import csv
import json
import math
from pathlib import Path
import tempfile
import time
from typing import Iterable

import numpy as np


AC_INFO = "0010111101101111010011000111010010111001000101000100100100101110"
TX_SAMPLE_RATE = 1_000_000.0
TX_SPS = 52.0
SYMBOL_RATE = TX_SAMPLE_RATE / TX_SPS
PLUTO_RX_HARDWARE_GAIN_MAX = 73

DEFAULT_INFO_FREQS_HZ = {
    "RED": 433_200_000,
    "BLUE": 433_920_000,
}

INFO_FILTERS = {
    "normal": {
        "kind": "asym_fft",
        "pass_low": -263_000.0,
        "pass_high": 315_000.0,
        "stop_low": -296_000.0,
        "stop_high": 405_000.0,
        "smooth_frac": 0.34,
        "trend_bits": 16,
        "max_ac_errors": 2,
    },
    "loose3": {
        "kind": "asym_fft",
        "pass_low": -263_000.0,
        "pass_high": 315_000.0,
        "stop_low": -296_000.0,
        "stop_high": 405_000.0,
        "smooth_frac": 0.34,
        "trend_bits": 16,
        "max_ac_errors": 3,
    },
    "wide_loose3": {
        "kind": "asym_fft",
        "pass_low": -315_000.0,
        "pass_high": 360_000.0,
        "stop_low": -365_000.0,
        "stop_high": 455_000.0,
        "smooth_frac": 0.34,
        "trend_bits": 16,
        "max_ac_errors": 3,
    },
    "tight_loose3": {
        "kind": "asym_fft",
        "pass_low": -220_000.0,
        "pass_high": 280_000.0,
        "stop_low": -260_000.0,
        "stop_high": 340_000.0,
        "smooth_frac": 0.34,
        "trend_bits": 16,
        "max_ac_errors": 3,
    },
}

THRESHOLD_K_VALUES = (0.0, 0.35, -0.35, 0.2, -0.2, 0.1, -0.1)

CSV_FIELDS = [
    "rank",
    "idx",
    "team",
    "gain",
    "rf_bw_khz",
    "offset_khz",
    "filter",
    "notch",
    "adc_rms",
    "adc_peak",
    "band_snr_db",
    "peak_offset_khz",
    "peak_snr_db",
    "soft_corr",
    "soft_sigma",
    "soft_margin",
    "soft_shift",
    "soft_polarity",
    "soft_symbol_index",
    "hard_min_errors",
    "hard_hits_le3",
    "score",
]


def _db(value: float) -> float:
    return 10.0 * math.log10(max(float(value), 1e-18))


def _parse_int_list(text: str) -> list[int]:
    values = []
    for item in str(text).replace(";", ",").split(","):
        item = item.strip()
        if item:
            values.append(int(float(item)))
    return values


def _parse_str_list(text: str) -> list[str]:
    return [item.strip() for item in str(text).replace(";", ",").split(",") if item.strip()]


def _gain_candidates(text: str) -> list[int]:
    values = []
    warned = False
    for gain in _parse_int_list(text):
        clamped = min(int(gain), PLUTO_RX_HARDWARE_GAIN_MAX)
        warned = warned or clamped != gain
        if clamped not in values:
            values.append(clamped)
    if warned:
        print(
            f"WARN,gain candidates above {PLUTO_RX_HARDWARE_GAIN_MAX} dB were clamped",
            flush=True,
        )
    return values or [PLUTO_RX_HARDWARE_GAIN_MAX]


def _mirror_asym_filter_params(cfg: dict) -> dict:
    if cfg.get("kind") != "asym_fft":
        return dict(cfg)
    mirrored = dict(cfg)
    mirrored["pass_low"] = -float(cfg["pass_high"])
    mirrored["pass_high"] = -float(cfg["pass_low"])
    mirrored["stop_low"] = -float(cfg["stop_high"])
    mirrored["stop_high"] = -float(cfg["stop_low"])
    return mirrored


def _filter_params(name: str, team: str) -> dict:
    if name not in INFO_FILTERS:
        available = ", ".join(sorted(INFO_FILTERS))
        raise ValueError(f"unknown INFO filter {name!r}; available: {available}")
    cfg = dict(INFO_FILTERS[name])
    return _mirror_asym_filter_params(cfg) if team == "BLUE" else cfg


def _make_fft_mask(n: int, sample_rate: int, cfg: dict) -> np.ndarray:
    freqs = np.fft.fftfreq(n, d=1.0 / float(sample_rate))
    mask = np.zeros(n, dtype=np.float32)
    if cfg["kind"] == "sym_fft":
        cutoff = float(cfg["cutoff"])
        transition = float(cfg["transition"])
        abs_f = np.abs(freqs)
        mask[abs_f <= cutoff] = 1.0
        edge = (abs_f > cutoff) & (abs_f < cutoff + transition)
        mask[edge] = 0.5 * (1.0 + np.cos(np.pi * (abs_f[edge] - cutoff) / transition))
        return mask

    pass_low = float(cfg["pass_low"])
    pass_high = float(cfg["pass_high"])
    stop_low = float(cfg["stop_low"])
    stop_high = float(cfg["stop_high"])
    mask[(freqs >= pass_low) & (freqs <= pass_high)] = 1.0
    left = (freqs > stop_low) & (freqs < pass_low)
    mask[left] = 0.5 * (1.0 - np.cos(np.pi * (freqs[left] - stop_low) / (pass_low - stop_low)))
    right = (freqs > pass_high) & (freqs < stop_high)
    mask[right] = 0.5 * (1.0 + np.cos(np.pi * (freqs[right] - pass_high) / (stop_high - pass_high)))
    return mask


def _filter_iq(samples: np.ndarray, sample_rate: int, cfg: dict) -> np.ndarray:
    return np.fft.ifft(np.fft.fft(samples) * _make_fft_mask(len(samples), sample_rate, cfg))


def _moving_average(x: np.ndarray, n: int) -> np.ndarray:
    n = max(3, int(n))
    return np.convolve(x, np.ones(n, dtype=np.float64) / float(n), mode="same")


def _digital_shift(samples: np.ndarray, sample_rate: int, offset_hz: int) -> np.ndarray:
    if not offset_hz:
        return samples
    n = np.arange(len(samples), dtype=np.float64)
    return samples * np.exp(1j * 2.0 * np.pi * float(offset_hz) * n / float(sample_rate))


def _adaptive_notch(samples: np.ndarray, sample_rate: int, max_bins: int, threshold_db: float) -> np.ndarray:
    if max_bins <= 0:
        return samples
    n = len(samples)
    spec = np.fft.fft(samples)
    power = np.abs(spec) ** 2
    freqs = np.fft.fftfreq(n, d=1.0 / float(sample_rate))
    median = float(np.median(power))
    if median <= 0.0:
        return samples
    candidates = np.where((power > median * (10.0 ** (threshold_db / 10.0))) & (np.abs(freqs) > 5_000.0))[0]
    if candidates.size == 0:
        return samples
    ranked = candidates[np.argsort(power[candidates])[-max_bins:]]
    for idx in ranked:
        for delta in (-2, -1, 0, 1, 2):
            spec[(int(idx) + delta) % n] = 0.0
    return np.fft.ifft(spec)


def _threshold_grid(values: np.ndarray, k_values: Iterable[float]) -> list[tuple[float, float]]:
    mid = float(np.median(values))
    spread = float(np.percentile(values, 75) - np.percentile(values, 25))
    if spread < 1e-9:
        return [(0.0, mid)]
    return [(float(k), mid + float(k) * spread) for k in k_values]


def _soft_ac_metrics(smoothed: np.ndarray, sps: int, threshold_ks: Iterable[float]) -> dict:
    template = np.array([1.0 if bit == "1" else -1.0 for bit in AC_INFO], dtype=np.float64)
    ac_len = len(template)
    best = {
        "soft_corr": -999.0,
        "soft_sigma": 0.0,
        "soft_margin": 0.0,
        "soft_shift": 0,
        "soft_polarity": "+",
        "soft_symbol_index": -1,
        "hard_min_errors": ac_len,
        "hard_hits_le3": 0,
    }
    shift_step = max(1, sps // 32)
    for shift in range(0, sps, shift_step):
        symbols = smoothed[shift::sps]
        if len(symbols) < ac_len:
            continue
        mid = float(np.median(symbols))
        scale = float(np.percentile(symbols, 75) - np.percentile(symbols, 25))
        if scale < 1e-9:
            continue
        z = (symbols - mid) / scale
        for polarity in ("+", "-"):
            z_pol = z if polarity == "+" else -z
            corr = np.correlate(z_pol, template, mode="valid") / float(ac_len)
            if corr.size:
                idx = int(np.argmax(corr))
                corr_best = float(corr[idx])
                corr_std = float(np.std(corr)) or 1e-9
                corr_p99 = float(np.percentile(corr, 99.0))
                sigma = corr_best / corr_std
                margin = corr_best - corr_p99
                if sigma > best["soft_sigma"]:
                    best.update(
                        {
                            "soft_corr": corr_best,
                            "soft_sigma": sigma,
                            "soft_margin": margin,
                            "soft_shift": int(shift),
                            "soft_polarity": polarity,
                            "soft_symbol_index": idx,
                        }
                    )

            for _k, threshold in _threshold_grid(symbols, threshold_ks):
                bits = np.where(symbols > threshold, 1.0, -1.0)
                if polarity == "-":
                    bits = -bits
                dots = np.correlate(bits, template, mode="valid")
                if not dots.size:
                    continue
                errors = ((ac_len - dots) / 2.0).astype(np.int32)
                min_errors = int(np.min(errors))
                best["hard_min_errors"] = min(best["hard_min_errors"], min_errors)
                best["hard_hits_le3"] += int(np.sum(errors <= 3))
    if best["soft_corr"] < -100.0:
        best["soft_corr"] = 0.0
    return best


def _spectrum_metrics(samples: np.ndarray, sample_rate: int, cfg: dict, nfft: int) -> dict:
    n = min(len(samples), max(256, int(nfft)))
    x = samples[:n] - np.mean(samples[:n])
    window = np.hanning(n).astype(np.float32)
    spec = np.abs(np.fft.fftshift(np.fft.fft(x * window))) ** 2
    freqs = np.fft.fftshift(np.fft.fftfreq(n, d=1.0 / float(sample_rate)))
    pass_low = float(cfg.get("pass_low", -260_000.0))
    pass_high = float(cfg.get("pass_high", 315_000.0))
    signal_mask = (freqs >= pass_low) & (freqs <= pass_high)
    noise_mask = (np.abs(freqs) > max(abs(pass_low), abs(pass_high)) + 80_000.0) & (
        np.abs(freqs) < min(float(sample_rate) / 2.2, 900_000.0)
    )
    if not np.any(noise_mask):
        noise_mask = ~signal_mask
    signal_power = float(np.mean(spec[signal_mask])) if np.any(signal_mask) else 0.0
    noise_power = float(np.median(spec[noise_mask])) if np.any(noise_mask) else float(np.median(spec))
    peak_idx = int(np.argmax(spec[signal_mask])) if np.any(signal_mask) else int(np.argmax(spec))
    signal_freqs = freqs[signal_mask] if np.any(signal_mask) else freqs
    signal_spec = spec[signal_mask] if np.any(signal_mask) else spec
    peak_power = float(signal_spec[peak_idx])
    return {
        "band_snr_db": _db(signal_power / max(noise_power, 1e-18)),
        "peak_offset_hz": float(signal_freqs[peak_idx]),
        "peak_snr_db": _db(peak_power / max(noise_power, 1e-18)),
    }


def _analyze_capture(samples: np.ndarray, args, candidate: dict) -> dict:
    cfg = _filter_params(candidate["filter"], args.team)
    sps = int(round(float(args.sample_rate) / SYMBOL_RATE))
    threshold_ks = [float(value) for value in str(args.threshold_ks).split(",") if value.strip()]

    abs_samples = np.abs(samples)
    adc_rms = float(np.sqrt(np.mean(abs_samples**2))) / 2048.0
    adc_peak = float(np.max(abs_samples)) / 2048.0

    soft_rows = []
    spectrum_rows = []
    for row in samples:
        x = np.asarray(row, dtype=np.complex64)
        x = x - np.mean(x)
        shifted = _digital_shift(x, int(args.sample_rate), int(candidate["offset_hz"]))
        if candidate["notch"]:
            shifted = _adaptive_notch(
                shifted,
                int(args.sample_rate),
                int(args.notch_max_bins),
                float(args.notch_threshold_db),
            )
        spectrum_rows.append(_spectrum_metrics(shifted, int(args.sample_rate), cfg, int(args.nfft)))
        filtered = _filter_iq(shifted, int(args.sample_rate), cfg)
        raw_freq = np.angle(filtered[1:] * np.conj(filtered[:-1]))
        freq = raw_freq - np.median(raw_freq)
        trend_len = int(sps * float(cfg.get("trend_bits", 16)))
        if len(freq) > trend_len * 2:
            freq = freq - _moving_average(freq, trend_len)
        smooth_len = max(5, int(sps * float(cfg.get("smooth_frac", 0.34))))
        smoothed = _moving_average(freq, smooth_len)
        soft_rows.append(_soft_ac_metrics(smoothed, sps, threshold_ks))

    best_soft = max(soft_rows, key=lambda item: (item["soft_sigma"], -item["hard_min_errors"]))
    band_snr_db = float(np.mean([row["band_snr_db"] for row in spectrum_rows]))
    peak_row = max(spectrum_rows, key=lambda item: item["peak_snr_db"])
    hard_min_errors = min(row["hard_min_errors"] for row in soft_rows)
    hard_hits_le3 = sum(row["hard_hits_le3"] for row in soft_rows)
    score = (
        max(0.0, best_soft["soft_sigma"]) * 5.0
        + max(0.0, best_soft["soft_margin"]) * 60.0
        + max(0.0, 18.0 - float(hard_min_errors)) * 2.0
        + min(hard_hits_le3, 10) * 8.0
        + max(0.0, band_snr_db) * 2.0
        + max(0.0, peak_row["peak_snr_db"]) * 0.25
    )
    if adc_peak > 0.90 or adc_rms > 0.82:
        score -= 200.0
    return {
        "adc_rms": adc_rms,
        "adc_peak": adc_peak,
        "band_snr_db": band_snr_db,
        "peak_offset_khz": peak_row["peak_offset_hz"] / 1000.0,
        "peak_snr_db": peak_row["peak_snr_db"],
        "soft_corr": best_soft["soft_corr"],
        "soft_sigma": best_soft["soft_sigma"],
        "soft_margin": best_soft["soft_margin"],
        "soft_shift": best_soft["soft_shift"],
        "soft_polarity": best_soft["soft_polarity"],
        "soft_symbol_index": best_soft["soft_symbol_index"],
        "hard_min_errors": hard_min_errors,
        "hard_hits_le3": hard_hits_le3,
        "score": round(score, 3),
    }


def _candidate_iter(args) -> Iterable[dict]:
    for gain in _gain_candidates(args.gains):
        for rf_bw in _parse_int_list(args.rf_bws):
            for offset_hz in _parse_int_list(args.offsets_hz):
                for filter_name in _parse_str_list(args.info_filters):
                    for notch in _parse_str_list(args.notch_modes):
                        yield {
                            "gain": int(gain),
                            "rf_bw": int(rf_bw),
                            "offset_hz": int(offset_hz),
                            "filter": filter_name,
                            "notch": notch.lower() in ("on", "true", "1", "yes"),
                        }


def _configure_sdr(args):
    import adi

    sdr = adi.Pluto(args.uri)
    sdr.sample_rate = int(args.sample_rate)
    sdr.rx_buffer_size = int(args.buffer_size)
    sdr.gain_control_mode_chan0 = "manual"
    try:
        sdr.filter = ""
    except Exception:
        pass
    return sdr


def _capture_samples(sdr, captures: int, settle_sec: float) -> np.ndarray:
    time.sleep(max(0.0, float(settle_sec)))
    chunks = []
    for _ in range(max(1, int(captures))):
        chunks.append(np.asarray(sdr.rx(), dtype=np.complex64))
        time.sleep(0.02)
    return np.stack(chunks, axis=0)


def _apply_candidate_sdr(sdr, args, candidate: dict) -> None:
    center = DEFAULT_INFO_FREQS_HZ[args.team] + int(candidate["offset_hz"])
    sdr.rx_lo = int(center)
    sdr.rx_hardwaregain_chan0 = int(candidate["gain"])
    try:
        sdr.rx_rf_bandwidth = int(candidate["rf_bw"])
    except Exception:
        pass


def _format_value(value) -> str:
    if isinstance(value, float):
        return f"{value:.4f}"
    return str(value)


def _print_row(row: dict) -> None:
    print(",".join(_format_value(row.get(field, "")) for field in CSV_FIELDS), flush=True)


def _sort_key(row: dict) -> tuple:
    return (
        float(row.get("score", 0.0) or 0.0),
        -int(row.get("hard_min_errors", 64) or 64),
        float(row.get("soft_sigma", 0.0) or 0.0),
        float(row.get("band_snr_db", 0.0) or 0.0),
    )


def _write_outputs(out_dir: Path, args, rows: list[dict]) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    ranked = sorted(rows, key=_sort_key, reverse=True)
    for rank, row in enumerate(ranked, start=1):
        row["rank"] = rank
    csv_path = out_dir / "weak_info_probe.csv"
    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_FIELDS)
        writer.writeheader()
        writer.writerows(ranked)
    summary = {
        "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "args": vars(args),
        "best": ranked[0] if ranked else None,
        "results": ranked,
    }
    (out_dir / "weak_info_probe.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    if ranked:
        best = ranked[0]
        yaml_text = "\n".join(
            [
                "weak_info_probe_best:",
                f"  team: {best['team']}",
                f"  gain: {best['gain']}",
                f"  rf_bw_hz: {int(best['rf_bw_khz']) * 1000}",
                f"  freq_offset_hz: {int(best['offset_khz']) * 1000}",
                f"  filter: {best['filter']}",
                f"  notch: {best['notch']}",
                f"  score: {best['score']}",
                f"  soft_sigma: {best['soft_sigma']}",
                f"  hard_min_errors: {best['hard_min_errors']}",
                f"  band_snr_db: {best['band_snr_db']}",
                "",
            ]
        )
        (out_dir / "best_weak_info_probe.yaml").write_text(yaml_text, encoding="utf-8")
        sweep_filters = []
        for row in ranked[: max(1, min(4, len(ranked)))]:
            name = _sweep_filter_from_probe(row)
            if str(row["notch"]) == "on":
                name = f"{name}_notch"
            if name not in sweep_filters:
                sweep_filters.append(name)
        sweep_gains = sorted({int(row["gain"]) for row in ranked[: max(1, min(4, len(ranked)))]})
        sweep_bws = sorted({int(row["rf_bw_khz"]) * 1000 for row in ranked[: max(1, min(4, len(ranked)))]})
        sweep_offsets = sorted({int(row["offset_khz"]) * 1000 for row in ranked[: max(1, min(4, len(ranked)))]})
        command = "\n".join(
            [
                "ros2 run sdr_receiver_py_wrapper adaptive_profile_sweep -- \\",
                f"  --team {args.team} \\",
                "  --profiles INFO \\",
                f"  --gains {','.join(str(item) for item in sweep_gains)} \\",
                f"  --rf-bws {','.join(str(item) for item in sweep_bws)} \\",
                f"  --offsets-hz {','.join(str(item) for item in sweep_offsets)} \\",
                f"  --info-filters {','.join(sweep_filters)} \\",
                "  --dwell-sec 6.0",
                "",
            ]
        )
        (out_dir / "suggested_adaptive_profile_sweep.sh").write_text(command, encoding="utf-8")


def _sweep_filter_from_probe(row: dict) -> str:
    name = str(row.get("filter", "normal"))
    try:
        hard_min_errors = int(row.get("hard_min_errors", 64))
    except Exception:
        hard_min_errors = 64
    if hard_min_errors <= 3:
        return name
    if hard_min_errors <= 12:
        mapping = {
            "normal": "loose10",
            "loose3": "loose10",
            "wide_loose3": "wide_loose10",
            "tight_loose3": "tight_loose10",
        }
        return mapping.get(name, name)
    return name


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Weak INFO IQ probe using spectral score and soft access-code correlation."
    )
    parser.add_argument("--uri", default="ip:192.168.2.1")
    parser.add_argument("--team", choices=["RED", "BLUE"], default="RED")
    parser.add_argument("--sample-rate", type=int, default=2_500_000)
    parser.add_argument("--buffer-size", type=int, default=160_000)
    parser.add_argument("--captures", type=int, default=3)
    parser.add_argument("--nfft", type=int, default=65536)
    parser.add_argument("--gains", default="70,73")
    parser.add_argument("--rf-bws", default="220000,300000,420000,540000")
    parser.add_argument("--offsets-hz", default="0,-80000,80000,-150000,150000,-250000,250000")
    parser.add_argument("--info-filters", default="normal,loose3")
    parser.add_argument("--notch-modes", default="off")
    parser.add_argument("--notch-max-bins", type=int, default=8)
    parser.add_argument("--notch-threshold-db", type=float, default=22.0)
    parser.add_argument("--threshold-ks", default="0.0,0.35,-0.35,0.2,-0.2,0.1,-0.1")
    parser.add_argument("--settle-sec", type=float, default=0.20)
    parser.add_argument("--out-dir", default="")
    parser.add_argument("--limit", type=int, default=0)
    args = parser.parse_args()

    out_dir = Path(args.out_dir or Path(tempfile.gettempdir()) / "weak_info_probe" / time.strftime("%Y%m%d_%H%M%S"))
    sdr = _configure_sdr(args)
    rows = []
    candidates = list(_candidate_iter(args))
    if args.limit > 0:
        candidates = candidates[: int(args.limit)]

    print(",".join(CSV_FIELDS), flush=True)
    for idx, candidate in enumerate(candidates, start=1):
        _apply_candidate_sdr(sdr, args, candidate)
        samples = _capture_samples(sdr, int(args.captures), float(args.settle_sec))
        metrics = _analyze_capture(samples, args, candidate)
        row = {
            "rank": idx,
            "idx": idx,
            "team": args.team,
            "gain": candidate["gain"],
            "rf_bw_khz": int(candidate["rf_bw"] / 1000),
            "offset_khz": int(candidate["offset_hz"] / 1000),
            "filter": candidate["filter"],
            "notch": "on" if candidate["notch"] else "off",
            **metrics,
        }
        rows.append(row)
        _print_row(row)
        print(f"PROGRESS,{idx}/{len(candidates)}", flush=True)

    _write_outputs(out_dir, args, rows)
    ranked = sorted(rows, key=_sort_key, reverse=True)
    if ranked:
        best = ranked[0]
        print(
            "BEST,"
            f"gain={best['gain']},rf_bw_khz={best['rf_bw_khz']},offset_khz={best['offset_khz']},"
            f"filter={best['filter']},notch={best['notch']},score={best['score']},"
            f"soft_sigma={best['soft_sigma']:.2f},hard_min_errors={best['hard_min_errors']}",
            flush=True,
        )
    print(f"OUT_DIR,{out_dir}", flush=True)


if __name__ == "__main__":
    main()
