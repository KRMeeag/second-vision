#!/usr/bin/env python3
"""Tester T-17 device-integration: the REAL tool starting the REAL scripts/sv-main.sh (camera, Hailo, boards) with --steps chart,
answers from a pipe. Checks: it starts, sees `Camera view: clear`, stops the program with exit 0, leaves nothing; and what
`pgrep -af second_vision/main.py` sees of the running program (criterion 5). Only PIDs started here are signalled."""
import importlib.util, os, re, signal, subprocess, sys, time, json
from pathlib import Path
REPO = Path("/home/sv-rpi5/Projects/Second-Vision/worktrees/kenth")
PY = str(REPO / "my_hailo_env/bin/python")
out = Path(sys.argv[1]).resolve(); out.mkdir(parents=True, exist_ok=True)
console = out / "tool-console.log"
env = dict(os.environ, PYTHONUNBUFFERED="1")
t0 = time.time()
tool = subprocess.Popen([PY, str(REPO / "docs/testing/tools/demo_run.py"), "--steps", "chart", "--out", str(out / "evidence")],
                        stdin=subprocess.PIPE, stdout=open(console, "w"), stderr=subprocess.STDOUT, text=True, env=env, cwd=REPO)
print("tool pid", tool.pid, flush=True)
def text(): return console.read_text()
def alive(pid):
    try: os.kill(pid, 0)
    except ProcessLookupError: return False
    try: return Path(f"/proc/{pid}/stat").read_text().split(")")[1].split()[0] != "Z"
    except OSError: return False
res = {"tool_pid": tool.pid}
app = None
try:
    while "Press Enter" not in text():
        if tool.poll() is not None or time.time() - t0 > 170:
            raise RuntimeError("tool ended or timed out before the chart prompt:\n" + text()[-2000:])
        time.sleep(0.5)
    res["seconds_to_prompt"] = round(time.time() - t0, 1)
    kids = subprocess.run(["pgrep", "-P", str(tool.pid)], capture_output=True, text=True).stdout.split()
    app = int(kids[0]) if kids else None
    res["app_pid"] = app
    if app:
        res["app_cmdline_now"] = Path(f"/proc/{app}/cmdline").read_bytes().replace(b"\0", b" ").decode(errors="replace")[:200]
        res["app_pgid_sid"] = os.getpgid(app), os.getsid(app)
        res["app_tree"] = subprocess.run(["ps", "-o", "pid,ppid,pgid,sid,stat,comm", "--ppid", str(app), "-p", str(app)], capture_output=True, text=True).stdout
    pg = subprocess.run(["pgrep", "-af", "second_vision/main.py"], capture_output=True, text=True).stdout.splitlines()
    res["pgrep_second_vision_main_py"] = [l[:140] for l in pg if "pgrep" not in l]
    pg2 = subprocess.run(["pgrep", "-af", "Parallel-Depth"], capture_output=True, text=True).stdout.splitlines()
    res["pgrep_Parallel_Depth"] = [l[:140] for l in pg2 if "pgrep" not in l]
    spec = importlib.util.spec_from_file_location("demo_run_x", REPO / "docs/testing/tools/demo_run.py")
    d = importlib.util.module_from_spec(spec); sys.modules["demo_run_x"] = d; spec.loader.exec_module(d)
    res["preflight_while_the_real_program_runs"] = d.preflight(dict(os.environ))     # read-only
    tool.stdin.write("\n"); tool.stdin.flush(); time.sleep(0.5)
    tool.stdin.write("\n"); tool.stdin.flush(); tool.stdin.close()
    tool.wait(60)
    res["tool_exit"] = tool.returncode
except Exception as e:
    res["error"] = repr(e)
    if tool.poll() is None: tool.send_signal(signal.SIGINT)
    try: tool.wait(40)
    except Exception: pass
finally:
    time.sleep(1)
    if app:
        res["app_alive_after"] = alive(app)
        grp = subprocess.run(["ps", "-eo", "pid,pgid,stat,cmd"], capture_output=True, text=True).stdout.splitlines()
        res["leftover_in_app_group"] = [l[:120] for l in grp if app and re.match(rf"\s*\d+\s+{app}\s", l)]
        if alive(app):
            os.killpg(app, signal.SIGKILL)        # only the group of the pid recorded above
    res["regex_pgrep_leftovers"] = [l[:120] for l in subprocess.run(["pgrep", "-af", r"second_visio[n]/main|sv-mai[n]|Parallel-Dept[h]|demo_ru[n]\.py"], capture_output=True, text=True).stdout.splitlines() if "pgrep" not in l]
    res["wall_s"] = round(time.time() - t0)
print(json.dumps(res, indent=1, default=str))
(out / "result.json").write_text(json.dumps(res, indent=1, default=str))
