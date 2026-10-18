"""
F3: the earphones lost and back (REQ-CON-10 to 12, D-17, D-18): core/connections.py,
core/alerts.py (the silent alert) and workers/earphones_worker.py.

    ~/.cache/pypoetry/virtualenvs/second-vision-repo-wwT_Rtha-py3.13/bin/python -m pytest tests/test_earphones_alerts.py -v
    my_hailo_env/bin/python -m pytest tests/test_earphones_alerts.py -v

No earphones, no board. Four layers:
  * the presence rule, on fake /proc/asound trees (D-18: a USB sound card that can PLAY);
  * the scheduler's silent alert: raised and counted, never spoken;
  * the lost / back rules, on a VIRTUAL clock: the real AlertScheduler and PriorityMailbox, a
    speech worker reduced to take() / finish(), and a recorder in place of the pattern player;
  * the REAL earphones worker in a thread, reading a fake /proc/asound tree that the test edits.

"Detection on" is pipeline_mode "detection" or "both": the panel reports its DETECT rocker only
through the M: line (firmware/control-panel/PROTOCOL.md), never as detection_enabled.
"""

import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import second_vision.core.connections as connections
import second_vision.workers.earphones_worker as earphones_worker_module
from second_vision.core.alerts import SPOKEN, AlertScheduler
from second_vision.core.config import SystemConfig
from second_vision.core.connections import (
    CAMERA_MISSING,
    EARPHONES_BACK,
    EARPHONES_BACK_TEXT,
    EARPHONES_MISSING,
    EARPHONES_PATTERN_SECONDS,
    EVERYTHING_CONNECTED_TEXT,
    GLASSES_BACK_TEXT,
    GLASSES_MISSING,
    MISSING_ALERTS,
    EarphonesWatcher,
    detection_on,
    earphones_back,
    earphones_lost,
    earphones_present,
    glasses_back,
    glasses_lost,
    reconnect_text,
)
from second_vision.core.haptic_patterns import PATTERN_EARPHONES, PATTERN_RECONNECT
from second_vision.core.priority import PriorityMailbox
from second_vision.workers.earphones_worker import earphones_worker

TICK = 0.05
UTTERANCE = 2.0           # seconds a sentence takes in the model


# ============================================================
# The presence rule (D-18), on fake /proc/asound trees
# ============================================================
def make_card(root, number, usbid=None, playback=False, capture=False):
    card = root / f"card{number}"
    card.mkdir()
    (card / "id").write_text(f"CARD{number}\n")
    if usbid:
        (card / "usbid").write_text(usbid + "\n")
    if playback:
        (card / "pcm0p").mkdir()
    if capture:
        (card / "pcm0c").mkdir()
    return card


def this_pi(root):
    """The four cards measured on the Pi, 2026-10-18: two HDMI outputs, the earphones, the camera's mic."""
    (root / "cards").write_text(" 0 [vc4hdmi0 ]: vc4-hdmi\n 1 [vc4hdmi1 ]: vc4-hdmi\n")
    make_card(root, 0, playback=True)
    make_card(root, 1, playback=True)
    earphones = make_card(root, 2, usbid="0666:0880", playback=True, capture=True)
    make_card(root, 3, usbid="0c45:0261", capture=True)
    (root / "Audio").symlink_to(earphones)          # /proc/asound/Audio -> card2, as on the Pi
    return earphones


def test_the_cards_of_this_pi_count_as_earphones_present(tmp_path):
    this_pi(tmp_path)
    assert earphones_present(tmp_path) is True


def test_without_the_usb_sound_card_only_hdmi_and_the_camera_mic_remain_and_that_is_missing(tmp_path):
    """The camera's microphone is a USB sound card too, capture only: it must not look like earphones."""
    this_pi(tmp_path)
    import shutil
    shutil.rmtree(tmp_path / "card2")
    (tmp_path / "Audio").unlink()
    assert earphones_present(tmp_path) is False


def test_a_usb_card_that_only_records_is_not_earphones(tmp_path):
    make_card(tmp_path, 3, usbid="0c45:0261", capture=True)
    assert earphones_present(tmp_path) is False


