import adi
import numpy as np
import time
import struct
import os
import sys
import csv
import json

if os.name == "nt":
    import msvcrt

    def init_input_terminal():
        pass

    def restore_input_terminal():
        pass

    def key_pressed():
        return msvcrt.kbhit()

    def read_key():
        return msvcrt.getch().decode("utf-8", errors="ignore").lower()
else:
    import select
    import termios
    import tty

    _OLD_TERMINAL_SETTINGS = None

    def init_input_terminal():
        global _OLD_TERMINAL_SETTINGS
        if not sys.stdin.isatty():
            return
        fd = sys.stdin.fileno()
        if _OLD_TERMINAL_SETTINGS is None:
            _OLD_TERMINAL_SETTINGS = termios.tcgetattr(fd)
        tty.setcbreak(fd)

    def restore_input_terminal():
        global _OLD_TERMINAL_SETTINGS
        if _OLD_TERMINAL_SETTINGS is None:
            return
        termios.tcsetattr(sys.stdin.fileno(), termios.TCSADRAIN, _OLD_TERMINAL_SETTINGS)
        _OLD_TERMINAL_SETTINGS = None

    def key_pressed():
        if not sys.stdin.isatty():
            return False
        return select.select([sys.stdin], [], [], 0)[0]

    def read_key():
        return sys.stdin.read(1).lower()

# ==========================================
# 1. PHY configuration
# ==========================================
# The transmitter uses 52 samples/bit at 1 Msps, so the real symbol rate is
# 1e6 / 52. 2.5 Msps keeps this an exact 130 samples/bit on RX.
RX_PATCH_TAG = "20260505_l2rescue_80k_g40"
TX_SAMPLE_RATE = 1_000_000.0
TX_SPS = 52.0
SYMBOL_RATE = TX_SAMPLE_RATE / TX_SPS
SDR_FS = int(2.5e6)
SPS = int(round(SDR_FS / SYMBOL_RATE))
RX_BUFFER_SIZE = 160_000
VERBOSE_DSP_LOGS = False

AIR_HEADER = "00000000000011110000000000001111"
POOL_MAX_BITS = 2160
POOL_KEEP_BITS = 1080
POOL_STALE_KEEP_BITS = 720
INFO_CHUNK_POOL_KEY = ("INFO", "chunks", 0, 0.0)
INFO_CHUNK_POOL_MAX_BITS = 1680
INFO_CHUNK_POOL_KEEP_BITS = 1320
INFO_CHUNK_POOL_STALE_KEEP_BITS = 1080
INFO_CHUNK_MAX_RECORDS = 12
LOW_TRUST_CONFIRM_SEC = 2.0
LOW_TRUST_CONFIRM_COUNT = 2
MAX_ACTIVE_POOLS = 14
MAX_TOUCHED_POOLS = 40
RESCUE_PLAN_LIMIT = 48
CRC16_STALE_SEC = 0.75
INFO_L3_CRC16_STALE_SEC = 0.45
INFO_L3_RELOCK_PLAN_LIMIT = 48
INFO_L3_CRC8_LOCK_SEC = 1.20
INFO_L3_AIR_LOCK_SEC = 0.45
INFO_L3_CRC8_KEEP_POOLS = 4
INFO_L3_AIR_KEEP_POOLS = 6
DASH_WIDTH = 132
ADC_SAT_LEVEL = 0.92
ADC_BACKOFF_LEVEL = 0.82
ADC_LOW_LEVEL = 0.08
ADC_TARGET_HIGH = 0.72
ADC_TARGET_LOW = 0.16
GAIN_RECOVER_SEC = 0.35
GAIN_RECOVER_STALE_SEC = 0.90
RX_GAIN_MIN = 0
RX_GAIN_MAX = 73
GAIN_STEP_DB = 1
MANUAL_RX_GAINS = {
    "INFO": 24,
    "L1": 22,
    "L2": 22,
    "L3": 25,
}
INFO_HEADER_MAX_ERRORS = 3
INFO_L3_HEADER_MAX_ERRORS = 1
JAM_HEADER_MAX_ERRORS = 1
INFO_RESCUE_AC_ERRORS = 3
INFO_L3_RESCUE_SEARCH_AC_ERRORS = 3
INFO_L3_RESCUE_ACCEPT_AC_ERRORS = 2
INFO_L3_RESCUE_LO_OFFSETS = (80_000, 160_000, 120_000)
INFO_L3_RESCUE_RF_BW = 760_000
INFO_L3_RESCUE_GAIN = 24
INFO_L2_RESCUE_SEARCH_AC_ERRORS = 3
INFO_L2_RESCUE_ACCEPT_AC_ERRORS = 2
INFO_L2_HEADER_MAX_ERRORS = 2
INFO_L2_RESCUE_LO_OFFSETS = (80_000, 200_000, 240_000, 160_000, 120_000)
INFO_L2_RESCUE_RF_BW = 660_000
INFO_L2_RESCUE_GAIN = 40
CAL_DWELL_SEC = 2.5
CAL_GAINS = (18, 22, 24, 26, 30)
CAL_L2_RF_BWS = (660_000, 760_000)
CAL_L3_RF_BWS = (760_000,)
CAL_LOG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "calibration_logs")
PROFILE_DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "calibration_profiles.json")
RX_LOG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "rx_logs")
RX_STRUCT_LOG_ENABLED = os.environ.get("RX_STRUCT_LOG", "1") != "0"
RX_LOG_CSV_PATH = os.environ.get("RX_LOG_CSV", "")
RX_LOG_JSONL_PATH = os.environ.get("RX_LOG_JSONL", "")
PROFILE_CONTEXT = {
    "antenna": os.environ.get("RX_ANTENNA", "default"),
    "front_end": os.environ.get("RX_FRONT_END", "default"),
    "venue": os.environ.get("RX_VENUE", time.strftime("%Y%m%d")),
    "geometry": os.environ.get("RX_GEOMETRY", "unspecified"),
}
CAL_TOP_K = 6
CAL_VALIDATE_ROUNDS = 2
CAL_VALIDATE_DWELL_SEC = 1.5
CAL_QUICK_GAINS = (30, 36, 40)
CAL_FULL_GAINS = (24, 30, 36, 40, 44)
CAL_L2_QUICK_OFFSETS = (80_000, 200_000, 240_000, 160_000, 120_000)
CAL_L3_QUICK_OFFSETS = (80_000, 120_000, 160_000, 200_000, 240_000, 280_000)
CAL_L2_FULL_OFFSETS = (80_000, 200_000, 240_000, 160_000, 120_000, 280_000, 40_000)
CAL_L3_FULL_OFFSETS = (40_000, 80_000, 120_000, 160_000, 200_000, 240_000, 280_000)
CAL_SCOPE_ALL = "ALL"
CAL_SCOPE_L2 = "L2"
CAL_SCOPE_L3 = "L3"

L2_RESCUE_FALLBACK_SPECS = (
    (80_000, 40, 660_000, "hist248"),
    (80_000, 36, 660_000, "hist248"),
    (200_000, 40, 660_000, "hist248"),
    (200_000, 30, 660_000, "hist248"),
    (240_000, 36, 660_000, "hist248"),
    (200_000, 22, 660_000, "hist248"),
)
L3_RESCUE_FALLBACK_SPECS = (
    (200_000, 22, 760_000, "l3tight"),
    (160_000, 22, 660_000, "l3tight"),
    (80_000, 24, 760_000, "l3tight"),
)

AC_INFO = "0010111101101111010011000111010010111001000101000100100100101110"
AC_JAM = "0001011011101000110100110111011100010101000111000111000100101101"

INFO_CMD_LENGTHS = {
    0x0A01: 24,
    0x0A02: 12,
    0x0A03: 10,
    0x0A04: 8,
    0x0A05: 36,
    0x020E: 1,
}
JAM_CMD_LENGTHS = {0x0A06: 6}
INFO_LENGTH_VALUES = frozenset(INFO_CMD_LENGTHS.values())
JAM_LENGTH_VALUES = frozenset(JAM_CMD_LENGTHS.values())

# Blue frequencies are aligned to the transmitter file.
RADAR_PARAMS = {
    "RED": {
        "INFO": {"freq": int(433.2e6), "ac": AC_INFO, "gain": 40, "rf_bw": 540_000},
        "L1": {"freq": int(432.2e6), "ac": AC_JAM, "gain": 22, "rf_bw": 1_250_000},
        "L2": {"freq": int(432.5e6), "ac": AC_JAM, "gain": 22, "rf_bw": 1_100_000},
        "L3": {"freq": int(432.8e6), "ac": AC_JAM, "gain": 25, "rf_bw": 400_000},
    },
    "BLUE": {
        "INFO": {"freq": int(433.92e6), "ac": AC_INFO, "gain": 40, "rf_bw": 540_000},
        "L1": {"freq": int(434.92e6), "ac": AC_JAM, "gain": 22, "rf_bw": 1_250_000},
        "L2": {"freq": int(434.62e6), "ac": AC_JAM, "gain": 22, "rf_bw": 1_100_000},
        "L3": {"freq": int(434.32e6), "ac": AC_JAM, "gain": 25, "rf_bw": 400_000},
    },
}

JAM_TX_BW_HZ = {
    "L1": 940_000.0,
    "L2": 860_000.0,
    "L3": 250_000.0,
}
JAM_RF_SOURCE_CONF_MIN = 1.18
JAM_RF_TARGET_GATE_HZ = 190_000.0
JAM_RF_ACCEPT_STREAK_MIN = 5
JAM_RF_TARGET_SWITCH_GUARD_SEC = 0.75
JAM_ALT_CRC16_XOROUT = 0x3014
INFO_ALT_CRC16_ENABLED = os.environ.get("RX_INFO_ALT_CRC16", "1") != "0"
JAM_ALT_CRC16_ENABLED = os.environ.get("RX_JAM_ALT_CRC16", "1") != "0"
JAM_RF_DIRECT_FALLBACK_REASONS = frozenset(("rf-weak", "rf-streak", "target-switch-guard"))

# Digital filtering is done before FM discrimination. That is important:
# once angle(rx) is taken, a much stronger adjacent signal can dominate the
# phase and the weaker INFO waveform is already damaged.
FILTER_PARAMS = {
    # INFO is asymmetric on purpose. When RED-INFO is centered, RED-L2 sits
    # around -700 kHz and its upper FSK skirt approaches -290 kHz.
    "INFO": {
        "kind": "asym_fft",
        "pass_low": -263_000.0,
        "pass_high": 315_000.0,
        "stop_low": -296_000.0,
        "stop_high": 405_000.0,
        "smooth_frac": 0.34,
        "trend_bits": 16,
        "max_ac_errors": 2,
    },
    "L1": {
        "kind": "sym_fft",
        "cutoff": 620_000.0,
        "transition": 90_000.0,
        "smooth_frac": 0.34,
        "trend_bits": 16,
        "max_ac_errors": 2,
    },
    "L2": {
        "kind": "sym_fft",
        "cutoff": 560_000.0,
        "transition": 80_000.0,
        "smooth_frac": 0.34,
        "trend_bits": 16,
        "max_ac_errors": 2,
    },
    "L3": {
        "kind": "sym_fft",
        "cutoff": 220_000.0,
        "transition": 60_000.0,
        "smooth_frac": 0.38,
        "trend_bits": 16,
        "max_ac_errors": 2,
    },
}

INFO_L3_RESCUE_FILTER_PARAMS = {
    "kind": "asym_fft",
    "pass_low": -263_000.0,
    "pass_high": 315_000.0,
    "stop_low": -286_000.0,
    "stop_high": 390_000.0,
    "smooth_frac": 0.34,
    "trend_bits": 16,
    "max_ac_errors": 3,
}

INFO_L3_RESCUE_FILTER_PROFILES = {
    "l3cur": dict(INFO_L3_RESCUE_FILTER_PARAMS),
    "l3tight": {
        "kind": "asym_fft",
        "pass_low": -248_000.0,
        "pass_high": 305_000.0,
        "stop_low": -276_000.0,
        "stop_high": 375_000.0,
        "smooth_frac": 0.34,
        "trend_bits": 16,
        "max_ac_errors": 3,
    },
}

INFO_L2_RESCUE_FILTER_PROFILES = {
    "hist255": {
        "kind": "asym_fft",
        "pass_low": -255_000.0,
        "pass_high": 315_000.0,
        "stop_low": -288_000.0,
        "stop_high": 405_000.0,
        "smooth_frac": 0.34,
        "trend_bits": 16,
        "max_ac_errors": 3,
    },
    "hist248": {
        "kind": "asym_fft",
        "pass_low": -248_000.0,
        "pass_high": 315_000.0,
        "stop_low": -276_000.0,
        "stop_high": 405_000.0,
        "smooth_frac": 0.34,
        "trend_bits": 16,
        "max_ac_errors": 3,
    },
    "wide263": dict(INFO_L3_RESCUE_FILTER_PARAMS),
}

TUNE_CFG = {"TEAM": "RED", "TARGET": "INFO"}
LAST_SDR_CFG = {"KEY": None}

