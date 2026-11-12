#!/bin/bash
# Session B3: the 20-minute real-time run on the 13-class model, with the latency log on.
#
#   bash results/panel-revisions/session-b/run_b3.sh standin       # stand-in glasses on a pty, no panel (as 18 Oct)
#   bash results/panel-revisions/session-b/run_b3.sh realboards    # the real glasses board and control panel
#   SA_DURATION=90 bash ... standin                                # a short check
#
# Same protocol as 18 Oct: perf_run.py starts scripts/sv-main.sh as the boot service does (headless, no
# preview), samples every 10 s for the duration and stops the app with Ctrl+C. Added for Session B:
#   SV_LATENCY_LOG=1, SV_LATENCY_DIR=<run>/latency   capture -> motor packet / ACK, capture -> speech start
#   hailo_temp_sampler.py (Session A)                 the Hailo chip temperature every 10 s
#   --no-screenshots                                  PRIVACY (Kenth, 2026-11-11): no image is saved
# Needs second-vision.service STOPPED. With realboards the panel's two rockers must both be ON (mode both).
set -u
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WT="$(cd "$HERE/../../.." && pwd)"
PY="$WT/my_hailo_env/bin/python"
SA="$WT/results/panel-revisions/session-a/app-runs"
MODE="${1:?standin or realboards}"
DUR="${SA_DURATION:-1200}"
case "$MODE" in
  standin)    BOARDS=() ;;
  realboards) BOARDS=(--real-boards) ;;
  *) echo "standin or realboards"; exit 2 ;;
esac
LABEL="b3-${MODE}-$((DUR / 60))min"
OUT="$HERE/$LABEL"; STEM="$OUT/perf-$LABEL"
mkdir -p "$OUT/latency"

if systemctl is-active --quiet second-vision.service; then
  echo "second-vision.service is running: stop it first (sudo systemctl stop second-vision)"; exit 2
fi
echo "# $LABEL start $(date -Iseconds); Pi $(awk '{printf "%.1f", $1/1000}' /sys/class/thermal/thermal_zone0/temp) C"
"$PY" -I "$SA/hailo_temp_sampler.py" --duration $((DUR + 120)) --delay 20 --out "$STEM-hailo-temp.csv" &
sampler=$!
SV_LATENCY_LOG=1 SV_LATENCY_DIR="$OUT/latency" \
  "$PY" "$WT/docs/testing/tools/perf_run.py" --duration "$DUR" --label "$LABEL" --out "$OUT" --no-screenshots \
  "${BOARDS[@]}" > "$STEM-perf-run-console.txt" 2>&1
rc=$?
kill "$sampler" 2>/dev/null; wait "$sampler" 2>/dev/null
echo "# $LABEL perf_run exit $rc $(date -Iseconds)"
python3 -I "$SA/analyse_app_run.py" "$STEM" > /dev/null 2>&1 || echo "  app-run analysis failed"
python3 -I "$HERE/analyse_latency.py" "$OUT" > /dev/null 2>&1 || echo "  latency analysis failed"
echo "# $LABEL done $(date -Iseconds)"