def test_a_non_usb_card_that_plays_is_not_earphones(tmp_path):
    make_card(tmp_path, 0, playback=True)
    assert earphones_present(tmp_path) is False


def test_a_usb_card_that_plays_is_earphones_whatever_its_id(tmp_path):
    """Independent of brand: replacing the earphones must not break the check."""
    make_card(tmp_path, 4, usbid="1234:abcd", playback=True)
    assert earphones_present(tmp_path) is True


def test_the_cards_file_is_not_a_card(tmp_path):
    (tmp_path / "cards").write_text("usbid pcm0p\n")
    (tmp_path / "card9").write_text("a file, not a directory")
    assert earphones_present(tmp_path) is False


def test_no_sound_tree_at_all_is_missing(tmp_path):
    assert earphones_present(tmp_path) is False
    assert earphones_present(tmp_path / "does-not-exist") is False


def test_one_read_is_fast_and_never_raises_on_the_real_system():
    """Must not slow the pipeline: plain file reads, once a second. Passes whatever this machine has."""
    start = time.perf_counter()
    for _ in range(20):
        assert earphones_present() in (True, False)
    assert (time.perf_counter() - start) / 20 < 0.02


# ============================================================
# The scheduler's silent alert
# ============================================================
class VClock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


class Plays:
    """Stands in for the pattern player: records (time, name) of each play()."""

    def __init__(self, clock):
        self.clock = clock
        self.log = []
        self._lock = threading.Lock()

    def play(self, name):
        with self._lock:
            self.log.append((self.clock(), name))
        return True

    def times(self, name):
        with self._lock:
            return [t for t, n in self.log if n == name]


class World:
    """Scheduler + mailbox + a minimal speech worker + the earphones watcher, on a virtual clock."""

    def __init__(self, mode="both", present=True, confirm_reads=None):
        self.clock = VClock()
        self.mailbox = PriorityMailbox()
        self.alerts = AlertScheduler(self.mailbox, clock=self.clock)
        self.config = SystemConfig()
        self.config.update(pipeline_mode=mode)
        self.patterns = Plays(self.clock)
        self.present = present
        kwargs = {} if confirm_reads is None else {"confirm_reads": confirm_reads}
        self.watcher = EarphonesWatcher(self.alerts, self.patterns, self.config,
                                        present=lambda: self.present, **kwargs)
        self.spoken = []          # (start time, text)
        self._current = None
        self._ends = 0.0
        self.events = []          # (time, callable), run in time order
        self._next_step = 0.0
        self.watching = True

    def at(self, t, fn):
        self.events.append((t, fn))
        self.events.sort(key=lambda e: e[0])

    def unplug_at(self, t):
        self.at(t, lambda: setattr(self, "present", False))

    def plug_at(self, t):
        self.at(t, lambda: setattr(self, "present", True))

    def run(self, until):
        while self.clock.t < until - 1e-9:
            while self.events and self.events[0][0] <= self.clock.t + 1e-9:
                self.events.pop(0)[1]()
            if self.watching and self.clock.t >= self._next_step - 1e-9:
                self.watcher.step(self.clock.t)          # what the worker does once a second
                self._next_step += 1.0
            self.alerts.poll()
            now = self.clock.t
            if self._current is not None and now >= self._ends - 1e-9:
                self.alerts.finish(self._current, SPOKEN)
                self._current = None
            if self._current is None:
                item = self.alerts.take()
                if item is not None:
                    self.spoken.append((now, item["announce"]))
                    self._current, self._ends = item, now + UTTERANCE
            self.clock.t = round(self.clock.t + TICK, 6)

    def starts(self, text):
        return [t for t, said in self.spoken if said == text]


def near(actual, expected, tol=0.3):
    return len(actual) == len(expected) and all(abs(a - b) <= tol for a, b in zip(actual, expected))


