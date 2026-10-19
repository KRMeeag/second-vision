"""
T-00a: the alert scheduler (core/alerts.py) at unit level, on a VIRTUAL clock: no real waits,
no threads, no espeak. Runs in the Poetry environment and in my_hailo_env alike.

    ~/.cache/pypoetry/virtualenvs/second-vision-repo-wwT_Rtha-py3.13/bin/python -m pytest tests/test_alerts.py -v

What is real here: AlertScheduler and PriorityMailbox. What is a model: the speech worker.
`SpeechSim` follows tts_worker's rules in virtual time (one utterance at a time, an urgent or
announcement item preempts except where tw._cuts says not (D-16: no object warning cuts an alert,
and an alert waiting in the slot cuts nothing), a 0.5 s gap after an utterance unless an urgent item waits, the
head-turn mute and drain, the speech switch) and talks to the scheduler through exactly the
calls tts_worker makes: take(), drain(), finish(item, SPOKEN|CUT|DROPPED). The REAL tts_worker
with a stub espeak is in tests/test_alerts_speech.py; this file proves the scheduling logic,
that file proves the wiring.

What the randomised runs check (black box, from the log of what was spoken), for every alert:
  * never cut by another alert, never two alerts at once;
  * not doubled: delivered utterances of one alert are at least `interval` apart;
  * not late: an alert that is due is spoken as soon as the channel is free, i.e. its delay
    beyond the due time is at most the time the channel was busy (any utterance plus 0.15 s of
    hand-over, or a mute window) plus 0.2 s;
  * not lost: after a cut, a head-turn drain or a replacement in the slot, it is spoken again
    under the same rule, a once-only alert included.
"""

import heapq
import itertools
import random
import sys
import threading
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from second_vision.core.alerts import CUT, DROPPED, SPOKEN, AlertScheduler
from second_vision.core.priority import TIER_NORMAL, TIER_URGENT, PriorityMailbox, item_tier
from second_vision.workers import tts_worker as tw
from second_vision.workers.alert_worker import alert_worker

TICK = 0.05
LATE_EPS = 0.2     # scheduler tick + worker tick + float noise, beyond the time the channel was busy
HANDOFF = 0.15     # each utterance occupies the channel this long after its end: the next item is
                   # offered on the scheduler's next tick and taken on the worker's (at most 3 ticks)


class VClock:
    """A clock that only moves when the test moves it."""

    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


def detection(label="person", zone="left", priority=1.0, tier=TIER_NORMAL):
    return {"label": label, "zone": zone, "confidence": 0.9, "priority": priority, "tier": tier}


class SpeechSim:
    """tts_worker in virtual time. See the module docstring for what it follows."""

    def __init__(self, clock, mailbox, sched, duration=2.0):
        self.clock, self.mailbox, self.sched = clock, mailbox, sched
        self.duration = duration                  # seconds per utterance (float or callable(text))
        self.tts_enabled = True
        self.mute_until = -1.0
        self.gap_until = -1.0
        self.turn_pending = False
        self.current = None                       # dict: item, text, start, dur
        self.log = []                             # one dict per utterance
        self.mutes = []                           # (start, end) head-turn mute windows

    def _dur(self, text):
        return self.duration(text) if callable(self.duration) else self.duration

    def _begin(self, item, now):
        text = tw._text_for(item)
        if not self.tts_enabled:
            self.log.append(dict(text=text, item=item, start=now, end=now, outcome=DROPPED, cut_by=None))
            self.sched.finish(item, DROPPED)
            return
        self.current = dict(item=item, text=text, start=now, dur=self._dur(text))

    def _end(self, now, outcome, cut_by=None):
        c = self.current
        self.log.append(dict(text=c["text"], item=c["item"], start=c["start"], end=now,
                             outcome=outcome, cut_by=cut_by))
        self.current = None

    def head_turn(self):
        """A 0x03 turn event. tts_worker looks at it between utterances, never in the middle of one."""
        self.turn_pending = True
        if self.current is None:
            self._apply_turn()

    def _apply_turn(self):
        now = self.clock()
        self.turn_pending = False
        self.mute_until = now + tw.TURN_MUTE_SECONDS
        self.mutes.append((now, self.mute_until))
        self.sched.drain()

    def step(self):
        now = self.clock()
        if self.current is not None:
            c = self.current
            pending = self.mailbox.peek()
            # The worker's own rule (D-16): preempts(), but no object warning cuts an alert and a waiting alert cuts nothing.
            if pending is not None and tw._cuts(pending, c["item"], self.sched, self.sched.is_failsafe(c["item"])):
                nxt = self.sched.take()
                self._end(now, CUT, cut_by=tw._text_for(nxt) if nxt else None)
                self.sched.finish(c["item"], CUT)
                if nxt is not None:
                    self._begin(nxt, now)
            elif now >= c["start"] + c["dur"] - 1e-9:
                self._end(now, SPOKEN)
                self.sched.finish(c["item"], SPOKEN)
                self.gap_until = now + tw.MIN_UTTERANCE_GAP
            return
        if self.turn_pending:
            self._apply_turn()
        if now < self.mute_until - 1e-9:
            return
        if now < self.gap_until - 1e-9:
            pending = self.mailbox.peek()
            if pending is None or item_tier(pending) != TIER_URGENT:
                return
        item = self.sched.take()
        if item is not None:
            self._begin(item, now)


