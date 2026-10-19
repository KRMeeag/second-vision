#!/usr/bin/env python3
"""The demo run (F8): start the program, walk the presenter through each step, record what it said and sent.

    my_hailo_env/bin/python docs/testing/tools/demo_run.py                    # the whole demo
    my_hailo_env/bin/python docs/testing/tools/demo_run.py --steps camera,lens
    my_hailo_env/bin/python docs/testing/tools/demo_run.py --list             # the sequence, as markdown

What it does:
  1. Checks the device is free (the boot service stopped, no other copy of the program, a camera attached)
     and which boards are there. A step whose board is missing is skipped, and the summary says why.
  2. Starts scripts/sv-main.sh as the boot service does, with the preview window on the Pi's screen (the TV);
     started over SSH, it borrows the Pi desktop's display.
  3. For each step: shows what to tell the panel and what to do, waits for Enter, then watches the program's
     output for the lines that step should produce, ticking each one with its delay from the Enter. A step
     moves on as soon as everything expected was seen, or when its time is up.
  4. Asks what you saw, heard and felt (Enter to skip) and keeps your words.
  5. Stops the program with Ctrl+C, as the service does, and writes the evidence folder: session.log (every
     line, timestamped), one log per step, and summary.md (the table and your notes).

It never uses sudo and never unplugs anything itself: every unplug is a real one, by hand.
"""
from __future__ import annotations

import argparse
import os
import re
import shlex
import signal
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
DEFAULT_OUT = REPO / "docs/testing/reports/DEMO/evidence"
CHART = "docs/testing/reports/T-15/evidence/perf-npu-20min-with-npu.png"
MONITOR_SHOT = "docs/testing/reports/T-15/evidence/perf-npu-20min-screen-hailortcli-monitor.png"

# The sentences, as the program says them. tests/test_demo_run.py pins each to its constant in src/.
CAMERA_MISSING_TEXT = "Critical Error: Camera cannot be reached"          # pipeline/app.py
CAMERA_BACK_TEXT = "Camera has been reconnected"                          # pipeline/app.py
GLASSES_MISSING_TEXT = "Vibration is not working"                         # core/connections.py
PANEL_MISSING_TEXT = "Controller board cannot be reached. Using the current settings."
EVERYTHING_CONNECTED_TEXT = "Everything is connected"
VIEW_DARK_TEXT = "Camera is blocked or it is too dark. Obstacle warnings may be missing."   # core/camera_view.py
VIEW_BRIGHT_TEXT = "Camera is facing very bright light. Obstacle warnings may be missing."
VIEW_BACK_TEXT = "Camera view is back."

# Lines worth showing live while the program runs; the rest go to session.log only.
LIVE = re.compile(r"^\[(CHECK|TTS|PATTERN)\]|^\[ALERT\] raised|^\[SERIAL\] ESP32 (stopped|responding)|"
                  r"^\[CONFIG\] (No heartbeat|Control panel is back|Mode)|^\[EARPHONES\]|^\[VIEW\] camera view is now")


def said(text: str) -> str:
    """The pattern of the speech worker's line for one sentence: [TTS] '<text>'."""
    return r"^\[TTS\] '" + re.escape(text) + "'"


@dataclass
class Expect:
    """One line the program should print after an action: a regex searched in each output line."""
    pattern: str
    within: float                   # seconds after the presenter's Enter
    what: str                       # plain words, for the table
    count: int = 1                  # how many matching lines: 2 means "and it is repeated"
    absent: bool = False            # True: the line must NOT appear in the window


@dataclass
class Action:
    do: str                         # the instruction to the presenter
    expect: list[Expect] = field(default_factory=list)


@dataclass
class Step:
    key: str
    title: str
    say: str                        # one line to tell the panel
    actions: list[Action]
    needs: str = ""                 # "glasses", "panel" or "earphones": skipped when that part is missing


