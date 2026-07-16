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
readonly STATUS_PERIOD_SEC="1.0"
readonly STATUS_SCHEDULING_MARGIN_SEC="0.25"
readonly MAX_CHUNK_TOLERANCE_SEC="0.1"
readonly CHUNK_SCHEDULING_MARGIN_SEC="0.002"
readonly ADC_CODE_SCALE="2048.0"
readonly STATUS_RAW_MAX_BYTES="1048576"
readonly STATUS_CAPTURE_MAX_BYTES="8388608"
readonly JAM_RAW_MAX_BYTES="1048576"
readonly LAUNCH_LOG_MAX_BYTES="67108864"
readonly RADAR_DELTA_MAX_BYTES="16777216"
readonly RADAR_PREFIX_MAX_BYTES="67108864"
readonly EVIDENCE_LINE_MAX_BYTES="1048576"
readonly EVIDENCE_JSON_MAX_DEPTH="20"
readonly EVIDENCE_JSON_MAX_KEYS="512"
readonly RESOURCE_FILES_PER_WINDOW="7"
readonly RESOURCE_DIRS_PER_WINDOW="2"
readonly RESOURCE_FIXED_FILES="32"
readonly RESOURCE_FIXED_DIRS="4"
readonly RESOURCE_FD_MARGIN="64"
readonly RESOURCE_INOTIFY_WATCH_MARGIN="32"
readonly RESOURCE_INOTIFY_INSTANCE_MARGIN="1"
readonly RESOURCE_INOTIFY_EVENT_MARGIN="64"
readonly CONFIRMED_L1_SHA256="8cde16d3fe8230334a9efcb36c81ae105b76b4118f4fe3fc63943aeb791be7cc"

BOUNDED_LOGGER_PY=""
IFS= read -r -d '' BOUNDED_LOGGER_PY <<'PY' || true
import os
from pathlib import Path
import signal
import stat
import sys

path = Path(sys.argv[1])
maximum = int(sys.argv[2])
if maximum <= 0:
    raise SystemExit("launch log limit is invalid")
fd = os.open(
    path,
    os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
    0o600,
)
total = 0
try:
    info = os.fstat(fd)
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise SystemExit("launch log is not a private regular file")
    while True:
        block = sys.stdin.buffer.read(64 * 1024)
        if not block:
            break
        allowed = min(len(block), maximum - total)
        offset = 0
        while offset < allowed:
            offset += os.write(fd, block[offset:allowed])
        total += allowed
        if allowed != len(block):
            os.fsync(fd)
            os.killpg(os.getpgrp(), signal.SIGTERM)
            raise SystemExit("launch log exceeded its resource limit")
    os.fsync(fd)
finally:
    os.close(fd)
dir_fd = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
try:
    os.fsync(dir_fd)
finally:
    os.close(dir_fd)
PY
readonly BOUNDED_LOGGER_PY
export BOUNDED_LOGGER_PY

TIMEOUT_CLASSIFIER_PY=""
IFS= read -r -d '' TIMEOUT_CLASSIFIER_PY <<'PY' || true
import re

_timeout_negative = re.compile(
    r"\bno\s+(?:(?:libiio|iio|libusb)\s+)?timeouts?\b|"
    r"\btimeouts?\s*(?:=|:)\s*0\b|"
    r"\bzero\s+(?:(?:libiio|iio|libusb)\s+)?timeouts?\b",
    re.IGNORECASE,
)
_timeout_positive_explicit = re.compile(
    r"\blibusb_error_timeout\b|\betimedout\b|"
    r"\b(?:errno|error)\s*(?:=|:|-|_)?\s*-?110\b|"
    r"(?:^|[^0-9])-110(?:[^0-9]|$)|"
    r"\b(?:connection|buffer(?:\s+read)?|read|receive|recv|poll|refill|"
    r"stream|transfer|usb|device)\s+(?:operation\s+)?timed\s+out\b",
    re.IGNORECASE,
)
_timeout_context = re.compile(
    r"\blibiio\b|(?:^|[^a-z])iio(?:[^a-z]|$)|\blibusb\b",
    re.IGNORECASE,
)
_timeout_generic = re.compile(r"\btimed\s+out\b", re.IGNORECASE)
_timeout_clause_split = re.compile(
    r"[;|]+|(?<=[.!?])\s+|\s+(?:but|however|yet)\s+",
    re.IGNORECASE,
)

def count_libiio_timeout_lines(lines):
    count = 0
    for line in lines:
        line_has_failure = False
        for clause in _timeout_clause_split.split(line):
            # Remove only a corresponding negative assertion. A separate real
            # failure in the same log line remains visible and is still fatal.
            candidate = _timeout_negative.sub(" ", clause)
            if _timeout_positive_explicit.search(candidate) \
                    or (_timeout_context.search(candidate)
                        and _timeout_generic.search(candidate)):
                line_has_failure = True
                break
        if line_has_failure:
            count += 1
    return count
PY
readonly TIMEOUT_CLASSIFIER_PY

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
CURRENT_SID=""
CURRENT_LEADER_PID=""
CURRENT_LEADER_START=""
CURRENT_PROVISIONAL_PID=""
CURRENT_PROVISIONAL_START=""
CURRENT_PROVISIONAL_PGID=""
CURRENT_PROVISIONAL_SID=""
COLLECTOR_PID=""
COLLECTOR_START=""
COLLECTOR_PGID=""
COLLECTOR_SID=""
COLLECTOR_PROVISIONAL_PID=""
COLLECTOR_PROVISIONAL_START=""
COLLECTOR_PROVISIONAL_PGID=""
COLLECTOR_PROVISIONAL_SID=""
COLLECTOR_FINISH_TIMEOUT_SEC=""
SPAWN_CRITICAL_KIND=""
PENDING_SIGNAL_EXIT=""
RADAR_LOG_FD=""
L1_SNAPSHOT_FD=""
L1_SNAPSHOT_SIZE="0"
L1_SNAPSHOT_PATH=""
L1_SOURCE_SIZE="0"
RESULTS_JSONL=""
AUDIT_JSONL=""
RESULTS_IDENTITY=""
AUDIT_IDENTITY=""
RUN_ELIGIBLE=true
LAST_METRICS_PATH=""
LAST_FINAL_GAIN=""

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
  if [[ "$GAIN_STEP_DB" =~ ^[1-9][0-9]*$ && "$MAX_GAIN_DB" =~ ^[0-9]+$ ]]; then
    local gain_windows=$((
      MAX_GAIN_DB / GAIN_STEP_DB
      + (MAX_GAIN_DB % GAIN_STEP_DB != 0 ? 1 : 0)
      + 1
    ))
    local worst_windows=$((6 * gain_windows + 2))
    printf 'Resource plan: worst measurement windows=%d; execute checks RLIMIT_NOFILE and current inotify watches/instances/queued-events before any ROS or RF action.\n' "$worst_windows"
  else
    printf 'Resource plan: execute validates gain inputs, then applies the fail-closed FD/inotify budget before any ROS or RF action.\n'
  fi
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
  [[ -z "${SDR_RECEIVER_ORIGINAL_SCRIPT-}" ]] \
    || die "SDR_RECEIVER_ORIGINAL_SCRIPT override is forbidden for acceptance"
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
  local radar_log_size
  radar_log_size="$(stat -Lc '%s' -- "$RADAR_LOG")" \
    || die "radar log size could not be read"
  [[ "$radar_log_size" =~ ^[0-9]+$ ]] \
    && ((radar_log_size <= RADAR_PREFIX_MAX_BYTES)) \
    || die "radar log must be fresh and no larger than $RADAR_PREFIX_MAX_BYTES bytes"
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
    L1_SOURCE_SIZE="$(python3 - "$L1_IQ" <<'PY'
import os
import stat
import sys
fd = os.open(sys.argv[1], os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
try:
    info = os.fstat(fd)
    if not stat.S_ISREG(info.st_mode) or info.st_size <= 0:
        raise SystemExit("confirmed L1 source must be a nonempty regular file")
    print(info.st_size)
finally:
    os.close(fd)
PY
)" || die "L1 IQ identity validation failed"
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
  local identities
  identities="$(python3 - "$OUT_DIR" <<'PY'
import os
from pathlib import Path
import stat
import sys
directory = Path(sys.argv[1])
dir_fd = os.open(directory, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
identities = []
try:
    for name in ("results.jsonl", "audit.jsonl"):
        fd = os.open(
            name,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
            0o600,
            dir_fd=dir_fd,
        )
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise SystemExit("evidence stream is not a private regular file")
            os.fsync(fd)
            identities.append(f"{info.st_dev}:{info.st_ino}")
        finally:
            os.close(fd)
    os.fsync(dir_fd)
finally:
    os.close(dir_fd)
print(" ".join(identities))
PY
)" || die "failed to create private evidence streams"
  read -r RESULTS_IDENTITY AUDIT_IDENTITY <<<"$identities"
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
    "$DISK_RUN_HEADROOM_BYTES" "$L1_SOURCE_SIZE" <<'PY'
import math
import sys
(
    scan_duration, stability_duration, closed_duration, gain_step, max_gain,
    sample_rate, source, bytes_per_sample, ratio, metadata, run_headroom,
    l1_source_size,
) = sys.argv[1:]
gain_step = int(gain_step)
max_gain = int(max_gain)
gain_windows = (
    max_gain // gain_step
    + int(max_gain % gain_step != 0)
    + 1
)
def window_bytes(duration):
    return math.ceil(
        float(duration) * int(sample_rate) * int(bytes_per_sample) * float(ratio)
    ) + int(metadata)
total = 6 * gain_windows * window_bytes(scan_duration)
total += 2 * window_bytes(stability_duration)
if source == "bench":
    total += window_bytes(closed_duration)
total += int(run_headroom)
total += int(l1_source_size)
print(total)
PY
)" || die "planned disk requirement calculation failed"
  python3 - "$OUT_DIR/disk_preflight.json" "$OUT_DIR" "$required" <<'PY'
import hashlib
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

preflight_resources() {
  python3 - "$OUT_DIR/resource_preflight.json" "$GAIN_STEP_DB" "$MAX_GAIN_DB" \
    "$RESOURCE_FILES_PER_WINDOW" "$RESOURCE_DIRS_PER_WINDOW" \
    "$RESOURCE_FIXED_FILES" "$RESOURCE_FIXED_DIRS" "$RESOURCE_FD_MARGIN" \
    "$RESOURCE_INOTIFY_WATCH_MARGIN" "$RESOURCE_INOTIFY_INSTANCE_MARGIN" \
    "$RESOURCE_INOTIFY_EVENT_MARGIN" <<'PY'
import json
import os
from pathlib import Path
import resource
import stat
import sys

(
    output_text, gain_step_text, max_gain_text, files_per_window_text,
    dirs_per_window_text, fixed_files_text, fixed_dirs_text, fd_margin_text,
    watch_margin_text, instance_margin_text, event_margin_text,
) = sys.argv[1:]
gain_step = int(gain_step_text)
max_gain = int(max_gain_text)
files_per_window = int(files_per_window_text)
dirs_per_window = int(dirs_per_window_text)
fixed_files = int(fixed_files_text)
fixed_dirs = int(fixed_dirs_text)
fd_margin = int(fd_margin_text)
watch_margin = int(watch_margin_text)
instance_margin = int(instance_margin_text)
event_margin = int(event_margin_text)
if gain_step <= 0 or max_gain < 0 or min(
    files_per_window, dirs_per_window, fixed_files, fixed_dirs, fd_margin,
    watch_margin, instance_margin, event_margin,
) < 0:
    raise SystemExit("resource preflight inputs are invalid")

gain_windows = (
    max_gain // gain_step
    + int(max_gain % gain_step != 0)
    + 1
)
worst_windows = 6 * gain_windows + 2
planned_files = worst_windows * files_per_window + fixed_files
planned_directories = worst_windows * dirs_per_window + fixed_dirs
planned_watches = planned_files + planned_directories
current_open_fds = len(list(Path("/proc/self/fd").iterdir()))
soft_nofile, _hard_nofile = resource.getrlimit(resource.RLIMIT_NOFILE)
required_nofile = current_open_fds + planned_files + planned_directories + fd_margin

current_inotify_instances = 0
current_inotify_watches = 0
for proc in Path("/proc").iterdir():
    if not proc.name.isdigit():
        continue
    try:
        if proc.stat().st_uid != os.getuid():
            continue
        for fd_path in (proc / "fd").iterdir():
            try:
                target = os.readlink(fd_path)
            except (FileNotFoundError, PermissionError, ProcessLookupError, OSError):
                continue
            if target != "anon_inode:inotify":
                continue
            current_inotify_instances += 1
            try:
                fdinfo = (proc / "fdinfo" / fd_path.name).read_text(
                    encoding="ascii", errors="strict",
                )
            except (FileNotFoundError, PermissionError, ProcessLookupError, OSError):
                continue
            current_inotify_watches += sum(
                line.startswith("inotify wd:") for line in fdinfo.splitlines()
            )
    except (FileNotFoundError, PermissionError, ProcessLookupError, OSError):
        continue

def read_sysctl(name):
    value = int((Path("/proc/sys/fs/inotify") / name).read_text(
        encoding="ascii", errors="strict",
    ).strip())
    if value <= 0:
        raise SystemExit(f"inotify {name} is invalid")
    return value

max_user_watches = read_sysctl("max_user_watches")
max_user_instances = read_sysctl("max_user_instances")
max_queued_events = read_sysctl("max_queued_events")
required_watches = current_inotify_watches + planned_watches + watch_margin
required_instances = current_inotify_instances + 1 + instance_margin
required_queue_events = planned_watches * 4 + event_margin
nofile_passed = soft_nofile == resource.RLIM_INFINITY or soft_nofile >= required_nofile
passed = nofile_passed \
    and max_user_watches >= required_watches \
    and max_user_instances >= required_instances \
    and max_queued_events >= required_queue_events
payload = {
    "schema_version": 1,
    "worst_case_measurement_windows": worst_windows,
    "planned_artifact_files": planned_files,
    "planned_artifact_directories": planned_directories,
    "current_open_fds": current_open_fds,
    "rlimit_nofile_soft": None if soft_nofile == resource.RLIM_INFINITY else soft_nofile,
    "required_rlimit_nofile": required_nofile,
    "current_same_uid_inotify_watches": current_inotify_watches,
    "current_same_uid_inotify_instances": current_inotify_instances,
    "max_user_watches": max_user_watches,
    "required_max_user_watches": required_watches,
    "max_user_instances": max_user_instances,
    "required_max_user_instances": required_instances,
    "max_queued_events": max_queued_events,
    "required_max_queued_events": required_queue_events,
    "passed": passed,
}
path = Path(output_text)
fd = os.open(
    path,
    os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
    0o600,
)
try:
    info = os.fstat(fd)
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise SystemExit("resource preflight output is not private")
    encoded = (json.dumps(payload, indent=2, sort_keys=True) + "\n").encode()
    offset = 0
    while offset < len(encoded):
        offset += os.write(fd, encoded[offset:])
    os.fsync(fd)
finally:
    os.close(fd)
if not passed:
    raise SystemExit(
        "insufficient FD/inotify capacity for worst-case evidence publication"
    )
PY
}

snapshot_l1_source() {
  [[ "$CLOSED_LOOP_SOURCE" == "replay" ]] || return 0
  L1_SNAPSHOT_PATH="$OUT_DIR/.l1_source.private"
  local identity_path="$OUT_DIR/l1_source_identity.json"
  python3 - "$L1_IQ" "$L1_SNAPSHOT_PATH" "$identity_path" \
    "$CONFIRMED_L1_SHA256" "$L1_SOURCE_SIZE" <<'PY'
import hashlib
import json
import os
from pathlib import Path
import stat
import sys

source_text, snapshot_text, identity_text, expected_hash, expected_size_text = sys.argv[1:]
expected_size = int(expected_size_text)
source_fd = os.open(source_text, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
snapshot_fd = None
try:
    before = os.fstat(source_fd)
    if not stat.S_ISREG(before.st_mode) or before.st_size != expected_size:
        raise SystemExit("confirmed L1 source identity changed before snapshot")
    snapshot_fd = os.open(
        snapshot_text,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
        0o600,
    )
    digest = hashlib.sha256()
    copied = 0
    while True:
        block = os.read(source_fd, 1024 * 1024)
        if not block:
            break
        digest.update(block)
        written = 0
        while written < len(block):
            written += os.write(snapshot_fd, block[written:])
        copied += len(block)
    os.fsync(snapshot_fd)
    after = os.fstat(source_fd)
    path_after = Path(source_text).lstat()
    if (before.st_dev, before.st_ino, before.st_size) != (
        after.st_dev, after.st_ino, after.st_size
    ) or (path_after.st_dev, path_after.st_ino) != (before.st_dev, before.st_ino):
        raise SystemExit("confirmed L1 source changed during snapshot")
    actual_hash = digest.hexdigest()
    if copied != expected_size or actual_hash != expected_hash:
        raise SystemExit("L1 IQ snapshot SHA-256 or size does not match confirmed fixture")
    os.fchmod(snapshot_fd, 0o400)
    snapshot_info = os.fstat(snapshot_fd)
    identity = {
        "schema_version": 1,
        "source_device": before.st_dev,
        "source_inode": before.st_ino,
        "snapshot_device": snapshot_info.st_dev,
        "snapshot_inode": snapshot_info.st_ino,
        "size": copied,
        "sha256": actual_hash,
    }
    identity_fd = os.open(
        identity_text,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
        0o400,
    )
    try:
        encoded = (json.dumps(identity, indent=2, sort_keys=True) + "\n").encode()
        offset = 0
        while offset < len(encoded):
            offset += os.write(identity_fd, encoded[offset:])
        os.fsync(identity_fd)
    finally:
        os.close(identity_fd)
finally:
    if snapshot_fd is not None:
        os.close(snapshot_fd)
    os.close(source_fd)
PY
  exec {L1_SNAPSHOT_FD}<"$L1_SNAPSHOT_PATH" \
    || die "failed to hold private L1 snapshot descriptor"
  L1_SNAPSHOT_SIZE="$L1_SOURCE_SIZE"
  verify_l1_snapshot || die "private L1 snapshot verification failed"
  rm -- "$L1_SNAPSHOT_PATH"
  L1_SNAPSHOT_PATH="/proc/$BASHPID/fd/$L1_SNAPSHOT_FD"
  verify_l1_snapshot || die "unlinked L1 snapshot verification failed"
}

verify_l1_snapshot() {
  [[ "$CLOSED_LOOP_SOURCE" == "replay" ]] || return 0
  [[ -n "$L1_SNAPSHOT_FD" ]] || return 1
  python3 - "$BASHPID" "$L1_SNAPSHOT_FD" "$L1_SNAPSHOT_SIZE" \
    "$CONFIRMED_L1_SHA256" <<'PY'
import hashlib
import os
import stat
import sys
pid, fd_text, size_text, expected_hash = sys.argv[1:]
path = f"/proc/{pid}/fd/{fd_text}"
fd = os.open(path, os.O_RDONLY)
try:
    info = os.fstat(fd)
    if not stat.S_ISREG(info.st_mode) or info.st_size != int(size_text):
        raise SystemExit("held L1 snapshot identity or size changed")
    digest = hashlib.sha256()
    while True:
        block = os.read(fd, 1024 * 1024)
        if not block:
            break
        digest.update(block)
    if digest.hexdigest() != expected_hash:
        raise SystemExit("held L1 snapshot hash changed")
finally:
    os.close(fd)
PY
}

close_l1_snapshot_fd() {
  if [[ -n "$L1_SNAPSHOT_FD" ]]; then
    exec {L1_SNAPSHOT_FD}<&-
    L1_SNAPSHOT_FD=""
  fi
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
  python3 - "$AUDIT_JSONL" "$AUDIT_IDENTITY" "$kind" "$stage" "$detail" <<'PY'
import datetime
import json
import os
from pathlib import Path
import stat
import sys
path, identity, kind, stage, detail = sys.argv[1:]
record = {
    "schema_version": 1,
    "utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    "kind": kind,
    "stage": stage,
    "detail": detail,
}
expected = tuple(int(value) for value in identity.split(":"))
fd = os.open(path, os.O_WRONLY | os.O_APPEND | getattr(os, "O_NOFOLLOW", 0))
try:
    info = os.fstat(fd)
    path_info = Path(path).lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or (
        info.st_dev, info.st_ino
    ) != expected or (path_info.st_dev, path_info.st_ino) != expected:
        raise SystemExit("audit stream identity changed")
    encoded = (json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n").encode()
    offset = 0
    while offset < len(encoded):
        offset += os.write(fd, encoded[offset:])
    os.fsync(fd)
finally:
    os.close(fd)
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
  stop_collector || exit_code=1
  stop_launch || exit_code=1
  close_radar_log_fd
  close_l1_snapshot_fd
  exit "$exit_code"
}

handle_signal() {
  local exit_code="$1"
  if [[ -n "$SPAWN_CRITICAL_KIND" ]]; then
    PENDING_SIGNAL_EXIT="$exit_code"
    return 0
  fi
  cleanup "$exit_code"
}

finish_spawn_critical() {
  SPAWN_CRITICAL_KIND=""
  if [[ -n "$PENDING_SIGNAL_EXIT" ]]; then
    local exit_code="$PENDING_SIGNAL_EXIT"
    PENDING_SIGNAL_EXIT=""
    cleanup "$exit_code"
  fi
}

trap 'cleanup $?' EXIT
trap 'handle_signal 130' INT
trap 'handle_signal 143' TERM
trap 'handle_signal 129' HUP

register_launch_group() {
  local pid="$1"
  local identity=""
  local attempt
  for attempt in {1..50}; do
    if identity="$(python3 - "$pid" <<'PY'
from pathlib import Path
import os
import sys

pid = int(sys.argv[1])
proc = Path("/proc") / str(pid)
if not proc.is_dir() or proc.stat().st_uid != os.getuid():
    raise SystemExit(1)
text = (proc / "stat").read_text(encoding="ascii")
right = text.rfind(")")
fields = text[right + 2:].split() if right >= 0 else []
if len(fields) < 20:
    raise SystemExit(1)
pgrp, session, start = int(fields[2]), int(fields[3]), fields[19]
print(pid, pgrp, session, start, fields[0])
PY
)"; then
      local observed_pid observed_pgid observed_sid observed_start observed_state
      read -r observed_pid observed_pgid observed_sid observed_start observed_state <<<"$identity"
      CURRENT_PROVISIONAL_PID="$observed_pid"
      CURRENT_PROVISIONAL_PGID="$observed_pgid"
      CURRENT_PROVISIONAL_SID="$observed_sid"
      CURRENT_PROVISIONAL_START="$observed_start"
      if [[ "$observed_pgid" != "$pid" || "$observed_sid" != "$pid" ]]; then
        return 1
      fi
      if [[ "$observed_state" == "T" || "$observed_state" == "t" ]]; then
        CURRENT_LEADER_PID="$observed_pid"
        CURRENT_PGID="$observed_pgid"
        CURRENT_SID="$observed_sid"
        CURRENT_LEADER_START="$observed_start"
        CURRENT_PROVISIONAL_PID=""
        CURRENT_PROVISIONAL_PGID=""
        CURRENT_PROVISIONAL_SID=""
        CURRENT_PROVISIONAL_START=""
        return 0
      fi
    fi
    kill -0 "$pid" 2>/dev/null || break
    sleep 0.02
  done
  return 1
}

discard_provisional_launch() {
  [[ -n "$CURRENT_PROVISIONAL_PID" ]] || return 0
  local pid="$CURRENT_PROVISIONAL_PID"
  local start="$CURRENT_PROVISIONAL_START"
  local pgid="$CURRENT_PROVISIONAL_PGID"
  local sid="$CURRENT_PROVISIONAL_SID"
  if [[ -z "$start" ]]; then
    if register_launch_group "$pid"; then
      stop_launch || true
      return 0
    fi
    start="$CURRENT_PROVISIONAL_START"
    pgid="$CURRENT_PROVISIONAL_PGID"
    sid="$CURRENT_PROVISIONAL_SID"
  fi
  if [[ -n "$start" && "$pgid" == "$pid" && "$sid" == "$pid" ]]; then
    CURRENT_LEADER_PID="$pid"
    CURRENT_LEADER_START="$start"
    CURRENT_PGID="$pgid"
    CURRENT_SID="$sid"
    CURRENT_PROVISIONAL_PID=""
    CURRENT_PROVISIONAL_START=""
    CURRENT_PROVISIONAL_PGID=""
    CURRENT_PROVISIONAL_SID=""
    stop_launch || true
    return 0
  fi
  if [[ -n "$start" ]]; then
    python3 - "$pid" "$start" <<'PY' || true
from pathlib import Path
import os
import signal
import sys
pid, expected_start = sys.argv[1:]
proc = Path("/proc") / pid
try:
    if proc.stat().st_uid != os.getuid():
        raise SystemExit(1)
    text = (proc / "stat").read_text(encoding="ascii")
except (FileNotFoundError, PermissionError, ProcessLookupError):
    raise SystemExit(0)
right = text.rfind(")")
fields = text[right + 2:].split() if right >= 0 else []
if len(fields) < 20 or fields[19] != expected_start:
    raise SystemExit(1)
os.kill(int(pid), signal.SIGKILL)
PY
  fi
  wait "$pid" 2>/dev/null || true
  CURRENT_PROVISIONAL_PID=""
  CURRENT_PROVISIONAL_START=""
  CURRENT_PROVISIONAL_PGID=""
  CURRENT_PROVISIONAL_SID=""
}

start_bounded_launch() {
  local launch_log="$1"
  shift
  SPAWN_CRITICAL_KIND="launch"
  setsid bash -c '
    kill -STOP "$$"
    set -o pipefail
    launch_log="$1"
    maximum="$2"
    shift 2
    (ulimit -c 0; exec "$@") 2>&1 |
      python3 -c "$BOUNDED_LOGGER_PY" "$launch_log" "$maximum"
  ' bash "$launch_log" "$LAUNCH_LOG_MAX_BYTES" "$@" </dev/null &
  local launch_pid=$!
  CURRENT_PROVISIONAL_PID="$launch_pid"
  if ! register_launch_group "$launch_pid"; then
    discard_provisional_launch
    finish_spawn_critical
    return 1
  fi
  if ! kill -CONT -- "-$CURRENT_PGID" 2>/dev/null; then
    stop_launch || true
    finish_spawn_critical
    return 1
  fi
  finish_spawn_critical
}

launch_group_state() {
  [[ -n "$CURRENT_PGID" && -n "$CURRENT_SID" && -n "$CURRENT_LEADER_PID" ]] || return 1
  python3 - "$CURRENT_LEADER_PID" "$CURRENT_LEADER_START" \
    "$CURRENT_PGID" "$CURRENT_SID" <<'PY'
from pathlib import Path
import os
import sys

leader, expected_start, expected_pgrp, expected_session = sys.argv[1:]
expected_pgrp = int(expected_pgrp)
expected_session = int(expected_session)
members = []
for proc in Path("/proc").iterdir():
    if not proc.name.isdigit():
        continue
    try:
        if proc.stat().st_uid != os.getuid():
            continue
        text = (proc / "stat").read_text(encoding="ascii")
    except (FileNotFoundError, PermissionError, ProcessLookupError):
        continue
    right = text.rfind(")")
    fields = text[right + 2:].split() if right >= 0 else []
    if len(fields) < 20:
        continue
    pgrp, session = int(fields[2]), int(fields[3])
    if proc.name == leader and fields[19] != expected_start:
        raise SystemExit(2)
    if fields[0] == "Z":
        continue
    if pgrp == expected_pgrp or session == expected_session:
        if pgrp != expected_pgrp or session != expected_session:
            raise SystemExit(2)
        members.append(proc.name)
raise SystemExit(0 if members else 1)
PY
}

stop_launch() {
  if [[ -z "$CURRENT_PGID" && -n "$CURRENT_PROVISIONAL_PID" ]]; then
    discard_provisional_launch
  fi
  [[ -n "$CURRENT_PGID" ]] || return 0
  local state=0 leader_rc=0 forced=false deadline
  launch_group_state || state=$?
  ((state != 2)) || return 1
  if ((state == 0)); then
    kill -TERM -- "-$CURRENT_PGID" 2>/dev/null || return 1
    kill -CONT -- "-$CURRENT_PGID" 2>/dev/null || true
    deadline=$((SECONDS + 10))
    while launch_group_state; do
      if ((SECONDS >= deadline)); then
        forced=true
        launch_group_state || state=$?
        ((state != 2)) || return 1
        ((state == 1)) || kill -KILL -- "-$CURRENT_PGID" 2>/dev/null || return 1
        break
      fi
      read -r -t 0.05 _ </dev/null || true
    done
    deadline=$((SECONDS + 5))
    while launch_group_state; do
      ((SECONDS < deadline)) || return 1
      read -r -t 0.05 _ </dev/null || true
    done
    state=0
    launch_group_state || state=$?
    ((state == 1)) || return 1
  fi
  wait "$CURRENT_LEADER_PID" 2>/dev/null || leader_rc=$?
  if [[ "$forced" == true ]] || ((leader_rc != 0 && leader_rc != 130 && leader_rc != 143)); then
    return 1
  fi
  CURRENT_PGID=""
  CURRENT_SID=""
  CURRENT_LEADER_PID=""
  CURRENT_LEADER_START=""
}

register_collector_group() {
  local pid="$1"
  local identity=""
  local attempt
  for attempt in {1..50}; do
    if identity="$(python3 - "$pid" <<'PY'
from pathlib import Path
import os
import sys

pid = int(sys.argv[1])
proc = Path("/proc") / str(pid)
if not proc.is_dir() or proc.stat().st_uid != os.getuid():
    raise SystemExit(1)
text = (proc / "stat").read_text(encoding="ascii")
right = text.rfind(")")
fields = text[right + 2:].split() if right >= 0 else []
if len(fields) < 20:
    raise SystemExit(1)
pgrp, session, start = int(fields[2]), int(fields[3]), fields[19]
print(pid, start, pgrp, session, fields[0])
PY
)"; then
      local observed_pid observed_start observed_pgid observed_sid observed_state
      read -r observed_pid observed_start observed_pgid observed_sid observed_state <<<"$identity"
      COLLECTOR_PROVISIONAL_PID="$observed_pid"
      COLLECTOR_PROVISIONAL_START="$observed_start"
      COLLECTOR_PROVISIONAL_PGID="$observed_pgid"
      COLLECTOR_PROVISIONAL_SID="$observed_sid"
      if [[ "$observed_pgid" != "$pid" || "$observed_sid" != "$pid" ]]; then
        return 1
      fi
      if [[ "$observed_state" == "T" || "$observed_state" == "t" ]]; then
        COLLECTOR_PID="$observed_pid"
        COLLECTOR_START="$observed_start"
        COLLECTOR_PGID="$observed_pgid"
        COLLECTOR_SID="$observed_sid"
        COLLECTOR_PROVISIONAL_PID=""
        COLLECTOR_PROVISIONAL_START=""
        COLLECTOR_PROVISIONAL_PGID=""
        COLLECTOR_PROVISIONAL_SID=""
        return 0
      fi
    fi
    kill -0 "$pid" 2>/dev/null || break
    sleep 0.02
  done
  return 1
}