STATE = {
    "INFO_L3_RESCUE": False,
    "INFO_L2_RESCUE": False,
    "INFO_L3_RESCUE_OFFSET_INDEX": 0,
    "INFO_L2_RESCUE_OFFSET_INDEX": 0,
    "CAL_PROFILE": None,
    "CAL": {
        "ACTIVE": False,
        "QUEUE": [],
        "INDEX": -1,
        "RESULTS": [],
        "SEED_RESULTS": [],
        "STEP_START": 0.0,
        "LOG_PATH": "",
        "BEST": None,
        "LAST_FAILOVER": 0.0,
        "STAGE": "idle",
        "SCOPE": CAL_SCOPE_ALL,
        "FULL": False,
        "DWELL_SEC": CAL_DWELL_SEC,
        "VALIDATE_TOP_K": CAL_TOP_K,
        "VALIDATE_ROUNDS": CAL_VALIDATE_ROUNDS,
        "FALLBACK_INDEX": 0,
    },
    "MANUAL_RX_GAINS": dict(MANUAL_RX_GAINS),
    "JAM_KEYS": {"L1": "---", "L2": "---", "L3": "---"},
    "JAM_KEYS_CNT": {"L1": 0, "L2": 0, "L3": 0},
    "ENCRYPT_LVL": 1,
    "STATS": {
        "AC": 0,
        "SOF": 0,
        "CRC8": 0,
        "CRC16": 0,
        "LAST_LOG": 0.0,
        "LAST_PACKET_LOG": 0.0,
        "LAST_CFG_LOG": "",
        "LAST_DATA_SNAPSHOT": None,
        "LAST_DATA_UPDATE": 0.0,
        "LAST_DATA_CHANGE": "none",
        "AC_RAW": 0,
        "HDR_DROP": 0,
        "LEN_DROP": 0,
        "CMD_DROP": 0,
        "CRC16_FAIL": 0,
        "CRC16_FIX": 0,
        "CRC16_ALT": 0,
        "ASM_CHUNKS": 0,
        "ASM_CRC16": 0,
        "FRAME_REJECT": 0,
        "FRAME_PENDING": 0,
        "LAST_AC_TIME": 0.0,
        "LAST_CRC16_TIME": 0.0,
        "LAST_CRC16_CMD": "none",
        "LAST_CRC16_MODE": "none",
        "LOOP_MS": 0.0,
        "RX_MS": 0.0,
        "DEMOD_MS": 0.0,
        "ADC_RMS": 0.0,
        "RX_GAIN": 0,
        "GAIN_CEILING": 0,
        "GAIN_NOTE": "init",
        "RF_STATE": "INIT",
        "RF_ADVICE": "",
        "JAM_RF_SOURCE": "",
        "JAM_RF_CONF": 0.0,
        "JAM_RF_OFFSET": 0.0,
        "JAM_RF_TARGET_OFFSET": 0.0,
        "JAM_RF_MATCH_STREAK": 0,
        "JAM_RF_TARGET_CHANGED": time.time(),
        "JAM_RF_LEVELS": "",
        "JAM_RF_GATE_REASON": "",
        "JAM_RF_GATE_MODE": "",
        "JAM_RF_GATE_ACCEPT": 0,
        "JAM_RF_GATE_FALLBACK": 0,
        "JAM_RF_GATE_REJECT": 0,
        "JAM_DIRECT_CRC16_ACCEPT": 0,
        "RX_LOG_PATH": "",
        "LAST_FRAME_HEX": "",
        "LAST_FRAME_SOURCE": "",
        "LAST_FRAME_SEQ": "",
        "LAST_CFG_TIME": 0.0,
        "DSP_MODE": "normal",
        "LAST_ERROR": "",
    },
    "TRACK": {
        "TARGET": None,
        "PROFILE": None,
        "LOCK_UNTIL": 0.0,
        "LAST_CRC16": 0.0,
        "MISS": 0,
    },
    "BIT_POOLS": {},
    "POOL_SCORES": {},
    "PENDING_FRAMES": {},
}

D = {
    "POS": {"H1": (0, 0), "E2": (0, 0), "I3": (0, 0), "I4": (0, 0), "A6": (0, 0), "S7": (0, 0)},
    "HP": {"H1": 0, "E2": 0, "I3": 0, "I4": 0, "S7": 0},
    "AMMO": {"H1": 0, "I3": 0, "I4": 0, "A6": 0, "S7": 0},
    "COIN": {"Rem": 0, "Tot": 0},
    "OCCU": {
        "Sup": 0,
        "Cen": 0,
        "Trp": 0,
        "For": 0,
        "Out": 0,
        "Base": 0,
        "Tun_1": 0,
        "Tun_2": 0,
        "Tun_3": 0,
        "Tun_4": 0,
        "Hig": 0,
        "Fly": 0,
        "Roa": 0,
    },
    "BUFF": {
        "H1": {"Hp": 0, "Heat": 0, "Def": 0, "Vul": 0, "Atk": 0},
        "E2": {"Hp": 0, "Heat": 0, "Def": 0, "Vul": 0, "Atk": 0},
        "I3": {"Hp": 0, "Heat": 0, "Def": 0, "Vul": 0, "Atk": 0},
        "I4": {"Hp": 0, "Heat": 0, "Def": 0, "Vul": 0, "Atk": 0},
        "S7": {"Hp": 0, "Heat": 0, "Def": 0, "Vul": 0, "Atk": 0, "Pose": 0},
    },
    "ID": "0x0000",
    "BIT_POOL": "",
}

RX_LOG_FIELDS = [
    "rx_time",
    "team",
    "target",
    "rf_state",
    "source",
    "cmd_id",
    "seq",
    "crc16_ok",
    "crc16_fixed",
    "frame_hex",
    "payload_hex",
    "coin_rem",
    "coin_tot",
    "hp_h1",
    "ammo_h1",
    "pos_h1_x",
    "pos_h1_y",
    "cfg_log",
    "data_change",
    "adc_rms",
    "ac_count",
    "crc8_count",
    "crc16_count",
    "crc16_fail_count",
]
_RX_LOG_READY = False


def ensure_rx_log_ready():
    global _RX_LOG_READY, RX_LOG_CSV_PATH, RX_LOG_JSONL_PATH
    if not RX_STRUCT_LOG_ENABLED:
        return False
    if _RX_LOG_READY:
        return True

    os.makedirs(RX_LOG_DIR, exist_ok=True)
    stamp = time.strftime("%Y%m%d_%H%M%S")
    if not RX_LOG_CSV_PATH:
        RX_LOG_CSV_PATH = os.path.join(RX_LOG_DIR, f"rx_decoded_{stamp}.csv")
    if RX_LOG_JSONL_PATH == "1":
        RX_LOG_JSONL_PATH = os.path.join(RX_LOG_DIR, f"rx_decoded_{stamp}.jsonl")

    for path in (RX_LOG_CSV_PATH, RX_LOG_JSONL_PATH):
        if path:
            parent = os.path.dirname(os.path.abspath(path))
            if parent:
                os.makedirs(parent, exist_ok=True)

    if RX_LOG_CSV_PATH and not os.path.exists(RX_LOG_CSV_PATH):
        with open(RX_LOG_CSV_PATH, "w", newline="", encoding="utf-8") as f:
            csv.DictWriter(f, fieldnames=RX_LOG_FIELDS).writeheader()

    STATE["STATS"]["RX_LOG_PATH"] = RX_LOG_CSV_PATH or RX_LOG_JSONL_PATH or ""
    _RX_LOG_READY = True
    return True


def rx_log_row(cmd_id, seq, source, crc16_ok, crc16_fixed, frame_bytes, payload_bytes):
    pos_h1 = D["POS"]["H1"]
    return {
        "rx_time": time.time(),
        "team": TUNE_CFG["TEAM"],
        "target": TUNE_CFG["TARGET"],
        "rf_state": STATE["STATS"].get("RF_STATE", ""),
        "source": source,
        "cmd_id": f"0x{int(cmd_id):04X}",
        "seq": int(seq) & 0xFF,
        "crc16_ok": bool(crc16_ok),
        "crc16_fixed": bool(crc16_fixed),
        "frame_hex": bytes(frame_bytes).hex(),
        "payload_hex": bytes(payload_bytes).hex(),
        "coin_rem": D["COIN"]["Rem"],
        "coin_tot": D["COIN"]["Tot"],
        "hp_h1": D["HP"]["H1"],
        "ammo_h1": D["AMMO"]["H1"],
        "pos_h1_x": pos_h1[0],
        "pos_h1_y": pos_h1[1],
        "cfg_log": STATE["STATS"].get("LAST_CFG_LOG", ""),
        "data_change": STATE["STATS"].get("LAST_DATA_CHANGE", ""),
        "adc_rms": f"{STATE['STATS'].get('ADC_RMS', 0.0):.4f}",
        "ac_count": STATE["STATS"].get("AC", 0),
        "crc8_count": STATE["STATS"].get("CRC8", 0),
        "crc16_count": STATE["STATS"].get("CRC16", 0),
        "crc16_fail_count": STATE["STATS"].get("CRC16_FAIL", 0),
    }


def log_rx_frame(cmd_id, seq, source, frame_bytes, payload_bytes, crc16_fixed=False):
    STATE["STATS"]["LAST_FRAME_HEX"] = bytes(frame_bytes).hex()
    STATE["STATS"]["LAST_FRAME_SOURCE"] = source
    STATE["STATS"]["LAST_FRAME_SEQ"] = int(seq) & 0xFF
    if not ensure_rx_log_ready():
        return
    try:
        update_data_change_state()
        row = rx_log_row(cmd_id, seq, source, True, crc16_fixed, frame_bytes, payload_bytes)
        if RX_LOG_CSV_PATH:
            with open(RX_LOG_CSV_PATH, "a", newline="", encoding="utf-8") as f:
                csv.DictWriter(f, fieldnames=RX_LOG_FIELDS).writerow(row)
        if RX_LOG_JSONL_PATH:
            with open(RX_LOG_JSONL_PATH, "a", encoding="utf-8") as f:
                f.write(json.dumps(row, sort_keys=True) + "\n")
    except Exception as exc:
        STATE["STATS"]["LAST_ERROR"] = f"RX LOG ERROR: {exc}"


def classified_jam_target():
    target, _reason = jam_rf_gate_status()
    return target


def jam_rf_gate_status():
    target = TUNE_CFG["TARGET"]
    if target not in ("L1", "L2", "L3"):
        return "", "not-jam-target"

    rf_source = STATE["STATS"].get("JAM_RF_SOURCE", "")
    rf_conf = float(STATE["STATS"].get("JAM_RF_CONF", 0.0) or 0.0)
    streak = int(STATE["STATS"].get("JAM_RF_MATCH_STREAK", 0) or 0)
    if rf_source in ("L1", "L2", "L3") and rf_conf >= JAM_RF_SOURCE_CONF_MIN:
        if rf_source != target:
            return "", f"strong-rf-mismatch:{rf_source}"
        if time.time() - float(STATE["STATS"].get("JAM_RF_TARGET_CHANGED", 0.0) or 0.0) < JAM_RF_TARGET_SWITCH_GUARD_SEC:
            return "", "target-switch-guard"
        if streak >= JAM_RF_ACCEPT_STREAK_MIN:
            return target, "rf-classified"
        return "", "rf-streak"
    return "", "rf-weak"


def jam_target_for_valid_frame(source):
    target, reason = jam_rf_gate_status()
    STATE["STATS"]["JAM_RF_GATE_REASON"] = reason
    if target in ("L1", "L2", "L3"):
        STATE["STATS"]["JAM_RF_GATE_MODE"] = "rf-classified"
        STATE["STATS"]["JAM_RF_GATE_ACCEPT"] += 1
        return target

    tuned_target = TUNE_CFG["TARGET"]
    if source == "direct" and tuned_target in ("L1", "L2", "L3") and reason in JAM_RF_DIRECT_FALLBACK_REASONS:
        STATE["STATS"]["JAM_RF_GATE_MODE"] = f"direct-fallback:{reason}"
        STATE["STATS"]["JAM_RF_GATE_FALLBACK"] += 1
        STATE["STATS"]["JAM_DIRECT_CRC16_ACCEPT"] += 1
        return tuned_target
    STATE["STATS"]["JAM_RF_GATE_MODE"] = f"reject:{reason}"
    STATE["STATS"]["JAM_RF_GATE_REJECT"] += 1
    return ""


# ==========================================
# 2. Protocol parsing
# ==========================================
def is_printable(s):
    return all(32 <= ord(c) <= 126 for c in s)


def source_needs_confirmation(source):
    return source != "direct"


def frame_confirmed(cmd_id, p, source):
    if not source_needs_confirmation(source):
        return True

    now = time.time()
    key = (cmd_id, bytes(p))
    pending = STATE["PENDING_FRAMES"]
    for old_key, (_count, last_seen) in list(pending.items()):
        if now - last_seen > LOW_TRUST_CONFIRM_SEC:
            del pending[old_key]

    count, _last_seen = pending.get(key, (0, 0.0))
    count += 1
    pending[key] = (count, now)
    if count >= LOW_TRUST_CONFIRM_COUNT:
        del pending[key]
        return True

    STATE["STATS"]["FRAME_PENDING"] += 1
    return False


def reject_frame(reason):
    STATE["STATS"]["FRAME_REJECT"] += 1
    if VERBOSE_DSP_LOGS or reason.startswith("jam"):
        STATE["STATS"]["LAST_ERROR"] = f"FRAME REJECT: {reason}"
    return False


def validate_and_parse(cmd_id, p, source="direct"):
    try:
        now = time.time()
        if VERBOSE_DSP_LOGS and now - STATE["STATS"]["LAST_PACKET_LOG"] > 0.5:
            STATE["STATS"]["LAST_ERROR"] = f"CRC16 PASS {source} cmd={hex(cmd_id)} payload={p.hex()[:32]}..."
            STATE["STATS"]["LAST_PACKET_LOG"] = now

        if cmd_id == 0x0A01 and len(p) >= 24:
            v = struct.unpack("<12h", p[:24])
            if not all(0 <= x <= 2800 for x in v):
                return reject_frame("pos range")
            if not frame_confirmed(cmd_id, p[:24], source):
                return False
            D["POS"]["H1"], D["POS"]["E2"], D["POS"]["I3"] = (v[0], v[1]), (v[2], v[3]), (v[4], v[5])
            D["POS"]["I4"], D["POS"]["A6"], D["POS"]["S7"] = (v[6], v[7]), (v[8], v[9]), (v[10], v[11])
        elif cmd_id == 0x0A02 and len(p) >= 12:
            v = struct.unpack("<6H", p[:12])
            if not (v[4] == 0 and all(0 <= hp <= 450 for hp in [v[0], v[1], v[2], v[3], v[5]])):
                return reject_frame("hp range")
            if not frame_confirmed(cmd_id, p[:12], source):
                return False
            D["HP"]["H1"], D["HP"]["E2"], D["HP"]["I3"], D["HP"]["I4"], D["HP"]["S7"] = v[0], v[1], v[2], v[3], v[5]
        elif cmd_id == 0x0A03 and len(p) >= 10:
            v = struct.unpack("<5H", p[:10])
            if not (v[0] <= 100 and all(ammo <= 1500 for ammo in v[1:])):
                return reject_frame("ammo range")
            if not frame_confirmed(cmd_id, p[:10], source):
                return False
            D["AMMO"]["H1"], D["AMMO"]["I3"], D["AMMO"]["I4"], D["AMMO"]["A6"], D["AMMO"]["S7"] = v[0], v[1], v[2], v[3], v[4]
        elif cmd_id == 0x0A04 and len(p) >= 8:
            rem_coin, tot_coin = struct.unpack("<HH", p[:4])
            occu_bits = struct.unpack("<I", p[4:8])[0]
            occu_high = occu_bits & ~0xFFFF
            if not (rem_coin <= tot_coin <= 1000 and occu_high in (0, 0xFFFF0000)):
                return reject_frame("coin/occu range")
            if not frame_confirmed(cmd_id, p[:8], source):
                return False
            D["COIN"]["Rem"], D["COIN"]["Tot"] = rem_coin, tot_coin
            D["OCCU"] = {
                "Sup": occu_bits & 1,
                "Cen": (occu_bits >> 1) & 3,
                "Trp": (occu_bits >> 3) & 1,
                "For": (occu_bits >> 4) & 3,
                "Out": (occu_bits >> 6) & 3,
                "Base": (occu_bits >> 8) & 1,
                "Tun_1": (occu_bits >> 9) & 1,
                "Tun_2": (occu_bits >> 10) & 1,
                "Tun_3": (occu_bits >> 11) & 1,
                "Tun_4": (occu_bits >> 12) & 1,
                "Hig": (occu_bits >> 13) & 1,
                "Fly": (occu_bits >> 14) & 1,
                "Roa": (occu_bits >> 15) & 1,
            }
        elif cmd_id == 0x0A05 and len(p) >= 36:
            v = struct.unpack("<" + "BhBBh" * 5 + "B", p[:36])
            groups = [v[i:i + 5] for i in range(0, 25, 5)]
            if not all(
                0 <= hp <= 100 and 0 <= heat <= 500 and 0 <= defense <= 100 and 0 <= vuln <= 100 and 0 <= atk <= 500
                for hp, heat, defense, vuln, atk in groups
            ) or v[25] not in (0, 1):
                return reject_frame("buff range")
            if not frame_confirmed(cmd_id, p[:36], source):
                return False
            D["BUFF"]["H1"] = {"Hp": v[0], "Heat": v[1], "Def": v[2], "Vul": v[3], "Atk": v[4]}
            D["BUFF"]["E2"] = {"Hp": v[5], "Heat": v[6], "Def": v[7], "Vul": v[8], "Atk": v[9]}
            D["BUFF"]["I3"] = {"Hp": v[10], "Heat": v[11], "Def": v[12], "Vul": v[13], "Atk": v[14]}
            D["BUFF"]["I4"] = {"Hp": v[15], "Heat": v[16], "Def": v[17], "Vul": v[18], "Atk": v[19]}
            D["BUFF"]["S7"] = {"Hp": v[20], "Heat": v[21], "Def": v[22], "Vul": v[23], "Atk": v[24], "Pose": v[25]}
        elif cmd_id == 0x020E and len(p) >= 1:
            if not frame_confirmed(cmd_id, p[:1], source):
                return False
            STATE["ENCRYPT_LVL"] = (p[0] >> 3) & 0x03
        elif cmd_id == 0x0A06 and len(p) >= 6:
            key = p[0:6].decode("ascii", errors="ignore")
            if len(key) == 6 and is_printable(key):
                if not frame_confirmed(cmd_id, p[:6], source):
                    return False
                target = jam_target_for_valid_frame(source)
                if target in ["L1", "L2", "L3"]:
                    STATE["JAM_KEYS_CNT"][target] += 1
                    STATE["JAM_KEYS"][target] = key
                else:
                    reason = STATE["STATS"].get("JAM_RF_GATE_REASON", "")
                    return reject_frame(f"jam rf gate {reason}".strip())
            else:
                return reject_frame("jam key")
        else:
            return reject_frame("unknown cmd")
        D["ID"] = hex(cmd_id)
        return True
    except Exception as exc:
        STATE["STATS"]["LAST_ERROR"] = f"PARSE ERROR: {exc}"
        return False


