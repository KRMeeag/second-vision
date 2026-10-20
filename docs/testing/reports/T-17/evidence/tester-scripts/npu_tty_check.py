#!/usr/bin/env python3
"""Tester T-17: perf_npu.capture()'s stdin=DEVNULL fix, red-before-green under a real controlling terminal (a pty).
A = the call as it was before the fix (stdin inherited); B = perf_npu.capture(3) as it is now.  Read-only: `hailortcli monitor`
only reads the monitor files; no sudo (use_sudo False); nothing is written to the Hailo device."""
import os, pty, subprocess, sys, json
from pathlib import Path
result = Path(sys.argv[1])
inner = r'''
import json, subprocess, sys, time
sys.path.insert(0, "/home/sv-rpi5/Projects/Second-Vision/worktrees/kenth/docs/testing/tools")
import perf_npu
cmd = ["timeout", "3", "script", "-qfc", "hailortcli monitor", "/dev/null"]
t = time.time()
try:
    a = subprocess.run(cmd, capture_output=True, text=True, timeout=11).stdout        # before the fix: stdin is the terminal
    a_err = None
except subprocess.TimeoutExpired as e:
    a, a_err = "", "timeout"
ta = time.time() - t
t = time.time()
b = perf_npu.capture(3)
tb = time.time() - t
json.dump({"A_before_fix_len": len(a), "A_before_fix_text": perf_npu.ANSI.sub("", a)[:300], "A_s": round(ta, 1), "A_err": a_err,
           "B_now_len": len(b), "B_now_text": b[:300], "B_s": round(tb, 1)}, open(sys.argv[1], "w"))
'''
pid, fd = pty.fork()
if pid == 0:
    os.execv(sys.executable, [sys.executable, "-c", inner, str(result)])
try:
    while True:
        try:
            if not os.read(fd, 4096): break
        except OSError:
            break
finally:
    os.waitpid(pid, 0)
print(json.dumps(json.load(open(result)), indent=1))
