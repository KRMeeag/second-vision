#!/bin/bash
# usage: evlog.sh <logfile> <workdir> <command...>   -- writes header + real output of the command
LOG="$1"; WD="$2"; shift 2
{
  echo "# Tester T-17 attempt 1 evidence"
  echo "# command (cwd $WD): $*"
  echo "# date: $(date '+%Y-%m-%d %H:%M:%S %Z')"
  echo "# git: $(git -C /home/sv-rpi5/Projects/Second-Vision/worktrees/kenth rev-parse --short HEAD) with uncommitted task changes (perf_run.py, perf_sample.py, tests/test_perf_tools.py)"
  echo "# service: $(systemctl is-active second-vision.service)   battery/charger: $(nice -n 19 /home/sv-rpi5/Projects/Second-Vision/worktrees/kenth/my_hailo_env/bin/python -I -c "import sys; sys.path.insert(0,'/home/sv-rpi5/Projects/Second-Vision/worktrees/kenth/docs/testing/tools'); import perf_sample as p; print(p.battery(), repr(p.ac_power()))")"
  echo
} > "$LOG"
cd "$WD" && "$@" >> "$LOG" 2>&1
rc=$?
{ echo; echo "# exit code: $rc"; echo "# finished: $(date '+%Y-%m-%d %H:%M:%S %Z')"; } >> "$LOG"
exit $rc
