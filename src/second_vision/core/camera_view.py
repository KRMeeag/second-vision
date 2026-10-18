"""Camera view check (F5): is the picture the camera sends usable at all?

A blocked lens, a dark room or a camera facing the sun gives the vision models nothing to see, and the
device then says nothing, which is exactly what it says when the way is clear. This module tells the
wearer instead (docs/testing/URGENT-TESTS.md section 1; the user's decisions of 2026-10-17):

    "Camera is blocked or it is too dark. Obstacle warnings may be missing."   every 30 s
    "Camera is facing very bright light. Obstacle warnings may be missing."    every 30 s
    "Camera view is back."                                                     once, when it clears

How it looks: once a second, on one camera frame taken before any AI runs (pipeline/app.py takes it),
a tiny grey picture (every 16th pixel in each direction: 40 x 30 = 1,200 pixels of a 640 x 480 frame).
  * "Blocked or dark": the 95th-percentile brightness under 40 (0 to 255), or the mean under 14. A
    dark room with a lamp that fills more than about 5 % of the view is not flagged; one that fills less
    is (the models cannot see in it either). It cannot tell a covered lens from a dark room, and does not
    need to: the wearer should be told either way.
  * "Too bright": the mean over 240.
  * Three checks in a row (about 3 s) to raise, three good ones to clear, so a blink or a passing hand
    never triggers it and it does not flap. A change straight from dark to bright (or back) needs three
    checks of the new kind and says the new sentence, not "Camera view is back."
The thresholds are the ones agreed in URGENT-TESTS.md, PLACEHOLDERS until real footage sets them: each
change of what the check sees is logged with the measured mean and 95th percentile, for that purpose.

These alerts are not "a part is missing" (core/connections.py MISSING_ALERTS): the camera is connected,
so they do not hold back "Everything is connected". While the camera itself is missing the
camera-missing alert takes over: pipeline/app.py calls reset(), which withdraws these silently.

Every alert raised here is a failsafe alert (D41): spoken even with the speech switch off.
"""

import threading

import numpy as np

VIEW_CHECK_SECONDS = 1.0        # one look per second
VIEW_SAMPLE_STEP = 16           # every 16th pixel, each direction
DARK_P95_BELOW = 40.0           # PLACEHOLDER (URGENT-TESTS.md): 95th percentile under this is dark
DARK_MEAN_BELOW = 14.0          # PLACEHOLDER: mean under this is dark (the paper's value)
BRIGHT_MEAN_ABOVE = 240.0       # PLACEHOLDER: mean over this is too bright
VIEW_CONFIRM_CHECKS = 3         # checks in a row to raise, and to clear
VIEW_ALERT_SECONDS = 30.0       # how often a raised view alert is said again

VIEW_OK = "ok"
VIEW_DARK = "dark"
VIEW_BRIGHT = "bright"

VIEW_DARK_ALERT = "camera-view-dark"
VIEW_DARK_TEXT = "Camera is blocked or it is too dark. Obstacle warnings may be missing."
VIEW_BRIGHT_ALERT = "camera-view-bright"
VIEW_BRIGHT_TEXT = "Camera is facing very bright light. Obstacle warnings may be missing."
VIEW_BACK_ALERT = "camera-view-back"
VIEW_BACK_TEXT = "Camera view is back."

_ALERT_OF = {VIEW_DARK: (VIEW_DARK_ALERT, VIEW_DARK_TEXT), VIEW_BRIGHT: (VIEW_BRIGHT_ALERT, VIEW_BRIGHT_TEXT)}
_LUMA = np.array([0.299, 0.587, 0.114], dtype=np.float32)       # ITU-R BT.601, R G B


def grey_sample(frame, step: int = VIEW_SAMPLE_STEP) -> np.ndarray:
    """
    The tiny grey picture: every `step`-th pixel of `frame` in each direction, as float32 brightness
    0 to 255. `frame` is what hailo_apps' get_numpy_from_buffer() returns: an RGB (H, W, 3) array, a
    YUYV (H, W, 2) array (channel 0 is the brightness), an NV12 (Y plane, UV plane) pair, or a grey
    (H, W) array. Raises ValueError for anything else.
    """
    if isinstance(frame, (tuple, list)):        # NV12: (Y plane, UV plane)
        frame = frame[0]
    frame = np.asarray(frame)
    if frame.ndim == 2:
        return frame[::step, ::step].astype(np.float32)
    if frame.ndim == 3 and frame.shape[2] >= 3:
        return frame[::step, ::step, :3].astype(np.float32) @ _LUMA
    if frame.ndim == 3 and frame.shape[2] == 2:     # YUYV: brightness in channel 0
        return frame[::step, ::step, 0].astype(np.float32)
    raise ValueError(f"not a picture: shape {frame.shape}")


