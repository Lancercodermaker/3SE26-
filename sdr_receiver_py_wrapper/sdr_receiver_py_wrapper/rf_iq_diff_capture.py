from __future__ import annotations

import argparse
import csv
import json
import math
from pathlib import Path
import time
from typing import Iterable

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


def _db(value: float) -> float:
    return 10.0 * math.log10(max(float(value), 1e-18))


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


def _resolve_frequency(args) -> tuple[str, int]:
    label = args.label.upper() if args.label else ""
    if args.freq:
        return label or "CUSTOM", _parse_frequency(args.freq)
    if label in DEFAULT_FREQS_HZ:
        return label, DEFAULT_FREQS_HZ[label]
    return "RED_INFO", DEFAULT_FREQS_HZ["RED_INFO"]


def _configure_sdr(args, center_hz: int):
    import adi

    sdr = adi.Pluto(args.uri)
    sdr.sample_rate = int(args.sample_rate)
    sdr.rx_buffer_size = int(args.buffer_size)
    sdr.gain_control_mode_chan0 = "manual"
    sdr.rx_hardwaregain_chan0 = int(args.gain)
    sdr.rx_lo = int(center_hz)
    try:
        sdr.rx_rf_bandwidth = int(args.rf_bw)
    except Exception:
        pass
    try:
        sdr.filter = ""
    except Exception:
        pass
    return sdr


def _capture_samples(sdr, captures: int, settle_sec: float) -> np.ndarray:
    time.sleep(max(0.0, float(settle_sec)))
    chunks = []
    for _ in range(max(1, int(captures))):
        samples = np.asarray(sdr.rx(), dtype=np.complex64)
        samples = samples - np.mean(samples)
        chunks.append(samples.astype(np.complex64, copy=False))
        time.sleep(0.02)
    return np.stack(chunks, axis=0)


def _average_spectrum(samples: np.ndarray, sample_rate: int, nfft: int, span_hz: float) -> dict:
    n = int(min(samples.shape[1], max(256, nfft)))
    window = np.hanning(n).astype(np.float32)
    scale = float(np.sum(window**2))

    spectra = []
    for row in samples:
        spec = np.abs(np.fft.fftshift(np.fft.fft(row[:n] * window))) ** 2 / max(scale, 1e-18)
        spectra.append(spec.astype(np.float64, copy=False))
    avg_power = np.mean(np.stack(spectra, axis=0), axis=0)
    freqs = np.fft.fftshift(np.fft.fftfreq(n, d=1.0 / float(sample_rate)))

    mask = np.abs(freqs) <= float(span_hz) / 2.0
    if not np.any(mask):
        mask = np.ones_like(freqs, dtype=bool)
    freqs = freqs[mask]
    avg_power = avg_power[mask]

    peak_idx = int(np.argmax(avg_power))
    median_power = float(np.median(avg_power))
    peak_power = float(avg_power[peak_idx])
    mean_power = float(np.mean(avg_power))
    return {
        "freq_offsets_hz": freqs.astype(np.float64),
        "power": avg_power.astype(np.float64),
        "power_db": np.array([_db(value) for value in avg_power], dtype=np.float64),
        "peak_offset_hz": float(freqs[peak_idx]),
        "peak_power_db": _db(peak_power),
        "median_power_db": _db(median_power),
        "mean_power_db": _db(mean_power),
        "snr_like_db": _db(peak_power / max(median_power, 1e-18)),
    }


def _summarize_phase(
    phase: str,
    samples: np.ndarray,
    spectrum: dict,
    args,
    label: str,
    center_hz: int,
) -> dict:
    abs_samples = np.abs(samples)
    rms_raw = float(np.sqrt(np.mean(abs_samples**2)))
    peak_raw = float(np.max(abs_samples))
    return {
        "phase": phase,
        "label": label,
        "center_hz": int(center_hz),
        "center_mhz": center_hz / 1e6,
        "gain": int(args.gain),
        "sample_rate": int(args.sample_rate),
        "buffer_size": int(args.buffer_size),
        "captures": int(samples.shape[0]),
        "nfft": int(min(samples.shape[1], max(256, args.nfft))),
        "span_hz": float(args.span_hz),
        "rms_raw": rms_raw,
        "adc_rms": rms_raw / 2048.0,
        "peak_raw": peak_raw,
        "adc_peak": peak_raw / 2048.0,
        "peak_offset_hz": spectrum["peak_offset_hz"],
        "peak_power_db": spectrum["peak_power_db"],
        "median_power_db": spectrum["median_power_db"],
        "mean_power_db": spectrum["mean_power_db"],
        "snr_like_db": spectrum["snr_like_db"],
    }


