from __future__ import annotations

import argparse
import os
import threading
import types

import numpy as np

from .original_receiver_adapter import ReceiverCoreAdapter
from .patches import PatchCallbacks, PatchManager


DEFAULT_SCRIPT = "auto"


def run_import_smoke(script_path: str, *, allow_adi_stub: bool) -> None:
    adapter = ReceiverCoreAdapter(script_path, logger=lambda message: print(f"[adapter] {message}"))
    module = adapter.load(allow_adi_import_stub=allow_adi_stub)
    required = ["main", "validate_and_parse", "handle_keyboard", "select_tune_target", "STATE", "TUNE_CFG"]
    missing = [name for name in required if not hasattr(module, name)]
    if missing:
        raise AssertionError(f"missing required symbols after import: {missing}")
    print(
        "import smoke ok: "
        f"module={module.__name__} target={module.TUNE_CFG.get('TARGET')} "
        "main_not_executed=True"
    )


def run_patch_smoke() -> None:
    captured = []

    def fake_validate_and_parse(cmd_id, payload, source="direct"):
        return True

    fake_module = types.SimpleNamespace()
    fake_module.validate_and_parse = fake_validate_and_parse
    fake_module.handle_keyboard = lambda: True
    fake_module.init_dashboard = lambda: None
    fake_module.render_dashboard = lambda locked=False, adc_peak=0.0: None
    fake_module.restore_terminal = lambda: None
    fake_module.select_tune_target = lambda target, **kwargs: fake_module.TUNE_CFG.update({"TARGET": target})
    fake_module.TUNE_CFG = {"TEAM": "RED", "TARGET": "L1"}
    fake_module.STATE = {"ENCRYPT_LVL": 1, "STATS": {}}

    callbacks = PatchCallbacks(on_jam_key=lambda event: captured.append(event))
    manager = PatchManager(
        fake_module,
        run_mode="competition",
        callbacks=callbacks,
        stop_event=threading.Event(),
        logger=lambda message: print(f"[patch] {message}"),
    )
    manager.apply()
    try:
        result = fake_module.validate_and_parse(0x0A06, b"ABC123", source="direct")
        if not result:
            raise AssertionError("patched validate_and_parse returned false")
        if len(captured) != 1:
            raise AssertionError(f"expected one captured key, got {len(captured)}")
        event = captured[0]
        if event.ascii_code != "ABC123" or event.level != 1 or event.team != "RED":
            raise AssertionError(f"unexpected captured event: {event}")
        if fake_module.handle_keyboard() is not True:
            raise AssertionError("competition handle_keyboard patch should return true before stop")
        print("patch smoke ok: captured fake 0x0A06 key ABC123")
    finally:
        manager.restore()


