#!/usr/bin/env bash
set -euo pipefail

readonly ACK_TEXT="I_ACKNOWLEDGE_CONTROLLED_RF_BENCH"
readonly DEFAULT_SCAN_DURATION_SEC="30"
readonly DEFAULT_STABILITY_DURATION_SEC="1800"
readonly DEFAULT_CLOSED_LOOP_DURATION_SEC="120"
readonly DEFAULT_RADAR_STOP_TIMEOUT_SEC="30"
readonly IQ_BYTES_PER_SAMPLE="8"
readonly IQ_WINDOW_HEADROOM_RATIO="1.10"
readonly IQ_WINDOW_METADATA_HEADROOM_BYTES="67108864"
readonly DISK_RUN_HEADROOM_BYTES="1073741824"
readonly CONFIRMED_L1_SHA256="8cde16d3fe8230334a9efcb36c81ae105b76b4118f4fe3fc63943aeb791be7cc"

readonly -a COMBINATION_IDS=(
  "sdr_direct"
  "sdr_saw"
  "sdr_lna"
  "sdr_lna_saw"
  "full_chain_10db"
  "full_chain_20db"
)
readonly -a COMBINATION_LABELS=(
  "SDR direct"
  "SDR + SAW"
  "SDR + LNA"
  "SDR + LNA + SAW"
  "complete chain + 10 dB attenuation"
  "complete chain + 20 dB attenuation"
)

MODE="plan"
OUT_DIR=""
OWN_TEAM=""
CABLE_LENGTH_M=""
POWER_SUPPLY=""
TX_DISTANCE_M=""
POLARIZATION=""
RADAR_LOG=""
RADAR_PID=""
RADAR_PID_START=""
RADAR_STOP_TIMEOUT_SEC="$DEFAULT_RADAR_STOP_TIMEOUT_SEC"
CLOSED_LOOP_SOURCE="replay"
L1_IQ=""
ACKNOWLEDGEMENT=""
SCAN_DURATION_SEC="$DEFAULT_SCAN_DURATION_SEC"
STABILITY_DURATION_SEC="$DEFAULT_STABILITY_DURATION_SEC"
CLOSED_LOOP_DURATION_SEC="$DEFAULT_CLOSED_LOOP_DURATION_SEC"
GAIN_STEP_DB="5"
MAX_GAIN_DB="70"
SAMPLE_RATE_HZ="2000000"
MIN_DUTY="0.99"
MIN_CRC16="1"
CLIPPING_THRESHOLD="0.001"
ALLOW_SHORT_DURATION=false
CURRENT_PGID=""
CURRENT_COLLECTOR_PID=""
RESULTS_JSONL=""
AUDIT_JSONL=""
RUN_ELIGIBLE=true

usage() {
  cat <<'EOF'
Usage:
  run_rf_bench.sh plan [options]
  run_rf_bench.sh execute [required options]

Default mode is plan. Plan mode never starts ROS or touches hardware.

Required for execute:
  --acknowledge I_ACKNOWLEDGE_CONTROLLED_RF_BENCH
  --out-dir /absolute/new/run-directory
  --own-team RED|BLUE
  --cable-length-m NUMBER
  --power-supply TEXT
  --tx-distance-m NUMBER
  --polarization H-H|V-V|H-V|V-H|RHCP-RHCP|LHCP-LHCP
  --radar-log /absolute/path/to/current-radar.log
  --radar-pid INTEGER  (the running radar main process; script never stops it)
  --closed-loop-source replay|bench
  --l1-iq /absolute/path/RX_BLUE_ganrao_1.c64   (required for replay)

Execution controls:
  --scan-duration-sec NUMBER       default 30
  --stability-duration-sec NUMBER  default 1800 (30 minutes per USB cable)
  --closed-loop-duration-sec NUMBER default 120
  --radar-stop-timeout-sec NUMBER default 30
  --gain-step-db INTEGER           default 5
  --max-gain-db INTEGER            default 70, allowed 0..73
  --sample-rate-hz INTEGER         default 2000000
  --min-duty NUMBER                default 0.99, must be >0 and <=1
  --min-crc16 INTEGER              default 1, must be >0
  --clipping-threshold NUMBER      default 0.001, must be >0 and <=1
  --allow-short-duration           test only; result is NOT hardware-acceptance eligible
  -h, --help

Execute mode requires an exact READY:<stage-id> line on stdin before every
physical reconfiguration. This is an operator acknowledgement, not proof that
the hardware was configured correctly.
EOF
}

die() {
  printf 'ERROR: %s\n' "$*" >&2
  exit 2
}

require_value() {
  local option="$1"
  local value="${2-}"
  [[ -n "$value" ]] || die "$option requires a value"
}

parse_args() {
  if [[ "${1-}" == "plan" || "${1-}" == "execute" ]]; then
    MODE="$1"
    shift
  fi
  while (($#)); do
    case "$1" in
      -h|--help) usage; exit 0 ;;
      --acknowledge) require_value "$1" "${2-}"; ACKNOWLEDGEMENT="$2"; shift 2 ;;
      --out-dir) require_value "$1" "${2-}"; OUT_DIR="$2"; shift 2 ;;
      --own-team) require_value "$1" "${2-}"; OWN_TEAM="$2"; shift 2 ;;
      --cable-length-m) require_value "$1" "${2-}"; CABLE_LENGTH_M="$2"; shift 2 ;;
      --power-supply) require_value "$1" "${2-}"; POWER_SUPPLY="$2"; shift 2 ;;
      --tx-distance-m) require_value "$1" "${2-}"; TX_DISTANCE_M="$2"; shift 2 ;;
      --polarization) require_value "$1" "${2-}"; POLARIZATION="$2"; shift 2 ;;
      --radar-log) require_value "$1" "${2-}"; RADAR_LOG="$2"; shift 2 ;;
      --radar-pid) require_value "$1" "${2-}"; RADAR_PID="$2"; shift 2 ;;
      --closed-loop-source) require_value "$1" "${2-}"; CLOSED_LOOP_SOURCE="$2"; shift 2 ;;
      --l1-iq) require_value "$1" "${2-}"; L1_IQ="$2"; shift 2 ;;
      --scan-duration-sec) require_value "$1" "${2-}"; SCAN_DURATION_SEC="$2"; shift 2 ;;
      --stability-duration-sec) require_value "$1" "${2-}"; STABILITY_DURATION_SEC="$2"; shift 2 ;;
      --closed-loop-duration-sec) require_value "$1" "${2-}"; CLOSED_LOOP_DURATION_SEC="$2"; shift 2 ;;
      --radar-stop-timeout-sec) require_value "$1" "${2-}"; RADAR_STOP_TIMEOUT_SEC="$2"; shift 2 ;;
      --gain-step-db) require_value "$1" "${2-}"; GAIN_STEP_DB="$2"; shift 2 ;;
      --max-gain-db) require_value "$1" "${2-}"; MAX_GAIN_DB="$2"; shift 2 ;;
      --sample-rate-hz) require_value "$1" "${2-}"; SAMPLE_RATE_HZ="$2"; shift 2 ;;
      --min-duty) require_value "$1" "${2-}"; MIN_DUTY="$2"; shift 2 ;;
      --min-crc16) require_value "$1" "${2-}"; MIN_CRC16="$2"; shift 2 ;;
      --clipping-threshold) require_value "$1" "${2-}"; CLIPPING_THRESHOLD="$2"; shift 2 ;;
      --allow-short-duration) ALLOW_SHORT_DURATION=true; shift ;;
      --) shift; (($# == 0)) || die "positional arguments are not supported" ;;
      *) die "unknown option: $1" ;;
    esac
  done
}

print_plan() {
  printf 'Mode: plan (no hardware action)\n'
  printf 'Gain scan: start=0 dB, step=%s dB, max=%s dB; increase only after state=linear; stop immediately at state=clipped.\n' "$GAIN_STEP_DB" "$MAX_GAIN_DB"
  local index
  for index in "${!COMBINATION_IDS[@]}"; do
    printf '%d\t%s\t%s\n' "$((index + 1))" "${COMBINATION_IDS[$index]}" "${COMBINATION_LABELS[$index]}"
  done
  printf 'Stability order: verified short USB 3 cable for %ss, then competition 3 m USB cable for %ss.\n' "$STABILITY_DURATION_SEC" "$STABILITY_DURATION_SEC"
  printf 'Closed loop: confirmed L1 replay or controlled bench transmission; require exactly one correct /sdr/jam_code and new radar log evidence through phase 2.\n'
}

