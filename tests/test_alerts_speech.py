"""
T-00a: the alert scheduler together with the REAL speech worker, in real time.

    ~/.cache/pypoetry/virtualenvs/second-vision-repo-wwT_Rtha-py3.13/bin/python -m pytest tests/test_alerts_speech.py -v
    my_hailo_env/bin/python -m pytest tests/test_alerts_speech.py -v

What is real: workers/tts_worker.py (its own loop, preemption, pacing, head-turn mute, volume,
speech switch), core/priority.py's PriorityMailbox, core/turn_event.py, core/alerts.py, the
alert worker thread, and the espeak-ng subprocess protocol (Popen, terminate). What is a stub:
`espeak-ng` itself. A script first on PATH records when each phrase starts and ends (CLOCK_MONOTONIC,
comparable with the scheduler's clock) and runs for a set time; it logs "cut" when it is terminated.
No sound, no device. tests/test_alerts.py proves the scheduling logic on a virtual clock; this file
proves the wiring and the loss paths against the real worker.

Run lengths. The default run is short (about a minute). Two environment variables lengthen it:
  SV_ALERT_RUNS=20       randomised runs per loss path (default 1); the report quotes 20
  SV_REAL_CADENCE=1      the brief's real-time cadences: alerts of 10 s and 30 s for 96 s with
                         announcements and detections arriving at random (about 100 s). In
                         my_hailo_env also set PYTEST_TIMEOUT=300 (its default is 60 s per test).
"""

import json
import os
import random
import signal
import subprocess
import sys
import threading
import time
import importlib.util
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC))

from second_vision.core.config import SystemConfig
from second_vision.core.priority import TIER_NORMAL, TIER_URGENT, PriorityMailbox
from second_vision.core.turn_event import TurnEvent
from second_vision.workers import tts_worker as tw

try:                                    # absent on the T-00a baseline: the guard tests then run without it
    from second_vision.core.alerts import AlertScheduler
    from second_vision.workers.alert_worker import alert_worker
except ImportError:                     # pragma: no cover
    AlertScheduler = None
    alert_worker = None

STUB = """#!{python} -S
import json, os, signal, sys, time
text = sys.argv[-1]
cfg = json.loads(os.environ.get("ESPEAK_STUB_CONFIG", "{{}}"))
duration = cfg.get("texts", {{}}).get(text, cfg.get("default", 0.3))
log = os.open(os.environ["ESPEAK_STUB_LOG"], os.O_WRONLY | os.O_APPEND | os.O_CREAT)
def rec(ev, **kw):
    os.write(log, (json.dumps(dict(ev=ev, text=text, t=time.monotonic(), pid=os.getpid(), **kw)) + "\\n").encode())
def on_term(signum, frame):
    rec("end", cut=True)
    os._exit(143)
signal.signal(signal.SIGTERM, on_term)
rec("start", argv=sys.argv[1:])
time.sleep(duration)
rec("end", cut=False)
"""

MODE = {"announce": "depth mode"}
_REAL_CHECK_TURN_MUTE = tw._check_turn_mute      # the worker's own, captured once (Rigs wrap it per test)
_RIG_COUNT = [0]


def detection(label="person", zone="left", priority=1.0, tier=TIER_NORMAL):
    return {"label": label, "zone": zone, "confidence": 0.9, "priority": priority, "tier": tier}


class UserData:
    """What the workers see of SecondVisionUserData (main.py builds the real one the same way)."""

    def __init__(self, with_alerts):
        self.tts_queue = PriorityMailbox()
        self.shutdown_event = threading.Event()
        self.turn_event = TurnEvent()
        if with_alerts:
            self.alerts = AlertScheduler(self.tts_queue)


