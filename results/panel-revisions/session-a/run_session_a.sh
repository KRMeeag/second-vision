#!/bin/bash
# Session A: YOLOv8 sizes on this RPi 5 + Hailo-8, each alone and each beside SC-DepthV3.
#
#   bash results/panel-revisions/session-a/run_session_a.sh [--quick]
#
# Needs second-vision.service STOPPED (this script refuses otherwise) and nothing else on the chip.
# For every repetition (3), every model in turn:
#   benchmark: hailortcli benchmark <hef>                      (FPS hw_only / streaming, HW latency)
#   with depth: hailortcli run2 -t 60 set-net <hef> set-net scdepthv3.hef  (FPS per network)
# Before and after each run: SoC temperature (kernel and vcgencmd), throttle flags, fan. A run whose
# throttle flags show current throttling, or a new since-boot flag, is kept as "-discarded" and repeated
# (up to 2 more attempts). ~30 s idle after every run. Models are interleaved within a repetition so slow
# drift (heat, time) is spread over all of them.
#
# Device access: `benchmark` opens the chip directly (its HW-only mode fails through hailort_service:
# "get_input_streams_by_network ... not supported when using multi-process service"; the service stays
# running but idle). `run2` goes through hailort_service (--multi-process-service), the way the app reaches
# the chip, unless SA_ACCESS=direct. run2 takes no latency flags with two networks ("When latency
# measurement is enabled, only one model is allowed"), so the with-depth runs give FPS only. Outputs: raw/<run>.txt (full CLI output + the before/after readings),
# runs.csv (one row per attempt). parse_session_a.py builds matrix.csv from them.
set -u
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WT="$(cd "$HERE/../../.." && pwd)"
ZOO="$WT/results/panel-revisions/downloads/model-zoo-v2.17.0/hailo8"
DEPTH=/usr/local/hailo/resources/models/hailo8/scdepthv3.hef
DEPLOYED="$WT/models/hef_v3/hef_deploy/best.hef"
RAW="$HERE/raw"; CSV="$HERE/runs.csv"
REPS="${SA_REPS:-3}"; IDLE="${SA_IDLE:-30}"; RUN2_T="${SA_RUN2_T:-60}"; BENCH_T="${SA_BENCH_T:-15}"
ACCESS="${SA_ACCESS:-service}"     # for run2 only
if [ "${1:-}" = "--quick" ]; then REPS=1; IDLE=5; RUN2_T=5; BENCH_T=3; RAW="$HERE/raw-quick"; CSV="$HERE/runs-quick.csv"; fi
MP=(); [ "$ACCESS" = "service" ] && MP=(--multi-process-service)

declare -A HEF=([yolov8n]="$ZOO/yolov8n.hef" [yolov8s]="$ZOO/yolov8s.hef" [yolov8m]="$ZOO/yolov8m.hef"
                [yolov8l]="$ZOO/yolov8l.hef" [yolov8x]="$ZOO/yolov8x.hef" [deployed-13class]="$DEPLOYED"
                [scdepthv3]="$DEPTH")
ORDER=(yolov8n yolov8s yolov8m yolov8l yolov8x deployed-13class)

if systemctl is-active --quiet second-vision.service; then
  echo "second-vision.service is running: stop it first (sudo systemctl stop second-vision)"; exit 2
fi
if pgrep -f Parallel-Depth-Detection >/dev/null || pgrep -f 'second_vision/main.py' >/dev/null; then
  echo "another copy of the app is running:"; pgrep -af 'Parallel-Depth-Detection|second_vision/main.py'; exit 2
fi
mkdir -p "$RAW"
[ -f "$CSV" ] || echo "run_id,rep,attempt,kind,model,access,start_iso,end_iso,exit_code,temp_before_c,temp_after_c,vc_temp_before,vc_temp_after,throttled_before,throttled_after,throttle_verdict,fan_before,fan_after,raw_file" > "$CSV"

