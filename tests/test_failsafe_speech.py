"""
F1: failsafe alerts are spoken even with the speech switch off, never under 30 % volume (D41).

    ~/.cache/pypoetry/virtualenvs/second-vision-repo-wwT_Rtha-py3.13/bin/python -m pytest tests/test_failsafe_speech.py -v
    my_hailo_env/bin/python -m pytest tests/test_failsafe_speech.py -v

Decision D41 (DISAGREEMENTS.md D-15): an alert raised on the alert scheduler (core/alerts.py) is a
failsafe alert. It is spoken with the speech switch off, at max(tts_volume, 0.30) on the knob's own
scale, that one alert only, and no setting changes. Detections and mode announcements still obey
the switch and the knob, including one that would cut in on an alert.

What is real: workers/tts_worker.py's loop, preemption and pacing, the PriorityMailbox, the
AlertScheduler and the alert worker thread. What is a stub: the espeak-ng process (_start_espeak),
which records the text and the amplitude (-a) and "speaks" for a set time.
"""

import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from second_vision.core.alerts import AlertScheduler
from second_vision.core.config import SystemConfig
from second_vision.core.priority import TIER_NORMAL, TIER_URGENT, PriorityMailbox
from second_vision.workers import tts_worker as tw
from second_vision.workers.alert_worker import alert_worker

ALERT = "Critical Error: Camera cannot be reached"
MODE = {"announce": "depth mode"}


def detection(label="person", zone="left", tier=TIER_NORMAL, priority=5.0):
    return {"label": label, "zone": zone, "confidence": 0.9, "priority": priority, "tier": tier}


def amp(volume):
    return tw._espeak_amplitude(volume)


# ============================================================
# _item_amplitude: who is spoken, and how loud
# ============================================================
class _Scheduler:
    """Stands in for AlertScheduler.is_failsafe: the one item it calls its own."""

    def __init__(self, own=None):
        self.own = own

    def is_failsafe(self, item):
        return item is self.own


@pytest.mark.parametrize("enabled", [True, False])
@pytest.mark.parametrize("volume, want", [(0.0, 0.30), (0.1, 0.30), (0.29, 0.30), (0.30, 0.30),
                                          (0.6, 0.6), (1.0, 1.0)])
def test_a_failsafe_alert_is_spoken_whatever_the_switch_never_under_30_percent(enabled, volume, want):
    cfg = SystemConfig()
    cfg.update(tts_enabled=enabled, tts_volume=volume)
    alert = {"announce": ALERT}
    assert tw._item_amplitude(alert, cfg, _Scheduler(alert)) == amp(want)


@pytest.mark.parametrize("item", [detection(), MODE], ids=["detection", "mode"])
def test_everything_else_obeys_the_switch_and_the_knob_as_before(item):
    cfg = SystemConfig()
    cfg.update(tts_enabled=True, tts_volume=0.0)
    assert tw._item_amplitude(item, cfg, _Scheduler()) == amp(0.0), "the 30 % floor leaked"
    assert tw._item_amplitude(item, cfg, None) == amp(0.0)
    cfg.update(tts_enabled=False)
    assert tw._item_amplitude(item, cfg, _Scheduler()) is None, "spoken with the switch off"
    assert tw._item_amplitude(item, cfg, None) is None


def test_the_amplitude_at_the_floor_is_audible_and_above_the_knob_minimum():
    assert amp(0.30) > amp(0.0) == tw._AMPLITUDE_FLOOR


# ============================================================
# The real speech worker with the alert scheduler
# ============================================================
class _Proc:
    """A stand-in espeak process: 'speaks' for a set time, or until terminated."""

    def __init__(self, seconds):
        self.end = time.monotonic() + seconds
        self.terminated = False

    def poll(self):
        if self.terminated:
            return -15
        return 0 if time.monotonic() >= self.end else None

    def terminate(self):
        self.terminated = True


class Rig:
    def __init__(self, monkeypatch, durations=None, **config):
        self.durations = durations or {}
        self.said = []
        self.lock = threading.Lock()
        monkeypatch.setattr(tw, "_start_espeak", self._start)
        self.config = SystemConfig()
        self.config.update(**config)
        mailbox = PriorityMailbox()
        self.ud = SimpleNamespace(tts_queue=mailbox, shutdown_event=threading.Event(),
                                  alerts=AlertScheduler(mailbox))
        self.threads = [threading.Thread(target=tw.tts_worker, args=(self.ud, self.config), daemon=True),
                        threading.Thread(target=alert_worker, args=(self.ud, self.config), daemon=True)]

    def _start(self, text, amplitude=tw._AMPLITUDE_MAX):
        proc = _Proc(self.durations.get(text, 0.2))
        with self.lock:
            self.said.append(SimpleNamespace(text=text, amp=amplitude, at=time.monotonic(), proc=proc))
        return proc

    def start(self):
        for t in self.threads:
            t.start()
        return self

    def texts(self):
        with self.lock:
            return [u.text for u in self.said]

    def utterances(self, text=None):
        with self.lock:
            return [u for u in self.said if text is None or u.text == text]

    def wait_for(self, pred, timeout=3.0):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            if pred():
                return True
            time.sleep(0.01)
        return pred()

    def stop(self):
        self.ud.shutdown_event.set()
        for t in self.threads:
            t.join(2.0)