STEPS = [
    Step("normal", "Normal use, everything connected",
         "The strip along the bottom shows every part working; the device announces what the camera sees.",
         [Action("Point the camera at the room; have someone step in front of it, or walk it past a chair.",
                 [Expect(r"^\[TTS\] '(?!Critical|Vibration|Controller|Camera|Everything|Earphones)", 30,
                         "an object announced"),
                  Expect(r"^\[ALERT\] raised", 15, "failsafe alert", absent=True)])]),
    Step("camera", "Camera out and back",
         "Pull the camera: the wearer is told at once, every 10 s, with two long pulses; it recovers by itself.",
         [Action("Pull the camera's USB cable.",
                 [Expect(r"^\[CHECK\] Camera: NOT CONNECTED", 10, "status: camera NOT CONNECTED"),
                  Expect(said(CAMERA_MISSING_TEXT), 10, f'"{CAMERA_MISSING_TEXT}"'),
                  Expect(r"^\[PATTERN\] playing 'camera'", 10, "camera pulses (two long)"),
                  Expect(said(CAMERA_MISSING_TEXT), 25, "the sentence repeated 10 s later", count=2)]),
          Action("Plug the camera back in.",
                 [Expect(r"^\[CHECK\] Camera: connected", 40, "status: camera connected"),
                  Expect(said(CAMERA_BACK_TEXT), 40, f'"{CAMERA_BACK_TEXT}"')])]),
    Step("glasses", "Glasses board out and back",
         "Unplug the vibration board: the wearer hears that vibration is not working; on return, two short pulses.",
         [Action("Unplug the glasses board's cable.",
                 [Expect(r"^\[SERIAL\] ESP32 stopped acknowledging", 10, "the Pi notices no answer"),
                  Expect(said(GLASSES_MISSING_TEXT), 10, f'"{GLASSES_MISSING_TEXT}"'),
                  Expect(r"^\[CHECK\] Glasses board: NOT RESPONDING", 10, "status: glasses NOT RESPONDING")]),
          Action("Plug the glasses board's cable back in.",
                 [Expect(r"^\[SERIAL\] ESP32 responding again", 20, "the board answers again"),
                  Expect(r"^\[PATTERN\] playing 'reconnect'", 20, "reconnect pulses (two short)"),
                  Expect(said(EVERYTHING_CONNECTED_TEXT), 20, f'"{EVERYTHING_CONNECTED_TEXT}"')])],
         needs="glasses"),
    Step("earphones", "Earphones out and back",
         "Without earphones nothing can be heard, so the device tells the wearer by vibration: strong, weak, three times.",
         [Action("Unplug the earphones.",
                 [Expect(r"^\[EARPHONES\] not found", 6, "the Pi notices the sound card is gone"),
                  Expect(r"^\[PATTERN\] playing 'earphones'", 6, "earphone pulses (strong, weak, x3)"),
                  Expect(r"^\[CHECK\] Earphones: MISSING", 6, "status: earphones MISSING")]),
          Action("Plug the earphones back in.",
                 [Expect(r"^\[EARPHONES\] found again", 10, "the Pi finds them again"),
                  Expect(r"^\[PATTERN\] playing 'reconnect'", 10, "reconnect pulses (two short)"),
                  Expect(said(EVERYTHING_CONNECTED_TEXT), 10, f'"{EVERYTHING_CONNECTED_TEXT}"')])],
         needs="earphones"),
    Step("panel", "Control panel out and back",
         "Unplug the control panel: after 30 s without its heartbeat the wearer is told, and the settings stay as they were.",
         [Action("Unplug the control panel's wire. The sentence comes 20 to 31 s later.",
                 [Expect(r"^\[CONFIG\] No heartbeat", 40, "the Pi notices the heartbeat stopped"),
                  Expect(said(PANEL_MISSING_TEXT), 40, f'"{PANEL_MISSING_TEXT}"'),
                  Expect(r"^\[CHECK\] Control panel: NOT RESPONDING", 40, "status: panel NOT RESPONDING")]),
          Action("Plug the control panel back in. It is noticed with its next heartbeat, up to 10 s.",
                 [Expect(r"^\[CONFIG\] Control panel is back", 15, "the heartbeat is back"),
                  Expect(said(EVERYTHING_CONNECTED_TEXT), 15, f'"{EVERYTHING_CONNECTED_TEXT}"')])],
         needs="panel"),
    Step("lens", "Camera covered, and facing a bright light",
         "A covered or blinded camera sees no obstacles, so the wearer is warned; the numbers top right are what it measures.",
         [Action("Cover the lens with your hand, and keep it covered.",
                 [Expect(r"^\[CHECK\] Camera view: BLOCKED OR TOO DARK", 8, "status: view BLOCKED OR TOO DARK"),
                  Expect(said(VIEW_DARK_TEXT), 8, f'"{VIEW_DARK_TEXT}"')]),
          Action("Uncover the lens.",
                 [Expect(said(VIEW_BACK_TEXT), 8, f'"{VIEW_BACK_TEXT}"'),
                  Expect(r"^\[CHECK\] Camera view: clear", 8, "status: view clear")]),
          Action("Hold a phone's flash right at the lens.",
                 [Expect(r"^\[CHECK\] Camera view: TOO BRIGHT", 8, "status: view TOO BRIGHT"),
                  Expect(said(VIEW_BRIGHT_TEXT), 8, f'"{VIEW_BRIGHT_TEXT}"')]),
          Action("Take the flash away.",
                 [Expect(said(VIEW_BACK_TEXT), 8, f'"{VIEW_BACK_TEXT}"')])]),
    Step("chart", "The performance chart",
         "20 minutes at full load: 30 frames per second, the AI chip at 85 %, no throttling.",
         [Action(f"Open the chart on the screen: {CHART} (and the live NPU monitor: {MONITOR_SHOT})")]),
]


