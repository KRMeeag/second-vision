#!/bin/bash
# SV_RUN_SH replacement: stands in for scripts/run.sh (the program).
exec /home/sv-rpi5/Projects/Second-Vision/worktrees/kenth/my_hailo_env/bin/python "$(dirname "$0")/perf_standin.py" "$@"