def test_a_silent_alert_is_raised_and_counted_but_never_offered_to_speech():
    w = World()
    w.watching = False
    w.alerts.raise_alert("quiet", "Never said", speak=False)
    w.run(120)
    assert w.alerts.is_raised("quiet") and w.alerts.raised() == ["quiet"]
    assert w.spoken == [] and w.mailbox.peek() is None


def test_a_silent_alert_does_not_hold_back_a_spoken_one():
    w = World()
    w.watching = False
    w.alerts.raise_alert("quiet", "Never said", interval=5.0, speak=False)
    w.alerts.raise_alert("loud", "Said once")
    w.run(30)
    assert near(w.starts("Said once"), [0]) and w.starts("Never said") == []


def test_a_silent_alert_clears_like_any_other():
    w = World()
    w.watching = False
    w.alerts.raise_alert("quiet", "Never said", speak=False)
    assert w.alerts.clear("quiet") is True
    assert w.alerts.raised() == [] and w.alerts.clear("quiet") is False


def test_raising_the_same_name_again_as_spoken_replaces_the_silent_one():
    w = World()
    w.watching = False
    w.alerts.raise_alert("part", "Said now", speak=False)
    w.alerts.raise_alert("part", "Said now")
    w.run(5)
    assert near(w.starts("Said now"), [0])


def test_alerts_are_spoken_by_default():
    w = World()
    w.watching = False
    w.alerts.raise_alert("plain", "Spoken by default")
    w.run(5)
    assert near(w.starts("Spoken by default"), [0])


# ============================================================
# Detection on, D-17 and the names
# ============================================================
@pytest.mark.parametrize("mode,on", [("both", True), ("detection", True), ("depth", False), ("none", False)])
def test_detection_on_is_read_from_the_pipeline_mode(mode, on):
    config = SystemConfig()
    config.update(pipeline_mode=mode)
    assert detection_on(config) is on


def test_detection_on_ignores_detection_enabled_which_the_panel_never_sends():
    config = SystemConfig()
    config.update(pipeline_mode="depth", detection_enabled=True)
    assert not detection_on(config)


def test_the_earphones_are_one_of_the_parts_that_can_be_missing():
    assert EARPHONES_MISSING in MISSING_ALERTS
    assert len(set(MISSING_ALERTS)) == len(MISSING_ALERTS)
    assert EARPHONES_PATTERN_SECONDS == 30.0
    assert EARPHONES_BACK_TEXT == "Earphones connected"


# ============================================================
# Lost and back, on a virtual clock
# ============================================================
def test_earphones_present_at_start_say_and_play_nothing():
    w = World(present=True)
    w.run(120)
    assert w.spoken == [] and w.patterns.log == [] and w.alerts.raised() == []


def test_absent_at_start_up_counts_as_lost_at_once_and_is_never_spoken():
    w = World(mode="both", present=False)
    w.config.update(tts_enabled=True)
    w.run(200)
    assert w.alerts.is_raised(EARPHONES_MISSING)
    assert near(w.patterns.times(PATTERN_EARPHONES), [0, 30, 60, 90, 120, 150, 180]), w.patterns.log
    assert w.spoken == [], "nothing to speak through: the earphones' alert is a vibration pattern only"


@pytest.mark.parametrize("mode", ["both", "detection"])
def test_lost_with_detection_on_repeats_the_pattern_every_30_seconds(mode):
    w = World(mode=mode)
    w.unplug_at(10.0)
    w.run(130)
    # Two readings in a row confirm the loss: read at 10 and at 11, so it is acted on at 11.
    assert near(w.patterns.times(PATTERN_EARPHONES), [11, 41, 71, 101]), w.patterns.log


@pytest.mark.parametrize("mode", ["depth", "none"])
def test_lost_with_detection_off_plays_the_pattern_once(mode):
    w = World(mode=mode)
    w.unplug_at(10.0)
    w.run(200)
    assert near(w.patterns.times(PATTERN_EARPHONES), [11]), w.patterns.log
    assert w.alerts.is_raised(EARPHONES_MISSING), "still missing, whether or not it is repeated"


