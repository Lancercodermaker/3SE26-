#!/usr/bin/env bash
set -eo pipefail

usage() {
  echo "Usage: bash start_competition_bo3.sh BLUE|RED"
  echo "Optional env: RADAR_WS, MATCH_TAG, SDR_PROFILE_DIR, SDR_IQ_RECORD_DIR"
  echo "Optional env: SDR_KEY_RETRY_LIMIT, SDR_KEY_PUBLISH_MIN_INTERVAL_SEC"
}

if [ "${1:-}" = "-h" ] || [ "${1:-}" = "--help" ]; then
  usage
  exit 0
fi

OWN_TEAM_ARG="${1:-${OWN_TEAM:-BLUE}}"
OWN_TEAM="$(printf '%s' "$OWN_TEAM_ARG" | tr '[:lower:]' '[:upper:]')"

case "$OWN_TEAM" in
  BLUE)
    RX_TEAM=RED
    FALLBACK_SELF_ID=109
    ;;
  RED)
    RX_TEAM=BLUE
    FALLBACK_SELF_ID=9
    ;;
  *)
    usage >&2
    echo "ERROR: team must be BLUE or RED, got '$OWN_TEAM_ARG'" >&2
    exit 2
    ;;
esac

DEFAULT_RADAR_WS="$HOME/3SE_2026_Radar"
if [ -z "${RADAR_WS:-}" ] && [ ! -d "$DEFAULT_RADAR_WS" ] && [ -d "$HOME/radar_ws" ]; then
  DEFAULT_RADAR_WS="$HOME/radar_ws"
fi
RADAR_WS="${RADAR_WS:-$DEFAULT_RADAR_WS}"
MATCH_TAG="${MATCH_TAG:-bo3_match}"
PROFILE_DIR="${SDR_PROFILE_DIR:-$HOME/sdr_runtime/profiles}"
RECORD_DIR="${SDR_IQ_RECORD_DIR:-$HOME/sdr_iq_records}"
KEY_RETRY_LIMIT="${SDR_KEY_RETRY_LIMIT:--1}"
KEY_PUBLISH_MIN_INTERVAL_SEC="${SDR_KEY_PUBLISH_MIN_INTERVAL_SEC:-0.5}"
PROFILE_ARG=()

if [ ! -d "$RADAR_WS" ]; then
  echo "ERROR: radar workspace not found: $RADAR_WS" >&2
  echo "Set RADAR_WS=/path/to/workspace if your workspace uses another path." >&2
  exit 2
fi

cd "$RADAR_WS"
source /opt/ros/humble/setup.bash
source install/setup.bash

if [ -f "$HOME/sdr_runtime/venv/bin/activate" ]; then
  source "$HOME/sdr_runtime/venv/bin/activate"
fi
export PYTHONPATH="$HOME/sdr_runtime/venv/lib/python3.10/site-packages:${PYTHONPATH:-}"

mkdir -p "$RECORD_DIR" "$PROFILE_DIR"
echo "IQ record dir: $RECORD_DIR"
df -h "$RECORD_DIR"

for candidate in "$PROFILE_DIR/best_profile_${RX_TEAM}.yaml" "$PROFILE_DIR/best_profile.yaml"; do
  if [ -f "$candidate" ]; then
    PROFILE_ARG=("profile_path:=$candidate")
    echo "Using adaptive profile: $candidate"
    break
  fi
done

if [ "${#PROFILE_ARG[@]}" -eq 0 ]; then
  echo "No adaptive profile found; launching without profile_path."
fi

echo "Launching competition receiver: own=$OWN_TEAM rx=$RX_TEAM fallback_self_id=$FALLBACK_SELF_ID match=$MATCH_TAG"
echo "JamCode repeat policy: key_retry_limit=$KEY_RETRY_LIMIT min_interval=${KEY_PUBLISH_MIN_INTERVAL_SEC}s"

exec ros2 launch sdr_receiver_py_wrapper competition_receiver.launch.py \
  max_jam_break_level:=3 \
  key_retry_limit:=$KEY_RETRY_LIMIT \
  key_publish_min_interval_sec:=$KEY_PUBLISH_MIN_INTERVAL_SEC \
  context_topic:=/judge/radar_context \
  enable_fallback_topics:=true \
  fallback_self_id:=$FALLBACK_SELF_ID \
  match_slot:=$MATCH_TAG \
  "${PROFILE_ARG[@]}" \
  record_iq:=true \
  iq_record_dir:=$RECORD_DIR \
  iq_record_prefix:=$MATCH_TAG \
  iq_record_max_sec:=900 \
  iq_record_max_bytes:=17179869184 \
  iq_record_every_n:=1
