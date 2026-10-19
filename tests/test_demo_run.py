"""F8's demo tool (docs/testing/tools/demo_run.py): the sentences it listens for, its step engine, and whole runs
against a stand-in program that prints the real program's lines when the "presenter" acts."""
import importlib.util
import io
import re
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
SRC = REPO / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

_spec = importlib.util.spec_from_file_location("demo_run", REPO / "docs/testing/tools/demo_run.py")
demo = importlib.util.module_from_spec(_spec)
sys.modules["demo_run"] = demo
_spec.loader.exec_module(demo)

FAKE_APP = r'''
import os, signal, sys, time
triggers = sys.argv[1]
script = {
    "1": ["[CHECK] Camera: NOT CONNECTED", "[TTS] 'Critical Error: Camera cannot be reached'",
          "[PATTERN] playing 'camera'", "[TTS] 'Critical Error: Camera cannot be reached'"],
    "2": ["[CHECK] Camera: connected", "[TTS] 'Camera has been reconnected'"],
}
def stop(*_):
    print("[MAIN] Done.", flush=True)
    sys.exit(0)
signal.signal(signal.SIGINT, stop)
print("[MAIN] Status worker started", flush=True)
print("[CHECK] Camera: connected | Camera view: not checked yet", flush=True)
print("[CHECK] Camera view: clear (brightness 0-255: average 111, top 5 %: 187, at white: 0%)", flush=True)
done = set()
while True:
    for key, lines in script.items():
        if key not in done and os.path.exists(os.path.join(triggers, key)):
            done.add(key)
            for line in lines:
                print(line, flush=True)
                time.sleep(0.05)
    time.sleep(0.05)
'''


class Presenter:
    """Answers the tool's prompts: each "Press Enter" is an action (it drops a trigger file the stand-in
    program reacts to); the question about what was felt gets `note`."""

    def __init__(self, triggers: Path, answers=(), note="felt two long pulses"):
        self.triggers, self.answers, self.note = triggers, list(answers), note
        self.actions = 0
        self.prompts = []

    def __call__(self, prompt: str) -> str:
        self.prompts.append(prompt)
        if "Press Enter" in prompt:
            answer = self.answers.pop(0) if self.answers else ""
            if answer == "":
                self.actions += 1
                (self.triggers / str(self.actions)).touch()
            return answer
        return self.note


def _book(*lines):
    book = demo.LogBook()
    for t, line in lines:
        book.add(t, line)
    return book


def _run(tmp_path, steps, presenter, extra=()):
    triggers = tmp_path / "triggers"
    triggers.mkdir(exist_ok=True)
    fake = tmp_path / "fake_app.py"
    fake.write_text(FAKE_APP)
    said = []
    rc = demo.run(["--steps", steps, "--out", str(tmp_path / "ev"), "--no-preflight",
                   "--app", f"{sys.executable} {fake} {triggers}", *extra], ask=presenter, say=said.append)
    runs = list((tmp_path / "ev").iterdir())
    assert len(runs) == 1, runs
    return rc, runs[0], "\n".join(said)


# ============================================================
# The sentences are the program's own
# ============================================================
def test_the_sentences_it_listens_for_are_the_programs_own():
    from second_vision.core import camera_view, connections
    assert demo.GLASSES_MISSING_TEXT == connections.GLASSES_MISSING_TEXT
    assert demo.PANEL_MISSING_TEXT == connections.PANEL_MISSING_TEXT
    assert demo.EVERYTHING_CONNECTED_TEXT == connections.EVERYTHING_CONNECTED_TEXT
    assert demo.VIEW_DARK_TEXT == camera_view.VIEW_DARK_TEXT
    assert demo.VIEW_BRIGHT_TEXT == camera_view.VIEW_BRIGHT_TEXT
    assert demo.VIEW_BACK_TEXT == camera_view.VIEW_BACK_TEXT
    app = (SRC / "second_vision/pipeline/app.py").read_text()       # read, not imported: it needs GStreamer
    for name in ("CAMERA_MISSING_TEXT", "CAMERA_BACK_TEXT"):
        m = re.search(rf'^{name} = "(.*)"$', app, re.M)
        assert m and m.group(1) == getattr(demo, name), name


def test_the_speech_pattern_matches_the_speech_workers_line_and_nothing_longer():
    pattern = demo.said("Camera view is back.")
    assert re.search(pattern, "[TTS] 'Camera view is back.'")
    assert not re.search(pattern, "[TTS] 'Camera view is back. Again'")
    assert not re.search(pattern, "[ALERT] raised 'camera-view-back': 'Camera view is back.' (once)")


