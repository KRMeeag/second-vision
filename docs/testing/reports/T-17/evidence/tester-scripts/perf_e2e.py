#!/usr/bin/env python3
"""Tester T-17 criterion 8: perf_run.py + perf_sample.py + perf_plot.py end to end with a STAND-IN program (SV_RUN_SH),
a FAKE gauge and charger line (fake smbus2/gpiod on PYTHONPATH, state in a JSON file), a FAKE sudo and grim on PATH.
No real rundown, nothing is driven, no sudo runs, the real gauge is not read by the child tools here.

usage: perf_e2e.py SCENARIO OUTDIR
 scenarios: rundown-volts | rundown-pct | wait-mode | charger-mid | ignore-sigint | no-gauge | refuse
"""
import json, os, signal, subprocess, sys, threading, time
import os
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = Path(os.environ.get("T17_REPO", "/home/sv-rpi5/Projects/Second-Vision/worktrees/kenth"))
PY = "/home/sv-rpi5/Projects/Second-Vision/worktrees/kenth/my_hailo_env/bin/python"
scenario, outdir = sys.argv[1], Path(sys.argv[2]); outdir.mkdir(parents=True, exist_ok=True)
state = outdir / "gauge.json"

def setstate(**kw):
    cur = json.loads(state.read_text()) if state.exists() else {"volts": 3.9, "pct": 50.0, "ac": "0"}
    cur.update(kw); tmp = state.with_suffix(".tmp"); tmp.write_text(json.dumps(cur)); tmp.replace(state)

env = dict(os.environ, PATH=f"{HERE / 'fakebin'}:{os.environ['PATH']}", PYTHONPATH=f"{HERE / 'fake'}",
           FAKE_GAUGE_STATE=str(state), FAKE_SUDO_LOG=str(outdir / "sudo-and-grim-attempts.log"),
           FAKE_GPIOD_LOG=str(outdir / "gpiod-requests.log"),
           SV_RUN_SH=str(HERE / "run_sh_standin.sh"), STANDIN_PIDFILE=str(outdir / "standin.pid"),
           STANDIN_MODE="ignore" if scenario == "ignore-sigint" else "good", PYTHONDONTWRITEBYTECODE="1")
env.pop("DISPLAY", None)
t0 = time.time()
timeline = []
def at(sec, **kw):
    def go():
        time.sleep(sec); setstate(**kw); timeline.append((round(time.time() - t0, 1), kw))
    threading.Thread(target=go, daemon=True).start()

args = [PY, str(REPO / "docs/testing/tools/perf_run.py"), "--label", "t17", "--out", str(outdir / "out")]
if scenario == "rundown-volts":
    setstate(volts=3.9, pct=50.0, ac="0")
    args += ["--duration", "300", "--stop-at-volts", "3.5"]
    at(24, volts=3.45, pct=3.0)                 # falls under the stop voltage before the 3rd sample
elif scenario == "rundown-pct":
    setstate(volts=3.9, pct=50.0, ac="0")
    args += ["--duration", "300", "--stop-at-battery", "5"]
    at(24, pct=3.0)                              # the percentage falls under 5 %; the voltage stays high
elif scenario == "wait-mode":
    setstate(volts=3.9, pct=50.0, ac="1")
    args += ["--duration", "30", "--stop-at-volts", "3.5", "--wait-for-battery"]
    at(8, ac="0")                                # the charger is unplugged 8 s in
elif scenario == "charger-mid":
    setstate(volts=3.45, pct=3.0, ac="0")        # low voltage but then the charger comes in: must NOT stop
    args += ["--duration", "45", "--stop-at-volts", "3.5"]
    at(2.5, ac="1")                              # the first sample (about +13 s) sees low voltage WITH the charger in
elif scenario == "ignore-sigint":
    setstate(volts=3.45, pct=3.0, ac="0")
    args += ["--duration", "300", "--stop-at-volts", "3.5"]
elif scenario == "no-gauge":
    setstate(gauge="missing", ac="busy")
    args += ["--duration", "25"]
elif scenario == "refuse":
    setstate(volts=3.9, pct=50.0, ac="1")
    args += ["--duration", "30", "--stop-at-volts", "3.5"]
else:
    sys.exit("unknown scenario")

log = open(outdir / "perf_run-console.log", "w")
p = subprocess.Popen(args, stdout=log, stderr=subprocess.STDOUT, env=env, cwd=REPO)
print("perf_run pid", p.pid, flush=True)
try:
    rc = p.wait(timeout=400)
except subprocess.TimeoutExpired:
    p.send_signal(signal.SIGINT); rc = p.wait(30)
log.close()
pid = int((outdir / "standin.pid").read_text()) if (outdir / "standin.pid").exists() else None
def alive(pid):
    try: os.kill(pid, 0)
    except ProcessLookupError: return False
    try: return Path(f"/proc/{pid}/stat").read_text().split(")")[1].split()[0] != "Z"
    except OSError: return False
left = bool(pid and alive(pid))
print(json.dumps({"scenario": scenario, "perf_run_exit": rc, "wall_s": round(time.time() - t0), "standin_pid": pid,
                  "standin_left_running": left, "timeline": timeline}), flush=True)
if left:
    os.kill(pid, signal.SIGKILL)       # my own recorded stand-in only
