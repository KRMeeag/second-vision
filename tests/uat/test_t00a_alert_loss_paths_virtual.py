"""
T-00a (repeating spoken system alerts), requirements REQ-CON-03 / REQ-CON-05 / REQ-CON-07 (cadence), the loss
paths of the brief's criteria 5 and 6. Written by the Tester (tests/uat/); independent of the Developer's
tests/test_alerts.py.

There is no UAT case of its own. The task is the enabler for TC-022-002..006, TC-023-002/004, TC-024-002/003 and
TC-025-002..006. Requirement text (REQUIREMENTS.md): REQ-CON-03 "the REQ-CON-02 alert repeats every 30 s while depth
estimation is enabled ... said once while depth estimation is disabled, including when depth is switched off
mid-run"; REQ-CON-05 "It repeats every 5 minutes"; REQ-CON-07 "It repeats every 10 seconds".

What this file is: the "at least 50 randomised runs at unit level (virtual clock)" of the brief, per loss path
(cut by a mode announcement, drained by a head turn, replaced by a tie, speech switch off). Level: unit.
REAL: core/alerts.py AlertScheduler, core/priority.py PriorityMailbox (+ preempts()), core/turn_event.py is not
needed. A MODEL (written by the Tester from reading workers/tts_worker.py, single-threaded, virtual time): the
speech worker, built from the same calls the real worker makes: alerts.take() / alerts.drain() / alerts.finish(),
the 0.5 s pace that an urgent pending item cuts short, the 1 s head-turn mute, the preempts() cut, the speech
switch. The REAL tts_worker is exercised with >= 20 runs per path in test_t00a_alert_scheduler.py.
The invariants are black-box over the utterance log, not over the scheduler's internals.
"""
from __future__ import annotations

import contextlib
import io
import os
import random
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "src"))

from second_vision.core.alerts import CUT, DROPPED, SPOKEN, AlertScheduler  # noqa: E402
from second_vision.core.priority import PriorityMailbox, TIER_URGENT, item_tier, preempts  # noqa: E402

DT = 0.05            # one scheduling step: the alert worker's tick; the real speech worker polls at 0.05 too
SIM_SECONDS = 330.0  # 10 s, 30 s and 300 s alerts all repeat at least once
MUTE = 1.0           # TURN_MUTE_SECONDS
PACE = 0.5           # MIN_UTTERANCE_GAP
PACE_TOL = 0.0       # extra allowance per hand-over; 0: a hand-over takes at most 2 steps (0.1 s)
SPECS = {"ALERT a10": 10.0, "ALERT b30": 30.0, "ALERT c300": 300.0}


def det(phrase, tier, priority):
    return {"label": "person", "zone": "left", "confidence": 0.9, "priority": priority, "tier": tier,
            "phrase": phrase}