def test_detection_switched_off_mid_run_stops_the_repeats():
    """TC-025-006 (W-13): once after detection goes off, not every 30 s."""
    w = World(mode="both")
    w.unplug_at(10.0)
    w.at(50.0, lambda: w.config.update(pipeline_mode="depth"))
    w.run(200)
    assert near(w.patterns.times(PATTERN_EARPHONES), [11, 41]), w.patterns.log


def test_detection_switched_back_on_plays_again_when_30_seconds_have_passed():
    w = World(mode="depth")
    w.unplug_at(10.0)
    w.at(100.0, lambda: w.config.update(pipeline_mode="both"))
    w.run(150)
    assert near(w.patterns.times(PATTERN_EARPHONES), [11, 100, 130]), w.patterns.log


def test_a_single_bad_reading_changes_nothing():
    """A glitch in one read must not play the pattern and then say "connected"."""
    w = World()
    w.unplug_at(10.0)
    w.plug_at(10.5)          # absent for exactly one reading (the one at t=10)
    w.run(60)
    assert w.patterns.log == [] and w.spoken == [] and w.alerts.raised() == []


def test_a_single_reading_of_presence_does_not_end_a_loss():
    w = World()
    w.unplug_at(10.0)
    w.plug_at(20.0)
    w.unplug_at(20.5)        # present for one reading only (at t=20)
    w.run(60)
    assert w.alerts.is_raised(EARPHONES_MISSING)
    assert w.starts(EVERYTHING_CONNECTED_TEXT) == [] and w.starts(EARPHONES_BACK_TEXT) == []
    assert w.patterns.times(PATTERN_RECONNECT) == []


def test_back_with_nothing_else_missing_plays_reconnect_and_says_everything_is_connected_once():
    w = World()
    w.unplug_at(10.0)
    w.plug_at(50.0)
    w.run(200)
    assert near(w.patterns.times(PATTERN_EARPHONES), [11, 41]), w.patterns.log
    assert near(w.patterns.times(PATTERN_RECONNECT), [51]), w.patterns.log
    assert near(w.starts(EVERYTHING_CONNECTED_TEXT), [51]), w.spoken
    assert not w.alerts.is_raised(EARPHONES_MISSING)    # and, from the list above, no repeat after back


def test_back_while_the_camera_is_missing_names_the_part_that_returned():
    """D-17: "Everything is connected" must be true."""
    w = World()
    w.at(0.0, lambda: w.alerts.raise_alert(CAMERA_MISSING, "Critical Error: Camera cannot be reached",
                                           interval=10.0))
    w.unplug_at(1.0)
    w.plug_at(60.0)
    w.run(100)
    assert len(w.starts(EARPHONES_BACK_TEXT)) == 1, w.spoken
    assert w.starts(EVERYTHING_CONNECTED_TEXT) == [], w.spoken
    assert near(w.patterns.times(PATTERN_RECONNECT), [61]), "the reconnect pattern plays in any case"


def test_back_while_the_glasses_board_is_still_missing_names_the_part_that_returned():
    w = World()
    w.at(0.0, lambda: glasses_lost(w.alerts, w.config))
    w.unplug_at(1.0)
    w.plug_at(60.0)
    w.run(100)
    assert len(w.starts(EARPHONES_BACK_TEXT)) == 1, w.spoken
    assert w.starts(EVERYTHING_CONNECTED_TEXT) == [], w.spoken


def test_the_glasses_returning_while_the_earphones_are_out_does_not_say_everything_is_connected():
    """The case F2 left open: the earphones have no spoken alert, so they must still count as missing."""
    w = World()
    w.at(0.0, lambda: glasses_lost(w.alerts, w.config))
    w.unplug_at(1.0)
    w.at(60.0, lambda: glasses_back(w.alerts, w.patterns))
    w.run(100)
    assert len(w.starts(GLASSES_BACK_TEXT)) == 1, w.spoken
    assert w.starts(EVERYTHING_CONNECTED_TEXT) == [], w.spoken