def test_the_status_patterns_match_the_status_reporters_lines():
    """The [CHECK] lines are the status board's (core/status_board.py), printed as they change."""
    from second_vision.core.status_board import read_status, StatusReporter
    from second_vision.core.alerts import AlertScheduler
    from second_vision.core.connections import CAMERA_MISSING, GLASSES_MISSING, EARPHONES_MISSING, PANEL_MISSING
    from second_vision.core.priority import PriorityMailbox

    alerts = AlertScheduler(PriorityMailbox())
    reporter = StatusReporter(out=io.StringIO(), color=False)
    printed = reporter.report(read_status(alerts))                  # no config: every part counts as in use
    for name in (CAMERA_MISSING, GLASSES_MISSING, EARPHONES_MISSING, PANEL_MISSING):
        alerts.raise_alert(name, "x", speak=False)
    printed += reporter.report(read_status(alerts))
    lost = [e for s in demo.STEPS for a in s.actions for e in a.expect
            if e.pattern.startswith(r"^\[CHECK\]") and re.search("NOT CONNECTED|NOT RESPONDING|MISSING", e.pattern)]
    assert len(lost) == 4
    for e in lost:
        assert any(re.search(e.pattern, line) for line in printed), (e.pattern, printed)
    connected = [e for s in demo.STEPS for a in s.actions for e in a.expect if e.pattern == r"^\[CHECK\] Camera: connected"]
    assert connected and not any(re.search(connected[0].pattern, line) for line in printed[1:]), \
        "the camera-back pattern must not match the line that says it is lost"


# ============================================================
# The step engine
# ============================================================
def test_a_line_counts_with_its_delay_from_the_enter():
    book = _book((100.5, "[TTS] 'Hello'"))
    [r] = demo.watch(book, 100.0, 0, [demo.Expect(r"^\[TTS\] 'Hello'", 5, "hello")], clock=lambda: 200.0)
    assert r.ok and r.hits == [0.5] and r.when() == "0.5 s"


def test_a_repeat_needs_both_lines():
    one = _book((101.0, "[TTS] 'A'"))
    two = _book((101.0, "[TTS] 'A'"), (111.0, "[TTS] 'A'"))
    e = demo.Expect(r"'A'", 25, "repeated", count=2)
    assert not demo.watch(one, 100.0, 0, [e], clock=lambda: 200.0)[0].ok
    [r] = demo.watch(two, 100.0, 0, [e], clock=lambda: 200.0)
    assert r.ok and r.when() == "11.0 s"


def test_an_absent_line_passes_only_when_it_never_comes():
    e = demo.Expect(r"^\[ALERT\] raised", 15, "no alert", absent=True)
    assert demo.watch(_book((101.0, "[TTS] 'person'")), 100.0, 0, [e], clock=lambda: 200.0)[0].ok
    [r] = demo.watch(_book((103.0, "[ALERT] raised 'x'")), 100.0, 0, [e], clock=lambda: 200.0)
    assert not r.ok and r.when() == "seen at 3.0 s"


def test_a_line_before_the_enter_counts_and_one_after_the_window_does_not():
    """The presenter may pull the cable before pressing Enter: the camera alert is said at once."""
    book = _book((99.2, "[TTS] 'early'"), (130.0, "[TTS] 'late'"))
    early, late = demo.watch(book, 100.0, 0, [demo.Expect("early", 10, "e"), demo.Expect("late", 10, "l")],
                             clock=lambda: 200.0)
    assert early.ok and early.when() == "-0.8 s"
    assert not late.ok


def test_lines_from_before_the_prompt_do_not_count():
    book = _book((99.0, "[TTS] 'old'"), (101.0, "[TTS] 'new'"))
    old, new = demo.watch(book, 100.0, 1, [demo.Expect("old", 10, "o"), demo.Expect("new", 10, "n")],
                          clock=lambda: 200.0)
    assert not old.ok and new.ok


def test_a_step_that_went_well_moves_on_at_once():
    book = demo.LogBook()
    threading.Timer(0.1, lambda: book.add(time.monotonic(), "[TTS] 'done'")).start()
    t = time.monotonic()
    [r] = demo.watch(book, t, 0, [demo.Expect("done", 30, "d")])
    assert r.ok and time.monotonic() - t < 2.0


def test_a_step_never_waits_past_its_longest_window():
    t = time.monotonic()
    [r] = demo.watch(demo.LogBook(), t, 0, [demo.Expect("never", 0.5, "n")])
    assert not r.ok and time.monotonic() - t < 1.5


def test_the_sequence_lists_every_step_and_action():
    out = []
    assert demo.run(["--list"], say=out.append) == 0
    table = "\n".join(out)
    for step in demo.STEPS:
        assert step.title in table
        for action in step.actions:
            assert action.do in table


def test_an_unknown_step_is_refused_with_the_list_of_steps():
    out = []
    assert demo.run(["--steps", "camera,nonsense"], say=out.append) == 2
    assert "nonsense" in out[0] and "camera" in out[0]


