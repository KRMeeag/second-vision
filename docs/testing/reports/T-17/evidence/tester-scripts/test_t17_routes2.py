"""Tester T-17 criteria 4 and 7, second batch: second Ctrl+C, SIGTERM/SIGHUP, the chart-only run, all-skipped runs,
two runs in the same second, summary counts against rows."""
import importlib.util, os, re, signal, subprocess, sys, time
import os
from pathlib import Path
import pytest
sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_t17_routes import Tool, alive, cleanup, pids_named, PY, HERE   # reuse the helpers and fixture
from test_t17_routes import tool_factory                                  # noqa: F401  (fixture)

REPO = Path(os.environ.get("T17_REPO", "/home/sv-rpi5/Projects/Second-Vision/worktrees/kenth"))

def test_second_ctrl_c_while_the_tool_waits_for_a_program_that_ignores_sigint(tool_factory):
    t = tool_factory(mode="ignore", extra_env={"STANDIN_SURVIVE_EPIPE": "1"})
    assert t.wait_for("Press Enter")
    t.p.send_signal(signal.SIGINT)          # 1st: the tool starts its 20 s wait
    time.sleep(2)
    t.p.send_signal(signal.SIGINT)          # 2nd: an impatient presenter
    t.finish(timeout=60)
    pid = t.app_pid
    left = alive(pid)
    print("exit code:", t.p.returncode, "| program still alive:", left, "| summary written:", any((t.tmp / "ev").glob("demo-*/summary.md")))
    print(t.out[-1200:])
    assert not left, "the program that ignores SIGINT was left running after a second Ctrl+C"

@pytest.mark.parametrize("sig", [signal.SIGTERM, signal.SIGHUP])
def test_sigterm_or_sighup_of_the_tool_stops_the_program(tool_factory, sig):
    t = tool_factory(mode="good", extra_env={"STANDIN_SURVIVE_EPIPE": "1"})
    assert t.wait_for("Press Enter")
    t.p.send_signal(sig)
    t.finish(timeout=60)
    left = alive(t.app_pid)
    print(sig.name, "-> exit code:", t.p.returncode, "| program still alive:", left)
    assert not left, f"{sig.name} to the tool leaves the program running (holding camera and boards)"

def run_cli(tmp, steps, stdin_text, mode="good", extra=()):
    env = dict(os.environ, STANDIN_MODE=mode, STANDIN_PIDFILE=str(tmp / "app.pid"), PYTHONUNBUFFERED="1")
    p = subprocess.run([PY, str(REPO / "docs/testing/tools/demo_run.py"), "--steps", steps, "--out", str(tmp / "ev"), "--no-preflight",
                        "--app", f"{PY} {HERE / 'standin.py'}", *extra], input=stdin_text, capture_output=True, text=True, env=env, timeout=120)
    return p

def test_chart_only_run_exit_code_and_summary(tmp_path):
    p = run_cli(tmp_path, "chart", "\n\n")
    s = next((tmp_path / "ev").glob("demo-*/summary.md")).read_text()
    print("exit code:", p.returncode); print(s)
    assert p.returncode == 0, "a chart-only run with nothing missed should exit 0"

def test_lens_only_run_with_the_standin_is_a_miss_exit_1(tmp_path):
    p = run_cli(tmp_path, "lens", "\n\n\n\n\n\n\n\n", extra=())
    assert p.returncode == 1          # the stand-in prints no lens lines: 8 s windows x 4 -> MISS
    s = next((tmp_path / "ev").glob("demo-*/summary.md")).read_text()
    m = re.search(r"\*\*(\d+) seen, (\d+) missed, (\d+) skipped\.\*\*", s)
    rows = [l for l in s.splitlines() if l.startswith("| ") and re.match(r"\| \d+ \|", l)]
    res = [l.split("|")[4].strip() for l in rows]
    assert (int(m[1]), int(m[2]), int(m[3])) == (res.count("ok"), res.count("MISSED"), res.count("skipped")), (m.groups(), res)

def test_everything_skipped_by_the_presenter_exit_code_information(tmp_path):
    p = run_cli(tmp_path, "chart,lens", "s\ns\n")
    s = next((tmp_path / "ev").glob("demo-*/summary.md")).read_text()
    print("exit code:", p.returncode); print(s)

def test_q_at_the_first_prompt_exit_code_information(tmp_path):
    p = run_cli(tmp_path, "lens", "q\n")
    print("exit code:", p.returncode)