collector_group_state() {
  [[ -n "$COLLECTOR_PID" && -n "$COLLECTOR_START" \
      && -n "$COLLECTOR_PGID" && -n "$COLLECTOR_SID" ]] || return 1
  python3 - "$COLLECTOR_PID" "$COLLECTOR_START" \
    "$COLLECTOR_PGID" "$COLLECTOR_SID" <<'PY'
from pathlib import Path
import os
import sys

leader, expected_start, expected_pgrp, expected_session = sys.argv[1:]
expected_pgrp = int(expected_pgrp)
expected_session = int(expected_session)
members = []
for proc in Path("/proc").iterdir():
    if not proc.name.isdigit():
        continue
    try:
        if proc.stat().st_uid != os.getuid():
            continue
        text = (proc / "stat").read_text(encoding="ascii")
    except (FileNotFoundError, PermissionError, ProcessLookupError):
        continue
    right = text.rfind(")")
    fields = text[right + 2:].split() if right >= 0 else []
    if len(fields) < 20:
        continue
    pgrp, session = int(fields[2]), int(fields[3])
    if proc.name == leader and fields[19] != expected_start:
        raise SystemExit(2)
    if fields[0] == "Z":
        continue
    if pgrp == expected_pgrp or session == expected_session:
        if pgrp != expected_pgrp or session != expected_session:
            raise SystemExit(2)
        members.append(proc.name)
raise SystemExit(0 if members else 1)
PY
}

clear_collector_group() {
  COLLECTOR_PID=""
  COLLECTOR_START=""
  COLLECTOR_PGID=""
  COLLECTOR_SID=""
  COLLECTOR_FINISH_TIMEOUT_SEC=""
}

discard_provisional_collector() {
  [[ -n "$COLLECTOR_PROVISIONAL_PID" ]] || return 0
  local pid="$COLLECTOR_PROVISIONAL_PID"
  local start="$COLLECTOR_PROVISIONAL_START"
  local pgid="$COLLECTOR_PROVISIONAL_PGID"
  local sid="$COLLECTOR_PROVISIONAL_SID"
  if [[ -z "$start" ]]; then
    if register_collector_group "$pid"; then
      terminate_collector_group false || true
      return 0
    fi
    start="$COLLECTOR_PROVISIONAL_START"
    pgid="$COLLECTOR_PROVISIONAL_PGID"
    sid="$COLLECTOR_PROVISIONAL_SID"
  fi
  if [[ -n "$start" && "$pgid" == "$pid" && "$sid" == "$pid" ]]; then
    COLLECTOR_PID="$pid"
    COLLECTOR_START="$start"
    COLLECTOR_PGID="$pgid"
    COLLECTOR_SID="$sid"
    COLLECTOR_PROVISIONAL_PID=""
    COLLECTOR_PROVISIONAL_START=""
    COLLECTOR_PROVISIONAL_PGID=""
    COLLECTOR_PROVISIONAL_SID=""
    terminate_collector_group false || true
    return 0
  fi
  if [[ -n "$start" ]]; then
    python3 - "$pid" "$start" <<'PY' || true
from pathlib import Path
import os
import signal
import sys
pid, expected_start = sys.argv[1:]
proc = Path("/proc") / pid
try:
    if proc.stat().st_uid != os.getuid():
        raise SystemExit(1)
    text = (proc / "stat").read_text(encoding="ascii")
except (FileNotFoundError, PermissionError, ProcessLookupError):
    raise SystemExit(0)
right = text.rfind(")")
fields = text[right + 2:].split() if right >= 0 else []
if len(fields) < 20 or fields[19] != expected_start:
    raise SystemExit(1)
os.kill(int(pid), signal.SIGKILL)
PY
  fi
  wait "$pid" 2>/dev/null || true
  COLLECTOR_PROVISIONAL_PID=""
  COLLECTOR_PROVISIONAL_START=""
  COLLECTOR_PROVISIONAL_PGID=""
  COLLECTOR_PROVISIONAL_SID=""
}

terminate_collector_group() {
  local leader_reaped="${1:-false}"
  [[ -n "$COLLECTOR_PGID" ]] || return 0
  local state=0 leader_rc=0 forced=false deadline
  collector_group_state || state=$?
  ((state != 2)) || return 1
  if ((state == 0)); then
    kill -TERM -- "-$COLLECTOR_PGID" 2>/dev/null || return 1
    kill -CONT -- "-$COLLECTOR_PGID" 2>/dev/null || true
    deadline=$((SECONDS + 5))
    while collector_group_state; do
      if ((SECONDS >= deadline)); then
        forced=true
        collector_group_state || state=$?
        ((state != 2)) || return 1
        ((state == 1)) || kill -KILL -- "-$COLLECTOR_PGID" 2>/dev/null || return 1
        break
      fi
      read -r -t 0.05 _ </dev/null || true
    done
    deadline=$((SECONDS + 5))
    while collector_group_state; do
      ((SECONDS < deadline)) || return 1
      read -r -t 0.05 _ </dev/null || true
    done
    state=0
    collector_group_state || state=$?
    ((state == 1)) || return 1
  fi
  if [[ "$leader_reaped" != true ]]; then
    wait "$COLLECTOR_PID" 2>/dev/null || leader_rc=$?
  fi
  clear_collector_group
  if [[ "$forced" == true ]] \
      || ((leader_rc != 0 && leader_rc != 130 && leader_rc != 143)); then
    return 1
  fi
}

stop_collector() {
  if [[ -z "$COLLECTOR_PGID" && -n "$COLLECTOR_PROVISIONAL_PID" ]]; then
    discard_provisional_collector
  fi
  terminate_collector_group false
}

finish_collector() {
  [[ -n "$COLLECTOR_PID" ]] || return 1
  [[ "$COLLECTOR_FINISH_TIMEOUT_SEC" =~ ^[1-9][0-9]*$ ]] || return 1
  local leader_rc=0 state=0 deadline=$((SECONDS + COLLECTOR_FINISH_TIMEOUT_SEC))
  while true; do
    state=0
    collector_group_state || state=$?
    ((state != 2)) || return 1
    ((state == 0)) || break
    if ((SECONDS >= deadline)); then
      terminate_collector_group false || true
      return 1
    fi
    sleep 0.05
  done
  wait "$COLLECTOR_PID" || leader_rc=$?
  collector_group_state || state=$?
  if ((state == 2)); then
    return 1
  fi
  if ((state == 0)); then
    terminate_collector_group true || true
    return 1
  fi
  clear_collector_group
  ((leader_rc == 0))
}

close_radar_log_fd() {
  if [[ -n "$RADAR_LOG_FD" ]]; then
    exec {RADAR_LOG_FD}<&-
    RADAR_LOG_FD=""
  fi
}

collect_status() {
  local output_path="$1"
  local duration_sec="$2"
  local expected_events_path="$3"
  python3 - "$output_path" "$duration_sec" "$expected_events_path" \
    "$STATUS_RAW_MAX_BYTES" \
    "$STATUS_CAPTURE_MAX_BYTES" \
    "$EVIDENCE_JSON_MAX_DEPTH" "$EVIDENCE_JSON_MAX_KEYS" <<'PY'
import json
import math
import os
import resource
import subprocess
import sys
import tempfile
import time

import yaml

(
    path, duration_text, expected_events_path, raw_max_text, capture_max_text,
    depth_max_text, keys_max_text,
) = sys.argv[1:]
duration = float(duration_text)
raw_max = int(raw_max_text)
capture_max = int(capture_max_text)
depth_max = int(depth_max_text)
keys_max = int(keys_max_text)
if not math.isfinite(duration) or duration <= 0:
    raise SystemExit("invalid collection duration")
command = [
    "ros2", "topic", "echo", "/sdr/status", "std_msgs/msg/String",
    "--once",
]

class UniqueKeyLoader(yaml.SafeLoader):
    def __init__(self, stream):
        super().__init__(stream)
        self.node_count = 0

    def compose_node(self, parent, index):
        if self.check_event(yaml.AliasEvent):
            raise yaml.YAMLError("YAML aliases are forbidden")
        self.node_count += 1
        if self.node_count > keys_max * 4:
            raise yaml.YAMLError("YAML node resource limit exceeded")
        return super().compose_node(parent, index)

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

def reject_json_constant(value):
    raise RuntimeError(f"non-finite JSON constant: {value}")

def validate_shape(value):
    key_count = 0
    stack = [(value, 1)]
    while stack:
        current, depth = stack.pop()
        if depth > depth_max:
            raise RuntimeError("status payload exceeds depth limit")
        if type(current) is dict:
            key_count += len(current)
            if key_count > keys_max:
                raise RuntimeError("status payload exceeds key limit")
            stack.extend((child, depth + 1) for child in current.values())
        elif type(current) is list:
            stack.extend((child, depth + 1) for child in current)

def child_limits():
    resource.setrlimit(resource.RLIMIT_FSIZE, (raw_max, raw_max))
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))