def test_both_out_and_back_in_turn_the_last_one_says_everything_is_connected():
    w = World()
    w.at(0.0, lambda: glasses_lost(w.alerts, w.config))
    w.unplug_at(1.0)
    w.at(60.0, lambda: glasses_back(w.alerts, w.patterns))
    w.plug_at(80.0)
    w.run(130)
    assert near(w.starts(GLASSES_BACK_TEXT), [60]), w.spoken
    assert near(w.starts(EVERYTHING_CONNECTED_TEXT), [81]), w.spoken
    assert w.starts(EARPHONES_BACK_TEXT) == [], w.spoken


def test_lost_and_back_twice_says_the_return_both_times():
    """The spent "back" sentence is cleared at each loss, or the second return (same text) would be silent."""
    w = World()
    w.unplug_at(10.0)
    w.plug_at(30.0)
    w.unplug_at(50.0)
    w.plug_at(70.0)
    w.run(130)
    assert near(w.starts(EVERYTHING_CONNECTED_TEXT), [31, 71]), w.spoken
    assert near(w.patterns.times(PATTERN_EARPHONES), [11, 51]), w.patterns.log
    assert near(w.patterns.times(PATTERN_RECONNECT), [31, 71]), w.patterns.log


def test_no_setting_is_changed():
    w = World()
    w.config.update(vibration_enabled=False, tts_enabled=False, motor_strength=0.1, tts_volume=0.1)
    before = w.config.snapshot()
    w.unplug_at(10.0)
    w.plug_at(50.0)
    w.run(100)
    assert w.config.snapshot() == before


def test_the_functions_work_without_a_pattern_player():
    w = World()
    w.watching = False
    earphones_lost(w.alerts, None)
    assert w.alerts.is_raised(EARPHONES_MISSING)
    earphones_back(w.alerts, None)
    w.run(5)
    assert near(w.starts(EVERYTHING_CONNECTED_TEXT), [0])


def test_lost_asks_for_the_earphones_pattern_and_back_for_the_reconnect_pattern():
    w = World()
    w.watching = False
    earphones_lost(w.alerts, w.patterns)
    earphones_back(w.alerts, w.patterns)
    assert [n for _, n in w.patterns.log] == [PATTERN_EARPHONES, PATTERN_RECONNECT]


def test_reconnect_text_sees_the_earphones_as_missing_while_they_are_out():
    w = World()
    w.watching = False
    earphones_lost(w.alerts, None)
    assert reconnect_text(w.alerts, "Vibration is working again") == "Vibration is working again"
    w.alerts.clear(EARPHONES_MISSING)
    assert reconnect_text(w.alerts, "Vibration is working again") == EVERYTHING_CONNECTED_TEXT


def test_a_failing_action_is_retried_on_the_next_reading_not_forgotten():
    """The state moves only after the action worked, so a transient error does not lose the alert."""
    w = World()
    w.watching = False
    real = w.alerts.raise_alert
    calls = []

    def flaky(*args, **kwargs):
        calls.append(args[0])
        if len(calls) == 1:
            raise RuntimeError("scheduler hiccup")
        return real(*args, **kwargs)

    w.alerts.raise_alert = flaky
    w.present = False
    with pytest.raises(RuntimeError):
        w.watcher.step(0.0)
    w.watcher.step(1.0)
    assert w.alerts.is_raised(EARPHONES_MISSING)


def _flaky_raise(w):
    """Make the scheduler's next raise_alert() fail once, then work."""
    real = w.alerts.raise_alert
    left = {"n": 1}

    def flaky(*args, **kwargs):
        if left["n"]:
            left["n"] -= 1
            raise RuntimeError("scheduler hiccup")
        return real(*args, **kwargs)

    w.alerts.raise_alert = flaky


def test_a_failing_action_on_a_loss_is_retried_at_the_next_reading():
    w = World()
    w.watching = False
    w.watcher.step(0.0)                   # present
    _flaky_raise(w)
    w.present = False
    w.watcher.step(1.0)                   # first absent reading
    with pytest.raises(RuntimeError):
        w.watcher.step(2.0)               # confirmed; the action fails
    assert not w.alerts.is_raised(EARPHONES_MISSING)
    w.watcher.step(3.0)                   # tried again
    assert w.alerts.is_raised(EARPHONES_MISSING) and w.watcher.connected is False
    assert len(w.patterns.times(PATTERN_EARPHONES)) == 1, "the failed attempt played nothing; the retry once"


