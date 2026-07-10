# RF IQ diff capture

Use `rf_iq_diff_capture` when INFO still shows `AC=0` and `rf_power_scan`
shows almost no TX off/on change. The goal is to save reproducible IQ and
per-bin spectrum data that proves whether INFO RF energy is reaching the RX
side.

## Interactive TX off/on capture

```bash
ros2 run sdr_receiver_py_wrapper rf_iq_diff_capture -- \
  --label RED_INFO \
  --gain 60 \
  --compare-interactive \
  --out-dir /tmp/info_iq_diff
```

The tool will prompt twice:

1. Set INFO TX off, then press Enter.
2. Set INFO TX on, then press Enter.

For each phase it writes:

- `off_iq.npz` / `on_iq.npz`: compressed raw complex IQ plus spectrum arrays.
- `off_spectrum.csv` / `on_spectrum.csv`: per-bin average spectrum.
- `off_summary.json` / `on_summary.json`: phase-level metrics.
- `off_on_delta_spectrum.csv`: TX-on minus TX-off spectrum.
- `off_on_delta_spectrum.png`: optional plot when matplotlib is installed.
- `run_summary.json`: all settings, artifact paths, and comparison metrics.

## Reading the result

The final `COMPARE` line reports:

- `integrated_delta_power_db`: broad-band power change across the selected
  span.
- `max_delta_db` and `max_delta_offset_khz`: strongest per-bin TX-on increase.
- `positive_delta_power_fraction`: fraction of bins where TX-on power is higher
  than TX-off.

If INFO RF is truly entering RX at a useful level, the TX-on phase should show a
clear positive delta near the INFO carrier or its modulation sidebands. If these
values remain near zero and the strongest peak stays at the same offset already
present with TX off, the receiver is still mostly seeing fixed local/environment
energy rather than the INFO transmitter.

## Useful variants

Capture BLUE INFO:

```bash
ros2 run sdr_receiver_py_wrapper rf_iq_diff_capture -- \
  --label BLUE_INFO \
  --gain 60 \
  --compare-interactive \
  --out-dir /tmp/blue_info_iq_diff
```

Capture a custom center frequency:

```bash
ros2 run sdr_receiver_py_wrapper rf_iq_diff_capture -- \
  --freq 433.328MHz \
  --label RED_INFO_PLUS_128K \
  --gain 73 \
  --compare-interactive \
  --out-dir /tmp/info_plus_128k_iq_diff
```

Increase averaging for weak signals:

```bash
ros2 run sdr_receiver_py_wrapper rf_iq_diff_capture -- \
  --label RED_INFO \
  --gain 73 \
  --captures 40 \
  --nfft 131072 \
  --compare-interactive \
  --out-dir /tmp/info_iq_diff_long
```