def test_switch_off_the_alert_is_spoken_at_the_floor_and_nothing_else_is(monkeypatch):
    r = Rig(monkeypatch, tts_enabled=False, tts_volume=0.0).start()
    before = r.config.snapshot()
    r.ud.alerts.raise_alert("camera-missing", ALERT)
    assert r.wait_for(lambda: r.texts() == [ALERT], 2.0), r.texts()
    assert r.utterances(ALERT)[0].amp == amp(0.30)
    r.ud.tts_queue.offer(MODE)
    time.sleep(0.8)
    r.ud.tts_queue.offer(detection("car", "center", TIER_URGENT))
    time.sleep(0.8)
    r.stop()
    assert r.texts() == [ALERT], "a mode announcement or a detection was spoken with the switch off"
    assert r.config.snapshot() == before, "a setting was changed"


def test_switch_off_the_alert_still_repeats_on_its_cadence(monkeypatch):
    r = Rig(monkeypatch, tts_enabled=False).start()
    r.ud.alerts.raise_alert("camera-missing", ALERT, interval=1.0)
    time.sleep(2.6)
    r.stop()
    starts = [u.at for u in r.utterances(ALERT)]
    assert len(starts) == 3, f"{len(starts)} utterances in 2.6 s at a 1 s interval"
    assert all(0.9 < b - a < 1.2 for a, b in zip(starts, starts[1:]))


def test_switch_off_a_detection_cannot_cut_the_alert(monkeypatch):
    r = Rig(monkeypatch, durations={ALERT: 1.0}, tts_enabled=False).start()
    r.ud.alerts.raise_alert("camera-missing", ALERT)
    assert r.wait_for(lambda: r.texts() == [ALERT], 2.0)
    time.sleep(0.2)
    r.ud.tts_queue.offer(detection("car", "center", TIER_URGENT))
    r.ud.tts_queue.offer(MODE)
    time.sleep(1.5)
    r.stop()
    assert r.texts() == [ALERT]
    assert r.utterances(ALERT)[0].proc.terminated is False, "the alert was cut for an item nobody hears"


def test_switch_on_an_item_that_cuts_the_alert_keeps_its_own_volume(monkeypatch):
    """Today's preemption rule stands (D-16 is a later item); only the alert gets the 30 % floor."""
    r = Rig(monkeypatch, durations={ALERT: 1.0}, tts_enabled=True, tts_volume=0.0).start()
    r.ud.alerts.raise_alert("camera-missing", ALERT)
    assert r.wait_for(lambda: r.texts() == [ALERT], 2.0)
    time.sleep(0.2)
    r.ud.tts_queue.offer(MODE)
    assert r.wait_for(lambda: r.texts()[:3] == [ALERT, "depth mode", ALERT], 4.0), r.texts()
    r.stop()
    first, mode, again = r.utterances()[:3]
    assert first.proc.terminated, "the mode announcement cut the alert, as before"
    assert mode.amp == amp(0.0), "the mode announcement took on the alert's raised volume"
    assert first.amp == again.amp == amp(0.30), "the cut alert is spoken again, at the floor"


def test_switch_on_detections_are_spoken_at_the_knob_while_alerts_are_at_the_floor(monkeypatch):
    r = Rig(monkeypatch, tts_enabled=True, tts_volume=0.1).start()
    r.ud.tts_queue.offer(detection("chair", "right"))
    assert r.wait_for(lambda: r.texts() == ["chair right"], 2.0)
    r.ud.alerts.raise_alert("camera-missing", ALERT)
    assert r.wait_for(lambda: r.texts() == ["chair right", ALERT], 3.0)
    r.ud.tts_queue.offer(detection("table", "left"))
    assert r.wait_for(lambda: r.texts() == ["chair right", ALERT, "table left"], 3.0)
    r.stop()
    assert [u.amp for u in r.utterances()] == [amp(0.1), amp(0.30), amp(0.1)]


def test_the_switch_going_off_mid_alert_drops_the_item_that_cut_it(monkeypatch):
    """The look-then-take race: a preempting item claimed after the switch went off is not spoken."""
    r = Rig(monkeypatch, durations={ALERT: 1.0}, tts_enabled=True).start()
    calls = {"n": 0}
    real = tw._item_amplitude

    def flip_after_the_look(item, config, alerts=None):
        result = real(item, config, alerts)
        if item is MODE:
            calls["n"] += 1
            if calls["n"] == 1:            # the preemption check said yes; the switch goes off now
                config.update(tts_enabled=False)
        return result

    monkeypatch.setattr(tw, "_item_amplitude", flip_after_the_look)
    r.ud.alerts.raise_alert("camera-missing", ALERT)
    assert r.wait_for(lambda: r.texts() == [ALERT], 2.0)
    r.ud.tts_queue.offer(MODE)
    assert r.wait_for(lambda: len(r.texts()) >= 2, 3.0)
    time.sleep(0.5)
    r.stop()
    assert "depth mode" not in r.texts(), "spoken although the switch was off when it was claimed"
    assert r.texts() == [ALERT, ALERT], "the cut alert is spoken again"