def test_a_failing_action_on_a_return_is_retried_at_the_next_reading():
    w = World(present=False)
    w.watching = False
    w.watcher.step(0.0)                   # absent at start-up: lost
    w.present = True
    w.watcher.step(1.0)                   # first present reading
    _flaky_raise(w)
    with pytest.raises(RuntimeError):
        w.watcher.step(2.0)               # confirmed; the sentence cannot be raised
    w.watcher.step(3.0)
    assert w.alerts.is_raised(EARPHONES_BACK) and not w.alerts.is_raised(EARPHONES_MISSING)
    assert w.watcher.connected is True


# ============================================================
# The real worker, reading a fake /proc/asound tree
# ============================================================
POLL = 0.03


class Rig:
    """earphones_worker in a thread, with a scheduler and a recorder on its user_data."""

    def __init__(self, monkeypatch, tmp_path, with_earphones=True, alerts=True):
        self.root = tmp_path
        this_pi(self.root)
        if not with_earphones:
            import shutil
            shutil.rmtree(self.root / "card2")
            (self.root / "Audio").unlink()
        monkeypatch.setattr(connections, "ASOUND_ROOT", self.root)
        monkeypatch.setattr(earphones_worker_module, "EARPHONES_POLL_SECONDS", POLL)
        self.config = SystemConfig()
        self.config.update(pipeline_mode="both")
        self.mailbox = PriorityMailbox()
        self.patterns = Plays(time.monotonic)
        self.ud = SimpleNamespace(shutdown_event=threading.Event(), tts_queue=self.mailbox,
                                  patterns=self.patterns)
        if alerts is True:
            self.ud.alerts = AlertScheduler(self.mailbox)
        elif alerts is not None:
            self.ud.alerts = alerts
        self.thread = threading.Thread(target=earphones_worker, args=(self.ud, self.config), daemon=True)

    def start(self):
        self.thread.start()
        return self

    def unplug(self):
        import shutil
        shutil.rmtree(self.root / "card2")

    def plug(self):
        make_card(self.root, 2, usbid="0666:0880", playback=True, capture=True)

    def wait_for(self, check, timeout, what):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            if check():
                return time.monotonic()
            time.sleep(0.005)
        pytest.fail(f"never happened: {what}")

    def stop(self):
        self.ud.shutdown_event.set()
        self.thread.join(2.0)
        assert not self.thread.is_alive()


def test_worker_with_earphones_in_place_does_nothing(monkeypatch, tmp_path):
    r = Rig(monkeypatch, tmp_path).start()
    time.sleep(10 * POLL)
    r.stop()
    assert r.ud.alerts.raised() == [] and r.patterns.log == []


def test_worker_absent_at_start_up_raises_the_silent_alert_and_asks_for_the_pattern(monkeypatch, tmp_path):
    r = Rig(monkeypatch, tmp_path, with_earphones=False).start()
    r.wait_for(lambda: r.ud.alerts.is_raised(EARPHONES_MISSING), 1.0, "the missing alert")
    r.wait_for(lambda: r.patterns.times(PATTERN_EARPHONES), 1.0, "the earphones pattern")
    time.sleep(5 * POLL)
    r.ud.alerts.poll()
    assert r.mailbox.peek() is None, "nothing to say"
    r.stop()