def run_weak_soft_smoke() -> None:
    ac_info = "0010111101101111010011000111010010111001000101000100100100101110"
    air_header = "00000000000011110000000000001111"
    sof = "10100101"
    payload = (sof + "0" * 112)[:120]
    bits = ac_info + air_header + payload + "0" * 32
    sps = 1
    symbol_values = np.array([0.16 if bit == "1" else -0.16 for bit in bits], dtype=np.float64)
    freq = np.repeat(symbol_values, sps)
    phase = np.concatenate([[0.0], np.cumsum(freq)])
    rx_data = np.exp(1j * phase).astype(np.complex64)

    fake_module = types.SimpleNamespace()
    fake_module.SPS = sps
    fake_module.SDR_FS = 2_500_000
    fake_module.AIR_HEADER = air_header
    fake_module.THRESHOLD_K_VALUES = (0.0,)
    fake_module.POOL_MAX_BITS = 2160
    fake_module.POOL_KEEP_BITS = 1080
    fake_module.TUNE_CFG = {"TEAM": "RED", "TARGET": "INFO"}
    fake_module.STATE = {
        "ENCRYPT_LVL": 0,
        "STATS": {"AC_RAW": 0, "AC": 0, "SOF": 0, "CRC8": 0, "CRC16": 0},
        "BIT_POOLS": {},
        "TRACK": {"TARGET": None, "PROFILE": None, "LOCK_UNTIL": 0.0, "LAST_CRC16": 0.0, "MISS": 0},
    }
    fake_module.INFO_RESCUE_AC_ERRORS = 3
    fake_module.INFO_HEADER_MAX_ERRORS = 3
    fake_module.validate_and_parse = lambda _cmd_id, _payload, source="direct": True
    fake_module.fast_demod = lambda _rx_data, _ac_target: False
    fake_module.filter_iq = lambda rx, _cfg: rx
    fake_module.moving_average = lambda values, _n: values
    fake_module.get_effective_radio_params = lambda: {"digital_shift": 0}
    fake_module.get_effective_filter_params = lambda _target=None: {
        "weak_soft_ac": True,
        "weak_soft_min_sigma": 1.0,
        "weak_soft_peak_limit": 2,
        "weak_soft_max_candidates": 1,
        "weak_soft_ac_max_errors": 2,
        "weak_soft_header_max_errors": 2,
        "trend_bits": 9999,
        "smooth_frac": 1.0,
    }
    fake_module.threshold_grid = lambda _symbols, _values: [(0.0, 0.08)]

    def hamming_distance(a, b, stop_after=None):
        dist = 0
        for left, right in zip(a, b):
            if left != right:
                dist += 1
                if stop_after is not None and dist > stop_after:
                    return dist
        return dist

    def make_pool_key(target, polarity, shift, k):
        return (target, polarity, int(shift), round(float(k), 2))

    def process_pool(pool_key, **_kwargs):
        pool = fake_module.STATE["BIT_POOLS"].get(pool_key, "")
        if sof in pool:
            fake_module.STATE["STATS"]["SOF"] += 1
            return {"SOF": 1, "CRC8": 0, "CRC16": 0}
        return {"SOF": 0, "CRC8": 0, "CRC16": 0}

    fake_module.hamming_distance = hamming_distance
    fake_module.make_pool_key = make_pool_key
    fake_module.process_pool = process_pool
    fake_module.prune_pools = lambda **_kwargs: None

    manager = PatchManager(
        fake_module,
        run_mode="competition",
        callbacks=PatchCallbacks(),
        stop_event=threading.Event(),
        logger=lambda message: print(f"[patch] {message}"),
    )
    manager.apply()
    try:
        locked = fake_module.fast_demod(rx_data, ac_info)
        stats = fake_module.STATE["STATS"]
        if not locked:
            raise AssertionError(f"weak_soft fast_demod wrapper should report activity: {stats}")
        if stats.get("SOFT_AC", 0) != 1 or stats.get("SOFT_SOF", 0) != 1:
            raise AssertionError(f"weak_soft stats not updated as expected: {stats}")
        print("weak_soft smoke ok: soft AC payload entered pool and produced SOF")
    finally:
        manager.restore()


def main() -> None:
    parser = argparse.ArgumentParser(description="Offline smoke tests for sdr_receiver_py_wrapper.")
    parser.add_argument(
        "--script-path",
        default=os.environ.get("SDR_RECEIVER_ORIGINAL_SCRIPT", DEFAULT_SCRIPT),
        help="Path to the original v67 Python receiver script, or 'auto' to search bundled/runtime paths.",
    )
    parser.add_argument(
        "--allow-adi-stub",
        action="store_true",
        help="Install a minimal adi module stub so import can be checked on machines without pyadi-iio.",
    )
    parser.add_argument(
        "--skip-import",
        action="store_true",
        help="Only run the pure monkey-patch smoke test.",
    )
    args = parser.parse_args()

    if not args.skip_import:
        run_import_smoke(args.script_path, allow_adi_stub=args.allow_adi_stub)
    run_patch_smoke()
    run_weak_soft_smoke()


if __name__ == "__main__":
    main()