def get_crc8(data):
    crc = 0xFF
    for b in data:
        crc ^= b
        for _ in range(8):
            crc = (crc >> 1) ^ 0x8C if (crc & 1) else crc >> 1
    return crc & 0xFF


def get_crc16(data):
    crc = 0xFFFF
    for b in data:
        crc ^= b
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA001 if (crc & 1) else crc >> 1
    return crc & 0xFFFF


def get_crc16_kermit(data):
    crc = 0x0000
    for b in data:
        crc ^= b
        for _ in range(8):
            crc = (crc >> 1) ^ 0x8408 if (crc & 1) else crc >> 1
    return crc & 0xFFFF


def cmd_allows_kermit_x3014_crc16(cmd_id):
    if cmd_id == 0x0A06:
        return JAM_ALT_CRC16_ENABLED
    if cmd_id in INFO_CMD_LENGTHS:
        return INFO_ALT_CRC16_ENABLED
    return False


def crc16_mode_for_frame(cmd_id, frame_bytes):
    got_crc = struct.unpack("<H", frame_bytes[-2:])[0]
    frame_no_crc = frame_bytes[:-2]
    if get_crc16(frame_no_crc) == got_crc:
        return "modbus"
    if cmd_allows_kermit_x3014_crc16(cmd_id) and (get_crc16_kermit(frame_no_crc) ^ JAM_ALT_CRC16_XOROUT) == got_crc:
        return "kermit-x3014"
    return ""


def bits_to_bytes(bit_string):
    return bytearray(int(bit_string[i:i + 8], 2) for i in range(0, len(bit_string), 8))


def try_fix_crc16_single_bit(f_bits, expected_lengths, d_len):
    # Header and cmd have already passed CRC8/cmd checks. Try payload/CRC only.
    for bit_idx in range(56, len(f_bits)):
        flipped = "1" if f_bits[bit_idx] == "0" else "0"
        candidate_bits = f_bits[:bit_idx] + flipped + f_bits[bit_idx + 1:]
        candidate = bits_to_bytes(candidate_bits)
        cmd_id = struct.unpack("<H", candidate[5:7])[0]
        if expected_lengths.get(cmd_id) != d_len:
            continue
        crc_mode = crc16_mode_for_frame(cmd_id, candidate)
        if crc_mode:
            return candidate, crc_mode
    return None, ""


def expected_lengths_for_target(target=None):
    target = target or TUNE_CFG["TARGET"]
    return INFO_CMD_LENGTHS if target == "INFO" else JAM_CMD_LENGTHS


def expected_length_values_for_target(target=None):
    target = target or TUNE_CFG["TARGET"]
    return INFO_LENGTH_VALUES if target == "INFO" else JAM_LENGTH_VALUES


def trim_bit_pools(max_bits=POOL_STALE_KEEP_BITS):
    for key, pool in list(STATE["BIT_POOLS"].items()):
        if len(pool) > max_bits:
            STATE["BIT_POOLS"][key] = pool[-max_bits:]


def reset_tracking_state(clear_scores=True):
    STATE["BIT_POOLS"].clear()
    STATE["PENDING_FRAMES"].clear()
    if clear_scores:
        STATE["POOL_SCORES"].clear()
    STATE["TRACK"]["TARGET"] = None
    STATE["TRACK"]["PROFILE"] = None
    STATE["TRACK"]["LOCK_UNTIL"] = 0.0
    STATE["TRACK"]["LAST_CRC16"] = 0.0
    STATE["TRACK"]["MISS"] = 0


def prune_pools(preferred_key=None, max_pools=MAX_ACTIVE_POOLS):
    pools = STATE["BIT_POOLS"]
    if len(pools) <= max_pools:
        return

    def rank(key):
        preferred = 1 if key == preferred_key else 0
        assembler = 1 if key == INFO_CHUNK_POOL_KEY else 0
        return (preferred, assembler, STATE["POOL_SCORES"].get(key, 0.0), len(pools.get(key, "")))

    keep = set(sorted(pools, key=rank, reverse=True)[:max_pools])
    for key in list(pools):
        if key not in keep:
            del pools[key]
            STATE["POOL_SCORES"].pop(key, None)


def append_info_chunk_candidates(chunk_candidates):
    if not chunk_candidates:
        return 0

    best_by_bin = {}
    for idx_bin, score, payload in chunk_candidates:
        current = best_by_bin.get(idx_bin)
        if current is None or score > current[0]:
            best_by_bin[idx_bin] = (score, payload)

    selected = [
        (idx_bin, score, payload)
        for idx_bin, (score, payload) in sorted(best_by_bin.items())
        if score > 0.0
    ][:INFO_CHUNK_MAX_RECORDS]
    if not selected:
        return 0

    bit_pool = STATE["BIT_POOLS"].get(INFO_CHUNK_POOL_KEY, "")
    bit_pool += "".join(payload for _idx_bin, _score, payload in selected)
    if len(bit_pool) > INFO_CHUNK_POOL_MAX_BITS:
        bit_pool = bit_pool[-INFO_CHUNK_POOL_KEEP_BITS:]
    STATE["BIT_POOLS"][INFO_CHUNK_POOL_KEY] = bit_pool
    STATE["STATS"]["ASM_CHUNKS"] += len(selected)
    return len(selected)


def process_pool(
    pool_key,
    max_bits=POOL_MAX_BITS,
    keep_bits=POOL_KEEP_BITS,
    stale_keep_bits=POOL_STALE_KEEP_BITS,
    source="direct",
):
    start_sof = STATE["STATS"]["SOF"]
    start_crc8 = STATE["STATS"]["CRC8"]
    start_crc16 = STATE["STATS"]["CRC16"]
    sof = "10100101"
    bit_pool = STATE["BIT_POOLS"].get(pool_key, "")
    expected_lengths = expected_lengths_for_target(pool_key[0] if pool_key else None)
    expected_length_values = expected_length_values_for_target(pool_key[0] if pool_key else None)
    steps = 0

    if len(bit_pool) > max_bits:
        bit_pool = bit_pool[-keep_bits:]

    while len(bit_pool) >= 72 and steps < 48:
        steps += 1
        idx = bit_pool.find(sof)
        if idx == -1:
            if len(bit_pool) > keep_bits:
                bit_pool = bit_pool[-stale_keep_bits:]
            break

        STATE["STATS"]["SOF"] += 1

        try:
            h_bits = bit_pool[idx:idx + 40]
            if len(h_bits) < 40:
                break
            h_bytes = bits_to_bytes(h_bits)

            if get_crc8(h_bytes[:4]) != h_bytes[4]:
                bit_pool = bit_pool[idx + 1:]
                continue

            STATE["STATS"]["CRC8"] += 1
            d_len = struct.unpack("<H", h_bytes[1:3])[0]
            if d_len not in expected_length_values:
                STATE["STATS"]["LEN_DROP"] += 1
                bit_pool = bit_pool[idx + 8:]
                continue

            total_bits = (d_len + 9) * 8
            if len(bit_pool) < idx + total_bits:
                if idx > 0:
                    bit_pool = bit_pool[idx:]
                break

            f_bits = bit_pool[idx:idx + total_bits]
            f_bytes = bits_to_bytes(f_bits)
            cmd_id = struct.unpack("<H", f_bytes[5:7])[0]
            if expected_lengths.get(cmd_id) != d_len:
                STATE["STATS"]["CMD_DROP"] += 1
                bit_pool = bit_pool[idx + 8:]
                continue

            got_crc = struct.unpack("<H", f_bytes[-2:])[0]
            calc_crc = get_crc16(f_bytes[:-2])
            crc_mode = crc16_mode_for_frame(cmd_id, f_bytes)

            if crc_mode:
                STATE["STATS"]["CRC16"] += 1
                if crc_mode != "modbus":
                    STATE["STATS"]["CRC16_ALT"] += 1
                if validate_and_parse(cmd_id, f_bytes[7:-2], source=source):
                    STATE["STATS"]["LAST_CRC16_TIME"] = time.time()
                    STATE["STATS"]["LAST_CRC16_CMD"] = hex(cmd_id)
                    STATE["STATS"]["LAST_CRC16_MODE"] = crc_mode
                    log_rx_frame(cmd_id, f_bytes[3], source, f_bytes, f_bytes[7:-2], crc16_fixed=False)
                bit_pool = bit_pool[idx + total_bits:]
            else:
                fixed, fixed_crc_mode = try_fix_crc16_single_bit(f_bits, expected_lengths, d_len)
                if fixed is not None:
                    fixed_cmd_id = struct.unpack("<H", fixed[5:7])[0]
                    STATE["STATS"]["CRC16"] += 1
                    STATE["STATS"]["CRC16_FIX"] += 1
                    if fixed_crc_mode != "modbus":
                        STATE["STATS"]["CRC16_ALT"] += 1
                    fixed_source = f"{source}_fix"
                    if validate_and_parse(fixed_cmd_id, fixed[7:-2], source=fixed_source):
                        STATE["STATS"]["LAST_CRC16_TIME"] = time.time()
                        STATE["STATS"]["LAST_CRC16_CMD"] = hex(fixed_cmd_id)
                        STATE["STATS"]["LAST_CRC16_MODE"] = fixed_crc_mode
                        log_rx_frame(
                            fixed_cmd_id,
                            fixed[3],
                            fixed_source,
                            fixed,
                            fixed[7:-2],
                            crc16_fixed=True,
                        )
                    bit_pool = bit_pool[idx + total_bits:]
                else:
                    STATE["STATS"]["CRC16_FAIL"] += 1
                    now = time.time()
                    if VERBOSE_DSP_LOGS and now - STATE["STATS"]["LAST_LOG"] > 0.5:
                        STATE["STATS"]["LAST_ERROR"] = (
                            f"CRC16 FAIL len={d_len} head={f_bytes[:8].hex()} got={got_crc:04x} calc={calc_crc:04x}"
                        )
                        STATE["STATS"]["LAST_LOG"] = now
                    bit_pool = bit_pool[idx + 8:]
        except Exception as exc:
            STATE["STATS"]["LAST_ERROR"] = f"POOL ERROR: {exc}"
            bit_pool = bit_pool[idx + 1:]

    if len(bit_pool) > max_bits:
        bit_pool = bit_pool[-keep_bits:]
    STATE["BIT_POOLS"][pool_key] = bit_pool

    return {
        "SOF": STATE["STATS"]["SOF"] - start_sof,
        "CRC8": STATE["STATS"]["CRC8"] - start_crc8,
        "CRC16": STATE["STATS"]["CRC16"] - start_crc16,
    }


# ==========================================
# 3. DSP helpers
# ==========================================
def make_fft_mask(n, cfg):
    freqs = np.fft.fftfreq(n, d=1.0 / SDR_FS)
    mask = np.zeros(n, dtype=np.float32)

    if cfg["kind"] == "sym_fft":
        cutoff = cfg["cutoff"]
        transition = cfg["transition"]
        abs_f = np.abs(freqs)
        mask[abs_f <= cutoff] = 1.0
        edge = (abs_f > cutoff) & (abs_f < cutoff + transition)
        mask[edge] = 0.5 * (1.0 + np.cos(np.pi * (abs_f[edge] - cutoff) / transition))
        return mask

    pass_low = cfg["pass_low"]
    pass_high = cfg["pass_high"]
    stop_low = cfg["stop_low"]
    stop_high = cfg["stop_high"]

    mask[(freqs >= pass_low) & (freqs <= pass_high)] = 1.0

    left = (freqs > stop_low) & (freqs < pass_low)
    mask[left] = 0.5 * (1.0 - np.cos(np.pi * (freqs[left] - stop_low) / (pass_low - stop_low)))

    right = (freqs > pass_high) & (freqs < stop_high)
    mask[right] = 0.5 * (1.0 + np.cos(np.pi * (freqs[right] - pass_high) / (stop_high - pass_high)))

    return mask


FFT_MASK_CACHE = {}


def get_active_cal_profile(target=None):
    target = target or TUNE_CFG["TARGET"]
    profile = STATE.get("CAL_PROFILE")
    if target == "INFO" and isinstance(profile, dict):
        return profile
    return None