def test_worker_unplug_then_plug_gives_the_pattern_then_the_reconnect_and_the_sentence(monkeypatch, tmp_path):
    r = Rig(monkeypatch, tmp_path).start()
    time.sleep(5 * POLL)
    t_out = time.monotonic()
    r.unplug()
    t_lost = r.wait_for(lambda: r.ud.alerts.is_raised(EARPHONES_MISSING), 1.0, "the missing alert")
    assert t_lost - t_out < 0.5, "two readings, not many"
    r.wait_for(lambda: r.patterns.times(PATTERN_EARPHONES), 1.0, "the earphones pattern")
    r.ud.alerts.poll()
    assert r.mailbox.peek() is None
    r.plug()
    r.wait_for(lambda: r.ud.alerts.is_raised(EARPHONES_BACK), 1.0, "the return")
    assert not r.ud.alerts.is_raised(EARPHONES_MISSING)
    assert r.patterns.times(PATTERN_RECONNECT)
    r.ud.alerts.poll()
    assert r.mailbox.peek() == {"announce": EVERYTHING_CONNECTED_TEXT}
    r.stop()


def test_worker_survives_a_broken_scheduler(monkeypatch, tmp_path):
    class Broken:
        def __getattr__(self, name):
            def fail(*args, **kwargs):
                raise RuntimeError("scheduler broken")
            return fail

    r = Rig(monkeypatch, tmp_path, alerts=Broken()).start()
    time.sleep(5 * POLL)
    r.unplug()
    time.sleep(10 * POLL)
    assert r.thread.is_alive()
    r.stop()


def test_worker_without_a_pattern_player_still_raises_and_clears(monkeypatch, tmp_path):
    r = Rig(monkeypatch, tmp_path)
    del r.ud.patterns
    r.start()
    time.sleep(5 * POLL)
    r.unplug()
    r.wait_for(lambda: r.ud.alerts.is_raised(EARPHONES_MISSING), 1.0, "the missing alert")
    r.plug()
    r.wait_for(lambda: not r.ud.alerts.is_raised(EARPHONES_MISSING), 1.0, "the alert to clear")
    r.stop()


def test_worker_stops_at_once_on_shutdown_even_with_the_real_poll_interval(monkeypatch, tmp_path):
    r = Rig(monkeypatch, tmp_path)
    monkeypatch.setattr(earphones_worker_module, "EARPHONES_POLL_SECONDS", 1.0)
    r.start()
    time.sleep(0.1)
    start = time.monotonic()
    r.stop()
    assert time.monotonic() - start < 0.5


def test_the_poll_interval_is_between_one_and_two_seconds():
    """F3 spec: a presence check every 1 to 2 s. (A sysfs/procfs read each time, nothing heavier.)"""
    assert 1.0 <= earphones_worker_module.EARPHONES_POLL_SECONDS <= 2.0


# ============================================================
# The program itself starts it
# ============================================================
def test_main_starts_the_earphones_worker_and_still_stops_cleanly_on_ctrl_c(tmp_path):
    """The real program path (--mock, a do-nothing espeak, no device): the worker is started by main.py,
    and Ctrl+C still ends the program with exit 0 in time. Without this nothing proves a caller exists."""
    import os
    import signal
    import subprocess

    src = Path(__file__).resolve().parent.parent / "src"
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    stub = bin_dir / "espeak-ng"
    stub.write_text("#!/bin/sh\ncat > /dev/null 2>&1\nexit 0\n")
    stub.chmod(0o755)
    env = dict(os.environ, PATH=f"{bin_dir}{os.pathsep}{os.environ['PATH']}", PYTHONPATH=str(src),
               PYTHONUNBUFFERED="1")
    proc = subprocess.Popen([sys.executable, str(src / "second_vision" / "main.py"), "--mock"], env=env,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, cwd=tmp_path)
    ready = time.monotonic() + 60
    out = ""
    while time.monotonic() < ready and "Generators running" not in out:
        line = proc.stdout.readline()
        if not line and proc.poll() is not None:
            break
        out += line
    assert "Earphones worker started" in out, out[-800:]
    time.sleep(1.5)                                  # a few readings of the real /proc/asound
    sent = time.monotonic()
    proc.send_signal(signal.SIGINT)
    try:
        rest, _ = proc.communicate(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()
        pytest.fail("main() did not stop within 10 s of Ctrl+C")
    assert proc.returncode == 0, (proc.returncode, rest[-800:])
    assert "[MAIN] Done." in rest
    assert time.monotonic() - sent <= 2.6, "stopped too long after Ctrl+C"