def receive_one(timeout):
    with tempfile.TemporaryFile() as stdout, tempfile.TemporaryFile() as stderr:
        completed = subprocess.run(
            command, check=False, stdout=stdout, stderr=stderr,
            stdin=subprocess.DEVNULL, timeout=timeout, preexec_fn=child_limits,
        )
        stdout.seek(0)
        raw_stdout = stdout.read(raw_max + 1)
        stderr.seek(0)
        raw_stderr = stderr.read(min(raw_max, 4096) + 1)
    if len(raw_stdout) > raw_max or len(raw_stderr) > min(raw_max, 4096):
        raise RuntimeError("status echo exceeded output resource limit")
    try:
        decoded_stdout = raw_stdout.decode("utf-8", errors="strict")
        decoded_stderr = raw_stderr.decode("utf-8", errors="replace")
    except UnicodeDecodeError as exc:
        raise RuntimeError("status echo is not UTF-8") from exc
    if completed.returncode != 0:
        raise RuntimeError(
            "status echo failed: " + decoded_stderr.strip()[:500]
        )
    documents = [
        document
        for document in yaml.load_all(decoded_stdout, Loader=UniqueKeyLoader)
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
    status = json.loads(
        decoded,
        object_pairs_hook=unique_json_object,
        parse_constant=reject_json_constant,
    )
    if type(status) is not dict:
        raise RuntimeError("status JSON must be an object")
    validate_shape(status)
    return status

first_deadline = time.monotonic() + 30.0
records = []
captured_bytes = 0

def append_record(status):
    global captured_bytes
    encoded_size = len(json.dumps(
        status, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")) + 128
    captured_bytes += encoded_size
    if captured_bytes > capture_max:
        raise SystemExit("status capture exceeded aggregate resource limit")
    records.append((time.monotonic_ns(), status))

while not records:
    remaining = first_deadline - time.monotonic()
    if remaining <= 0:
        raise SystemExit("no /sdr/status message within 30 seconds")
    try:
        status = receive_one(min(10.0, remaining))
    except subprocess.TimeoutExpired:
        continue
    except RuntimeError as exc:
        if str(exc).startswith("status echo failed:"):
            time.sleep(min(0.05, max(0.0, first_deadline - time.monotonic())))
            continue
        raise SystemExit(f"invalid /sdr/status message: {exc}") from None
    except (json.JSONDecodeError, yaml.YAMLError) as exc:
        raise SystemExit(f"invalid /sdr/status message: {exc}") from None
    runtime = status.get("common_runtime")
    recorder = runtime.get("recorder") if type(runtime) is dict else None
    paths = recorder.get("paths") if type(recorder) is dict else None
    observed_events_path = paths.get("events_path") if type(paths) is dict else None
    if type(observed_events_path) is not str \
            or os.path.normpath(observed_events_path) \
            != os.path.normpath(expected_events_path):
        time.sleep(min(0.05, max(0.0, first_deadline - time.monotonic())))
        continue
    append_record(status)

window_start_ns = records[0][0]
deadline_ns = window_start_ns + math.ceil(duration * 1_000_000_000)
maximum_records = max(100, int(math.ceil(duration * 10)) + 100)
while time.monotonic_ns() < deadline_ns:
    if len(records) >= maximum_records:
        raise SystemExit("status message resource limit exceeded")
    remaining = (deadline_ns - time.monotonic_ns()) / 1_000_000_000
    try:
        status = receive_one(max(0.05, min(10.0, remaining)))
    except subprocess.TimeoutExpired:
        continue
    except (RuntimeError, json.JSONDecodeError, yaml.YAMLError) as exc:
        raise SystemExit(f"invalid /sdr/status message: {exc}") from None
    append_record(status)

if not records:
    raise SystemExit("no /sdr/status snapshots")
window_end_ns = time.monotonic_ns()
with open(path, "x", encoding="utf-8") as handle:
    handle.write(json.dumps(
        {
            "record_type": "window_bounds",
            "requested_duration_sec": duration,
            "window_end_monotonic_ns": window_end_ns,
            "window_start_monotonic_ns": window_start_ns,
        },
        ensure_ascii=False,
        sort_keys=True,
    ) + "\n")
    for captured_ns, status in records:
        handle.write(json.dumps(
            {
                "captured_monotonic_ns": captured_ns,
                "record_type": "status_snapshot",
                "status": status,
            },
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
  local requested_duration_sec="$9"
  python3 - "$status_path" "$launch_log" "$iq_dir" "$metrics_path" \
    "$stage" "$combination" "$gain" "$SAMPLE_RATE_HZ" "$MIN_DUTY" \
    "$MIN_CRC16" "$CLIPPING_THRESHOLD" "$ADC_CODE_SCALE" "$enforce_stability" \
    "$requested_duration_sec" "$STATUS_PERIOD_SEC" \
    "$STATUS_SCHEDULING_MARGIN_SEC" "$MAX_CHUNK_TOLERANCE_SEC" \
    "$CHUNK_SCHEDULING_MARGIN_SEC" "$EVIDENCE_LINE_MAX_BYTES" \
    "$EVIDENCE_JSON_MAX_DEPTH" "$EVIDENCE_JSON_MAX_KEYS" \
    "$LAUNCH_LOG_MAX_BYTES" "$TIMEOUT_CLASSIFIER_PY" <<'PY'
import hashlib
import json
import math
import os
from pathlib import Path
import re
import stat
import sys

(
    status_path, launch_log, iq_dir, metrics_path, stage, combination, gain,
    sample_rate, min_duty, min_crc16, clipping_threshold, required_adc_code_scale,
    enforce_stability,
    requested_duration_sec, status_period_sec, status_scheduling_margin_sec,
    max_chunk_tolerance_sec, chunk_scheduling_margin_sec, line_max_bytes,
    json_max_depth, json_max_keys, launch_log_max_bytes, timeout_classifier_py,
) = sys.argv[1:]
sample_rate = int(sample_rate)
min_duty = float(min_duty)
min_crc16 = int(min_crc16)
clipping_threshold = float(clipping_threshold)
required_adc_code_scale = float(required_adc_code_scale)
requested_gain = int(gain)
enforce_stability = enforce_stability == "true"
requested_duration_sec = float(requested_duration_sec)
status_period_sec = float(status_period_sec)
status_scheduling_margin_sec = float(status_scheduling_margin_sec)
max_chunk_tolerance_sec = float(max_chunk_tolerance_sec)
chunk_scheduling_margin_sec = float(chunk_scheduling_margin_sec)
line_max_bytes = int(line_max_bytes)
json_max_depth = int(json_max_depth)
json_max_keys = int(json_max_keys)
launch_log_max_bytes = int(launch_log_max_bytes)
exec(timeout_classifier_py, globals())
if not math.isfinite(requested_duration_sec) or requested_duration_sec <= 0:
    raise SystemExit("requested duration is invalid")
if not math.isfinite(status_period_sec) or status_period_sec <= 0:
    raise SystemExit("status period is invalid")
if not math.isfinite(status_scheduling_margin_sec) or status_scheduling_margin_sec < 0:
    raise SystemExit("status scheduling margin is invalid")
if not math.isfinite(max_chunk_tolerance_sec) or max_chunk_tolerance_sec <= 0:
    raise SystemExit("chunk tolerance limit is invalid")
if not math.isfinite(chunk_scheduling_margin_sec) or chunk_scheduling_margin_sec < 0:
    raise SystemExit("chunk scheduling margin is invalid")
status_tolerance_sec = status_period_sec + status_scheduling_margin_sec

def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key!r}")
        result[key] = value
    return result

def reject_json_constant(value):
    raise ValueError(f"non-finite JSON constant: {value}")

def load_json_line(line, description):
    if len(line.encode("utf-8")) > line_max_bytes:
        raise SystemExit(f"{description} exceeds the line resource limit")
    try:
        value = json.loads(
            line,
            object_pairs_hook=unique_object,
            parse_constant=reject_json_constant,
        )
    except (json.JSONDecodeError, ValueError) as exc:
        raise SystemExit(f"invalid {description}") from exc
    if type(value) is not dict:
        raise SystemExit(f"{description} must be an exact object")
    validate_json_shape(value, description)
    return value

def validate_json_shape(value, description):
    keys = 0
    stack = [(value, 1)]
    while stack:
        current, depth = stack.pop()
        if depth > json_max_depth:
            raise SystemExit(f"{description} exceeds the JSON depth limit")
        if type(current) is dict:
            keys += len(current)
            if keys > json_max_keys:
                raise SystemExit(f"{description} exceeds the JSON key limit")
            stack.extend((child, depth + 1) for child in current.values())
        elif type(current) is list:
            stack.extend((child, depth + 1) for child in current)

def bounded_lines(path, description, max_bytes, max_lines):
    if path.is_symlink() or not path.is_file():
        raise SystemExit(f"{description} must be a non-symlink regular file")
    size = path.stat().st_size
    if size > max_bytes:
        raise SystemExit(f"{description} exceeds the file resource limit")
    with path.open(encoding="utf-8", errors="strict") as handle:
        for number, line in enumerate(handle, 1):
            if number > max_lines:
                raise SystemExit(f"{description} exceeds the line-count resource limit")
            if len(line.encode("utf-8")) > line_max_bytes:
                raise SystemExit(f"{description} line exceeds the resource limit")
            yield number, line

def bounded_fd_lines(fd, description, max_bytes, max_lines):
    info = os.fstat(fd)
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise SystemExit(f"{description} held descriptor is invalid")
    if info.st_size > max_bytes:
        raise SystemExit(f"{description} exceeds the file resource limit")
    duplicate = os.dup(fd)
    try:
        os.lseek(duplicate, 0, os.SEEK_SET)
        with os.fdopen(duplicate, "r", encoding="utf-8", errors="strict") as handle:
            duplicate = -1
            for number, line in enumerate(handle, 1):
                if number > max_lines:
                    raise SystemExit(
                        f"{description} exceeds the line-count resource limit"
                    )
                if len(line.encode("utf-8")) > line_max_bytes:
                    raise SystemExit(f"{description} line exceeds the resource limit")
                yield number, line
    finally:
        if duplicate >= 0:
            os.close(duplicate)

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

def classify_sample_window(sample_start_ns, sample_end_ns, authoritative_start_ns, authoritative_end_ns):
    if sample_start_ns >= authoritative_start_ns and sample_end_ns <= authoritative_end_ns:
        return "in_window"
    if sample_end_ns <= authoritative_start_ns:
        return "before_window"
    if sample_start_ns >= authoritative_end_ns:
        return "after_window"
    return "crosses_boundary"

window_bounds = None
status_records = []
for line_number, line in bounded_lines(
    Path(status_path), "normalized status", launch_log_max_bytes,
    max(100, math.ceil(requested_duration_sec * 10) + 101),
):
    item = load_json_line(line, f"normalized status line {line_number}")
    if line_number == 1:
        if set(item) != {
            "record_type", "requested_duration_sec", "window_end_monotonic_ns",
            "window_start_monotonic_ns",
        } or item["record_type"] != "window_bounds":
            raise SystemExit("normalized window bounds shape is invalid")
        recorded_duration = finite_number(
            item["requested_duration_sec"], "recorded requested duration", 0
        )
        if recorded_duration != requested_duration_sec:
            raise SystemExit("recorded requested duration does not match invocation")
        window_start_ns = exact_int(
            item["window_start_monotonic_ns"], "window start monotonic ns", 1
        )
        window_end_ns = exact_int(
            item["window_end_monotonic_ns"], "window end monotonic ns", 1
        )
        if window_end_ns <= window_start_ns:
            raise SystemExit("window monotonic bounds are invalid")
        window_bounds = (window_start_ns, window_end_ns)
        continue
    if set(item) != {"captured_monotonic_ns", "record_type", "status"} \
            or item["record_type"] != "status_snapshot":
        raise SystemExit("normalized status snapshot shape is invalid")
    exact_int(item["captured_monotonic_ns"], "captured_monotonic_ns", 1)
    if type(item["status"]) is not dict:
        raise SystemExit("normalized status payload is invalid")
    if status_records and item["captured_monotonic_ns"] <= status_records[-1]["captured_monotonic_ns"]:
        raise SystemExit("status capture monotonic time must strictly increase")
    status_records.append(item)
if window_bounds is None or not status_records:
    raise SystemExit("at least one status record is required")
window_start_ns, window_end_ns = window_bounds
window_coverage_sec = (window_end_ns - window_start_ns) / 1_000_000_000
status_start_ns = status_records[0]["captured_monotonic_ns"]
status_end_ns = status_records[-1]["captured_monotonic_ns"]
status_intersection_start_ns = max(status_start_ns, window_start_ns)
status_intersection_end_ns = min(status_end_ns, window_end_ns)
status_coverage_sec = max(
    0.0, (status_intersection_end_ns - status_intersection_start_ns) / 1_000_000_000
)
status_head_missing_sec = max(0.0, (status_start_ns - window_start_ns) / 1_000_000_000)
status_tail_missing_sec = max(0.0, (window_end_ns - status_end_ns) / 1_000_000_000)
status_early_extra_sec = max(0.0, (window_start_ns - status_start_ns) / 1_000_000_000)
status_late_extra_sec = max(0.0, (status_end_ns - window_end_ns) / 1_000_000_000)

runtimes = []
previous_counters = None
counter_violations = []
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
    acquisition = runtime.get("acquisition")
    device = runtime.get("device")
    if type(acquisition) is not dict or type(device) is not dict:
        raise SystemExit("acquisition or device counters are missing")
    current = {
        "queue_drops": acquisition.get("queue_drops"),
        "acquisition_read_errors": acquisition.get("read_errors"),
        "acquisition_reconnects": acquisition.get("reconnects"),
        "device_read_errors": device.get("read_errors"),
        "device_reconnects": device.get("reconnects"),
        "device_connection_errors": device.get("connection_errors"),
        "recorder_dropped_chunks": stats.get("dropped_chunks"),
        "recorder_dropped_events": stats.get("dropped_events"),
    }
    for name, value in current.items():
        exact_int(value, name)
        if value:
            counter_violations.append(f"{name}_nonzero")
        if previous_counters is not None and value < previous_counters[name]:
            counter_violations.append(f"{name}_decreased")
    previous_counters = current
    runtimes.append(runtime)
counter_fields = previous_counters

iq_root = Path(iq_dir).resolve(strict=True)
def controlled_file(pattern, description):
    matches = sorted(iq_root.glob(pattern))
    if len(matches) != 1 or matches[0].is_symlink():
        raise SystemExit(f"exactly one non-symlink {description} is required")
    resolved = matches[0].resolve(strict=True)
    info = resolved.stat()
    if resolved.parent != iq_root or not stat.S_ISREG(info.st_mode) \
            or info.st_nlink != 1:
        raise SystemExit(f"{description} escaped the controlled IQ directory")
    return resolved

entries = list(iq_root.iterdir())
if any(entry.is_symlink() or not entry.is_file() for entry in entries):
    raise SystemExit("IQ directory may contain only finalized non-symlink regular files")
c64_path = controlled_file("*.c64", "IQ c64")
chunks_path = controlled_file("*.chunks.jsonl", "chunks JSONL")
events_path = controlled_file("*.events.jsonl", "events JSONL")
summary_path = controlled_file("*.summary.json", "recorder summary")
stem = c64_path.name[:-4]
expected_names = {
    f"{stem}.c64", f"{stem}.chunks.jsonl", f"{stem}.events.jsonl",
    f"{stem}.summary.json",
}
if {entry.name for entry in entries} != expected_names:
    raise SystemExit("IQ recorder output must be exactly one consistent finalized four-file set")

# Open the finalized recorder artifacts once and retain the descriptors through
# parsing and hashing, closing the path-replacement race during analysis.
iq_dir_fd = os.open(iq_root, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
recorder_paths = (c64_path, chunks_path, events_path, summary_path)
recorder_fds = {}
recorder_baselines = {}

def stable_identity(info):
    return (
        info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns,
    )

for recorder_path in recorder_paths:
    fd = os.open(
        recorder_path.name,
        os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
        dir_fd=iq_dir_fd,
    )
    expected = recorder_path.stat()
    observed = os.fstat(fd)
    if not stat.S_ISREG(observed.st_mode) or observed.st_nlink != 1 \
            or (observed.st_dev, observed.st_ino) != (
                expected.st_dev, expected.st_ino
            ):
        raise SystemExit("recorder evidence identity changed before flush")
    os.fchmod(fd, 0o400)
    os.fsync(fd)
    observed = os.fstat(fd)
    recorder_fds[recorder_path.name] = fd
    recorder_baselines[recorder_path.name] = stable_identity(observed)
os.fsync(iq_dir_fd)
c64_fd = recorder_fds[c64_path.name]
chunks_fd = recorder_fds[chunks_path.name]
events_fd = recorder_fds[events_path.name]
summary_fd = recorder_fds[summary_path.name]

def digest_held_artifact(name, fd, baseline, phase):
    before = os.fstat(fd)
    path_before = os.stat(name, dir_fd=iq_dir_fd, follow_symlinks=False)
    if stable_identity(before) != baseline \
            or not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 \
            or (path_before.st_dev, path_before.st_ino) != (
                before.st_dev, before.st_ino,
            ):
        raise SystemExit(f"recorder artifact changed before {phase}: {name}")
    digest = hashlib.sha256()
    offset = 0
    while offset < before.st_size:
        block = os.pread(fd, min(1024 * 1024, before.st_size - offset), offset)
        if not block:
            raise SystemExit(f"recorder artifact became unreadable during {phase}: {name}")
        digest.update(block)
        offset += len(block)
    after = os.fstat(fd)
    path_after = os.stat(name, dir_fd=iq_dir_fd, follow_symlinks=False)
    if stable_identity(after) != baseline \
            or (path_after.st_dev, path_after.st_ino) != (
                after.st_dev, after.st_ino,
            ):
        raise SystemExit(f"recorder artifact changed during {phase}: {name}")
    return digest.hexdigest()

# The three control artifacts determine the semantic metrics. Hash them before
# any parsing and require a second identical digest afterwards, so an in-place,
# same-length rewrite cannot detach the parsed meaning from the manifest hash.
recorder_baseline_hashes = {}
for control_path in (chunks_path, events_path, summary_path):
    control_name = control_path.name
    recorder_baseline_hashes[control_name] = digest_held_artifact(
        control_name,
        recorder_fds[control_name],
        recorder_baselines[control_name],
        "pre-parse hashing",
    )

chunk_keys = {
    "chunk_id", "first_sample_index", "sample_rate_hz", "rx_wall_time",
    "rx_monotonic_ns", "lo_hz", "rf_bandwidth_hz", "rx_gain_db",
    "target_version", "context_version", "target", "metadata", "rf_metrics",
    "sample_count", "byte_offset", "byte_length",
}
chunks = []
for line_number, line in bounded_fd_lines(
    chunks_fd, "chunks JSONL", launch_log_max_bytes,
    max(1000, math.ceil(requested_duration_sec * 1000) + 1000),
):
    chunk = load_json_line(line, f"chunk line {line_number}")
    if set(chunk) != chunk_keys:
        raise SystemExit(f"chunk line {line_number} schema is invalid")
    chunk_id = exact_int(chunk["chunk_id"], "chunk_id")
    first_index = exact_int(chunk["first_sample_index"], "first_sample_index")
    count = exact_int(chunk["sample_count"], "sample_count", 1)
    rate = exact_int(chunk["sample_rate_hz"], "sample_rate_hz", 1)
    monotonic_ns = exact_int(chunk["rx_monotonic_ns"], "rx_monotonic_ns", 1)
    duration_ns = math.ceil(count * 1_000_000_000 / rate)
    byte_offset = exact_int(chunk["byte_offset"], "byte_offset")
    byte_length = exact_int(chunk["byte_length"], "byte_length", 1)
    rx_wall_time = finite_number(chunk["rx_wall_time"], "rx_wall_time", 0)
    chunk_gain = exact_int(chunk["rx_gain_db"], "chunk rx_gain_db")
    if rate != sample_rate or byte_length != count * 8:
        raise SystemExit("chunk sample rate or byte length is invalid")
    if chunk_gain != requested_gain:
        raise SystemExit("chunk rx_gain_db disagrees with requested window gain")
    if type(chunk["metadata"]) is not dict or type(chunk["rf_metrics"]) is not dict:
        raise SystemExit("chunk metadata or RF metrics is invalid")
    if type(chunk["target_version"]) is not int or type(chunk["context_version"]) is not int \
            or type(chunk["target"]) is not str or not chunk["target"]:
        raise SystemExit("chunk target context is invalid")
    metadata = chunk["metadata"]
    metadata_keys = {
        "target", "team", "profile", "target_version", "context_version",
        "decoder_primary", "decoder_shadow", "adc_code_scale",
    }
    if set(metadata) != metadata_keys:
        raise SystemExit("chunk metadata schema is invalid")
    chunk_context_expected = {
        "target": chunk["target"],
        "target_version": chunk["target_version"],
        "context_version": chunk["context_version"],
    }
    if any(type(metadata.get(name)) is not type(value) or metadata.get(name) != value
           for name, value in chunk_context_expected.items()) \
            or type(metadata.get("team")) is not str or not metadata["team"] \
            or type(metadata.get("profile")) is not str or not metadata["profile"] \
            or type(metadata.get("decoder_primary")) is not str \
            or not metadata["decoder_primary"] \
            or type(metadata.get("decoder_shadow")) is not str:
        raise SystemExit("chunk metadata context is invalid")
    adc_code_scale = finite_number(
        metadata["adc_code_scale"], "chunk adc_code_scale", 0,
    )
    if adc_code_scale != required_adc_code_scale:
        raise SystemExit("chunk adc_code_scale is not the required 2048.0")
    metric_payload = chunk["rf_metrics"]
    if set(metric_payload) != {"rms", "peak", "clipping_ratio", "sample_count"}:
        raise SystemExit("chunk RF metrics schema is invalid")
    metric_count = exact_int(metric_payload["sample_count"], "chunk RF sample_count", 1)
    metric_peak = finite_number(metric_payload["peak"], "chunk RF peak", 0)
    metric_rms = finite_number(metric_payload["rms"], "chunk RF RMS", 0)
    metric_clipping = finite_number(
        metric_payload["clipping_ratio"], "chunk RF clipping ratio", 0, 1,
    )
    if metric_count != count or metric_rms > metric_peak:
        raise SystemExit("chunk RF metrics disagree with chunk")
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
        "duration_ns": duration_ns,
        "rx_monotonic_ns": monotonic_ns,
        "rx_wall_time": rx_wall_time,
        "byte_offset": byte_offset,
        "byte_length": byte_length,
        "target_version": chunk["target_version"],
        "context_version": chunk["context_version"],
        "target": chunk["target"],
        "team": metadata["team"],
        "profile": metadata["profile"],
        "decoder_primary": metadata["decoder_primary"],
        "decoder_shadow": metadata["decoder_shadow"],
        "adc_code_scale": adc_code_scale,
        "rx_gain_db": chunk_gain,
        "lo_hz": exact_int(chunk["lo_hz"], "chunk lo_hz", 1),
        "rf_bandwidth_hz": exact_int(
            chunk["rf_bandwidth_hz"], "chunk rf_bandwidth_hz", 1,
        ),
        "rf_metrics": {
            "rms": metric_rms,
            "peak": metric_peak,
            "clipping_ratio": metric_clipping,
            "sample_count": metric_count,
        },
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
chunk_start_ns = chunks[0]["rx_monotonic_ns"]
chunk_start_ns -= chunks[0]["duration_ns"]
chunk_end_ns = chunks[-1]["rx_monotonic_ns"]
chunk_intersection_start_ns = max(chunk_start_ns, window_start_ns)
chunk_intersection_end_ns = min(chunk_end_ns, window_end_ns)
chunk_coverage_sec = max(
    0.0, (chunk_intersection_end_ns - chunk_intersection_start_ns) / 1_000_000_000
)
chunk_head_missing_sec = max(0.0, (chunk_start_ns - window_start_ns) / 1_000_000_000)
chunk_tail_missing_sec = max(0.0, (window_end_ns - chunk_end_ns) / 1_000_000_000)
chunk_early_extra_sec = max(0.0, (window_start_ns - chunk_start_ns) / 1_000_000_000)
chunk_late_extra_sec = max(0.0, (chunk_end_ns - window_end_ns) / 1_000_000_000)
max_chunk_period_sec = max(chunk["duration_ns"] for chunk in chunks) / 1_000_000_000
chunk_tolerance_sec = min(
    max_chunk_tolerance_sec,
    max_chunk_period_sec * 0.5 + chunk_scheduling_margin_sec,
    max_chunk_period_sec * 0.9,
)

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
validation_payload_keys = {
    "command_event_id", "chunk_id", "chunk_first_sample_index",
    "chunk_last_sample_index", "target_version", "context_version", "target",
    "team", "profile", "decoder_id", "cmd_id", "payload", "crc8_ok",
    "crc16_ok", "crc_mode", "receive_wall_time", "command_first_sample_index",
    "command_last_sample_index", "accepted", "reason", "ascii_code", "level",
}
decoder_reset_payload_keys = {
    "reason", "chunk_id", "context_version", "target_version", "target",
    "team", "profile", "role",
}
decoder_error_payload_keys = {"chunk_id", "decoder_id", "error", "role"}
output_error_payload_keys = {"chunk_id", "decoder_id", "error"}
discontinuity_payload_keys = {
    "reason", "reconnects", "read_errors", "reconnect_generations",
    "first_post_reconnect_chunk_id",
}
recording_stopped_payload_keys = {"reason"}
reset_reasons = {
    "startup", "context_change", "target_change", "device_reconnect", "manual",
}
event_processing_lag_sec = status_tolerance_sec
event_processing_lag_ns = math.ceil(event_processing_lag_sec * 1_000_000_000)

def event_chunk(payload, description):
    chunk_id = exact_int(payload.get("chunk_id"), f"{description} chunk_id")
    if chunk_id >= len(chunks):
        raise SystemExit(f"{description} chunk_id is outside recorder chunks")
    return chunks[chunk_id]

def require_event_chunk_time(event_ns, chunk, description):
    if event_ns < chunk["rx_monotonic_ns"] \
            or event_ns > chunk["rx_monotonic_ns"] + event_processing_lag_ns:
        raise SystemExit(f"{description} timestamp is outside its chunk processing interval")

def require_event_context(payload, chunk, description):
    expected = {
        "chunk_id": chunk["chunk_id"],
        "context_version": chunk["context_version"],
        "target_version": chunk["target_version"],
        "target": chunk["target"],
        "team": chunk["team"],
        "profile": chunk["profile"],
    }
    if any(type(payload.get(name)) is not type(value) or payload.get(name) != value
           for name, value in expected.items()):
        raise SystemExit(f"{description} context disagrees with recorder chunk")

rf_events = []
commands = {}
validations = {}
decoder_role_events = []
discontinuity_events = []
recording_stopped_line = None
event_kind_counts = {}
event_count = 0
for line_number, line in bounded_fd_lines(
    events_fd, "events JSONL", launch_log_max_bytes,
    max(2000, math.ceil(requested_duration_sec * 2000) + 2000),
):
        event_count += 1
        event = load_json_line(line, f"event line {line_number}")
        if set(event) != event_keys or type(event["kind"]) is not str or type(event["payload"]) is not dict:
            raise SystemExit(f"event line {line_number} schema is invalid")
        finite_number(event["wall_time"], "event wall_time", 0)
        event_monotonic_ns = exact_int(event["monotonic_ns"], "event monotonic ns", 1)
        payload = event["payload"]
        event_kind_counts[event["kind"]] = event_kind_counts.get(event["kind"], 0) + 1
        if event["kind"] == "rf_state":
            if set(payload) != rf_payload_keys:
                raise SystemExit("RF event payload schema is invalid")
            chunk_id = exact_int(payload["chunk_id"], "RF chunk_id")
            if chunk_id >= len(chunks) or chunk_id != len(rf_events):
                raise SystemExit("RF event chunk sequence is invalid")
            chunk = chunks[chunk_id]
            require_event_chunk_time(event_monotonic_ns, chunk, "RF event")
            if (
                payload["first_sample_index"] != chunk["first_sample_index"]
                or payload["last_sample_index"] != chunk["first_sample_index"] + chunk["sample_count"] - 1
                or payload["target_version"] != chunk["target_version"]
                or payload["context_version"] != chunk["context_version"]
                or payload["target"] != chunk["target"]
            ):
                raise SystemExit("RF event range or target context is invalid")
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
            rf_context_expected = {
                "target_version": chunk["target_version"],
                "context_version": chunk["context_version"],
                "target": chunk["target"],
                "team": chunk["team"],
                "profile": chunk["profile"],
            }
            if any(type(payload.get(name)) is not type(value) \
                   or payload.get(name) != value
                   for name, value in rf_context_expected.items()):
                raise SystemExit("RF event context disagrees with chunk metadata")
            event_adc_scale = finite_number(
                payload["adc_code_scale"], "RF event adc_code_scale", 0,
            )
            event_clipping_threshold = finite_number(
                payload["rf_clipping_ratio"], "RF event clipping threshold", 0, 1,
            )
            if event_adc_scale != chunk["adc_code_scale"] \
                    or event_clipping_threshold != clipping_threshold:
                raise SystemExit("RF event configuration disagrees with acceptance configuration")
            exact_chunk_metrics = chunk["rf_metrics"]
            normalized_event_metrics = {
                "rms": rms,
                "peak": peak,
                "clipping_ratio": clipping,
                "sample_count": count,
            }
            if normalized_event_metrics != exact_chunk_metrics:
                raise SystemExit("RF event metrics disagree with chunk RF metrics")
            if (clipping >= clipping_threshold) != (state == "clipped"):
                raise SystemExit("RF event state contradicts clipping threshold")
            rf_events.append({"state": state, "peak": peak, "rms": rms, "clipping": clipping, "count": count})
        elif event["kind"] == "command":
            if set(payload) != command_payload_keys:
                raise SystemExit("command event payload schema is invalid")
            event_id = payload["command_event_id"]
            if type(event_id) is not str or not event_id or len(event_id) > 128 \
                    or event_id in commands:
                raise SystemExit("command event id is invalid or duplicated")
            commands[event_id] = (payload, event_monotonic_ns)
        elif event["kind"] == "validation":
            if set(payload) != validation_payload_keys:
                raise SystemExit("validation event payload schema is invalid")
            event_id = payload["command_event_id"]
            if type(event_id) is not str or not event_id or len(event_id) > 128 \
                    or event_id in validations:
                raise SystemExit("validation event id is invalid or duplicated")
            validations[event_id] = (payload, event_monotonic_ns)
        elif event["kind"] in {"decoder_reset", "decoder_reset_error"}:
            expected_keys = decoder_reset_payload_keys | (
                {"error"} if event["kind"] == "decoder_reset_error" else set()
            )
            if set(payload) != expected_keys:
                raise SystemExit(f"{event['kind']} payload schema is invalid")
            chunk = event_chunk(payload, event["kind"])
            require_event_chunk_time(event_monotonic_ns, chunk, event["kind"])
            require_event_context(payload, chunk, event["kind"])
            if type(payload["reason"]) is not str \
                    or payload["reason"] not in reset_reasons:
                raise SystemExit(f"{event['kind']} reason is invalid")
            role = payload["role"]
            if type(role) is not str or role not in {"primary", "shadow"}:
                raise SystemExit(f"{event['kind']} role is invalid")
            if event["kind"] == "decoder_reset_error" \
                    and (type(payload["error"]) is not str or not payload["error"]):
                raise SystemExit("decoder_reset_error error text is invalid")
            decoder_role_events.append((event["kind"], role, None))
        elif event["kind"] == "decoder_error":
            if set(payload) != decoder_error_payload_keys:
                raise SystemExit("decoder_error payload schema is invalid")
            chunk = event_chunk(payload, "decoder_error")
            require_event_chunk_time(event_monotonic_ns, chunk, "decoder_error")
            role = payload["role"]
            decoder_id = payload["decoder_id"]
            if type(role) is not str or role not in {"primary", "shadow"} \
                    or type(decoder_id) is not str or not decoder_id \
                    or type(payload["error"]) is not str or not payload["error"]:
                raise SystemExit("decoder_error payload values are invalid")
            decoder_role_events.append(("decoder_error", role, decoder_id))
        elif event["kind"] == "output_error":
            if set(payload) != output_error_payload_keys:
                raise SystemExit("output_error payload schema is invalid")
            chunk = event_chunk(payload, "output_error")
            require_event_chunk_time(event_monotonic_ns, chunk, "output_error")
            decoder_id = payload["decoder_id"]
            if type(decoder_id) is not str or not decoder_id \
                    or type(payload["error"]) is not str or not payload["error"]:
                raise SystemExit("output_error payload values are invalid")
            decoder_role_events.append(("output_error", "primary", decoder_id))
        elif event["kind"] == "discontinuity":
            if set(payload) != discontinuity_payload_keys \
                    or payload["reason"] != "device_reconnect":
                raise SystemExit("discontinuity payload schema is invalid")
            reconnects = exact_int(payload["reconnects"], "discontinuity reconnects", 1)
            exact_int(payload["read_errors"], "discontinuity read_errors")
            generations = payload["reconnect_generations"]
            if type(generations) is not list or not generations \
                    or any(type(value) is not int or value <= 0 for value in generations) \
                    or generations != list(range(generations[0], generations[0] + len(generations))) \
                    or generations[-1] != reconnects:
                raise SystemExit("discontinuity reconnect generations are invalid")
            first_post = exact_int(
                payload["first_post_reconnect_chunk_id"],
                "discontinuity first post-reconnect chunk_id",
            )
            if first_post > len(chunks):
                raise SystemExit("discontinuity post-reconnect chunk is outside recorder chunks")
            if first_post > 0 and event_monotonic_ns < chunks[first_post - 1]["rx_monotonic_ns"]:
                raise SystemExit("discontinuity timestamp predates the previous chunk")
            if first_post < len(chunks) \
                    and event_monotonic_ns > chunks[first_post]["rx_monotonic_ns"]:
                raise SystemExit("discontinuity timestamp follows the declared post-reconnect chunk")
            discontinuity_events.append(payload)
        elif event["kind"] == "recording_stopped":
            if set(payload) != recording_stopped_payload_keys \
                    or type(payload["reason"]) is not str or not payload["reason"] \
                    or recording_stopped_line is not None \
                    or event_monotonic_ns < chunks[-1]["rx_monotonic_ns"]:
                raise SystemExit("recording_stopped event is invalid")
            recording_stopped_line = line_number
        else:
            raise SystemExit(f"unsupported recorder event kind: {event['kind']}")
if len(rf_events) != len(chunks):
    raise SystemExit("every chunk must have exactly one authoritative RF event")
if recording_stopped_line is not None and recording_stopped_line != event_count:
    raise SystemExit("recording_stopped must be the final recorder event")
if discontinuity_events and (
    max(item["reconnects"] for item in discontinuity_events)
        > counter_fields["acquisition_reconnects"]
    or max(item["read_errors"] for item in discontinuity_events)
        > counter_fields["acquisition_read_errors"]
):
    raise SystemExit("discontinuity events disagree with acquisition counters")

if os.fstat(summary_fd).st_size > line_max_bytes:
    raise SystemExit("recorder summary exceeds the resource limit")
summary_raw = os.pread(summary_fd, os.fstat(summary_fd).st_size + 1, 0)
try:
    summary_text = summary_raw.decode("utf-8", errors="strict")
except UnicodeDecodeError as exc:
    raise SystemExit("recorder summary is not UTF-8") from exc
summary = load_json_line(summary_text, "recorder summary")
summary_core = {
    "format", "files", "chunks_written", "events_written", "samples_written",
    "bytes_written", "dropped_chunks", "dropped_events", "queue_overflows",
    "dropped_chunk_range", "dropped_chunk_ranges",
    "dropped_chunk_ranges_overflow", "dropped_event_kinds", "latest_rf_metrics",
    "started_wall_time", "closed_wall_time", "stopped_reason",
}
summary_metadata = {
    "sample_rate", "rx_buffer_size", "sps", "symbol_rate", "run_mode",
    "own_team", "rx_team", "team", "core_team", "target", "context_version",
    "radio", "rx_lo_hz", "rf_bandwidth_hz", "rx_gain", "gain_ceiling",
    "adc_rms", "rf_state", "profile_path", "profile", "runtime",
    "decoder_primary", "decoder_shadow", "adc_code_scale", "rf_clipping_ratio",
}
if not summary_core <= set(summary) or not set(summary) <= summary_core | summary_metadata:
    raise SystemExit("recorder summary schema is invalid")
def contains_metadata_error(value):
    if type(value) is dict:
        return "metadata_error" in value or any(contains_metadata_error(child) for child in value.values())
    if type(value) is list:
        return any(contains_metadata_error(child) for child in value)
    return False
if contains_metadata_error(summary):
    raise SystemExit("recorder summary contains a metadata provider error")
decoder_primary = summary.get("decoder_primary")
decoder_shadow = summary.get("decoder_shadow")
if type(decoder_primary) is not str or not decoder_primary \
        or len(decoder_primary) > 128:
    raise SystemExit("recorder summary decoder_primary is invalid")
if type(decoder_shadow) is not str or len(decoder_shadow) > 128:
    raise SystemExit("recorder summary decoder_shadow is invalid")
for kind, role, event_decoder_id in decoder_role_events:
    expected_decoder_id = decoder_primary if role == "primary" else decoder_shadow
    if not expected_decoder_id:
        raise SystemExit(f"{kind} references an unconfigured {role} decoder")
    if event_decoder_id is not None and event_decoder_id != expected_decoder_id:
        raise SystemExit(f"{kind} decoder identity disagrees with recorder summary")
if summary.get("runtime") != "common_competition" \
        or summary.get("run_mode") != "competition":
    raise SystemExit("recorder summary is not from the bundled competition runtime")
summary_adc_code_scale = finite_number(
    summary.get("adc_code_scale"), "recorder summary adc_code_scale", 0,
)
summary_clipping_threshold = finite_number(
    summary.get("rf_clipping_ratio"), "recorder summary clipping threshold", 0, 1,
)
summary_rx_gain = exact_int(summary.get("rx_gain"), "recorder summary rx_gain")
summary_sample_rate = exact_int(
    summary.get("sample_rate"), "recorder summary sample_rate", 1,
)
summary_rx_lo_hz = exact_int(
    summary.get("rx_lo_hz"), "recorder summary rx_lo_hz", 1,
)
summary_rf_bandwidth_hz = exact_int(
    summary.get("rf_bandwidth_hz"), "recorder summary rf_bandwidth_hz", 1,
)
radio = summary.get("radio")
radio_keys = {
    "team", "target", "base_freq_hz", "rx_lo_hz", "lo_offset_hz",
    "digital_shift_hz", "rf_bandwidth_hz", "rx_gain", "mode",
}
if type(radio) is not dict or set(radio) != radio_keys:
    raise SystemExit("recorder summary radio snapshot schema is invalid")
for name in ("team", "target", "mode"):
    if type(radio[name]) is not str or not radio[name]:
        raise SystemExit(f"recorder summary radio {name} is invalid")
for name in ("base_freq_hz", "rx_lo_hz", "rf_bandwidth_hz"):
    exact_int(radio[name], f"recorder summary radio {name}", 1)
for name in ("lo_offset_hz", "digital_shift_hz"):
    if type(radio[name]) is not int:
        raise SystemExit(f"recorder summary radio {name} is invalid")
radio_rx_gain = exact_int(
    radio["rx_gain"], "recorder summary radio rx_gain",
)
if summary_adc_code_scale != required_adc_code_scale \
        or summary_clipping_threshold != clipping_threshold \
        or summary_sample_rate != sample_rate \
        or summary_rx_gain != requested_gain \
        or radio_rx_gain != summary_rx_gain \
        or radio["rx_lo_hz"] != summary_rx_lo_hz \
        or radio["rf_bandwidth_hz"] != summary_rf_bandwidth_hz:
    raise SystemExit("recorder summary RF configuration is invalid")
if any(chunk["decoder_primary"] != decoder_primary \
       or chunk["decoder_shadow"] != decoder_shadow \
       or chunk["adc_code_scale"] != summary_adc_code_scale \
       or chunk["lo_hz"] != summary_rx_lo_hz \
       or chunk["rf_bandwidth_hz"] != summary_rf_bandwidth_hz
       for chunk in chunks):
    raise SystemExit("chunk metadata disagrees with recorder summary")

command_evidence_violations = []
accepted_ids = []
crc_valid_ids = []
out_of_window_command_ids = []
primary_ids = {
    event_id for event_id, (command, _event_ns) in commands.items()
    if command.get("role") == "primary"
}
if primary_ids != set(validations):
    command_evidence_violations.append("command_validation_id_set_mismatch")
for event_id in sorted(set(commands) | set(validations)):
    command_record = commands.get(event_id)
    if command_record is None:
        continue
    command, command_event_ns = command_record
    role = command.get("role")
    if type(role) is not str or role not in {"primary", "shadow"}:
        command_evidence_violations.append(f"{event_id}:role_invalid")
        continue
    validation_record = validations.get(event_id)
    if role == "primary" and validation_record is None:
        continue
    if role == "shadow" and validation_record is not None:
        command_evidence_violations.append(f"{event_id}:shadow_has_validation")
        continue
    validation = validation_record[0] if validation_record is not None else None
    validation_event_ns = validation_record[1] if validation_record is not None else None
    try:
        chunk_id = exact_int(command["chunk_id"], "command chunk_id")
        chunk = chunks[chunk_id]
    except (IndexError, SystemExit):
        command_evidence_violations.append(f"{event_id}:chunk_missing")
        continue
    first = chunk["first_sample_index"]
    last_index = first + chunk["sample_count"] - 1
    event_deadline_ns = chunk["rx_monotonic_ns"] + event_processing_lag_ns
    if not chunk["rx_monotonic_ns"] <= command_event_ns <= event_deadline_ns \
            or (validation_event_ns is not None and not command_event_ns \
                <= validation_event_ns <= event_deadline_ns):
        command_evidence_violations.append(f"{event_id}:event_processing_lag_invalid")
        continue
    command_context_expected = {
        "chunk_first_sample_index": first,
        "chunk_last_sample_index": last_index,
        "target_version": chunk["target_version"],
        "context_version": chunk["context_version"],
        "target": chunk["target"],
        "team": chunk["team"],
        "profile": chunk["profile"],
    }
    if any(type(command.get(name)) is not type(value) \
           or command.get(name) != value
           for name, value in command_context_expected.items()):
        command_evidence_violations.append(f"{event_id}:command_chunk_context_mismatch")
        continue
    expected_decoder = decoder_primary if role == "primary" else decoder_shadow
    if not expected_decoder or type(command.get("decoder_id")) is not str \
            or command["decoder_id"] != expected_decoder:
        command_evidence_violations.append(f"{event_id}:decoder_role_mismatch")
        continue
    if type(command.get("team")) is not str or not command["team"] \
            or len(command["team"]) > 32 \
            or type(command.get("profile")) is not str or not command["profile"] \
            or len(command["profile"]) > 128 \
            or type(command.get("crc_mode")) is not str or not command["crc_mode"] \
            or len(command["crc_mode"]) > 128:
        command_evidence_violations.append(f"{event_id}:command_text_field_invalid")
        continue
    if type(command.get("cmd_id")) is not int \
            or not 0 <= command["cmd_id"] <= 0xFFFF:
        command_evidence_violations.append(f"{event_id}:cmd_id_invalid")
        continue
    payload_hex = command.get("payload")
    if type(payload_hex) is not str or len(payload_hex) > 8192 \
            or len(payload_hex) % 2 != 0 \
            or re.fullmatch(r"[0-9a-f]*", payload_hex) is None:
        command_evidence_violations.append(f"{event_id}:payload_invalid")
        continue
    if type(command.get("crc8_ok")) is not bool \
            or type(command.get("crc16_ok")) is not bool:
        command_evidence_violations.append(f"{event_id}:crc_flag_invalid")
        continue
    if type(command["evidence"]) is not dict:
        command_evidence_violations.append(f"{event_id}:evidence_invalid")
        continue
    if type(command["receive_wall_time"]) not in (int, float) \
            or not math.isfinite(command["receive_wall_time"]):
        command_evidence_violations.append(f"{event_id}:receive_time_invalid")
        continue
    command_first = command["first_sample_index"]
    command_last = command["last_sample_index"]
    if type(command_first) is not int or type(command_last) is not int \
            or not first <= command_first <= command_last <= last_index:
        command_evidence_violations.append(f"{event_id}:sample_range_invalid")
        continue
    chunk_sample_start_ns = chunk["rx_monotonic_ns"] - chunk["duration_ns"]
    command_sample_start_ns = chunk_sample_start_ns + (
        (command_first - first) * 1_000_000_000 // sample_rate
    )
    command_sample_end_ns = chunk_sample_start_ns + math.ceil(
        (command_last - first + 1) * 1_000_000_000 / sample_rate
    )
    sample_window_class = classify_sample_window(
        command_sample_start_ns, command_sample_end_ns, window_start_ns, window_end_ns,
    )
    if sample_window_class == "crosses_boundary":
        command_evidence_violations.append(f"{event_id}:sample_range_crosses_window_boundary")
        continue
    window_eligible = sample_window_class == "in_window"
    if validation is not None:
        mirrored = {
            "chunk_id": chunk_id,
            "chunk_first_sample_index": first,
            "chunk_last_sample_index": last_index,
            "target_version": chunk["target_version"],
            "context_version": chunk["context_version"],
            "target": chunk["target"],
            "team": command["team"],
            "profile": command["profile"],
            "decoder_id": command["decoder_id"],
            "cmd_id": command["cmd_id"],
            "payload": command["payload"],
            "crc8_ok": command["crc8_ok"],
            "crc16_ok": command["crc16_ok"],
            "crc_mode": command["crc_mode"],
            "receive_wall_time": command["receive_wall_time"],
            "command_first_sample_index": command["first_sample_index"],
            "command_last_sample_index": command["last_sample_index"],
        }
        if any(type(validation.get(name)) is not type(value) \
               or validation.get(name) != value for name, value in mirrored.items()):
            command_evidence_violations.append(f"{event_id}:command_validation_mismatch")
            continue
        ascii_code = validation.get("ascii_code")
        level = validation.get("level")
        if type(validation.get("accepted")) is not bool \
                or type(validation.get("reason")) is not str \
                or not validation["reason"] or len(validation["reason"]) > 512 \
                or (ascii_code is not None and (type(ascii_code) is not str \
                    or len(ascii_code) > 4096)) \
                or (level is not None and (type(level) is not int \
                    or level not in {1, 2, 3})):
            command_evidence_violations.append(f"{event_id}:validation_result_invalid")
            continue
        if validation["accepted"]:
            try:
                payload_ascii = bytes.fromhex(payload_hex).decode("ascii")
            except (ValueError, UnicodeDecodeError):
                payload_ascii = None
            evidence_level = command["evidence"].get("level")
            if command["crc8_ok"] is not True \
                    or command["crc16_ok"] is not True \
                    or command["cmd_id"] != 0x0A06 or payload_ascii is None \
                    or re.fullmatch(r"[A-Za-z0-9]{6}", payload_ascii) is None \
                    or type(evidence_level) is not int \
                    or evidence_level not in {1, 2, 3} \
                    or validation["reason"] != "accepted" \
                    or ascii_code != payload_ascii or level != evidence_level:
                command_evidence_violations.append(f"{event_id}:accepted_validation_invalid")
                continue
            if window_eligible:
                accepted_ids.append(event_id)
    if not window_eligible:
        out_of_window_command_ids.append(event_id)
    if window_eligible and role == "primary" and event_id in accepted_ids \
            and command["crc8_ok"] is True and command["crc16_ok"] is True:
        crc_valid_ids.append(event_id)
crc16_count = len(crc_valid_ids)
expected_files = {
    "iq": c64_path.name, "chunks": chunks_path.name, "events": events_path.name,
    "summary": summary_path.name,
}
if summary["format"] != "numpy.complex64 little-endian interleaved IQ" \
        or summary["files"] != expected_files:
    raise SystemExit("recorder summary format or file manifest is invalid")
summary_counts = {
    "chunks_written": len(chunks),
    "events_written": event_count,
    "samples_written": actual_samples,
    "bytes_written": actual_samples * 8,
    "dropped_chunks": 0,
    "dropped_events": 0,
    "queue_overflows": 0,
}
for name, expected in summary_counts.items():
    if type(summary.get(name)) is not int or summary[name] != expected:
        raise SystemExit(f"recorder summary {name} is invalid")
if os.fstat(c64_fd).st_size != summary["bytes_written"] \
        or chunks[-1]["byte_offset"] + chunks[-1]["byte_length"] != summary["bytes_written"]:
    raise SystemExit("IQ byte size disagrees with chunks or recorder summary")
overflow = summary["dropped_chunk_ranges_overflow"]
overflow_keys = {
    "first_chunk_id", "last_chunk_id", "first_sample_index",
    "last_sample_index_exclusive", "first_target", "first_target_version",
    "first_context_version", "last_target", "last_target_version",
    "last_context_version", "mixed_context", "range_count",
}
if overflow is not None:
    if type(overflow) is not dict or set(overflow) != overflow_keys:
        raise SystemExit("recorder summary dropped-range overflow schema is invalid")
    for name in {
        "first_chunk_id", "last_chunk_id", "first_sample_index",
        "last_sample_index_exclusive", "first_target_version",
        "first_context_version", "last_target_version", "last_context_version",
        "range_count",
    }:
        exact_int(overflow[name], f"recorder overflow {name}", 1 if name == "range_count" else 0)
    if type(overflow["first_target"]) is not str \
            or type(overflow["last_target"]) is not str \
            or type(overflow["mixed_context"]) is not bool \
            or overflow["last_chunk_id"] < overflow["first_chunk_id"] \
            or overflow["last_sample_index_exclusive"] <= overflow["first_sample_index"]:
        raise SystemExit("recorder summary dropped-range overflow is invalid")
latest_summary_metrics = summary.get("latest_rf_metrics")
if type(latest_summary_metrics) is not dict \
        or set(latest_summary_metrics) != {"rms", "peak", "clipping_ratio", "sample_count"}:
    raise SystemExit("recorder summary latest RF metrics schema is invalid")
normalized_summary_metrics = {
    "rms": finite_number(latest_summary_metrics["rms"], "summary latest RF RMS", 0),
    "peak": finite_number(latest_summary_metrics["peak"], "summary latest RF peak", 0),
    "clipping_ratio": finite_number(
        latest_summary_metrics["clipping_ratio"], "summary latest RF clipping ratio", 0, 1,
    ),
    "sample_count": exact_int(
        latest_summary_metrics["sample_count"], "summary latest RF sample_count", 1,
    ),
}
if normalized_summary_metrics != chunks[-1]["rf_metrics"]:
    raise SystemExit("recorder summary latest RF metrics disagree with last chunk")
if summary["dropped_chunk_range"] is not None \
        or summary["dropped_chunk_ranges"] != [] \
        or overflow is not None \
        or summary["dropped_event_kinds"] != {}:
    raise SystemExit("recorder summary reports dropped evidence")
started = finite_number(summary["started_wall_time"], "recorder started_wall_time", 0)
closed = finite_number(summary["closed_wall_time"], "recorder closed_wall_time", 0)
if started > min(chunk["rx_wall_time"] for chunk in chunks) \
        or closed < max(chunk["rx_wall_time"] for chunk in chunks) \
        or closed < started \
        or summary["stopped_reason"] != "common receiver stopped":
    raise SystemExit("recorder summary finalization state is invalid")

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

launch_path = Path(launch_log)
if launch_path.is_symlink() or not launch_path.is_file() \
        or launch_path.stat().st_size > launch_log_max_bytes:
    raise SystemExit("launch log is invalid or exceeds its resource limit")
launch_lines = []
with launch_path.open(encoding="utf-8", errors="replace") as handle:
    for line_number, line in enumerate(handle, 1):
        if len(line.encode("utf-8")) > line_max_bytes:
            raise SystemExit("launch log line exceeds its resource limit")
        launch_lines.append(line)
libiio_timeouts = count_libiio_timeout_lines(launch_lines)

violations = sorted(set(counter_violations))
if not min_duty <= acquisition_duty <= 1.0:
    violations.append("acquisition_duty_outside_0.99_to_1.0")
if window_coverage_sec + 1e-9 < requested_duration_sec:
    violations.append("window_coverage_shorter_than_requested")
if status_coverage_sec < max(0.0, requested_duration_sec - status_tolerance_sec):
    violations.append("status_coverage_shorter_than_requested")
if chunk_coverage_sec < max(0.0, requested_duration_sec - chunk_tolerance_sec):
    violations.append("chunk_coverage_shorter_than_requested")
if status_head_missing_sec > status_tolerance_sec:
    violations.append("status_head_missing_exceeds_tolerance")
if status_tail_missing_sec > status_tolerance_sec:
    violations.append("status_tail_missing_exceeds_tolerance")
if chunk_head_missing_sec > chunk_tolerance_sec:
    violations.append("chunk_head_missing_exceeds_tolerance")
if chunk_tail_missing_sec > chunk_tolerance_sec:
    violations.append("chunk_tail_missing_exceeds_tolerance")
if counter_fields["queue_drops"] != 0:
    violations.append("queue_drops_nonzero")
if libiio_timeouts != 0:
    violations.append("libiio_timeouts_nonzero")
if counter_fields["acquisition_read_errors"] != 0 \
        or counter_fields["device_read_errors"] != 0 \
        or counter_fields["device_connection_errors"] != 0:
    violations.append("read_errors_nonzero")
if counter_fields["acquisition_reconnects"] != 0 \
        or counter_fields["device_reconnects"] != 0:
    violations.append("device_reconnects_nonzero")
if counter_fields["recorder_dropped_chunks"] != 0 or counter_fields["recorder_dropped_events"] != 0:
    violations.append("recorder_drops_nonzero")
if command_evidence_violations:
    violations.append("command_evidence_invalid")
if enforce_stability:
    if rf_state != "linear":
        violations.append("rf_state_not_linear")
    if crc16_count < min_crc16:
        violations.append("crc16_below_threshold")

def held_artifact_identity(name, fd, baseline, expected_digest=None):
    before = os.fstat(fd)
    path_info = os.stat(name, dir_fd=iq_dir_fd, follow_symlinks=False)
    if stable_identity(before) != baseline:
        raise SystemExit("recorder artifact changed after parsing")
    if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 \
            or (path_info.st_dev, path_info.st_ino) != (before.st_dev, before.st_ino):
        raise SystemExit("recorder artifact path identity changed before hashing")
    digest = digest_held_artifact(name, fd, baseline, "manifest hashing")
    if expected_digest is not None and digest != expected_digest:
        raise SystemExit(
            "recorder control artifact semantic/hash baseline changed: " + name
        )
    after = os.fstat(fd)
    path_after = os.stat(name, dir_fd=iq_dir_fd, follow_symlinks=False)
    if stable_identity(after) != baseline \
            or (path_after.st_dev, path_after.st_ino) != (before.st_dev, before.st_ino):
        raise SystemExit("recorder artifact changed while hashing")
    return {
        "path": f"iq/{name}",
        "device": before.st_dev,
        "inode": before.st_ino,
        "bytes": before.st_size,
        "sha256": digest,
    }

recorder_artifacts = {
    "iq": held_artifact_identity(
        c64_path.name, c64_fd, recorder_baselines[c64_path.name],
    ),
    "chunks": held_artifact_identity(
        chunks_path.name, chunks_fd, recorder_baselines[chunks_path.name],
        recorder_baseline_hashes[chunks_path.name],
    ),
    "events": held_artifact_identity(
        events_path.name, events_fd, recorder_baselines[events_path.name],
        recorder_baseline_hashes[events_path.name],
    ),
    "summary": held_artifact_identity(
        summary_path.name, summary_fd, recorder_baselines[summary_path.name],
        recorder_baseline_hashes[summary_path.name],
    ),
}
os.fsync(iq_dir_fd)

result = {
    "schema_version": 1,
    "stage": stage,
    "combination": combination,
    "gain_db": int(gain),
    "sample_rate_hz": sample_rate,
    "adc_code_scale": required_adc_code_scale,
    "rf_clipping_threshold": clipping_threshold,
    "requested_duration_sec": requested_duration_sec,
    "event_kind_counts": event_kind_counts,
    "status_period_sec": status_period_sec,
    "status_scheduling_margin_sec": status_scheduling_margin_sec,
    "status_tolerance_sec": status_tolerance_sec,
    "max_chunk_period_sec": max_chunk_period_sec,
    "chunk_scheduling_margin_sec": chunk_scheduling_margin_sec,
    "chunk_tolerance_sec": chunk_tolerance_sec,
    "window_coverage_sec": window_coverage_sec,
    "status_coverage_sec": status_coverage_sec,
    "chunk_coverage_sec": chunk_coverage_sec,
    "status_head_missing_sec": status_head_missing_sec,
    "status_tail_missing_sec": status_tail_missing_sec,
    "status_early_extra_sec": status_early_extra_sec,
    "status_late_extra_sec": status_late_extra_sec,
    "chunk_head_missing_sec": chunk_head_missing_sec,
    "chunk_tail_missing_sec": chunk_tail_missing_sec,
    "chunk_early_extra_sec": chunk_early_extra_sec,
    "chunk_late_extra_sec": chunk_late_extra_sec,
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
    "crc_valid_primary_command_event_ids": crc_valid_ids,
    "accepted_command_event_ids": accepted_ids,
    "out_of_window_command_event_ids": out_of_window_command_ids,
    "event_processing_lag_sec": event_processing_lag_sec,
    "command_evidence_violations": command_evidence_violations,
    "acquisition_duty": acquisition_duty,
    "queue_drops": counter_fields["queue_drops"],
    "libiio_timeouts": libiio_timeouts,
    "acquisition_read_errors": counter_fields["acquisition_read_errors"],
    "acquisition_reconnects": counter_fields["acquisition_reconnects"],
    "device_read_errors": counter_fields["device_read_errors"],
    "device_reconnects": counter_fields["device_reconnects"],
    "device_connection_errors": counter_fields["device_connection_errors"],
    "recorder_dropped_chunks": counter_fields["recorder_dropped_chunks"],
    "recorder_dropped_events": counter_fields["recorder_dropped_events"],
    "stability_thresholds_enforced": enforce_stability,
    "recorder_artifacts": recorder_artifacts,
    "violations": violations,
    "passed": not violations,
}
production_metrics_schema = {
    "schema_version", "stage", "combination", "gain_db", "sample_rate_hz",
    "adc_code_scale", "rf_clipping_threshold", "requested_duration_sec",
    "event_kind_counts", "status_period_sec", "status_scheduling_margin_sec",
    "status_tolerance_sec", "max_chunk_period_sec",
    "chunk_scheduling_margin_sec", "chunk_tolerance_sec",
    "window_coverage_sec", "status_coverage_sec", "chunk_coverage_sec",
    "status_head_missing_sec", "status_tail_missing_sec",
    "status_early_extra_sec", "status_late_extra_sec",
    "chunk_head_missing_sec", "chunk_tail_missing_sec",
    "chunk_early_extra_sec", "chunk_late_extra_sec", "status_messages",
    "chunk_count", "actual_samples", "expected_samples", "peak", "rms",
    "rms_min", "rms_max", "rms_aggregation", "clipping_ratio", "rf_state",
    "observed_rf_states", "crc16_count",
    "crc_valid_primary_command_event_ids", "accepted_command_event_ids",
    "out_of_window_command_event_ids", "event_processing_lag_sec",
    "command_evidence_violations", "acquisition_duty", "queue_drops",
    "libiio_timeouts", "acquisition_read_errors", "acquisition_reconnects",
    "device_read_errors", "device_reconnects", "device_connection_errors",
    "recorder_dropped_chunks", "recorder_dropped_events",
    "stability_thresholds_enforced", "recorder_artifacts", "violations",
    "passed",
}
if set(result) != production_metrics_schema:
    raise SystemExit("internal production metrics schema mismatch")
with open(metrics_path, "x", encoding="utf-8") as handle:
    json.dump(result, handle, ensure_ascii=False, indent=2, sort_keys=True)
    handle.write("\n")
for fd in recorder_fds.values():
    os.close(fd)
os.close(iq_dir_fd)
if violations:
    raise SystemExit("window failed: " + ",".join(violations))
PY
}

append_result() {
  local metrics_path="$1"
  local label="$2"
  local usb_cable="$3"
  python3 - "$metrics_path" "$RESULTS_JSONL" "$RESULTS_IDENTITY" "$label" "$usb_cable" \
    "$CABLE_LENGTH_M" "$POWER_SUPPLY" "$TX_DISTANCE_M" "$POLARIZATION" <<'PY'
import json
import os
from pathlib import Path
import stat
import sys
(
    metrics_path, results_path, results_identity, label, usb_cable,
    cable, power, distance, polarization,
) = sys.argv[1:]
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
expected = tuple(int(value) for value in results_identity.split(":"))
fd = os.open(results_path, os.O_WRONLY | os.O_APPEND | getattr(os, "O_NOFOLLOW", 0))
try:
    info = os.fstat(fd)
    path_info = Path(results_path).lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or (
        info.st_dev, info.st_ino
    ) != expected or (path_info.st_dev, path_info.st_ino) != expected:
        raise SystemExit("results stream identity changed")
    encoded = (json.dumps(result, ensure_ascii=False, sort_keys=True) + "\n").encode()
    offset = 0
    while offset < len(encoded):
        offset += os.write(fd, encoded[offset:])
    os.fsync(fd)
finally:
    os.close(fd)
PY
}

append_combination_summary() {
  local combination="$1"
  local label="$2"
  local final_gain="$3"
  local total_crc16="$4"
  local final_linear_metrics="$5"
  python3 - "$RESULTS_JSONL" "$RESULTS_IDENTITY" "$combination" "$label" "$final_gain" \
    "$total_crc16" "$final_linear_metrics" "$CABLE_LENGTH_M" "$POWER_SUPPLY" \
    "$TX_DISTANCE_M" "$POLARIZATION" <<'PY'
import json
import os
from pathlib import Path
import stat
import sys
(
    results_path, results_identity, combination, label, final_gain, total_crc16, metrics_path,
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
expected = tuple(int(value) for value in results_identity.split(":"))
fd = os.open(results_path, os.O_WRONLY | os.O_APPEND | getattr(os, "O_NOFOLLOW", 0))
try:
    info = os.fstat(fd)
    path_info = Path(results_path).lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or (
        info.st_dev, info.st_ino
    ) != expected or (path_info.st_dev, path_info.st_ino) != expected:
        raise SystemExit("results stream identity changed")
    encoded = (json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n").encode()
    offset = 0
    while offset < len(encoded):
        offset += os.write(fd, encoded[offset:])
    os.fsync(fd)
finally:
    os.close(fd)
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
  LAST_METRICS_PATH=""

  local fallback_self_id
  if [[ "$OWN_TEAM" == "RED" ]]; then fallback_self_id=9; else fallback_self_id=109; fi
  start_bounded_launch "$launch_log" \
    ros2 launch sdr_receiver_py_wrapper competition_receiver.launch.py \
    initial_rx_gain:="$gain" \
    adc_code_scale:="$ADC_CODE_SCALE" \
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
    || { audit_event "window_failed" "$stage" "launch_group_identity"; return 1; }

  local collection_rc=0
  collect_status "$status_path" "$duration" "$iq_dir/$stage.events.jsonl" \
    || collection_rc=$?
  if ((collection_rc != 0)); then
    stop_launch
    audit_event "window_failed" "$stage" "status_collection_exit=$collection_rc"
    return "$collection_rc"
  fi
  local group_state=0
  launch_group_state || group_state=$?
  if ((group_state != 0)); then
    stop_launch || true
    audit_event "window_failed" "$stage" "receiver_exited_before_measurement_end"
    return 1
  fi
  stop_launch || { audit_event "window_failed" "$stage" "receiver_group_stop_failed"; return 1; }
  if ! analyze_window "$status_path" "$launch_log" "$iq_dir" "$metrics_path" \
    "$stage" "$combination" "$gain" "$enforce_stability" "$duration"; then
    audit_event "window_failed" "$stage" "offline_analysis_failed"
    return 1
  fi
  if ! append_result "$metrics_path" "$label" "$usb_cable"; then
    audit_event "window_failed" "$stage" "result_append_failed"
    return 1
  fi
  audit_event "window_complete" "$stage" "metrics=$metrics_path"
  LAST_METRICS_PATH="$metrics_path"
}

run_combination() {
  local ordinal="$1"
  local combination="$2"
  local label="$3"
  confirm_stage "$combination" "$label"
  audit_event "combination_start" "$combination" "ordinal=$ordinal label=$label"
  local gain=0
  local last_linear_gain=""
  local last_linear_crc16=0
  local last_linear_metrics=""
  local total_crc16=0
  while ((gain <= MAX_GAIN_DB)); do
    local stage metrics state crc16
    stage="$(printf 'matrix_%02d_%s_gain_%02d' "$ordinal" "$combination" "$gain")"
    if ! run_window "$stage" "$combination" "$label" "$gain" "$SCAN_DURATION_SEC" false ""; then
      die "$label measurement window failed at gain $gain"
    fi
    metrics="$LAST_METRICS_PATH"
    [[ -n "$metrics" ]] || die "$label measurement window did not publish a metrics path"
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
  LAST_FINAL_GAIN="$last_linear_gain"
}

start_jam_collector() {
  local output_path="$1"
  local prelaunch_ready_path="$2"
  local bound_ready_path="$3"
  local duration="$4"
  local expected_node_name="$5"
  COLLECTOR_FINISH_TIMEOUT_SEC="$(python3 -c '
import math
import sys
value = float(sys.argv[1])
if not math.isfinite(value) or value <= 0:
    raise SystemExit(1)
print(math.ceil(value) + 5)
' "$duration")" || return 1
  SPAWN_CRITICAL_KIND="collector"
  setsid python3 - "$output_path" "$prelaunch_ready_path" "$bound_ready_path" \
    "$duration" "$expected_node_name" "$JAM_RAW_MAX_BYTES" <<'PY' &
import json
import math
import os
import signal
import sys
import time

# Stop before importing ROS or creating any child/resource. The parent records
# PID/startticks/PGID/SID first, then resumes this already-isolated process.
os.kill(os.getpid(), signal.SIGSTOP)

import rclpy
from rclpy.executors import SingleThreadedExecutor, await_or_execute
from rclpy.qos import HistoryPolicy, QoSProfile, ReliabilityPolicy
from sdr_receiver.msg import JamCode

(
    output_path, prelaunch_ready_path, bound_ready_path, duration_text,
    expected_node_name, raw_max_text,
) = sys.argv[1:]
duration = float(duration_text)
raw_max = int(raw_max_text)
if not math.isfinite(duration) or duration <= 0:
    raise SystemExit("invalid JamCode collection duration")
if not expected_node_name or "/" in expected_node_name:
    raise SystemExit("invalid expected JamCode publisher node name")

def endpoint_gid(info):
    try:
        raw = bytes(info.endpoint_gid)
    except (TypeError, ValueError) as exc:
        raise SystemExit("JamCode publisher endpoint GID is unavailable") from exc
    if not raw or all(value == 0 for value in raw):
        raise SystemExit("JamCode publisher endpoint GID is invalid")
    return raw.hex()

def write_exclusive_json(path, payload):
    encoded = (json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8")
    if len(encoded) > raw_max:
        raise SystemExit("JamCode collector evidence exceeds resource limit")
    fd = os.open(
        path,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
        0o600,
    )
    try:
        offset = 0
        while offset < len(encoded):
            offset += os.write(fd, encoded[offset:])
        os.fsync(fd)
    finally:
        os.close(fd)

def message_payload(message):
    return {
        "header": {
            "stamp": {
                "sec": int(message.header.stamp.sec),
                "nanosec": int(message.header.stamp.nanosec),
            },
            "frame_id": str(message.header.frame_id),
        },
        "valid": bool(message.valid),
        "command_id": int(message.command_id),
        "level": int(message.level),
        "team": str(message.team),
        "target": str(message.target),
        "radio_mode": str(message.radio_mode),
        "rf_state": str(message.rf_state),
        "radar_info_raw": int(message.radar_info_raw),
        "key_mutable": bool(message.key_mutable),
        "key": [int(value) for value in message.key],
        "ascii_code": str(message.ascii_code),
    }

def interrupted(_signum, _frame):
    raise InterruptedError("collector interrupted")

signal.signal(signal.SIGTERM, interrupted)
signal.signal(signal.SIGINT, interrupted)
signal.signal(signal.SIGHUP, interrupted)
rclpy.init(args=None)
node = rclpy.create_node(f"rf_bench_jam_collector_{os.getpid()}")

class MessageInfoExecutor(SingleThreadedExecutor):
    """Humble's public executor drops subscription message_info."""

    def _take_subscription(self, subscription):
        with subscription.handle:
            return subscription.handle.take_message(
                subscription.msg_type, subscription.raw
            )

    async def _execute_subscription(self, subscription, taken_data):
        if taken_data is not None:
            await await_or_execute(subscription.callback, *taken_data)

executor = MessageInfoExecutor()
executor.add_node(node)
epoch_monotonic_ns = None
epoch_wall_ns = None
binding = None
records = []
callback_violations = []

def graph_publishers():
    return node.get_publishers_info_by_topic("/sdr/jam_code")

def current_binding_is_exclusive():
    if binding is None:
        return False
    infos = graph_publishers()
    if len(infos) != 1:
        return False
    info = infos[0]
    return (
        info.node_name == binding["publisher_node_name"]
        and info.node_namespace == binding["publisher_node_namespace"]
        and endpoint_gid(info) == binding["publisher_gid"]
    )

def on_jam_code(message, message_info):
    captured = time.monotonic_ns()
    if epoch_monotonic_ns is None or epoch_wall_ns is None or binding is None:
        return
    if not current_binding_is_exclusive():
        callback_violations.append("publisher graph changed at callback")
        return
    if type(message_info) is not dict or set(message_info) != {
        "source_timestamp", "received_timestamp",
    }:
        callback_violations.append("ROS message timestamp metadata is unavailable")
        return
    source_timestamp = message_info["source_timestamp"]
    received_timestamp = message_info["received_timestamp"]
    if type(source_timestamp) is not int or type(received_timestamp) is not int \
            or source_timestamp < epoch_wall_ns or received_timestamp < epoch_wall_ns:
        callback_violations.append("JamCode predates the measurement epoch")
        return
    records.append({
        **binding,
        "graph_binding_mode": "exclusive_expected_node_gid",
        "measurement_epoch_monotonic_ns": epoch_monotonic_ns,
        "measurement_epoch_wall_ns": epoch_wall_ns,
        "captured_monotonic_ns": captured,
        "source_timestamp_ns": source_timestamp,
        "received_timestamp_ns": received_timestamp,
        "message": message_payload(message),
    })

qos = QoSProfile(
    history=HistoryPolicy.KEEP_LAST,
    depth=5,
    reliability=ReliabilityPolicy.RELIABLE,
)
subscription = node.create_subscription(JamCode, "/sdr/jam_code", on_jam_code, qos)
try:
    zero_observations = 0
    graph_deadline = time.monotonic() + 20.0
    while zero_observations < 3:
        executor.spin_once(timeout_sec=0.1)
        if graph_publishers():
            raise SystemExit("JamCode publisher existed before tested receiver launch")
        zero_observations += 1
        if time.monotonic() >= graph_deadline:
            raise SystemExit("JamCode prelaunch graph check timed out")
    write_exclusive_json(prelaunch_ready_path, {
        "schema_version": 1,
        "publisher_count": 0,
        "collector_node_name": node.get_name(),
        "checked_monotonic_ns": time.monotonic_ns(),
    })

    graph_deadline = time.monotonic() + 20.0
    while binding is None:
        executor.spin_once(timeout_sec=0.05)
        infos = graph_publishers()
        if infos:
            if len(infos) == 1 and infos[0].node_name in {
                "", "_NODE_NAME_UNKNOWN_",
            }:
                if time.monotonic() >= graph_deadline:
                    raise SystemExit("JamCode publisher identity remained unresolved")
                continue
            if len(infos) != 1 or infos[0].node_name != expected_node_name:
                observed = ",".join(
                    f"{info.node_namespace}{info.node_name}:{endpoint_gid(info)}"
                    for info in infos
                )
                raise SystemExit(
                    "JamCode graph contains an unexpected publisher: " + observed
                )
            info = infos[0]
            binding = {
                "publisher_gid": endpoint_gid(info),
                "publisher_node_name": info.node_name,
                "publisher_node_namespace": info.node_namespace,
            }
            break
        if time.monotonic() >= graph_deadline:
            raise SystemExit("expected JamCode publisher did not appear")

    # Drain callbacks delivered before the epoch. DDS source timestamps then close
    # the small race between this drain and establishing the two-clock epoch.
    for _ in range(20):
        executor.spin_once(timeout_sec=0.0)
    epoch_wall_ns = time.time_ns()
    epoch_monotonic_ns = time.monotonic_ns()
    write_exclusive_json(bound_ready_path, {
        "schema_version": 1,
        **binding,
        "graph_binding_mode": "exclusive_expected_node_gid",
        "measurement_epoch_monotonic_ns": epoch_monotonic_ns,
        "measurement_epoch_wall_ns": epoch_wall_ns,
    })

    deadline = time.monotonic() + duration
    while time.monotonic() < deadline:
        executor.spin_once(timeout_sec=min(0.1, deadline - time.monotonic()))
        if not current_binding_is_exclusive():
            raise SystemExit("bound JamCode publisher graph changed during collection")
        if callback_violations:
            raise SystemExit(callback_violations[0])
        if len(records) > 1:
            raise SystemExit("JamCode capture must contain exactly one message")
    if callback_violations:
        raise SystemExit(callback_violations[0])
    if len(records) != 1:
        raise SystemExit("JamCode capture must contain exactly one message")
    write_exclusive_json(output_path, records[0])
except InterruptedError:
    pass
finally:
    executor.remove_node(node)
    executor.shutdown()
    node.destroy_subscription(subscription)
    node.destroy_node()
    if rclpy.ok():
        rclpy.shutdown()
PY
  local collector_pid=$!
  COLLECTOR_PROVISIONAL_PID="$collector_pid"
  if ! register_collector_group "$collector_pid"; then
    discard_provisional_collector
    finish_spawn_critical
    return 1
  fi
  if ! kill -CONT -- "-$COLLECTOR_PGID" 2>/dev/null; then
    terminate_collector_group false || true
    finish_spawn_critical
    return 1
  fi
  finish_spawn_critical
}

wait_for_ready_file() {
  local path="$1"
  local pid="$2"
  local deadline=$((SECONDS + 25))
  while [[ ! -e "$path" ]]; do
    [[ "$pid" == "$COLLECTOR_PID" ]] || die "topic collector readiness PID changed"
    local state=0
    collector_group_state || state=$?
    if ((state != 0)); then
      # The collector publishes readiness with an exclusive+fsynced write and
      # may exit immediately afterwards in very short test windows. Recheck
      # the marker after observing group exit before classifying that race as
      # a readiness failure; the caller still performs the bounded reap.
      [[ -e "$path" ]] && break
      finish_collector || true
      die "topic collector failed before readiness"
    fi
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

hold_radar_log_identity() {
  local identity_path="$1"
  exec {RADAR_LOG_FD}<"$RADAR_LOG" || return 1
  if ! python3 - "$RADAR_PID" "$RADAR_PID_START" "$RADAR_LOG" \
    "$RADAR_LOG_FD" "$identity_path" "$RADAR_PREFIX_MAX_BYTES" <<'PY'
import hashlib
import json
from pathlib import Path
import os
import stat
import sys

pid, expected_start, log_text, bash_fd_text, identity_text, prefix_max_text = sys.argv[1:]
prefix_max = int(prefix_max_text)
proc_dir = Path("/proc") / pid
proc_stat = proc_dir / "stat"
log_path = Path(log_text)
identity_path = Path(identity_text)
if not proc_stat.is_file() or proc_dir.stat().st_uid != os.getuid():
    raise SystemExit("radar PID identity is unavailable or owned by another user")
text = proc_stat.read_text(encoding="ascii")
right = text.rfind(")")
fields = text[right + 2:].split() if right >= 0 else []
if len(fields) < 20 or fields[19] != expected_start:
    raise SystemExit("radar PID was reused before log identity lock")

bash_fd_path = Path("/proc") / str(os.getppid()) / "fd" / bash_fd_text
held_stat = bash_fd_path.stat()
path_stat = log_path.lstat()
if not stat.S_ISREG(path_stat.st_mode) or (
    path_stat.st_dev, path_stat.st_ino
) != (held_stat.st_dev, held_stat.st_ino):
    raise SystemExit("radar log path changed while acquiring held descriptor")
if not stat.S_ISREG(held_stat.st_mode):
    raise SystemExit("held radar log is not a regular file")
if held_stat.st_size > prefix_max:
    raise SystemExit("radar log is not a fresh bounded evidence log")

for radar_fd_path in (proc_dir / "fd").iterdir():
    try:
        candidate = radar_fd_path.stat()
    except (FileNotFoundError, PermissionError):
        continue
    if (candidate.st_dev, candidate.st_ino) == (held_stat.st_dev, held_stat.st_ino):
        break
else:
    raise SystemExit("radar PID does not have the declared log inode open")

digest = hashlib.sha256()
with bash_fd_path.open("rb", buffering=0) as handle:
    remaining = held_stat.st_size
    while remaining:
        block = handle.read(min(1024 * 1024, remaining))
        if not block:
            raise SystemExit("radar log prefix became unreadable")
        digest.update(block)
        remaining -= len(block)
identity = {
    "schema_version": 1,
    "radar_pid": int(pid),
    "radar_pid_start_ticks": expected_start,
    "device": held_stat.st_dev,
    "inode": held_stat.st_ino,
    "start_size": held_stat.st_size,
    "prefix_sha256": digest.hexdigest(),
}
with identity_path.open("x", encoding="utf-8") as handle:
    json.dump(identity, handle, ensure_ascii=False, indent=2, sort_keys=True)
    handle.write("\n")
PY
  then
    close_radar_log_fd
    return 1
  fi
}

lock_radar_evidence_start() {
  local identity_path="$1"
  local evidence_start_path="$2"
  python3 - "$RADAR_PID" "$RADAR_PID_START" "$RADAR_LOG" \
    "$RADAR_LOG_FD" "$identity_path" "$evidence_start_path" \
    "$RADAR_PREFIX_MAX_BYTES" <<'PY'
import hashlib
import json
from pathlib import Path
import os
import stat
import sys
import time

(
    pid, expected_start, log_text, bash_fd_text, identity_text,
    evidence_start_text, prefix_max_text,
) = sys.argv[1:]
prefix_max = int(prefix_max_text)

def digest_prefix(fd, size):
    digest = hashlib.sha256()
    offset = 0
    while offset < size:
        block = os.pread(fd, min(1024 * 1024, size - offset), offset)
        if not block:
            raise SystemExit("held radar log prefix became unreadable")
        digest.update(block)
        offset += len(block)
    return digest.hexdigest()

identity_path = Path(identity_text)
if identity_path.is_symlink() or not identity_path.is_file():
    raise SystemExit("radar log identity evidence is invalid")
with identity_path.open(encoding="utf-8") as handle:
    identity = json.load(handle)
if type(identity) is not dict or set(identity) != {
    "schema_version", "radar_pid", "radar_pid_start_ticks", "device", "inode",
    "start_size", "prefix_sha256",
}:
    raise SystemExit("radar log identity evidence is invalid")
if identity["schema_version"] != 1 or identity["radar_pid"] != int(pid) \
        or identity["radar_pid_start_ticks"] != expected_start:
    raise SystemExit("radar log identity evidence disagrees with PID")
for name in ("device", "inode", "start_size"):
    if type(identity[name]) is not int or identity[name] < 0:
        raise SystemExit(f"radar log identity {name} is invalid")
if identity["start_size"] > prefix_max:
    raise SystemExit("radar log initial prefix exceeds its resource limit")
if type(identity["prefix_sha256"]) is not str \
        or len(identity["prefix_sha256"]) != 64:
    raise SystemExit("radar log identity prefix hash is invalid")

proc_dir = Path("/proc") / pid
proc_stat = proc_dir / "stat"
if not proc_stat.is_file() or proc_dir.stat().st_uid != os.getuid():
    raise SystemExit("radar process stopped before measurement evidence start")
text = proc_stat.read_text(encoding="ascii")
right = text.rfind(")")
fields = text[right + 2:].split() if right >= 0 else []
if len(fields) < 20 or fields[19] != expected_start:
    raise SystemExit("radar PID changed before measurement evidence start")

held_proc_path = Path("/proc") / str(os.getppid()) / "fd" / bash_fd_text
held_fd = os.open(held_proc_path, os.O_RDONLY)
try:
    held = os.fstat(held_fd)
    path_info = Path(log_text).lstat()
    expected_identity = (identity["device"], identity["inode"])
    if not stat.S_ISREG(held.st_mode) or held.st_nlink < 1 \
            or (held.st_dev, held.st_ino) != expected_identity \
            or (path_info.st_dev, path_info.st_ino) != expected_identity:
        raise SystemExit("radar log identity changed before measurement evidence start")
    if held.st_size < identity["start_size"] \
            or digest_prefix(held_fd, identity["start_size"]) \
            != identity["prefix_sha256"]:
        raise SystemExit("radar log initial prefix changed before measurement evidence start")
    for radar_fd_path in (proc_dir / "fd").iterdir():
        try:
            candidate = radar_fd_path.stat()
        except (FileNotFoundError, PermissionError):
            continue
        if (candidate.st_dev, candidate.st_ino) == expected_identity:
            break
    else:
        raise SystemExit("radar PID no longer has the declared log inode open")

    evidence_epoch_monotonic_ns = time.monotonic_ns()
    evidence_start = os.fstat(held_fd)
    evidence_start_size = evidence_start.st_size
    if evidence_start_size > prefix_max:
        raise SystemExit("radar log measurement prefix exceeds its resource limit")
    evidence_prefix_sha256 = digest_prefix(held_fd, evidence_start_size)
    after_hash = os.fstat(held_fd)
    if (after_hash.st_dev, after_hash.st_ino, after_hash.st_size) != (
        evidence_start.st_dev, evidence_start.st_ino, evidence_start.st_size,
    ):
        raise SystemExit("radar log changed while recording measurement evidence start")
    payload = {
        "schema_version": 1,
        "radar_pid": int(pid),
        "radar_pid_start_ticks": expected_start,
        "device": evidence_start.st_dev,
        "inode": evidence_start.st_ino,
        "radar_evidence_start_size": evidence_start_size,
        "radar_evidence_prefix_sha256": evidence_prefix_sha256,
        "radar_evidence_epoch_monotonic_ns": evidence_epoch_monotonic_ns,
    }
finally:
    os.close(held_fd)

encoded = (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()
output_fd = os.open(
    evidence_start_text,
    os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
    0o600,
)
try:
    offset = 0
    while offset < len(encoded):
        offset += os.write(output_fd, encoded[offset:])
    os.fsync(output_fd)
finally:
    os.close(output_fd)
PY
}

wait_for_radar_stop_and_flush() {
  local identity_path="$1"
  local evidence_start_path="$2"
  local delta_path="$3"
  python3 - "$RADAR_PID" "$RADAR_PID_START" "$RADAR_STOP_TIMEOUT_SEC" \
    "$RADAR_LOG" "$RADAR_LOG_FD" "$identity_path" "$evidence_start_path" \
    "$delta_path" \
    "$RADAR_DELTA_MAX_BYTES" "$EVIDENCE_LINE_MAX_BYTES" \
    "$RADAR_PREFIX_MAX_BYTES" <<'PY'
import hashlib
import json
from pathlib import Path
import math
import os
import stat
import sys
import time

(
    pid, expected_start, timeout_text, log_text, bash_fd_text,
    identity_text, evidence_start_text, delta_text, delta_max_text,
    line_max_text, prefix_max_text,
) = sys.argv[1:]
timeout = float(timeout_text)
delta_max = int(delta_max_text)
line_max = int(line_max_text)
prefix_max = int(prefix_max_text)
if not math.isfinite(timeout) or timeout <= 0:
    raise SystemExit("radar stop timeout is invalid")
if Path(identity_text).stat().st_size > line_max:
    raise SystemExit("radar identity evidence exceeds resource limit")
with open(identity_text, encoding="utf-8") as handle:
    identity = json.load(handle)
if type(identity) is not dict or set(identity) != {
    "schema_version", "radar_pid", "radar_pid_start_ticks", "device", "inode",
    "start_size", "prefix_sha256",
}:
    raise SystemExit("radar log identity evidence is invalid")
if identity["schema_version"] != 1 or identity["radar_pid"] != int(pid) \
        or identity["radar_pid_start_ticks"] != expected_start:
    raise SystemExit("radar log identity evidence disagrees with PID")
for name in ("device", "inode", "start_size"):
    if type(identity[name]) is not int or identity[name] < 0:
        raise SystemExit(f"radar log identity {name} is invalid")
if identity["start_size"] > prefix_max:
    raise SystemExit("radar log initial prefix exceeds its resource limit")
if type(identity["prefix_sha256"]) is not str or len(identity["prefix_sha256"]) != 64:
    raise SystemExit("radar log prefix hash is invalid")
evidence_start_path = Path(evidence_start_text)
if evidence_start_path.is_symlink() or not evidence_start_path.is_file() \
        or evidence_start_path.stat().st_size > line_max:
    raise SystemExit("radar measurement evidence start is invalid")
with evidence_start_path.open(encoding="utf-8") as handle:
    evidence_start = json.load(handle)
if type(evidence_start) is not dict or set(evidence_start) != {
    "schema_version", "radar_pid", "radar_pid_start_ticks", "device", "inode",
    "radar_evidence_start_size", "radar_evidence_prefix_sha256",
    "radar_evidence_epoch_monotonic_ns",
}:
    raise SystemExit("radar measurement evidence start schema is invalid")
if evidence_start["schema_version"] != 1 \
        or evidence_start["radar_pid"] != int(pid) \
        or evidence_start["radar_pid_start_ticks"] != expected_start \
        or (evidence_start["device"], evidence_start["inode"]) != (
            identity["device"], identity["inode"],
        ):
    raise SystemExit("radar measurement evidence start disagrees with identity")
for name in (
    "device", "inode", "radar_evidence_start_size",
    "radar_evidence_epoch_monotonic_ns",
):
    if type(evidence_start[name]) is not int or evidence_start[name] < 0:
        raise SystemExit(f"radar measurement evidence {name} is invalid")
if evidence_start["radar_evidence_epoch_monotonic_ns"] <= 0 \
        or evidence_start["radar_evidence_start_size"] < identity["start_size"] \
        or evidence_start["radar_evidence_start_size"] > prefix_max \
        or type(evidence_start["radar_evidence_prefix_sha256"]) is not str \
        or len(evidence_start["radar_evidence_prefix_sha256"]) != 64:
    raise SystemExit("radar measurement evidence start values are invalid")

held_proc_path = Path("/proc") / str(os.getppid()) / "fd" / bash_fd_text
held_fd = os.open(held_proc_path, os.O_RDONLY)

def prefix_sha256(size):
    digest = hashlib.sha256()
    offset = 0
    while offset < size:
        block = os.pread(
            held_fd, min(1024 * 1024, size - offset), offset
        )
        if not block:
            raise SystemExit("held radar log prefix became unreadable")
        digest.update(block)
        offset += len(block)
    return digest.hexdigest()

def require_path_identity():
    path_stat = log_path.lstat()
    if not stat.S_ISREG(path_stat.st_mode) or (
        path_stat.st_dev, path_stat.st_ino
    ) != (identity["device"], identity["inode"]):
        raise SystemExit("radar log path was rotated, renamed, or replaced")

try:
    held_stat = os.fstat(held_fd)
    if not stat.S_ISREG(held_stat.st_mode) or (
        held_stat.st_dev, held_stat.st_ino
    ) != (identity["device"], identity["inode"]):
        raise SystemExit("held radar log descriptor identity changed")

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
        current = os.fstat(held_fd)
        if (current.st_dev, current.st_ino) != (identity["device"], identity["inode"]):
            raise SystemExit("held radar log descriptor identity changed after stop")
        if current.st_size < evidence_start["radar_evidence_start_size"]:
            raise SystemExit("held radar log was truncated")
        if current.st_size == stable_size:
            stable_observations += 1
        else:
            stable_size = current.st_size
            stable_observations = 1
        if stable_observations >= 3:
            break
        time.sleep(min(0.1, deadline - time.monotonic()))
    else:
        raise SystemExit("held radar log did not become stable after process stop")

    if stable_size - evidence_start["radar_evidence_start_size"] > delta_max:
        raise SystemExit("radar log delta exceeds resource limit")

    require_path_identity()
    if prefix_sha256(identity["start_size"]) != identity["prefix_sha256"]:
        raise SystemExit("held radar log prefix was rewritten")
    if prefix_sha256(evidence_start["radar_evidence_start_size"]) \
            != evidence_start["radar_evidence_prefix_sha256"]:
        raise SystemExit("held radar measurement prefix was rewritten")

    temporary_delta = delta_text + ".tmp"
    output_fd = os.open(
        temporary_delta,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
        0o600,
    )
    try:
        offset = evidence_start["radar_evidence_start_size"]
        while offset < stable_size:
            block = os.pread(held_fd, min(1024 * 1024, stable_size - offset), offset)
            if not block:
                raise SystemExit("held radar log delta became unreadable")
            written = 0
            while written < len(block):
                written += os.write(output_fd, block[written:])
            offset += len(block)
        os.fsync(output_fd)
    finally:
        os.close(output_fd)
    try:
        final_stat = os.fstat(held_fd)
        if final_stat.st_size != stable_size:
            raise SystemExit("held radar log changed while copying evidence")
        require_path_identity()
        if prefix_sha256(identity["start_size"]) != identity["prefix_sha256"]:
            raise SystemExit("held radar log prefix was rewritten while copying evidence")
        if prefix_sha256(evidence_start["radar_evidence_start_size"]) \
                != evidence_start["radar_evidence_prefix_sha256"]:
            raise SystemExit(
                "held radar measurement prefix was rewritten while copying evidence"
            )
        os.link(temporary_delta, delta_text, follow_symlinks=False)
    finally:
        try:
            os.unlink(temporary_delta)
        except FileNotFoundError:
            pass
finally:
    os.close(held_fd)
PY
}

run_closed_loop() {
  local gain="$1"
  require_same_radar_process || die "radar process identity is not valid for closed loop"
  local stage_dir="$OUT_DIR/closed_loop"
  local radar_identity="$stage_dir/radar_log_identity.json"
  local radar_evidence_start="$stage_dir/radar_evidence_start.json"
  mkdir -- "$stage_dir"
  hold_radar_log_identity "$radar_identity" \
    || die "radar PID/log held-descriptor identity verification failed"
  audit_event "radar_log_identity_locked" "closed_loop" \
    "identity=closed_loop/radar_log_identity.json"
  if [[ "$CLOSED_LOOP_SOURCE" == "bench" ]]; then
    confirm_stage "confirmed_blue_l1_fcyqtc_transmitter" \
      "transmitter configured as confirmed BLUE/L1/fcYqTC source for RED receiver"
    audit_event "confirmed_source" "closed_loop" "bench BLUE L1 fcYqTC cmd_id=2566"
  else
    audit_event "confirmed_source" "closed_loop" \
      "replay BLUE L1 fcYqTC sha256=$CONFIRMED_L1_SHA256"
  fi
  confirm_stage "closed_loop" "ROS closed loop ($CLOSED_LOOP_SOURCE, confirmed L1)"
  local launch_log="$stage_dir/receiver.log"
  local jam_jsonl="$stage_dir/jam_codes.jsonl"
  local prelaunch_ready_file="$stage_dir/monitor.prelaunch-ready.json"
  local bound_ready_file="$stage_dir/monitor.bound-ready.json"
  local radar_delta="$stage_dir/radar.delta.log"
  local result_path="$stage_dir/result.json"
  local expected_publisher_node
  local closed_iq_max_bytes="0"
  if [[ "$CLOSED_LOOP_SOURCE" == "replay" ]]; then
    expected_publisher_node="sdr_receiver_py_wrapper_iq_jam_code"
  else
    expected_publisher_node="sdr_receiver_py_wrapper_competition"
  fi
  start_jam_collector "$jam_jsonl" "$prelaunch_ready_file" "$bound_ready_file" \
    "$CLOSED_LOOP_DURATION_SEC" "$expected_publisher_node" \
    || die "JamCode collector process group identity failed"
  wait_for_ready_file "$prelaunch_ready_file" "$COLLECTOR_PID"
  lock_radar_evidence_start "$radar_identity" "$radar_evidence_start" \
    || die "radar measurement evidence start could not be recorded"
  audit_event "radar_evidence_start_locked" "closed_loop" \
    "evidence=closed_loop/radar_evidence_start.json"

  if [[ "$CLOSED_LOOP_SOURCE" == "replay" ]]; then
    verify_l1_snapshot || die "held L1 snapshot failed verification before replay"
    start_bounded_launch "$launch_log" \
      ros2 launch sdr_receiver_py_wrapper iq_replay_jam_code.launch.py \
      iq_source_path:="$L1_SNAPSHOT_PATH" \
      iq_source_loop:=true \
      iq_source_throttle:=true \
      iq_source_sample_rate:="$SAMPLE_RATE_HZ" \
      iq_source_center_hz:=433920000 \
      initial_team:=BLUE \
      initial_target:=L1 \
      || die "closed-loop launch group identity failed"
  else
    local fallback_self_id
    if [[ "$OWN_TEAM" == "RED" ]]; then fallback_self_id=9; else fallback_self_id=109; fi
    mkdir -- "$stage_dir/iq"
    closed_iq_max_bytes="$(iq_limit_for_duration "$CLOSED_LOOP_DURATION_SEC")" \
      || die "closed-loop IQ limit calculation failed"
    ensure_window_space "$stage_dir/iq" "$closed_iq_max_bytes" \
      || die "insufficient disk space for closed-loop IQ"
    start_bounded_launch "$launch_log" \
      ros2 launch sdr_receiver_py_wrapper competition_receiver.launch.py \
      initial_rx_gain:="$gain" \
      adc_code_scale:="$ADC_CODE_SCALE" \
      record_iq:=true \
      iq_record_dir:="$stage_dir/iq" \
      iq_record_prefix:=closed_loop \
      iq_record_max_sec:="$(python3 -c 'import sys; print(float(sys.argv[1]) + 60)' "$CLOSED_LOOP_DURATION_SEC")" \
      iq_record_max_bytes:="$closed_iq_max_bytes" \
      fallback_self_id:="$fallback_self_id" \
      enable_fallback_topics:=true \
      key_retry_limit:=1 \
      || die "closed-loop launch group identity failed"
  fi
  wait_for_ready_file "$bound_ready_file" "$COLLECTOR_PID"
  if ! finish_collector; then
    stop_launch
    die "JamCode collection failed"
  fi
  local group_state=0
  launch_group_state || group_state=$?
  if ((group_state != 0)); then
    stop_launch || true
    die "closed-loop receiver exited before collection ended"
  fi
  stop_launch || die "closed-loop receiver process group did not stop cleanly"
  verify_l1_snapshot || die "held L1 snapshot failed verification after replay"

  confirm_stage "radar_stopped_log_flushed" \
    "operator declaration: radar main was cleanly stopped and its log was flushed"
  local flush_rc=0
  wait_for_radar_stop_and_flush "$radar_identity" "$radar_evidence_start" \
    "$radar_delta" \
    || flush_rc=$?
  close_radar_log_fd
  ((flush_rc == 0)) || die "radar process/log flush verification failed"

  python3 - "$stage_dir" "$OUT_DIR" "$result_path" \
    "$JAM_RAW_MAX_BYTES" "$RADAR_DELTA_MAX_BYTES" "$EVIDENCE_LINE_MAX_BYTES" \
    "$LAUNCH_LOG_MAX_BYTES" "$CLOSED_LOOP_SOURCE" "$closed_iq_max_bytes" \
    "$CONFIRMED_L1_SHA256" <<'PY'
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import sys

(
    stage_text, root_text, result_path, jam_max_text, radar_max_text,
    line_max_text, launch_max_text, source, closed_iq_max_text,
    confirmed_l1_sha256,
) = sys.argv[1:]
jam_max = int(jam_max_text)
radar_max = int(radar_max_text)
line_max = int(line_max_text)
launch_max = int(launch_max_text)
closed_iq_max = int(closed_iq_max_text)
root_input = Path(root_text)
stage_input = Path(stage_text)
if root_input.is_symlink() or not root_input.is_dir() \
        or stage_input.is_symlink() or not stage_input.is_dir():
    raise SystemExit("closed-loop evidence directories are invalid")
root = root_input.resolve(strict=True)
stage = stage_input.resolve(strict=True)
if stage.parent != root or stage.name != "closed_loop" \
        or Path(result_path).parent.resolve(strict=True) != stage \
        or Path(result_path).name != "result.json":
    raise SystemExit("closed-loop evidence root or result path is invalid")
if source not in {"bench", "replay"}:
    raise SystemExit("closed-loop source is invalid")

evidence_specs = {
    "jam_codes": ("closed_loop/jam_codes.jsonl", jam_max),
    "monitor_prelaunch_ready": (
        "closed_loop/monitor.prelaunch-ready.json", line_max,
    ),
    "monitor_bound_ready": ("closed_loop/monitor.bound-ready.json", line_max),
    "radar_log_identity": ("closed_loop/radar_log_identity.json", line_max),
    "radar_evidence_start": (
        "closed_loop/radar_evidence_start.json", line_max,
    ),
    "radar_delta": ("closed_loop/radar.delta.log", radar_max),
    "receiver_log": ("closed_loop/receiver.log", launch_max),
}
if source == "bench":
    if closed_iq_max <= 0:
        raise SystemExit("bench closed-loop IQ limit is invalid")
    iq_dir = stage / "iq"
    if iq_dir.is_symlink() or not iq_dir.is_dir():
        raise SystemExit("bench closed-loop IQ directory is invalid")
    expected_iq_names = {
        "closed_loop.c64", "closed_loop.chunks.jsonl",
        "closed_loop.events.jsonl", "closed_loop.summary.json",
    }
    entries = list(iq_dir.iterdir())
    if {entry.name for entry in entries} != expected_iq_names \
            or any(entry.is_symlink() or not entry.is_file() for entry in entries):
        raise SystemExit("bench closed-loop IQ evidence set is invalid")
    evidence_specs.update({
        "bench_iq": ("closed_loop/iq/closed_loop.c64", closed_iq_max),
        "bench_chunks": (
            "closed_loop/iq/closed_loop.chunks.jsonl", launch_max,
        ),
        "bench_events": (
            "closed_loop/iq/closed_loop.events.jsonl", launch_max,
        ),
        "bench_summary": (
            "closed_loop/iq/closed_loop.summary.json", line_max,
        ),
    })
else:
    evidence_specs["replay_source_identity"] = (
        "l1_source_identity.json", line_max,
    )

evidence_fds = {}
evidence_baselines = {}
evidence_manifest = {}

def digest_fd(fd, size):
    digest = hashlib.sha256()
    offset = 0
    while offset < size:
        block = os.pread(fd, min(1024 * 1024, size - offset), offset)
        if not block:
            raise SystemExit("closed-loop evidence became unreadable")
        digest.update(block)
        offset += len(block)
    return digest.hexdigest()

for role, (relative_text, maximum) in evidence_specs.items():
    relative = PurePosixPath(relative_text)
    if relative.is_absolute() or ".." in relative.parts or not relative.parts:
        raise SystemExit(f"closed-loop evidence path is unsafe: {role}")
    path = root.joinpath(*relative.parts)
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    info = os.fstat(fd)
    path_info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 \
            or (path_info.st_dev, path_info.st_ino) != (info.st_dev, info.st_ino) \
            or info.st_size > maximum:
        os.close(fd)
        raise SystemExit(f"closed-loop evidence identity or size is invalid: {role}")
    os.fchmod(fd, 0o400)
    os.fsync(fd)
    info = os.fstat(fd)
    digest = digest_fd(fd, info.st_size)
    after = os.fstat(fd)
    path_after = path.lstat()
    baseline = (
        info.st_dev, info.st_ino, info.st_nlink, info.st_size,
        info.st_mtime_ns, info.st_ctime_ns, digest,
    )
    if (
        after.st_dev, after.st_ino, after.st_nlink, after.st_size,
        after.st_mtime_ns, after.st_ctime_ns, digest_fd(fd, after.st_size),
    ) != baseline or (path_after.st_dev, path_after.st_ino) != (
        info.st_dev, info.st_ino,
    ):
        os.close(fd)
        raise SystemExit(f"closed-loop evidence changed while sealing: {role}")
    evidence_fds[role] = fd
    evidence_baselines[role] = baseline
    evidence_manifest[role] = {
        "path": relative.as_posix(),
        "device": info.st_dev,
        "inode": info.st_ino,
        "nlink": info.st_nlink,
        "bytes": info.st_size,
        "sha256": digest,
    }

def evidence_bytes(role):
    fd = evidence_fds[role]
    baseline = evidence_baselines[role]
    info = os.fstat(fd)
    if (
        info.st_dev, info.st_ino, info.st_nlink, info.st_size,
        info.st_mtime_ns, info.st_ctime_ns,
    ) != baseline[:6]:
        raise SystemExit(f"closed-loop evidence changed before validation: {role}")
    raw = bytearray()
    offset = 0
    while offset < info.st_size:
        block = os.pread(fd, min(1024 * 1024, info.st_size - offset), offset)
        if not block:
            raise SystemExit(f"closed-loop evidence became unreadable: {role}")
        raw.extend(block)
        offset += len(block)
    if hashlib.sha256(raw).hexdigest() != baseline[6]:
        raise SystemExit(f"closed-loop evidence hash changed: {role}")
    return bytes(raw)

def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise SystemExit(f"duplicate JSON key: {key}")
        result[key] = value
    return result

def load_evidence_json(role):
    try:
        return json.loads(
            evidence_bytes(role).decode("utf-8", errors="strict"),
            object_pairs_hook=unique_object,
            parse_constant=lambda value: (_ for _ in ()).throw(
                ValueError(f"non-finite JSON constant: {value}")
            ),
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise SystemExit(f"closed-loop JSON evidence is invalid: {role}") from exc

prelaunch_ready = load_evidence_json("monitor_prelaunch_ready")
if type(prelaunch_ready) is not dict or set(prelaunch_ready) != {
    "schema_version", "publisher_count", "collector_node_name",
    "checked_monotonic_ns",
} or type(prelaunch_ready["schema_version"]) is not int \
        or prelaunch_ready["schema_version"] != 1 \
        or type(prelaunch_ready["publisher_count"]) is not int \
        or prelaunch_ready["publisher_count"] != 0 \
        or type(prelaunch_ready["collector_node_name"]) is not str \
        or not prelaunch_ready["collector_node_name"] \
        or type(prelaunch_ready["checked_monotonic_ns"]) is not int \
        or prelaunch_ready["checked_monotonic_ns"] <= 0:
    raise SystemExit("prelaunch-ready evidence is invalid")

bound_ready = load_evidence_json("monitor_bound_ready")
if type(bound_ready) is not dict or set(bound_ready) != {
    "schema_version", "publisher_gid", "publisher_node_name",
    "publisher_node_namespace", "graph_binding_mode",
    "measurement_epoch_monotonic_ns", "measurement_epoch_wall_ns",
} or type(bound_ready["schema_version"]) is not int \
        or bound_ready["schema_version"] != 1 \
        or type(bound_ready["publisher_gid"]) is not str \
        or re.fullmatch(r"[0-9a-f]{48}", bound_ready["publisher_gid"]) is None \
        or type(bound_ready["publisher_node_name"]) is not str \
        or not bound_ready["publisher_node_name"] \
        or type(bound_ready["publisher_node_namespace"]) is not str \
        or not bound_ready["publisher_node_namespace"].startswith("/") \
        or bound_ready["graph_binding_mode"] != "exclusive_expected_node_gid" \
        or type(bound_ready["measurement_epoch_monotonic_ns"]) is not int \
        or bound_ready["measurement_epoch_monotonic_ns"] <= 0 \
        or type(bound_ready["measurement_epoch_wall_ns"]) is not int \
        or bound_ready["measurement_epoch_wall_ns"] <= 0 \
        or prelaunch_ready["checked_monotonic_ns"] \
        > bound_ready["measurement_epoch_monotonic_ns"]:
    raise SystemExit("bound-ready evidence is invalid")

radar_identity = load_evidence_json("radar_log_identity")
if type(radar_identity) is not dict or set(radar_identity) != {
    "schema_version", "radar_pid", "radar_pid_start_ticks", "device", "inode",
    "start_size", "prefix_sha256",
} or type(radar_identity["schema_version"]) is not int \
        or radar_identity["schema_version"] != 1 \
        or any(type(radar_identity[name]) is not int or radar_identity[name] < 0
               for name in ("radar_pid", "device", "inode", "start_size")) \
        or radar_identity["radar_pid"] <= 0 \
        or type(radar_identity["radar_pid_start_ticks"]) is not str \
        or not radar_identity["radar_pid_start_ticks"].isdigit() \
        or type(radar_identity["prefix_sha256"]) is not str \
        or re.fullmatch(r"[0-9a-f]{64}", radar_identity["prefix_sha256"]) is None:
    raise SystemExit("radar log identity evidence is invalid")

radar_evidence_start = load_evidence_json("radar_evidence_start")
if type(radar_evidence_start) is not dict or set(radar_evidence_start) != {
    "schema_version", "radar_pid", "radar_pid_start_ticks", "device", "inode",
    "radar_evidence_start_size", "radar_evidence_prefix_sha256",
    "radar_evidence_epoch_monotonic_ns",
} or type(radar_evidence_start["schema_version"]) is not int \
        or radar_evidence_start["schema_version"] != 1 \
        or any(type(radar_evidence_start[name]) is not int
               or radar_evidence_start[name] < 0 for name in (
                   "radar_pid", "device", "inode", "radar_evidence_start_size",
                   "radar_evidence_epoch_monotonic_ns",
               )) \
        or radar_evidence_start["radar_evidence_epoch_monotonic_ns"] <= 0 \
        or type(radar_evidence_start["radar_pid_start_ticks"]) is not str \
        or type(radar_evidence_start["radar_evidence_prefix_sha256"]) is not str \
        or re.fullmatch(
            r"[0-9a-f]{64}",
            radar_evidence_start["radar_evidence_prefix_sha256"],
        ) is None \
        or any(radar_evidence_start[name] != radar_identity[name] for name in (
            "radar_pid", "radar_pid_start_ticks", "device", "inode",
        )) \
        or radar_evidence_start["radar_evidence_start_size"] \
        < radar_identity["start_size"] \
        or radar_evidence_start["radar_evidence_epoch_monotonic_ns"] \
        > bound_ready["measurement_epoch_monotonic_ns"]:
    raise SystemExit("radar measurement-start evidence is invalid")

if source == "replay":
    replay_identity = load_evidence_json("replay_source_identity")
    if type(replay_identity) is not dict or set(replay_identity) != {
        "schema_version", "source_device", "source_inode", "snapshot_device",
        "snapshot_inode", "size", "sha256",
    } or type(replay_identity["schema_version"]) is not int \
            or replay_identity["schema_version"] != 1 \
            or any(type(replay_identity[name]) is not int or replay_identity[name] < 0
                   for name in (
                       "source_device", "source_inode", "snapshot_device",
                       "snapshot_inode", "size",
                   )) \
            or replay_identity["size"] <= 0 \
            or type(replay_identity["sha256"]) is not str \
            or replay_identity["sha256"] != confirmed_l1_sha256:
        raise SystemExit("replay source identity evidence is invalid")

records = []
try:
    jam_text = evidence_bytes("jam_codes").decode("utf-8", errors="strict")
except UnicodeDecodeError as exc:
    raise SystemExit("JamCode JSONL is not UTF-8") from exc
if not jam_text.endswith("\n") or any(not line.strip() for line in jam_text.splitlines()):
    raise SystemExit("JamCode JSONL framing is invalid")
for line_number, line in enumerate(jam_text.splitlines(), 1):
    if line_number > 100 or len(line.encode("utf-8")) > line_max:
        raise SystemExit("JamCode JSONL resource limit exceeded")
    records.append(json.loads(
        line,
        object_pairs_hook=unique_object,
        parse_constant=lambda value: (_ for _ in ()).throw(
            ValueError(f"non-finite JSON constant: {value}")
        ),
    ))
violations = []
if len(records) != 1:
    violations.append(f"jam_code_count={len(records)}")
else:
    record = records[0]
    record_fields = {
        "publisher_gid", "publisher_node_name", "publisher_node_namespace",
        "graph_binding_mode", "measurement_epoch_monotonic_ns",
        "measurement_epoch_wall_ns", "captured_monotonic_ns",
        "source_timestamp_ns", "received_timestamp_ns", "message",
    }
    integer_timestamps = (
        "measurement_epoch_monotonic_ns", "measurement_epoch_wall_ns",
        "captured_monotonic_ns", "source_timestamp_ns", "received_timestamp_ns",
    )
    if type(record) is not dict or set(record) != record_fields \
            or type(record.get("publisher_gid")) is not str \
            or not re.fullmatch(r"[0-9a-f]{48}", record["publisher_gid"]) \
            or type(record.get("publisher_node_name")) is not str \
            or not record["publisher_node_name"] \
            or type(record.get("publisher_node_namespace")) is not str \
            or not record["publisher_node_namespace"].startswith("/") \
            or record.get("graph_binding_mode") != "exclusive_expected_node_gid" \
            or any(type(record.get(field)) is not int or record[field] <= 0
                   for field in integer_timestamps) \
            or record["captured_monotonic_ns"] < record["measurement_epoch_monotonic_ns"] \
            or record["source_timestamp_ns"] < record["measurement_epoch_wall_ns"] \
            or record["received_timestamp_ns"] < record["measurement_epoch_wall_ns"] \
            or record["received_timestamp_ns"] < record["source_timestamp_ns"]:
        violations.append("jam_code_record_schema_invalid")
        message = None
    else:
        message = record["message"]
        bound_binding = {
            "publisher_gid": bound_ready["publisher_gid"],
            "publisher_node_name": bound_ready["publisher_node_name"],
            "publisher_node_namespace": bound_ready["publisher_node_namespace"],
            "graph_binding_mode": bound_ready["graph_binding_mode"],
            "measurement_epoch_monotonic_ns": (
                bound_ready["measurement_epoch_monotonic_ns"]
            ),
            "measurement_epoch_wall_ns": bound_ready["measurement_epoch_wall_ns"],
        }
        if any(type(record.get(name)) is not type(value)
               or record.get(name) != value
               for name, value in bound_binding.items()):
            violations.append("jam_code_bound_ready_mismatch")
    if type(message) is not dict:
        violations.append("jam_code_not_mapping")
    else:
        expected_common = {
            "valid": True,
            "command_id": 2566,
            "level": 1,
            "team": "BLUE",
            "target": "JAM_L1_KEY",
            "ascii_code": "fcYqTC",
            "key": [102, 99, 89, 113, 84, 67],
        }
        source_fields = {"radio_mode", "rf_state", "radar_info_raw", "key_mutable"}
        if set(message) != set(expected_common) | source_fields | {"header"}:
            violations.append("jam_code_schema_mismatch")
        header = message.get("header")
        stamp = header.get("stamp") if type(header) is dict else None
        if type(header) is not dict or set(header) != {"stamp", "frame_id"} \
                or header.get("frame_id") != "" or type(stamp) is not dict \
                or set(stamp) != {"sec", "nanosec"} \
                or type(stamp.get("sec")) is not int or stamp["sec"] < 0 \
                or type(stamp.get("nanosec")) is not int \
                or not 0 <= stamp["nanosec"] < 1_000_000_000:
            violations.append("jam_code_header_mismatch")
        for field, value in expected_common.items():
            actual = message.get(field)
            if field == "key":
                matches = type(actual) is list and len(actual) == len(value) \
                    and all(type(item) is int for item in actual) and actual == value
            else:
                matches = type(actual) is type(value) and actual == value
            if not matches:
                violations.append(f"jam_code_{field}_mismatch")
        rf_state = message.get("rf_state")
        legal_rf_states = {
            "INIT", "SATURATED", "RF_LOW", "CRC_LOCKED", "DSP_MARGINAL",
            "SEARCHING",
        }
        if type(rf_state) is not str or rf_state not in legal_rf_states:
            violations.append("jam_code_rf_state_mismatch")
        radio_mode = message.get("radio_mode")
        radar_info_raw = message.get("radar_info_raw")
        key_mutable = message.get("key_mutable")
        if source == "replay":
            if type(radio_mode) is not str or radio_mode != "debug":
                violations.append("jam_code_radio_mode_mismatch")
            if type(radar_info_raw) is not int or not 0 <= radar_info_raw <= 0xFF:
                violations.append("jam_code_radar_info_raw_mismatch")
            elif radar_info_raw == 0:
                if type(key_mutable) is not bool or key_mutable is not False:
                    violations.append("jam_code_key_mutable_mismatch")
            elif ((radar_info_raw >> 3) & 0x3) != 1:
                violations.append("jam_code_radar_level_context_mismatch")
            elif type(key_mutable) is not bool \
                    or key_mutable is not bool(radar_info_raw & 0x20):
                violations.append("jam_code_key_mutable_mismatch")
        elif source == "bench":
            if type(radio_mode) is not str or radio_mode != "competition":
                violations.append("jam_code_radio_mode_mismatch")
            if type(radar_info_raw) is not int or not 0 <= radar_info_raw <= 0xFF:
                violations.append("jam_code_radar_info_raw_mismatch")
            else:
                if ((radar_info_raw >> 3) & 0x3) != 1:
                    violations.append("jam_code_radar_level_context_mismatch")
                if (radar_info_raw & 0x20) == 0:
                    violations.append("jam_code_radar_mutability_context_mismatch")
            if type(key_mutable) is not bool or key_mutable is not True:
                violations.append("jam_code_key_mutable_mismatch")
        else:
            raise SystemExit("closed-loop source is invalid")

radar_text = evidence_bytes("radar_delta").decode("utf-8", errors="replace")
patterns = [
    (
        "callback",
        re.compile(
            r"Received JamCode[^\n]*command_id:\s*(?:0x0*A06|0x2566|2566)(?![0-9A-Fa-f])",
            re.I,
        ),
    ),
    ("ascii_key", re.compile(r"ASCII Key:\s*\[fcYqTC\]")),
    ("stored", re.compile(r"Stored password:")),
    ("phase2", re.compile(r"key phase 2 start")),
    ("sent", re.compile(r"key has send")),
]
first_offsets = {}
offset = 0
for line in radar_text.splitlines(keepends=True):
    if len(line.encode("utf-8")) > line_max:
        raise SystemExit("radar delta line exceeds resource limit")
    for name, pattern in patterns:
        if name in first_offsets:
            continue
        match = pattern.search(line)
        if match is not None:
            first_offsets[name] = offset + match.start()
    offset += len(line)
for name, _pattern in patterns:
    if name not in first_offsets:
        violations.append(f"radar_{name}_missing")
if len(first_offsets) == len(patterns):
    ordered_offsets = [first_offsets[name] for name, _pattern in patterns]
    if any(left >= right for left, right in zip(ordered_offsets, ordered_offsets[1:])):
        violations.append("radar_evidence_out_of_order")

for role, fd in evidence_fds.items():
    baseline = evidence_baselines[role]
    info = os.fstat(fd)
    relative = PurePosixPath(evidence_manifest[role]["path"])
    path_info = root.joinpath(*relative.parts).lstat()
    if (
        info.st_dev, info.st_ino, info.st_nlink, info.st_size,
        info.st_mtime_ns, info.st_ctime_ns, digest_fd(fd, info.st_size),
    ) != baseline or (path_info.st_dev, path_info.st_ino) != (
        info.st_dev, info.st_ino,
    ):
        raise SystemExit(f"closed-loop evidence changed during validation: {role}")

result = {
    "schema_version": 2,
    "source": source,
    "expected_key": "fcYqTC",
    "jam_code_count": len(records),
    "radar_callback_stored_key": not any(v.startswith("radar_callback") or v.startswith("radar_ascii") or v.startswith("radar_stored") for v in violations),
    "radar_entered_phase2": "radar_phase2_missing" not in violations and "radar_sent_missing" not in violations,
    "evidence_manifest": evidence_manifest,
    "violations": violations,
    "passed": not violations,
}
encoded = (json.dumps(
    result, ensure_ascii=False, indent=2, sort_keys=True,
) + "\n").encode()
result_fd = os.open(
    result_path,
    os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
    0o400,
)
try:
    offset = 0
    while offset < len(encoded):
        offset += os.write(result_fd, encoded[offset:])
    os.fsync(result_fd)
finally:
    os.close(result_fd)
for fd in evidence_fds.values():
    os.close(fd)
if violations:
    raise SystemExit("closed-loop validation failed: " + ",".join(violations))
PY
  audit_event "closed_loop_complete" "closed_loop" "result=$result_path"
}

write_final_summary() {
  local final_gain="$1"
  python3 - "$OUT_DIR/acceptance_summary.json" "$RESULTS_JSONL" \
    "$OUT_DIR/closed_loop/result.json" "$RUN_ELIGIBLE" "$final_gain" \
    "$RESULTS_IDENTITY" <<'PY'
import json
import os
from pathlib import Path
import stat
import sys
(
    summary_path, results_path, closed_path, eligible_text, final_gain,
    results_identity,
) = sys.argv[1:]

def reject_constant(value):
    raise ValueError(f"non-finite JSON constant: {value}")

def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result

def read_private(path, expected_identity=None):
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    try:
        info = os.fstat(fd)
        path_info = Path(path).lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 \
                or (path_info.st_dev, path_info.st_ino) != (info.st_dev, info.st_ino):
            raise SystemExit("final summary input identity is invalid")
        if expected_identity is not None and (info.st_dev, info.st_ino) != expected_identity:
            raise SystemExit("results stream changed from its initial inode")
        blocks = []
        while True:
            block = os.read(fd, 1024 * 1024)
            if not block:
                break
            blocks.append(block)
        return b"".join(blocks).decode("utf-8", errors="strict")
    finally:
        os.close(fd)

expected_results = tuple(int(value) for value in results_identity.split(":"))
results_text = read_private(results_path, expected_results)
results = [
    json.loads(
        line, parse_constant=reject_constant, object_pairs_hook=unique_object,
    )
    for line in results_text.splitlines() if line.strip()
]
closed_loop = json.loads(
    read_private(closed_path),
    parse_constant=reject_constant,
    object_pairs_hook=unique_object,
)
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

write_completion_marker() {
  python3 - "$OUT_DIR" "$RUN_ELIGIBLE" "$RESULTS_IDENTITY" \
    "$AUDIT_IDENTITY" "$TIMEOUT_CLASSIFIER_PY" <<'PY'
import datetime
import ctypes
import hashlib
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
import stat
import struct
import sys

root_input = Path(sys.argv[1])
if root_input.is_symlink() or not root_input.is_dir():
    raise SystemExit("completion root must be a non-symlink directory")
root = root_input.resolve(strict=True)
eligible = sys.argv[2] == "true"
expected_results = tuple(int(value) for value in sys.argv[3].split(":"))
expected_audit = tuple(int(value) for value in sys.argv[4].split(":"))
exec(sys.argv[5], globals())
hashes = {}
cached = {}
line_counts = {}
directory_paths = {""}
dir_fd = os.open(root, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
try:
    root_info = os.fstat(dir_fd)
    input_info = root_input.lstat()
    if (root_info.st_dev, root_info.st_ino) != (input_info.st_dev, input_info.st_ino):
        raise SystemExit("completion root identity changed")

    def require_initial_stream_identity(name, expected):
        fd = os.open(
            name, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0), dir_fd=dir_fd,
        )
        try:
            info = os.fstat(fd)
            path_info = os.stat(name, dir_fd=dir_fd, follow_symlinks=False)
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 \
                    or (info.st_dev, info.st_ino) != expected \
                    or (path_info.st_dev, path_info.st_ino) != expected:
                raise SystemExit(f"{name[:-6]} stream no longer has its initial inode")
        finally:
            os.close(fd)

    require_initial_stream_identity("results.jsonl", expected_results)
    require_initial_stream_identity("audit.jsonl", expected_audit)

    def hash_file(parent_fd, name, relative_path):
        fd = os.open(
            name, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0), dir_fd=parent_fd,
        )
        try:
            info = os.fstat(fd)
            path_info = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 \
                    or (path_info.st_dev, path_info.st_ino) != (
                        info.st_dev, info.st_ino
                    ):
                raise SystemExit(
                    f"completion artifact is not a private regular file: {relative_path}"
                )
            os.fchmod(fd, 0o400)
            os.fsync(fd)
            info = os.fstat(fd)
            digest = hashlib.sha256()
            count_lines = relative_path.endswith(".jsonl")
            newline_count = 0
            last_byte = b""
            keep = relative_path in {
                "results.jsonl", "audit.jsonl", "acceptance_summary.json",
                "run_metadata.json", "closed_loop/result.json",
            } or relative_path.endswith("/metrics.json")
            blocks = [] if keep else None
            while True:
                block = os.read(fd, 1024 * 1024)
                if not block:
                    break
                digest.update(block)
                if count_lines:
                    newline_count += block.count(b"\n")
                    last_byte = block[-1:]
                if blocks is not None:
                    blocks.append(block)
            if count_lines:
                if info.st_size and last_byte != b"\n":
                    raise SystemExit(
                        f"completion JSONL is not newline terminated: {relative_path}"
                    )
                line_counts[relative_path] = newline_count
            if blocks is not None:
                cached[relative_path] = b"".join(blocks)
            after = os.fstat(fd)
            path_after = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
            if (
                after.st_dev, after.st_ino, after.st_size,
                after.st_mtime_ns, after.st_ctime_ns,
            ) != (
                info.st_dev, info.st_ino, info.st_size,
                info.st_mtime_ns, info.st_ctime_ns,
            ) or (path_after.st_dev, path_after.st_ino) != (
                info.st_dev, info.st_ino
            ):
                raise SystemExit(f"completion artifact changed while hashing: {relative_path}")
            hashes[relative_path] = {
                "device": info.st_dev,
                "inode": info.st_ino,
                "nlink": info.st_nlink,
                "bytes": info.st_size,
                "sha256": digest.hexdigest(),
            }
        finally:
            os.close(fd)

    def walk_directory(current_fd, prefix=""):
        for name in sorted(os.listdir(current_fd)):
            relative = f"{prefix}/{name}" if prefix else name
            if relative in {"completion.json", ".completion.private"}:
                raise SystemExit("completion marker already exists")
            info = os.stat(name, dir_fd=current_fd, follow_symlinks=False)
            if stat.S_ISDIR(info.st_mode):
                directory_paths.add(relative)
                child_fd = os.open(
                    name,
                    os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
                    | getattr(os, "O_NOFOLLOW", 0),
                    dir_fd=current_fd,
                )
                try:
                    child_info = os.fstat(child_fd)
                    if (child_info.st_dev, child_info.st_ino) != (
                        info.st_dev, info.st_ino
                    ):
                        raise SystemExit(f"evidence directory identity changed: {relative}")
                    walk_directory(child_fd, relative)
                    os.fsync(child_fd)
                finally:
                    os.close(child_fd)
            elif stat.S_ISREG(info.st_mode):
                hash_file(current_fd, name, relative)
            else:
                raise SystemExit(f"unsupported evidence entry: {relative}")

    walk_directory(dir_fd)
    for required in (
        "results.jsonl", "audit.jsonl", "acceptance_summary.json",
        "run_metadata.json", "closed_loop/jam_codes.jsonl",
        "closed_loop/monitor.prelaunch-ready.json",
        "closed_loop/monitor.bound-ready.json",
        "closed_loop/radar_evidence_start.json", "closed_loop/radar.delta.log",
        "closed_loop/radar_log_identity.json", "closed_loop/receiver.log",
        "closed_loop/result.json",
    ):
        if required not in hashes:
            raise SystemExit(f"required completion artifact is missing: {required}")
    if (hashes["results.jsonl"]["device"], hashes["results.jsonl"]["inode"]) \
            != expected_results:
        raise SystemExit("results stream no longer has its initial inode")
    if (hashes["audit.jsonl"]["device"], hashes["audit.jsonl"]["inode"]) \
            != expected_audit:
        raise SystemExit("audit stream no longer has its initial inode")

    def reject_constant(value):
        raise ValueError(f"non-finite JSON constant: {value}")

    def unique_object(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"duplicate JSON key: {key}")
            result[key] = value
        return result

    def load_cached(path):
        try:
            return json.loads(
                cached[path].decode("utf-8", errors="strict"),
                parse_constant=reject_constant,
                object_pairs_hook=unique_object,
            )
        except (KeyError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
            raise SystemExit(f"completion JSON is invalid: {path}") from exc

    def load_cached_jsonl(path, description):
        try:
            return [
                json.loads(
                    line,
                    parse_constant=reject_constant,
                    object_pairs_hook=unique_object,
                )
                for line in cached[path].decode("utf-8", errors="strict").splitlines()
                if line.strip()
            ]
        except (KeyError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
            raise SystemExit(f"{description} is invalid at completion") from exc

    def read_current_bytes(path, description, maximum):
        expected = hashes.get(path)
        relative = PurePosixPath(path)
        if expected is None or relative.is_absolute() or ".." in relative.parts \
                or not relative.parts:
            raise SystemExit(f"{description} is absent from the completion manifest")
        native_path = root.joinpath(*relative.parts)
        fd = os.open(native_path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        try:
            before = os.fstat(fd)
            if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 \
                    or (before.st_dev, before.st_ino, before.st_size) != (
                        expected["device"], expected["inode"], expected["bytes"],
                    ) or before.st_size > maximum:
                raise SystemExit(f"{description} identity or size is invalid")
            digest = hashlib.sha256()
            blocks = []
            while True:
                block = os.read(fd, 1024 * 1024)
                if not block:
                    break
                digest.update(block)
                blocks.append(block)
            after = os.fstat(fd)
            path_after = native_path.lstat()
            if (after.st_dev, after.st_ino, after.st_size) != (
                    before.st_dev, before.st_ino, before.st_size,
            ) or (path_after.st_dev, path_after.st_ino) != (
                    before.st_dev, before.st_ino,
            ) or digest.hexdigest() != expected["sha256"]:
                raise SystemExit(f"{description} changed after completion hashing")
            return b"".join(blocks)
        finally:
            os.close(fd)

    def load_current_json(path, description, maximum=1024 * 1024):
        try:
            return json.loads(
                read_current_bytes(path, description, maximum).decode(
                    "utf-8", errors="strict",
                ),
                parse_constant=reject_constant,
                object_pairs_hook=unique_object,
            )
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
            raise SystemExit(f"{description} is invalid at completion") from exc

    def load_current_jsonl(path, description, maximum):
        try:
            text = read_current_bytes(path, description, maximum).decode(
                "utf-8", errors="strict",
            )
            if not text.endswith("\n"):
                raise ValueError("JSONL stream is not newline terminated")
            lines = text.splitlines()
            if not lines or any(not line.strip() for line in lines):
                raise ValueError("JSONL stream contains an empty record")
            return [
                json.loads(
                    line,
                    parse_constant=reject_constant,
                    object_pairs_hook=unique_object,
                )
                for line in lines
            ]
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
            raise SystemExit(f"{description} is invalid at completion") from exc

    def exact_json_equal(left, right):
        if type(left) is not type(right):
            return False
        if type(left) is dict:
            return set(left) == set(right) and all(
                exact_json_equal(left[key], right[key]) for key in left
            )
        if type(left) is list:
            return len(left) == len(right) and all(
                exact_json_equal(left_item, right_item)
                for left_item, right_item in zip(left, right)
            )
        return left == right

    production_metrics_schema = {
        "schema_version", "stage", "combination", "gain_db", "sample_rate_hz",
        "adc_code_scale", "rf_clipping_threshold", "requested_duration_sec",
        "event_kind_counts", "status_period_sec", "status_scheduling_margin_sec",
        "status_tolerance_sec", "max_chunk_period_sec",
        "chunk_scheduling_margin_sec", "chunk_tolerance_sec",
        "window_coverage_sec", "status_coverage_sec", "chunk_coverage_sec",
        "status_head_missing_sec", "status_tail_missing_sec",
        "status_early_extra_sec", "status_late_extra_sec",
        "chunk_head_missing_sec", "chunk_tail_missing_sec",
        "chunk_early_extra_sec", "chunk_late_extra_sec", "status_messages",
        "chunk_count", "actual_samples", "expected_samples", "peak", "rms",
        "rms_min", "rms_max", "rms_aggregation", "clipping_ratio", "rf_state",
        "observed_rf_states", "crc16_count",
        "crc_valid_primary_command_event_ids", "accepted_command_event_ids",
        "out_of_window_command_event_ids", "event_processing_lag_sec",
        "command_evidence_violations", "acquisition_duty", "queue_drops",
        "libiio_timeouts", "acquisition_read_errors", "acquisition_reconnects",
        "device_read_errors", "device_reconnects", "device_connection_errors",
        "recorder_dropped_chunks", "recorder_dropped_events",
        "stability_thresholds_enforced", "recorder_artifacts", "violations",
        "passed",
    }
    metric_integer_fields = {
        "gain_db", "sample_rate_hz", "status_messages", "chunk_count",
        "actual_samples", "crc16_count", "queue_drops", "libiio_timeouts",
        "acquisition_read_errors", "acquisition_reconnects",
        "device_read_errors", "device_reconnects", "device_connection_errors",
        "recorder_dropped_chunks", "recorder_dropped_events",
    }
    metric_float_fields = {
        "adc_code_scale", "rf_clipping_threshold", "requested_duration_sec",
        "status_period_sec", "status_scheduling_margin_sec",
        "status_tolerance_sec", "max_chunk_period_sec",
        "chunk_scheduling_margin_sec", "chunk_tolerance_sec",
        "window_coverage_sec", "status_coverage_sec", "chunk_coverage_sec",
        "status_head_missing_sec", "status_tail_missing_sec",
        "status_early_extra_sec", "status_late_extra_sec",
        "chunk_head_missing_sec", "chunk_tail_missing_sec",
        "chunk_early_extra_sec", "chunk_late_extra_sec", "expected_samples",
        "peak", "rms", "rms_min", "rms_max", "clipping_ratio",
        "event_processing_lag_sec", "acquisition_duty",
    }
    allowed_event_kinds = {
        "rf_state", "command", "validation", "decoder_reset",
        "decoder_reset_error", "decoder_error", "output_error",
        "discontinuity", "recording_stopped",
    }

    def close_float(left, right, tolerance=1e-9):
        return math.isclose(left, right, rel_tol=1e-12, abs_tol=tolerance)

    def validate_production_metrics(metrics, metadata, thresholds, description):
        if type(metrics) is not dict or set(metrics) != production_metrics_schema:
            raise SystemExit(f"{description} does not use the exact production metrics schema")
        if type(metrics["schema_version"]) is not int \
                or metrics["schema_version"] != 1:
            raise SystemExit(f"{description} schema version is invalid")
        if type(metrics["stage"]) is not str or not metrics["stage"] \
                or "/" in metrics["stage"] or metrics["stage"] in {".", ".."} \
                or type(metrics["combination"]) is not str \
                or not metrics["combination"]:
            raise SystemExit(f"{description} string identity is invalid")
        for name in metric_integer_fields:
            if type(metrics[name]) is not int or metrics[name] < 0:
                raise SystemExit(f"{description} integer field is invalid: {name}")
        for name in metric_float_fields:
            if type(metrics[name]) is not float or not math.isfinite(metrics[name]):
                raise SystemExit(f"{description} float field is invalid: {name}")
        if any(metrics[name] <= 0 for name in (
            "requested_duration_sec", "status_period_sec", "status_tolerance_sec",
            "max_chunk_period_sec", "chunk_tolerance_sec", "window_coverage_sec",
            "expected_samples", "adc_code_scale",
        )) or any(metrics[name] < 0 for name in (
            "status_scheduling_margin_sec", "chunk_scheduling_margin_sec",
            "status_coverage_sec", "chunk_coverage_sec", "status_head_missing_sec",
            "status_tail_missing_sec", "status_early_extra_sec",
            "status_late_extra_sec", "chunk_head_missing_sec",
            "chunk_tail_missing_sec", "chunk_early_extra_sec",
            "chunk_late_extra_sec", "peak", "rms", "rms_min", "rms_max",
            "event_processing_lag_sec",
        )):
            raise SystemExit(f"{description} numeric range is invalid")
        if metrics["status_messages"] <= 0 or metrics["chunk_count"] <= 0 \
                or metrics["actual_samples"] <= 0 \
                or not 0 <= metrics["clipping_ratio"] <= 1 \
                or not 0 <= metrics["rf_clipping_threshold"] <= 1 \
                or not 0 < metrics["acquisition_duty"] <= 1 \
                or not metrics["rms_min"] <= metrics["rms"] \
                <= metrics["rms_max"] <= metrics["peak"]:
            raise SystemExit(f"{description} aggregate range is invalid")
        if metrics["sample_rate_hz"] != 2_000_000 \
                or metrics["sample_rate_hz"] != metadata["sample_rate_hz"] \
                or metrics["adc_code_scale"] != 2048.0 \
                or metrics["rf_clipping_threshold"] \
                != thresholds["rf_clipping_ratio"]:
            raise SystemExit(f"{description} receiver configuration is invalid")
        if metrics["status_period_sec"] != 1.0 \
                or metrics["status_scheduling_margin_sec"] != 0.25 \
                or metrics["status_tolerance_sec"] != 1.25 \
                or metrics["chunk_scheduling_margin_sec"] != 0.002 \
                or not close_float(
                    metrics["chunk_tolerance_sec"],
                    min(
                        0.1,
                        metrics["max_chunk_period_sec"] * 0.5 + 0.002,
                        metrics["max_chunk_period_sec"] * 0.9,
                    ),
                ) or metrics["event_processing_lag_sec"] \
                != metrics["status_tolerance_sec"]:
            raise SystemExit(f"{description} collection tolerance is invalid")
        if metrics["window_coverage_sec"] + 1e-9 \
                < metrics["requested_duration_sec"] \
                or metrics["status_coverage_sec"] + 1e-9 < max(
                    0.0,
                    metrics["requested_duration_sec"] - metrics["status_tolerance_sec"],
                ) or metrics["chunk_coverage_sec"] + 1e-9 < max(
                    0.0,
                    metrics["requested_duration_sec"] - metrics["chunk_tolerance_sec"],
                ) or metrics["status_head_missing_sec"] \
                > metrics["status_tolerance_sec"] + 1e-9 \
                or metrics["status_tail_missing_sec"] \
                > metrics["status_tolerance_sec"] + 1e-9 \
                or metrics["chunk_head_missing_sec"] \
                > metrics["chunk_tolerance_sec"] + 1e-9 \
                or metrics["chunk_tail_missing_sec"] \
                > metrics["chunk_tolerance_sec"] + 1e-9:
            raise SystemExit(f"{description} coverage is not eligible")
        expected_duty = metrics["actual_samples"] / metrics["expected_samples"]
        if not close_float(metrics["acquisition_duty"], expected_duty) \
                or metrics["acquisition_duty"] \
                < thresholds["minimum_acquisition_duty"]:
            raise SystemExit(f"{description} acquisition duty is inconsistent")
        if type(metrics["event_kind_counts"]) is not dict \
                or not metrics["event_kind_counts"] \
                or not set(metrics["event_kind_counts"]) <= allowed_event_kinds \
                or any(
                    type(name) is not str or type(count) is not int or count <= 0
                    for name, count in metrics["event_kind_counts"].items()
                ) or metrics["event_kind_counts"].get("rf_state") \
                != metrics["chunk_count"]:
            raise SystemExit(f"{description} event counts are invalid")
        states = metrics["observed_rf_states"]
        if type(states) is not list or len(states) != metrics["chunk_count"] \
                or any(type(state) is not str or state not in {
                    "linear", "clipped", "too_strong", "too_weak", "disconnected",
                } for state in states) \
                or metrics["rf_state"] not in {"linear", "clipped"} \
                or (metrics["rf_state"] == "clipped") != ("clipped" in states) \
                or (metrics["rf_state"] == "clipped") \
                != (metrics["clipping_ratio"] >= metrics["rf_clipping_threshold"]):
            raise SystemExit(f"{description} RF state is invalid")
        for name in (
            "crc_valid_primary_command_event_ids", "accepted_command_event_ids",
            "out_of_window_command_event_ids", "command_evidence_violations",
            "violations",
        ):
            if type(metrics[name]) is not list:
                raise SystemExit(f"{description} list field is invalid: {name}")
        for name in (
            "crc_valid_primary_command_event_ids", "accepted_command_event_ids",
            "out_of_window_command_event_ids",
        ):
            values = metrics[name]
            if any(type(value) is not str or not value or len(value) > 128
                   for value in values) or len(values) != len(set(values)):
                raise SystemExit(f"{description} event-id list is invalid: {name}")
        crc_ids = metrics["crc_valid_primary_command_event_ids"]
        accepted_ids = metrics["accepted_command_event_ids"]
        outside_ids = metrics["out_of_window_command_event_ids"]
        if metrics["crc16_count"] != len(crc_ids) \
                or set(crc_ids) != set(accepted_ids) \
                or set(accepted_ids) & set(outside_ids) \
                or metrics["command_evidence_violations"] != [] \
                or metrics["violations"] != []:
            raise SystemExit(f"{description} command evidence is invalid")
        zero_fields = metric_integer_fields - {
            "gain_db", "sample_rate_hz", "status_messages", "chunk_count",
            "actual_samples", "crc16_count",
        }
        if any(metrics[name] != 0 for name in zero_fields):
            raise SystemExit(f"{description} contains a nonzero failure counter")
        if type(metrics["stability_thresholds_enforced"]) is not bool \
                or type(metrics["passed"]) is not bool \
                or metrics["passed"] is not True \
                or metrics["rms_aggregation"] \
                != "sample_count_weighted_root_mean_square" \
                or type(metrics["recorder_artifacts"]) is not dict:
            raise SystemExit(f"{description} terminal fields are invalid")
        if metrics["stability_thresholds_enforced"] and (
            metrics["rf_state"] != "linear"
            or metrics["crc16_count"] < thresholds["minimum_crc16_count"]
        ):
            raise SystemExit(f"{description} stability evidence is invalid")

    def validate_status_evidence(stage, metrics, events_relative_path):
        path = f"{stage}/status.jsonl"
        records = load_current_jsonl(
            path, f"{stage} normalized status", 8 * 1024 * 1024,
        )
        if len(records) != metrics["status_messages"] + 1 \
                or line_counts.get(path) != len(records):
            raise SystemExit(f"{stage} status record count is inconsistent")
        bounds = records[0]
        if type(bounds) is not dict or set(bounds) != {
            "record_type", "requested_duration_sec", "window_start_monotonic_ns",
            "window_end_monotonic_ns",
        } or bounds["record_type"] != "window_bounds" \
                or type(bounds["requested_duration_sec"]) is not float \
                or bounds["requested_duration_sec"] \
                != metrics["requested_duration_sec"] \
                or type(bounds["window_start_monotonic_ns"]) is not int \
                or type(bounds["window_end_monotonic_ns"]) is not int \
                or bounds["window_start_monotonic_ns"] <= 0 \
                or bounds["window_end_monotonic_ns"] \
                <= bounds["window_start_monotonic_ns"]:
            raise SystemExit(f"{stage} status window bounds are invalid")
        start = bounds["window_start_monotonic_ns"]
        end = bounds["window_end_monotonic_ns"]
        captured = []
        previous_samples = -1
        expected_events_path = root.joinpath(stage, *PurePosixPath(
            events_relative_path,
        ).parts).resolve(strict=True)
        for record in records[1:]:
            if type(record) is not dict or set(record) != {
                "captured_monotonic_ns", "record_type", "status",
            } or record["record_type"] != "status_snapshot" \
                    or type(record["captured_monotonic_ns"]) is not int \
                    or record["captured_monotonic_ns"] <= 0 \
                    or type(record["status"]) is not dict:
                raise SystemExit(f"{stage} status snapshot schema is invalid")
            timestamp = record["captured_monotonic_ns"]
            if captured and timestamp <= captured[-1]:
                raise SystemExit(f"{stage} status timestamps are not increasing")
            captured.append(timestamp)
            runtime = record["status"].get("common_runtime")
            if type(runtime) is not dict \
                    or runtime.get("worker_error") is not None \
                    or runtime.get("cleanup_error") is not None \
                    or runtime.get("rf_state") not in {
                        "linear", "clipped", "too_strong", "too_weak", "disconnected",
                    }:
                raise SystemExit(f"{stage} common runtime status is invalid")
            acquisition = runtime.get("acquisition")
            device = runtime.get("device")
            recorder = runtime.get("recorder")
            if type(acquisition) is not dict or type(device) is not dict \
                    or type(recorder) is not dict \
                    or recorder.get("enabled") is not True:
                raise SystemExit(f"{stage} status counter groups are invalid")
            stats = recorder.get("stats")
            paths = recorder.get("paths")
            counters = (
                acquisition.get("queue_drops"), acquisition.get("read_errors"),
                acquisition.get("reconnects"), device.get("read_errors"),
                device.get("reconnects"), device.get("connection_errors"),
                stats.get("dropped_chunks") if type(stats) is dict else None,
                stats.get("dropped_events") if type(stats) is dict else None,
            )
            if type(stats) is not dict or stats.get("worker_error") is not None \
                    or any(type(value) is not int or value != 0 for value in counters) \
                    or type(paths) is not dict \
                    or type(paths.get("events_path")) is not str:
                raise SystemExit(f"{stage} status reports a receiver failure")
            try:
                observed_events_path = Path(paths["events_path"]).resolve(strict=True)
            except (OSError, RuntimeError) as exc:
                raise SystemExit(f"{stage} status recorder path is invalid") from exc
            if observed_events_path != expected_events_path:
                raise SystemExit(f"{stage} status recorder path is inconsistent")
            samples_written = stats.get("samples_written")
            if type(samples_written) is not int or samples_written < previous_samples:
                raise SystemExit(f"{stage} status sample counter is invalid")
            previous_samples = samples_written
        status_start = captured[0]
        status_end = captured[-1]
        expected_values = {
            "window_coverage_sec": (end - start) / 1_000_000_000,
            "status_coverage_sec": max(
                0.0, (min(status_end, end) - max(status_start, start))
                / 1_000_000_000,
            ),
            "status_head_missing_sec": max(
                0.0, (status_start - start) / 1_000_000_000,
            ),
            "status_tail_missing_sec": max(
                0.0, (end - status_end) / 1_000_000_000,
            ),
            "status_early_extra_sec": max(
                0.0, (start - status_start) / 1_000_000_000,
            ),
            "status_late_extra_sec": max(
                0.0, (status_end - end) / 1_000_000_000,
            ),
        }
        if any(not close_float(metrics[name], value)
               for name, value in expected_values.items()):
            raise SystemExit(f"{stage} status coverage disagrees with metrics")

    def validate_launch_evidence(stage, metrics):
        raw = read_current_bytes(
            f"{stage}/launch.log", f"{stage} launch log", 64 * 1024 * 1024,
        )
        lines = []
        for line in raw.decode("utf-8", errors="replace").splitlines():
            if len(line.encode("utf-8")) > 1024 * 1024:
                raise SystemExit(f"{stage} launch log line exceeds its limit")
            lines.append(line)
        timeout_count = count_libiio_timeout_lines(lines)
        if timeout_count != metrics["libiio_timeouts"] or timeout_count != 0:
            raise SystemExit(f"{stage} launch log reports a libiio timeout")

    def contains_metadata_error(value):
        if type(value) is dict:
            return "metadata_error" in value or any(
                contains_metadata_error(child) for child in value.values()
            )
        if type(value) is list:
            return any(contains_metadata_error(child) for child in value)
        return False

    def validate_recorder_summary(stage, metrics, role_paths):
        summary_path = f"{stage}/{role_paths['summary']}"
        summary_record = load_current_json(
            summary_path, f"{stage} recorder summary",
        )
        if type(summary_record) is not dict or contains_metadata_error(summary_record):
            raise SystemExit(f"{stage} recorder summary is invalid")
        expected_files = {
            "iq": PurePosixPath(role_paths["iq"]).name,
            "chunks": PurePosixPath(role_paths["chunks"]).name,
            "events": PurePosixPath(role_paths["events"]).name,
            "summary": PurePosixPath(role_paths["summary"]).name,
        }
        integer_values = {
            "chunks_written": metrics["chunk_count"],
            "events_written": sum(metrics["event_kind_counts"].values()),
            "samples_written": metrics["actual_samples"],
            "bytes_written": metrics["actual_samples"] * 8,
            "dropped_chunks": 0,
            "dropped_events": 0,
            "queue_overflows": 0,
            "sample_rate": metrics["sample_rate_hz"],
            "rx_gain": metrics["gain_db"],
        }
        if summary_record.get("files") != expected_files \
                or any(type(summary_record.get(name)) is not int
                       or summary_record.get(name) != value
                       for name, value in integer_values.items()) \
                or summary_record.get("runtime") != "common_competition" \
                or summary_record.get("run_mode") != "competition" \
                or summary_record.get("stopped_reason") \
                != "common receiver stopped" \
                or type(summary_record.get("adc_code_scale")) is not float \
                or summary_record["adc_code_scale"] != metrics["adc_code_scale"] \
                or type(summary_record.get("rf_clipping_ratio")) is not float \
                or summary_record["rf_clipping_ratio"] \
                != metrics["rf_clipping_threshold"] \
                or summary_record.get("dropped_chunk_range") is not None \
                or summary_record.get("dropped_chunk_ranges") != [] \
                or summary_record.get("dropped_chunk_ranges_overflow") is not None \
                or summary_record.get("dropped_event_kinds") != {}:
            raise SystemExit(f"{stage} recorder summary disagrees with metrics")
        chunks_path = f"{stage}/{role_paths['chunks']}"
        events_path = f"{stage}/{role_paths['events']}"
        iq_path = f"{stage}/{role_paths['iq']}"
        if line_counts.get(chunks_path) != metrics["chunk_count"] \
                or line_counts.get(events_path) \
                != sum(metrics["event_kind_counts"].values()) \
                or hashes[iq_path]["bytes"] != metrics["actual_samples"] * 8:
            raise SystemExit(f"{stage} recorder file counts disagree with metrics")

    results = load_cached_jsonl("results.jsonl", "results stream")
    audit = load_cached_jsonl("audit.jsonl", "audit stream")
    summary = load_cached("acceptance_summary.json")
    metadata = load_cached("run_metadata.json")
    closed_loop = load_cached("closed_loop/result.json")

    if not audit or any(
        type(item) is not dict
        or set(item) != {"schema_version", "utc", "kind", "stage", "detail"}
        or type(item["schema_version"]) is not int
        or item["schema_version"] != 1
        or any(type(item[field]) is not str or not item[field]
               for field in ("utc", "kind", "stage"))
        or type(item["detail"]) is not str
        for item in audit
    ):
        raise SystemExit("audit stream schema is invalid at completion")
    if audit[0]["kind"] != "run_start" or audit[0]["stage"] != "run" \
            or audit[-1]["kind"] != "run_complete" \
            or audit[-1]["stage"] != "run" \
            or not audit[-1]["detail"].endswith("/acceptance_summary.json") \
            or sum(item["kind"] == "run_start" for item in audit) != 1 \
            or sum(item["kind"] == "run_complete" for item in audit) != 1 \
            or sum(item["kind"] == "closed_loop_complete" for item in audit) != 1:
        raise SystemExit("audit stream does not contain the expected terminal records")

    source = metadata.get("closed_loop_source")
    closed_loop_schema = {
        "schema_version", "source", "expected_key", "jam_code_count",
        "radar_callback_stored_key", "radar_entered_phase2", "evidence_manifest",
        "violations", "passed",
    }
    if type(closed_loop) is not dict or set(closed_loop) != closed_loop_schema \
            or type(closed_loop["schema_version"]) is not int \
            or closed_loop["schema_version"] != 2 \
            or closed_loop["source"] != source \
            or closed_loop["expected_key"] != "fcYqTC" \
            or type(closed_loop["jam_code_count"]) is not int \
            or closed_loop["jam_code_count"] != 1 \
            or closed_loop["radar_callback_stored_key"] is not True \
            or closed_loop["radar_entered_phase2"] is not True \
            or closed_loop["violations"] != [] \
            or closed_loop["passed"] is not True:
        raise SystemExit("closed-loop result is not a successful current-source result")
    common_closed_loop_paths = {
        "jam_codes": "closed_loop/jam_codes.jsonl",
        "monitor_prelaunch_ready": "closed_loop/monitor.prelaunch-ready.json",
        "monitor_bound_ready": "closed_loop/monitor.bound-ready.json",
        "radar_log_identity": "closed_loop/radar_log_identity.json",
        "radar_evidence_start": "closed_loop/radar_evidence_start.json",
        "radar_delta": "closed_loop/radar.delta.log",
        "receiver_log": "closed_loop/receiver.log",
    }
    source_closed_loop_paths = {
        "bench": {
            "bench_iq": "closed_loop/iq/closed_loop.c64",
            "bench_chunks": "closed_loop/iq/closed_loop.chunks.jsonl",
            "bench_events": "closed_loop/iq/closed_loop.events.jsonl",
            "bench_summary": "closed_loop/iq/closed_loop.summary.json",
        },
        "replay": {
            "replay_source_identity": "l1_source_identity.json",
        },
    }
    if source not in source_closed_loop_paths:
        raise SystemExit("run metadata closed-loop source is invalid")
    expected_closed_loop_paths = {
        **common_closed_loop_paths, **source_closed_loop_paths[source],
    }
    closed_manifest = closed_loop["evidence_manifest"]
    if type(closed_manifest) is not dict \
            or set(closed_manifest) != set(expected_closed_loop_paths):
        raise SystemExit("closed-loop evidence manifest roles are invalid")
    for role, expected_path in expected_closed_loop_paths.items():
        identity = closed_manifest[role]
        if type(identity) is not dict or set(identity) != {
            "path", "device", "inode", "nlink", "bytes", "sha256",
        } or type(identity.get("path")) is not str \
                or identity["path"] != expected_path \
                or any(type(identity.get(name)) is not int or identity[name] < 0
                       for name in ("device", "inode", "nlink", "bytes")) \
                or identity["nlink"] != 1 \
                or type(identity.get("sha256")) is not str \
                or re.fullmatch(r"[0-9a-f]{64}", identity["sha256"]) is None:
            raise SystemExit(f"closed-loop evidence identity is invalid: {role}")
        observed = hashes.get(expected_path)
        if observed is None or not exact_json_equal(identity | {"path": expected_path}, {
            "path": expected_path,
            "device": observed["device"],
            "inode": observed["inode"],
            "nlink": observed["nlink"],
            "bytes": observed["bytes"],
            "sha256": observed["sha256"],
        }):
            raise SystemExit(f"closed-loop evidence changed after validation: {role}")

    metadata_schema = {
        "schema_version", "created_utc", "operator_acknowledgement", "own_team",
        "fixed_rf_metadata", "durations_sec", "gain", "thresholds",
        "sample_rate_hz", "closed_loop_source", "confirmed_source",
        "radar_log_flush", "hardware_acceptance_eligible",
        "hardware_acceptance_claimed",
    }
    fixed_schema = {
        "cable_length_m", "power_supply", "tx_distance_m", "polarization",
    }
    gain_schema = {"start_db", "step_db", "max_db"}
    threshold_schema = {
        "minimum_acquisition_duty", "maximum_queue_drops",
        "maximum_libiio_timeouts", "minimum_crc16_count",
        "rf_clipping_ratio",
    }
    if type(metadata) is not dict or set(metadata) != metadata_schema \
            or type(metadata.get("schema_version")) is not int \
            or metadata.get("schema_version") != 1 \
            or type(metadata.get("created_utc")) is not str \
            or not metadata["created_utc"] \
            or metadata.get("operator_acknowledgement") \
            != "I_ACKNOWLEDGE_CONTROLLED_RF_BENCH" \
            or metadata.get("own_team") not in {"RED", "BLUE"} \
            or metadata.get("hardware_acceptance_eligible") is not eligible \
            or metadata.get("hardware_acceptance_claimed") is not False \
            or type(metadata.get("sample_rate_hz")) is not int \
            or metadata["sample_rate_hz"] != 2_000_000:
        raise SystemExit("run metadata schema or top-level values are invalid")
    fixed_rf = metadata.get("fixed_rf_metadata")
    if type(fixed_rf) is not dict or set(fixed_rf) != fixed_schema \
            or any(
                type(fixed_rf.get(name)) is not float
                or not math.isfinite(fixed_rf[name]) or fixed_rf[name] <= 0
                for name in ("cable_length_m", "tx_distance_m")
            ) or any(
                type(fixed_rf.get(name)) is not str or not fixed_rf[name]
                for name in ("power_supply", "polarization")
            ):
        raise SystemExit("run fixed RF metadata is invalid")
    durations = metadata.get("durations_sec")
    if type(durations) is not dict \
            or set(durations) != {"gain_scan", "usb_stability_each", "closed_loop"} \
            or any(
                type(value) is not float
                or not math.isfinite(value) or value <= 0
                for value in durations.values()
            ):
        raise SystemExit("run duration metadata is invalid")
    gain_plan = metadata.get("gain")
    if type(gain_plan) is not dict or set(gain_plan) != gain_schema \
            or any(type(gain_plan.get(name)) is not int for name in gain_schema) \
            or gain_plan["start_db"] != 0 or gain_plan["step_db"] <= 0 \
            or gain_plan["max_db"] < 0:
        raise SystemExit("run gain plan is invalid")
    thresholds = metadata.get("thresholds")
    if type(thresholds) is not dict or set(thresholds) != threshold_schema \
            or type(thresholds.get("maximum_queue_drops")) is not int \
            or thresholds["maximum_queue_drops"] < 0 \
            or type(thresholds.get("maximum_libiio_timeouts")) is not int \
            or thresholds["maximum_libiio_timeouts"] < 0 \
            or type(thresholds.get("minimum_crc16_count")) is not int \
            or thresholds["minimum_crc16_count"] < 0 \
            or type(thresholds.get("minimum_acquisition_duty")) is not float \
            or not math.isfinite(thresholds["minimum_acquisition_duty"]) \
            or type(thresholds.get("rf_clipping_ratio")) is not float \
            or not math.isfinite(thresholds["rf_clipping_ratio"]) \
            or thresholds["minimum_acquisition_duty"] != 0.99 \
            or thresholds["maximum_queue_drops"] != 0 \
            or thresholds["maximum_libiio_timeouts"] != 0 \
            or thresholds["minimum_crc16_count"] != 1 \
            or thresholds["rf_clipping_ratio"] != 0.001:
        raise SystemExit("run threshold metadata is invalid")
    confirmed = metadata.get("confirmed_source")
    if type(confirmed) is not dict or set(confirmed) != {
        "team", "target", "expected_cmd_id", "expected_ascii", "sha256",
    } or confirmed.get("team") != "BLUE" or confirmed.get("target") != "L1" \
            or type(confirmed.get("expected_cmd_id")) is not int \
            or confirmed.get("expected_cmd_id") != 2566 \
            or confirmed.get("expected_ascii") != "fcYqTC" \
            or (
                source == "bench" and confirmed.get("sha256") is not None
            ) or (
                source == "replay" and (
                    type(confirmed.get("sha256")) is not str
                    or len(confirmed["sha256"]) != 64
                    or any(character not in "0123456789abcdef"
                           for character in confirmed["sha256"])
                )
            ):
        raise SystemExit("confirmed source metadata is invalid")
    radar_flush = metadata.get("radar_log_flush")
    if type(radar_flush) is not dict or set(radar_flush) != {
        "pid", "process_start_ticks", "stop_timeout_sec", "script_stops_process",
    } or type(radar_flush.get("pid")) is not int or radar_flush["pid"] <= 0 \
            or type(radar_flush.get("process_start_ticks")) is not str \
            or not radar_flush["process_start_ticks"].isdigit() \
            or type(radar_flush.get("stop_timeout_sec")) not in {int, float} \
            or not math.isfinite(radar_flush["stop_timeout_sec"]) \
            or radar_flush["stop_timeout_sec"] <= 0 \
            or radar_flush.get("script_stops_process") is not False:
        raise SystemExit("radar flush metadata is invalid")

    if not results or any(
        type(item) is not dict
        or item.get("record_type") not in {"measurement_window", "combination_summary"}
        for item in results
    ):
        raise SystemExit("results stream contains an unsupported record")
    windows = [item for item in results if item["record_type"] == "measurement_window"]
    combinations = [item for item in results if item["record_type"] == "combination_summary"]
    if not windows or any(item.get("passed") is not True for item in windows):
        raise SystemExit("a measurement window did not pass at completion")
    stages = [item.get("stage") for item in windows]
    if any(type(stage) is not str or not stage for stage in stages) \
            or len(stages) != len(set(stages)):
        raise SystemExit("measurement window stages are invalid or duplicated")

    expected_metric_paths = set()
    windows_by_stage = {}
    for window in windows:
        stage = window.get("stage")
        recorder_manifest = window.get("recorder_artifacts")
        if type(stage) is not str or "/" in stage or stage in {".", ".."} \
                or type(recorder_manifest) is not dict \
                or set(recorder_manifest) != {"iq", "chunks", "events", "summary"} \
                or not exact_json_equal(window.get("fixed_rf_metadata"), fixed_rf):
            raise SystemExit("measurement window metadata or recorder manifest is invalid")
        metrics_path = f"{stage}/metrics.json"
        expected_metric_paths.add(metrics_path)
        metrics = load_cached(metrics_path)
        validate_production_metrics(
            metrics, metadata, thresholds, f"{stage} metrics",
        )
        result_only_fields = {
            "record_type", "hardware_label", "usb_cable", "fixed_rf_metadata",
        }
        if set(window) != set(metrics) | result_only_fields \
                or any(
                    not exact_json_equal(window.get(name), value)
                    for name, value in metrics.items()
                ) \
                or window.get("record_type") != "measurement_window" \
                or not exact_json_equal(
                    metrics.get("recorder_artifacts"), recorder_manifest,
                ):
            raise SystemExit(f"results/metrics content mismatch: {stage}")
        expected_role_paths = {
            "iq": f"iq/{stage}.c64",
            "chunks": f"iq/{stage}.chunks.jsonl",
            "events": f"iq/{stage}.events.jsonl",
            "summary": f"iq/{stage}.summary.json",
        }
        expected_stage_files = {
            f"{stage}/launch.log", f"{stage}/status.jsonl", metrics_path,
        } | {
            f"{stage}/{path}" for path in expected_role_paths.values()
        }
        observed_stage_files = {
            path for path in hashes if path.startswith(f"{stage}/")
        }
        if observed_stage_files != expected_stage_files:
            raise SystemExit(f"{stage} does not contain the exact production evidence set")
        identities = set()
        for role, identity in recorder_manifest.items():
            if type(identity) is not dict or set(identity) != {
                "path", "device", "inode", "bytes", "sha256",
            } or type(identity.get("path")) is not str \
                    or identity["path"] != expected_role_paths[role] \
                    or any(type(identity.get(field)) is not int
                           or identity[field] < 0
                           for field in ("device", "inode", "bytes")) \
                    or type(identity.get("sha256")) is not str \
                    or re.fullmatch(r"[0-9a-f]{64}", identity["sha256"]) is None:
                raise SystemExit(f"recorder artifact identity is invalid: {stage}/{role}")
            relative = PurePosixPath(identity["path"])
            if relative.is_absolute() or ".." in relative.parts \
                    or len(relative.parts) != 2 or relative.parts[0] != "iq":
                raise SystemExit(f"recorder artifact path is unsafe: {stage}/{role}")
            artifact_path = str(PurePosixPath(stage) / relative)
            observed = hashes.get(artifact_path)
            if observed is None or any(
                type(identity.get(field)) is not type(observed[field])
                or identity.get(field) != observed[field]
                for field in ("device", "inode", "bytes", "sha256")
            ):
                raise SystemExit(f"recorder artifact changed after analysis: {artifact_path}")
            identities.add((identity["device"], identity["inode"]))
        if len(identities) != 4:
            raise SystemExit(f"{stage} recorder roles do not have distinct file identities")
        validate_launch_evidence(stage, metrics)
        validate_status_evidence(stage, metrics, expected_role_paths["events"])
        validate_recorder_summary(stage, metrics, expected_role_paths)
        windows_by_stage[stage] = window
    observed_metric_paths = {
        path for path in cached if path.endswith("/metrics.json")
    }
    if observed_metric_paths != expected_metric_paths:
        raise SystemExit("completion metrics set does not match results windows")

    combination_plan = (
        ("sdr_direct", "SDR direct"),
        ("sdr_saw", "SDR + SAW"),
        ("sdr_lna", "SDR + LNA"),
        ("sdr_lna_saw", "SDR + LNA + SAW"),
        ("full_chain_10db", "complete chain + 10 dB attenuation"),
        ("full_chain_20db", "complete chain + 20 dB attenuation"),
    )
    combination_schema = {
        "schema_version", "record_type", "combination", "hardware_label",
        "final_gain_db", "final_linear_peak", "final_linear_rms",
        "final_linear_clipping_ratio", "final_linear_crc16_count",
        "all_scan_crc16_count", "fixed_rf_metadata", "fieldable", "passed",
    }
    cursor = 0
    graph = []
    for ordinal, (combination, label) in enumerate(combination_plan, 1):
        scan = []
        while cursor < len(results):
            item = results[cursor]
            if item["record_type"] != "measurement_window" \
                    or not str(item.get("stage", "")).startswith("matrix_"):
                break
            scan.append(item)
            cursor += 1
        if not scan:
            raise SystemExit(f"matrix evidence is missing for combination: {combination}")
        for index, window in enumerate(scan):
            gain = window.get("gain_db")
            if type(gain) is not int or gain < 0:
                raise SystemExit(f"matrix gain is invalid for combination: {combination}")
            expected_stage = (
                f"matrix_{ordinal:02d}_{combination}_gain_{gain:02d}"
            )
            if window.get("stage") != expected_stage \
                    or window.get("combination") != combination \
                    or window.get("hardware_label") != label \
                    or window.get("usb_cable") is not None \
                    or window.get("requested_duration_sec") \
                    != durations["gain_scan"] \
                    or window.get("stability_thresholds_enforced") is not False:
                raise SystemExit(f"matrix identity is invalid: {expected_stage}")
            if index == 0 and gain != 0:
                raise SystemExit(f"matrix gain scan did not start at zero: {combination}")

        last_linear = None
        total_crc16 = 0
        for index, window in enumerate(scan):
            gain = window["gain_db"]
            state = window.get("rf_state")
            crc16 = window.get("crc16_count")
            if gain > gain_plan["max_db"] or state not in {"linear", "clipped"} \
                    or type(crc16) is not int or crc16 < 0:
                raise SystemExit(f"matrix RF state or CRC evidence is invalid: {combination}")
            total_crc16 += crc16
            if state == "clipped":
                if index != len(scan) - 1:
                    raise SystemExit(f"matrix continued after clipping: {combination}")
                continue
            last_linear = window
            if gain == gain_plan["max_db"]:
                if index != len(scan) - 1:
                    raise SystemExit(f"matrix continued after maximum gain: {combination}")
                continue
            expected_next = min(
                gain + gain_plan["step_db"], gain_plan["max_db"],
            )
            if index + 1 >= len(scan) \
                    or scan[index + 1].get("gain_db") != expected_next:
                raise SystemExit(f"matrix gain progression is incomplete: {combination}")
        if last_linear is None:
            raise SystemExit(f"matrix has no legal linear window: {combination}")
        terminal = scan[-1]
        if terminal.get("rf_state") == "linear" \
                and terminal.get("gain_db") != gain_plan["max_db"]:
            raise SystemExit(f"matrix ended before maximum gain without clipping: {combination}")

        if cursor >= len(results):
            raise SystemExit(f"combination summary is missing: {combination}")
        combination_result = results[cursor]
        cursor += 1
        expected_binding = {
            "final_gain_db": last_linear["gain_db"],
            "final_linear_peak": last_linear["peak"],
            "final_linear_rms": last_linear["rms"],
            "final_linear_clipping_ratio": last_linear["clipping_ratio"],
            "final_linear_crc16_count": last_linear["crc16_count"],
            "all_scan_crc16_count": total_crc16,
        }
        if type(combination_result) is not dict \
                or set(combination_result) != combination_schema \
                or type(combination_result.get("schema_version")) is not int \
                or combination_result.get("schema_version") != 1 \
                or combination_result.get("record_type") != "combination_summary" \
                or combination_result.get("combination") != combination \
                or combination_result.get("hardware_label") != label \
                or not exact_json_equal(
                    combination_result.get("fixed_rf_metadata"), fixed_rf,
                ) \
                or combination_result.get("fieldable") is not True \
                or combination_result.get("passed") is not True \
                or any(
                    type(combination_result.get(name)) is not type(value)
                    or combination_result.get(name) != value
                    for name, value in expected_binding.items()
                ) or combination_result["final_linear_crc16_count"] \
                < thresholds["minimum_crc16_count"]:
            raise SystemExit(f"combination summary does not bind current matrix: {combination}")
        graph.append((ordinal, combination, label, scan, combination_result))

    stability_plan = (
        ("stability_usb3_short", "usb3_short", "verified short USB 3 cable, complete RF chain",
         "verified_short_usb3"),
        ("stability_usb3_competition_3m", "usb3_competition_3m",
         "competition 3 m USB cable, same host port and RF chain",
         "competition_usb3_3m"),
    )
    final_combination = graph[-1][4]
    stability_windows = []
    for stage, _, _, cable in stability_plan:
        if cursor >= len(results):
            raise SystemExit(f"required stability window is missing: {stage}")
        window = results[cursor]
        cursor += 1
        if window.get("record_type") != "measurement_window" \
                or window.get("stage") != stage \
                or window.get("combination") != "full_chain_20db" \
                or window.get("hardware_label") != "complete chain stability" \
                or window.get("gain_db") != final_combination["final_gain_db"] \
                or window.get("usb_cable") != cable \
                or window.get("requested_duration_sec") \
                != durations["usb_stability_each"] \
                or window.get("stability_thresholds_enforced") is not True:
            raise SystemExit(f"required stability window is invalid: {stage}")
        stability_windows.append(window)
    if cursor != len(results) or len(windows) != sum(
        len(item[3]) for item in graph
    ) + len(stability_windows) or len(combinations) != len(combination_plan):
        raise SystemExit("results stream does not exactly match the matrix and stability graph")

    structural_kinds = {
        "operator_ack", "combination_start", "window_start",
        "window_complete", "gain_scan_stop", "combination_complete",
    }
    structural_stages = {
        combination for combination, _ in combination_plan
    } | {
        stage for stage, _, _, _ in stability_plan
    } | {
        prompt_stage for _, prompt_stage, _, _ in stability_plan
    } | set(windows_by_stage)
    observed_structure = [
        (item["kind"], item["stage"], item["detail"])
        for item in audit
        if item["kind"] in structural_kinds and item["stage"] in structural_stages
    ]
    if any(
        item["kind"] == "window_failed"
        or (
            item["kind"] in structural_kinds - {"operator_ack"}
            and item["stage"] not in structural_stages
        )
        for item in audit
    ):
        raise SystemExit("audit contains an unexpected matrix lifecycle record")
    expected_structure = []
    for ordinal, combination, label, scan, combination_result in graph:
        expected_structure.extend([
            ("operator_ack", combination, label),
            ("combination_start", combination, f"ordinal={ordinal} label={label}"),
        ])
        for window in scan:
            stage = window["stage"]
            expected_structure.extend([
                ("window_start", stage, f"gain_db={window['gain_db']}"),
                ("window_complete", stage, f"metrics={root / stage / 'metrics.json'}"),
            ])
        if scan[-1]["rf_state"] == "clipped":
            expected_structure.append((
                "gain_scan_stop", combination,
                f"RF_CLIPPED at gain_db={scan[-1]['gain_db']}",
            ))
        expected_structure.append((
            "combination_complete", combination,
            f"final_gain_db={combination_result['final_gain_db']} "
            f"crc16_count={combination_result['all_scan_crc16_count']}",
        ))
    for window, (stage, prompt_stage, prompt, _) in zip(
        stability_windows, stability_plan,
    ):
        expected_structure.extend([
            ("operator_ack", prompt_stage, prompt),
            ("window_start", stage, f"gain_db={window['gain_db']}"),
            ("window_complete", stage, f"metrics={root / stage / 'metrics.json'}"),
        ])
    if observed_structure != expected_structure:
        raise SystemExit("audit order does not match the current matrix result graph")

    expected_summary = {
        "schema_version": 1,
        "hardware_acceptance_eligible": eligible,
        "hardware_acceptance_status": (
            "PROCEDURE_PASSED" if eligible else "NOT_ELIGIBLE_SHORT_DURATION"
        ),
        "hardware_acceptance_claimed_by_script": False,
        "window_count": len(windows),
        "combination_count": len(combinations),
        "all_recorded_windows_passed": True,
        "all_combinations_fieldable": True,
        "closed_loop_passed": True,
        "final_full_chain_gain_db": final_combination["final_gain_db"],
        "results_jsonl": "results.jsonl",
        "audit_jsonl": "audit.jsonl",
    }
    if not exact_json_equal(summary, expected_summary):
        raise SystemExit("acceptance summary disagrees with current completion evidence")
    publication_files = {}
    publication_directories = {}
    owned_publication_file_fds = []
    owned_publication_directory_fds = []

    def artifact_state(info):
        return (
            info.st_dev, info.st_ino, info.st_nlink, info.st_size,
            info.st_mtime_ns, info.st_ctime_ns, stat.S_IMODE(info.st_mode),
        )

    def directory_state(info):
        return info.st_dev, info.st_ino, stat.S_IMODE(info.st_mode)

    def open_publication_tree(
        current_fd, prefix="", parent_fd=None, entry_name=None,
    ):
        current_info = os.fstat(current_fd)
        if not stat.S_ISDIR(current_info.st_mode):
            raise SystemExit(f"publication directory is invalid: {prefix or '.'}")
        publication_directories[prefix] = {
            "fd": current_fd,
            "parent_fd": parent_fd,
            "entry_name": entry_name,
            "baseline": directory_state(current_info),
            "children": set(os.listdir(current_fd)),
        }
        for name in sorted(publication_directories[prefix]["children"]):
            relative = f"{prefix}/{name}" if prefix else name
            if relative in {"completion.json", ".completion.private"}:
                raise SystemExit("completion marker already exists")
            path_info = os.stat(name, dir_fd=current_fd, follow_symlinks=False)
            if stat.S_ISDIR(path_info.st_mode):
                child_fd = os.open(
                    name,
                    os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
                    | getattr(os, "O_NOFOLLOW", 0),
                    dir_fd=current_fd,
                )
                try:
                    child_info = os.fstat(child_fd)
                    if not stat.S_ISDIR(child_info.st_mode) or directory_state(
                        child_info
                    ) != directory_state(path_info):
                        raise SystemExit(
                            f"publication directory identity changed: {relative}"
                        )
                except BaseException:
                    os.close(child_fd)
                    raise
                owned_publication_directory_fds.append(child_fd)
                open_publication_tree(child_fd, relative, current_fd, name)
            elif stat.S_ISREG(path_info.st_mode):
                artifact_fd = os.open(
                    name,
                    os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
                    dir_fd=current_fd,
                )
                try:
                    info = os.fstat(artifact_fd)
                    path_after = os.stat(
                        name, dir_fd=current_fd, follow_symlinks=False,
                    )
                    expected = hashes.get(relative)
                    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 \
                            or artifact_state(path_after) != artifact_state(info) \
                            or stat.S_IMODE(info.st_mode) != 0o400 \
                            or expected is None \
                            or (info.st_dev, info.st_ino, info.st_nlink, info.st_size) \
                            != (
                                expected["device"], expected["inode"],
                                expected["nlink"], expected["bytes"],
                            ):
                        raise SystemExit(
                            f"publication artifact identity changed: {relative}"
                        )
                except BaseException:
                    os.close(artifact_fd)
                    raise
                owned_publication_file_fds.append(artifact_fd)
                publication_files[relative] = {
                    "fd": artifact_fd,
                    "parent_fd": current_fd,
                    "entry_name": name,
                    "baseline": artifact_state(info),
                }
            else:
                raise SystemExit(f"unsupported publication entry: {relative}")

    def digest_publication_artifact(fd, size, relative):
        digest = hashlib.sha256()
        offset = 0
        while offset < size:
            block = os.pread(fd, min(1024 * 1024, size - offset), offset)
            if not block:
                raise SystemExit(
                    f"publication artifact became unreadable: {relative}"
                )
            digest.update(block)
            offset += len(block)
        return digest.hexdigest()

    class PublicationTransactionWatcher:
        IN_MODIFY = 0x00000002
        IN_ATTRIB = 0x00000004
        IN_CLOSE_WRITE = 0x00000008
        IN_MOVED_FROM = 0x00000040
        IN_MOVED_TO = 0x00000080
        IN_CREATE = 0x00000100
        IN_DELETE = 0x00000200
        IN_DELETE_SELF = 0x00000400
        IN_MOVE_SELF = 0x00000800
        IN_UNMOUNT = 0x00002000
        IN_Q_OVERFLOW = 0x00004000
        IN_IGNORED = 0x00008000
        IN_ONLYDIR = 0x01000000
        IN_ISDIR = 0x40000000

        ARTIFACT_MASK = (
            IN_MODIFY | IN_ATTRIB | IN_CLOSE_WRITE | IN_DELETE_SELF
            | IN_MOVE_SELF | IN_UNMOUNT
        )
        DIRECTORY_MASK = (
            IN_ATTRIB | IN_CLOSE_WRITE | IN_MOVED_FROM | IN_MOVED_TO
            | IN_CREATE | IN_DELETE | IN_DELETE_SELF | IN_MOVE_SELF
            | IN_UNMOUNT | IN_ONLYDIR
        )

        def __init__(self, files, directories):
            self.fd = None
            self.watches = {}
            self.marker_state = "await_private_create"
            self.marker_move_cookie = None
            try:
                libc = ctypes.CDLL(None, use_errno=True)
                init = libc.inotify_init1
                add = libc.inotify_add_watch
            except (AttributeError, OSError) as exc:
                raise SystemExit(
                    "publication inotify is unavailable on this execute platform"
                ) from exc
            init.argtypes = [ctypes.c_int]
            init.restype = ctypes.c_int
            add.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_uint32]
            add.restype = ctypes.c_int
            watcher_fd = init(os.O_NONBLOCK | os.O_CLOEXEC)
            if watcher_fd < 0:
                error = ctypes.get_errno()
                raise SystemExit(
                    f"publication inotify initialization failed: {os.strerror(error)}"
                )
            self.fd = watcher_fd
            self._add_watch = add
            try:
                for relative, record in files.items():
                    self._watch_fd(
                        record["fd"], self.ARTIFACT_MASK,
                        {"kind": "artifact", "relative": relative},
                    )
                for relative, record in directories.items():
                    self._watch_fd(
                        record["fd"], self.DIRECTORY_MASK,
                        {"kind": "directory", "relative": relative},
                    )
            except BaseException:
                self.close()
                raise

        def _watch_fd(self, target_fd, mask, description):
            proc_path = os.fsencode(f"/proc/self/fd/{target_fd}")
            watch = self._add_watch(self.fd, proc_path, mask)
            if watch < 0:
                error = ctypes.get_errno()
                raise SystemExit(
                    "publication inotify watch failed for "
                    f"{description['relative'] or '.'}: {os.strerror(error)}"
                )
            if watch in self.watches:
                raise SystemExit("publication inotify watch identity was reused")
            self.watches[watch] = description

        def _read_pending(self):
            events = []
            while True:
                try:
                    payload = os.read(self.fd, 1024 * 1024)
                except BlockingIOError:
                    break
                if not payload:
                    raise SystemExit("publication inotify watcher closed unexpectedly")
                offset = 0
                while offset < len(payload):
                    if len(payload) - offset < 16:
                        raise SystemExit("publication inotify event framing is invalid")
                    watch, mask, cookie, name_length = struct.unpack_from(
                        "iIII", payload, offset,
                    )
                    name_start = offset + 16
                    event_end = name_start + name_length
                    if event_end > len(payload):
                        raise SystemExit("publication inotify event framing is invalid")
                    raw_name = payload[name_start:event_end].rstrip(b"\0")
                    try:
                        name = raw_name.decode("utf-8", errors="strict")
                    except UnicodeDecodeError as exc:
                        raise SystemExit(
                            "publication inotify event name is not UTF-8"
                        ) from exc
                    events.append((watch, mask, cookie, name))
                    offset = event_end
            return events

        def _process_marker_event(self, mask, cookie, name):
            meaningful = mask & ~self.IN_ISDIR
            if meaningful == self.IN_CREATE \
                    and name == ".completion.private" \
                    and self.marker_state == "await_private_create":
                self.marker_state = "private_created"
                return
            if meaningful == self.IN_MOVED_FROM \
                    and name == ".completion.private" \
                    and self.marker_state == "private_created" \
                    and cookie != 0:
                self.marker_move_cookie = cookie
                self.marker_state = "private_moved_from"
                return
            if meaningful == self.IN_MOVED_TO \
                    and name == "completion.json" \
                    and self.marker_state == "private_moved_from" \
                    and cookie == self.marker_move_cookie:
                self.marker_state = "final_moved_to"
                return
            if meaningful == self.IN_CLOSE_WRITE \
                    and name == "completion.json" \
                    and self.marker_state == "final_moved_to":
                self.marker_state = "final_closed"
                return
            raise SystemExit(
                "publication directory contained an unexpected marker event: "
                f"name={name!r},mask=0x{mask:x},state={self.marker_state}"
            )

        def drain(self, phase):
            events = self._read_pending()
            if any(mask & self.IN_Q_OVERFLOW for _watch, mask, _cookie, _name in events):
                raise SystemExit("publication inotify queue overflow")
            if any(mask & (self.IN_IGNORED | self.IN_UNMOUNT)
                   for _watch, mask, _cookie, _name in events):
                raise SystemExit("publication inotify watch became invalid")
            for watch, mask, cookie, name in events:
                description = self.watches.get(watch)
                if description is None:
                    raise SystemExit("publication inotify returned an unknown watch")
                if description["kind"] == "artifact":
                    raise SystemExit(
                        "publication artifact changed during transaction: "
                        f"{description['relative']}"
                    )
                if description["relative"] == "" \
                        and name in {".completion.private", "completion.json"}:
                    self._process_marker_event(mask, cookie, name)
                    continue
                raise SystemExit(
                    "publication directory changed during transaction: "
                    f"{description['relative'] or '.'}/{name}"
                )
            expected_state = {
                "pre_rename": "private_created",
                "post_publish": "final_closed",
            }.get(phase)
            if expected_state is None:
                raise SystemExit("publication inotify phase is invalid")
            if self.marker_state != expected_state:
                raise SystemExit(
                    "publication marker event sequence is incomplete: "
                    f"phase={phase},state={self.marker_state}"
                )

        def finish(self):
            self.drain("post_publish")
            self.close()

        def close(self):
            if self.fd is not None:
                os.close(self.fd)
                self.fd = None

    transaction_watcher = None
    try:
        open_publication_tree(dir_fd)
        if set(publication_files) != set(hashes) \
                or set(publication_directories) != directory_paths:
            raise SystemExit("publication evidence path set changed")
        transaction_watcher = PublicationTransactionWatcher(
            publication_files, publication_directories,
        )

        for relative in sorted(publication_files, reverse=True):
            record = publication_files[relative]
            before = os.fstat(record["fd"])
            if artifact_state(before) != record["baseline"]:
                raise SystemExit(
                    f"publication artifact changed before second hash: {relative}"
                )
            digest = digest_publication_artifact(
                record["fd"], before.st_size, relative,
            )
            after = os.fstat(record["fd"])
            if artifact_state(after) != record["baseline"] \
                    or digest != hashes[relative]["sha256"]:
                raise SystemExit(
                    f"publication artifact changed during second hash: {relative}"
                )

        payload = {
            "schema_version": 2,
            "status": "complete",
            "created_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "hardware_acceptance_eligible": eligible,
            "hardware_acceptance_status": summary.get("hardware_acceptance_status"),
            "artifacts": hashes,
        }
        encoded = (json.dumps(
            payload, ensure_ascii=False, indent=2, sort_keys=True,
        ) + "\n").encode()
        temporary = ".completion.private"
        final = "completion.json"
        marker_created = False
        publication_complete = False
        marker_fd = None
        private_path_fd = None
        final_path_fd = None
        try:
            marker_fd = os.open(
                temporary,
                os.O_RDWR | os.O_CREAT | os.O_EXCL
                | getattr(os, "O_NOFOLLOW", 0),
                0o400,
                dir_fd=dir_fd,
            )
            marker_created = True
            offset = 0
            while offset < len(encoded):
                offset += os.write(marker_fd, encoded[offset:])
            os.fsync(marker_fd)
            marker_info = os.fstat(marker_fd)
            expected_marker = {
                "content_bytes": encoded,
                "sha256": hashlib.sha256(encoded).hexdigest(),
                "size": marker_info.st_size,
                "device": marker_info.st_dev,
                "inode": marker_info.st_ino,
                "nlink": marker_info.st_nlink,
                "mode": stat.S_IMODE(marker_info.st_mode),
                "mtime_ns": marker_info.st_mtime_ns,
                "ctime_ns": marker_info.st_ctime_ns,
            }
            expected_marker_state = (
                expected_marker["device"], expected_marker["inode"],
                expected_marker["nlink"], expected_marker["size"],
                expected_marker["mtime_ns"], expected_marker["ctime_ns"],
                expected_marker["mode"],
            )
            if not stat.S_ISREG(marker_info.st_mode) \
                    or expected_marker["nlink"] != 1 \
                    or expected_marker["size"] != len(
                        expected_marker["content_bytes"]
                    ) \
                    or expected_marker["mode"] != 0o400 \
                    or digest_publication_artifact(
                        marker_fd, expected_marker["size"], temporary,
                    ) != expected_marker["sha256"] \
                    or artifact_state(os.fstat(marker_fd)) \
                    != expected_marker_state:
                raise SystemExit("private completion marker is invalid")

            for relative, record in publication_files.items():
                current = os.fstat(record["fd"])
                try:
                    path_info = os.stat(
                        record["entry_name"], dir_fd=record["parent_fd"],
                        follow_symlinks=False,
                    )
                except FileNotFoundError as exc:
                    raise SystemExit(
                        f"publication artifact path disappeared: {relative}"
                    ) from exc
                if not stat.S_ISREG(path_info.st_mode) \
                        or artifact_state(current) != record["baseline"] \
                        or artifact_state(path_info) != record["baseline"]:
                    raise SystemExit(
                        f"publication artifact changed before marker rename: {relative}"
                    )

            for relative, record in publication_directories.items():
                current = os.fstat(record["fd"])
                if directory_state(current) != record["baseline"]:
                    raise SystemExit(
                        f"publication directory changed before marker rename: {relative or '.'}"
                    )
                if record["parent_fd"] is None:
                    path_info = root_input.lstat()
                    if not stat.S_ISDIR(path_info.st_mode) \
                            or directory_state(path_info) != record["baseline"]:
                        raise SystemExit(
                            "publication root path identity changed"
                        )
                else:
                    try:
                        path_info = os.stat(
                            record["entry_name"], dir_fd=record["parent_fd"],
                            follow_symlinks=False,
                        )
                    except FileNotFoundError as exc:
                        raise SystemExit(
                            f"publication directory path disappeared: {relative}"
                        ) from exc
                    if not stat.S_ISDIR(path_info.st_mode) \
                            or directory_state(path_info) != record["baseline"]:
                        raise SystemExit(
                            f"publication directory identity changed: {relative}"
                        )
                expected_children = set(record["children"])
                if relative == "":
                    expected_children.add(temporary)
                if set(os.listdir(record["fd"])) != expected_children:
                    raise SystemExit(
                        f"publication directory entries changed: {relative or '.'}"
                    )

            private_path_fd = os.open(
                temporary,
                os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
                dir_fd=dir_fd,
            )
            private_info = os.fstat(private_path_fd)
            private_path_info = os.stat(
                temporary, dir_fd=dir_fd, follow_symlinks=False,
            )
            marker_before_rename = os.fstat(marker_fd)
            if not stat.S_ISREG(private_info.st_mode) \
                    or artifact_state(marker_before_rename) \
                    != expected_marker_state \
                    or artifact_state(private_info) != expected_marker_state \
                    or artifact_state(private_path_info) != expected_marker_state \
                    or digest_publication_artifact(
                        marker_fd, expected_marker["size"], temporary,
                    ) != expected_marker["sha256"] \
                    or digest_publication_artifact(
                        private_path_fd, expected_marker["size"], temporary,
                    ) != expected_marker["sha256"] \
                    or artifact_state(os.fstat(marker_fd)) \
                    != expected_marker_state \
                    or artifact_state(os.fstat(private_path_fd)) \
                    != expected_marker_state:
                raise SystemExit("private completion marker identity changed")
            transaction_watcher.drain("pre_rename")

            for relative, record in sorted(
                publication_directories.items(),
                key=lambda item: (
                    0 if item[0] == "" else item[0].count("/") + 1
                ),
                reverse=True,
            ):
                os.fsync(record["fd"])
            os.rename(
                temporary, final, src_dir_fd=dir_fd, dst_dir_fd=dir_fd,
            )
            expected_marker_identity = (
                expected_marker["device"], expected_marker["inode"],
                expected_marker["nlink"], expected_marker["size"],
                expected_marker["mode"],
            )

            def marker_identity(info):
                return (
                    info.st_dev, info.st_ino, info.st_nlink, info.st_size,
                    stat.S_IMODE(info.st_mode),
                )

            final_path_fd = os.open(
                final,
                os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
                dir_fd=dir_fd,
            )
            marker_after_rename = os.fstat(marker_fd)
            private_fd_after_rename = os.fstat(private_path_fd)
            final_info = os.fstat(final_path_fd)
            final_path_info = os.stat(
                final, dir_fd=dir_fd, follow_symlinks=False,
            )
            marker_post_state = artifact_state(marker_after_rename)
            final_post_state = artifact_state(final_info)
            if not stat.S_ISREG(final_info.st_mode) \
                    or marker_identity(marker_after_rename) \
                    != expected_marker_identity \
                    or marker_identity(private_fd_after_rename) \
                    != expected_marker_identity \
                    or marker_identity(final_info) != expected_marker_identity \
                    or marker_identity(final_path_info) \
                    != expected_marker_identity \
                    or digest_publication_artifact(
                        marker_fd, expected_marker["size"], final,
                    ) != expected_marker["sha256"] \
                    or digest_publication_artifact(
                        final_path_fd, expected_marker["size"], final,
                    ) != expected_marker["sha256"] \
                    or artifact_state(os.fstat(marker_fd)) != marker_post_state \
                    or artifact_state(os.fstat(final_path_fd)) \
                    != final_post_state:
                raise SystemExit("published completion marker identity is invalid")
            final_path_after = os.stat(
                final, dir_fd=dir_fd, follow_symlinks=False,
            )
            if not stat.S_ISREG(final_path_after.st_mode) \
                    or marker_identity(final_path_after) \
                    != expected_marker_identity:
                raise SystemExit("published completion marker path changed")
            try:
                os.stat(temporary, dir_fd=dir_fd, follow_symlinks=False)
            except FileNotFoundError:
                pass
            else:
                raise SystemExit("private completion marker still exists after rename")
            os.fsync(dir_fd)
            os.close(marker_fd)
            marker_fd = None
            transaction_watcher.finish()
            transaction_watcher = None
            publication_complete = True
        finally:
            if final_path_fd is not None:
                os.close(final_path_fd)
            if private_path_fd is not None:
                os.close(private_path_fd)
            if marker_fd is not None:
                os.close(marker_fd)
            if marker_created and not publication_complete:
                for marker_name in (temporary, final):
                    try:
                        os.unlink(marker_name, dir_fd=dir_fd)
                    except FileNotFoundError:
                        pass
                os.fsync(dir_fd)
    finally:
        if transaction_watcher is not None:
            transaction_watcher.close()
        for artifact_fd in reversed(owned_publication_file_fds):
            os.close(artifact_fd)
        for directory_fd in reversed(owned_publication_directory_fds):
            os.close(directory_fd)
finally:
    os.close(dir_fd)
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
  preflight_disk
  preflight_resources
  snapshot_l1_source
  write_metadata
  audit_event "run_start" "run" "execute"

  local final_full_chain_gain=""
  local index
  for index in "${!COMBINATION_IDS[@]}"; do
    run_combination "$((index + 1))" "${COMBINATION_IDS[$index]}" "${COMBINATION_LABELS[$index]}"
    final_full_chain_gain="$LAST_FINAL_GAIN"
    [[ -n "$final_full_chain_gain" ]] || die "combination did not publish a final gain"
  done

  confirm_stage "usb3_short" "verified short USB 3 cable, complete RF chain"
  run_window "stability_usb3_short" "full_chain_20db" "complete chain stability" \
    "$final_full_chain_gain" "$STABILITY_DURATION_SEC" true "verified_short_usb3"

  confirm_stage "usb3_competition_3m" "competition 3 m USB cable, same host port and RF chain"
  run_window "stability_usb3_competition_3m" "full_chain_20db" "complete chain stability" \
    "$final_full_chain_gain" "$STABILITY_DURATION_SEC" true "competition_usb3_3m"

  run_closed_loop "$final_full_chain_gain"
  write_final_summary "$final_full_chain_gain"
  audit_event "run_complete" "run" "summary=$OUT_DIR/acceptance_summary.json"
  close_l1_snapshot_fd
  write_completion_marker
  printf 'RF bench procedure finished. Evidence: %s\n' "$OUT_DIR"
  if [[ "$RUN_ELIGIBLE" != true ]]; then
    printf 'NOT HARDWARE-ACCEPTANCE ELIGIBLE: short test duration was used.\n' >&2
  fi
}

main "$@"
