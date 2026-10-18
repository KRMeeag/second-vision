"""Camera view check (F5): is the picture the camera sends usable at all?

A blocked lens, a dark room or a camera facing the sun gives the vision models nothing to see, and the
device then says nothing, which is exactly what it says when the way is clear. This module tells the
wearer instead (docs/testing/URGENT-TESTS.md section 1; the user's decisions of 2026-10-17):

    "Camera is blocked or it is too dark. Obstacle warnings may be missing."   every 30 s
    "Camera is facing very bright light. Obstacle warnings may be missing."    every 30 s
    "Camera view is back."                                                     once, when it clears

How it looks: once a second, on one camera frame taken before any AI runs (pipeline/app.py takes it),
a tiny grey picture: the average brightness of each 16 x 16 block (40 x 30 = 1,200 values for a 640 x 480
frame). Block averages, not single pixels: this low-light camera turns its gain up in the dark, and a
covered lens comes out as coloured noise whose bright speckles would hide that the picture is black.
  * "Blocked or dark": the 95th percentile of the blocks under 40 (0 to 255), or their mean under 14. A
    dark room with a lamp that fills more than about 5 % of the view is not flagged; one that fills less
    is (the models cannot see in it either). It cannot tell a covered lens from a dark room, and does not
    need to: the wearer should be told either way.
  * "Too bright": at least 40 % of the blocks at the camera's white (230 or more).
  * Three checks in a row (about 3 s) to raise, three good ones to clear, so a blink or a passing hand
    never triggers it and it does not flap. A change straight from dark to bright (or back) needs three
    checks of the new kind and says the new sentence, not "Camera view is back."
The numbers come from the first real footage (the user's screenshots, 2026-10-19), which the first rules
missed: a finger over the lens measured mean 19.7 and pixel 95th percentile 54 (noise), but block 95th
percentile 28 (dark, with room to spare under 40); a phone flash measured mean 217, because this camera's
white tops out at about 241, so the agreed "mean over 240" could never fire, but 62 % of the blocks at
white (bright, well over 40 %). Still to check on the device: a dim room and a normal room are not
flagged. Each change of what the check sees is logged with the measured values; SV_VIEW_LOG=1 logs every
check, for a calibration walk-round; the screen shows them live (core/status_view.py).

These alerts are not "a part is missing" (core/connections.py MISSING_ALERTS): the camera is connected,
so they do not hold back "Everything is connected". While the camera itself is missing the
camera-missing alert takes over: pipeline/app.py calls reset(), which withdraws these silently.

Every alert raised here is a failsafe alert (D41): spoken even with the speech switch off.
"""

import os
import threading

import numpy as np

try:  # the block averages use cv2 when it is there (it always is on the device); numpy otherwise
    import cv2
except ImportError:  # pragma: no cover - a machine without OpenCV
    cv2 = None

