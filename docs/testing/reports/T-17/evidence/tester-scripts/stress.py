#!/usr/bin/env python3
"""Tester T-17: how often does the program's own stdout interleave two prints (so a line no longer starts with its tag)?
Runs the REAL emitters (real_app.py) through repeated demo events with the same PIPE and PYTHONUNBUFFERED=1 the tool uses,
and counts lines that have a second record tag glued in the middle."""
import os, re, signal, subprocess, sys, threading, time
from pathlib import Path
HERE = Path(__file__).resolve().parent
PY = "/home/sv-rpi5/Projects/Second-Vision/worktrees/kenth/my_hailo_env/bin/python"
out = Path(sys.argv[1]).resolve(); out.mkdir(parents=True, exist_ok=True)
passes = int(sys.argv[2]) if len(sys.argv) > 2 else 3
trig = out / "triggers"; trig.mkdir(exist_ok=True)
env = dict(os.environ, PYTHONUNBUFFERED="1", PATH=f"{HERE / 'bin'}:{os.environ['PATH']}", STUB_SPEAK_SECONDS="1")
p = subprocess.Popen([PY, str(HERE / "real_app.py"), str(trig)], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, env=env, start_new_session=True)
lines = []
def rd():
    for l in p.stdout: lines.append(l.rstrip("\n"))
threading.Thread(target=rd, daemon=True).start()
n = [0]
def fire(x, wait):
    n[0] += 1; (trig / f"{n[0]:04d}-{x}").write_text(""); time.sleep(wait)
time.sleep(4)
for _ in range(passes):
    for x, w in (("camera_off", 12), ("camera_on", 5), ("glasses_off", 5), ("glasses_on", 4), ("earphones_off", 4), ("earphones_on", 3),
                 ("view_dark", 5), ("view_ok", 5), ("view_bright", 5), ("view_ok", 5), ("detect", 3)):
        fire(x, w)
os.killpg(p.pid, signal.SIGINT); p.wait(30)
TAG = re.compile(r"\[(TTS|PATTERN|CHECK|ALERT|SERIAL|CONFIG|EARPHONES|VIEW|MAIN)\]")
glued = [l for l in lines if len(TAG.findall(l)) > 1 and not l.startswith("[CHECK] Camera: connected |")]
tags = sum(len(TAG.findall(l)) for l in lines)
print(f"lines {len(lines)}, records {tags}, lines with a second tag glued on: {len(glued)}")
for l in glued: print("GLUED:", l[:220])
# the tool's own anchored patterns: would they find each record? count records that are not at a line start
mid = sum(1 for l in lines for m in TAG.finditer(l) if m.start() != 0 and not l.startswith("[CHECK] Camera: connected |"))
print("records not at the start of their line:", mid)
(out / "stdout.log").write_text("\n".join(lines) + "\n")