class World:
    """A scheduler, a real mailbox, a speech model and a virtual clock, wired as in main()."""

    def __init__(self, duration=2.0):
        self.clock = VClock()
        self.mailbox = PriorityMailbox()
        self.sched = AlertScheduler(self.mailbox, clock=self.clock)
        self.sim = SpeechSim(self.clock, self.mailbox, self.sched, duration)
        self.events = []                          # heap of (time, seq, callable)
        self._seq = itertools.count()
        self.hooks = []                           # called each tick after poll(), before the worker steps

    def at(self, t, fn):
        heapq.heappush(self.events, (t, next(self._seq), fn))

    def run(self, until, tick=TICK, shuffle=None):
        """Advance virtual time to `until`. `shuffle` (a Random) varies whether the scheduler or the worker goes first."""
        while self.clock.t < until - 1e-9:
            while self.events and self.events[0][0] <= self.clock.t + 1e-9:
                heapq.heappop(self.events)[2]()
            if shuffle is not None and shuffle.random() < 0.5:
                self.sim.step()
                self.sched.poll()
            else:
                self.sched.poll()
                for hook in self.hooks:
                    hook()
                self.sim.step()
            self.clock.t = round(self.clock.t + tick, 6)

    def starts(self, text, outcomes=(SPOKEN, DROPPED)):
        return [e["start"] for e in self.sim.log if e["text"] == text and e["outcome"] in outcomes]

    def texts(self):
        return [e["text"] for e in self.sim.log]


def approx_list(actual, expected, tol=1.0):
    return len(actual) == len(expected) and all(abs(a - b) <= tol for a, b in zip(actual, expected))


# ============================================================
# Criterion 1: spoken at once, then on the cadence (10, 30, 300 s)
# ============================================================
@pytest.mark.parametrize("interval", [10.0, 30.0, 300.0])
def test_spoken_at_once_then_every_interval(interval):
    w = World()
    w.at(0.0, lambda: w.sched.raise_alert("a", "controller missing", interval=interval))
    w.run(4 * interval + 5)

    assert approx_list(w.starts("controller missing"), [k * interval for k in range(5)]), \
        w.starts("controller missing")
    assert w.starts("controller missing")[0] <= 1.0
    # exactly {"announce": text}, nothing else, and every utterance ran to its end
    for e in w.sim.log:
        assert e["item"] == {"announce": "controller missing"}
        assert e["outcome"] == SPOKEN


def test_the_cadence_is_start_to_start_not_end_to_start():
    w = World(duration=4.0)
    w.at(0.0, lambda: w.sched.raise_alert("a", "x", interval=10.0))
    w.run(35)
    assert approx_list(w.starts("x"), [0, 10, 20, 30], tol=0.2), w.starts("x")


def test_an_alert_raised_later_is_spoken_at_once_whatever_the_clock_says():
    w = World()
    w.at(123.0, lambda: w.sched.raise_alert("a", "late one", interval=30.0))
    w.run(200)
    assert approx_list(w.starts("late one"), [123, 153, 183], tol=0.5), w.starts("late one")


# ============================================================
# Criterion 2: once
# ============================================================
def test_once_is_spoken_exactly_once():
    w = World()
    w.at(0.0, lambda: w.sched.raise_alert("a", "camera lost"))      # interval None
    w.run(1000)
    assert w.starts("camera lost") == pytest.approx([0.0], abs=0.2)
    assert w.sched.is_raised("a"), "a spent once-alert stays raised until cleared"


def test_repeat_switched_off_after_the_first_utterance_is_not_spoken_again():
    w = World()
    w.at(0.0, lambda: w.sched.raise_alert("a", "depth board", interval=30.0))
    w.at(5.0, lambda: w.sched.set_repeat("a", False))                # depth switched off mid-run
    w.run(300)
    assert len(w.starts("depth board")) == 1


def test_repeat_switched_off_before_the_first_utterance_finished_still_delivers_it_once():
    w = World(duration=4.0)
    w.at(0.0, lambda: w.sched.raise_alert("a", "depth board", interval=30.0))
    w.at(1.0, lambda: w.sched.set_repeat("a", False))                # mid-utterance
    w.run(200)
    assert [e["outcome"] for e in w.sim.log] == [SPOKEN]


def test_repeat_off_before_the_first_utterance_even_started_still_delivers_it_once():
    """The worker is busy with a mode announcement when the alert is raised and its repeat switched off."""
    w = World(duration=3.0)
    w.at(0.0, lambda: w.mailbox.offer({"announce": "depth mode"}))
    w.at(0.5, lambda: w.sched.raise_alert("a", "depth board", interval=30.0))
    w.at(1.0, lambda: w.sched.set_repeat("a", False))
    w.run(200)
    assert w.texts() == ["depth mode", "depth board"]
    assert w.sim.log[1]["outcome"] == SPOKEN


