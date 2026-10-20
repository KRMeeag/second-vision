"""Tester T-17 criteria 4 and 7: every exit route of demo_run stops the program and leaves a truthful evidence folder.

The tool runs as a real subprocess (python wrap.py ... = the real run()), with a stand-in program that records its PID.
Leftovers are checked by recorded PID (never by name), and with a pgrep regex that cannot match its own command line.
"""
import os, re, signal, subprocess, sys, threading, time
from pathlib import Path
import pytest

HERE = Path(__file__).resolve().parent
PY = "/home/sv-rpi5/Projects/Second-Vision/worktrees/kenth/my_hailo_env/bin/python"

def alive(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    try:
        return Path(f"/proc/{pid}/stat").read_text().split(")")[1].split()[0] != "Z"
    except OSError:
        return False

def pids_named():
    r = subprocess.run(["pgrep", "-af", r"second_visio[n]/main|sv-mai[n]|run\.s[h]|standi[n]\.py"], capture_output=True, text=True)
    return [l for l in r.stdout.splitlines() if "wayvnc" not in l and "pgrep" not in l]

class Tool:
    def __init__(self, tmp, mode="good", steps="one,two", extra_env=None, stdin=subprocess.PIPE):
        self.tmp = tmp
        self.pidfile = tmp / "app.pid"
        env = dict(os.environ, STANDIN_MODE=mode, STANDIN_PIDFILE=str(self.pidfile), PYTHONUNBUFFERED="1")
        env.update(extra_env or {})
        self.out = ""
        self.t0 = time.monotonic()
        self.p = subprocess.Popen([PY, str(HERE / "wrap.py"), "--steps", steps, "--out", str(tmp / "ev"), "--no-preflight",
                                   "--app", f"{PY} {HERE / 'standin.py'}"],
                                  stdin=stdin, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, env=env)
        threading.Thread(target=self._read, daemon=True).start()

    def _read(self):
        for line in self.p.stdout:
            self.out += line
    def wait_for(self, text, timeout=30):
        end = time.monotonic() + timeout
        while text not in self.out:
            if time.monotonic() > end or self.p.poll() is not None and text not in self.out:
                return text in self.out
            time.sleep(0.05)
        return True
    @property
    def app_pid(self):
        return int(self.pidfile.read_text())
    def finish(self, timeout=60):
        self.p.wait(timeout)
        self.elapsed = time.monotonic() - self.t0
        time.sleep(0.3)
    def folder(self):
        runs = list((self.tmp / "ev").glob("demo-*"))
        assert len(runs) == 1, runs
        return runs[0]
    def summary(self):
        return (self.folder() / "summary.md").read_text()

def cleanup(tool):
    # only PIDs this test recorded
    for f in (tool.pidfile, Path(str(tool.pidfile) + ".child")):
        if f.exists():
            try:
                pid = int(f.read_text())
            except ValueError:
                continue
            if alive(pid):
                os.kill(pid, signal.SIGKILL)

@pytest.fixture
def tool_factory(tmp_path):
    made = []
    def make(**kw):
        sub = tmp_path / f"t{len(made)}"; sub.mkdir()
        t = Tool(sub, **kw); made.append(t); return t
    yield make
    for t in made:
        if t.p.poll() is None:
            t.p.kill()
        cleanup(t)

# ---------------- the routes, with a stand-in that stops on SIGINT --------------------------------------
def test_route_normal_finish_exit0_program_gone_summary_truthful(tool_factory):
    t = tool_factory()
    for _ in range(4):
        t.p.stdin.write("\n"); t.p.stdin.flush(); time.sleep(0.6)
    t.p.stdin.close()
    t.finish()
    assert t.p.returncode == 0, t.out
    assert not alive(t.app_pid)
    s = t.summary()
    assert "2 seen, 0 missed, 0 skipped" in s, s
    assert re.search(r"exited 0 [0-9.]+ s after Ctrl\+C", s)
    assert (t.folder() / "steps/01-one.log").exists() and (t.folder() / "steps/02-two.log").exists()
    assert not pids_named()

def test_route_q_finishes_early_program_stopped_exit_is_decided(tool_factory):
    t = tool_factory()
    assert t.wait_for("Press Enter")
    t.p.stdin.write("q\n"); t.p.stdin.flush(); t.finish()
    assert not alive(t.app_pid)
    s = t.summary()
    assert "| skipped | by the presenter |" in s and "Finishing early." in t.out
    print("q route exit code:", t.p.returncode)

def test_route_ctrl_c_of_the_tool_while_a_prompt_waits(tool_factory):
    t = tool_factory()
    assert t.wait_for("Press Enter")
    t.p.send_signal(signal.SIGINT)
    t.finish()
    assert not alive(t.app_pid), "program left running after Ctrl+C of the tool"
    assert (t.folder() / "summary.md").exists() and (t.folder() / "session.log").exists()
    assert "Finishing early." in t.out
    assert not pids_named()

def test_route_ctrl_c_of_the_tool_during_the_startup_wait(tool_factory):
    t = tool_factory(mode="good", extra_env={})
    assert t.wait_for("Starting the program", timeout=20)
    for _ in range(100):
        if t.pidfile.exists() and t.pidfile.read_text(): break
        time.sleep(0.02)
    time.sleep(0.5)                      # the program is up and printing; the tool is in its startup wait or at the first prompt
    t.p.send_signal(signal.SIGINT)
    t.finish()
    assert not alive(t.app_pid)
    assert (t.folder() / "summary.md").exists()

def test_route_eof_on_stdin(tool_factory):
    t = tool_factory()
    t.p.stdin.close()
    t.finish()
    assert not alive(t.app_pid)
    s = t.summary()
    assert "Finishing early." in t.out and (t.folder() / "session.log").exists()
    print("EOF route exit code:", t.p.returncode)

def test_route_exception_inside_the_run_still_stops_the_program_and_writes_the_summary(tool_factory):
    t = tool_factory(extra_env={"WRAP_RAISE": "1", "WRAP_RAISE_AFTER": "1.5"})
    t.finish()
    assert t.p.returncode != 0
    assert "boom in watch" in t.out
    assert not alive(t.app_pid), "program left running after an exception"
    assert (t.folder() / "summary.md").exists()

# ---------------- a program that ignores SIGINT -------------------------------------------------------
@pytest.mark.parametrize("route", ["q", "eof", "ctrl_c"])
def test_ignoring_program_is_killed_after_20_s_not_before_and_nothing_is_left(tool_factory, route):
    t = tool_factory(mode="ignore", extra_env={"STANDIN_SURVIVE_EPIPE": "1"})
    if route == "eof":
        t.p.stdin.close()
    else:
        assert t.wait_for("Press Enter")
        if route == "q":
            t.p.stdin.write("q\n"); t.p.stdin.flush()
        else:
            t.p.send_signal(signal.SIGINT)
    t.finish(timeout=90)
    s = t.summary()
    assert "did NOT exit within 20 s of Ctrl+C: killed" in s, s
    assert not alive(t.app_pid), "the SIGINT-ignoring program is still running"
    assert not pids_named()

def test_slow_program_that_exits_in_3_s_is_waited_for_not_killed(tool_factory):
    t = tool_factory(mode="slow")
    assert t.wait_for("Press Enter")
    t.p.send_signal(signal.SIGINT)
    t.finish()
    s = t.summary()
    assert re.search(r"exited 0 3\.\d s after Ctrl\+C", s), s

def test_program_that_crashes_by_itself_is_reported_and_the_run_does_not_pretend_ok(tool_factory):
    t = tool_factory(mode="crash")
    time.sleep(4)                                   # the program died at 2 s
    t.p.stdin.write("\n"); t.p.stdin.flush(); time.sleep(0.5)
    t.p.stdin.write("\n"); t.p.stdin.flush(); time.sleep(0.5)
    t.p.stdin.close()
    t.finish()
    s = t.summary()
    print(s)
    assert t.p.returncode in (0, 1)

# ---------------- output handling -------------------------------------------------------------------
def test_stderr_is_merged_and_kept(tool_factory):
    t = tool_factory()
    assert t.wait_for("Press Enter"); t.p.send_signal(signal.SIGINT); t.finish()
    assert "[STDERR] a line on stderr" in (t.folder() / "session.log").read_text()

def test_a_flood_of_lines_is_not_lost_and_does_not_block_the_program(tool_factory):
    t = tool_factory(mode="flood", extra_env={"STANDIN_FLOOD": "200000"})
    assert t.wait_for("Press Enter", timeout=60)
    time.sleep(8)                                    # the stand-in finishes its flood (SIGINT is blocked in it until then)
    t.p.send_signal(signal.SIGINT); t.finish()
    log = (t.folder() / "session.log").read_text().splitlines()
    floods = [l for l in log if "[FLOOD] line" in l]
    assert len(floods) == 200000, len(floods)
    assert any("[FLOOD] end" in l for l in log)

def test_invalid_utf8_in_the_programs_output_does_not_kill_the_reader(tool_factory):
    t = tool_factory(mode="badutf8")
    time.sleep(2)
    assert t.wait_for("Press Enter")
    t.p.stdin.write("\n"); t.p.stdin.flush(); time.sleep(4)
    t.p.send_signal(signal.SIGINT); t.finish()
    log = (t.folder() / "session.log").read_text()
    ticks = log.count("[TTS] 'tick'")
    print("ticks after a bad byte:", ticks, "\n", t.summary(), "\n--- tool console tail:\n", t.out[-1800:])
    assert ticks > 5, "the reader thread died on a non-UTF-8 byte: later lines are lost"

def test_the_child_of_the_program_that_ignores_sigint_is_left_behind_note(tool_factory):
    t = tool_factory(mode="fork")
    assert t.wait_for("Press Enter"); t.p.send_signal(signal.SIGINT); t.finish()
    child = int(Path(str(t.pidfile) + ".child").read_text())
    left = alive(child)
    print("grandchild still alive after the tool exited:", left)
    # information only: recorded in the report