validate_number_fields() {
  python3 - "$CABLE_LENGTH_M" "$TX_DISTANCE_M" "$SCAN_DURATION_SEC" \
    "$STABILITY_DURATION_SEC" "$CLOSED_LOOP_DURATION_SEC" "$GAIN_STEP_DB" \
    "$MAX_GAIN_DB" "$SAMPLE_RATE_HZ" "$MIN_DUTY" "$MIN_CRC16" \
    "$CLIPPING_THRESHOLD" "$RADAR_STOP_TIMEOUT_SEC" <<'PY'
import math
import sys

(
    cable, distance, scan, stability, closed_loop, gain_step, max_gain,
    sample_rate, min_duty, min_crc16, clipping, radar_stop_timeout,
) = sys.argv[1:]

def finite_positive(name, text):
    try:
        value = float(text)
    except ValueError as exc:
        raise SystemExit(f"{name} must be numeric") from exc
    if not math.isfinite(value) or value <= 0:
        raise SystemExit(f"{name} must be finite and > 0")
    return value

finite_positive("cable length", cable)
finite_positive("transmit distance", distance)
finite_positive("scan duration", scan)
finite_positive("stability duration", stability)
finite_positive("closed-loop duration", closed_loop)
finite_positive("radar stop timeout", radar_stop_timeout)
duty = finite_positive("minimum duty", min_duty)
clipping_value = finite_positive("clipping threshold", clipping)
if duty > 1 or clipping_value > 1:
    raise SystemExit("ratio thresholds must be <= 1")
if duty < 0.99:
    raise SystemExit("minimum duty cannot be lower than the 0.99 acceptance requirement")
for name, text, minimum, maximum in (
    ("gain step", gain_step, 1, 73),
    ("maximum gain", max_gain, 0, 73),
    ("sample rate", sample_rate, 1, 100_000_000),
    ("minimum CRC16", min_crc16, 1, 1_000_000_000),
):
    try:
        value = int(text)
    except ValueError as exc:
        raise SystemExit(f"{name} must be an integer") from exc
    if str(value) != text or not minimum <= value <= maximum:
        raise SystemExit(f"{name} is outside the allowed range")
PY
}

