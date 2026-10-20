#!/bin/bash
# usage: run_suite.sh <python> <logfile> [pytest targets/args...]   (default target: tests/)
PY="$1"; LOG="$2"; shift 2
cd /home/sv-rpi5/Projects/Second-Vision/worktrees/kenth || exit 2
TARGET=("$@"); [ ${#TARGET[@]} -eq 0 ] && TARGET=(tests/)
{
  echo "# Tester T-17 attempt 1 evidence"
  echo "# command: nice -n 19 $PY -m pytest ${TARGET[*]} -v -rA"
  echo "# date: $(date '+%Y-%m-%d %H:%M:%S %Z')"
  echo "# git: $(git rev-parse --short HEAD) with uncommitted task changes (git status lists perf_run.py, perf_sample.py, tests/test_perf_tools.py and other slices' files as modified)"
  echo "# battery/charger before start: $(nice -n 19 my_hailo_env/bin/python -I -c "import sys; sys.path.insert(0,'docs/testing/tools'); import perf_sample as p; print(p.battery(), repr(p.ac_power()))")"
  echo "# service: $(systemctl is-active second-vision.service)"
  echo
} > "$LOG"
nice -n 19 "$PY" -m pytest "${TARGET[@]}" -v -rA >> "$LOG" 2>&1
rc=$?
{
  echo
  echo "# exit code: $rc"
  echo "# finished: $(date '+%Y-%m-%d %H:%M:%S %Z')"
  echo "# battery/charger after: $(nice -n 19 my_hailo_env/bin/python -I -c "import sys; sys.path.insert(0,'docs/testing/tools'); import perf_sample as p; print(p.battery(), repr(p.ac_power()))")"
} >> "$LOG"