readings() {  # temp_c vcgencmd_temp throttled fan
  local t; t=$(awk '{printf "%.1f", $1/1000}' /sys/class/thermal/thermal_zone0/temp)
  local vt; vt=$(sudo -n vcgencmd measure_temp 2>&1 | sed -E "s/temp=([0-9.]+)'C/\1/")
  local th; th=$(sudo -n vcgencmd get_throttled 2>&1 | sed 's/throttled=//')
  local fan; fan=$(cat /sys/class/hwmon/*/fan1_input 2>/dev/null | head -1)
  echo "$t $vt $th $fan"
}

verdict() {  # before after -> ok | throttled
  local b=$(( $1 )) a=$(( $2 ))
  if (( (a & 0xF) != 0 )) || (( ((a & ~b) & 0xF0000) != 0 )); then echo throttled; else echo ok; fi
}

one_run() {  # kind model rep attempt
  local kind=$1 model=$2 rep=$3 attempt=$4 hef=${HEF[$2]}
  local id="${kind}-${model}-r${rep}-a${attempt}" out="$RAW/${kind}-${model}-r${rep}-a${attempt}.txt"
  local cmd
  if [ "$kind" = benchmark ]; then
    cmd=(hailortcli benchmark -t "$BENCH_T" "$hef")
  else
    cmd=(hailortcli run2 -t "$RUN2_T" "${MP[@]}"
         -j "$RAW/${kind}-${model}-r${rep}-a${attempt}.json"
         set-net "$hef" set-net "$DEPTH")
  fi
  read -r t0 vt0 th0 fan0 < <(readings)
  local s; s=$(date -Iseconds)
  { echo "# $id"; echo "# command: ${cmd[*]}"; echo "# before: thermal_zone0=${t0}C vcgencmd=${vt0}C throttled=${th0} fan=${fan0}rpm start=$s"; } > "$out"
  local tmo=$(( RUN2_T + 180 )); [ "$kind" = benchmark ] && tmo=$(( BENCH_T * 3 + 180 ))
  timeout "$tmo" "${cmd[@]}" >> "$out" 2>&1
  local rc=$?
  local e; e=$(date -Iseconds)
  read -r t1 vt1 th1 fan1 < <(readings)
  local v; v=$(verdict "$th0" "$th1")
  echo "# after: thermal_zone0=${t1}C vcgencmd=${vt1}C throttled=${th1} fan=${fan1}rpm end=$e exit=$rc verdict=$v" >> "$out"
  local acc=$ACCESS; [ "$kind" = benchmark ] && acc=direct
  echo "$id,$rep,$attempt,$kind,$model,$acc,$s,$e,$rc,$t0,$t1,$vt0,$vt1,$th0,$th1,$v,$fan0,$fan1,$(basename "$out")" >> "$CSV"
  printf '%s  %-10s %-17s rep %s try %s  exit %s  %s->%s C  %s\n' "$(date +%T)" "$kind" "$model" "$rep" "$attempt" "$rc" "$t0" "$t1" "$v"
  [ "$v" = ok ] && [ $rc -eq 0 ]
}

run_with_retry() {  # kind model rep
  local a
  for a in 1 2 3; do
    if one_run "$1" "$2" "$3" "$a"; then sleep "$IDLE"; return 0; fi
    local last="$RAW/$1-$2-r$3-a$a.txt"; mv "$last" "${last%.txt}-discarded.txt"
    [ -f "${last%.txt}.json" ] && mv "${last%.txt}.json" "${last%.txt}-discarded.json"
    sed -i "s#,$(basename "$last")\$#,$(basename "${last%.txt}-discarded.txt")#" "$CSV"
    sleep "$IDLE"
  done
  echo "  gave up on $1 $2 rep $3 after 3 attempts"
}

echo "# Session A start $(date -Iseconds) access=$ACCESS reps=$REPS idle=${IDLE}s run2=${RUN2_T}s bench=${BENCH_T}s/mode"
for rep in $(seq 1 "$REPS"); do
  run_with_retry benchmark scdepthv3 "$rep"           # depth alone: reference for "depth FPS with det"
  for m in "${ORDER[@]}"; do
    run_with_retry benchmark "$m" "$rep"
    run_with_retry with-depth "$m" "$rep"
  done
done
echo "# Session A done $(date -Iseconds)"