def get_info_rescue_mode(target=None):
    target = target or TUNE_CFG["TARGET"]
    if target != "INFO":
        return None
    profile = get_active_cal_profile(target)
    if profile is not None:
        return profile.get("rescue")
    if STATE.get("INFO_L2_RESCUE"):
        return "L2"
    if STATE.get("INFO_L3_RESCUE"):
        return "L3"
    return None


def get_info_rescue_offset(mode):
    if mode == "L2":
        idx = int(STATE.get("INFO_L2_RESCUE_OFFSET_INDEX", 0)) % len(INFO_L2_RESCUE_LO_OFFSETS)
        STATE["INFO_L2_RESCUE_OFFSET_INDEX"] = idx
        return int(INFO_L2_RESCUE_LO_OFFSETS[idx])
    idx = int(STATE.get("INFO_L3_RESCUE_OFFSET_INDEX", 0)) % len(INFO_L3_RESCUE_LO_OFFSETS)
    STATE["INFO_L3_RESCUE_OFFSET_INDEX"] = idx
    return int(INFO_L3_RESCUE_LO_OFFSETS[idx])


def get_info_rescue_accept_errors(mode):
    return INFO_L2_RESCUE_ACCEPT_AC_ERRORS if mode == "L2" else INFO_L3_RESCUE_ACCEPT_AC_ERRORS


def get_info_rescue_search_errors(mode):
    return INFO_L2_RESCUE_SEARCH_AC_ERRORS if mode == "L2" else INFO_L3_RESCUE_SEARCH_AC_ERRORS


def get_info_rescue_header_limit(mode):
    return INFO_L2_HEADER_MAX_ERRORS if mode == "L2" else INFO_L3_HEADER_MAX_ERRORS


def get_info_rescue_threshold_values(mode):
    if mode == "L2":
        return (-0.35, -0.2, -0.1, 0.0, 0.1, 0.2, 0.35)
    return INFO_L3_THRESHOLD_K_VALUES


def get_info_rescue_polarities(mode):
    return ["+", "-"] if mode == "L2" else ["+"]


def is_info_l3_rescue(target=None):
    return get_info_rescue_mode(target) == "L3"


def is_info_l2_rescue(target=None):
    return get_info_rescue_mode(target) == "L2"


def is_info_rescue(target=None):
    return get_info_rescue_mode(target) is not None


def team_signed_rescue_offset(offset, team=None):
    team = team or TUNE_CFG["TEAM"]
    offset = int(offset)
    return -offset if team == "BLUE" else offset


def mirror_asym_filter_params(cfg):
    if cfg.get("kind") != "asym_fft":
        return dict(cfg)
    mirrored = dict(cfg)
    pass_low = float(cfg["pass_low"])
    pass_high = float(cfg["pass_high"])
    stop_low = float(cfg["stop_low"])
    stop_high = float(cfg["stop_high"])
    mirrored["pass_low"] = -pass_high
    mirrored["pass_high"] = -pass_low
    mirrored["stop_low"] = -stop_high
    mirrored["stop_high"] = -stop_low
    return mirrored


def get_effective_radio_params(team=None, target=None):
    team = team or TUNE_CFG["TEAM"]
    target = target or TUNE_CFG["TARGET"]
    p = dict(RADAR_PARAMS[team][target])
    p["base_freq"] = p["freq"]
    p["lo_offset"] = 0
    p["digital_shift"] = 0
    manual_gain = STATE.get("MANUAL_RX_GAINS", {}).get(target)
    if manual_gain is not None:
        p["gain"] = int(manual_gain)
    p["gain_floor"] = int(p["gain"])
    p["mode"] = "normal"

    cal_profile = get_active_cal_profile(target)
    rescue_mode = get_info_rescue_mode(target)
    if cal_profile is not None:
        offset = team_signed_rescue_offset(cal_profile["offset"], team)
        p["lo_offset"] = offset
        p["digital_shift"] = offset
        p["rf_bw"] = int(cal_profile["rf_bw"])
        p["gain"] = int(cal_profile["gain"])
        p["gain_floor"] = int(cal_profile["gain"])
        p["mode"] = cal_profile["label"]
    elif rescue_mode == "L2":
        offset = team_signed_rescue_offset(get_info_rescue_offset("L2"), team)
        p["lo_offset"] = offset
        p["digital_shift"] = offset
        p["rf_bw"] = max(int(p["rf_bw"]), INFO_L2_RESCUE_RF_BW)
        p["gain"] = INFO_L2_RESCUE_GAIN
        p["gain_floor"] = INFO_L2_RESCUE_GAIN
        p["mode"] = "info_l2_rescue"
    elif rescue_mode == "L3":
        offset = team_signed_rescue_offset(get_info_rescue_offset("L3"), team)
        p["lo_offset"] = offset
        p["digital_shift"] = offset
        p["rf_bw"] = max(int(p["rf_bw"]), INFO_L3_RESCUE_RF_BW)
        if manual_gain is None:
            p["gain"] = INFO_L3_RESCUE_GAIN
            p["gain_floor"] = INFO_L3_RESCUE_GAIN
        p["mode"] = "info_l3_rescue"

    p["rx_lo"] = int(p["base_freq"] + p["lo_offset"])
    return p


def get_effective_filter_params(target=None):
    target = target or TUNE_CFG["TARGET"]
    cal_profile = get_active_cal_profile(target)
    if cal_profile is not None:
        cfg = dict(cal_profile["filter_params"])
        return mirror_asym_filter_params(cfg) if TUNE_CFG["TEAM"] == "BLUE" else cfg
    if is_info_l2_rescue(target):
        cfg = dict(INFO_L2_RESCUE_FILTER_PROFILES["hist248"])
        return mirror_asym_filter_params(cfg) if TUNE_CFG["TEAM"] == "BLUE" else cfg
    if is_info_l3_rescue(target):
        cfg = dict(INFO_L3_RESCUE_FILTER_PARAMS)
        return mirror_asym_filter_params(cfg) if TUNE_CFG["TEAM"] == "BLUE" else cfg
    cfg = dict(FILTER_PARAMS[target])
    return mirror_asym_filter_params(cfg) if TUNE_CFG["TEAM"] == "BLUE" else cfg


def filter_iq(rx_data, cfg):
    key = (len(rx_data), tuple(sorted((k, float(v) if isinstance(v, (int, float)) else v) for k, v in cfg.items())))
    mask = FFT_MASK_CACHE.get(key)
    if mask is None:
        mask = make_fft_mask(len(rx_data), cfg)
        FFT_MASK_CACHE[key] = mask
    return np.fft.ifft(np.fft.fft(rx_data) * mask)


def moving_average(x, n):
    n = max(3, int(n))
    return np.convolve(x, np.ones(n, dtype=np.float64) / float(n), mode="same")


def hamming_distance(a, b, stop_after=None):
    dist = 0
    for c1, c2 in zip(a, b):
        if c1 != c2:
            dist += 1
            if stop_after is not None and dist > stop_after:
                return dist
    return dist


def find_access_candidates(bits, ac_target, max_errors, max_candidates=16, exact_only=False):
    candidates = []

    exact = bits.find(ac_target)
    while exact != -1 and len(candidates) < max_candidates:
        candidates.append((exact, 0))
        exact = bits.find(ac_target, exact + 1)

    if candidates or exact_only or max_errors <= 0:
        return candidates

    ac_len = len(ac_target)
    limit = len(bits) - ac_len
    for i in range(max(0, limit + 1)):
        dist = hamming_distance(bits[i:i + ac_len], ac_target, max_errors)
        if dist <= max_errors:
            candidates.append((i, dist))
            if len(candidates) >= max_candidates:
                break
    return candidates


THRESHOLD_K_VALUES = (0.0, 0.35, -0.35, 0.2, -0.2, 0.1, -0.1)
INFO_L3_THRESHOLD_K_VALUES = (0.35, 0.25, 0.45, 0.15, 0.55, 0.0, -0.1)


def threshold_grid(smoothed, k_values=THRESHOLD_K_VALUES):
    mid = float(np.median(smoothed))
    spread = float(np.percentile(smoothed, 75) - np.percentile(smoothed, 25))
    if spread < 1e-9:
        return [(0.0, mid)]
    return [(k, mid + k * spread) for k in k_values]


def make_pool_key(target, polarity, shift, k):
    return (target, polarity, int(shift), round(float(k), 2))


def plan_score(plan):
    k, _threshold, shift, polarity = plan
    return STATE["POOL_SCORES"].get(make_pool_key(TUNE_CFG["TARGET"], polarity, shift, k), 0.0)


def prioritized_plans(plans):
    return sorted(plans, key=plan_score, reverse=True)


