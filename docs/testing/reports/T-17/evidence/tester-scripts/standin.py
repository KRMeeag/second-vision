#!/usr/bin/env python3
"""Tester stand-in program for demo_run routes. STANDIN_MODE: good | ignore | slow | crash | fork | badutf8 | flood."""
import os, signal, subprocess, sys, time
mode = os.environ.get("STANDIN_MODE", "good")
pidfile = os.environ["STANDIN_PIDFILE"]
open(pidfile, "w").write(str(os.getpid()))
def done(*_):
    if mode == "burst_on_exit":
        for i in range(100000):
            print(f"[BURST] line {i}", flush=True)
    if mode == "slow":
        time.sleep(float(os.environ.get("STANDIN_EXIT_DELAY", "3")))
    print("[MAIN] Done.", flush=True)
    sys.exit(0)
if mode == "ignore":
    signal.signal(signal.SIGINT, signal.SIG_IGN)
else:
    signal.signal(signal.SIGINT, done)
if mode == "fork2":
    child = subprocess.Popen(["sleep", "300"])          # a normal child in the same group: dies on a group SIGINT
    open(pidfile + ".child", "w").write(str(child.pid))
if mode == "fork":
    child = subprocess.Popen(["sh", "-c", "trap '' INT; sleep 300"])
    open(pidfile + ".child", "w").write(str(child.pid))
print("[MAIN] Status worker started", flush=True)
print("[CHECK] Camera view: clear (brightness 0-255: average 111, top 5 %: 187, at white: 0%)", flush=True)
sys.stderr.write("[STDERR] a line on stderr\n"); sys.stderr.flush()
if mode == "badutf8":
    sys.stdout.flush(); sys.stdout.buffer.write(b"[GST] device name caf\xe9 \xff\xfe broken\n"); sys.stdout.buffer.flush()
if mode == "flood":
    signal.pthread_sigmask(signal.SIG_BLOCK, {signal.SIGINT})
    for i in range(int(os.environ.get("STANDIN_FLOOD", "200000"))):
        print(f"[FLOOD] line {i}", flush=True)
    print("[FLOOD] end", flush=True)
    signal.pthread_sigmask(signal.SIG_UNBLOCK, {signal.SIGINT})
t0 = time.time(); n = 0
while True:
    if mode == "crash" and time.time() - t0 > 2:
        sys.exit(3)
    try:
        print("[TTS] 'tick'", flush=True)
    except BrokenPipeError:
        if os.environ.get("STANDIN_SURVIVE_EPIPE") != "1":
            raise
        open(pidfile + ".epipe", "a").write("x")      # a worker thread that catches the exception and goes on
    n += 1
    time.sleep(0.2)
