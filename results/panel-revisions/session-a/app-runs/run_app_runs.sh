#!/bin/bash
# Session A, part 2: each detection model inside the real app, with depth on, as the boot service runs it.
#
#   bash results/panel-revisions/session-a/app-runs/run_app_runs.sh [--quick]
#
# Needs second-vision.service STOPPED. For each model, in order:
#   1. cool down: at least COOL_MIN s idle, then until the Pi SoC and the Hailo chip are both within 2 C of
#      the idle readings taken at the start (at most COOL_MAX s);
#   2. start hailo_temp_sampler.py (chip temperature every 10 s);
#   3. perf_run.py: starts scripts/sv-main.sh as the service does, headless, with the stand-in glasses
#      board and no panel (mode "both"), and samples the Pi every 10 s for the run's duration. The model is
#      swapped through SV_RUN_SH=run_with_model.sh + SV_DET_HEF; SV_LATENCY_LOG=1 adds the [DET] lines;
#   4. analyse_app_run.py.
# Then the HW-only batch-size check of yolov8n and yolov8s.
# PRIVACY (Kenth, 2026-11-11): no camera frame is saved and perf_run.py takes no screenshots
# (--no-screenshots). Describe the scene in words only.
set -u
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WT="$(cd "$HERE/../../../.." && pwd)"
PY="$WT/my_hailo_env/bin/python"
ZOO="$WT/results/panel-revisions/downloads/model-zoo-v2.17.0/hailo8"
OUT="$HERE"
LONG=1800; SHORT=300; COOL_MIN=180; COOL_MAX=900; BATCH_T=5
if [ "${1:-}" = "--quick" ]; then LONG=90; SHORT=90; COOL_MIN=10; COOL_MAX=30; BATCH_T=2; OUT="$HERE/quick"; fi
mkdir -p "$OUT"
# label  hef  duration
RUNS=("deployed-13class $WT/models/hef_v3/hef_deploy/best.hef $LONG"
      "yolov8m $ZOO/yolov8m.hef $LONG"
      "yolov8n $ZOO/yolov8n.hef $SHORT"
      "yolov8s $ZOO/yolov8s.hef $SHORT"
      "yolov8l $ZOO/yolov8l.hef $SHORT"
      "yolov8x $ZOO/yolov8x.hef $SHORT")
[ "${1:-}" = "--quick" ] && RUNS=("${RUNS[0]}" "${RUNS[1]}")
# SA_MODELS="yolov8m yolov8n ..." runs only those, in the order of RUNS (used to resume after a stop).
if [ -n "${SA_MODELS:-}" ]; then
  keep=(); for r in "${RUNS[@]}"; do for m in $SA_MODELS; do [ "${r%% *}" = "$m" ] && keep+=("$r"); done; done
  RUNS=("${keep[@]}")
fi

if systemctl is-active --quiet second-vision.service; then
  echo "second-vision.service is running: stop it first (sudo systemctl stop second-vision)"; exit 2
fi

pi_temp()    { awk '{printf "%.1f", $1/1000}' /sys/class/thermal/thermal_zone0/temp; }
hailo_temp() { "$PY" -I -c "
from hailo_platform import Device
with Device(Device.scan()[0]) as d:
    t = d.control.get_chip_temperature(); print(f'{max(t.ts0_temperature, t.ts1_temperature):.1f}')" 2>/dev/null || echo nan; }
cooler() { awk -v a="$1" -v b="$2" 'BEGIN{exit !(a <= b + 2.0)}'; }

sleep 20                                   # the chip and the Pi settle after the service stops
# SA_IDLE_PI / SA_IDLE_H fix the cool-down reference (e.g. the temperatures an earlier run started at).
IDLE_PI=${SA_IDLE_PI:-$(pi_temp)}; IDLE_H=${SA_IDLE_H:-$(hailo_temp)}
echo "# app runs start $(date -Iseconds); idle Pi ${IDLE_PI} C, idle Hailo ${IDLE_H} C; long ${LONG}s, short ${SHORT}s"

for run in "${RUNS[@]}"; do
  read -r model hef dur <<< "$run"
  label="app-${model}-$((dur / 60))min"; stem="$OUT/perf-$label"
  # 1. cool down
  waited=0; sleep "$COOL_MIN"; waited=$COOL_MIN
  while [ "$waited" -lt "$COOL_MAX" ]; do
    p=$(pi_temp); h=$(hailo_temp)
    if cooler "$p" "$IDLE_PI" && { [ "$h" = nan ] || cooler "$h" "$IDLE_H"; }; then break; fi
    sleep 15; waited=$((waited + 15))
  done
  echo "$(date +%T) $label: cooled ${waited}s, Pi $(pi_temp) C, Hailo $(hailo_temp) C (idle ${IDLE_PI} / ${IDLE_H})"
  { echo "model: $model"; echo "hef: $hef"; sha256sum "$hef"; echo "duration_s: $dur"
    echo "cooled_s: $waited; at start: Pi $(pi_temp) C, Hailo $(hailo_temp) C; idle reference Pi $IDLE_PI C, Hailo $IDLE_H C"
    echo "launch: SV_RUN_SH=run_with_model.sh SV_DET_HEF=<hef> SV_LATENCY_LOG=1 perf_run.py --duration $dur --label $label"
  } > "$stem-model.txt"
  # 2 + 3. chip sampler beside perf_run
  "$PY" -I "$HERE/hailo_temp_sampler.py" --duration $((dur + 120)) --delay 20 --out "$stem-hailo-temp.csv" &
  sampler=$!
  SV_RUN_SH="$HERE/run_with_model.sh" SV_DET_HEF="$hef" SV_LATENCY_LOG=1 \
    "$PY" "$WT/docs/testing/tools/perf_run.py" --duration "$dur" --label "$label" --out "$OUT" --no-screenshots > "$stem-perf-run-console.txt" 2>&1
  rc=$?
  kill "$sampler" 2>/dev/null; wait "$sampler" 2>/dev/null
  echo "$(date +%T) $label: perf_run exit $rc"
  # 4. analysis
  python3 -I "$HERE/analyse_app_run.py" "$stem" > /dev/null 2>&1 || echo "  analysis failed for $label"
done

echo "# batch-size check (HW-only, direct access, ${BATCH_T}s per mode) $(date -Iseconds)"
mkdir -p "$OUT/batch-check"
for m in yolov8n yolov8s; do
  for b in 1 2 4 8; do
    f="$OUT/batch-check/benchmark-$m-batch$b.txt"
    { echo "# before: Pi $(pi_temp) C, $(sudo -n vcgencmd get_throttled)"
      timeout 120 hailortcli benchmark -t "$BATCH_T" --batch-size "$b" "$ZOO/$m.hef"
      echo "# exit $? after: Pi $(pi_temp) C, $(sudo -n vcgencmd get_throttled)"; } > "$f" 2>&1
    echo "$m batch $b: $(grep -a -E 'hw_only|streaming\)|Latency \(hw\)' "$f" | tr -s ' ' | tr '\n' ' ')"
    sleep 10
  done
done
echo "# app runs done $(date -Iseconds)"
