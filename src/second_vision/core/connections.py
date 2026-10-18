"""Failsafes for the parts that can be lost and come back: what is said and felt (REQ-CON).

Each part that can drop out (camera, glasses board, control panel, earphones) has a "missing"
alert on the alert scheduler (core/alerts.py) while it is gone, and on its return says a
sentence once and, for some parts, plays the reconnect pattern (core/haptic_patterns.py).
This module holds the wording and the rules the parts share, so each part's own code only
reports "lost" and "back".

The reconnect sentence (D-17, the user's decision 2026-10-18): "Everything is connected" only
when no other part is still missing; otherwise a sentence about the part that returned
("Vibration is working again"). "Still missing" is read from the MISSING_ALERTS names, not
from alerts.raised() as a whole: the scheduler keeps a spent once-only sentence raised until it
is cleared (for example "Camera has been reconnected", cleared only at the next camera loss),
and such a sentence is not a missing part.

"Depth on" and "detection on" are read from pipeline_mode, the only way the control panel
reports its DETECT and DEPTH rockers (the M: line, firmware/control-panel/PROTOCOL.md). The
panel never sends depth_enabled or detection_enabled, so those stay at their defaults.

Glasses board (F2; REQ-CON-02 to 04): lost -> "Vibration is not working", again every 30 s
while depth is on, once while it is off (a depth switch-off mid-run stops the repeats; the
scheduler asks on every tick). Back -> that alert stops, the reconnect *beep beep* plays (D41:
even with the vibration switch off), and the D-17 sentence is said once.

Earphones (F3; REQ-CON-10 to 12, D-18): lost -> the *strong weak* x3 pattern at once, again every
30 s while detection is on, once while it is off (no speech: there is nothing to speak through).
Back -> the reconnect *beep beep* and the D-17 sentence, said through the returned earphones.
They have no spoken alert, so while they are out a SILENT alert (alerts.raise_alert(speak=False))
stands for them: that is how reconnect_text() and the other parts see them as missing.
EarphonesWatcher holds the rules (when they count as lost or back, when the pattern repeats);
workers/earphones_worker.py calls its step() once a second.

Control panel (F6; REQ-CON-05 to 06): lost (silent for 30 s, or its port cannot be opened at
start-up) -> "Controller board cannot be reached. Using the current settings." at once and every
5 minutes, whatever the mode; nothing else changes, the settings in use stay as they are. Back
(its heartbeat returns) -> that alert stops and the D-17 sentence is said once; no vibration.
workers/config_reader.py reports lost and back; panel_lost() / panel_back() do the rest.
"""

import re
from pathlib import Path

from second_vision.core.haptic_patterns import PATTERN_EARPHONES, PATTERN_RECONNECT

EVERYTHING_CONNECTED_TEXT = "Everything is connected"

# The scheduler names of the "a part is missing" alerts. D-17 asks only about these.
CAMERA_MISSING = "camera-missing"       # must equal pipeline/app.py CAMERA_ALERT_MISSING (a test pins it)
GLASSES_MISSING = "glasses-missing"
EARPHONES_MISSING = "earphones-missing"     # silent: never spoken, only counted
PANEL_MISSING = "panel-missing"
MISSING_ALERTS = (CAMERA_MISSING, GLASSES_MISSING, EARPHONES_MISSING, PANEL_MISSING)

GLASSES_MISSING_TEXT = "Vibration is not working"
GLASSES_MISSING_SECONDS = 30.0
GLASSES_BACK = "glasses-reconnected"
GLASSES_BACK_TEXT = "Vibration is working again"

EARPHONES_MISSING_TEXT = "Earphones missing"     # describes the silent alert; never spoken
EARPHONES_PATTERN_SECONDS = 30.0
EARPHONES_BACK = "earphones-reconnected"
EARPHONES_BACK_TEXT = "Earphones connected"
PANEL_MISSING_TEXT = "Controller board cannot be reached. Using the current settings."
PANEL_MISSING_SECONDS = 300.0       # every 5 minutes
PANEL_BACK = "panel-reconnected"
PANEL_BACK_TEXT = "Controller board connected"

# Readings in a row that must agree before a change counts, so that one bad read of /proc/asound
# is not announced as a loss followed by a return. Two once-a-second readings: 1 to 2 s.
EARPHONES_CONFIRM_READS = 2

ASOUND_ROOT = Path("/proc/asound")      # the sound cards the kernel has set up
_CARD_DIR = re.compile(r"card\d+")      # not "cards" (a file), nor the name links such as "Audio"

_DEPTH_MODES = ("depth", "both")
_DETECTION_MODES = ("detection", "both")


def depth_on(config) -> bool:
    """True while the depth estimation runs: pipeline_mode "depth" or "both"."""
    return config.get("pipeline_mode") in _DEPTH_MODES


def detection_on(config) -> bool:
    """True while the object detection runs: pipeline_mode "detection" or "both"."""
    return config.get("pipeline_mode") in _DETECTION_MODES


def reconnect_text(alerts, part_text: str) -> str:
    """
    The sentence for a part that has just returned (D-17): EVERYTHING_CONNECTED_TEXT if no
    MISSING_ALERTS alert is raised, else `part_text`. Clear the part's own missing alert first.
    """
    still_missing = [name for name in alerts.raised() if name in MISSING_ALERTS]
    return part_text if still_missing else EVERYTHING_CONNECTED_TEXT


def glasses_lost(alerts, config) -> None:
    """
    The glasses board stopped answering, or never answered. Raises the missing alert; clears
    the spent reconnect sentence so that the next return is said again (the scheduler would
    not repeat a once-alert with the same text).
    """
    alerts.clear(GLASSES_BACK)
    alerts.raise_alert(GLASSES_MISSING, GLASSES_MISSING_TEXT, interval=GLASSES_MISSING_SECONDS,
                       repeat=lambda: depth_on(config))