VIEW_CHECK_SECONDS = 1.0        # one look per second
VIEW_SAMPLE_STEP = 16           # one value per 16 x 16 block: its average, so sensor noise cancels
DARK_P95_BELOW = 40.0           # 95th percentile of the blocks under this is dark (URGENT-TESTS.md)
DARK_MEAN_BELOW = 14.0          # mean of the blocks under this is dark (the paper's value)
WHITE_LEVEL = 230.0             # a block this bright is at the camera's white (this one tops out near 241)
BRIGHT_WHITE_SHARE = 0.40       # at least this share of the blocks at white is too bright
VIEW_LOG_EVERY_CHECK = os.environ.get("SV_VIEW_LOG", "0") == "1"     # print every check (calibration)
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
    The tiny grey picture: the average of each `step` x `step` block of `frame`, as float32 brightness
    0 to 255. `frame` is what hailo_apps' get_numpy_from_buffer() returns: an RGB (H, W, 3) array, a
    YUYV (H, W, 2) array (channel 0 is the brightness), an NV12 (Y plane, UV plane) pair, or a grey
    (H, W) array. Raises ValueError for anything else, or for a frame smaller than one block.
    """
    if isinstance(frame, (tuple, list)):        # NV12: (Y plane, UV plane)
        frame = frame[0]
    frame = np.asarray(frame)
    if frame.ndim == 2:
        return _block_means(frame, step).astype(np.float32)
    if frame.ndim == 3 and frame.shape[2] >= 3:
        return _block_means(np.ascontiguousarray(frame[..., :3]), step).astype(np.float32) @ _LUMA
    if frame.ndim == 3 and frame.shape[2] == 2:     # YUYV: brightness in channel 0
        return _block_means(np.ascontiguousarray(frame[..., 0]), step).astype(np.float32)
    raise ValueError(f"not a picture: shape {frame.shape}")


def _block_means(plane, step):
    """The average of each whole step x step block (a partial block at the edge is left out)."""
    rows, cols = plane.shape[0] // step, plane.shape[1] // step
    if rows == 0 or cols == 0:
        raise ValueError(f"frame smaller than one {step} x {step} block: shape {plane.shape}")
    whole = plane[:rows * step, :cols * step]
    if cv2 is not None:                 # INTER_AREA at a whole-number factor is exactly the block mean
        return cv2.resize(whole, (cols, rows), interpolation=cv2.INTER_AREA)
    return whole.astype(np.float32).reshape(rows, step, cols, step, *plane.shape[2:]).mean(axis=(1, 3))


def view_stats(grey) -> tuple:
    """(mean, 95th percentile, share at white) of a grey sample."""
    grey = np.asarray(grey, dtype=np.float32)
    if grey.size == 0:
        raise ValueError("empty sample")
    return float(grey.mean()), float(np.percentile(grey, 95)), float(np.mean(grey >= WHITE_LEVEL))


def classify(grey) -> str:
    """VIEW_DARK, VIEW_BRIGHT or VIEW_OK for one grey sample (see the module docstring)."""
    return _kind(*view_stats(grey))


def _kind(mean: float, p95: float, white: float) -> str:
    if p95 < DARK_P95_BELOW or mean < DARK_MEAN_BELOW:
        return VIEW_DARK
    if white >= BRIGHT_WHITE_SHARE:
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
        self._last_stats = None     # (mean, 95th percentile, share at white) of the last check, for the display

    @property
    def state(self) -> str:
        return self._state

    def snapshot(self) -> dict:
        """
        What the check sees now, for the status display (core/status_board.py): the settled state,
        the kind being confirmed and how many checks of it so far, the checks it takes, and the
        last check's mean, 95th percentile and share at white (None before the first).
        """
        with self._lock:
            mean, p95, white = self._last_stats if self._last_stats is not None else (None, None, None)
            return {"state": self._state, "candidate": self._candidate, "count": self._count,
                    "confirm": self._confirm, "mean": mean, "p95": p95, "white": white}

    def observe_frame(self, frame) -> str:
        """Classify one camera frame and act on it. Returns what this frame looked like."""
        mean, p95, white = view_stats(grey_sample(frame))
        kind = _kind(mean, p95, white)
        if VIEW_LOG_EVERY_CHECK:
            print(f"[VIEW] check: {kind}{_stats_note(mean, p95, white)}")
        self.observe(kind, mean, p95, white)
        return kind

    def observe(self, kind: str, mean: float = None, p95: float = None, white: float = None) -> None:
        """One check's result (VIEW_OK, VIEW_DARK or VIEW_BRIGHT), with its measured values if known."""
        with self._lock:
            if mean is not None and p95 is not None:
                self._last_stats = (mean, p95, white)
            if kind == self._state:
                self._candidate, self._count = None, 0
                return
            if kind != self._candidate:
                self._candidate, self._count = kind, 0
            self._count += 1
            if self._count == 1:
                print(f"[VIEW] the camera looks {kind}{_stats_note(mean, p95, white)}; "
                      f"{self._confirm} checks in a row to act on it")
            if self._count < self._confirm:
                return
            self._move_to(kind)
            print(f"[VIEW] camera view is now {kind}{_stats_note(mean, p95, white)}")
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


def _stats_note(mean, p95, white=None) -> str:
    if mean is None or p95 is None:
        return ""
    at_white = "" if white is None else f", {white:.0%} at white"
    return f" (mean {mean:.1f}, 95th percentile {p95:.1f}{at_white})"