def test_repeat_given_as_a_callable_is_followed_without_any_call_from_the_producer():
    on = {"depth": True}
    w = World()
    w.at(0.0, lambda: w.sched.raise_alert("a", "depth board", interval=30.0, repeat=lambda: on["depth"]))
    w.at(40.0, lambda: on.update(depth=False))
    w.at(100.0, lambda: on.update(depth=True))
    w.run(200)
    # spoken at 0 and 30 while depth is on, none while off (40..100), then at once at 100 and every 30 s
    assert approx_list(w.starts("depth board"), [0, 30, 100, 130, 160, 190], tol=0.3), w.starts("depth board")


def test_a_failing_repeat_callable_keeps_the_last_value_and_does_not_stop_the_scheduler():
    state = {"boom": False}

    def flaky():
        if state["boom"]:
            raise RuntimeError("config gone")
        return True

    w = World()
    w.at(0.0, lambda: w.sched.raise_alert("a", "x", interval=10.0, repeat=flaky))
    w.at(15.0, lambda: state.update(boom=True))
    w.run(45)
    assert approx_list(w.starts("x"), [0, 10, 20, 30, 40], tol=0.3), w.starts("x")


# ============================================================
# Criterion 3: repeat back on
# ============================================================
def test_repeat_back_on_resumes_the_cadence_from_the_last_utterance():
    w = World()
    w.at(0.0, lambda: w.sched.raise_alert("a", "x", interval=30.0))
    w.at(10.0, lambda: w.sched.set_repeat("a", False))
    w.at(20.0, lambda: w.sched.set_repeat("a", True))                # before it falls due again
    w.run(100)
    assert approx_list(w.starts("x"), [0, 30, 60, 90], tol=0.3), w.starts("x")


def test_repeat_back_on_after_the_due_time_has_passed_speaks_at_once():
    """The literal reading of REQ-CON-03 (reported to the Lead): on again and overdue -> now."""
    w = World()
    w.at(0.0, lambda: w.sched.raise_alert("a", "x", interval=30.0))
    w.at(10.0, lambda: w.sched.set_repeat("a", False))
    w.at(100.0, lambda: w.sched.set_repeat("a", True))
    w.run(200)
    assert approx_list(w.starts("x"), [0, 100, 130, 160, 190], tol=0.3), w.starts("x")


def test_flipping_the_repeat_quickly_does_not_make_a_storm():
    w = World()
    w.at(0.0, lambda: w.sched.raise_alert("a", "x", interval=30.0))
    for k in range(1, 40):
        w.at(2.0 + k * 0.5, lambda k=k: w.sched.set_repeat("a", k % 2 == 0))
    w.at(24.0, lambda: w.sched.set_repeat("a", True))
    w.run(25)
    assert len(w.starts("x")) == 1


def test_set_repeat_on_an_alert_that_is_not_raised_does_nothing():
    w = World()
    assert w.sched.set_repeat("ghost", True) is False


# ============================================================
# Criterion 4: cleared means stopped
# ============================================================
def test_clear_stops_the_repeats_at_once():
    w = World()
    w.at(0.0, lambda: w.sched.raise_alert("a", "x", interval=10.0))
    w.at(15.0, lambda: w.sched.clear("a"))
    w.run(100)
    assert approx_list(w.starts("x"), [0, 10], tol=0.3)
    assert not w.sched.is_raised("a")


def test_clear_does_not_cut_an_utterance_under_way():
    w = World(duration=4.0)
    w.at(0.0, lambda: w.sched.raise_alert("a", "x", interval=10.0))
    w.at(1.0, lambda: w.sched.clear("a"))
    w.run(60)
    assert [(e["text"], e["outcome"]) for e in w.sim.log] == [("x", SPOKEN)]


def test_clear_removes_its_own_queued_item_from_the_mailbox():
    w = World()
    w.sim.mute_until = 50.0                                          # the worker is not taking
    w.at(0.0, lambda: w.sched.raise_alert("a", "x", interval=10.0))
    w.at(5.0, lambda: w.sched.clear("a"))
    w.run(4.0)
    assert w.mailbox.peek() == {"announce": "x"}                    # queued, untaken
    w.run(6.0)
    assert w.mailbox.peek() is None
    w.run(100)
    assert w.sim.log == []


def test_clear_of_a_name_that_is_not_raised_does_nothing_and_touches_no_one_elses_item():
    w = World()
    mode = {"announce": "depth mode"}
    w.mailbox.offer(mode)
    assert w.sched.clear("ghost") is False
    assert w.mailbox.peek() is mode


def test_clear_leaves_a_mode_announcement_that_replaced_its_queued_item():
    w = World()
    w.sim.mute_until = 50.0
    w.sched.raise_alert("a", "x", interval=10.0)
    w.sched.poll()
    assert w.mailbox.peek() == {"announce": "x"}
    mode = {"announce": "depth mode"}
    w.mailbox.offer(mode)                                            # the tie: incoming wins
    assert w.mailbox.peek() is mode
    assert w.sched.clear("a") is True
    assert w.mailbox.peek() is mode, "clear() removed an item that was not the scheduler's own"