def _write_spectrum_csv(path: Path, freq_offsets_hz: np.ndarray, columns: dict[str, np.ndarray]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    names = list(columns.keys())
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["freq_offset_hz", *names])
        for idx, freq_hz in enumerate(freq_offsets_hz):
            writer.writerow([f"{float(freq_hz):.3f}", *[f"{float(columns[name][idx]):.6f}" for name in names]])


def _save_phase(out_dir: Path, phase: str, samples: np.ndarray, spectrum: dict, summary: dict) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    iq_path = out_dir / f"{phase}_iq.npz"
    spec_path = out_dir / f"{phase}_spectrum.csv"
    summary_path = out_dir / f"{phase}_summary.json"

    np.savez_compressed(
        iq_path,
        samples=samples,
        freq_offsets_hz=spectrum["freq_offsets_hz"],
        power=spectrum["power"],
        power_db=spectrum["power_db"],
    )
    _write_spectrum_csv(spec_path, spectrum["freq_offsets_hz"], {"power_db": spectrum["power_db"]})
    summary_path.write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")
    return {
        "iq_path": str(iq_path),
        "spectrum_csv": str(spec_path),
        "summary_json": str(summary_path),
    }


def _compare_spectra(off: dict, on: dict, args) -> dict:
    if len(off["freq_offsets_hz"]) != len(on["freq_offsets_hz"]) or not np.allclose(
        off["freq_offsets_hz"], on["freq_offsets_hz"]
    ):
        raise ValueError("off/on spectra do not share the same frequency grid")

    delta_db = on["power_db"] - off["power_db"]
    delta_linear = on["power"] - off["power"]
    top_indices = np.argsort(delta_db)[-max(1, int(args.top_bins)) :][::-1]
    top_bins = [
        {
            "freq_offset_hz": float(on["freq_offsets_hz"][idx]),
            "off_power_db": float(off["power_db"][idx]),
            "on_power_db": float(on["power_db"][idx]),
            "delta_db": float(delta_db[idx]),
        }
        for idx in top_indices
    ]
    return {
        "mean_delta_db": float(np.mean(delta_db)),
        "max_delta_db": float(np.max(delta_db)),
        "max_delta_offset_hz": float(on["freq_offsets_hz"][int(np.argmax(delta_db))]),
        "integrated_delta_power_db": _db(float(np.mean(on["power"]))) - _db(float(np.mean(off["power"]))),
        "positive_delta_power_fraction": float(np.mean(delta_linear > 0.0)),
        "top_delta_bins": top_bins,
        "delta_db": delta_db.astype(np.float64),
    }


def _write_optional_plot(path: Path, freq_offsets_hz: np.ndarray, off_db: np.ndarray, on_db: np.ndarray, delta_db: np.ndarray) -> None:
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception:
        return

    x_khz = freq_offsets_hz / 1e3
    fig, axes = plt.subplots(2, 1, figsize=(10, 7), sharex=True)
    axes[0].plot(x_khz, off_db, label="TX off", linewidth=1.0)
    axes[0].plot(x_khz, on_db, label="TX on", linewidth=1.0)
    axes[0].set_ylabel("Power (dB)")
    axes[0].legend(loc="best")
    axes[0].grid(True, alpha=0.25)
    axes[1].plot(x_khz, delta_db, color="black", linewidth=1.0)
    axes[1].set_xlabel("Frequency offset (kHz)")
    axes[1].set_ylabel("On - off (dB)")
    axes[1].grid(True, alpha=0.25)
    fig.tight_layout()
    fig.savefig(path, dpi=140)
    plt.close(fig)


def _print_summary(summary: dict) -> None:
    print(
        ",".join(
            [
                summary["phase"],
                summary["label"],
                f"{summary['center_mhz']:.6f}",
                str(summary["gain"]),
                f"{summary['adc_rms']:.6f}",
                f"{summary['adc_peak']:.6f}",
                f"{summary['peak_offset_hz'] / 1e3:.1f}",
                f"{summary['peak_power_db']:.2f}",
                f"{summary['median_power_db']:.2f}",
                f"{summary['snr_like_db']:.2f}",
            ]
        )
    )