def fast_demod(rx_data, ac_target):
    target = TUNE_CFG["TARGET"]
    cfg = get_effective_filter_params(target)
    p = get_effective_radio_params()
    rescue_mode = get_info_rescue_mode(target)
    info_rescue = rescue_mode is not None

    rx_data = rx_data - np.mean(rx_data)
    digital_shift = p.get("digital_shift", 0)
    if digital_shift:
        n = np.arange(len(rx_data), dtype=np.float64)
        rx_data = rx_data * np.exp(1j * 2.0 * np.pi * digital_shift * n / SDR_FS)

    filtered = filter_iq(rx_data, cfg)

    # Complex phase difference is more stable than unwrap(diff(angle())).
    raw_freq = np.angle(filtered[1:] * np.conj(filtered[:-1]))
    freq = raw_freq - np.median(raw_freq)

    trend_len = int(SPS * cfg.get("trend_bits", 16))
    if len(freq) > trend_len * 2:
        freq = freq - moving_average(freq, trend_len)

    smooth_len = max(5, int(SPS * cfg.get("smooth_frac", 0.34)))
    smoothed = moving_average(freq, smooth_len)

    found = False
    best_diag = None
    best_profile = None
    seen_candidates = set()
    touched_pools = set()
    pool_profiles = {}
    pool_air_scores = {}
    chunk_candidates = []
    best_air_profile = None
    best_air_pool_key = None
    best_air_score = 0.0
    shift_step = max(1, SPS // (32 if info_rescue else 16))
    max_touched_pools = 12 if info_rescue else MAX_TOUCHED_POOLS

    threshold_values = get_info_rescue_threshold_values(rescue_mode) if info_rescue else THRESHOLD_K_VALUES
    threshold_items = threshold_grid(smoothed, threshold_values)
    broad_polarities = get_info_rescue_polarities(rescue_mode) if info_rescue else (["+", "-"] if target == "INFO" else ["+"])
    broad_plans = [
        (k, threshold, shift, polarity)
        for k, threshold in threshold_items
        for shift in range(0, SPS, shift_step)
        for polarity in broad_polarities
    ]
    broad_plans = prioritized_plans(broad_plans)

    now = time.time()
    track = STATE["TRACK"]
    tracking = track["TARGET"] == target and track["PROFILE"] is not None and now < track["LOCK_UNTIL"]

    if tracking:
        profile = track["PROFILE"]
        tracked_thresholds = sorted(threshold_items, key=lambda item: abs(item[0] - profile["k"]))[:3]
        tracked_shifts = sorted({
            profile["shift"] % SPS,
            (profile["shift"] - shift_step) % SPS,
            (profile["shift"] + shift_step) % SPS,
        })
        tracked_plans = [
            (k, threshold, shift, profile["polarity"])
            for k, threshold in tracked_thresholds
            for shift in tracked_shifts
        ]
    else:
        tracked_plans = []

    def scan_plans(plans, exact_only, max_errors, max_candidates):
        nonlocal found, best_diag, best_profile, best_air_profile, best_air_pool_key, best_air_score
        hits = 0
        for k, threshold, shift, polarity in plans:
            samples = smoothed[shift::SPS]
            if polarity == "+":
                bits = "".join("1" if s > threshold else "0" for s in samples)
            else:
                bits = "".join("0" if s > threshold else "1" for s in samples)

            candidates = find_access_candidates(
                bits,
                ac_target,
                max_errors,
                max_candidates=max_candidates,
                exact_only=exact_only,
            )

            for idx, ac_errors in candidates:
                STATE["STATS"]["AC_RAW"] += 1
                if info_rescue and ac_errors > get_info_rescue_accept_errors(rescue_mode):
                    STATE["STATS"]["HDR_DROP"] += 1
                    continue

                air_header = bits[idx + 64:idx + 96]
                header_errors = hamming_distance(air_header, AIR_HEADER) if len(air_header) == 32 else 32
                if info_rescue:
                    header_limit = get_info_rescue_header_limit(rescue_mode)
                else:
                    header_limit = INFO_HEADER_MAX_ERRORS if target == "INFO" else JAM_HEADER_MAX_ERRORS
                if header_errors > header_limit:
                    STATE["STATS"]["HDR_DROP"] += 1
                    continue

                payload = bits[idx + 96:idx + 216]
                if len(payload) != 120:
                    continue

                pool_key = make_pool_key(target, polarity, shift, k)
                dedupe_key = (pool_key, idx, payload)
                if dedupe_key in seen_candidates:
                    continue
                seen_candidates.add(dedupe_key)

                air_score = max(0.0, 2.0 - ac_errors - 0.5 * header_errors)
                if info_rescue and target == "INFO":
                    chunk_candidates.append((int(round(idx / 216.0)), air_score, payload))
                pool_air_scores[pool_key] = pool_air_scores.get(pool_key, 0.0) + air_score
                if air_score > best_air_score:
                    best_air_score = air_score
                    best_air_profile = {"k": k, "shift": shift, "polarity": polarity}
                    best_air_pool_key = pool_key

                if (
                    pool_key not in STATE["BIT_POOLS"]
                    and len(touched_pools) >= max_touched_pools
                    and STATE["POOL_SCORES"].get(pool_key, 0.0) <= 0.0
                ):
                    continue

                STATE["STATS"]["AC"] += 1
                STATE["STATS"]["LAST_AC_TIME"] = time.time()
                found = True
                hits += 1
                new_pool = STATE["BIT_POOLS"].get(pool_key, "") + payload
                if len(new_pool) > POOL_MAX_BITS:
                    new_pool = new_pool[-POOL_KEEP_BITS:]
                STATE["BIT_POOLS"][pool_key] = new_pool
                touched_pools.add(pool_key)

                raw_head = int(payload[:32], 2)
                best_profile = {"k": k, "shift": shift, "polarity": polarity}
                pool_profiles[pool_key] = best_profile
                best_diag = (
                    f"target={target} pol={polarity} ac_err={ac_errors} "
                    f"shift={shift} k={k:.2f} head=0x{raw_head:08x}"
                )
        return hits

    exact_hits = 0
    if tracked_plans:
        exact_hits += scan_plans(tracked_plans, exact_only=True, max_errors=0, max_candidates=8)

    if exact_hits == 0:
        exact_candidates = 4 if info_rescue else 2
        exact_hits += scan_plans(broad_plans, exact_only=True, max_errors=0, max_candidates=exact_candidates)

    rescue_hits = 0
    fuzzy_plan_limit = INFO_L3_RELOCK_PLAN_LIMIT if info_rescue else RESCUE_PLAN_LIMIT
    if exact_hits == 0 and not tracking:
        # Keep fuzzy recovery bounded. Rescue modes can create many near-matches,
        # and an unbounded fuzzy pass makes one demod loop take seconds.
        ac_error_limit = get_info_rescue_search_errors(rescue_mode) if info_rescue else (
            INFO_RESCUE_AC_ERRORS if target == "INFO" else cfg.get("max_ac_errors", 0)
        )
        rescue_hits += scan_plans(
            broad_plans[:fuzzy_plan_limit],
            exact_only=False,
            max_errors=ac_error_limit,
            max_candidates=1 if target == "INFO" else 2,
        )

    if exact_hits == 0 and rescue_hits == 0 and not tracking and not info_rescue:
        scan_plans(
            broad_plans[:fuzzy_plan_limit],
            exact_only=False,
            max_errors=INFO_RESCUE_AC_ERRORS if target == "INFO" else cfg.get("max_ac_errors", 0),
            max_candidates=1,
        )

    assembled_chunks = append_info_chunk_candidates(chunk_candidates) if info_rescue else 0
    if assembled_chunks:
        found = True
        touched_pools.add(INFO_CHUNK_POOL_KEY)
        if best_air_profile is not None:
            pool_profiles[INFO_CHUNK_POOL_KEY] = best_air_profile
        pool_air_scores[INFO_CHUNK_POOL_KEY] = pool_air_scores.get(INFO_CHUNK_POOL_KEY, 0.0) + assembled_chunks * 0.5

    if found:
        pool_delta = {"SOF": 0, "CRC8": 0, "CRC16": 0}
        crc16_profile = None
        crc16_pool_key = None
        crc8_profile = None
        crc8_pool_key = None
        for pool_key in touched_pools:
            if pool_key == INFO_CHUNK_POOL_KEY:
                delta = process_pool(
                    pool_key,
                    max_bits=INFO_CHUNK_POOL_MAX_BITS,
                    keep_bits=INFO_CHUNK_POOL_KEEP_BITS,
                    stale_keep_bits=INFO_CHUNK_POOL_STALE_KEEP_BITS,
                    source="asm",
                )
                STATE["STATS"]["ASM_CRC16"] += delta["CRC16"]
            else:
                delta = process_pool(pool_key)
            score = STATE["POOL_SCORES"].get(pool_key, 0.0) * 0.94
            score += pool_air_scores.get(pool_key, 0.0) * 0.25
            score += delta["CRC8"] * 0.5
            score += delta["CRC16"] * 12.0
            if delta["SOF"] > 0 and delta["CRC8"] == 0:
                score -= min(delta["SOF"], 10) * 0.05
            STATE["POOL_SCORES"][pool_key] = max(0.0, min(score, 200.0))
            for name in pool_delta:
                pool_delta[name] += delta[name]
            if delta["CRC16"] > 0:
                crc16_profile = pool_profiles.get(pool_key)
                crc16_pool_key = pool_key
            elif delta["CRC8"] > 0 and crc8_profile is None:
                crc8_profile = pool_profiles.get(pool_key)
                crc8_pool_key = pool_key

        now = time.time()
        lock_bonus = 0.25
        if pool_delta["CRC8"] > 0:
            lock_bonus = 0.30
        if pool_delta["CRC16"] > 0:
            lock_bonus = 1.0

        track["TARGET"] = target
        track["LOCK_UNTIL"] = max(track["LOCK_UNTIL"], now + lock_bonus)
        if crc16_profile is not None:
            track["PROFILE"] = crc16_profile
            track["LAST_CRC16"] = now
            track["MISS"] = 0
            STATE["STATS"]["LAST_ERROR"] = ""
            prune_pools(preferred_key=crc16_pool_key)
        elif crc8_profile is not None:
            track["PROFILE"] = crc8_profile
            track["MISS"] = 0
            crc8_lock = INFO_L3_CRC8_LOCK_SEC if info_rescue else 0.40
            track["LOCK_UNTIL"] = max(track["LOCK_UNTIL"], now + crc8_lock)
            keep_pools = INFO_L3_CRC8_KEEP_POOLS if info_rescue else MAX_ACTIVE_POOLS
            prune_pools(preferred_key=crc8_pool_key, max_pools=keep_pools)
        elif info_rescue and best_air_profile is not None and best_air_score >= 1.5:
            track["PROFILE"] = best_air_profile
            track["MISS"] = 0
            track["LOCK_UNTIL"] = max(track["LOCK_UNTIL"], now + INFO_L3_AIR_LOCK_SEC)
            prune_pools(preferred_key=best_air_pool_key, max_pools=INFO_L3_AIR_KEEP_POOLS)
        else:
            track["MISS"] = track.get("MISS", 0) + 1
            last_crc16 = track.get("LAST_CRC16", 0.0)
            stale_limit = INFO_L3_CRC16_STALE_SEC if info_rescue else CRC16_STALE_SEC
            stale_crc16 = last_crc16 > 0.0 and (now - last_crc16) > stale_limit
            if track["MISS"] >= 3 or stale_crc16:
                track["PROFILE"] = None
                track["LOCK_UNTIL"] = min(track["LOCK_UNTIL"], now + 0.15)
                trim_bit_pools()
                score_decay = 0.25 if info_rescue else 0.70
                for key in list(STATE["POOL_SCORES"]):
                    STATE["POOL_SCORES"][key] *= score_decay
                prune_pools()

        if VERBOSE_DSP_LOGS and best_diag and now - STATE["STATS"]["LAST_LOG"] > 0.2:
            STATE["STATS"]["LAST_ERROR"] = f"AC HIT {best_diag}"
            STATE["STATS"]["LAST_LOG"] = now
    else:
        now = time.time()
        last_crc16 = track.get("LAST_CRC16", 0.0)
        stale_limit = INFO_L3_CRC16_STALE_SEC if info_rescue else CRC16_STALE_SEC
        if last_crc16 > 0.0 and (now - last_crc16) > stale_limit:
            track["PROFILE"] = None
            track["LOCK_UNTIL"] = min(track["LOCK_UNTIL"], now + 0.15)
            trim_bit_pools()
            if info_rescue:
                for key in list(STATE["POOL_SCORES"]):
                    STATE["POOL_SCORES"][key] *= 0.25
            prune_pools()

    display_locked = time.time() < STATE["TRACK"]["LOCK_UNTIL"]
    return display_locked


# ==========================================
# 4. UI and SDR loop
# ==========================================
def set_rx_gain(sdr, gain, note):
    gain = int(max(RX_GAIN_MIN, min(RX_GAIN_MAX, gain)))
    if STATE["STATS"].get("RX_GAIN") != gain:
        sdr.rx_hardwaregain_chan0 = gain
    STATE["STATS"]["RX_GAIN"] = gain
    STATE["STATS"]["GAIN_NOTE"] = note


def measure_adc(raw_rx):
    scale = 2048.0
    if np.iscomplexobj(raw_rx):
        i_peak = float(np.max(np.abs(np.real(raw_rx)))) / scale
        q_peak = float(np.max(np.abs(np.imag(raw_rx)))) / scale
        rms = float(np.sqrt(np.mean(np.abs(raw_rx) ** 2))) / scale
        return max(i_peak, q_peak), rms
    peak = float(np.max(np.abs(raw_rx))) / scale
    rms = float(np.sqrt(np.mean(np.asarray(raw_rx, dtype=np.float64) ** 2))) / scale
    return peak, rms


def update_rf_diagnostic(adc_peak):
    stats = STATE["STATS"]
    adc_rms = float(stats.get("ADC_RMS", 0.0))
    now = time.time()
    last_crc16 = float(stats.get("LAST_CRC16_TIME", 0.0) or 0.0)
    last_crc16_age = now - last_crc16 if last_crc16 else 999.0
    previous = stats.get("RF_STATE", "")

    if adc_peak >= ADC_SAT_LEVEL or adc_rms >= ADC_BACKOFF_LEVEL:
        state = "SATURATED"
        advice = "ADC high/saturated: reduce RX gain with '-' or add attenuation"
    elif adc_rms < ADC_LOW_LEVEL and stats.get("AC", 0) == 0 and stats.get("AC_RAW", 0) == 0:
        state = "RF_LOW"
        advice = "ADC/RMS too low and no AC: check antenna, cable, geometry, LO/profile"
    elif last_crc16_age < 0.8:
        state = "CRC_LOCKED"
        advice = "CRC16 path active"
    elif stats.get("CRC8", 0) > 0 or stats.get("SOF", 0) > 0 or stats.get("AC", 0) > 0:
        state = "DSP_MARGINAL"
        advice = "CRC8/SOF present but CRC16 stale: try profile failover or quick calibration"
    else:
        state = "SEARCHING"
        advice = "Searching for AC/header"

    stats["RF_STATE"] = state
    stats["RF_ADVICE"] = advice
    if state in ("RF_LOW", "SATURATED") and previous != state:
        stats["LAST_ERROR"] = advice


def _mean_fft_power_near(spec, freqs, center_hz, span_hz):
    mask = np.abs(freqs - float(center_hz)) <= float(span_hz)
    if not np.any(mask):
        return 0.0
    return float(np.mean(spec[mask]))


def update_jam_rf_source(raw_rx, rx_lo):
    target = TUNE_CFG["TARGET"]
    if target not in ("L1", "L2", "L3"):
        STATE["STATS"]["JAM_RF_SOURCE"] = ""
        STATE["STATS"]["JAM_RF_CONF"] = 0.0
        STATE["STATS"]["JAM_RF_OFFSET"] = 0.0
        STATE["STATS"]["JAM_RF_TARGET_OFFSET"] = 0.0
        STATE["STATS"]["JAM_RF_MATCH_STREAK"] = 0
        STATE["STATS"]["JAM_RF_LEVELS"] = ""
        return

    try:
        n = min(len(raw_rx), 65536)
        if n < 4096:
            return
        x = np.asarray(raw_rx[:n], dtype=np.complex64)
        x = x - np.mean(x)
        window = np.hanning(n).astype(np.float32)
        spec = np.abs(np.fft.fftshift(np.fft.fft(x * window))) ** 2
        freqs = np.fft.fftshift(np.fft.fftfreq(n, d=1.0 / SDR_FS))
        levels = {}
        offsets = {}
        for mode in ("L1", "L2", "L3"):
            center = float(RADAR_PARAMS[TUNE_CFG["TEAM"]][mode]["freq"] - int(rx_lo))
            deviation = max(10_000.0, JAM_TX_BW_HZ[mode] / 2.0 - SYMBOL_RATE)
            span = 55_000.0 if mode == "L3" else 85_000.0
            lobe_a = center - deviation
            lobe_b = center + deviation
            levels[mode] = _mean_fft_power_near(spec, freqs, lobe_a, span) + _mean_fft_power_near(spec, freqs, lobe_b, span)
            offsets[mode] = center

        ranked = sorted(levels.items(), key=lambda item: item[1], reverse=True)
        best_mode, best_level = ranked[0]
        second_level = ranked[1][1] if len(ranked) > 1 else 0.0
        conf = (best_level + 1e-18) / (second_level + 1e-18)
        if best_mode == target and conf >= JAM_RF_SOURCE_CONF_MIN:
            STATE["STATS"]["JAM_RF_MATCH_STREAK"] = int(STATE["STATS"].get("JAM_RF_MATCH_STREAK", 0) or 0) + 1
        else:
            STATE["STATS"]["JAM_RF_MATCH_STREAK"] = 0
        STATE["STATS"]["JAM_RF_SOURCE"] = best_mode
        STATE["STATS"]["JAM_RF_CONF"] = conf
        STATE["STATS"]["JAM_RF_OFFSET"] = offsets.get(best_mode, 0.0)
        STATE["STATS"]["JAM_RF_TARGET_OFFSET"] = offsets.get(target, 0.0)
        STATE["STATS"]["JAM_RF_LEVELS"] = " ".join(
            f"{mode}:{10.0 * np.log10(max(level, 1e-18)):.1f}dB" for mode, level in ranked
        )
    except Exception as exc:
        STATE["STATS"]["LAST_ERROR"] = f"JAM RF classify error: {exc}"


def mark_sdr_config_dirty():
    LAST_SDR_CFG["KEY"] = None


def clear_calibration_override(cancel_active=True):
    STATE["CAL_PROFILE"] = None
    if cancel_active:
        STATE["CAL"]["ACTIVE"] = False
        STATE["CAL"]["QUEUE"] = []
        STATE["CAL"]["INDEX"] = -1
        STATE["CAL"]["STAGE"] = "idle"
        STATE["CAL"]["FALLBACK_INDEX"] = 0


def select_tune_target(target, info_l3_rescue=False, info_l2_rescue=False):
    clear_calibration_override(cancel_active=True)
    TUNE_CFG.update({"TARGET": target})
    STATE["STATS"]["JAM_RF_MATCH_STREAK"] = 0
    STATE["STATS"]["JAM_RF_TARGET_CHANGED"] = time.time()
    STATE["INFO_L2_RESCUE"] = bool(target == "INFO" and info_l2_rescue)
    STATE["INFO_L3_RESCUE"] = bool(target == "INFO" and info_l3_rescue)
    if STATE["INFO_L2_RESCUE"]:
        mode = "INFO_L2_RESCUE"
    elif STATE["INFO_L3_RESCUE"]:
        mode = "INFO_L3_RESCUE"
    else:
        mode = f"{target} normal"
    STATE["STATS"]["LAST_ERROR"] = f"manual mode: {mode}"
    mark_sdr_config_dirty()


def cycle_info_rescue_offset(delta):
    clear_calibration_override(cancel_active=True)
    mode = get_info_rescue_mode("INFO") or "L3"
    if mode == "L2":
        idx = int(STATE.get("INFO_L2_RESCUE_OFFSET_INDEX", 0))
        idx = (idx + int(delta)) % len(INFO_L2_RESCUE_LO_OFFSETS)
        STATE["INFO_L2_RESCUE_OFFSET_INDEX"] = idx
        select_tune_target("INFO", info_l2_rescue=True)
        offset = INFO_L2_RESCUE_LO_OFFSETS[idx]
        STATE["STATS"]["LAST_ERROR"] = f"INFO_L2_RESCUE offset={offset / 1e3:.0f}kHz"
    else:
        idx = int(STATE.get("INFO_L3_RESCUE_OFFSET_INDEX", 0))
        idx = (idx + int(delta)) % len(INFO_L3_RESCUE_LO_OFFSETS)
        STATE["INFO_L3_RESCUE_OFFSET_INDEX"] = idx
        select_tune_target("INFO", info_l3_rescue=True)
        offset = INFO_L3_RESCUE_LO_OFFSETS[idx]
        STATE["STATS"]["LAST_ERROR"] = f"INFO_L3_RESCUE offset={offset / 1e3:.0f}kHz"


def adjust_manual_gain(delta):
    clear_calibration_override(cancel_active=True)
    target = TUNE_CFG["TARGET"]
    gains = STATE["MANUAL_RX_GAINS"]
    current = int(gains.get(target, RADAR_PARAMS[TUNE_CFG["TEAM"]][target]["gain"]))
    new_gain = int(max(RX_GAIN_MIN, min(RX_GAIN_MAX, current + int(delta))))
    gains[target] = new_gain
    STATE["STATS"]["GAIN_CEILING"] = new_gain
    STATE["STATS"]["LAST_ERROR"] = f"manual gain {target}={new_gain}"
    mark_sdr_config_dirty()


def apply_sdr_config(sdr):
    target = TUNE_CFG["TARGET"]
    team = TUNE_CFG["TEAM"]
    if target != "INFO":
        STATE["INFO_L3_RESCUE"] = False
        STATE["INFO_L2_RESCUE"] = False
        STATE["CAL_PROFILE"] = None
    rescue = get_info_rescue_mode(target)
    p = get_effective_radio_params(team, target)
    cfg_key = (
        team,
        target,
        rescue or "normal",
        int(p["rx_lo"]),
        int(p["digital_shift"]),
        int(p["gain"]),
        int(p["rf_bw"]),
        p["mode"],
    )

    if LAST_SDR_CFG.get("KEY") == cfg_key:
        return p

    D["BIT_POOL"] = ""
    reset_tracking_state(clear_scores=True)
    for key in ("AC", "SOF", "CRC8", "CRC16"):
        STATE["STATS"][key] = 0
    for key in (
        "AC_RAW",
        "HDR_DROP",
        "LEN_DROP",
        "CMD_DROP",
        "CRC16_FAIL",
        "CRC16_FIX",
        "CRC16_ALT",
        "ASM_CHUNKS",
        "ASM_CRC16",
        "FRAME_REJECT",
        "FRAME_PENDING",
        "JAM_DIRECT_CRC16_ACCEPT",
        "JAM_RF_GATE_ACCEPT",
        "JAM_RF_GATE_FALLBACK",
        "JAM_RF_GATE_REJECT",
    ):
        STATE["STATS"][key] = 0
    STATE["STATS"]["LAST_DATA_SNAPSHOT"] = None
    STATE["STATS"]["LAST_DATA_UPDATE"] = 0.0
    STATE["STATS"]["LAST_DATA_CHANGE"] = "none"
    STATE["STATS"]["LAST_AC_TIME"] = 0.0
    STATE["STATS"]["LAST_CRC16_TIME"] = 0.0
    STATE["STATS"]["LAST_CRC16_CMD"] = "none"
    STATE["STATS"]["LAST_CRC16_MODE"] = "none"
    STATE["STATS"]["JAM_RF_GATE_REASON"] = ""
    STATE["STATS"]["JAM_RF_GATE_MODE"] = ""
    STATE["STATS"]["LAST_ERROR"] = ""
    STATE["STATS"]["LAST_GAIN_ADJUST"] = 0.0
    STATE["STATS"]["GAIN_CEILING"] = int(p["gain"])
    STATE["STATS"]["LAST_CFG_TIME"] = time.time()
    STATE["STATS"]["DSP_MODE"] = p["mode"]

    sdr.rx_lo = int(p["rx_lo"])
    sdr.rx_rf_bandwidth = int(p["rf_bw"])
    set_rx_gain(sdr, int(p["gain"]), "manual")

    LAST_SDR_CFG["KEY"] = cfg_key

    cfg = get_effective_filter_params(target)
    if cfg["kind"] == "sym_fft":
        filt = f"sym cutoff={cfg['cutoff'] / 1e3:.0f}kHz"
    else:
        filt = f"asym pass={cfg['pass_low'] / 1e3:.0f}..{cfg['pass_high'] / 1e3:.0f}kHz"
    msg = (
        f"[CFG] {team}-{target} lo={p['rx_lo'] / 1e6:.3f}MHz shift={p['digital_shift'] / 1e3:.0f}kHz "
        f"gain={p['gain']} rf_bw={p['rf_bw'] / 1e3:.0f}kHz {filt} mode={p['mode']} SPS={SPS}"
    )
    STATE["STATS"]["LAST_CFG_LOG"] = msg
    return p


def make_cal_profile(rescue, offset, gain, rf_bw, filter_name, filter_params):
    return {
        "rescue": rescue,
        "offset": int(offset),
        "gain": int(gain),
        "rf_bw": int(rf_bw),
        "filter_name": filter_name,
        "filter_params": dict(filter_params),
        "label": f"cal_l{rescue[-1]}_{filter_name}_{int(offset / 1000)}k_g{gain}_bw{int(rf_bw / 1000)}",
    }


def calibration_scope_from_state():
    if STATE.get("INFO_L2_RESCUE"):
        return CAL_SCOPE_L2
    if STATE.get("INFO_L3_RESCUE"):
        return CAL_SCOPE_L3
    return CAL_SCOPE_ALL


def build_direct_profile(rescue):
    if rescue == "L2":
        return make_cal_profile(
            "L2",
            80_000,
            40,
            660_000,
            "hist248",
            INFO_L2_RESCUE_FILTER_PROFILES["hist248"],
        )
    return make_cal_profile(
        "L3",
        200_000,
        22,
        760_000,
        "l3tight",
        INFO_L3_RESCUE_FILTER_PROFILES["l3tight"],
    )


def fallback_cal_profiles(rescue):
    if rescue == "L2":
        return [
            make_cal_profile("L2", offset, gain, rf_bw, filter_name, INFO_L2_RESCUE_FILTER_PROFILES[filter_name])
            for offset, gain, rf_bw, filter_name in L2_RESCUE_FALLBACK_SPECS
        ]
    if rescue == "L3":
        return [
            make_cal_profile("L3", offset, gain, rf_bw, filter_name, INFO_L3_RESCUE_FILTER_PROFILES[filter_name])
            for offset, gain, rf_bw, filter_name in L3_RESCUE_FALLBACK_SPECS
        ]
    return []


def current_rescue_mode():
    profile = STATE.get("CAL_PROFILE")
    if isinstance(profile, dict) and profile.get("rescue") in ("L2", "L3"):
        return profile["rescue"]
    if STATE.get("INFO_L2_RESCUE"):
        return "L2"
    if STATE.get("INFO_L3_RESCUE"):
        return "L3"
    return None


def apply_rescue_fallback(rescue, reason, advance=False):
    profiles = fallback_cal_profiles(rescue)
    if not profiles:
        return False
    cal = STATE["CAL"]
    idx = int(cal.get("FALLBACK_INDEX", 0)) % len(profiles)
    profile = profiles[idx]
    if advance:
        cal["FALLBACK_INDEX"] = (idx + 1) % len(profiles)
    STATE["CAL_PROFILE"] = profile
    STATE["INFO_L2_RESCUE"] = profile["rescue"] == "L2"
    STATE["INFO_L3_RESCUE"] = profile["rescue"] == "L3"
    cal["LAST_FAILOVER"] = time.time()
    STATE["STATS"]["LAST_ERROR"] = f"{reason} -> {profile['label']}"
    mark_sdr_config_dirty()
    return True


def load_profile_db():
    try:
        with open(PROFILE_DB_PATH, "r", encoding="utf-8") as f:
            db = json.load(f)
        if isinstance(db, dict):
            db.setdefault("version", 1)
            db.setdefault("profiles", {})
            return db
    except FileNotFoundError:
        pass
    except Exception as exc:
        STATE["STATS"]["LAST_ERROR"] = f"profile DB load failed: {exc}"
    return {"version": 1, "profiles": {}}


def profile_db_key(team, rescue):
    return (
        f"{team}:INFO+{rescue}:"
        f"ant={PROFILE_CONTEXT['antenna']}:"
        f"fe={PROFILE_CONTEXT['front_end']}:"
        f"venue={PROFILE_CONTEXT['venue']}:"
        f"geo={PROFILE_CONTEXT['geometry']}"
    )


def best_profile_from_db(rescue):
    profiles = load_profile_db().get("profiles", {})
    record = profiles.get(profile_db_key(TUNE_CFG["TEAM"], rescue))
    if not isinstance(record, dict):
        candidates = [
            value for value in profiles.values()
            if isinstance(value, dict)
            and value.get("team") == TUNE_CFG["TEAM"]
            and value.get("rescue") == rescue
            and value.get("class") in ("CRC16_LOCK", "CRC8_STABLE")
        ]
        candidates.sort(key=calibration_sort_key, reverse=True)
        record = candidates[0] if candidates else None
    if not isinstance(record, dict):
        return None
    if record.get("class") not in ("CRC16_LOCK", "CRC8_STABLE"):
        return None
    profile = record.get("profile")
    return dict(profile) if isinstance(profile, dict) else None


def save_profile_db_result(result):
    try:
        profile = result["profile"]
        key = profile_db_key(TUNE_CFG["TEAM"], profile["rescue"])
        db = load_profile_db()
        db["profiles"][key] = {
            "saved_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "team": TUNE_CFG["TEAM"],
            "target": "INFO",
            "rescue": profile["rescue"],
            "context": dict(PROFILE_CONTEXT),
            "label": profile["label"],
            "class": result.get("class", "NO_LOCK"),
            "score": result.get("score", 0.0),
            "profile": profile,
            "stats": result.get("stats", {}),
        }
        with open(PROFILE_DB_PATH, "w", encoding="utf-8") as f:
            json.dump(db, f, indent=2, sort_keys=True)
    except Exception as exc:
        STATE["STATS"]["LAST_ERROR"] = f"profile DB save failed: {exc}"


def apply_direct_profile(rescue):
    if rescue == "L2":
        profile = fallback_cal_profiles("L2")[0]
    else:
        profile = best_profile_from_db(rescue) or build_direct_profile(rescue)
    clear_calibration_override(cancel_active=True)
    TUNE_CFG.update({"TARGET": "INFO"})
    STATE["INFO_L2_RESCUE"] = profile["rescue"] == "L2"
    STATE["INFO_L3_RESCUE"] = profile["rescue"] == "L3"
    STATE["CAL_PROFILE"] = profile
    STATE["CAL"]["LAST_FAILOVER"] = time.time()
    STATE["STATS"]["LAST_ERROR"] = f"direct preset -> {profile['label']}"
    mark_sdr_config_dirty()


def build_calibration_queue(scope=None, full=False):
    scope = scope or calibration_scope_from_state()
    queue = []
    l2_offsets = CAL_L2_FULL_OFFSETS if full else CAL_L2_QUICK_OFFSETS
    l3_offsets = CAL_L3_FULL_OFFSETS if full else CAL_L3_QUICK_OFFSETS
    gains = CAL_FULL_GAINS if full else CAL_QUICK_GAINS
    l2_bws = (560_000, 660_000, 760_000) if full else CAL_L2_RF_BWS
    l3_bws = (560_000, 660_000, 760_000) if full else (660_000, 760_000)

    if scope in (CAL_SCOPE_ALL, CAL_SCOPE_L2):
        for filter_name in ("hist248",):
            filter_params = INFO_L2_RESCUE_FILTER_PROFILES[filter_name]
            for rf_bw in l2_bws:
                for offset in l2_offsets:
                    for gain in gains:
                        queue.append(make_cal_profile("L2", offset, gain, rf_bw, filter_name, filter_params))

    if scope in (CAL_SCOPE_ALL, CAL_SCOPE_L3):
        for filter_name in ("l3tight", "l3cur"):
            filter_params = INFO_L3_RESCUE_FILTER_PROFILES[filter_name]
            for rf_bw in l3_bws:
                for offset in l3_offsets:
                    for gain in gains:
                        queue.append(make_cal_profile("L3", offset, gain, rf_bw, filter_name, filter_params))
    return queue


def open_calibration_log():
    os.makedirs(CAL_LOG_DIR, exist_ok=True)
    path = os.path.join(CAL_LOG_DIR, f"cal_{time.strftime('%Y%m%d_%H%M%S')}.csv")
    fields = [
        "timestamp",
        "stage",
        "dwell_sec",
        "score",
        "class",
        "rescue",
        "offset_khz",
        "gain",
        "rf_bw_khz",
        "filter",
        "adc_rms",
        "rf_state",
        "ac_raw",
        "ac",
        "ac_admit_ratio",
        "hdr_drop",
        "sof",
        "crc8",
        "crc8_rate",
        "crc16",
        "crc16_rate",
        "crc16_crc8_ratio",
        "crc16_fail",
        "frame_reject",
        "frame_pending",
        "demod_ms",
        "loop_ms",
    ]
    with open(path, "w", newline="", encoding="utf-8") as f:
        csv.DictWriter(f, fieldnames=fields).writeheader()
    return path, fields


def score_calibration_stats(stats, dwell_sec=None):
    dwell_sec = max(float(dwell_sec or CAL_DWELL_SEC), 0.1)
    crc16_rate = stats["CRC16"] / dwell_sec
    crc8_rate = stats["CRC8"] / dwell_sec
    crc16_crc8_ratio = stats["CRC16"] / max(1.0, float(stats["CRC8"]))
    ac_admit_ratio = stats["AC"] / max(1.0, float(stats["AC_RAW"]))
    crc16 = int(stats.get("CRC16", 0))
    crc8 = int(stats.get("CRC8", 0))
    score = (
        (crc16_rate * 380.0 if crc16 >= 2 else crc16 * 45.0)
        + np.sqrt(max(0, crc8)) * 22.0
        + crc16_crc8_ratio * 55.0
        + ac_admit_ratio * 35.0
        + min(stats["AC"], 60) * 0.65
        - stats["HDR_DROP"] * 1.8
        - stats["CRC16_FAIL"] * (4.5 if crc16 == 0 else 2.5)
        - stats["FRAME_REJECT"] * 5.0
    )
    if crc16 == 1:
        score -= 90.0
    if ADC_TARGET_LOW <= stats["ADC_RMS"] <= ADC_TARGET_HIGH:
        score += 25.0
    elif stats["ADC_RMS"] > ADC_TARGET_HIGH:
        score -= (stats["ADC_RMS"] - ADC_TARGET_HIGH) * 120.0
    elif stats["ADC_RMS"] < ADC_LOW_LEVEL:
        score -= (ADC_LOW_LEVEL - stats["ADC_RMS"]) * 160.0
    if stats["DEMOD_MS"] > 140.0:
        score -= (stats["DEMOD_MS"] - 140.0) * 0.05
    return round(score, 2)


def snapshot_calibration_stats():
    stats = STATE["STATS"]
    return {
        "ADC_RMS": float(stats.get("ADC_RMS", 0.0)),
        "AC_RAW": int(stats.get("AC_RAW", 0)),
        "AC": int(stats.get("AC", 0)),
        "HDR_DROP": int(stats.get("HDR_DROP", 0)),
        "SOF": int(stats.get("SOF", 0)),
        "CRC8": int(stats.get("CRC8", 0)),
        "CRC16": int(stats.get("CRC16", 0)),
        "CRC16_FAIL": int(stats.get("CRC16_FAIL", 0)),
        "FRAME_REJECT": int(stats.get("FRAME_REJECT", 0)),
        "FRAME_PENDING": int(stats.get("FRAME_PENDING", 0)),
        "DEMOD_MS": float(stats.get("DEMOD_MS", 0.0)),
        "LOOP_MS": float(stats.get("LOOP_MS", 0.0)),
        "RF_STATE": stats.get("RF_STATE", ""),
    }


def calibration_derived_metrics(stats, dwell_sec):
    dwell_sec = max(float(dwell_sec or CAL_DWELL_SEC), 0.1)
    crc8 = float(stats.get("CRC8", 0))
    crc16 = float(stats.get("CRC16", 0))
    ac_raw = float(stats.get("AC_RAW", 0))
    ac = float(stats.get("AC", 0))
    return {
        "crc8_rate": crc8 / dwell_sec,
        "crc16_rate": crc16 / dwell_sec,
        "crc16_crc8_ratio": crc16 / max(1.0, crc8),
        "ac_admit_ratio": ac / max(1.0, ac_raw),
    }


def classify_calibration_stats(stats, dwell_sec):
    metrics = calibration_derived_metrics(stats, dwell_sec)
    if stats.get("CRC16", 0) >= 2 and metrics["crc16_rate"] >= 0.15:
        return "CRC16_LOCK"
    if stats.get("CRC16", 0) == 1:
        return "CRC16_WEAK"
    if stats.get("CRC8", 0) > 0 and metrics["crc8_rate"] >= 0.40:
        return "CRC8_STABLE"
    return "NO_LOCK"


def calibration_sort_key(result):
    class_rank = {
        "CRC16_LOCK": 3,
        "CRC8_STABLE": 2,
        "CRC16_WEAK": 1,
        "NO_LOCK": 0,
    }.get(result.get("class", "NO_LOCK"), 0)
    stats = result.get("stats", {})
    crc16 = int(stats.get("CRC16", 0) or 0)
    crc8 = int(stats.get("CRC8", 0) or 0)
    crc16_fail = int(stats.get("CRC16_FAIL", 0) or 0)
    score = float(result.get("score", 0.0) or 0.0)
    return (class_rank, crc16, crc8, -crc16_fail, score)


def make_calibration_result(profile, stage, dwell_sec):
    stats = snapshot_calibration_stats()
    metrics = calibration_derived_metrics(stats, dwell_sec)
    result = {
        "profile": profile,
        "stats": stats,
        "stage": stage,
        "dwell_sec": float(dwell_sec),
        "class": classify_calibration_stats(stats, dwell_sec),
        "score": score_calibration_stats(stats, dwell_sec),
    }
    result.update(metrics)
    return result


def log_calibration_result(result):
    cal = STATE["CAL"]
    path = cal.get("LOG_PATH", "")
    fields = cal.get("FIELDS", [])
    if not path or not fields:
        return
    profile = result["profile"]
    stats = result["stats"]
    row = {
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
        "stage": result.get("stage", ""),
        "dwell_sec": f"{result.get('dwell_sec', 0.0):.2f}",
        "score": result["score"],
        "class": result.get("class", "NO_LOCK"),
        "rescue": profile["rescue"],
        "offset_khz": int(profile["offset"] / 1000),
        "gain": profile["gain"],
        "rf_bw_khz": int(profile["rf_bw"] / 1000),
        "filter": profile["filter_name"],
        "adc_rms": f"{stats['ADC_RMS']:.4f}",
        "rf_state": stats.get("RF_STATE", ""),
        "ac_raw": stats["AC_RAW"],
        "ac": stats["AC"],
        "ac_admit_ratio": f"{result.get('ac_admit_ratio', 0.0):.3f}",
        "hdr_drop": stats["HDR_DROP"],
        "sof": stats["SOF"],
        "crc8": stats["CRC8"],
        "crc8_rate": f"{result.get('crc8_rate', 0.0):.3f}",
        "crc16": stats["CRC16"],
        "crc16_rate": f"{result.get('crc16_rate', 0.0):.3f}",
        "crc16_crc8_ratio": f"{result.get('crc16_crc8_ratio', 0.0):.3f}",
        "crc16_fail": stats["CRC16_FAIL"],
        "frame_reject": stats["FRAME_REJECT"],
        "frame_pending": stats["FRAME_PENDING"],
        "demod_ms": f"{stats['DEMOD_MS']:.2f}",
        "loop_ms": f"{stats['LOOP_MS']:.2f}",
    }
    with open(path, "a", newline="", encoding="utf-8") as f:
        csv.DictWriter(f, fieldnames=fields).writerow(row)


def start_next_calibration_profile():
    cal = STATE["CAL"]
    cal["INDEX"] += 1
    if cal["INDEX"] >= len(cal["QUEUE"]):
        if cal.get("STAGE") == "seed" and cal.get("RESULTS"):
            cal["SEED_RESULTS"] = list(cal["RESULTS"])
            validation_queue = build_validation_queue(cal["SEED_RESULTS"], cal.get("VALIDATE_TOP_K", CAL_TOP_K))
            if validation_queue:
                cal["QUEUE"] = validation_queue
                cal["INDEX"] = -1
                cal["RESULTS"] = []
                cal["STAGE"] = "validate"
                cal["DWELL_SEC"] = CAL_VALIDATE_DWELL_SEC
                STATE["STATS"]["LAST_ERROR"] = (
                    f"CAL validation: top {min(cal.get('VALIDATE_TOP_K', CAL_TOP_K), len(cal['SEED_RESULTS']))} "
                    f"x {cal.get('VALIDATE_ROUNDS', CAL_VALIDATE_ROUNDS)}"
                )
                start_next_calibration_profile()
                return
        finish_calibration()
        return
    profile = cal["QUEUE"][cal["INDEX"]]
    TUNE_CFG.update({"TARGET": "INFO"})
    STATE["INFO_L2_RESCUE"] = profile["rescue"] == "L2"
    STATE["INFO_L3_RESCUE"] = profile["rescue"] == "L3"
    STATE["CAL_PROFILE"] = profile
    cal["STEP_START"] = time.time()
    STATE["STATS"]["LAST_ERROR"] = f"CAL {cal.get('STAGE', 'seed')} {cal['INDEX'] + 1}/{len(cal['QUEUE'])}: {profile['label']}"
    mark_sdr_config_dirty()


def build_validation_queue(results, top_k=CAL_TOP_K):
    ranked = sorted(results, key=calibration_sort_key, reverse=True)
    ranked = ranked[: max(1, min(int(top_k), len(ranked)))]
    queue = []
    for _round in range(CAL_VALIDATE_ROUNDS):
        for result in ranked:
            queue.append(dict(result["profile"]))
    return queue


def start_calibration(full=False):
    clear_calibration_override(cancel_active=False)
    path, fields = open_calibration_log()
    scope = calibration_scope_from_state()
    cal = STATE["CAL"]
    cal["ACTIVE"] = True
    cal["QUEUE"] = build_calibration_queue(scope=scope, full=full)
    cal["INDEX"] = -1
    cal["RESULTS"] = []
    cal["SEED_RESULTS"] = []
    cal["LOG_PATH"] = path
    cal["FIELDS"] = fields
    cal["BEST"] = None
    cal["STAGE"] = "seed"
    cal["SCOPE"] = scope
    cal["FULL"] = bool(full)
    cal["DWELL_SEC"] = CAL_DWELL_SEC
    cal["VALIDATE_TOP_K"] = CAL_TOP_K
    cal["VALIDATE_ROUNDS"] = CAL_VALIDATE_ROUNDS
    cal["FALLBACK_INDEX"] = 0
    start_next_calibration_profile()


def finish_calibration():
    cal = STATE["CAL"]
    cal["ACTIVE"] = False
    results = cal["RESULTS"] or cal.get("SEED_RESULTS", [])
    if not results:
        rescue = current_rescue_mode()
        if rescue and apply_rescue_fallback(rescue, "CAL finished: no results fallback"):
            cal["STAGE"] = "idle"
            return
        STATE["CAL_PROFILE"] = None
        cal["STAGE"] = "idle"
        STATE["STATS"]["LAST_ERROR"] = "CAL finished: no results"
        mark_sdr_config_dirty()
        return
    crc16_locked = [item for item in results if item.get("class") == "CRC16_LOCK"]
    crc8_stable = [item for item in results if item.get("class") == "CRC8_STABLE"]
    pool = crc16_locked or crc8_stable
    if not pool:
        rescue = current_rescue_mode() or calibration_scope_from_state()
        if rescue in (CAL_SCOPE_L2, CAL_SCOPE_L3) and apply_rescue_fallback(rescue, "CAL finished: no stable profile fallback"):
            cal["BEST"] = sorted(results, key=lambda item: item["score"], reverse=True)[0]
            cal["STAGE"] = "idle"
            return
        pool = results
    best = sorted(pool, key=calibration_sort_key, reverse=True)[0]
    cal["BEST"] = best
    STATE["CAL_PROFILE"] = best["profile"]
    STATE["INFO_L2_RESCUE"] = best["profile"]["rescue"] == "L2"
    STATE["INFO_L3_RESCUE"] = best["profile"]["rescue"] == "L3"
    cal["LAST_FAILOVER"] = time.time()
    cal["STAGE"] = "idle"
    STATE["STATS"]["LAST_ERROR"] = f"CAL best {best.get('class', 'NO_LOCK')} score={best['score']:.1f}: {best['profile']['label']}"
    if best.get("class") in ("CRC16_LOCK", "CRC8_STABLE"):
        save_profile_db_result(best)
    mark_sdr_config_dirty()


def update_calibration():
    cal = STATE["CAL"]
    if not cal.get("ACTIVE"):
        return
    dwell_sec = float(cal.get("DWELL_SEC", CAL_DWELL_SEC))
    if time.time() - cal.get("STEP_START", 0.0) < dwell_sec:
        return
    profile = STATE.get("CAL_PROFILE")
    if not isinstance(profile, dict):
        start_next_calibration_profile()
        return
    result = make_calibration_result(profile, cal.get("STAGE", "seed"), dwell_sec)
    cal["RESULTS"].append(result)
    log_calibration_result(result)
    start_next_calibration_profile()


def format_cal_result(result):
    if not result:
        return "none"
    profile = result["profile"]
    stats = result["stats"]
    return (
        f"{result['score']:.1f} {result.get('class', 'NO_LOCK')} {profile['label']} "
        f"crc16:{stats['CRC16']}({result.get('crc16_rate', 0.0):.1f}/s) "
        f"crc8:{stats['CRC8']} ac:{stats['AC']}/{stats['AC_RAW']}"
    )


def sorted_calibration_results(limit=None):
    cal = STATE["CAL"]
    results = list(cal.get("RESULTS", []))
    if not results:
        results = list(cal.get("SEED_RESULTS", []))
    results = sorted(results, key=calibration_sort_key, reverse=True)
    return results[:limit] if limit is not None else results


def maybe_failover_cal_profile():
    cal = STATE["CAL"]
    if cal.get("ACTIVE") or not STATE.get("CAL_PROFILE"):
        return
    now = time.time()
    stats = STATE["STATS"]
    last_crc16 = stats.get("LAST_CRC16_TIME", 0.0)
    crc16_age = (now - last_crc16) if last_crc16 else 999.0
    all_results = sorted_calibration_results()
    crc16_results = [item for item in all_results if item.get("class") == "CRC16_LOCK"]
    crc16_weak_results = [item for item in all_results if item.get("class") == "CRC16_WEAK"]
    crc8_results = [item for item in all_results if item.get("class") == "CRC8_STABLE"]
    if not (crc16_results or crc16_weak_results or crc8_results):
        rescue = current_rescue_mode()
        if (
            rescue in ("L2", "L3")
            and crc16_age > 3.0
            and now - stats.get("LAST_CFG_TIME", 0.0) >= 1.5
            and now - cal.get("LAST_FAILOVER", 0.0) >= 2.5
        ):
            apply_rescue_fallback(rescue, "CAL stale: fallback sweep", advance=True)
        return
    if crc16_results and crc8_results and crc16_age > 3.0:
        results = crc8_results[:5]
    else:
        results = (crc16_results or crc16_weak_results or crc8_results)[:5]
    if len(results) < 2:
        return

    if last_crc16 and now - last_crc16 < 0.8:
        return
    if now - stats.get("LAST_CFG_TIME", 0.0) < 1.5:
        return
    if now - cal.get("LAST_FAILOVER", 0.0) < 1.5:
        return

    current = STATE["CAL_PROFILE"].get("label")
    labels = [item["profile"]["label"] for item in results]
    try:
        idx = labels.index(current)
    except ValueError:
        idx = -1
    next_result = results[(idx + 1) % len(results)]
    STATE["CAL_PROFILE"] = next_result["profile"]
    STATE["INFO_L2_RESCUE"] = next_result["profile"]["rescue"] == "L2"
    STATE["INFO_L3_RESCUE"] = next_result["profile"]["rescue"] == "L3"
    cal["LAST_FAILOVER"] = now
    STATE["STATS"]["LAST_ERROR"] = f"CAL failover {next_result.get('class', 'NO_LOCK')} -> {next_result['profile']['label']}"
    mark_sdr_config_dirty()


def handle_keyboard():
    global TUNE_CFG

    if not key_pressed():
        return True

    try:
        key = read_key()
    except Exception:
        return True

    if key == "q":
        return False
    if key == "r":
        TUNE_CFG.update({"TEAM": "RED"})
        select_tune_target(TUNE_CFG["TARGET"], info_l3_rescue=False)
    elif key == "b":
        TUNE_CFG.update({"TEAM": "BLUE"})
        select_tune_target(TUNE_CFG["TARGET"], info_l3_rescue=False)
    elif key == "1":
        select_tune_target("INFO", info_l3_rescue=False)
    elif key == "2":
        select_tune_target("L1")
    elif key == "3":
        select_tune_target("L2")
    elif key == "4":
        select_tune_target("L3")
    elif key == "5":
        select_tune_target("INFO", info_l3_rescue=True)
    elif key == "6":
        select_tune_target("INFO", info_l2_rescue=True)
    elif key == "7":
        apply_direct_profile("L2")
    elif key == "8":
        apply_direct_profile("L3")
    elif key == "m":
        select_tune_target("INFO", info_l3_rescue=not is_info_rescue("INFO"))
    elif key == "c":
        if STATE["CAL"].get("ACTIVE"):
            clear_calibration_override(cancel_active=True)
            STATE["STATS"]["LAST_ERROR"] = "CAL cancelled"
            mark_sdr_config_dirty()
        else:
            start_calibration(full=False)
    elif key == "f":
        if STATE["CAL"].get("ACTIVE"):
            clear_calibration_override(cancel_active=True)
            STATE["STATS"]["LAST_ERROR"] = "CAL cancelled"
            mark_sdr_config_dirty()
        else:
            start_calibration(full=True)
    elif key in ("]", "}"):
        cycle_info_rescue_offset(1)
    elif key in ("[", "{"):
        cycle_info_rescue_offset(-1)
    elif key in ("+", "="):
        adjust_manual_gain(GAIN_STEP_DB)
    elif key in ("-", "_"):
        adjust_manual_gain(-GAIN_STEP_DB)

    return True


def make_data_snapshot():
    snapshot = {}

    for robot in ["H1", "E2", "I3", "I4", "S7"]:
        snapshot[f"HP.{robot}"] = D["HP"][robot]
    for robot in ["H1", "I3", "I4", "A6", "S7"]:
        snapshot[f"AMMO.{robot}"] = D["AMMO"][robot]

    snapshot["COIN.Rem"] = D["COIN"]["Rem"]
    snapshot["COIN.Tot"] = D["COIN"]["Tot"]

    for robot in ["H1", "E2", "I3", "I4", "A6", "S7"]:
        snapshot[f"POS.{robot}"] = D["POS"][robot]

    for name in ["Sup", "Cen", "Trp", "For", "Out", "Base", "Tun_1", "Tun_2", "Tun_3", "Tun_4", "Hig", "Fly", "Roa"]:
        snapshot[f"OCCU.{name}"] = D["OCCU"][name]

    for robot in ["H1", "E2", "I3", "I4", "S7"]:
        for field, value in D["BUFF"][robot].items():
            snapshot[f"BUFF.{robot}.{field}"] = value

    for lvl in ["L1", "L2", "L3"]:
        snapshot[f"KEY.{lvl}"] = STATE["JAM_KEYS"][lvl]
        snapshot[f"KEY_HITS.{lvl}"] = STATE["JAM_KEYS_CNT"][lvl]

    return snapshot


def update_data_change_state():
    snapshot = make_data_snapshot()
    last_snapshot = STATE["STATS"]["LAST_DATA_SNAPSHOT"]

    if last_snapshot is None:
        STATE["STATS"]["LAST_DATA_SNAPSHOT"] = snapshot
        return

    changes = []
    for key in sorted(snapshot):
        old_value = last_snapshot.get(key)
        new_value = snapshot[key]
        if old_value != new_value:
            changes.append((key, old_value, new_value))

    if not changes:
        return

    parts = [f"{key}:{old_value}->{new_value}" for key, old_value, new_value in changes[:4]]
    if len(changes) > 4:
        parts.append(f"+{len(changes) - 4} more")
    STATE["STATS"]["LAST_DATA_UPDATE"] = time.time()
    STATE["STATS"]["LAST_DATA_CHANGE"] = " | ".join(parts)
    STATE["STATS"]["LAST_DATA_SNAPSHOT"] = snapshot


def enable_ansi_console():
    if not sys.platform.startswith("win"):
        return
    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32
        handle = kernel32.GetStdHandle(-11)
        mode = ctypes.c_uint()
        if kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            kernel32.SetConsoleMode(handle, mode.value | 0x0004)
    except Exception:
        pass


def fit_line(text):
    return str(text)[:DASH_WIDTH].ljust(DASH_WIDTH)


def age_text(timestamp, now=None):
    if not timestamp:
        return "never"
    now = now or time.time()
    age = max(0.0, now - timestamp)
    if age < 10.0:
        return f"{age:.2f}s ago"
    return f"{age:.1f}s ago"


def profile_text():
    profile = STATE["TRACK"].get("PROFILE")
    if not profile:
        return "none"
    return f"pol={profile['polarity']} shift={profile['shift']} k={profile['k']:.2f}"


def pool_text():
    pools = STATE["BIT_POOLS"]
    if not pools:
        return "pools=0 bits=0 max=0"
    bit_counts = [len(pool) for pool in pools.values()]
    return f"pools={len(bit_counts)} bits={sum(bit_counts)} max={max(bit_counts)}"


def init_dashboard():
    enable_ansi_console()
    init_input_terminal()
    sys.stdout.write("\033[?1049h\033[2J\033[H\033[?25l")
    sys.stdout.flush()


def restore_terminal():
    restore_input_terminal()
    sys.stdout.write("\033[?25h\033[?1049l\n")
    sys.stdout.flush()


def render_dashboard(locked, adc_peak):
    stats = STATE["STATS"]
    now = time.time()
    adc_str = "SAT" if adc_peak >= ADC_SAT_LEVEL else (f"{adc_peak:.3f}" if adc_peak < 0.10 else f"{adc_peak:.2f}")
    rms_str = f"{stats['ADC_RMS']:.3f}" if stats["ADC_RMS"] < 0.10 else f"{stats['ADC_RMS']:.2f}"
    status = "LOCKED" if locked else "SEARCHING"
    cfg_log = stats["LAST_CFG_LOG"] or "not configured"
    cal = STATE["CAL"]
    if cal.get("ACTIVE"):
        dwell_sec = float(cal.get("DWELL_SEC", CAL_DWELL_SEC))
        cal_text = (
            f"CAL {cal.get('STAGE', 'seed')} {cal['INDEX'] + 1}/{len(cal['QUEUE'])} "
            f"dwell:{max(0.0, dwell_sec - (now - cal['STEP_START'])):.1f}s "
            f"scope:{cal.get('SCOPE', CAL_SCOPE_ALL)} log:{cal.get('LOG_PATH', '')}"
        )
    else:
        best = cal.get("BEST")
        results = sorted_calibration_results(limit=3)
        if best:
            cal_text = "CAL best " + " | ".join(format_cal_result(item) for item in results)
        else:
            cal_text = "CAL idle"

    b = D["BUFF"]
    jam_code_text = (
        f"JAM_CODE L1:[{STATE['JAM_KEYS']['L1']}]x{STATE['JAM_KEYS_CNT']['L1']}  "
        f"L2:[{STATE['JAM_KEYS']['L2']}]x{STATE['JAM_KEYS_CNT']['L2']}  "
        f"L3:[{STATE['JAM_KEYS']['L3']}]x{STATE['JAM_KEYS_CNT']['L3']}"
    )
    lines = [
        f"V67 L2Cal SDR Receiver {RX_PATCH_TAG} | R/B team | 1 INFO 2 L1 3 L2 4 L3 5 INFO-L3 6 INFO-L2 7 L2 preset 8 L3 preset | C quick F full [/] offset +/- gain | Q quit",
        "=" * 92,
        f"{time.strftime('%H:%M:%S')}  {TUNE_CFG['TEAM']}-{TUNE_CFG['TARGET']}  {status}  ADC:{adc_str}  "
        f"RMS:{rms_str} gain:{stats['RX_GAIN']}/{stats['GAIN_CEILING']}({stats['GAIN_NOTE']})  "
        f"AC:{stats['AC']}/{stats['AC_RAW']} HD:{stats['HDR_DROP']} SOF:{stats['SOF']} CRC8:{stats['CRC8']} CRC16:{stats['CRC16']} cmd:{D['ID']}",
        jam_code_text,
        f"RF state:{stats.get('RF_STATE', 'INIT')}  {stats.get('RF_ADVICE', '')}  rx_log:{stats.get('RX_LOG_PATH', '') or 'off until first frame'}",
        cfg_log,
        f"RM drop len:{stats['LEN_DROP']} cmd:{stats['CMD_DROP']} crc16fail:{stats['CRC16_FAIL']} "
        f"fix:{stats['CRC16_FIX']} alt:{stats.get('CRC16_ALT', 0)} asm:{stats['ASM_CHUNKS']}/{stats['ASM_CRC16']} "
        f"rej:{stats['FRAME_REJECT']} pend:{stats['FRAME_PENDING']} jam_direct:{stats.get('JAM_DIRECT_CRC16_ACCEPT', 0)}",
        f"Timing data:{age_text(stats['LAST_DATA_UPDATE'], now)}  crc16:{age_text(stats['LAST_CRC16_TIME'], now)}({stats['LAST_CRC16_CMD']})  "
        f"mode:{stats.get('LAST_CRC16_MODE', 'none')} loop:{stats['LOOP_MS']:.1f}ms rx:{stats['RX_MS']:.1f}ms demod:{stats['DEMOD_MS']:.1f}ms",
        f"JAM RF source:{stats.get('JAM_RF_SOURCE', '') or 'n/a'} conf:{stats.get('JAM_RF_CONF', 0.0):.2f} "
        f"streak:{stats.get('JAM_RF_MATCH_STREAK', 0)} "
        f"gate:{stats.get('JAM_RF_GATE_MODE', '') or stats.get('JAM_RF_GATE_REASON', '') or 'n/a'} "
        f"offset:{stats.get('JAM_RF_OFFSET', 0.0) / 1000.0:.0f}kHz {stats.get('JAM_RF_LEVELS', '')}",
        f"Track {profile_text()}  lock:{max(0.0, STATE['TRACK']['LOCK_UNTIL'] - now):.2f}s  {pool_text()}",
        cal_text,
        f"Last frame src:{stats.get('LAST_FRAME_SOURCE', '')} seq:{stats.get('LAST_FRAME_SEQ', '')} hex:{stats.get('LAST_FRAME_HEX', '')[:72]}",
        f"Last data: {stats['LAST_DATA_CHANGE']}",
        f"Error: {stats['LAST_ERROR'] or 'none'}",
        "-" * 92,
        jam_code_text,
        f"HP   H1:{D['HP']['H1']:<4} E2:{D['HP']['E2']:<4} I3:{D['HP']['I3']:<4} I4:{D['HP']['I4']:<4} S7:{D['HP']['S7']:<4}    "
        f"AMMO H1:{D['AMMO']['H1']:<4} I3:{D['AMMO']['I3']:<4} I4:{D['AMMO']['I4']:<4} A6:{D['AMMO']['A6']:<4} S7:{D['AMMO']['S7']:<4}",
        f"COIN {D['COIN']['Rem']}/{D['COIN']['Tot']}   POS H1:{D['POS']['H1']} E2:{D['POS']['E2']} I3:{D['POS']['I3']} "
        f"I4:{D['POS']['I4']} A6:{D['POS']['A6']} S7:{D['POS']['S7']}",
        f"OCCU Sup:{D['OCCU']['Sup']} Cen:{D['OCCU']['Cen']} Trp:{D['OCCU']['Trp']} For:{D['OCCU']['For']} Out:{D['OCCU']['Out']} Base:{D['OCCU']['Base']} "
        f"Tun:{D['OCCU']['Tun_1']}{D['OCCU']['Tun_2']}{D['OCCU']['Tun_3']}{D['OCCU']['Tun_4']} Hig:{D['OCCU']['Hig']} Fly:{D['OCCU']['Fly']} Roa:{D['OCCU']['Roa']}",
        "-" * 92,
        f"BUFF H1 Hp:{b['H1']['Hp']} Heat:{b['H1']['Heat']} Def:{b['H1']['Def']} Vul:{b['H1']['Vul']} Atk:{b['H1']['Atk']}  "
        f"E2 Hp:{b['E2']['Hp']} Heat:{b['E2']['Heat']} Def:{b['E2']['Def']} Vul:{b['E2']['Vul']} Atk:{b['E2']['Atk']}",
        f"BUFF I3 Hp:{b['I3']['Hp']} Heat:{b['I3']['Heat']} Def:{b['I3']['Def']} Vul:{b['I3']['Vul']} Atk:{b['I3']['Atk']}  "
        f"I4 Hp:{b['I4']['Hp']} Heat:{b['I4']['Heat']} Def:{b['I4']['Def']} Vul:{b['I4']['Vul']} Atk:{b['I4']['Atk']}",
        f"BUFF S7 Hp:{b['S7']['Hp']} Heat:{b['S7']['Heat']} Def:{b['S7']['Def']} Vul:{b['S7']['Vul']} Atk:{b['S7']['Atk']} Pose:{b['S7']['Pose']}",
        f"JAM L1:[{STATE['JAM_KEYS']['L1']}] {STATE['JAM_KEYS_CNT']['L1']}  "
        f"L2:[{STATE['JAM_KEYS']['L2']}] {STATE['JAM_KEYS_CNT']['L2']}  "
        f"L3:[{STATE['JAM_KEYS']['L3']}] {STATE['JAM_KEYS_CNT']['L3']}",
    ]

    sys.stdout.write("\033[H\033[J" + "\n".join(fit_line(line) for line in lines))
    sys.stdout.flush()


def main():
    init_dashboard()
    ensure_rx_log_ready()
    locked = False
    adc_peak = 0.0
    last_dashboard = 0.0

    try:
        sdr = adi.Pluto("ip:192.168.2.1")
        sdr.sample_rate = SDR_FS
        sdr.rx_buffer_size = RX_BUFFER_SIZE
        sdr.gain_control_mode_chan0 = "manual"
        try:
            sdr.filter = ""
        except Exception:
            pass

        while True:
            loop_start = time.perf_counter()
            if not handle_keyboard():
                break

            p = apply_sdr_config(sdr)
            rx_start = time.perf_counter()
            raw_rx = sdr.rx()
            STATE["STATS"]["RX_MS"] = (time.perf_counter() - rx_start) * 1000.0

            adc_peak, adc_rms = measure_adc(raw_rx)
            STATE["STATS"]["ADC_RMS"] = adc_rms
            update_jam_rf_source(raw_rx, p["rx_lo"])

            if adc_peak >= 1.00:
                locked = False
                STATE["STATS"]["DEMOD_MS"] = 0.0
                STATE["STATS"]["LAST_ERROR"] = "ADC saturated: lower manual gain with -"
            else:
                demod_start = time.perf_counter()
                locked = fast_demod(raw_rx, p["ac"])
                STATE["STATS"]["DEMOD_MS"] = (time.perf_counter() - demod_start) * 1000.0

            update_rf_diagnostic(adc_peak)
            update_data_change_state()
            STATE["STATS"]["LOOP_MS"] = (time.perf_counter() - loop_start) * 1000.0
            update_calibration()
            maybe_failover_cal_profile()

            now = time.time()
            if now - last_dashboard > 0.10:
                render_dashboard(locked, adc_peak)
                last_dashboard = now
    except KeyboardInterrupt:
        pass
    except Exception as exc:
        STATE["STATS"]["LAST_ERROR"] = f"FATAL: {exc}"
        render_dashboard(locked, adc_peak)
        time.sleep(1.5)
    finally:
        restore_terminal()


if __name__ == "__main__":
    main()