class LogBook:
    """Every line the program prints, with the monotonic time it was read. Safe across threads."""

    def __init__(self):
        self.lines: list[tuple[float, str]] = []
        self._cond = threading.Condition()

    def add(self, t: float, line: str) -> None:
        with self._cond:
            self.lines.append((t, line))
            self._cond.notify_all()

    def read_from(self, i: int, timeout: float) -> tuple[list[tuple[float, str]], int]:
        """The lines from index i on, waiting up to `timeout` s for one if there are none yet."""
        with self._cond:
            if len(self.lines) <= i:
                self._cond.wait(timeout)
            new = self.lines[i:]
            return new, i + len(new)

    def mark(self) -> int:
        with self._cond:
            return len(self.lines)


@dataclass
class Result:
    expect: Expect
    hits: list[float]               # delays from the action, in seconds

    @property
    def ok(self) -> bool:
        return not self.hits if self.expect.absent else len(self.hits) >= self.expect.count

    def when(self) -> str:
        if self.expect.absent:
            return "none seen" if self.ok else f"seen at {self.hits[0]:.1f} s"
        return f"{self.hits[self.expect.count - 1]:.1f} s" if self.ok else "not seen"


def watch(book: LogBook, start: float, start_i: int, expects: list[Expect], clock=time.monotonic) -> list[Result]:
    """Watch the program's output from line `start_i` until every expectation is settled or its time is up.

    Times are measured from `start` (the presenter's Enter). Lines from `start_i` on count even when they
    came before `start` (a negative delay): the presenter may press Enter before or after acting, and an
    unplug is noticed at once. A line counts only up to `within` s after `start`. The watch ends as soon as
    all positive expectations are met and every absent one has had its full window, so a step that went
    well moves on at once. It never runs past the longest window.
    """
    results = [Result(e, []) for e in expects]
    if not expects:
        return results
    deadline = start + max(e.within for e in expects)
    i = start_i
    while True:
        lines, i = book.read_from(i, timeout=0.2)
        for t, line in lines:
            for r in results:
                e = r.expect
                if t - start <= e.within and re.search(e.pattern, line):
                    if e.absent or len(r.hits) < e.count:
                        r.hits.append(t - start)
        now = clock()
        settled = all((r.ok if not r.expect.absent else (bool(r.hits) or now - start >= r.expect.within))
                      for r in results)
        if settled or now >= deadline:
            return results


def preflight(env: dict) -> tuple[list[str], dict[str, str]]:
    """(problems that stop the demo, parts that are missing -> why). Only reads."""
    problems = []
    r = subprocess.run(["systemctl", "is-active", "second-vision.service"], capture_output=True, text=True)
    if r.stdout.strip() == "active":
        problems.append("second-vision.service is running (it holds the camera and the boards): "
                        "sudo systemctl stop second-vision")
    r = subprocess.run(["pgrep", "-af", "second_vision/main.py"], capture_output=True, text=True)
    others = [l for l in r.stdout.splitlines() if "pgrep" not in l]
    if others:
        problems.append("another copy of the program is running: " + others[0][:120])
    if not Path(env.get("INPUT", "/dev/video0")).exists():
        problems.append("no camera: /dev/video0 is missing")
    missing = {}
    if not Path(env.get("SERIAL_PORT", "/dev/serial0")).exists():
        missing["glasses"] = "the glasses board's port is missing"
    if not Path(env.get("CONFIG_PORT", "/dev/ttyAMA3")).exists():
        missing["panel"] = "the control panel's port is missing"
    try:
        sys.path.insert(0, str(REPO / "src"))
        from second_vision.core.connections import earphones_present
        if not earphones_present():
            missing["earphones"] = "no USB sound card that plays (earphones not plugged in)"
    except Exception as exc:                                    # a read problem must not stop the demo
        missing["earphones"] = f"could not check the earphones ({exc!r})"
    return problems, missing


