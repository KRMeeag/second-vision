"""
F6: the control panel lost and back (REQ-CON-05 to 06, D-17): core/connections.py and workers/config_reader.py.

    ~/.cache/pypoetry/virtualenvs/second-vision-repo-wwT_Rtha-py3.13/bin/python -m pytest tests/test_panel_alerts.py -v
    my_hailo_env/bin/python -m pytest tests/test_panel_alerts.py -v

Requirement: when the panel goes silent (30 s without its B:alive heartbeat), or cannot be opened at
start-up, the device says "Controller board cannot be reached. Using the current settings." at once and
every 5 minutes, whatever the mode, and changes nothing: the settings in use stay as the panel last left
them. When the panel is heard again the alert stops and the D-17 sentence is said once ("Everything is
connected", or "Controller board connected" while another part is still missing). No vibration.
Saving settings between sessions is NOT part of this.

Two layers:
  * the REAL config_reader_worker loop, run in the test's own thread against a scripted panel, with the
    module's clock replaced by the virtual clock of tests/test_alerts.py's World (the real scheduler and
    its speech model). The 30 s silence and the 5-minute cadence are exact and take no time;
  * the same worker in a thread on the real clock, with the silence shortened.
"""

import queue
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

TESTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(TESTS_DIR.parent / "src"))
sys.path.insert(0, str(TESTS_DIR))

from test_alerts import World  # noqa: E402

import second_vision.workers.config_reader as cr  # noqa: E402
from second_vision.core.alerts import AlertScheduler  # noqa: E402
from second_vision.core.config import SystemConfig  # noqa: E402
from second_vision.core.connections import (  # noqa: E402
    CAMERA_MISSING,
    EARPHONES_MISSING,
    EVERYTHING_CONNECTED_TEXT,
    GLASSES_BACK_TEXT,
    MISSING_ALERTS,
    PANEL_BACK,
    PANEL_BACK_TEXT,
    PANEL_MISSING,
    PANEL_MISSING_SECONDS,
    PANEL_MISSING_TEXT,
    earphones_lost,
    glasses_back,
    glasses_lost,
    panel_back,
    panel_lost,
)
from second_vision.core.priority import PriorityMailbox  # noqa: E402

PANEL_PORT = "/dev/fake-panel"


def near(actual, expected, tol=0.6):
    return len(actual) == len(expected) and all(abs(a - b) <= tol for a, b in zip(actual, expected))


# ============================================================
# The wording and names
# ============================================================
def test_the_wording_and_the_cadence_are_the_decided_ones():
    assert PANEL_MISSING_TEXT == "Controller board cannot be reached. Using the current settings."
    assert PANEL_MISSING_SECONDS == 300.0
    assert PANEL_BACK_TEXT == "Controller board connected"
    assert PANEL_MISSING in MISSING_ALERTS and len(set(MISSING_ALERTS)) == len(MISSING_ALERTS)
    assert cr.ALIVE_TIMEOUT_SECONDS == 30.0


# ============================================================
# The real reader loop on a virtual clock
# ============================================================
class Run:
    """
    config_reader_worker against a scripted panel, on the virtual clock of a World.

    `alive` is a list of (start, stop): a B:alive every 10 s at start, start+10, ... below stop. `lines`
    maps a time to another line the panel sends then. The worker ends at `end`. `port` False makes the
    port fail to open (the worker is still given a port path); `port_path=None` with `real_open=True`
    asks for no panel, through the real _open_config_port.
    """

    def __init__(self, monkeypatch, alive, end, lines=None, port=True, port_path=PANEL_PORT, mode="both",
                 alerts=True, real_open=False):
        self.w = World(duration=2.0)
        self.end = end
        self.config = SystemConfig()
        self.config.update(pipeline_mode=mode)
        self.user_data = SimpleNamespace(shutdown_event=threading.Event(), tts_queue=self.w.mailbox)
        if alerts is True:
            self.user_data.alerts = self.w.sched
        elif alerts is not None:
            self.user_data.alerts = alerts
        self.port_path = port_path
        events = {t: b"B:alive\n" for a, b in alive for t in range(a, b, 10)}
        for t, line in (lines or {}).items():
            events[t] = line if isinstance(line, bytes) else (line + "\n").encode()
        self.events = sorted(events.items())
        self._next = 0
        monkeypatch.setattr(cr, "time", SimpleNamespace(monotonic=lambda: self.w.clock.t, sleep=self._sleep))
        if not real_open:
            monkeypatch.setattr(cr, "_open_config_port", lambda path: self if port else None)

    # ---- the fake port -------------------------------------------------------------------
    def _advance(self, to):
        self.w.run(to)
        if self.w.clock.t >= self.end - 1e-9:
            self.user_data.shutdown_event.set()

    def _sleep(self, seconds):
        self._advance(self.w.clock.t + seconds)

    def readline(self):
        """One line, or b"" after READ_TIMEOUT_SECONDS: what the real port does."""
        now = self.w.clock.t
        if self._next < len(self.events) and self.events[self._next][0] <= now + cr.READ_TIMEOUT_SECONDS + 1e-9:
            t, line = self.events[self._next]
            self._next += 1
            self._advance(max(t, now))
            return line
        self._advance(now + cr.READ_TIMEOUT_SECONDS)
        return b""

    def reset_input_buffer(self):
        pass

    def close(self):
        pass

    # ---- running it ----------------------------------------------------------------------
    def go(self, *at):
        """Run the worker to `end`. `at` are (time, callable) events run on the virtual clock."""
        for t, fn in at:
            self.w.at(t, fn)
        cr.config_reader_worker(self.user_data, self.config, self.port_path, None)
        return self

    def said(self, text):
        return self.w.starts(text)