def glasses_back(alerts, patterns=None) -> None:
    """
    The glasses board answers again. Stops the missing alert, plays the reconnect pattern
    (when a pattern player is given) and says the D-17 sentence once.
    """
    alerts.clear(GLASSES_MISSING)
    if patterns is not None:
        patterns.play(PATTERN_RECONNECT)
    alerts.raise_alert(GLASSES_BACK, reconnect_text(alerts, GLASSES_BACK_TEXT))


def panel_lost(alerts) -> None:
    """
    The control panel went silent, or was never there. Raises the missing alert (said at once, then
    every 5 minutes, whatever the mode) and clears the spent "back" sentence so that the next return
    is said again. Changes no setting: the settings in use stay as the panel last left them.
    """
    alerts.clear(PANEL_BACK)
    alerts.raise_alert(PANEL_MISSING, PANEL_MISSING_TEXT, interval=PANEL_MISSING_SECONDS)


def panel_back(alerts) -> None:
    """
    The control panel is heard again. Stops the missing alert and says the D-17 sentence once. No
    vibration for the panel (REQUIREMENTS section 8).
    """
    alerts.clear(PANEL_MISSING)
    alerts.raise_alert(PANEL_BACK, reconnect_text(alerts, PANEL_BACK_TEXT))


def earphones_present(root=None) -> bool:
    """
    True if the Pi has a USB sound card that can play sound (D-18): a /proc/asound/cardN with a
    `usbid` file and a `pcm*p` (playback) entry. Plain file reads, no command, no root; it
    ignores the HDMI outputs (no usbid) and the camera's microphone (a USB card, capture only),
    and does not depend on the earphones' brand. Never raises: an unreadable tree counts as
    missing. `root` is for tests; the default is ASOUND_ROOT.
    """
    root = ASOUND_ROOT if root is None else Path(root)
    try:
        cards = [c for c in root.iterdir() if _CARD_DIR.fullmatch(c.name)]
    except OSError:
        return False
    for card in cards:
        try:
            if (card / "usbid").is_file() and any(card.glob("pcm*p")):
                return True
        except OSError:
            continue        # the card went away while it was being looked at
    return False


def earphones_lost(alerts, patterns=None) -> None:
    """
    The earphones are gone, or were never there. Raises the silent missing alert, clears the spent
    "back" sentence so that the next return is said again, and plays the earphones pattern once
    (when a pattern player is given). The repeats are EarphonesWatcher's.
    """
    alerts.clear(EARPHONES_BACK)
    alerts.raise_alert(EARPHONES_MISSING, EARPHONES_MISSING_TEXT, speak=False)
    if patterns is not None:
        patterns.play(PATTERN_EARPHONES)


def earphones_back(alerts, patterns=None) -> None:
    """
    The earphones are back. Stops the missing alert, plays the reconnect pattern (when a pattern
    player is given) and says the D-17 sentence once.
    """
    alerts.clear(EARPHONES_MISSING)
    if patterns is not None:
        patterns.play(PATTERN_RECONNECT)
    alerts.raise_alert(EARPHONES_BACK, reconnect_text(alerts, EARPHONES_BACK_TEXT))


class EarphonesWatcher:
    """
    When the earphones count as lost or back, and when the lost pattern repeats (REQ-CON-10 to 12).

    step(now) takes one reading and is called about once a second, by workers/earphones_worker.py
    (tests call it by hand with a virtual clock). `present` returns whether the earphones are
    there (default earphones_present).

      * The first reading decides at once: absent at start-up counts as lost, with the pattern.
      * After that a change counts only when CONFIRM readings in a row show it.
      * Lost: the pattern again every 30 s while detection is on (pipeline_mode "detection" or
        "both", read at each step), once while it is off. Switching detection off mid-run stops
        the repeats; switching it back on plays at once if 30 s have passed.
      * Back: see earphones_back().

    The state moves only after the action has worked, so an error in the scheduler is retried at
    the next reading, not forgotten. Single-threaded: only one thread calls step().
    """

    def __init__(self, alerts, patterns, config, present=None, confirm_reads=EARPHONES_CONFIRM_READS):
        self._alerts = alerts
        self._patterns = patterns
        self._config = config
        self._present = earphones_present if present is None else present
        self._confirm = confirm_reads
        self._connected = None      # None until the first reading
        self._streak = 0            # readings in a row that disagree with _connected
        self._pattern_at = None     # when the lost pattern was last asked for

    @property
    def connected(self):
        """True / False once the first reading is in, None before."""
        return self._connected

    def step(self, now: float) -> None:
        """Take one reading at clock time `now` (seconds, monotonic) and act on it."""
        present = bool(self._present())
        if self._connected is None:
            if not present:
                self._lost(now)
            self._connected = present
            return
        if present == self._connected:
            self._streak = 0
        else:
            self._streak += 1
            if self._streak >= self._confirm:
                if present:
                    self._back()
                else:
                    self._lost(now)
                self._connected = present
                self._streak = 0
                return
        if not self._connected and detection_on(self._config) \
                and now - self._pattern_at >= EARPHONES_PATTERN_SECONDS:
            self._pattern_at = now
            if self._patterns is not None:
                self._patterns.play(PATTERN_EARPHONES)

    def _lost(self, now: float) -> None:
        print("[EARPHONES] not found: the USB sound card that plays is gone")
        earphones_lost(self._alerts, self._patterns)
        self._pattern_at = now

    def _back(self) -> None:
        print("[EARPHONES] found again")
        earphones_back(self._alerts, self._patterns)