def test_clear_leaves_a_detection_item_alone():
    w = World()
    det = detection()
    w.sched.raise_alert("a", "x", interval=10.0)
    w.mailbox.offer(det)                                             # detection in the slot, not ours
    assert w.sched.clear("a") is True
    assert w.mailbox.peek() is det


def test_clear_then_raise_again_speaks_at_once():
    w = World()
    w.at(0.0, lambda: w.sched.raise_alert("a", "x"))
    w.at(20.0, lambda: w.sched.clear("a"))
    w.at(30.0, lambda: w.sched.raise_alert("a", "x"))
    w.run(60)
    assert approx_list(w.starts("x"), [0, 30], tol=0.3)


# ============================================================
# Criterion 5: several alerts never cut or lose each other
# ============================================================
def test_alerts_due_together_are_spoken_one_after_the_other_each_to_the_end():
    w = World(duration=3.0)
    for name, text in [("a", "alpha"), ("b", "bravo"), ("c", "charlie")]:
        w.at(0.0, lambda name=name, text=text: w.sched.raise_alert(name, text))
    w.run(30)
    assert w.texts() == ["alpha", "bravo", "charlie"]               # in the order raised
    for first, second in zip(w.sim.log, w.sim.log[1:]):
        assert second["start"] >= first["end"] - 1e-9
    assert all(e["outcome"] == SPOKEN for e in w.sim.log)


def test_alerts_of_10_30_and_300_seconds_keep_their_own_cadence():
    w = World(duration=2.0)
    w.at(0.0, lambda: w.sched.raise_alert("a", "ten", interval=10.0))
    w.at(0.0, lambda: w.sched.raise_alert("b", "thirty", interval=30.0))
    w.at(0.0, lambda: w.sched.raise_alert("c", "threehundred", interval=300.0))
    w.run(620)
    # Alerts due together queue behind each other (2 s each), so a start may slip by a few seconds.
    assert approx_list(w.starts("ten"), [10 * k for k in range(62)], tol=7.0)
    assert approx_list(w.starts("thirty"), [30 * k for k in range(21)], tol=7.0)
    assert approx_list(w.starts("threehundred"), [0, 300, 600], tol=7.0)
    assert all(e["outcome"] == SPOKEN for e in w.sim.log)
    assert all(e["cut_by"] is None for e in w.sim.log)


def test_an_alert_is_not_offered_while_another_is_queued_or_speaking():
    w = World(duration=3.0)
    w.sched.raise_alert("a", "alpha")
    w.sched.raise_alert("b", "bravo")
    w.sched.poll()
    assert w.mailbox.peek() == {"announce": "alpha"}
    w.sched.poll()
    w.sched.poll()
    assert w.mailbox.peek() == {"announce": "alpha"}                 # bravo waits; it did not replace alpha
    assert w.sched.take() == {"announce": "alpha"}
    w.sched.poll()
    assert w.mailbox.peek() is None                                  # alpha is being spoken: bravo still waits


def test_an_alert_waits_for_a_mode_announcement_in_the_slot_and_does_not_replace_it():
    w = World()
    mode = {"announce": "depth mode"}
    w.mailbox.offer(mode)
    w.sched.raise_alert("a", "x")
    w.sched.poll()
    assert w.mailbox.peek() is mode


def test_an_alert_waits_for_the_worker_to_finish_a_detection_phrase_instead_of_cutting_it():
    w = World(duration=3.0)
    w.at(0.0, lambda: w.mailbox.offer(detection()))
    w.at(0.5, lambda: w.sched.raise_alert("a", "x"))
    w.run(20)
    assert w.texts() == ["person left", "x"]
    assert [e["outcome"] for e in w.sim.log] == [SPOKEN, SPOKEN]


def test_an_alert_replaces_a_waiting_detection_item_in_the_slot():
    """A detection item in the slot never blocks an alert (the scene is re-offered every frame)."""
    w = World()
    w.sim.mute_until = 5.0
    w.mailbox.offer(detection())
    w.sched.raise_alert("a", "x")
    w.sched.poll()
    assert w.mailbox.peek() == {"announce": "x"}


# ============================================================
# Criterion 6: not lost
# ============================================================
def test_an_alert_cut_by_a_mode_announcement_is_spoken_again_as_soon_as_the_channel_is_free():
    w = World(duration=4.0)
    w.at(0.0, lambda: w.sched.raise_alert("a", "x", interval=30.0))
    w.at(1.0, lambda: w.mailbox.offer({"announce": "depth mode"}))   # mid-utterance
    w.run(20)
    log = [(e["text"], e["outcome"]) for e in w.sim.log]
    assert log == [("x", CUT), ("depth mode", SPOKEN), ("x", SPOKEN)]
    mode_end = w.sim.log[1]["end"]
    assert w.sim.log[2]["start"] - mode_end <= LATE_EPS