def with_display(env: dict) -> tuple[dict, str]:
    """The environment with the Pi's own screen, when this runs over SSH; and a note saying what was done."""
    env = dict(env)
    if env.get("DISPLAY") or env.get("WAYLAND_DISPLAY"):
        return env, "using this terminal's display"
    if Path("/tmp/.X11-unix/X0").exists():
        env["DISPLAY"] = ":0"
        env.setdefault("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")
        if Path(env["XDG_RUNTIME_DIR"], "wayland-0").exists():
            env["WAYLAND_DISPLAY"] = "wayland-0"
        return env, "no display here (SSH?): the preview window goes to the Pi's own screen (:0)"
    return env, "NO DISPLAY: the program runs without its preview window, so the TV shows nothing"


def as_markdown(steps: list[Step]) -> str:
    out = ["| # | Step | Tell the panel | Do | The program should, within |", "|---|---|---|---|---|"]
    for n, s in enumerate(steps, 1):
        for k, a in enumerate(s.actions):
            exp = "; ".join(("no " if e.absent else "") + f"{e.what} ({e.within:g} s)" for e in a.expect) or "—"
            out.append(f"| {n if k == 0 else ''} | {s.title if k == 0 else ''} | {s.say if k == 0 else ''} | "
                       f"{a.do} | {exp} |")
    return "\n".join(out)


