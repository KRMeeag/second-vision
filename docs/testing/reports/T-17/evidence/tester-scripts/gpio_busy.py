#!/usr/bin/env python3
"""Tester T-17 8a: a REAL busy line. A helper process holds GPIO6 as an INPUT (what the vendor script does); then
perf_sample.ac_power() must return "" (not crash, not "0"), and read normally again once the helper has gone. Read-only."""
import os, signal, subprocess, sys, time
PY = "/home/sv-rpi5/Projects/Second-Vision/worktrees/kenth/my_hailo_env/bin/python"
sys.path.insert(0, "/home/sv-rpi5/Projects/Second-Vision/worktrees/kenth/docs/testing/tools")
import perf_sample as p
helper = subprocess.Popen([PY, "-c", """
import gpiod, time
with gpiod.request_lines('/dev/gpiochip0', consumer='t17-holder', config={6: gpiod.LineSettings(direction=gpiod.line.Direction.INPUT)}) as r:
    print('held', flush=True); time.sleep(30)
"""], stdout=subprocess.PIPE, text=True)
print("helper pid (mine):", helper.pid)
try:
    assert helper.stdout.readline().strip() == "held"
    t = time.time()
    print("ac_power() while another process holds the line:", repr(p.ac_power()), f"({time.time() - t:.3f} s)")
finally:
    helper.send_signal(signal.SIGTERM); helper.wait(10)
print("after the holder exited:", repr(p.ac_power()))