def test_a_once_only_alert_that_was_cut_is_spoken_again_and_then_not_again():
    w = World(duration=4.0)
    w.at(0.0, lambda: w.sched.raise_alert("a", "x"))
    w.at(1.0, lambda: w.mailbox.offer({"announce": "depth mode"}))
    w.run(300)
    assert [(e["text"], e["outcome"]) for e in w.sim.log] == [("x", CUT), ("depth mode", SPOKEN), ("x", SPOKEN)]


def test_d16_an_urgent_detection_waits_for_an_alert_in_progress():
    """D-16 (user, 2026-10-18): an alert in progress finishes. Until F7 the car cut it and it was said again."""
    w = World(duration=4.0)
    w.at(0.0, lambda: w.sched.raise_alert("a", "x"))
    w.at(1.0, lambda: w.mailbox.offer(detection("car", "center", priority=9.0, tier=TIER_URGENT)))
    w.run(30)
    log = w.sim.log
    assert [(e["text"], e["outcome"]) for e in log] == [("x", SPOKEN), ("car center", SPOKEN)]
    assert log[1]["start"] >= log[0]["end"] - 1e-9


def test_d16_an_alert_due_during_an_urgent_detection_waits_for_its_end_and_goes_next():
    """The alert does not cut the warning being said, and the next warning does not get ahead of it."""
    w = World(duration=4.0)
    w.at(0.0, lambda: w.mailbox.offer(detection("car", "center", priority=9.0, tier=TIER_URGENT)))
    w.at(1.0, lambda: w.sched.raise_alert("a", "x"))
    w.at(2.0, lambda: w.mailbox.offer(detection("bus", "center", priority=9.5, tier=TIER_URGENT)))
    w.run(30)
    log = w.sim.log
    assert [(e["text"], e["outcome"]) for e in log] == [("car center", SPOKEN), ("x", SPOKEN)]
    assert log[1]["start"] - log[0]["end"] <= HANDOFF + 1e-9, "said as soon as the warning ended"


def test_d16_urgent_detections_that_cut_each_other_cannot_keep_an_alert_out():
    """The case the Tester found (F-1): a new urgent warning every 0.1 s, each cutting the last, so the worker was
    never idle and the alert was never offered. Now it is offered behind the warning and said when it ends."""
    w = World(duration=1.2)
    t = 0.0
    while t < 20.0:
        w.at(t, lambda: w.mailbox.offer(detection("car", "center", priority=9.0, tier=TIER_URGENT)))
        t = round(t + 0.1, 6)
    w.at(5.0, lambda: w.sched.raise_alert("a", "x"))
    w.run(20)
    spoken = [e for e in w.sim.log if e["text"] == "x"]
    assert [e["outcome"] for e in spoken] == [SPOKEN], spoken
    assert spoken[0]["start"] - 5.0 <= 1.2 + HANDOFF + 1e-9, "at most one warning's length late"


def test_a_head_turn_does_not_discard_an_alert_waiting_in_the_slot():
    w = World()
    w.sim.mute_until = 0.4                                           # worker busy elsewhere for a moment
    w.at(0.0, lambda: w.sched.raise_alert("a", "x", interval=30.0))
    w.at(0.2, w.sim.head_turn)                                       # item is in the slot, untaken
    w.run(6)
    assert w.texts() == ["x"]
    assert w.sim.log[0]["start"] >= 0.2 + tw.TURN_MUTE_SECONDS - 0.1, "spoken after the mute window"
    assert w.sim.log[0]["outcome"] == SPOKEN


def test_a_head_turn_still_drains_a_mode_announcement_and_a_detection_item():
    """R2: today's drain behaviour is unchanged for everything that is not the scheduler's item."""
    w = World()
    mode = {"announce": "depth mode"}
    w.mailbox.offer(mode)
    w.sim.head_turn()
    assert w.mailbox.peek() is None
    w.mailbox.offer(detection())
    w.sim.head_turn()
    assert w.mailbox.peek() is None


def test_drain_leaves_the_schedulers_waiting_item_in_the_slot_and_drains_everything_else():
    mb = PriorityMailbox()
    sched = AlertScheduler(mb, clock=VClock())
    sched.raise_alert("a", "x", interval=10.0)
    sched.poll()
    ours = mb.peek()
    assert sched.drain() is None and mb.peek() is ours               # the alert waits through the turn
    mode = {"announce": "depth mode"}
    mb.offer(mode)                                                   # replaces ours (tie)
    assert sched.drain() is mode and mb.peek() is None               # a mode announcement is drained, as before
    det = detection()
    mb.offer(det)
    assert sched.drain() is det and mb.peek() is None                # and a detection item


def test_an_alert_that_is_being_spoken_is_not_in_the_slot_so_a_drain_takes_whatever_else_is_there():
    mb = PriorityMailbox()
    sched = AlertScheduler(mb, clock=VClock())
    sched.raise_alert("a", "x", interval=10.0)
    sched.poll()
    assert sched.take() == {"announce": "x"}                         # now speaking
    det = detection()
    mb.offer(det)
    assert sched.drain() is det