class Sim:
    """Virtual-time model of tts_worker + alert_worker around the real PriorityMailbox and AlertScheduler."""

    def __init__(self, seed: int, path: str):
        self.rng = random.Random(seed)
        self.path = path
        self.t = 0.0
        self.mb = PriorityMailbox()
        self.alerts = AlertScheduler(self.mb, clock=lambda: self.t)
        self.enabled = True
        self.mute_until = 0.0
        self.pace_until = 0.0
        self.turn_pending = False
        self.cur = None                  # dict: item, text, start, end
        self.log = []                    # finished/cut utterances
        self.dropped = {}                # text -> [take times] (speech switch off)
        self.mutes = []                  # (start, end)
        self.forced = 0
        self.off_until = None
        self.next_mode = self.rng.uniform(1.0, 5.0)
        self.next_det = self.rng.uniform(0.2, 1.0)
        self.cut_at = None               # path cut: when to inject the mode announcement
        self.last_started_alert = None
        self.rolled_start = None
        self.n = 0

    # --- the model of the speech worker -------------------------------------------------------------------
    def _text(self, item):
        return item["announce"] if "announce" in item else item["phrase"]

    def _dur(self, item):
        if "announce" in item:
            return self.rng.uniform(0.8, 1.5) if item["announce"].startswith("ALERT") else self.rng.uniform(0.5, 1.0)
        return self.rng.uniform(0.5, 1.5)

    def _start(self, item):
        self.cur = {"item": item, "text": self._text(item), "start": self.t, "end": self.t + self._dur(item)}
        if self.cur["text"].startswith("ALERT"):
            self.last_started_alert = self.t

    def _close(self, outcome):
        c = self.cur
        self.log.append({"text": c["text"], "start": c["start"],
                         "end": self.t if outcome == "cut" else c["end"], "outcome": "cut" if outcome == "cut" else "end"})

    def _worker_step(self):
        if self.cur is not None:                                 # _speak_interruptible
            if self.t >= self.cur["end"]:
                self._close("end")
                item = self.cur["item"]
                self.cur = None
                self.alerts.finish(item, SPOKEN)
                self.pace_until = self.t + PACE
                return
            pending = self.mb.peek()
            if pending is not None and preempts(pending, self.cur["item"]):
                old = self.cur["item"]
                self._close("cut")
                claimed = self.alerts.take()
                self.cur = None
                self.alerts.finish(old, CUT)
                if claimed is not None:
                    self._start(claimed)
                else:
                    self.pace_until = self.t + PACE
            return
        if self.t < self.pace_until:                             # _pace: an urgent pending item cuts the wait
            pending = self.mb.peek()
            if pending is not None and item_tier(pending) == TIER_URGENT:
                self.pace_until = 0.0
            else:
                return
        if self.turn_pending:                                    # _check_turn_mute: top of the outer loop
            self.turn_pending = False
            self.alerts.drain()
            self.mute_until = self.t + MUTE
            self.mutes.append((self.t, self.mute_until))
        if self.t < self.mute_until:
            return
        item = self.alerts.take()
        if item is None:
            return
        if not self.enabled:
            self.alerts.finish(item, DROPPED)
            if "announce" in item:
                self.dropped.setdefault(item["announce"], []).append(self.t)
            return
        self._start(item)

    # --- producers and the forced loss path ---------------------------------------------------------------
    def _producers(self):
        if self.t >= self.next_mode:
            self.n += 1
            self.mb.offer({"announce": f"MODE {self.n}"})
            self.next_mode = self.t + self.rng.uniform(1.5, 6.0)
        if self.t >= self.next_det:
            self.n += 1
            urgent = self.rng.random() < 0.15
            self.mb.offer(det(f"DET {self.n}", "urgent" if urgent else "normal", self.rng.uniform(0.5, 3.0)))
            self.next_det = self.t + self.rng.uniform(0.3, 2.0)

    def _alert_in_slot(self):
        it = self.mb.peek()
        return it is not None and "announce" in it and it["announce"].startswith("ALERT")

    def _inject(self):
        p = self.path
        if p == "cut":
            if self.cur is not None and self.cur["text"].startswith("ALERT") and self.cut_at is None \
                    and self.cur["start"] != self.rolled_start:
                self.rolled_start = self.cur["start"]            # one roll per alert utterance
                if self.rng.random() < 0.5:
                    self.cut_at = self.t + self.rng.uniform(0.1, 0.5)
            if self.cut_at is not None and self.t >= self.cut_at:
                self.cut_at = None
                if self.cur is not None and self.cur["text"].startswith("ALERT"):
                    self.n += 1
                    self.mb.offer({"announce": f"MODEX {self.n}"})
                    self.forced += 1
        elif p in ("drain", "tie"):
            if self.rng.random() < 0.01:                         # background head turns, as a user turns the head
                self.turn_pending = True
            if self._alert_in_slot() and self.rng.random() < 0.5:
                if p == "drain":
                    self.turn_pending = True
                else:
                    self.n += 1
                    self.mb.offer({"announce": f"MODEX {self.n}"})
                self.forced += 1
        elif p == "off":
            if self.off_until is None and self.rng.random() < 0.01:
                self.off_until = self.t + self.rng.uniform(0.3, 3.0)
                self.enabled = False
                self.forced += 1
            elif self.off_until is not None and self.t >= self.off_until:
                self.enabled = True
                self.off_until = None

    def run(self):
        self.alerts.raise_alert("a", "ALERT a10", interval=10.0)
        self.alerts.raise_alert("b", "ALERT b30", interval=30.0)
        self.alerts.raise_alert("c", "ALERT c300", interval=300.0)
        while self.t < SIM_SECONDS:
            self.t += DT
            self._producers()
            self._inject()
            self.alerts.poll()                                   # alert_worker
            self._worker_step()                                  # tts_worker
        self.enabled = True
        if self.cur is not None:                                 # an utterance still under way at the end counts
            self._close("end")
            self.cur = None
        return self


def busy_time(utts, a, b):
    segs = sorted((max(u["start"], a), min(u["end"], b)) for u in utts if u["end"] > a and u["start"] < b)
    total, cur_end = 0.0, a
    for s, e in segs:
        s = max(s, cur_end)
        if e > s:
            total += e - s
            cur_end = e
    return total