# ============================================================
# Whole runs, against a stand-in program
# ============================================================
def test_a_whole_step_against_the_stand_in_program(tmp_path):
    presenter = Presenter(tmp_path / "triggers")
    rc, run_dir, said = _run(tmp_path, "camera", presenter)
    summary = (run_dir / "summary.md").read_text()
    assert rc == 0, summary
    assert "6 seen, 0 missed, 0 skipped" in summary, summary
    assert "felt two long pulses" in summary
    assert "exited 0" in summary, "the program is stopped with Ctrl+C and exits cleanly"
    step_log = (run_dir / "steps/01-camera.log").read_text()
    assert "Critical Error: Camera cannot be reached" in step_log and "Camera has been reconnected" in step_log
    session = (run_dir / "session.log").read_text()
    assert "[MAIN] Done." in session
    assert re.search(r"^\d\d:\d\d:\d\d\.\d{3}  \+ *\d+\.\d\d  \[MAIN\] Status worker started$", session, re.M), session[:300]
    assert "[CHECK] Camera: NOT CONNECTED" in said, "the important lines are shown live"


class PullsFirst(Presenter):
    """Pulls the cable, waits a moment, then presses Enter: the program has already reacted."""

    def __call__(self, prompt: str) -> str:
        answer = super().__call__(prompt)
        if "Press Enter" in prompt:
            time.sleep(0.8)
        return answer


def test_an_unplug_noticed_before_the_enter_still_counts(tmp_path):
    rc, run_dir, _ = _run(tmp_path, "camera", PullsFirst(tmp_path / "triggers"))
    summary = (run_dir / "summary.md").read_text()
    assert rc == 0 and "6 seen, 0 missed" in summary, summary
    assert re.search(r"NOT CONNECTED: -0\.\d s", summary), "a reaction before the Enter shows as a negative time"


def test_a_missed_line_is_reported_and_the_run_fails(tmp_path, monkeypatch):
    step = demo.Step("quiet", "Nothing happens", "-", [demo.Action("Do nothing.", [demo.Expect("^never$", 0.5, "x")])])
    monkeypatch.setattr(demo, "STEPS", [step])
    rc, run_dir, said = _run(tmp_path, "quiet", Presenter(tmp_path / "triggers"))
    summary = (run_dir / "summary.md").read_text()
    assert rc == 1
    assert "0 seen, 1 missed" in summary and "| MISSED |" in summary
    assert "MISS x: not seen" in said


def test_the_presenter_can_skip_a_step_or_finish_early_and_the_program_is_still_stopped(tmp_path):
    presenter = Presenter(tmp_path / "triggers", answers=["s", "q"])
    rc, run_dir, said = _run(tmp_path, "camera,lens", presenter)
    summary = (run_dir / "summary.md").read_text()
    assert summary.count("| skipped | by the presenter |") == 2, "one row for the skip, one for the early finish"
    assert "MISSED" not in summary, "a skipped step is not watched"
    assert "| 1 | Camera out and back | Pull the camera's USB cable. | skipped |" in summary
    assert "Finishing early." in said
    assert "exited 0" in summary
    assert presenter.actions == 0, "a skip is not an action"


def test_a_step_whose_part_is_missing_is_skipped_and_says_why(tmp_path, monkeypatch):
    monkeypatch.setattr(demo, "preflight", lambda env: ([], {"panel": "the control panel's port is missing"}))
    triggers = tmp_path / "triggers"
    triggers.mkdir()
    fake = tmp_path / "fake_app.py"
    fake.write_text(FAKE_APP)
    out = []
    demo.run(["--steps", "panel", "--out", str(tmp_path / "ev"), "--app", f"{sys.executable} {fake} {triggers}"],
             ask=Presenter(triggers), say=out.append)
    summary = next((tmp_path / "ev").iterdir()).joinpath("summary.md").read_text()
    assert "| skipped | the control panel's port is missing |" in summary


# ============================================================
# Before it starts
# ============================================================
def _fake_system(service="inactive", others=""):
    def run(cmd, **kw):
        out = service if cmd[0] == "systemctl" else others
        return subprocess.CompletedProcess(cmd, 0, stdout=out, stderr="")
    return run


def test_it_refuses_while_the_boot_service_runs(monkeypatch, tmp_path):
    monkeypatch.setattr(demo.subprocess, "run", _fake_system(service="active\n"))
    problems, _ = demo.preflight({"INPUT": str(tmp_path)})
    assert any("sudo systemctl stop second-vision" in p for p in problems)


def test_it_refuses_while_another_copy_runs_and_without_a_camera(monkeypatch, tmp_path):
    monkeypatch.setattr(demo.subprocess, "run", _fake_system(others="4242 python3 src/second_vision/main.py --input usb\n"))
    problems, _ = demo.preflight({"INPUT": str(tmp_path / "no-video0")})
    assert any("another copy" in p for p in problems)
    assert any("no camera" in p for p in problems)


def test_missing_boards_are_found_before_the_start(monkeypatch, tmp_path):
    monkeypatch.setattr(demo.subprocess, "run", _fake_system())
    problems, missing = demo.preflight({"INPUT": str(tmp_path), "SERIAL_PORT": str(tmp_path / "none"),
                                        "CONFIG_PORT": str(tmp_path / "none")})
    assert problems == []
    assert "glasses" in missing and "panel" in missing


def test_a_display_already_there_is_kept():
    env, note = demo.with_display({"DISPLAY": ":5"})
    assert env["DISPLAY"] == ":5" and "this terminal" in note