def test_a_cleared_alert_that_is_cut_is_not_spoken_again_and_not_reported_as_coming_back(capsys):
    w = World(duration=4.0)
    w.at(0.0, lambda: w.sched.raise_alert("a", "x", interval=10.0))
    w.at(1.0, lambda: w.sched.clear("a"))
    w.at(2.0, lambda: w.mailbox.offer({"announce": "depth mode"}))   # cuts the cleared alert's utterance
    w.run(60)
    assert w.texts() == ["x", "depth mode"]
    assert [e["outcome"] for e in w.sim.log] == [CUT, SPOKEN]
    assert "will be spoken again" not in capsys.readouterr().out


def test_an_alert_replaced_in_the_slot_by_a_mode_announcement_is_offered_again():
    w = World(duration=3.0)
    w.sim.mute_until = 1.0                                           # keep the alert waiting in the slot
    w.at(0.0, lambda: w.sched.raise_alert("a", "x", interval=30.0))
    w.at(0.5, lambda: w.mailbox.offer({"announce": "depth mode"}))   # the tie: replaces it
    w.run(30)
    assert w.texts() == ["depth mode", "x"]
    assert w.sim.log[1]["outcome"] == SPOKEN


def test_speech_switch_off_counts_as_delivered_and_the_next_repeat_tries_again():
    w = World()
    w.sim.tts_enabled = False
    w.at(0.0, lambda: w.sched.raise_alert("a", "x", interval=30.0))
    w.at(100.0, lambda: setattr(w.sim, "tts_enabled", True))
    w.run(140)
    outcomes = [(round(e["start"]), e["outcome"]) for e in w.sim.log]
    assert outcomes == [(0, DROPPED), (30, DROPPED), (60, DROPPED), (90, DROPPED), (120, SPOKEN)]


def test_a_once_alert_dropped_by_the_speech_switch_is_not_offered_again():
    w = World()
    w.sim.tts_enabled = False
    w.at(0.0, lambda: w.sched.raise_alert("a", "x"))
    w.at(10.0, lambda: setattr(w.sim, "tts_enabled", True))
    w.run(100)
    assert [e["outcome"] for e in w.sim.log] == [DROPPED]


def test_a_failed_utterance_counts_as_delivered_through_release():
    mb = PriorityMailbox()
    clock = VClock()
    sched = AlertScheduler(mb, clock=clock)
    sched.raise_alert("a", "x", interval=30.0)
    sched.poll()
    item = sched.take()
    sched.release(DROPPED)                                           # the worker raised
    clock.t = 1.0
    sched.poll()
    assert mb.peek() is None, "a failure must not be re-offered in a storm"
    clock.t = 30.0
    sched.poll()
    assert mb.peek() == {"announce": "x"} and mb.peek() is not item


# ============================================================
# Criterion 7: raise again
# ============================================================
def test_raising_the_same_alert_again_changes_nothing():
    w = World()
    for t in (0.0, 0.5, 3.0, 12.0, 25.0):
        w.at(t, lambda: w.sched.raise_alert("a", "x", interval=30.0))
    w.run(50)
    assert approx_list(w.starts("x"), [0, 30], tol=0.3), w.starts("x")
    assert w.sched.raised() == ["a"]


def test_raising_with_a_different_text_replaces_the_alert():
    w = World()
    w.at(0.0, lambda: w.sched.raise_alert("a", "old wording", interval=30.0))
    w.at(40.0, lambda: w.sched.raise_alert("a", "new wording", interval=30.0))
    w.run(120)
    assert approx_list(w.starts("old wording"), [0, 30], tol=0.3)
    assert approx_list(w.starts("new wording"), [40, 70, 100], tol=0.3)
    assert w.sched.raised() == ["a"]


def test_raising_with_a_different_interval_replaces_the_alert():
    w = World()
    w.at(0.0, lambda: w.sched.raise_alert("a", "x", interval=30.0))
    w.at(5.0, lambda: w.sched.raise_alert("a", "x", interval=10.0))
    w.run(40)
    assert approx_list(w.starts("x"), [0, 5, 15, 25, 35], tol=0.3), w.starts("x")


def test_replacing_an_alert_pulls_its_queued_old_item():
    w = World()
    w.sim.mute_until = 50.0
    w.sched.raise_alert("a", "old", interval=30.0)
    w.sched.poll()
    assert w.mailbox.peek() == {"announce": "old"}
    w.sched.raise_alert("a", "new", interval=30.0)
    assert w.mailbox.peek() is None
    w.sched.poll()
    assert w.mailbox.peek() == {"announce": "new"}


def test_replacing_an_alert_while_it_is_being_spoken_lets_it_finish_then_speaks_the_new_one():
    w = World(duration=4.0)
    w.at(0.0, lambda: w.sched.raise_alert("a", "old", interval=30.0))
    w.at(1.0, lambda: w.sched.raise_alert("a", "new", interval=30.0))
    w.run(20)
    assert [(e["text"], e["outcome"]) for e in w.sim.log] == [("old", SPOKEN), ("new", SPOKEN)]