def run(argv: list[str] | None = None, ask=input, say=print) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--steps", help="comma-separated step keys, in the order given: "
                    + ", ".join(s.key for s in STEPS))
    ap.add_argument("--list", action="store_true", help="print the sequence as a markdown table and stop")
    ap.add_argument("--out", default=str(DEFAULT_OUT), help="where the evidence folder goes")
    ap.add_argument("--app", help=argparse.SUPPRESS)                     # tests: a stand-in program
    ap.add_argument("--no-preflight", action="store_true", help=argparse.SUPPRESS)
    args = ap.parse_args(argv)

    steps = STEPS
    if args.steps:
        by_key = {s.key: s for s in STEPS}
        unknown = [k for k in args.steps.split(",") if k not in by_key]
        if unknown:
            say(f"unknown step(s): {', '.join(unknown)}; the steps are: {', '.join(by_key)}")
            return 2
        steps = [by_key[k] for k in args.steps.split(",")]
    if args.list:
        say(as_markdown(steps))
        return 0

    env, display_note = with_display(dict(os.environ, PYTHONUNBUFFERED="1"))
    missing: dict[str, str] = {}
    if not args.no_preflight:
        problems, missing = preflight(env)
        if problems:
            say("NOT STARTED:\n  " + "\n  ".join(problems))
            return 1
    out = Path(args.out) / f"demo-{datetime.now():%Y%m%d-%H%M%S}"
    (out / "steps").mkdir(parents=True)
    say(f"Evidence: {out}\nDisplay: {display_note}")
    for part, why in missing.items():
        say(f"Missing: {part} ({why}): its step is skipped")
    say("Before you go on: DETECT and DEPTH rockers on, speech on, volume up, earphones in.\n")

    cmd = shlex.split(args.app) if args.app else [str(REPO / "scripts/sv-main.sh")]
    book = LogBook()
    session = open(out / "session.log", "w", buffering=1)
    t0 = time.monotonic()
    app = subprocess.Popen(cmd, cwd=REPO, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                           start_new_session=True)

    def pump():
        for raw in app.stdout:
            line = raw.rstrip("\n")
            t = time.monotonic()
            session.write(f"{datetime.now():%H:%M:%S.%f}"[:-3] + f"  +{t - t0:8.2f}  {line}\n")
            book.add(t, line)
            if LIVE.search(line):                       # shown as it happens, also while a prompt waits
                say(f"      {t - t0:7.1f} s  {line[:150]}")
    reader = threading.Thread(target=pump, daemon=True)
    reader.start()

    rows, notes, stop_note = [], [], ""
    try:
        say("Starting the program; waiting for the camera picture to be checked (up to 90 s)...")
        started = watch(book, time.monotonic(), 0, [Expect(r"^\[CHECK\] Camera view: clear", 90, "vision running")])
        if not started[0].ok:
            say("  vision did not report a clear view in 90 s: going on anyway (see session.log)")
        for n, step in enumerate(steps, 1):
            say(f"\n=== {n}. {step.title} ===\n  Tell the panel: {step.say}")
            if step.needs in missing:
                rows.append((n, step.title, "—", "skipped", missing[step.needs]))
                say(f"  skipped: {missing[step.needs]}")
                continue
            begin = book.mark()
            skipped = False
            for action in step.actions:
                shown = book.mark()                     # lines from here on count, even before the Enter
                answer = ask(f"  -> {action.do}\n     Press Enter as you do it (s = skip this step, q = finish now): ")
                if answer.strip().lower() in ("s", "q"):
                    rows.append((n, step.title, action.do, "skipped", "by the presenter"))
                    skipped = True
                    if answer.strip().lower() == "q":
                        raise KeyboardInterrupt
                    break
                results = watch(book, time.monotonic(), shown, action.expect)
                for r in results:
                    mark = "OK  " if r.ok else "MISS"
                    say(f"     {mark} {('no ' if r.expect.absent else '') + r.expect.what}: {r.when()}")
                    rows.append((n, step.title, action.do, "ok" if r.ok else "MISSED",
                                 ("no " if r.expect.absent else "") + f"{r.expect.what}: {r.when()}"))
            with open(out / "steps" / f"{n:02d}-{step.key}.log", "w") as fh:
                fh.writelines(f"+{t - t0:8.2f}  {line}\n" for t, line in book.lines[begin:])
            if not skipped:
                note = ask("  What did you see, hear and feel? (Enter to skip): ").strip()
                if note:
                    notes.append((n, step.title, note))
    except (KeyboardInterrupt, EOFError):
        say("\nFinishing early.")
    finally:
        t_stop = time.monotonic()
        try:
            os.killpg(app.pid, signal.SIGINT)
            code = app.wait(20)
            stop_note = f"exited {code} {time.monotonic() - t_stop:.1f} s after Ctrl+C"
        except subprocess.TimeoutExpired:
            os.killpg(app.pid, signal.SIGKILL)
            stop_note = "did NOT exit within 20 s of Ctrl+C: killed"
        except ProcessLookupError:
            stop_note = f"had already exited ({app.poll()})"
        reader.join(5)
        session.close()
        write_summary(out, steps, rows, notes, stop_note, display_note, missing)
        say(f"\nProgram {stop_note}.\nSummary: {out / 'summary.md'}")
    return 0 if rows and all(r[3] != "MISSED" for r in rows) else 1


def write_summary(out: Path, steps, rows, notes, stop_note, display_note, missing) -> None:
    lines = [f"# Demo run, {datetime.now():%Y-%m-%d %H:%M}", "",
             f"- Steps: {', '.join(s.key for s in steps)}", f"- Display: {display_note}",
             f"- Program: {stop_note}"]
    lines += [f"- Missing: {p} ({why})" for p, why in missing.items()]
    misses = sum(1 for r in rows if r[3] == "MISSED")
    lines += ["", f"**{sum(1 for r in rows if r[3] == 'ok')} seen, {misses} missed, "
                  f"{sum(1 for r in rows if r[3] == 'skipped')} skipped.**", "",
              "Times are from the presenter's Enter; a negative time means the program reacted before it.", "",
              "| # | Step | Action | Result | What |", "|---|---|---|---|---|"]
    lines += [f"| {n} | {title} | {do} | {res} | {what} |" for n, title, do, res, what in rows]
    lines += ["", "## What the presenter saw, heard and felt", ""]
    lines += [f"- **{n}. {title}:** {note}" for n, title, note in notes] or ["(nothing written)"]
    lines += ["", "Every line the program printed: `session.log`; each step's lines: `steps/`."]
    (out / "summary.md").write_text("\n".join(lines) + "\n")


if __name__ == "__main__":
    sys.exit(run())
