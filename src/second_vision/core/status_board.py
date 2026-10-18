"""The failsafe checks at a glance: one status that the screen and the terminal both show.

The device's failsafes (DEMO-TRACK F0 to F6) speak to the wearer through bone-conduction earphones and
vibrate on their head, so nobody else can tell they work. This module turns their state into text, so a
tester at the terminal and an audience watching the TV see the same thing at the same moment:

    CAMERA        connected | NOT CONNECTED
    CAMERA VIEW   clear | looks dark (2 of 3) | BLOCKED OR TOO DARK | TOO BRIGHT   (with the measured brightness)
    GLASSES BOARD connected | NOT RESPONDING
    EARPHONES     connected | MISSING
    CONTROL PANEL connected | NOT RESPONDING
    and the last failsafe sentence the device said, for a few seconds.

read_status() reads it from the alert scheduler (core/alerts.py: the "missing" alerts of
core/connections.py) and the camera view check (core/camera_view.py). It only reads: it changes no alert
and no setting. workers/status_worker.py reads it twice a second, keeps it on user_data.status and prints
each change (StatusReporter); the camera picture draws it (core/status_view.py, pipeline/callbacks.py).
No cv2 here, so the terminal side works in mock mode on any machine.
"""

import sys
from typing import NamedTuple, Optional

from second_vision.core.camera_view import VIEW_BRIGHT, VIEW_DARK, VIEW_OK
from second_vision.core.connections import CAMERA_MISSING, EARPHONES_MISSING, GLASSES_MISSING, PANEL_MISSING

STATUS_SECONDS = 0.5        # how often the status is read (workers/status_worker.py)
SAID_SECONDS = 8.0          # how long the last spoken failsafe sentence stays on screen

OK = "ok"                   # working
PROBLEM = "problem"         # a failsafe alert is raised for it
PENDING = "pending"         # a change is being confirmed (the camera view's three checks)
OFF = "off"                 # not in use, or not judged now

_SEEN_AS = {VIEW_OK: "clear", VIEW_DARK: "dark", VIEW_BRIGHT: "too bright"}


class Check(NamedTuple):
    """One line of the status."""
    key: str                # "camera", "view", "glasses", "earphones", "panel"
    name: str               # in words: "Camera view"
    label: str              # short, for the on-screen strip: "VIEW"
    state: str              # OK, PROBLEM, PENDING or OFF
    text: str               # what it is: "BLOCKED OR TOO DARK"
    short: str              # the same in a word or two, for the strip: "BLOCKED"
    detail: str = ""        # measured values, if any: "brightness 0-255: average 6, top 5 %: 11"


class Status(NamedTuple):
    """Every check, and the last failsafe sentence said (None if none yet) with its clock time."""
    checks: tuple
    said: Optional[str] = None
    said_at: Optional[float] = None

    def problems(self) -> list:
        return [c for c in self.checks if c.state == PROBLEM]

    def pending(self) -> list:
        return [c for c in self.checks if c.state == PENDING]

    def get(self, key: str) -> Optional[Check]:
        return next((c for c in self.checks if c.key == key), None)


def read_status(alerts, view=None, config=None) -> Status:
    """
    The status now. `alerts` is the AlertScheduler; `view` the CameraViewWatch, or None when there is
    no camera pipeline (mock mode); `config` the SystemConfig, or None (then every part counts as in use).
    """
    raised = set(alerts.raised())
    camera_missing = CAMERA_MISSING in raised
    checks = (
        Check("camera", "Camera", "CAMERA", PROBLEM, "NOT CONNECTED", "LOST") if camera_missing
        else Check("camera", "Camera", "CAMERA", OK, "connected", "OK"),
        _view_check(view, camera_missing, None if config is None else config.get("pipeline_mode")),
        _link_check("glasses", "Glasses board", "GLASSES", GLASSES_MISSING in raised,
                    _in_use(config, "serial_port")),
        Check("earphones", "Earphones", "EARPHONES", PROBLEM, "MISSING", "LOST") if EARPHONES_MISSING in raised
        else Check("earphones", "Earphones", "EARPHONES", OK, "connected", "OK"),
        _link_check("panel", "Control panel", "PANEL", PANEL_MISSING in raised, _in_use(config, "config_port")),
    )
    last = alerts.last_spoken() if hasattr(alerts, "last_spoken") else None
    said, said_at = last if last is not None else (None, None)
    return Status(checks, said, said_at)