def view_stats(grey) -> tuple:
    """(mean, 95th percentile) of a grey sample."""
    grey = np.asarray(grey, dtype=np.float32)
    if grey.size == 0:
        raise ValueError("empty sample")
    return float(grey.mean()), float(np.percentile(grey, 95))


def classify(grey) -> str:
    """VIEW_DARK, VIEW_BRIGHT or VIEW_OK for one grey sample (see the module docstring)."""
    mean, p95 = view_stats(grey)
    if p95 < DARK_P95_BELOW or mean < DARK_MEAN_BELOW:
        return VIEW_DARK
    if mean > BRIGHT_MEAN_ABOVE:
        return VIEW_BRIGHT
    return VIEW_OK


class CameraViewWatch:
    """
    What the camera has been seeing, and the alerts for it.

    observe_frame(frame) once a second (pipeline/app.py, from the camera's streaming thread);
    reset() when the camera goes missing or the check pauses. Thread-safe: one short lock, and the
    only calls made under it are the alert scheduler's, which never wait. The state moves only after
    its alerts were raised or cleared, so a scheduler error is retried at the next check, not lost.
    """

    def __init__(self, alerts, confirm_checks: int = VIEW_CONFIRM_CHECKS):
        self._alerts = alerts
        self._confirm = confirm_checks
        self._lock = threading.Lock()
        self._state = VIEW_OK
        self._candidate = None      # the kind being seen that differs from _state
        self._count = 0             # checks in a row of _candidate
        self._last_stats = None     # (mean, 95th percentile) of the last check, for the status display

    @property
    def state(self) -> str:
        return self._state

    def snapshot(self) -> dict:
        """
        What the check sees now, for the status display (core/status_board.py): the settled state,
        the kind being confirmed and how many checks of it so far, the checks it takes, and the
        last check's mean and 95th percentile (None before the first).
        """
        with self._lock:
            mean, p95 = self._last_stats if self._last_stats is not None else (None, None)
            return {"state": self._state, "candidate": self._candidate, "count": self._count,
                    "confirm": self._confirm, "mean": mean, "p95": p95}

    def observe_frame(self, frame) -> str:
        """Classify one camera frame and act on it. Returns what this frame looked like."""
        grey = grey_sample(frame)
        mean, p95 = view_stats(grey)
        kind = classify(grey)
        self.observe(kind, mean, p95)
        return kind

    def observe(self, kind: str, mean: float = None, p95: float = None) -> None:
        """One check's result (VIEW_OK, VIEW_DARK or VIEW_BRIGHT)."""
        with self._lock:
            if mean is not None and p95 is not None:
                self._last_stats = (mean, p95)
            if kind == self._state:
                self._candidate, self._count = None, 0
                return
            if kind != self._candidate:
                self._candidate, self._count = kind, 0
            self._count += 1
            if self._count == 1:
                print(f"[VIEW] the camera looks {kind}{_stats_note(mean, p95)}; "
                      f"{self._confirm} checks in a row to act on it")
            if self._count < self._confirm:
                return
            self._move_to(kind)
            print(f"[VIEW] camera view is now {kind}{_stats_note(mean, p95)}")
            self._state, self._candidate, self._count = kind, None, 0

    def reset(self) -> None:
        """Withdraw this module's alerts without a word and forget what was seen. For a missing camera."""
        with self._lock:
            for name in (VIEW_DARK_ALERT, VIEW_BRIGHT_ALERT, VIEW_BACK_ALERT):
                self._alerts.clear(name)
            self._state, self._candidate, self._count = VIEW_OK, None, 0
            self._last_stats = None

    def _move_to(self, kind: str) -> None:
        """The alerts for a change from self._state to `kind`. The lock is held."""
        if self._state in _ALERT_OF:
            self._alerts.clear(_ALERT_OF[self._state][0])
        if kind in _ALERT_OF:
            name, text = _ALERT_OF[kind]
            self._alerts.clear(VIEW_BACK_ALERT)        # a spent "back" sentence would silence the next one
            self._alerts.raise_alert(name, text, interval=VIEW_ALERT_SECONDS)
        else:
            self._alerts.raise_alert(VIEW_BACK_ALERT, VIEW_BACK_TEXT)


def _stats_note(mean, p95) -> str:
    return "" if mean is None or p95 is None else f" (mean {mean:.1f}, 95th percentile {p95:.1f})"