validate_text_field() {
  local name="$1"
  local value="$2"
  [[ -n "$value" ]] || die "$name is required"
  [[ ${#value} -le 128 ]] || die "$name exceeds 128 characters"
  [[ "$value" != *$'\n'* && "$value" != *$'\r'* && "$value" != *$'\t'* ]] \
    || die "$name contains a control character"
}

validate_execute_args() {
  [[ "$ACKNOWLEDGEMENT" == "$ACK_TEXT" ]] || die "explicit RF acknowledgement is missing"
  [[ "$OWN_TEAM" == "RED" || "$OWN_TEAM" == "BLUE" ]] || die "--own-team must be RED or BLUE"
  validate_text_field "--power-supply" "$POWER_SUPPLY"
  case "$POLARIZATION" in
    H-H|V-V|H-V|V-H|RHCP-RHCP|LHCP-LHCP) ;;
    *) die "unsupported polarization value" ;;
  esac
  [[ "$CLOSED_LOOP_SOURCE" == "replay" || "$CLOSED_LOOP_SOURCE" == "bench" ]] \
    || die "--closed-loop-source must be replay or bench"
  [[ -n "$OUT_DIR" && "$OUT_DIR" == /* ]] || die "--out-dir must be an absolute path"
  [[ -n "$RADAR_LOG" && "$RADAR_LOG" == /* ]] || die "--radar-log must be absolute"
  [[ -f "$RADAR_LOG" && ! -L "$RADAR_LOG" ]] || die "radar log must be an existing non-symlink regular file"
  [[ "$RADAR_PID" =~ ^[1-9][0-9]*$ ]] || die "--radar-pid must be a positive integer"
  kill -0 "$RADAR_PID" 2>/dev/null || die "radar process is not running at validation time"
  RADAR_PID_START="$(python3 - "$RADAR_PID" <<'PY'
import os
from pathlib import Path
import sys
pid = int(sys.argv[1])
proc = Path("/proc") / str(pid)
if proc.stat().st_uid != os.getuid():
    raise SystemExit("radar process must be owned by the current user")
stat_text = (proc / "stat").read_text(encoding="ascii")
right = stat_text.rfind(")")
if right < 0:
    raise SystemExit("radar process identity is malformed")
fields = stat_text[right + 2:].split()
if len(fields) < 20 or not fields[19].isdigit():
    raise SystemExit("radar process start time is missing")
print(fields[19])
PY
)" || die "radar process identity validation failed"
  [[ -n "$RADAR_PID_START" ]] || die "radar process identity validation failed"
  if [[ "$CLOSED_LOOP_SOURCE" == "bench" && "$OWN_TEAM" != "RED" ]]; then
    die "confirmed BLUE-L1 bench transmission requires --own-team RED"
  fi
  if [[ "$CLOSED_LOOP_SOURCE" == "replay" ]]; then
    [[ -n "$L1_IQ" && "$L1_IQ" == /* ]] || die "--l1-iq must be absolute for replay"
    [[ -f "$L1_IQ" && ! -L "$L1_IQ" ]] || die "L1 IQ must be an existing non-symlink regular file"
    local actual_sha
    actual_sha="$(sha256sum -- "$L1_IQ" | awk '{print $1}')"
    [[ "$actual_sha" == "$CONFIRMED_L1_SHA256" ]] || die "L1 IQ SHA-256 does not match the confirmed fixture"
    [[ "$SAMPLE_RATE_HZ" == "2000000" ]] || die "confirmed L1 replay requires sample rate 2000000"
  fi
  validate_number_fields || die "numeric validation failed"
  if [[ "$SCAN_DURATION_SEC" != "$DEFAULT_SCAN_DURATION_SEC" \
        || "$STABILITY_DURATION_SEC" != "$DEFAULT_STABILITY_DURATION_SEC" \
        || "$CLOSED_LOOP_DURATION_SEC" != "$DEFAULT_CLOSED_LOOP_DURATION_SEC" \
        || "$RADAR_STOP_TIMEOUT_SEC" != "$DEFAULT_RADAR_STOP_TIMEOUT_SEC" ]]; then
    if [[ "$ALLOW_SHORT_DURATION" != true ]]; then
      die "non-default timing requires --allow-short-duration"
    fi
    RUN_ELIGIBLE=false
  fi
  if [[ "$ALLOW_SHORT_DURATION" == true ]]; then
    RUN_ELIGIBLE=false
  fi
  local command
  for command in python3 ros2 setsid timeout tail sha256sum awk realpath stat dd; do
    command -v "$command" >/dev/null 2>&1 || die "required command is missing: $command"
  done
  python3 -c 'import yaml' >/dev/null 2>&1 || die "Python PyYAML is required before hardware execution"
}

create_output_dir() {
  local parent base canonical_parent
  parent="$(dirname -- "$OUT_DIR")"
  base="$(basename -- "$OUT_DIR")"
  [[ "$base" =~ ^[A-Za-z0-9][A-Za-z0-9._-]*$ ]] || die "output directory basename is unsafe"
  [[ "$base" != "." && "$base" != ".." ]] || die "output directory basename is unsafe"
  [[ -d "$parent" && ! -L "$parent" ]] || die "output parent must be an existing non-symlink directory"
  canonical_parent="$(realpath -e -- "$parent")"
  OUT_DIR="$canonical_parent/$base"
  [[ ! -e "$OUT_DIR" && ! -L "$OUT_DIR" ]] || die "output directory already exists"
  umask 077
  mkdir -- "$OUT_DIR"
  RESULTS_JSONL="$OUT_DIR/results.jsonl"
  AUDIT_JSONL="$OUT_DIR/audit.jsonl"
  : > "$RESULTS_JSONL"
  : > "$AUDIT_JSONL"
}

iq_limit_for_duration() {
  python3 - "$1" "$SAMPLE_RATE_HZ" "$IQ_BYTES_PER_SAMPLE" \
    "$IQ_WINDOW_HEADROOM_RATIO" "$IQ_WINDOW_METADATA_HEADROOM_BYTES" <<'PY'
import math
import sys
duration, rate, bytes_per_sample, ratio, metadata = sys.argv[1:]
value = math.ceil(float(duration) * int(rate) * int(bytes_per_sample) * float(ratio)) + int(metadata)
if value <= 0:
    raise SystemExit("IQ byte limit is invalid")
print(value)
PY
}

preflight_disk() {
  local required
  required="$(python3 - "$SCAN_DURATION_SEC" "$STABILITY_DURATION_SEC" \
    "$CLOSED_LOOP_DURATION_SEC" "$GAIN_STEP_DB" "$MAX_GAIN_DB" \
    "$SAMPLE_RATE_HZ" "$CLOSED_LOOP_SOURCE" "$IQ_BYTES_PER_SAMPLE" \
    "$IQ_WINDOW_HEADROOM_RATIO" "$IQ_WINDOW_METADATA_HEADROOM_BYTES" \
    "$DISK_RUN_HEADROOM_BYTES" <<'PY'
import math
import sys
(
    scan_duration, stability_duration, closed_duration, gain_step, max_gain,
    sample_rate, source, bytes_per_sample, ratio, metadata, run_headroom,
) = sys.argv[1:]
gain_windows = math.ceil(int(max_gain) / int(gain_step)) + 1
def window_bytes(duration):
    return math.ceil(
        float(duration) * int(sample_rate) * int(bytes_per_sample) * float(ratio)
    ) + int(metadata)
total = 6 * gain_windows * window_bytes(scan_duration)
total += 2 * window_bytes(stability_duration)
if source == "bench":
    total += window_bytes(closed_duration)
total += int(run_headroom)
print(total)
PY
)" || die "planned disk requirement calculation failed"
  python3 - "$OUT_DIR/disk_preflight.json" "$OUT_DIR" "$required" <<'PY'
import json
import os
import sys
path, directory, required_text = sys.argv[1:]
required = int(required_text)
stats = os.statvfs(directory)
available = stats.f_bavail * stats.f_frsize
payload = {
    "schema_version": 1,
    "available_bytes": available,
    "planned_required_bytes": required,
    "passed": available >= required,
}
with open(path, "x", encoding="utf-8") as handle:
    json.dump(payload, handle, indent=2, sort_keys=True)
    handle.write("\n")
if available < required:
    raise SystemExit(
        f"insufficient disk space: available={available} required={required}"
    )
PY
}

ensure_window_space() {
  local directory="$1"
  local required="$2"
  python3 - "$directory" "$required" <<'PY'
import os
import sys
directory, required_text = sys.argv[1:]
required = int(required_text)
stats = os.statvfs(directory)
available = stats.f_bavail * stats.f_frsize
if available < required:
    raise SystemExit(
        f"insufficient window disk space: available={available} required={required}"
    )
PY
}

write_metadata() {
  python3 - "$OUT_DIR/run_metadata.json" "$OWN_TEAM" "$CABLE_LENGTH_M" \
    "$POWER_SUPPLY" "$TX_DISTANCE_M" "$POLARIZATION" "$SCAN_DURATION_SEC" \
    "$STABILITY_DURATION_SEC" "$CLOSED_LOOP_DURATION_SEC" "$GAIN_STEP_DB" \
    "$MAX_GAIN_DB" "$SAMPLE_RATE_HZ" "$MIN_DUTY" "$MIN_CRC16" \
    "$CLIPPING_THRESHOLD" "$CLOSED_LOOP_SOURCE" "$RUN_ELIGIBLE" \
    "$RADAR_PID" "$RADAR_PID_START" "$RADAR_STOP_TIMEOUT_SEC" <<'PY'
import datetime
import json
import sys

(
    path, own_team, cable_length, power_supply, tx_distance, polarization,
    scan_duration, stability_duration, closed_loop_duration, gain_step,
    max_gain, sample_rate, min_duty, min_crc16, clipping, source, eligible,
    radar_pid, radar_pid_start, radar_stop_timeout,
) = sys.argv[1:]
payload = {
    "schema_version": 1,
    "created_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    "operator_acknowledgement": "I_ACKNOWLEDGE_CONTROLLED_RF_BENCH",
    "own_team": own_team,
    "fixed_rf_metadata": {
        "cable_length_m": float(cable_length),
        "power_supply": power_supply,
        "tx_distance_m": float(tx_distance),
        "polarization": polarization,
    },
    "durations_sec": {
        "gain_scan": float(scan_duration),
        "usb_stability_each": float(stability_duration),
        "closed_loop": float(closed_loop_duration),
    },
    "gain": {"start_db": 0, "step_db": int(gain_step), "max_db": int(max_gain)},
    "sample_rate_hz": int(sample_rate),
    "thresholds": {
        "minimum_acquisition_duty": float(min_duty),
        "minimum_crc16_count": int(min_crc16),
        "maximum_queue_drops": 0,
        "maximum_libiio_timeouts": 0,
        "rf_clipping_ratio": float(clipping),
    },
    "closed_loop_source": source,
    "confirmed_source": {
        "team": "BLUE",
        "target": "L1",
        "expected_ascii": "fcYqTC",
        "expected_cmd_id": 2566,
        "sha256": "8cde16d3fe8230334a9efcb36c81ae105b76b4118f4fe3fc63943aeb791be7cc" if source == "replay" else None,
    },
    "radar_log_flush": {
        "pid": int(radar_pid),
        "process_start_ticks": radar_pid_start,
        "stop_timeout_sec": float(radar_stop_timeout),
        "script_stops_process": False,
    },
    "hardware_acceptance_eligible": eligible == "true",
    "hardware_acceptance_claimed": False,
}
with open(path, "x", encoding="utf-8") as handle:
    json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
    handle.write("\n")
PY
}

audit_event() {
  local kind="$1"
  local stage="$2"
  local detail="$3"
  python3 - "$AUDIT_JSONL" "$kind" "$stage" "$detail" <<'PY'
import datetime
import json
import sys
path, kind, stage, detail = sys.argv[1:]
record = {
    "schema_version": 1,
    "utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    "kind": kind,
    "stage": stage,
    "detail": detail,
}
with open(path, "a", encoding="utf-8") as handle:
    handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
PY
}

confirm_stage() {
  local stage="$1"
  local label="$2"
  local confirmation=""
  printf '\nConfigure: %s\n' "$label" >&2
  printf 'Verify transmitter authorization, RF cabling, power, distance, and polarization.\n' >&2
  printf 'Enter exactly READY:%s to continue: ' "$stage" >&2
  IFS= read -r confirmation || die "stdin closed before stage acknowledgement: $stage"
  [[ "$confirmation" == "READY:$stage" ]] || die "stage acknowledgement rejected: $stage"
  audit_event "operator_ack" "$stage" "$label"
}

cleanup() {
  local exit_code="${1:-$?}"
  trap - EXIT INT TERM HUP
  if [[ -n "$CURRENT_COLLECTOR_PID" ]] && kill -0 "$CURRENT_COLLECTOR_PID" 2>/dev/null; then
    kill -TERM "$CURRENT_COLLECTOR_PID" 2>/dev/null || true
    wait "$CURRENT_COLLECTOR_PID" 2>/dev/null || true
  fi
  if [[ -n "$CURRENT_PGID" ]] && kill -0 "$CURRENT_PGID" 2>/dev/null; then
    kill -TERM -- "-$CURRENT_PGID" 2>/dev/null || true
    timeout 10 tail --pid="$CURRENT_PGID" -f /dev/null >/dev/null 2>&1 || \
      kill -KILL -- "-$CURRENT_PGID" 2>/dev/null || true
    wait "$CURRENT_PGID" 2>/dev/null || true
  fi
  exit "$exit_code"
}

trap 'cleanup $?' EXIT
trap 'cleanup 130' INT
trap 'cleanup 143' TERM
trap 'cleanup 129' HUP

stop_launch() {
  local pid="$CURRENT_PGID"
  [[ -n "$pid" ]] || return 0
  kill -TERM -- "-$pid" 2>/dev/null || true
  if ! timeout 10 tail --pid="$pid" -f /dev/null >/dev/null 2>&1; then
    kill -KILL -- "-$pid" 2>/dev/null || true
  fi
  wait "$pid" 2>/dev/null || true
  CURRENT_PGID=""
}

collect_status() {
  local output_path="$1"
  local duration_sec="$2"
  python3 - "$output_path" "$duration_sec" <<'PY'
import json
import math
import subprocess
import sys
import time

import yaml

path, duration_text = sys.argv[1:]
duration = float(duration_text)
if not math.isfinite(duration) or duration <= 0:
    raise SystemExit("invalid collection duration")
command = [
    "ros2", "topic", "echo", "/sdr/status", "std_msgs/msg/String",
    "--once",
]

class UniqueKeyLoader(yaml.SafeLoader):
    pass

def construct_unique_mapping(loader, node, deep=False):
    result = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in result:
            raise RuntimeError(f"duplicate YAML key: {key!r}")
        result[key] = loader.construct_object(value_node, deep=deep)
    return result

UniqueKeyLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
    construct_unique_mapping,
)

def unique_json_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise RuntimeError(f"duplicate JSON key: {key!r}")
        result[key] = value
    return result

def receive_one(timeout):
    completed = subprocess.run(
        command,
        check=False,
        capture_output=True,
        text=True,
        stdin=subprocess.DEVNULL,
        timeout=timeout,
    )
    if completed.returncode != 0:
        raise RuntimeError(
            "status echo failed: " + completed.stderr.strip()[:500]
        )
    documents = [
        document
        for document in yaml.load_all(completed.stdout, Loader=UniqueKeyLoader)
        if document is not None
    ]
    if len(documents) != 1:
        raise RuntimeError("status echo must contain exactly one non-empty YAML document")
    envelope = documents[0]
    if type(envelope) is not dict or set(envelope) != {"data"}:
        raise RuntimeError("status echo must be an exact std_msgs/String mapping")
    decoded = envelope["data"]
    if type(decoded) is not str:
        raise RuntimeError("status topic did not contain a String payload")
    status = json.loads(decoded, object_pairs_hook=unique_json_object)
    if type(status) is not dict:
        raise RuntimeError("status JSON must be an object")
    return status

first_deadline = time.monotonic() + 30.0
records = []
while not records:
    remaining = first_deadline - time.monotonic()
    if remaining <= 0:
        raise SystemExit("no /sdr/status message within 30 seconds")
    try:
        status = receive_one(min(10.0, remaining))
    except subprocess.TimeoutExpired:
        continue
    except (RuntimeError, json.JSONDecodeError, yaml.YAMLError) as exc:
        raise SystemExit(f"invalid /sdr/status message: {exc}") from None
    records.append((time.monotonic_ns(), status))

start = time.monotonic()
deadline = start + duration
maximum_records = max(100, int(math.ceil(duration * 10)) + 100)
while time.monotonic() < deadline:
    if len(records) >= maximum_records:
        raise SystemExit("status message resource limit exceeded")
    remaining = deadline - time.monotonic()
    try:
        status = receive_one(max(0.05, min(10.0, remaining)))
    except subprocess.TimeoutExpired:
        continue
    except (RuntimeError, json.JSONDecodeError, yaml.YAMLError) as exc:
        raise SystemExit(f"invalid /sdr/status message: {exc}") from None
    records.append((time.monotonic_ns(), status))
    common_runtime = status.get("common_runtime")
    if isinstance(common_runtime, dict) and common_runtime.get("rf_state") == "clipped":
        break

if not records:
    raise SystemExit("no /sdr/status snapshots")
with open(path, "x", encoding="utf-8") as handle:
    for captured_ns, status in records:
        handle.write(json.dumps(
            {"captured_monotonic_ns": captured_ns, "status": status},
            ensure_ascii=False,
            sort_keys=True,
        ) + "\n")
PY
}

analyze_window() {
  local status_path="$1"
  local launch_log="$2"
  local iq_dir="$3"
  local metrics_path="$4"
  local stage="$5"
  local combination="$6"
  local gain="$7"
  local enforce_stability="$8"
  python3 - "$status_path" "$launch_log" "$iq_dir" "$metrics_path" \
    "$stage" "$combination" "$gain" "$SAMPLE_RATE_HZ" "$MIN_DUTY" \
    "$MIN_CRC16" "$CLIPPING_THRESHOLD" "$enforce_stability" <<'PY'
import json
import math
from pathlib import Path
import re
import sys

(
    status_path, launch_log, iq_dir, metrics_path, stage, combination, gain,
    sample_rate, min_duty, min_crc16, clipping_threshold, enforce_stability,
) = sys.argv[1:]
sample_rate = int(sample_rate)
min_duty = float(min_duty)
min_crc16 = int(min_crc16)
clipping_threshold = float(clipping_threshold)
enforce_stability = enforce_stability == "true"

def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key!r}")
        result[key] = value
    return result

def load_json_line(line, description):
    try:
        value = json.loads(line, object_pairs_hook=unique_object)
    except (json.JSONDecodeError, ValueError) as exc:
        raise SystemExit(f"invalid {description}") from exc
    if type(value) is not dict:
        raise SystemExit(f"{description} must be an exact object")
    return value

def exact_int(value, name, minimum=0):
    if type(value) is not int or value < minimum:
        raise SystemExit(f"{name} is invalid")
    return value

def finite_number(value, name, minimum=None, maximum=None):
    if type(value) not in (int, float) or not math.isfinite(value):
        raise SystemExit(f"{name} is invalid")
    number = float(value)
    if minimum is not None and number < minimum:
        raise SystemExit(f"{name} is invalid")
    if maximum is not None and number > maximum:
        raise SystemExit(f"{name} is invalid")
    return number

status_records = []
with open(status_path, encoding="utf-8") as handle:
    for line_number, line in enumerate(handle, 1):
        item = load_json_line(line, f"normalized status line {line_number}")
        if set(item) != {"captured_monotonic_ns", "status"}:
            raise SystemExit("normalized status record shape is invalid")
        exact_int(item["captured_monotonic_ns"], "captured_monotonic_ns", 1)
        if type(item["status"]) is not dict:
            raise SystemExit("normalized status payload is invalid")
        status_records.append(item)
if not status_records:
    raise SystemExit("at least one status record is required")

runtimes = []
for item in status_records:
    runtime = item["status"].get("common_runtime")
    if type(runtime) is not dict:
        raise SystemExit("common_runtime status is missing")
    if runtime.get("worker_error") is not None or runtime.get("cleanup_error") is not None:
        raise SystemExit("common_runtime reported an error")
    recorder = runtime.get("recorder")
    if type(recorder) is not dict or recorder.get("enabled") is not True:
        raise SystemExit("structured recorder is not enabled")
    stats = recorder.get("stats")
    if type(stats) is not dict or stats.get("worker_error") is not None:
        raise SystemExit("recorder stats are missing or failed")
    state = runtime.get("rf_state")
    if state not in {"linear", "clipped", "too_strong", "too_weak", "disconnected"}:
        raise SystemExit("status RF state is invalid")
    runtimes.append(runtime)
last = runtimes[-1]
last_stats = last["recorder"]["stats"]
acquisition = last.get("acquisition")
device = last.get("device")
if type(acquisition) is not dict or type(device) is not dict:
    raise SystemExit("acquisition or device counters are missing")
counter_fields = {
    "queue_drops": acquisition.get("queue_drops"),
    "acquisition_read_errors": acquisition.get("read_errors"),
    "device_read_errors": device.get("read_errors"),
    "device_reconnects": device.get("reconnects"),
    "recorder_dropped_chunks": last_stats.get("dropped_chunks"),
    "recorder_dropped_events": last_stats.get("dropped_events"),
}
for name, value in counter_fields.items():
    exact_int(value, name)

iq_root = Path(iq_dir).resolve(strict=True)
def controlled_file(pattern, description):
    matches = sorted(iq_root.glob(pattern))
    if len(matches) != 1 or matches[0].is_symlink():
        raise SystemExit(f"exactly one non-symlink {description} is required")
    resolved = matches[0].resolve(strict=True)
    if resolved.parent != iq_root or not resolved.is_file():
        raise SystemExit(f"{description} escaped the controlled IQ directory")
    return resolved

chunks_path = controlled_file("*.chunks.jsonl", "chunks JSONL")
events_path = controlled_file("*.events.jsonl", "events JSONL")

chunk_keys = {
    "chunk_id", "first_sample_index", "sample_rate_hz", "rx_wall_time",
    "rx_monotonic_ns", "lo_hz", "rf_bandwidth_hz", "rx_gain_db",
    "target_version", "context_version", "target", "metadata", "rf_metrics",
    "sample_count", "byte_offset", "byte_length",
}
chunks = []
with chunks_path.open(encoding="utf-8") as handle:
    for line_number, line in enumerate(handle, 1):
        chunk = load_json_line(line, f"chunk line {line_number}")
        if set(chunk) != chunk_keys:
            raise SystemExit(f"chunk line {line_number} schema is invalid")
        chunk_id = exact_int(chunk["chunk_id"], "chunk_id")
        first_index = exact_int(chunk["first_sample_index"], "first_sample_index")
        count = exact_int(chunk["sample_count"], "sample_count", 1)
        rate = exact_int(chunk["sample_rate_hz"], "sample_rate_hz", 1)
        monotonic_ns = exact_int(chunk["rx_monotonic_ns"], "rx_monotonic_ns", 1)
        byte_offset = exact_int(chunk["byte_offset"], "byte_offset")
        byte_length = exact_int(chunk["byte_length"], "byte_length", 1)
        finite_number(chunk["rx_wall_time"], "rx_wall_time", 0)
        if rate != sample_rate or byte_length != count * 8:
            raise SystemExit("chunk sample rate or byte length is invalid")
        if type(chunk["metadata"]) is not dict or type(chunk["rf_metrics"]) is not dict:
            raise SystemExit("chunk metadata or RF metrics is invalid")
        if chunks:
            previous = chunks[-1]
            if chunk_id != previous["chunk_id"] + 1:
                raise SystemExit("chunk_id gap, overlap, or duplicate detected")
            if first_index != previous["first_sample_index"] + previous["sample_count"]:
                raise SystemExit("sample index gap, overlap, or duplicate detected")
            if byte_offset != previous["byte_offset"] + previous["byte_length"]:
                raise SystemExit("chunk byte range gap, overlap, or duplicate detected")
            if monotonic_ns <= previous["rx_monotonic_ns"]:
                raise SystemExit("chunk monotonic time must strictly increase")
        elif chunk_id != 0 or first_index != 0 or byte_offset != 0:
            raise SystemExit("first chunk must start at zero")
        chunks.append({
            "chunk_id": chunk_id,
            "first_sample_index": first_index,
            "sample_count": count,
            "rx_monotonic_ns": monotonic_ns,
            "byte_offset": byte_offset,
            "byte_length": byte_length,
        })
if not chunks:
    raise SystemExit("chunks JSONL is empty")
actual_samples = sum(chunk["sample_count"] for chunk in chunks)
expected_samples = chunks[0]["sample_count"] + (
    (chunks[-1]["rx_monotonic_ns"] - chunks[0]["rx_monotonic_ns"])
    * sample_rate / 1_000_000_000
)
if not math.isfinite(expected_samples) or expected_samples <= 0:
    raise SystemExit("expected sample calculation is invalid")
acquisition_duty = actual_samples / expected_samples
if not math.isfinite(acquisition_duty):
    raise SystemExit("acquisition duty is invalid")

event_keys = {"kind", "payload", "wall_time", "monotonic_ns"}
rf_payload_keys = {
    "chunk_id", "first_sample_index", "last_sample_index", "target_version",
    "context_version", "target", "team", "profile", "adc_code_scale",
    "rf_clipping_ratio", "state", "metrics",
}
command_payload_keys = {
    "command_event_id", "role", "chunk_id", "chunk_first_sample_index",
    "chunk_last_sample_index", "target_version", "context_version", "target",
    "team", "profile", "decoder_id", "cmd_id", "payload", "crc8_ok",
    "crc16_ok", "crc_mode", "receive_wall_time", "first_sample_index",
    "last_sample_index", "evidence",
}
rf_events = []
crc16_count = 0
with events_path.open(encoding="utf-8") as handle:
    for line_number, line in enumerate(handle, 1):
        event = load_json_line(line, f"event line {line_number}")
        if set(event) != event_keys or type(event["kind"]) is not str or type(event["payload"]) is not dict:
            raise SystemExit(f"event line {line_number} schema is invalid")
        finite_number(event["wall_time"], "event wall_time", 0)
        exact_int(event["monotonic_ns"], "event monotonic_ns", 1)
        payload = event["payload"]
        if event["kind"] == "rf_state":
            if set(payload) != rf_payload_keys:
                raise SystemExit("RF event payload schema is invalid")
            chunk_id = exact_int(payload["chunk_id"], "RF chunk_id")
            if chunk_id >= len(chunks) or chunk_id != len(rf_events):
                raise SystemExit("RF event chunk sequence is invalid")
            chunk = chunks[chunk_id]
            if (
                payload["first_sample_index"] != chunk["first_sample_index"]
                or payload["last_sample_index"] != chunk["first_sample_index"] + chunk["sample_count"] - 1
            ):
                raise SystemExit("RF event sample range is invalid")
            state = payload["state"]
            if state not in {"linear", "clipped", "too_strong", "too_weak", "disconnected"}:
                raise SystemExit("RF event state is invalid")
            metrics = payload["metrics"]
            if type(metrics) is not dict or set(metrics) != {"rms", "peak", "clipping_ratio", "sample_count"}:
                raise SystemExit("RF event metrics schema is invalid")
            count = exact_int(metrics["sample_count"], "RF metric sample_count", 1)
            if count != chunk["sample_count"]:
                raise SystemExit("RF event sample_count disagrees with chunk")
            peak = finite_number(metrics["peak"], "RF peak", 0)
            rms = finite_number(metrics["rms"], "RF RMS", 0)
            clipping = finite_number(metrics["clipping_ratio"], "RF clipping ratio", 0, 1)
            if rms > peak:
                raise SystemExit("RF RMS cannot exceed peak")
            if state == "clipped" and clipping < clipping_threshold:
                raise SystemExit("clipped RF event contradicts clipping ratio")
            rf_events.append({"state": state, "peak": peak, "rms": rms, "clipping": clipping, "count": count})
        elif event["kind"] == "command":
            if set(payload) != command_payload_keys:
                raise SystemExit("command event payload schema is invalid")
            if type(payload["crc16_ok"]) is not bool:
                raise SystemExit("command crc16_ok is invalid")
            if payload["crc16_ok"] is True:
                crc16_count += 1
if len(rf_events) != len(chunks):
    raise SystemExit("every chunk must have exactly one authoritative RF event")

observed_states = [event["state"] for event in rf_events]
rf_state = "clipped" if "clipped" in observed_states else observed_states[-1]
peak_max = max(event["peak"] for event in rf_events)
rms_min = min(event["rms"] for event in rf_events)
rms_max = max(event["rms"] for event in rf_events)
metric_samples = sum(event["count"] for event in rf_events)
rms_weighted = math.sqrt(
    sum(event["rms"] ** 2 * event["count"] for event in rf_events) / metric_samples
)
clipping_max = max(event["clipping"] for event in rf_events)

log_text = Path(launch_log).read_text(encoding="utf-8", errors="replace")
timeout_pattern = re.compile(
    r"(?im)(?:libiio|(?:^|[^a-z])iio)[^\n]{0,120}(?:timeout|timed out)"
    r"|(?:timeout|timed out)[^\n]{0,120}(?:libiio|(?:^|[^a-z])iio)"
)
libiio_timeouts = len(timeout_pattern.findall(log_text))

violations = []
if not min_duty <= acquisition_duty <= 1.0:
    violations.append("acquisition_duty_outside_0.99_to_1.0")
if counter_fields["queue_drops"] != 0:
    violations.append("queue_drops_nonzero")
if libiio_timeouts != 0:
    violations.append("libiio_timeouts_nonzero")
if counter_fields["acquisition_read_errors"] != 0 or counter_fields["device_read_errors"] != 0:
    violations.append("read_errors_nonzero")
if counter_fields["device_reconnects"] != 0:
    violations.append("device_reconnects_nonzero")
if counter_fields["recorder_dropped_chunks"] != 0 or counter_fields["recorder_dropped_events"] != 0:
    violations.append("recorder_drops_nonzero")
if enforce_stability:
    if rf_state != "linear":
        violations.append("rf_state_not_linear")
    if crc16_count < min_crc16:
        violations.append("crc16_below_threshold")

result = {
    "schema_version": 1,
    "stage": stage,
    "combination": combination,
    "gain_db": int(gain),
    "status_messages": len(status_records),
    "chunk_count": len(chunks),
    "actual_samples": actual_samples,
    "expected_samples": expected_samples,
    "peak": peak_max,
    "rms": rms_weighted,
    "rms_min": rms_min,
    "rms_max": rms_max,
    "rms_aggregation": "sample_count_weighted_root_mean_square",
    "clipping_ratio": clipping_max,
    "rf_state": rf_state,
    "observed_rf_states": observed_states,
    "crc16_count": crc16_count,
    "acquisition_duty": acquisition_duty,
    "queue_drops": counter_fields["queue_drops"],
    "libiio_timeouts": libiio_timeouts,
    "acquisition_read_errors": counter_fields["acquisition_read_errors"],
    "device_read_errors": counter_fields["device_read_errors"],
    "device_reconnects": counter_fields["device_reconnects"],
    "recorder_dropped_chunks": counter_fields["recorder_dropped_chunks"],
    "recorder_dropped_events": counter_fields["recorder_dropped_events"],
    "stability_thresholds_enforced": enforce_stability,
    "violations": violations,
    "passed": not violations,
}
with open(metrics_path, "x", encoding="utf-8") as handle:
    json.dump(result, handle, ensure_ascii=False, indent=2, sort_keys=True)
    handle.write("\n")
if violations:
    raise SystemExit("window failed: " + ",".join(violations))
PY
}

append_result() {
  local metrics_path="$1"
  local label="$2"
  local usb_cable="$3"
  python3 - "$metrics_path" "$RESULTS_JSONL" "$label" "$usb_cable" \
    "$CABLE_LENGTH_M" "$POWER_SUPPLY" "$TX_DISTANCE_M" "$POLARIZATION" <<'PY'
import json
import sys
metrics_path, results_path, label, usb_cable, cable, power, distance, polarization = sys.argv[1:]
with open(metrics_path, encoding="utf-8") as handle:
    result = json.load(handle)
result["record_type"] = "measurement_window"
result["hardware_label"] = label
result["usb_cable"] = usb_cable or None
result["fixed_rf_metadata"] = {
    "cable_length_m": float(cable),
    "power_supply": power,
    "tx_distance_m": float(distance),
    "polarization": polarization,
}
with open(results_path, "a", encoding="utf-8") as handle:
    handle.write(json.dumps(result, ensure_ascii=False, sort_keys=True) + "\n")
PY
}

append_combination_summary() {
  local combination="$1"
  local label="$2"
  local final_gain="$3"
  local total_crc16="$4"
  local final_linear_metrics="$5"
  python3 - "$RESULTS_JSONL" "$combination" "$label" "$final_gain" \
    "$total_crc16" "$final_linear_metrics" "$CABLE_LENGTH_M" "$POWER_SUPPLY" \
    "$TX_DISTANCE_M" "$POLARIZATION" <<'PY'
import json
import sys
(
    results_path, combination, label, final_gain, total_crc16, metrics_path,
    cable, power, distance, polarization,
) = sys.argv[1:]
with open(metrics_path, encoding="utf-8") as handle:
    final_metrics = json.load(handle)
record = {
    "schema_version": 1,
    "record_type": "combination_summary",
    "combination": combination,
    "hardware_label": label,
    "final_gain_db": int(final_gain),
    "final_linear_peak": final_metrics["peak"],
    "final_linear_rms": final_metrics["rms"],
    "final_linear_clipping_ratio": final_metrics["clipping_ratio"],
    "final_linear_crc16_count": final_metrics["crc16_count"],
    "all_scan_crc16_count": int(total_crc16),
    "fieldable": True,
    "passed": True,
    "fixed_rf_metadata": {
        "cable_length_m": float(cable),
        "power_supply": power,
        "tx_distance_m": float(distance),
        "polarization": polarization,
    },
}
with open(results_path, "a", encoding="utf-8") as handle:
    handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
PY
}

json_field() {
  python3 - "$1" "$2" <<'PY'
import json
import sys
with open(sys.argv[1], encoding="utf-8") as handle:
    value = json.load(handle)
for component in sys.argv[2].split("."):
    value = value[component]
if isinstance(value, bool):
    print("true" if value else "false")
else:
    print(value)
PY
}

run_window() {
  local stage="$1"
  local combination="$2"
  local label="$3"
  local gain="$4"
  local duration="$5"
  local enforce_stability="$6"
  local usb_cable="$7"
  local window_dir="$OUT_DIR/$stage"
  local iq_dir="$window_dir/iq"
  local launch_log="$window_dir/launch.log"
  local status_path="$window_dir/status.jsonl"
  local metrics_path="$window_dir/metrics.json"
  local iq_max_bytes
  mkdir -- "$window_dir" "$iq_dir"
  iq_max_bytes="$(iq_limit_for_duration "$duration")" \
    || { audit_event "window_failed" "$stage" "iq_limit_calculation"; return 1; }
  ensure_window_space "$iq_dir" "$iq_max_bytes" \
    || { audit_event "window_failed" "$stage" "insufficient_disk"; return 1; }
  audit_event "window_start" "$stage" "gain_db=$gain"

  local fallback_self_id
  if [[ "$OWN_TEAM" == "RED" ]]; then fallback_self_id=9; else fallback_self_id=109; fi
  setsid ros2 launch sdr_receiver_py_wrapper competition_receiver.launch.py \
    initial_rx_gain:="$gain" \
    record_iq:=true \
    iq_record_dir:="$iq_dir" \
    iq_record_prefix:="$stage" \
    iq_record_max_sec:="$(python3 -c 'import sys; print(float(sys.argv[1]) + 60)' "$duration")" \
    iq_record_max_bytes:="$iq_max_bytes" \
    iq_record_every_n:=1 \
    rf_clipping_ratio:="$CLIPPING_THRESHOLD" \
    fallback_self_id:="$fallback_self_id" \
    enable_fallback_topics:=true \
    key_retry_limit:=1 \
    </dev/null >"$launch_log" 2>&1 &
  CURRENT_PGID=$!

  local collection_rc=0
  collect_status "$status_path" "$duration" || collection_rc=$?
  if ((collection_rc != 0)); then
    stop_launch
    audit_event "window_failed" "$stage" "status_collection_exit=$collection_rc"
    return "$collection_rc"
  fi
  if ! kill -0 "$CURRENT_PGID" 2>/dev/null; then
    wait "$CURRENT_PGID" 2>/dev/null || true
    CURRENT_PGID=""
    audit_event "window_failed" "$stage" "receiver_exited_before_measurement_end"
    return 1
  fi
  stop_launch
  if ! analyze_window "$status_path" "$launch_log" "$iq_dir" "$metrics_path" \
    "$stage" "$combination" "$gain" "$enforce_stability"; then
    audit_event "window_failed" "$stage" "offline_analysis_failed"
    return 1
  fi
  if ! append_result "$metrics_path" "$label" "$usb_cable"; then
    audit_event "window_failed" "$stage" "result_append_failed"
    return 1
  fi
  audit_event "window_complete" "$stage" "metrics=$metrics_path"
  printf '%s\n' "$metrics_path"
}

run_combination() {
  local ordinal="$1"
  local combination="$2"
  local label="$3"
  confirm_stage "$combination" "$label"
  local gain=0
  local last_linear_gain=""
  local last_linear_crc16=0
  local last_linear_metrics=""
  local total_crc16=0
  while ((gain <= MAX_GAIN_DB)); do
    local stage metrics state crc16
    stage="$(printf 'matrix_%02d_%s_gain_%02d' "$ordinal" "$combination" "$gain")"
    if ! metrics="$(run_window "$stage" "$combination" "$label" "$gain" "$SCAN_DURATION_SEC" false "")"; then
      die "$label measurement window failed at gain $gain"
    fi
    state="$(json_field "$metrics" rf_state)"
    crc16="$(json_field "$metrics" crc16_count)"
    total_crc16=$((total_crc16 + crc16))
    case "$state" in
      linear)
        last_linear_gain="$gain"
        last_linear_crc16="$crc16"
        last_linear_metrics="$metrics"
        if ((gain == MAX_GAIN_DB)); then break; fi
        gain=$((gain + GAIN_STEP_DB))
        if ((gain > MAX_GAIN_DB)); then gain="$MAX_GAIN_DB"; fi
        ;;
      clipped)
        audit_event "gain_scan_stop" "$combination" "RF_CLIPPED at gain_db=$gain"
        break
        ;;
      *)
        audit_event "gain_scan_stop" "$combination" "non-linear state=$state at gain_db=$gain"
        die "$label is not fieldable: gain may increase only after RF linear; observed $state"
        ;;
    esac
  done
  [[ -n "$last_linear_gain" ]] || die "$label is not fieldable: no RF linear gain"
  ((last_linear_crc16 >= MIN_CRC16)) \
    || die "$label is not fieldable: final linear CRC16 count $last_linear_crc16 is below $MIN_CRC16"
  append_combination_summary "$combination" "$label" "$last_linear_gain" \
    "$total_crc16" "$last_linear_metrics"
  audit_event "combination_complete" "$combination" "final_gain_db=$last_linear_gain crc16_count=$total_crc16"
  printf '%s\n' "$last_linear_gain"
}

start_jam_collector() {
  local output_path="$1"
  local ready_path="$2"
  local duration="$3"
  python3 - "$output_path" "$ready_path" "$duration" <<'PY'
import json
import math
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import time

import yaml

output_path, ready_path, duration_text = sys.argv[1:]
duration = float(duration_text)
if not math.isfinite(duration) or duration <= 0:
    raise SystemExit("invalid JamCode collection duration")
echo = subprocess.Popen(
    ["ros2", "topic", "echo", "/sdr/jam_code", "sdr_receiver/msg/JamCode"],
    stdout=subprocess.PIPE,
    stderr=subprocess.PIPE,
    stdin=subprocess.DEVNULL,
    text=True,
    bufsize=1,
    start_new_session=True,
)

def interrupted(_signum, _frame):
    raise InterruptedError("collector interrupted")

signal.signal(signal.SIGTERM, interrupted)
signal.signal(signal.SIGINT, interrupted)
signal.signal(signal.SIGHUP, interrupted)
try:
    graph_deadline = time.monotonic() + 20.0
    while True:
        if echo.poll() is not None:
            raise SystemExit("JamCode topic monitor exited before readiness")
        info = subprocess.run(
            ["ros2", "topic", "info", "/sdr/jam_code", "--verbose"],
            check=False,
            capture_output=True,
            text=True,
            stdin=subprocess.DEVNULL,
            timeout=5.0,
        )
        match = re.search(r"Subscription count:\s*(\d+)", info.stdout)
        if info.returncode == 0 and match and int(match.group(1)) >= 1:
            break
        if time.monotonic() >= graph_deadline:
            raise SystemExit("JamCode monitor was not visible in the ROS graph")
        time.sleep(0.1)
    Path(ready_path).touch(exist_ok=False)
    deadline = time.monotonic() + duration
    while time.monotonic() < deadline:
        if echo.poll() is not None:
            raise SystemExit("JamCode topic monitor exited during collection")
        time.sleep(min(0.1, deadline - time.monotonic()))
    os.killpg(echo.pid, signal.SIGTERM)
    try:
        stdout, _stderr = echo.communicate(timeout=5)
    except subprocess.TimeoutExpired:
        os.killpg(echo.pid, signal.SIGKILL)
        stdout, _stderr = echo.communicate(timeout=5)
    messages = []
    for message in yaml.safe_load_all(stdout):
        if message is None:
            continue
        if not isinstance(message, dict):
            raise SystemExit("JamCode message is not a mapping")
        messages.append({
            "captured_monotonic_ns": time.monotonic_ns(),
            "message": message,
        })
        if len(messages) > 100:
            raise SystemExit("JamCode message resource limit exceeded")
    with open(output_path, "x", encoding="utf-8") as handle:
        for message in messages:
            handle.write(json.dumps(message, ensure_ascii=False, sort_keys=True) + "\n")
finally:
    if echo.poll() is None:
        os.killpg(echo.pid, signal.SIGTERM)
        try:
            echo.wait(timeout=5)
        except subprocess.TimeoutExpired:
            os.killpg(echo.pid, signal.SIGKILL)
            echo.wait(timeout=5)
PY
}

wait_for_ready_file() {
  local path="$1"
  local pid="$2"
  local deadline=$((SECONDS + 25))
  while [[ ! -e "$path" ]]; do
    kill -0 "$pid" 2>/dev/null || { wait "$pid" 2>/dev/null || true; die "topic collector failed before readiness"; }
    ((SECONDS < deadline)) || die "topic collector readiness timed out"
    read -r -t 0.1 _ </dev/null || true
  done
  [[ -f "$path" && ! -L "$path" ]] || die "collector readiness marker is invalid"
}

require_same_radar_process() {
  python3 - "$RADAR_PID" "$RADAR_PID_START" <<'PY'
from pathlib import Path
import sys
pid, expected_start = sys.argv[1:]
stat_path = Path("/proc") / pid / "stat"
if not stat_path.is_file():
    raise SystemExit("radar process stopped before closed-loop collection")
text = stat_path.read_text(encoding="ascii")
right = text.rfind(")")
fields = text[right + 2:].split() if right >= 0 else []
if len(fields) < 20 or fields[19] != expected_start:
    raise SystemExit("radar PID was reused or changed identity")
PY
}

wait_for_radar_stop_and_flush() {
  python3 - "$RADAR_PID" "$RADAR_PID_START" "$RADAR_STOP_TIMEOUT_SEC" "$RADAR_LOG" <<'PY'
from pathlib import Path
import math
import os
import sys
import time

pid, expected_start, timeout_text, log_text = sys.argv[1:]
timeout = float(timeout_text)
if not math.isfinite(timeout) or timeout <= 0:
    raise SystemExit("radar stop timeout is invalid")
proc_stat = Path("/proc") / pid / "stat"
log_path = Path(log_text)
deadline = time.monotonic() + timeout
while proc_stat.exists():
    text = proc_stat.read_text(encoding="ascii")
    right = text.rfind(")")
    fields = text[right + 2:].split() if right >= 0 else []
    if len(fields) < 20 or fields[19] != expected_start:
        raise SystemExit("radar PID was reused before flush verification")
    if time.monotonic() >= deadline:
        raise SystemExit("radar process did not stop within the bounded flush timeout")
    time.sleep(min(0.1, deadline - time.monotonic()))

stable_size = None
stable_observations = 0
while time.monotonic() < deadline:
    if log_path.is_symlink() or not log_path.is_file():
        raise SystemExit("radar log became invalid during flush verification")
    size = log_path.stat().st_size
    if size == stable_size:
        stable_observations += 1
    else:
        stable_size = size
        stable_observations = 1
    if stable_observations >= 3:
        print(size)
        break
    time.sleep(min(0.1, deadline - time.monotonic()))
else:
    raise SystemExit("radar log did not become stable after process stop")
PY
}

run_closed_loop() {
  local gain="$1"
  require_same_radar_process || die "radar process identity is not valid for closed loop"
  if [[ "$CLOSED_LOOP_SOURCE" == "bench" ]]; then
    confirm_stage "confirmed_blue_l1_fcyqtc_transmitter" \
      "transmitter configured as confirmed BLUE/L1/fcYqTC source for RED receiver"
    audit_event "confirmed_source" "closed_loop" "bench BLUE L1 fcYqTC cmd_id=2566"
  else
    audit_event "confirmed_source" "closed_loop" \
      "replay BLUE L1 fcYqTC sha256=$CONFIRMED_L1_SHA256"
  fi
  confirm_stage "closed_loop" "ROS closed loop ($CLOSED_LOOP_SOURCE, confirmed L1)"
  local stage_dir="$OUT_DIR/closed_loop"
  local launch_log="$stage_dir/receiver.log"
  local jam_jsonl="$stage_dir/jam_codes.jsonl"
  local ready_file="$stage_dir/monitor.ready"
  local radar_delta="$stage_dir/radar.delta.log"
  local result_path="$stage_dir/result.json"
  mkdir -- "$stage_dir"
  local radar_start_size
  radar_start_size="$(stat -c %s -- "$RADAR_LOG")"

  start_jam_collector "$jam_jsonl" "$ready_file" "$CLOSED_LOOP_DURATION_SEC" &
  CURRENT_COLLECTOR_PID=$!
  wait_for_ready_file "$ready_file" "$CURRENT_COLLECTOR_PID"

  if [[ "$CLOSED_LOOP_SOURCE" == "replay" ]]; then
    setsid ros2 launch sdr_receiver_py_wrapper iq_replay_jam_code.launch.py \
      iq_source_path:="$L1_IQ" \
      iq_source_loop:=true \
      iq_source_throttle:=true \
      iq_source_sample_rate:="$SAMPLE_RATE_HZ" \
      iq_source_center_hz:=433920000 \
      initial_team:=BLUE \
      initial_target:=L1 \
      </dev/null >"$launch_log" 2>&1 &
  else
    local fallback_self_id
    local closed_iq_max_bytes
    if [[ "$OWN_TEAM" == "RED" ]]; then fallback_self_id=9; else fallback_self_id=109; fi
    mkdir -- "$stage_dir/iq"
    closed_iq_max_bytes="$(iq_limit_for_duration "$CLOSED_LOOP_DURATION_SEC")" \
      || die "closed-loop IQ limit calculation failed"
    ensure_window_space "$stage_dir/iq" "$closed_iq_max_bytes" \
      || die "insufficient disk space for closed-loop IQ"
    setsid ros2 launch sdr_receiver_py_wrapper competition_receiver.launch.py \
      initial_rx_gain:="$gain" \
      record_iq:=true \
      iq_record_dir:="$stage_dir/iq" \
      iq_record_prefix:=closed_loop \
      iq_record_max_sec:="$(python3 -c 'import sys; print(float(sys.argv[1]) + 60)' "$CLOSED_LOOP_DURATION_SEC")" \
      iq_record_max_bytes:="$closed_iq_max_bytes" \
      fallback_self_id:="$fallback_self_id" \
      enable_fallback_topics:=true \
      key_retry_limit:=1 \
      </dev/null >"$launch_log" 2>&1 &
  fi
  CURRENT_PGID=$!
  local collector_rc=0
  wait "$CURRENT_COLLECTOR_PID" || collector_rc=$?
  CURRENT_COLLECTOR_PID=""
  if ((collector_rc != 0)); then
    stop_launch
    die "JamCode collection failed"
  fi
  if ! kill -0 "$CURRENT_PGID" 2>/dev/null; then
    wait "$CURRENT_PGID" 2>/dev/null || true
    CURRENT_PGID=""
    die "closed-loop receiver exited before collection ended"
  fi
  stop_launch

  confirm_stage "radar_stopped_log_flushed" \
    "radar main cleanly stopped and its current log flushed"
  wait_for_radar_stop_and_flush >/dev/null \
    || die "radar process/log flush verification failed"

  local radar_end_size
  radar_end_size="$(stat -c %s -- "$RADAR_LOG")"
  ((radar_end_size >= radar_start_size)) || die "radar log was truncated or rotated during the run"
  dd if="$RADAR_LOG" of="$radar_delta" bs=1 skip="$radar_start_size" status=none

  python3 - "$jam_jsonl" "$radar_delta" "$result_path" <<'PY'
import json
from pathlib import Path
import re
import sys

jam_path, radar_path, result_path = sys.argv[1:]
records = []
with open(jam_path, encoding="utf-8") as handle:
    for line in handle:
        records.append(json.loads(line))
violations = []
if len(records) != 1:
    violations.append(f"jam_code_count={len(records)}")
else:
    message = records[0].get("message")
    if not isinstance(message, dict):
        violations.append("jam_code_not_mapping")
    else:
        expected = {
            "valid": True,
            "command_id": 2566,
            "level": 1,
            "team": "BLUE",
            "target": "L1",
            "ascii_code": "fcYqTC",
            "key": [102, 99, 89, 113, 84, 67],
        }
        for field, value in expected.items():
            if message.get(field) != value:
                violations.append(f"jam_code_{field}_mismatch")

radar_text = Path(radar_path).read_text(encoding="utf-8", errors="replace")
patterns = [
    (
        "callback",
        re.compile(
            r"Received JamCode[^\n]*command_id:\s*(?:0x0*A06|0x2566)(?![0-9A-Fa-f])",
            re.I,
        ),
    ),
    ("ascii_key", re.compile(r"ASCII Key:\s*\[fcYqTC\]")),
    ("stored", re.compile(r"Stored password:")),
    ("phase2", re.compile(r"key phase 2 start")),
    ("sent", re.compile(r"key has send")),
]
positions = []
for name, pattern in patterns:
    match = pattern.search(radar_text)
    if match is None:
        violations.append(f"radar_{name}_missing")
    else:
        positions.append((name, match.start()))
if len(positions) == len(patterns):
    if [position for _, position in positions] != sorted(position for _, position in positions):
        violations.append("radar_evidence_out_of_order")

result = {
    "schema_version": 1,
    "expected_key": "fcYqTC",
    "jam_code_count": len(records),
    "radar_callback_stored_key": not any(v.startswith("radar_callback") or v.startswith("radar_ascii") or v.startswith("radar_stored") for v in violations),
    "radar_entered_phase2": "radar_phase2_missing" not in violations and "radar_sent_missing" not in violations,
    "violations": violations,
    "passed": not violations,
}
with open(result_path, "x", encoding="utf-8") as handle:
    json.dump(result, handle, ensure_ascii=False, indent=2, sort_keys=True)
    handle.write("\n")
if violations:
    raise SystemExit("closed-loop validation failed: " + ",".join(violations))
PY
  audit_event "closed_loop_complete" "closed_loop" "result=$result_path"
}

write_final_summary() {
  local final_gain="$1"
  python3 - "$OUT_DIR/acceptance_summary.json" "$RESULTS_JSONL" \
    "$OUT_DIR/closed_loop/result.json" "$RUN_ELIGIBLE" "$final_gain" <<'PY'
import json
import sys
summary_path, results_path, closed_path, eligible_text, final_gain = sys.argv[1:]
with open(results_path, encoding="utf-8") as handle:
    results = [json.loads(line) for line in handle if line.strip()]
with open(closed_path, encoding="utf-8") as handle:
    closed_loop = json.load(handle)
eligible = eligible_text == "true"
windows = [item for item in results if item.get("record_type") == "measurement_window"]
combinations = [item for item in results if item.get("record_type") == "combination_summary"]
summary = {
    "schema_version": 1,
    "hardware_acceptance_eligible": eligible,
    "hardware_acceptance_status": "PROCEDURE_PASSED" if eligible else "NOT_ELIGIBLE_SHORT_DURATION",
    "hardware_acceptance_claimed_by_script": False,
    "window_count": len(windows),
    "combination_count": len(combinations),
    "all_recorded_windows_passed": bool(windows) and all(item.get("passed") is True for item in windows),
    "all_combinations_fieldable": len(combinations) == 6 and all(item.get("fieldable") is True for item in combinations),
    "closed_loop_passed": closed_loop.get("passed") is True,
    "final_full_chain_gain_db": int(final_gain),
    "results_jsonl": "results.jsonl",
    "audit_jsonl": "audit.jsonl",
}
with open(summary_path, "x", encoding="utf-8") as handle:
    json.dump(summary, handle, ensure_ascii=False, indent=2, sort_keys=True)
    handle.write("\n")
PY
}

main() {
  parse_args "$@"
  if [[ "$MODE" == "plan" ]]; then
    print_plan
    return 0
  fi
  validate_execute_args
  create_output_dir
  write_metadata
  preflight_disk
  audit_event "run_start" "run" "execute"

  local final_full_chain_gain=""
  local index
  for index in "${!COMBINATION_IDS[@]}"; do
    final_full_chain_gain="$(run_combination "$((index + 1))" "${COMBINATION_IDS[$index]}" "${COMBINATION_LABELS[$index]}")"
  done

  confirm_stage "usb3_short" "verified short USB 3 cable, complete RF chain"
  run_window "stability_usb3_short" "full_chain_20db" "complete chain stability" \
    "$final_full_chain_gain" "$STABILITY_DURATION_SEC" true "verified_short_usb3" >/dev/null

  confirm_stage "usb3_competition_3m" "competition 3 m USB cable, same host port and RF chain"
  run_window "stability_usb3_competition_3m" "full_chain_20db" "complete chain stability" \
    "$final_full_chain_gain" "$STABILITY_DURATION_SEC" true "competition_usb3_3m" >/dev/null

  run_closed_loop "$final_full_chain_gain"
  write_final_summary "$final_full_chain_gain"
  audit_event "run_complete" "run" "summary=$OUT_DIR/acceptance_summary.json"
  printf 'RF bench procedure finished. Evidence: %s\n' "$OUT_DIR"
  if [[ "$RUN_ELIGIBLE" != true ]]; then
    printf 'NOT HARDWARE-ACCEPTANCE ELIGIBLE: short test duration was used.\n' >&2
  fi
}

main "$@"