@pytest.mark.parametrize("args", [
    dict(name="", text="x"), dict(name="a", text=""), dict(name="a", text="   "),
    dict(name="a", text="x", interval=0), dict(name="a", text="x", interval=-5),
    dict(name="a", text="x", interval=float("nan")), dict(name="a", text="x", interval="30"),
])
def test_a_bad_raise_is_refused_loudly_and_changes_nothing(args):
    mb = PriorityMailbox()
    sched = AlertScheduler(mb)
    with pytest.raises(ValueError):
        sched.raise_alert(**args)
    assert sched.raised() == []


# ============================================================
# Criterion 8: threads and shutdown
# ============================================================
def test_the_api_is_safe_from_many_threads_and_never_blocks():
    mb = PriorityMailbox()
    sched = AlertScheduler(mb)
    errors = []

    def hammer(k):
        try:
            for i in range(1500):
                name = f"n{(k + i) % 4}"
                sched.raise_alert(name, f"text {name}", interval=float(10 + (i % 3)), repeat=(i % 2 == 0))
                sched.set_repeat(name, i % 3 == 0)
                if i % 5 == 0:
                    sched.clear(name)
                sched.poll()
                sched.is_raised(name)
                sched.raised()
        except Exception as e:  # pragma: no cover - the assertion below reports it
            errors.append(repr(e))

    threads = [threading.Thread(target=hammer, args=(k,)) for k in range(6)]
    started = time.monotonic()
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
    assert not any(t.is_alive() for t in threads), "a call blocked"
    assert errors == []
    assert time.monotonic() - started < 20


def test_the_alert_worker_is_a_daemon_that_stops_within_a_quarter_second_of_shutdown():
    class UserData:
        pass

    ud = UserData()
    ud.tts_queue = PriorityMailbox()
    ud.alerts = AlertScheduler(ud.tts_queue)
    ud.shutdown_event = threading.Event()
    t = threading.Thread(target=alert_worker, args=(ud, None), daemon=True)
    t.start()
    ud.alerts.raise_alert("a", "x", interval=10.0)
    time.sleep(0.3)
    assert ud.tts_queue.peek() == {"announce": "x"}, "the worker thread did not offer a raised alert"
    stop = time.monotonic()
    ud.shutdown_event.set()
    t.join(timeout=1.0)
    assert not t.is_alive()
    assert time.monotonic() - stop <= 0.25
    assert t.daemon


def test_the_alert_worker_survives_a_failing_poll():
    class Exploding:
        calls = 0

        def poll(self):
            Exploding.calls += 1
            raise RuntimeError("boom")

    class UserData:
        alerts = Exploding()
        shutdown_event = threading.Event()

    t = threading.Thread(target=alert_worker, args=(UserData, None), daemon=True)
    t.start()
    time.sleep(0.3)
    UserData.shutdown_event.set()
    t.join(timeout=1.0)
    assert Exploding.calls >= 3 and not t.is_alive()


# ============================================================
# Randomised runs: the four loss paths (virtual clock). "Fixed means repeated."
# ============================================================
ALERTS = [("a10", "ten second alert", 10.0), ("a30", "thirty second alert", 30.0),
          ("a300", "five minute alert", 300.0)]
ONCE = ("once", "once only alert", None)
MODE = {"announce": "depth mode"}


def busy_measure(intervals, a, b):
    """Length of [a, b] covered by the union of `intervals` (the time the speech channel was occupied)."""
    covered = 0.0
    cursor = a
    for s, e in sorted(intervals):
        s, e = max(s, cursor), min(e, b)
        if e > s:
            covered += e - s
            cursor = e
    return covered


def check_invariants(world, specs, t_end, raised_at, loss_events):
    """
    Raise AssertionError if an alert was lost, doubled, late or cut by another alert.
    `specs`: (name, text, interval); `raised_at`: name -> time; `loss_events`: (name, time)
    for losses the speech log does not show (a head-turn drain, a replacement in the slot).
    """
    log = world.sim.log
    busy = [(e["start"], e["end"] + HANDOFF) for e in log if e["end"] > e["start"]] + list(world.sim.mutes)
    in_progress = world.sim.current                                    # not in the log yet
    if in_progress is not None:
        busy.append((in_progress["start"], t_end))
    waiting = world.mailbox.peek()                                     # offered, not yet taken
    alert_texts = {text for _, text, _ in specs}

    for e in log:
        if e["outcome"] == CUT and e["text"] in alert_texts:
            assert e["cut_by"] not in alert_texts, f"alert cut by another alert: {e}"

    for name, text, interval in specs:
        delivered = world.starts(text)
        if interval is None:
            assert len(delivered) == 1, f"{name}: once-alert spoken {len(delivered)} times: {delivered}"
        for s1, s2 in zip(delivered, delivered[1:]):                    # not doubled
            assert s2 - s1 >= interval - 1e-6, f"{name} doubled: {s1:.2f} then {s2:.2f}"
        # Not late, not skipped: each utterance follows its due time as soon as the channel is free.
        dues = [raised_at[name]] + [s + interval for s in delivered if interval is not None]
        for due, spoken in zip(dues, delivered):
            idle_wait = (spoken - due) - busy_measure(busy, due, spoken)
            assert idle_wait <= LATE_EPS, f"{name} late: due {due:.2f}, spoken {spoken:.2f}, idle wait {idle_wait:.2f}"
        speaking_now = (in_progress is not None and in_progress["text"] == text) or \
                       (waiting is not None and waiting.get("announce") == text)
        if len(dues) > len(delivered) and not speaking_now:             # one more is owed at the end
            due = dues[len(delivered)]
            assert (t_end - due) - busy_measure(busy, due, t_end) <= LATE_EPS, \
                f"{name} overdue at the end: due {due:.2f}"
        # Not lost: after a cut, a drain or a replacement it is spoken again as soon as the channel is free.
        losses = [e["end"] for e in log if e["text"] == text and e["outcome"] == CUT]
        losses += [t for n, t in loss_events if n == name]
        for lost_at in losses:
            later = [s for s in delivered if s >= lost_at - 1e-6]
            until = later[0] if later else t_end
            idle_wait = (until - lost_at) - busy_measure(busy, lost_at, until)
            assert idle_wait <= LATE_EPS, f"{name} not repaired after the loss at {lost_at:.2f}: idle wait {idle_wait:.2f}"


