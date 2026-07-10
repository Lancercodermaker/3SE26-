from __future__ import annotations

import argparse
import os
import sys
import time
from typing import List

from .original_receiver_adapter import ReceiverCoreAdapter
from .patches import JamKeyEvent, PatchCallbacks


def _positive_float(value: str) -> float:
    parsed = float(value)
    if parsed <= 0.0:
        raise argparse.ArgumentTypeError("must be > 0")
    return parsed


def _non_negative_float(value: str) -> float:
    parsed = float(value)
    if parsed < 0.0:
        raise argparse.ArgumentTypeError("must be >= 0")
    return parsed


def _print_status(adapter: ReceiverCoreAdapter) -> None:
    try:
        stats = adapter.get_stats_snapshot()
    except Exception as exc:
        print(f"STATUS adapter_error={exc}", flush=True)
        return

    jam_keys = stats.get("jam_keys") or {}
    jam_counts = stats.get("jam_key_counts") or {}
    print(
        "STATUS "
        f"team={stats.get('team')} target={stats.get('target')} "
        f"rf_state={stats.get('rf_state')} adc_rms={stats.get('adc_rms')} "
        f"gain={stats.get('rx_gain')}/{stats.get('gain_ceiling')} "
        f"L1={jam_keys.get('L1', '---')}x{jam_counts.get('L1', 0)} "
        f"L2={jam_keys.get('L2', '---')}x{jam_counts.get('L2', 0)} "
        f"L3={jam_keys.get('L3', '---')}x{jam_counts.get('L3', 0)} "
        f"last_error={stats.get('last_error') or 'none'}",
        flush=True,
    )


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Replay an IQ file through the wrapper adapter, patches, and bundled v67 "
            "receiver without starting ROS2. This validates the wrapper demod path, "
            "not only the standalone field replay tool."
        )
    )
    parser.add_argument("iq_source_path", help="Little-endian complex64 IQ file, for example RX_BLUE_ganrao_1.")
    parser.add_argument("--team", default="BLUE", choices=("RED", "BLUE"), help="RF team to decode.")
    parser.add_argument("--target", default="L1", choices=("INFO", "L1", "L2", "L3"), help="Receiver target.")
    parser.add_argument("--center-hz", type=float, default=433_920_000.0, help="RF center used when recording the IQ file.")
    parser.add_argument("--sample-rate", type=int, default=2_000_000, help="IQ file sample rate in samples/s.")
    parser.add_argument("--start-offset-sec", type=_non_negative_float, default=0.0, help="Start replay from this offset.")
    parser.add_argument("--duration-sec", type=_positive_float, default=10.0, help="Maximum replay time.")
    parser.add_argument("--max-keys", type=int, default=1, help="Stop after this many JamCode callbacks; <=0 disables the limit.")
    parser.add_argument("--gain", type=int, default=-1, help="Optional manual gain override for the selected target.")
    parser.add_argument("--script-path", default=os.environ.get("SDR_RECEIVER_ORIGINAL_SCRIPT", "auto"))
    parser.add_argument("--no-loop", action="store_true", help="Do not loop the IQ file at EOF.")
    parser.add_argument("--no-throttle", action="store_true", help="Replay as fast as the receiver loop can run.")
    parser.add_argument("--expect-key", default="", help="Expected 6-character JamCode; non-empty value is checked.")
    parser.add_argument("--allow-no-key", action="store_true", help="Return success even if no JamCode is decoded.")
    args = parser.parse_args(argv)

    events: List[JamKeyEvent] = []

    def on_jam_key(event: JamKeyEvent) -> None:
        events.append(event)
        print(
            "JAM_CODE "
            f"level={event.level} team={event.team} target={event.target} "
            f"key={event.ascii_code} source={event.source}",
            flush=True,
        )

    adapter = ReceiverCoreAdapter(args.script_path, logger=lambda message: print(f"LOG {message}", flush=True))
    adapter.load(allow_adi_import_stub=True)
    adapter.apply_patches(run_mode="competition", callbacks=PatchCallbacks(on_jam_key=on_jam_key))
    adapter.configure_iq_file_source(
        path=args.iq_source_path,
        loop=not args.no_loop,
        throttle=not args.no_throttle,
        center_hz=float(args.center_hz),
        start_offset_sec=float(args.start_offset_sec),
        sample_rate_hz=int(args.sample_rate),
    )
    adapter.set_team(args.team)
    adapter.set_target(args.target)
    if int(args.gain) >= 0:
        adapter.set_manual_gain(args.target, int(args.gain))

    print(
        "START "
        f"iq={args.iq_source_path} team={args.team} target={args.target} "
        f"center_hz={int(args.center_hz)} sample_rate={args.sample_rate} "
        f"duration_sec={args.duration_sec}",
        flush=True,
    )

    deadline = time.time() + float(args.duration_sec)
    next_status = 0.0
    adapter.start()
    try:
        while time.time() < deadline:
            if adapter.receiver_exception is not None:
                break
            if int(args.max_keys) > 0 and len(events) >= int(args.max_keys):
                break
            now = time.time()
            if now >= next_status:
                _print_status(adapter)
                next_status = now + 1.0
            time.sleep(0.05)
    finally:
        adapter.stop(timeout_sec=2.0)
        _print_status(adapter)

    if adapter.receiver_exception is not None:
        print(f"FAIL receiver_exception={adapter.receiver_exception}", flush=True)
        return 3

    if args.expect_key:
        if any(event.ascii_code == args.expect_key for event in events):
            print(f"PASS expected_key={args.expect_key}", flush=True)
            return 0
        found = ",".join(event.ascii_code for event in events) or "none"
        print(f"FAIL expected_key={args.expect_key} found={found}", flush=True)
        return 2

    if events or args.allow_no_key:
        print(f"PASS jam_code_count={len(events)}", flush=True)
        return 0

    print("FAIL no_jam_code_decoded", flush=True)
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