class Rig:
    """The real tts_worker (and alert_worker) on a stub espeak-ng."""

    def __init__(self, tmp_path, monkeypatch, default=0.3, texts=None, with_alerts=True):
        if with_alerts and AlertScheduler is None:
            pytest.fail("this tree has no alert scheduler (core/alerts.py)")
        _RIG_COUNT[0] += 1
        self.dir = tmp_path / f"rig{_RIG_COUNT[0]}"
        self.dir.mkdir()
        self.monkeypatch = monkeypatch
        bin_dir = self.dir / "bin"
        bin_dir.mkdir()
        stub = bin_dir / "espeak-ng"
        stub.write_text(STUB.format(python=sys.executable))
        stub.chmod(0o755)
        self.log_path = self.dir / "espeak.log"
        monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
        monkeypatch.setenv("ESPEAK_STUB_LOG", str(self.log_path))
        self.durations(default, texts)
        self.config = SystemConfig()
        self.ud = UserData(with_alerts)
        self.offers = []                              # (time, item) of everything offered to the mailbox
        real_offer = self.ud.tts_queue.offer

        def spy(item):
            self.offers.append((time.monotonic(), item))
            real_offer(item)
        self.ud.tts_queue.offer = spy
        self.turns = []                               # (time, mute deadline) when the worker consumed a turn

        def check(turn_event, mailbox, now, mute_until, *a, **k):
            out = _REAL_CHECK_TURN_MUTE(turn_event, mailbox, now, mute_until, *a, **k)
            if out != mute_until:
                self.turns.append((now, out))
            return out
        monkeypatch.setattr(tw, "_check_turn_mute", check)
        self.threads = []

    def durations(self, default, texts=None):
        self.monkeypatch.setenv("ESPEAK_STUB_CONFIG", json.dumps({"default": default, "texts": texts or {}}))

    def start(self):
        for target in (tw.tts_worker, alert_worker if hasattr(self.ud, "alerts") else None):
            if target is not None:
                t = threading.Thread(target=target, args=(self.ud, self.config), daemon=True)
                t.start()
                self.threads.append(t)

    def stop(self):
        self.ud.shutdown_event.set()
        for t in self.threads:
            t.join(timeout=3)
        assert not any(t.is_alive() for t in self.threads), "a worker did not stop"
        time.sleep(0.3)                               # let a terminated stub write its last line

    def utterances(self):
        """[{text, start, end, cut}] in start order, from the stub's log."""
        started, out = {}, []
        for line in self.log_path.read_text().splitlines() if self.log_path.exists() else []:
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            if rec["ev"] == "start":
                started[rec["pid"]] = rec
            elif rec["pid"] in started:
                s = started.pop(rec["pid"])
                out.append(dict(text=s["text"], start=s["t"], end=rec["t"], cut=rec["cut"], argv=s["argv"]))
        return sorted(out, key=lambda u: u["start"])

    def texts(self):
        return [u["text"] for u in self.utterances()]

    def started(self):
        """Texts of every phrase the stub has started (finished or still running)."""
        out = []
        try:
            for line in self.log_path.read_text().splitlines():
                rec = json.loads(line)
                if rec["ev"] == "start":
                    out.append(rec["text"])
        except (OSError, ValueError):
            pass
        return out

    def wait_for(self, predicate, timeout=10.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if predicate():
                return True
            time.sleep(0.02)
        return False


@pytest.fixture
def rig(tmp_path, monkeypatch):
    made = []

    def make(**kw):
        r = Rig(tmp_path, monkeypatch, **kw)
        made.append(r)
        return r
    yield make
    for r in made:
        r.ud.shutdown_event.set()


# ============================================================
# Criterion 9: nothing else changes (guards: they also run on the baseline, without a scheduler)
# ============================================================
@pytest.mark.parametrize("with_alerts", [False, True])
def test_a_detection_phrase_and_a_mode_announcement_behave_as_before(rig, with_alerts):
    """Scheduler running and idle (or absent): speech order, preemption, pacing, volume are today's."""
    r = rig(default=0.4, with_alerts=with_alerts)
    r.config.update(tts_volume=0.0)
    r.start()
    r.ud.tts_queue.offer(detection("person", "left"))
    assert r.wait_for(lambda: len(r.started()) >= 1)
    time.sleep(0.1)
    r.ud.tts_queue.offer(detection("car", "center", priority=9.0, tier=TIER_URGENT))     # preempts a normal phrase
    assert r.wait_for(lambda: "car center" in r.started())
    time.sleep(0.1)
    r.ud.tts_queue.offer(MODE)                                                           # and an announcement
    assert r.wait_for(lambda: "depth mode" in r.texts(), 5)
    time.sleep(0.8)
    r.stop()
    u = r.utterances()
    assert [x["text"] for x in u] == ["person left", "car center", "depth mode"]
    assert [x["cut"] for x in u] == [True, True, False], "person cut by the urgent car, car cut by the announcement"
    assert all(x["argv"][x["argv"].index("-a") + 1] == str(tw._AMPLITUDE_FLOOR) for x in u), "volume applied"
    assert u[2]["start"] - u[1]["end"] < 0.45, "an urgent item still skips the 0.5 s gap"


@pytest.mark.parametrize("with_alerts", [False, True])
def test_the_head_turn_mute_and_drain_and_the_speech_switch_behave_as_before(rig, with_alerts):
    r = rig(default=0.3, with_alerts=with_alerts)
    r.start()
    r.ud.turn_event.push(delta_deg=50, received_at=time.monotonic())
    assert r.wait_for(lambda: r.turns)
    r.ud.tts_queue.offer(MODE)                                       # offered inside the mute window
    r.ud.turn_event.push(delta_deg=50, received_at=time.monotonic())  # a second turn drains it
    time.sleep(1.6)
    assert r.texts() == [], "the drained announcement was spoken"
    r.config.update(tts_enabled=False)
    r.ud.tts_queue.offer(MODE)
    time.sleep(0.5)
    assert r.texts() == [], "spoken with the speech switch off"
    r.config.update(tts_enabled=True)
    r.ud.tts_queue.offer(detection("chair", "right"))
    assert r.wait_for(lambda: r.texts() == ["chair right"])
    r.stop()


# ============================================================
# Criteria 1, 5: cadence and several alerts, real worker
# ============================================================
def test_an_alert_is_spoken_at_once_and_then_on_its_cadence(rig):
    r = rig(default=0.3)
    r.start()
    t0 = time.monotonic()
    r.ud.alerts.raise_alert("a", "controller missing", interval=1.5)
    time.sleep(6.8)
    r.stop()
    starts = [u["start"] - t0 for u in r.utterances() if u["text"] == "controller missing"]
    assert len(starts) == 5, starts
    assert starts[0] <= 1.0
    for k, s in enumerate(starts):
        assert abs(s - starts[0] - 1.5 * k) <= 0.3, starts
    assert all(not u["cut"] for u in r.utterances())
    offered = [item for _, item in r.offers]
    assert offered and all(item == {"announce": "controller missing"} for item in offered)


def test_alerts_raised_together_are_spoken_one_after_the_other_each_to_the_end(rig):
    r = rig(default=0.4)
    r.start()
    for name, text in [("a", "alpha"), ("b", "bravo"), ("c", "charlie")]:
        r.ud.alerts.raise_alert(name, text)
    assert r.wait_for(lambda: len(r.utterances()) >= 3, 8)
    time.sleep(0.5)
    r.stop()
    u = r.utterances()
    assert [x["text"] for x in u] == ["alpha", "bravo", "charlie"]
    assert all(not x["cut"] for x in u)
    assert all(b["start"] >= a["end"] - 0.02 for a, b in zip(u, u[1:])), "two alerts at once"


def test_clear_stops_the_alert_and_does_not_cut_the_one_under_way(rig):
    r = rig(default=0.8)
    r.start()
    r.ud.alerts.raise_alert("a", "x", interval=1.2)
    assert r.wait_for(lambda: len(r.started()) >= 1)
    time.sleep(0.2)
    r.ud.alerts.clear("a")                                           # mid-utterance
    time.sleep(3.0)
    r.stop()
    u = r.utterances()
    assert [(x["text"], x["cut"]) for x in u] == [("x", False)]


# ============================================================
# Criterion 6: the loss paths, forced on purpose, real worker
# ============================================================
def test_an_alert_cut_by_a_mode_announcement_is_spoken_again(rig):
    r = rig(default=0.3, texts={"x": 1.0, "depth mode": 0.5})
    r.start()
    r.ud.alerts.raise_alert("a", "x", interval=20.0)
    assert r.wait_for(lambda: len(r.started()) >= 1)
    time.sleep(0.3)
    r.ud.tts_queue.offer(MODE)                                       # mid-utterance
    assert r.wait_for(lambda: [u["text"] for u in r.utterances()] == ["x", "depth mode", "x"], 8)
    r.stop()
    u = r.utterances()
    assert [(x["text"], x["cut"]) for x in u] == [("x", True), ("depth mode", False), ("x", False)]


def test_a_once_alert_cut_by_a_mode_announcement_is_spoken_again_and_only_once_more(rig):
    """A mode announcement (the wearer's own switch) may still cut an alert; the alert is said again, once."""
    r = rig(default=0.3, texts={"x": 1.0})
    r.start()
    r.ud.alerts.raise_alert("a", "x")
    assert r.wait_for(lambda: len(r.started()) >= 1)
    time.sleep(0.3)
    r.ud.tts_queue.offer({"announce": "depth mode"})
    time.sleep(4.0)
    r.stop()
    assert [(x["text"], x["cut"]) for x in r.utterances()] == [("x", True), ("depth mode", False), ("x", False)]


def test_d16_an_urgent_detection_does_not_cut_an_alert_in_progress(rig):
    """D-16 (user, 2026-10-18), through the real speech worker: the alert finishes, then the car is said.
    Until F7 the car cut the alert, which was then said again."""
    r = rig(default=0.3, texts={"x": 1.0})
    r.start()
    r.ud.alerts.raise_alert("a", "x")
    assert r.wait_for(lambda: len(r.started()) >= 1)
    time.sleep(0.3)
    r.ud.tts_queue.offer(detection("car", "center", priority=9.0, tier=TIER_URGENT))
    time.sleep(3.0)
    r.stop()
    assert [(x["text"], x["cut"]) for x in r.utterances()] == [("x", False), ("car center", False)]


def test_a_head_turn_with_an_alert_waiting_in_the_slot_does_not_discard_it(rig):
    r = rig(default=0.3)
    r.start()
    r.ud.turn_event.push(delta_deg=50, received_at=time.monotonic())     # the worker mutes for 1 s
    assert r.wait_for(lambda: r.turns)
    r.ud.alerts.raise_alert("a", "x", interval=30.0)
    assert r.wait_for(lambda: r.ud.tts_queue.peek() == {"announce": "x"}, 2), "alert never reached the slot"
    waiting = r.ud.tts_queue.peek()
    r.ud.turn_event.push(delta_deg=-50, received_at=time.monotonic())    # the drain, with the alert in the slot
    assert r.wait_for(lambda: len(r.turns) == 2)
    assert r.ud.tts_queue.peek() is waiting, "the drain discarded the alert"
    assert r.wait_for(lambda: r.texts() == ["x"], 5)
    time.sleep(0.8)
    r.stop()
    u = r.utterances()
    assert [x["text"] for x in u] == ["x"] and not u[0]["cut"]
    assert u[0]["start"] >= r.turns[1][1] - 0.1, "spoken inside the mute window"


def test_a_head_turn_still_discards_a_mode_announcement_and_a_detection_with_an_alert_around(rig):
    """R2: only the scheduler's own item is spared."""
    r = rig(default=0.3)
    r.start()
    r.ud.alerts.raise_alert("a", "x", interval=30.0)
    assert r.wait_for(lambda: r.texts() == ["x"], 3)
    time.sleep(0.8)
    r.ud.turn_event.push(delta_deg=50, received_at=time.monotonic())
    assert r.wait_for(lambda: r.turns)
    r.ud.tts_queue.offer(MODE)
    r.ud.tts_queue.offer(detection())
    r.ud.turn_event.push(delta_deg=50, received_at=time.monotonic())
    time.sleep(1.8)
    r.stop()
    assert r.texts() == ["x"]


def test_an_alert_replaced_in_the_slot_by_a_mode_announcement_is_spoken_after_it(rig):
    r = rig(default=0.3)
    r.start()
    r.ud.turn_event.push(delta_deg=50, received_at=time.monotonic())     # keep the slot occupied for a while
    assert r.wait_for(lambda: r.turns)
    r.ud.alerts.raise_alert("a", "x", interval=30.0)
    assert r.wait_for(lambda: r.ud.tts_queue.peek() == {"announce": "x"}, 2)
    r.ud.tts_queue.offer(MODE)                                           # the tie: replaces the alert
    assert r.wait_for(lambda: r.texts() == ["depth mode", "x"], 6)
    time.sleep(0.8)
    r.stop()
    assert r.texts() == ["depth mode", "x"] and not any(u["cut"] for u in r.utterances())


def test_speech_switch_off_does_not_silence_an_alert_and_it_keeps_its_cadence(rig):
    """D41 (F1, 2026-10-18): a failsafe alert is spoken with the speech switch off, on its cadence and at
    no less than 30 % volume; switching speech on changes nothing for it. Until F1 this test checked the
    opposite (dropped while off, no storm), the behaviour T-00a kept while D-15 was open."""
    r = rig(default=0.3)
    r.config.update(tts_enabled=False, tts_volume=0.0)
    r.start()
    t0 = time.monotonic()
    r.ud.alerts.raise_alert("a", "x", interval=1.0)
    time.sleep(3.5)
    offers_while_off = [t for t, item in r.offers if item == {"announce": "x"}]
    assert 3 <= len(offers_while_off) <= 5, f"offers while the switch was off: {len(offers_while_off)}"
    assert all(abs((t - t0) - round(t - t0)) <= 0.35 for t in offers_while_off), "offers are not on the 1 s cadence"
    assert len(r.texts()) == len(offers_while_off) or len(r.texts()) == len(offers_while_off) - 1, \
        "every offer is spoken (the last may still be starting)"
    floor = str(tw._espeak_amplitude(0.30))
    assert all(u["argv"][u["argv"].index("-a") + 1] == floor for u in r.utterances()), "not at the 30 % floor"
    n = len(offers_while_off)
    r.config.update(tts_enabled=True)
    time.sleep(1.0)
    r.stop()
    assert set(r.texts()) == {"x"}
    assert len([t for t, item in r.offers if item == {"announce": "x"}]) <= n + 1, "switching speech on re-offered it"


def test_a_failure_while_speaking_an_alert_does_not_leave_the_scheduler_waiting(rig, monkeypatch):
    """The worker's except branch: the alert counts as delivered (no storm) and the next repeat tries again."""
    r = rig(default=0.3)
    real_start = tw._start_espeak
    calls = []

    def flaky(text, amplitude=tw._AMPLITUDE_MAX):
        calls.append(time.monotonic())
        if len(calls) == 1:
            raise OSError("espeak-ng vanished")
        return real_start(text, amplitude)
    monkeypatch.setattr(tw, "_start_espeak", flaky)
    r.start()
    t0 = time.monotonic()
    r.ud.alerts.raise_alert("a", "x", interval=1.0)
    assert r.wait_for(lambda: len(r.started()) >= 2, 5), "the scheduler never offered the alert again"
    r.stop()
    assert len(calls) in (2, 3), f"offers after one failure: {len(calls)}"     # not a storm
    assert 0.8 <= calls[1] - calls[0] <= 1.4, f"the retry came {calls[1] - calls[0]:.2f} s after the failure"
    assert r.started()[0] == "x"


def test_the_fallback_engine_path_also_reports_the_alert_as_spoken(rig, monkeypatch):
    r = rig(default=0.3)
    spoken = []
    monkeypatch.setattr(tw, "_start_espeak", lambda text, amplitude=100: None)      # no espeak-ng
    monkeypatch.setattr(tw, "_speak_blocking", lambda text, amplitude=100: spoken.append(time.monotonic()))
    r.start()
    r.ud.alerts.raise_alert("a", "x", interval=1.0)
    assert r.wait_for(lambda: len(spoken) >= 3, 5)
    r.stop()
    assert 0.8 <= spoken[1] - spoken[0] <= 1.4 and 0.8 <= spoken[2] - spoken[1] <= 1.4, spoken


# ============================================================
# Real-time randomised runs: "fixed means repeated" (default 1 per path; SV_ALERT_RUNS=20 for the report)
# ============================================================
RUNS = int(os.environ.get("SV_ALERT_RUNS", "1"))
BATCH = 5                # runs per test, so each test stays under the 60 s default timeout of my_hailo_env
SCALED = [("a10", "ten second alert", 1.0), ("a30", "thirty second alert", 1.7), ("a300", "long period alert", 3.0)]
ONCE = ("once", "once only alert", None)
STUB_LAG = 0.15          # python start-up of the stub, between Popen and the first log line


def busy_measure(intervals, a, b):
    covered, cursor = 0.0, a
    for s, e in sorted(intervals):
        s, e = max(s, cursor), min(e, b)
        if e > s:
            covered += e - s
            cursor = e
    return covered


def check_real_log(rig, specs, raised_at, t_end, loss_times, late_tol=0.6, cadence=True):
    """The same invariants as tests/test_alerts.py, on the stub's log. Raises AssertionError."""
    utts = rig.utterances()
    alert_texts = {t for _, t, _ in specs}
    busy = [(u["start"] - STUB_LAG, u["end"] + 0.15) for u in utts]
    busy += [(now, deadline + 1.0) for now, deadline in rig.turns]        # mute window + the pace gap before it
    for k, u in enumerate(utts):
        if u["cut"] and u["text"] in alert_texts and u["end"] < t_end - 0.01:     # not the cut by shutdown
            later = [v for v in utts[k + 1:] if v["start"] >= u["end"] - 0.05]
            assert later and later[0]["text"] not in alert_texts, f"alert cut by another alert: {u} -> {later[:1]}"
    for a, b in zip(utts, utts[1:]):
        assert b["start"] >= a["end"] - 0.12, f"two utterances at once: {a['text']} / {b['text']}"
    for name, text, interval in specs:
        delivered = [u["start"] for u in utts if u["text"] == text and not u["cut"]]
        if interval is None:
            assert len(delivered) <= 1, f"{name}: once-alert delivered {len(delivered)} times"
        for s1, s2 in zip(delivered, delivered[1:]):
            assert s2 - s1 >= interval - 0.25, f"{name} doubled: {s1:.2f} then {s2:.2f}"
        if not cadence:                              # dropped items are delivered but leave no trace in the log
            continue
        dues = [raised_at[name]] + [s + interval for s in delivered if interval is not None]
        for due, spoken in zip(dues, delivered):
            wait = (spoken - due) - busy_measure(busy, due, spoken)
            assert wait <= late_tol, f"{name} late: due {due:.2f}, spoken {spoken:.2f}, idle wait {wait:.2f}"
        if len(dues) > len(delivered) and dues[len(delivered)] < t_end - 1.2:
            due = dues[len(delivered)]
            assert (t_end - due) - busy_measure(busy, due, t_end) <= late_tol, f"{name} overdue at the end: {due:.2f}"
        losses = [u["end"] for u in utts if u["text"] == text and u["cut"] and u["end"] < t_end - 0.01]
        losses += [t for n, t in loss_times if n == name]
        for lost_at in losses:
            later = [s for s in delivered if s >= lost_at - 0.05]
            until = later[0] if later else t_end
            if not later and lost_at > t_end - 1.2:
                continue
            wait = (until - lost_at) - busy_measure(busy, lost_at, until)
            assert wait <= late_tol, f"{name} not repaired after the loss at {lost_at:.2f}: idle wait {wait:.2f}"


class Chaos(threading.Thread):
    """Forces one loss path from outside the workers, at random moments, reading the stub's log."""

    def __init__(self, rig, path, by_text, rng, loss_times):
        super().__init__(daemon=True)
        self.rig, self.path, self.by_text, self.rng, self.loss = rig, path, by_text, rng, loss_times
        self.stop_flag = threading.Event()
        self.seen = 0
        self.events = 0
        self.last_turn = 0.0

    def run(self):
        mailbox, turn_event = self.rig.ud.tts_queue, self.rig.ud.turn_event
        while not self.stop_flag.wait(0.01):
            if self.path == "cut":
                started = [u for u in self._starts() if u["text"] in self.by_text]
                if len(started) > self.seen:
                    self.seen = len(started)
                    if self.rng.random() < 0.5:
                        self.stop_flag.wait(self.rng.uniform(0.03, 0.2))
                        mailbox.offer(dict(MODE))
                        self.events += 1
            elif self.path in ("drain", "tie"):
                peek = mailbox.peek()
                cooled = time.monotonic() - self.last_turn > 0.7        # let a mute window end now and then
                if peek is not None and peek.get("announce") in self.by_text and self.rng.random() < 0.5:
                    if self.path == "tie":
                        self.loss.append((self.by_text[peek["announce"]], time.monotonic()))
                        mailbox.offer(dict(MODE))
                        self.events += 1
                    elif cooled:
                        self.loss.append((self.by_text[peek["announce"]], time.monotonic()))
                        self.last_turn = time.monotonic()
                        turn_event.push(delta_deg=50, received_at=self.last_turn)
                        self.events += 1
                elif cooled and self.rng.random() < (0.02 if self.path == "drain" else 0.03):
                    self.last_turn = time.monotonic()
                    turn_event.push(delta_deg=50, received_at=self.last_turn)   # a mute: keeps the slot occupied

    def _starts(self):
        out = []
        try:
            for line in self.rig.log_path.read_text().splitlines():
                rec = json.loads(line)
                if rec["ev"] == "start":
                    out.append(rec)
        except (OSError, ValueError):
            pass
        return out


@pytest.mark.parametrize("path", ["cut", "drain", "tie", "off"])
@pytest.mark.parametrize("batch", range((RUNS + BATCH - 1) // BATCH))
def test_real_worker_randomised_runs_never_lose_double_or_cut_an_alert(rig, path, batch, capsys):
    seeds = range(batch * BATCH, min(RUNS, (batch + 1) * BATCH))
    total_events = 0
    for seed in seeds:
        rng = random.Random(1000 + seed)
        r = rig(default=0.3, texts={"depth mode": 0.4, **{t: rng.choice([0.25, 0.35, 0.5]) for _, t, _ in SCALED}})
        specs = list(SCALED) + ([ONCE] if seed % 2 == 0 else [])
        by_text = {t: n for n, t, _ in specs}
        loss_times, offers_off = [], []
        r.start()
        t0 = time.monotonic()
        raised_at = {}
        for name, text, interval in specs:
            raised_at[name] = time.monotonic()
            r.ud.alerts.raise_alert(name, text, interval=interval)
        chaos = Chaos(r, path, by_text, rng, loss_times)
        chaos.start()
        t_end = t0 + 5.5
        off_windows = []
        while time.monotonic() < t_end:
            time.sleep(rng.uniform(0.2, 0.7))
            roll = rng.random()
            if roll < 0.35:
                r.ud.tts_queue.offer(dict(MODE))
            elif roll < 0.7:
                r.ud.tts_queue.offer(detection("person", "left"))
            elif path == "off":
                start = time.monotonic()
                r.config.update(tts_enabled=False)
                time.sleep(rng.uniform(0.4, 1.2))
                r.config.update(tts_enabled=True)
                off_windows.append((start, time.monotonic()))
        chaos.stop_flag.set()
        chaos.join(timeout=2)
        end = time.monotonic()
        r.stop()
        total_events += chaos.events + len(off_windows)
        try:
            if path == "off":
                # D41 (F1): the speech switch no longer silences a failsafe alert, so the alerts are
                # checked as on every other path, cadence included. What the switch still silences
                # is everything else: no detection or mode announcement starts well inside an off
                # window (an utterance in hand when the switch went off may finish). Until F1 this
                # checked the opposite: alerts silenced, cadence not checkable.
                for w0, w1 in off_windows:
                    inside = [u for u in r.utterances()
                              if u["text"] not in by_text and w0 + 0.9 < u["start"] < w1 - 0.1]
                    assert not inside, f"spoken with the switch off: {inside}"
                check_real_log(r, specs, raised_at, end, loss_times)
            else:
                check_real_log(r, specs, raised_at, end, loss_times)
        except AssertionError as e:
            raise AssertionError(f"{path} seed {seed}: {e}") from None
    capsys.readouterr()
    assert total_events >= len(seeds), f"{path}: the forced loss path barely happened"
    print(f"\n[T-00a] real worker, {path}, seeds {seeds.start}-{seeds.stop - 1}: {len(seeds)} runs, "
          f"{total_events} forced loss events, no alert lost, doubled or cut by another alert")


@pytest.mark.skipif(not os.environ.get("SV_REAL_CADENCE"), reason="opt-in (about 100 s): SV_REAL_CADENCE=1 PYTEST_TIMEOUT=300")
def test_real_time_cadence_of_10_and_30_seconds_with_announcements_arriving_at_random(rig, capsys):
    """The brief's real-time check: at least 3 repeats each, none skipped, none cut by the other alert."""
    rng = random.Random(7)
    r = rig(default=0.4, texts={"ten second alert": 1.2, "thirty second alert": 1.6, "depth mode": 0.8})
    specs = [("a10", "ten second alert", 10.0), ("a30", "thirty second alert", 30.0)]
    by_text = {t: n for n, t, _ in specs}
    r.start()
    t0 = time.monotonic()
    raised_at = {}
    for name, text, interval in specs:
        raised_at[name] = time.monotonic()
        r.ud.alerts.raise_alert(name, text, interval=interval)
    loss_times = []
    chaos = Chaos(r, "cut", by_text, rng, loss_times)
    chaos.start()
    t_end = t0 + 96.0
    while time.monotonic() < t_end:
        time.sleep(rng.uniform(1.0, 6.0))
        roll = rng.random()
        if roll < 0.3:
            r.ud.tts_queue.offer(dict(MODE))
        elif roll < 0.8:
            r.ud.tts_queue.offer(detection("person", "left"))
        elif roll < 0.9:
            r.ud.turn_event.push(delta_deg=50, received_at=time.monotonic())
    chaos.stop_flag.set()
    chaos.join(timeout=2)
    end = time.monotonic()
    r.stop()
    check_real_log(r, specs, raised_at, end, loss_times, late_tol=1.0)
    for name, text, interval in specs:
        delivered = [u["start"] for u in r.utterances() if u["text"] == text and not u["cut"]]
        assert len(delivered) >= 4, f"{name}: {len(delivered)} utterances in 96 s (needs the first plus 3 repeats)"
    capsys.readouterr()
    print("\n[T-00a] real time: " + ", ".join(
        f"{n} spoken {len([u for u in r.utterances() if u['text'] == t and not u['cut']])}x, "
        f"{len([u for u in r.utterances() if u['text'] == t and u['cut']])} cut and repeated" for n, t, _ in specs))


# ============================================================
# Criterion 8, 10: the program path
# ============================================================
def _load_main(monkeypatch):
    """main.py as if hailo_apps were not installed (same method as tests/test_main_startup.py)."""
    for name in ("hailo_apps.python.core.common.hailo_logger", "hailo_apps.python.core.gstreamer.gstreamer_app"):
        monkeypatch.setitem(sys.modules, name, None)
    monkeypatch.setenv("GST_PLUGIN_FEATURE_RANK", os.environ.get("GST_PLUGIN_FEATURE_RANK", ""))
    spec = importlib.util.spec_from_file_location("second_vision_main_t00a", SRC / "second_vision" / "main.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_user_data_main_builds_has_a_scheduler_on_the_speech_mailbox(monkeypatch):
    if AlertScheduler is None:
        pytest.fail("this tree has no alert scheduler (core/alerts.py)")
    module = _load_main(monkeypatch)
    ud = module.SecondVisionUserData(SystemConfig())
    assert isinstance(ud.alerts, AlertScheduler)
    ud.alerts.raise_alert("a", "x")
    ud.alerts.poll()
    assert ud.tts_queue.peek() == {"announce": "x"}, "the scheduler offers into a different mailbox than the TTS worker reads"


def test_main_stops_with_exit_0_after_ctrl_c_and_starts_the_alert_worker(tmp_path):
    """The real program path (--mock, a stub espeak, no device): start it, Ctrl+C it, time the exit."""
    if AlertScheduler is None:
        pytest.fail("this tree has no alert scheduler (core/alerts.py)")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    stub = bin_dir / "espeak-ng"
    stub.write_text(STUB.format(python=sys.executable))
    stub.chmod(0o755)
    env = dict(os.environ, PATH=f"{bin_dir}{os.pathsep}{os.environ['PATH']}", PYTHONPATH=str(SRC), PYTHONUNBUFFERED="1",
               ESPEAK_STUB_LOG=str(tmp_path / "e.log"), ESPEAK_STUB_CONFIG='{"default": 1.0}')
    proc = subprocess.Popen([sys.executable, str(SRC / "second_vision" / "main.py"), "--mock"], env=env,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, cwd=tmp_path)
    try:
        ready = time.monotonic() + 60
        out = ""
        while time.monotonic() < ready and "Generators running" not in out:
            line = proc.stdout.readline()
            if not line and proc.poll() is not None:
                break
            out += line
        assert "Alert worker started" in out, out[-800:]
        time.sleep(2.5)                                  # let the mock detections reach the stub speaker
        sent = time.monotonic()
        proc.send_signal(signal.SIGINT)
        try:
            rest, _ = proc.communicate(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            pytest.fail("main() did not stop within 10 s of Ctrl+C")
        took = time.monotonic() - sent
        assert proc.returncode == 0, (proc.returncode, rest[-800:])
        assert "[MAIN] Done." in rest
        # Not 1 s: mock mode alone (the baseline, without the alert worker) takes 0.95 to 1.8 s here, depending
        # on where the mock detection generator is in its 2 s sleep. The alert worker itself stops within
        # 0.05 s (test_the_alert_worker_is_a_daemon_that_stops_within_a_quarter_second_of_shutdown).
        assert took <= 2.6, f"stopped {took:.2f} s after Ctrl+C"
    finally:
        # A failed check above must not leave mock mode running: three such copies ran for hours (2026-10-19).
        if proc.poll() is None:
            proc.kill()
            proc.wait(5)