def _phase_sequence(args) -> Iterable[str]:
    if args.compare_interactive:
        return ("off", "on")
    return (args.phase,)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Capture IQ and compare TX off/on spectra at one SDR center frequency."
    )
    parser.add_argument("--uri", default="ip:192.168.2.1")
    parser.add_argument("--sample-rate", type=int, default=2_500_000)
    parser.add_argument("--buffer-size", type=int, default=160_000)
    parser.add_argument("--captures", type=int, default=12, help="RX buffers per phase")
    parser.add_argument("--nfft", type=int, default=65536)
    parser.add_argument("--gain", type=int, default=60)
    parser.add_argument("--rf-bw", type=int, default=1_500_000)
    parser.add_argument("--span-hz", type=float, default=1_500_000.0)
    parser.add_argument("--settle-sec", type=float, default=0.25)
    parser.add_argument("--freq", help="Custom center frequency, for example 433.2MHz")
    parser.add_argument("--label", default="RED_INFO", help="Known label or custom label")
    parser.add_argument("--phase", choices=["off", "on", "single"], default="single")
    parser.add_argument("--compare-interactive", action="store_true", help="Prompt for TX off, then TX on.")
    parser.add_argument("--out-dir", default="/tmp/sdr_iq_diff")
    parser.add_argument("--top-bins", type=int, default=8)
    args = parser.parse_args()

    label, center_hz = _resolve_frequency(args)
    stamp = time.strftime("%Y%m%d_%H%M%S")
    out_dir = Path(args.out_dir).expanduser() / f"{label}_{center_hz}_{stamp}"
    sdr = _configure_sdr(args, center_hz)

    print("phase,label,center_mhz,gain,adc_rms,adc_peak,peak_offset_khz,peak_power_db,median_power_db,snr_like_db")
    spectra = {}
    summaries = {}
    artifacts = {}
    for phase in _phase_sequence(args):
        if args.compare_interactive:
            input(f"Set INFO TX {phase.upper()}, then press Enter to capture...")
        samples = _capture_samples(sdr, args.captures, args.settle_sec)
        spectrum = _average_spectrum(samples, args.sample_rate, args.nfft, args.span_hz)
        summary = _summarize_phase(phase, samples, spectrum, args, label, center_hz)
        saved = _save_phase(out_dir, phase, samples, spectrum, summary)
        spectra[phase] = spectrum
        summaries[phase] = summary
        artifacts[phase] = saved
        _print_summary(summary)

    compare = None
    if "off" in spectra and "on" in spectra:
        compare = _compare_spectra(spectra["off"], spectra["on"], args)
        compare_csv = out_dir / "off_on_delta_spectrum.csv"
        _write_spectrum_csv(
            compare_csv,
            spectra["on"]["freq_offsets_hz"],
            {
                "off_power_db": spectra["off"]["power_db"],
                "on_power_db": spectra["on"]["power_db"],
                "delta_db": compare["delta_db"],
            },
        )
        plot_path = out_dir / "off_on_delta_spectrum.png"
        _write_optional_plot(
            plot_path,
            spectra["on"]["freq_offsets_hz"],
            spectra["off"]["power_db"],
            spectra["on"]["power_db"],
            compare["delta_db"],
        )
        compare = {key: value for key, value in compare.items() if key != "delta_db"}
        compare["delta_csv"] = str(compare_csv)
        if plot_path.exists():
            compare["delta_png"] = str(plot_path)
        print(
            "COMPARE,"
            f"integrated_delta_power_db={compare['integrated_delta_power_db']:.2f},"
            f"max_delta_db={compare['max_delta_db']:.2f},"
            f"max_delta_offset_khz={compare['max_delta_offset_hz'] / 1e3:.1f},"
            f"positive_delta_power_fraction={compare['positive_delta_power_fraction']:.3f}"
        )

    run_summary = {
        "created_at": time.time(),
        "label": label,
        "center_hz": center_hz,
        "args": vars(args),
        "phases": summaries,
        "artifacts": artifacts,
        "compare": compare,
    }
    run_summary_path = out_dir / "run_summary.json"
    run_summary_path.write_text(json.dumps(run_summary, indent=2, sort_keys=True), encoding="utf-8")
    print(f"OUT_DIR,{out_dir}")
    print(f"RUN_SUMMARY,{run_summary_path}")


if __name__ == "__main__":
    main()
