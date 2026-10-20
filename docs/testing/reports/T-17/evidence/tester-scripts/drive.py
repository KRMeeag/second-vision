#!/usr/bin/env python3
"""Tester (T-17): run docs/testing/tools/demo_run.py against real_app.py (the REAL emitters), with a scripted presenter.

usage: drive.py OUTDIR [--steps a,b] [--presenter at|before|after] [--opts no-earphones] [--pre-actions glasses_off,...]
"""
import argparse, importlib.util, os, sys, threading, time, shlex
import os
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = Path(os.environ.get("T17_REPO", "/home/sv-rpi5/Projects/Second-Vision/worktrees/kenth"))
ap = argparse.ArgumentParser()
ap.add_argument("out"); ap.add_argument("--steps"); ap.add_argument("--presenter", default="at")
ap.add_argument("--opts", default=""); ap.add_argument("--delay", type=float, default=1.5)
ap.add_argument("--suppress", default="", help="trigger names the scripted presenter does NOT perform (a presenter who forgets)")
ap.add_argument("--also", default="", help="extra triggers fired with the FIRST action (e.g. view_dark: a real failsafe alert during step 1)")
ap.add_argument("--before-start", default="", help="triggers fired as soon as the program is up (before step 1)")
a = ap.parse_args()

spec = importlib.util.spec_from_file_location("demo_run", REPO / "docs/testing/tools/demo_run.py")
demo = importlib.util.module_from_spec(spec); sys.modules["demo_run"] = demo; spec.loader.exec_module(demo)

out = Path(a.out).resolve(); out.mkdir(parents=True, exist_ok=True)
trig = out / "triggers"; trig.mkdir(exist_ok=True)
seq = [0]
def fire(name):
    if name in a.suppress.split(","):
        return
    seq[0] += 1
    (trig / f"{seq[0]:03d}-{name}").write_text("")

MAP = [("Point the camera", "detect"), ("Pull the camera", "camera_off"), ("Plug the camera", "camera_on"),
       ("Unplug the glasses", "glasses_off"), ("Plug the glasses", "glasses_on"),
       ("Unplug the earphones", "earphones_off"), ("Plug the earphones", "earphones_on"),
       ("Unplug the control panel", "panel_off"), ("Plug the control panel", "panel_on"),
       ("Cover the lens", "view_dark"), ("Uncover the lens", "view_ok"), ("Hold a phone", "view_bright"),
       ("Take the flash", "view_ok2")]
log = []
def ask(prompt):
    if "Press Enter" not in prompt:
        return "felt as expected"
    line = prompt.splitlines()[0]
    name = next((n for p, n in MAP if p in line), None)
    if name:
        if a.presenter == "at":
            fire(name)
        elif a.presenter == "before":
            fire(name); time.sleep(a.delay)
        elif a.presenter == "after":
            threading.Timer(a.delay, fire, args=(name,)).start()
    if not log:
        for n in [x for x in a.also.split(",") if x]:
            fire(n)
    log.append((time.time(), line.strip(), name))
    return ""

said = []
def say(msg):
    said.append(msg); print(msg, flush=True)

os.environ["PATH"] = f"{HERE / 'bin'}:{os.environ['PATH']}"
for n in [x for x in a.before_start.split(",") if x]:
    pass
app_cmd = f"{sys.executable} {HERE / 'real_app.py'} {trig} {a.opts}"
# triggers to fire once the program is up: write them before the start (the program processes them at once)
for n in [x for x in a.before_start.split(",") if x]:
    fire(n)
argv = ["--out", str(out / "evidence"), "--no-preflight", "--app", app_cmd]
if a.steps:
    argv = ["--steps", a.steps] + argv
t0 = time.time()
rc = demo.run(argv, ask=ask, say=say)
print(f"\nDRIVER: demo_run exit code {rc}, {time.time() - t0:.0f} s wall", flush=True)
sys.exit(rc)