def _in_use(config, key) -> bool:
    return True if config is None else bool(config.get(key))


def _link_check(key, name, label, missing, in_use) -> Check:
    if not in_use:
        return Check(key, name, label, OFF, "not in use", "-")
    if missing:
        return Check(key, name, label, PROBLEM, "NOT RESPONDING", "LOST")
    return Check(key, name, label, OK, "connected", "OK")


def _view_check(view, camera_missing, mode) -> Check:
    def check(state, text, short, detail=""):
        return Check("view", "Camera view", "VIEW", state, text, short, detail)

    if view is None:
        return check(OFF, "not checked", "-")
    if camera_missing:
        return check(OFF, "no picture", "-")
    if mode == "none":
        return check(OFF, "paused (both rockers off)", "-")
    snap = view.snapshot()
    detail = ("" if snap["mean"] is None else
              f"brightness 0-255: average {snap['mean']:.0f}, top 5 %: {snap['p95']:.0f}")
    candidate = snap["candidate"]
    progress = "" if candidate is None else f"{snap['count']} of {snap['confirm']}"
    if snap["state"] in (VIEW_DARK, VIEW_BRIGHT):
        text, short = ("BLOCKED OR TOO DARK", "BLOCKED") if snap["state"] == VIEW_DARK else ("TOO BRIGHT", "BRIGHT")
        if candidate is not None:
            text += f" (looks {_SEEN_AS[candidate]}: {progress})"
        return check(PROBLEM, text, short, detail)
    if candidate is not None:
        return check(PENDING, f"looks {_SEEN_AS[candidate]} ({progress})",
                     f"{snap['count']}/{snap['confirm']}", detail)
    if snap["mean"] is None:
        # No frame checked yet (just started, the camera just came back, or mock mode, which has no
        # camera pipeline): "clear" would claim a look that never happened.
        return check(OFF, "not checked yet", "-")
    return check(OK, "clear", "OK", detail)


# ============================================================
# The terminal
# ============================================================
_ANSI = {OK: "\033[32m", PROBLEM: "\033[1;31m", PENDING: "\033[33m", OFF: "\033[90m"}
_RESET = "\033[0m"


class StatusReporter:
    """
    Prints the status to the terminal: all of it once, then one `[CHECK]` line per check that
    changes (its state or its words; the brightness numbers alone do not print, or the camera view
    would print every second). Coloured when `out` is a terminal, plain text otherwise (the journal).
    """

    def __init__(self, out=None, color: Optional[bool] = None):
        self._out = out if out is not None else sys.stdout
        if color is None:
            isatty = getattr(self._out, "isatty", None)
            color = bool(isatty and isatty())
        self._color = color
        self._last = None

    def report(self, status: Status) -> list:
        """Print what changed since the last call. Returns the lines printed (without colour)."""
        lines = []
        if self._last is None:
            plain = " | ".join(f"{c.name}: {c.text}" for c in status.checks)
            shown = " | ".join(f"{c.name}: {self._paint(c.state, c.text)}" for c in status.checks)
            lines.append((f"[CHECK] {plain}", f"[CHECK] {shown}"))
        else:
            before = {c.key: (c.state, c.text) for c in self._last.checks}
            for c in status.checks:
                if before.get(c.key) != (c.state, c.text):
                    extra = f" ({c.detail})" if c.detail else ""
                    lines.append((f"[CHECK] {c.name}: {c.text}{extra}",
                                  f"[CHECK] {c.name}: {self._paint(c.state, c.text)}{extra}"))
        self._last = status
        for _, shown in lines:
            print(shown, file=self._out, flush=True)
        return [plain for plain, _ in lines]

    def _paint(self, state, text):
        return f"{_ANSI[state]}{text}{_RESET}" if self._color else text