def random_world(seed, path):
    """One randomised run. `path` forces one loss path on purpose: cut, drain, tie or off."""
    rng = random.Random(seed)
    w = World(duration=lambda text: rng.choice([1.5, 2.0, 3.0]))
    specs = list(ALERTS) + ([ONCE] if seed % 2 == 0 else [])
    raised_at = {}
    for name, text, interval in specs:
        raised_at[name] = 0.0 if name == "a300" else round(rng.uniform(0.0, 3.0), 2)
        w.at(raised_at[name], lambda name=name, text=text, interval=interval:
             w.sched.raise_alert(name, text, interval=interval))
    t_end = 330.0
    loss_events = []
    by_text = {text: name for name, text, _ in specs}

    # Noise common to all paths: mode announcements and detection phrases at random moments.
    t = rng.uniform(2, 20)
    while t < t_end:
        w.at(t, lambda: w.mailbox.offer(dict(MODE)))
        t += rng.expovariate(1 / 40.0)
    t = rng.uniform(0, 5)
    while t < t_end:
        urgent = rng.random() < 0.1
        w.at(t, lambda urgent=urgent: w.mailbox.offer(detection(
            "car" if urgent else "person", "center", priority=9.0 if urgent else 1.0,
            tier=TIER_URGENT if urgent else TIER_NORMAL)))
        t += rng.uniform(0.5, 6.0)

    if path == "cut":                       # a mode announcement in the middle of an alert utterance
        def cut_hook():
            c = w.sim.current
            if c is not None and c["text"] in by_text and rng.random() < 0.03:
                w.mailbox.offer(dict(MODE))
        w.hooks.append(cut_hook)
    elif path == "drain":                   # head turns, many while an alert waits in the slot
        def drain_hook():
            peek = w.mailbox.peek()
            if peek is not None and peek.get("announce") in by_text and rng.random() < 0.3:
                loss_events.append((by_text[peek["announce"]], w.clock.t))
                w.sim.head_turn()
            elif rng.random() < 0.004:
                w.sim.head_turn()
        w.hooks.append(drain_hook)
    elif path == "tie":                     # a mode announcement overwrites the alert in the slot
        def tie_hook():
            peek = w.mailbox.peek()
            if peek is not None and peek.get("announce") in by_text and rng.random() < 0.4:
                loss_events.append((by_text[peek["announce"]], w.clock.t))
                w.mailbox.offer(dict(MODE))
        w.hooks.append(tie_hook)
    elif path == "off":                     # the speech switch off for stretches
        t = rng.uniform(5, 30)
        while t < t_end:
            span = rng.uniform(5, 60)
            w.at(t, lambda: setattr(w.sim, "tts_enabled", False))
            w.at(t + span, lambda: setattr(w.sim, "tts_enabled", True))
            t += span + rng.uniform(10, 60)
    else:  # pragma: no cover
        raise AssertionError(path)

    w.run(t_end, shuffle=rng)
    return w, specs, t_end, raised_at, loss_events


@pytest.mark.parametrize("path", ["cut", "drain", "tie", "off"])
def test_randomised_runs_never_lose_double_or_cut_an_alert(path, capsys):
    runs = 50
    loss_count = 0
    for seed in range(runs):
        w, specs, t_end, raised_at, loss_events = random_world(seed, path)
        try:
            check_invariants(w, specs, t_end, raised_at, loss_events)
        except AssertionError as e:
            raise AssertionError(f"{path} seed {seed}: {e}") from None
        texts = {text for _, text, _ in specs}
        loss_count += len(loss_events) + sum(
            1 for e in w.sim.log if e["text"] in texts and e["outcome"] in (CUT, DROPPED))
    capsys.readouterr()
    assert loss_count >= 3 * runs, f"{path}: the forced loss path barely happened ({loss_count} events in {runs} runs)"
    print(f"\n[T-00a] {path}: {runs} randomised runs, {loss_count} forced loss events, no alert lost, doubled or cut by another alert")
