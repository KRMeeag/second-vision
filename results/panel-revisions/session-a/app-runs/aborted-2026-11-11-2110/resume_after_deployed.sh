#!/bin/bash
# Resume the app runs after run_app_runs.sh was killed (2026-11-11 21:09) during the first run.
# The deployed-model run it had started (perf_run.py, the app and its samplers) kept going on its own.
# This waits for that perf_run.py to exit, stops the chip sampler it no longer needs, analyses the run,
# then runs the remaining models with the cool-down target set to the deployed run's starting
# temperatures, so yolov8m starts no more than 2 C warmer than the deployed model did.
#   setsid nohup bash resume_after_deployed.sh <perf_run pid> <sampler pid> > run-console-2.txt 2>&1 &
set -u
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PERF_PID=$1; SAMPLER_PID=$2
STEM="$HERE/perf-app-deployed-13class-30min"
echo "# resume $(date -Iseconds): waiting for perf_run.py (pid $PERF_PID) to finish the deployed-model run"
while kill -0 "$PERF_PID" 2>/dev/null; do sleep 10; done
kill "$SAMPLER_PID" 2>/dev/null
echo "$(date +%T) deployed run finished; console tail:"; tail -3 "$STEM-perf-run-console.txt"
python3 -I "$HERE/analyse_app_run.py" "$STEM" > /dev/null 2>&1 && echo "analysed $STEM" || echo "analysis FAILED for $STEM"
# The deployed run started at Pi 54.0 C / Hailo 52.5 C (run-console.txt, 21:09:34).
SA_MODELS="yolov8m yolov8n yolov8s yolov8l yolov8x" SA_IDLE_PI=54.0 SA_IDLE_H=52.5 \
  bash "$HERE/run_app_runs.sh"
echo "# resume done $(date -Iseconds)"