def invariants(sim: Sim, slack=0.3):
    """Black box over the utterance log. 1: none overlap. 2: an alert utterance is never cut by an alert.
    3: not doubled: consecutive deliveries of one alert >= interval - 0.1. 4: not lost: consecutive deliveries
    <= interval + (time other speech and head-turn mutes took in between) + slack, and the last one not older
    than that at the end. A take dropped by the speech switch counts as a delivery (brief criterion 6)."""
    allu = sim.log
    probs = []
    for a, b in zip(allu, allu[1:]):
        if a["end"] > b["start"] + 1e-6:
            probs.append(f"overlap {a['text']} / {b['text']}")
    for i, u in enumerate(allu):
        if u["text"] in SPECS and u["outcome"] == "cut":
            nxt = allu[i + 1] if i + 1 < len(allu) else None
            if nxt is not None and nxt["text"] in SPECS:
                probs.append(f"{u['text']} cut by alert {nxt['text']}")
    t_end = sim.t
    for text, iv in SPECS.items():
        dels = sorted([u["start"] for u in allu if u["text"] == text and u["outcome"] == "end"]
                      + sim.dropped.get(text, []))
        if not dels:
            probs.append(f"{text}: never delivered")
            continue
        for a, b in zip(dels, dels[1:]):
            gap = b - a
            if gap < iv - 0.1:
                probs.append(f"{text}: doubled (gap {gap:.2f} < {iv})")
            busy = busy_time(allu, a + iv, b)
            mute = sum(max(0.0, min(e, b) - max(s, a + iv)) for s, e in sim.mutes)
            hand = sum(1 for u in allu if a + iv - 1.5 <= u["start"] <= b)     # each hand-over costs <= 2 steps
            if gap > iv + busy + mute + slack + 2 * DT * hand + PACE_TOL * hand:
                probs.append(f"{text}: late (gap {gap:.2f}, interval {iv}, busy {busy:.2f}, mute {mute:.2f}, "
                             f"{hand} utterances in between)")
        last = dels[-1]
        busy = busy_time(allu, last + iv, t_end)
        mute = sum(max(0.0, min(e, t_end) - max(s, last + iv)) for s, e in sim.mutes)
        hand = sum(1 for u in allu if last + iv - 1.5 <= u["start"] <= t_end)
        if t_end - last > iv + busy + mute + slack + 2 * DT * hand + PACE_TOL * hand:
            probs.append(f"{text}: silent at the end ({t_end - last:.2f} s since the last, interval {iv})")
    return probs


@pytest.mark.parametrize("path", ["cut", "drain", "tie", "off"])
def test_req_con_03_loss_path_randomised_runs_virtual_clock(path, capsys):
    """>= 50 randomised runs per loss path, 330 virtual seconds each, 10 s + 30 s + 300 s alerts raised together,
    random mode announcements and detection phrases (normal and urgent) arriving, the path forced on purpose.
    In none: an alert lost, doubled, or cut by another alert."""
    runs = int(os.environ.get("T00A_VRUNS", "60"))
    base = {"cut": 1, "drain": 2, "tie": 3, "off": 4}[path] * 10000
    forced = cuts = drops = alert_utts = mutes = 0
    failures = []
    for seed in range(runs):
        with contextlib.redirect_stdout(io.StringIO()):
            sim = Sim(base + seed, path).run()
        probs = invariants(sim)
        forced += sim.forced
        mutes += len(sim.mutes)
        cuts += sum(1 for u in sim.log if u["text"] in SPECS and u["outcome"] == "cut")
        drops += sum(len(v) for v in sim.dropped.values())
        alert_utts += sum(1 for u in sim.log if u["text"] in SPECS)
        if probs:
            failures.append((seed, probs[:3]))
    with capsys.disabled():
        print(f"\n[tester-virtual] {path}: {runs} runs x {SIM_SECONDS:.0f} virtual s, forced loss events {forced}, "
              f"alert utterances cut {cuts}, dropped by switch {drops}, head-turn mute windows {mutes}, alert utterances {alert_utts}, "
              f"runs with a lost/doubled/cut alert: {len(failures)}")
    assert not failures, failures[:3]
    assert forced >= runs, "the loss path was not forced"
    if path == "cut":
        assert cuts >= runs, "alert utterances were not really cut"
    if path == "off":
        assert drops >= runs // 2, "the speech-switch-off path was not really exercised"


class _CutForgotten(AlertScheduler):
    """Mutant: an alert that was cut counts as delivered."""
    def finish(self, item, outcome):
        super().finish(item, SPOKEN if outcome == CUT else outcome)


class _TieForgotten(AlertScheduler):
    """Mutant: an alert item that was replaced in the slot is forgotten and counts as delivered."""
    def poll(self):
        with self._lock:
            fl = self._flight
            if fl is not None and not fl.speaking and self._mailbox.peek() is not fl.item:
                fl.alert.pending = False
                fl.alert.last_start = self._clock()
                self._flight = None
        super().poll()


class _DropStorm(AlertScheduler):
    """Mutant: an alert dropped because the speech switch is off is offered again at once (a storm)."""
    def finish(self, item, outcome):
        super().finish(item, CUT if outcome == DROPPED else outcome)


@pytest.mark.parametrize("mutant,path", [(_CutForgotten, "cut"), (_TieForgotten, "tie"), (_DropStorm, "off")])
def test_req_con_03_virtual_model_notices_a_broken_scheduler(mutant, path):
    """Guard for the harness itself: the same runs must FAIL against a scheduler with one deliberate bug (the
    invariants are not vacuous): a cut alert forgotten, a replaced alert forgotten, a dropped alert re-offered in
    a storm. At least half of 20 runs must show it."""
    bad = 0
    for seed in range(20):
        sim = Sim(90000 + seed, path)
        sim.alerts = mutant(sim.mb, clock=lambda s=sim: s.t)
        with contextlib.redirect_stdout(io.StringIO()):
            sim.run()
        bad += bool(invariants(sim))
    assert bad >= 10, f"the model failed to notice {mutant.__name__} ({bad} of 20 runs)"
