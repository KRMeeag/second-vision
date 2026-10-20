#!/usr/bin/env python3
"""Tester stand-in 'program' for perf_run.py (via SV_RUN_SH): prints [DEPTH] Frame lines, exits 0 on SIGINT."""
import os, signal, sys, time
pidfile = os.environ.get("STANDIN_PIDFILE")
if pidfile:
    open(pidfile, "w").write(str(os.getpid()))
mode = os.environ.get("STANDIN_MODE", "good")
if mode == "ignore":
    signal.signal(signal.SIGINT, signal.SIG_IGN)
else:
    def stop(*_):
        print("[MAIN] Done.", flush=True); sys.exit(0)
    signal.signal(signal.SIGINT, stop)
print("[STANDIN] args:", " ".join(sys.argv[1:]), flush=True)
n = 0
while True:
    n += 15
    print(f"[DEPTH] Frame {n} | depth 29.0 FPS / det 29.0 FPS | lat 100 ms", flush=True)
    time.sleep(0.5)