def test_two_runs_in_the_same_second_do_not_overwrite(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location("demo_run_x", REPO / "docs/testing/tools/demo_run.py")
    demo = importlib.util.module_from_spec(spec); sys.modules["demo_run_x"] = demo; spec.loader.exec_module(demo)
    import datetime as dt
    class Fixed(dt.datetime):
        @classmethod
        def now(cls, tz=None): return cls(2026, 10, 20, 3, 0, 0)
    monkeypatch.setattr(demo, "datetime", Fixed)
    monkeypatch.setenv("STANDIN_PIDFILE", str(tmp_path / "app.pid")); monkeypatch.setenv("STANDIN_MODE", "good")
    args = ["--steps", "chart", "--out", str(tmp_path / "ev"), "--no-preflight", "--app", f"{PY} {HERE / 'standin.py'}"]
    answers = iter(["", "", "", ""])
    out1, out2 = [], []
    demo.run(args, ask=lambda p: next(answers), say=out1.append)
    first = (next((tmp_path / "ev").glob("demo-*")) / "summary.md").read_text()
    with pytest.raises(FileExistsError):
        demo.run(args, ask=lambda p: "", say=out2.append)
    assert (next((tmp_path / "ev").glob("demo-*")) / "summary.md").read_text() == first, "first run's summary changed"
    print("second run in the same second: FileExistsError raised BEFORE anything started; output of 2nd:", out2)


def test_the_presenters_words_are_filed_under_the_right_step(tmp_path):
    p = run_cli_wrapped(tmp_path, "one,two", "\nnote-for-one\n\nnote-for-two\n")
    s = next((tmp_path / "ev").glob("demo-*/summary.md")).read_text()
    assert "- **1. Quick one:** note-for-one" in s and "- **2. Quick two:** note-for-two" in s, s

def test_a_normal_child_in_the_programs_group_is_stopped_too(tool_factory):
    t = tool_factory(mode="fork2")
    assert t.wait_for("Press Enter"); t.p.send_signal(signal.SIGINT); t.finish()
    child = int(Path(str(t.pidfile) + ".child").read_text())
    time.sleep(0.5)
    assert not alive(child), "a child of the program (like espeak-ng) is left running after the tool exited"

def test_lines_the_program_prints_while_it_is_stopping_are_all_kept(tool_factory):
    t = tool_factory(mode="burst_on_exit")
    assert t.wait_for("Press Enter"); t.p.send_signal(signal.SIGINT); t.finish()
    log = (t.folder() / "session.log").read_text()
    assert log.count("[BURST] line ") == 100000, log.count("[BURST] line ")

def run_cli_wrapped(tmp, steps, stdin_text):
    env = dict(os.environ, STANDIN_MODE="good", STANDIN_PIDFILE=str(tmp / "app.pid"), PYTHONUNBUFFERED="1")
    return subprocess.run([PY, str(HERE / "wrap.py"), "--steps", steps, "--out", str(tmp / "ev"), "--no-preflight",
                           "--app", f"{PY} {HERE / 'standin.py'}"], input=stdin_text, capture_output=True, text=True, env=env, timeout=120)


@pytest.mark.parametrize("part,step", [("glasses", "glasses"), ("panel", "panel"), ("earphones", "earphones")])
def test_a_missing_part_skips_only_its_own_step_and_says_why(tmp_path, monkeypatch, part, step):
    spec = importlib.util.spec_from_file_location("demo_run_y", REPO / "docs/testing/tools/demo_run.py")
    demo = importlib.util.module_from_spec(spec); sys.modules["demo_run_y"] = demo; spec.loader.exec_module(demo)
    monkeypatch.setattr(demo, "preflight", lambda env: ([], {part: f"the {part} is missing (test)"}))
    monkeypatch.setenv("STANDIN_PIDFILE", str(tmp_path / "app.pid")); monkeypatch.setenv("STANDIN_MODE", "good")
    quick = demo.Expect(r"^\[TTS\] 'tick'", 2, "a tick")
    monkeypatch.setattr(demo, "STEPS", [demo.Step(k, k, "x", [demo.Action("do " + k, [quick])], needs=(k if k in ("glasses", "panel", "earphones") else ""))
                                       for k in ("normal", "glasses", "earphones", "panel")])
    rc = demo.run(["--out", str(tmp_path / "ev"), "--app", f"{PY} {HERE / 'standin.py'}"], ask=lambda p: "", say=lambda m: None)
    s = next((tmp_path / "ev").glob("demo-*/summary.md")).read_text()
    rows = [l.split("|")[1:-1] for l in s.splitlines() if re.match(r"\| \d+ \|", l)]
    by = {r[1].strip(): r[3].strip() for r in rows}
    assert by[step] == "skipped" and f"the {part} is missing (test)" in s
    assert all(v == "ok" for k, v in by.items() if k != step), by
    assert f"- Missing: {part}" in s