def test_a_panel_that_keeps_beating_says_and_changes_nothing(monkeypatch):
    r = Run(monkeypatch, alive=[(0, 600)], end=600).go()
    assert r.w.sched.raised() == [] and r.w.sim.log == []


def test_silent_for_30_seconds_says_it_at_once_then_every_5_minutes(monkeypatch):
    r = Run(monkeypatch, alive=[(0, 50)], end=1000).go()
    # The last heartbeat was at 40 s; "more than 30 s" is first true at the 71 s read.
    assert near(r.said(PANEL_MISSING_TEXT), [71, 371, 671, 971]), r.said(PANEL_MISSING_TEXT)
    assert r.w.sched.raised() == [PANEL_MISSING]
    assert r.said(EVERYTHING_CONNECTED_TEXT) == []


@pytest.mark.parametrize("mode", ["both", "depth", "detection", "none"])
def test_the_cadence_does_not_depend_on_the_mode(monkeypatch, mode):
    r = Run(monkeypatch, alive=[(0, 20)], end=700, mode=mode).go()
    # Heartbeats at 0 and 10 s; the last one at 10 s, so "more than 30 s" is first true at the 41 s read.
    assert near(r.said(PANEL_MISSING_TEXT), [41, 341, 641]), (mode, r.said(PANEL_MISSING_TEXT))


def test_back_stops_the_alert_and_says_everything_is_connected_once(monkeypatch):
    r = Run(monkeypatch, alive=[(0, 50), (700, 800)], end=800).go()
    assert near(r.said(PANEL_MISSING_TEXT), [71, 371, 671])
    assert near(r.said(EVERYTHING_CONNECTED_TEXT), [700], tol=3.0), r.said(EVERYTHING_CONNECTED_TEXT)
    assert r.w.sched.raised() == [PANEL_BACK]
    assert r.said(PANEL_BACK_TEXT) == []


def test_a_panel_that_never_beats_is_reported_after_30_seconds_and_welcomed_when_it_does(monkeypatch):
    r = Run(monkeypatch, alive=[(200, 300)], end=300).go()
    assert near(r.said(PANEL_MISSING_TEXT), [31]), r.said(PANEL_MISSING_TEXT)
    assert near(r.said(EVERYTHING_CONNECTED_TEXT), [200], tol=3.0)


def test_a_panel_that_cannot_be_opened_at_start_up_is_reported_at_once_with_no_phantom_return(monkeypatch):
    r = Run(monkeypatch, alive=[], end=700, port=False).go()
    assert near(r.said(PANEL_MISSING_TEXT), [0, 300, 600], tol=1.0), r.said(PANEL_MISSING_TEXT)
    assert r.w.sched.raised() == [PANEL_MISSING]
    assert r.said(EVERYTHING_CONNECTED_TEXT) == []


def test_no_panel_asked_for_says_nothing(monkeypatch):
    """Not the cable: nobody asked for a panel (no --config-port), so there is none to miss."""
    r = Run(monkeypatch, alive=[], end=700, port_path=None, real_open=True).go()
    assert r.w.sched.raised() == [] and r.w.sim.log == []


def test_back_while_the_camera_is_missing_names_the_part_that_returned(monkeypatch):
    """D-17: "Everything is connected" must be true."""
    r = Run(monkeypatch, alive=[(0, 50), (400, 500)], end=500)
    r.go((1.0, lambda: r.w.sched.raise_alert(CAMERA_MISSING, "Critical Error: Camera cannot be reached",
                                             interval=10.0)))
    assert len(r.said(PANEL_BACK_TEXT)) == 1, r.w.texts()
    assert r.said(EVERYTHING_CONNECTED_TEXT) == []


def test_back_while_the_earphones_are_out_names_the_part_that_returned(monkeypatch):
    r = Run(monkeypatch, alive=[(0, 50), (400, 500)], end=500)
    r.go((1.0, lambda: earphones_lost(r.w.sched, None)))
    assert len(r.said(PANEL_BACK_TEXT)) == 1, r.w.texts()
    assert r.said(EVERYTHING_CONNECTED_TEXT) == []


def test_the_glasses_returning_while_the_panel_is_out_does_not_say_everything_is_connected(monkeypatch):
    r = Run(monkeypatch, alive=[(0, 50)], end=400)
    r.go((60.0, lambda: glasses_lost(r.w.sched, r.config)),
         (100.0, lambda: glasses_back(r.w.sched, None)))
    assert len(r.said(GLASSES_BACK_TEXT)) == 1, r.w.texts()
    assert r.said(EVERYTHING_CONNECTED_TEXT) == []


