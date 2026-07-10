#!/usr/bin/env bash
set -euo pipefail

IQ_SOURCE_PATH="${1:-${SDR_IQ_SOURCE_PATH:-}}"
if [ -z "$IQ_SOURCE_PATH" ]; then
  echo "usage: $0 /path/to/capture.c64 [TEAM] [TARGET] [CENTER_HZ] [SAMPLE_RATE]" >&2
  echo "example: $0 ~/sdr_offline_iq/RX_BLUE_2.c64 BLUE L1 433920000 2000000" >&2
  exit 2
fi

TEAM="${2:-${SDR_IQ_REPLAY_TEAM:-BLUE}}"
TARGET="${3:-${SDR_IQ_REPLAY_TARGET:-L1}}"
CENTER_HZ="${4:-${SDR_IQ_SOURCE_CENTER_HZ:-433920000}}"
SAMPLE_RATE="${5:-${SDR_IQ_SOURCE_SAMPLE_RATE:-2000000}}"

DEFAULT_RADAR_WS="$HOME/3SE_2026_Radar"
if [ -z "${RADAR_WS:-}" ] && [ ! -d "$DEFAULT_RADAR_WS" ] && [ -d "$HOME/radar_ws" ]; then
  DEFAULT_RADAR_WS="$HOME/radar_ws"
fi
RADAR_WS="${RADAR_WS:-$DEFAULT_RADAR_WS}"

set +u
if [ -f /opt/ros/humble/setup.bash ]; then
  source /opt/ros/humble/setup.bash
fi
if [ -f "$RADAR_WS/install/setup.bash" ]; then
  source "$RADAR_WS/install/setup.bash"
fi
if [ -f "$HOME/sdr_runtime/venv/bin/activate" ]; then
  source "$HOME/sdr_runtime/venv/bin/activate"
fi
set -u

export PYTHONPATH="$HOME/sdr_runtime/venv/lib/python3.10/site-packages:${PYTHONPATH:-}"

exec ros2 launch sdr_receiver_py_wrapper iq_replay_jam_code.launch.py \
  iq_source_path:="$IQ_SOURCE_PATH" \
  iq_source_loop:=true \
  iq_source_throttle:=true \
  iq_source_sample_rate:="$SAMPLE_RATE" \
  iq_source_center_hz:="$CENTER_HZ" \
  initial_team:="$TEAM" \
  initial_target:="$TARGET"