def test_lost_and_back_twice_says_the_return_both_times(monkeypatch):
    """The spent "back" sentence is cleared at each loss, or the second return (same text) would be silent."""
    r = Run(monkeypatch, alive=[(0, 50), (100, 200), (400, 500)], end=500).go()
    assert near(r.said(EVERYTHING_CONNECTED_TEXT), [100, 400], tol=3.0), r.w.texts()
    assert len(r.said(PANEL_MISSING_TEXT)) == 2, "once per loss: both are shorter than 5 minutes"


def test_the_settings_in_use_stay_as_the_panel_last_left_them(monkeypatch):
    snapshots = []
    r = Run(monkeypatch, alive=[(0, 50), (700, 800)], end=800,
            lines={5: "S:motor_strength:0.4", 6: "S:tts_volume:0.5", 7: "M:depth"})
    r.go((60.0, lambda: snapshots.append(r.config.snapshot())),
         (650.0, lambda: snapshots.append(r.config.snapshot())))
    assert snapshots[0] == snapshots[1], "something changed while the panel was gone"
    assert snapshots[0]["motor_strength"] == 0.4 and snapshots[0]["tts_volume"] == 0.5
    assert snapshots[0]["pipeline_mode"] == "depth"


def test_a_scheduler_that_fails_never_stops_the_panel_reader(monkeypatch):
    class Broken:
        def __getattr__(self, name):
            def fail(*args, **kwargs):
                raise RuntimeError("scheduler broken")
            return fail

    r = Run(monkeypatch, alive=[(0, 20)], end=300, lines={200: "M:depth"}, alerts=Broken(), mode="both")
    r.go()
    assert r.config.get("pipeline_mode") == "depth", "the reader stopped after the failure"


def test_without_a_scheduler_the_reader_runs_as_before(monkeypatch):
    r = Run(monkeypatch, alive=[(0, 20)], end=300, lines={200: "M:depth"}, alerts=None).go()
    assert r.config.get("pipeline_mode") == "depth"


def test_no_vibration_is_asked_for(monkeypatch):
    """Nothing in the table of REQUIREMENTS section 8 gives the panel a pattern."""
    played = []
    r = Run(monkeypatch, alive=[(0, 50), (400, 500)], end=500)
    r.user_data.patterns = SimpleNamespace(play=lambda name: played.append(name))
    r.go()
    assert played == []


# ============================================================
# The functions themselves
# ============================================================
def test_panel_lost_twice_is_one_alert():
    mailbox = PriorityMailbox()
    alerts = AlertScheduler(mailbox)
    panel_lost(alerts)
    panel_lost(alerts)
    assert alerts.raised() == [PANEL_MISSING]


def test_panel_back_without_a_loss_still_speaks_once_and_leaves_nothing_missing():
    mailbox = PriorityMailbox()
    alerts = AlertScheduler(mailbox)
    panel_back(alerts)
    assert alerts.raised() == [PANEL_BACK]


# ============================================================
# The same worker on the real clock, in a thread
# ============================================================
class LivePanel:
    """A port that beats while `alive`, reading at about 20 Hz like a real port with a short timeout."""

    def __init__(self):
        self.alive = True
        self._last = 0.0
        self.closed = False

    def readline(self):
        time.sleep(0.05)
        if self.alive and time.monotonic() - self._last >= 0.1:
            self._last = time.monotonic()
            return b"B:alive\n"
        return b""

    def reset_input_buffer(self):
        pass

    def close(self):
        self.closed = True


def wait_for(check, timeout, what):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if check():
            return
        time.sleep(0.01)
    pytest.fail(f"never happened: {what}")


def test_the_worker_in_a_thread_raises_the_alert_when_the_panel_goes_quiet_and_clears_it_on_return(monkeypatch):
    port = LivePanel()
    monkeypatch.setattr(cr, "_open_config_port", lambda path: port)
    monkeypatch.setattr(cr, "ALIVE_TIMEOUT_SECONDS", 0.4)
    mailbox = PriorityMailbox()
    ud = SimpleNamespace(shutdown_event=threading.Event(), tts_queue=mailbox, alerts=AlertScheduler(mailbox))
    worker = threading.Thread(target=cr.config_reader_worker, args=(ud, SystemConfig(), PANEL_PORT, None),
                              daemon=True)
    worker.start()
    time.sleep(0.8)
    assert ud.alerts.raised() == [], "a panel that beats is not missing"
    port.alive = False
    wait_for(lambda: ud.alerts.is_raised(PANEL_MISSING), 2.0, "the missing alert")
    ud.alerts.poll()
    assert mailbox.peek() == {"announce": PANEL_MISSING_TEXT}
    port.alive = True
    wait_for(lambda: ud.alerts.is_raised(PANEL_BACK), 2.0, "the return")
    assert not ud.alerts.is_raised(PANEL_MISSING)
    ud.alerts.poll()
    assert mailbox.peek() == {"announce": EVERYTHING_CONNECTED_TEXT}
    ud.shutdown_event.set()
    worker.join(3.0)
    assert not worker.is_alive() and port.closed
