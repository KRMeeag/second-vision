"""
Unit tests for the TTS priority model, mailbox, and the interruptible worker.

All hardware-independent: the worker tests fake the espeak subprocess, so this
runs on any machine (no Hailo, no espeak-ng, no camera). Run with:

    python3 -m pytest tests/test_priority.py -v
"""

import sys
import threading
from pathlib import Path

# No conftest/package install — put src/ on the path so `second_vision.*` resolves.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import second_vision.core.priority as priority
from second_vision.core.priority import (
    PriorityMailbox,
    TIER_NORMAL,
    TIER_URGENT,
    compute_priority,
    compute_tier,
    item_priority,
    item_tier,
    preempts,
)
from second_vision.workers import tts_worker as tw


# ============================================================
# PriorityMailbox
# ============================================================
def _item(pri, tier=TIER_NORMAL, label="x", zone="center"):
    return {"label": label, "zone": zone, "confidence": 0.9, "priority": pri, "tier": tier}


def test_mailbox_offer_take_peek_roundtrip():
    mb = PriorityMailbox()
    assert mb.peek() is None
    assert mb.take() is None

    a = _item(1.0)
    mb.offer(a)
    assert mb.peek() is a          # peek does not remove
    assert mb.peek() is a
    assert mb.take() is a          # take removes
    assert mb.peek() is None


def test_mailbox_keeps_higher_priority():
    mb = PriorityMailbox()
    low, high = _item(1.0), _item(5.0)
    mb.offer(low)
    mb.offer(high)                 # higher wins
    assert mb.take() is high

    mb.offer(high)
    mb.offer(low)                  # lower is discarded, higher stays
    assert mb.take() is high


def test_mailbox_ties_go_to_newer():
    # Equal priority -> the fresher item replaces the stale one (offer uses >=).
    mb = PriorityMailbox()
    first, second = _item(2.0), _item(2.0)
    mb.offer(first)
    mb.offer(second)
    assert mb.take() is second


def test_mailbox_never_grows_past_one_slot():
    mb = PriorityMailbox()
    for i in range(50):
        mb.offer(_item(float(i)))
    assert mb.take() is not None
    assert mb.take() is None       # only ever one item buffered


def test_mailbox_announce_always_wins():
    mb = PriorityMailbox()
    mb.offer(_item(999.0))         # a very high normal-detection priority
    mb.offer({"announce": "detection mode"})
    taken = mb.take()
    assert taken.get("announce") == "detection mode"


# ============================================================
# Scoring
# ============================================================
def test_priority_monotonic_in_confidence():
    now = 100.0
    low = compute_priority(0.5, 0.2, "left", "chair", None, now)
    high = compute_priority(0.9, 0.2, "left", "chair", None, now)
    assert high > low


def test_priority_monotonic_in_area():
    now = 100.0
    small = compute_priority(0.8, 0.1, "left", "chair", None, now)
    big = compute_priority(0.8, 0.6, "left", "chair", None, now)
    assert big > small


def test_priority_center_beats_side():
    now = 100.0
    side = compute_priority(0.8, 0.3, "left", "chair", None, now)
    center = compute_priority(0.8, 0.3, "center", "chair", None, now)
    assert center > side


def test_recency_penalty_lowers_priority_and_decays():
    now = 100.0
    fresh = compute_priority(0.8, 0.3, "center", "chair", None, now)          # never announced
    just_spoke = compute_priority(0.8, 0.3, "center", "chair", now, now)      # spoke at `now`
    assert just_spoke < fresh
    # Penalty decays to zero after RECENCY_DECAY_SECONDS.
    long_ago = compute_priority(
        0.8, 0.3, "center", "chair", now - priority.RECENCY_DECAY_SECONDS - 1, now
    )
    assert long_ago == fresh


# ============================================================
# Tier
# ============================================================
def test_tier_urgent_class_in_center():
    # A car in the center is urgent regardless of its numeric priority.
    assert compute_tier(0.1, "car", "center") == TIER_URGENT


def test_tier_urgent_class_off_center_is_not_auto_urgent():
    # Same class on a side is only urgent if the score crosses the abs threshold.
    assert compute_tier(0.1, "car", "left") == TIER_NORMAL


def test_tier_absolute_threshold():
    assert compute_tier(priority.URGENT_ABS_THRESHOLD + 0.1, "chair", "left") == TIER_URGENT
    assert compute_tier(priority.URGENT_ABS_THRESHOLD - 0.1, "chair", "left") == TIER_NORMAL


# ============================================================
# item helpers + preempts
# ============================================================
def test_item_helpers_default_fieldless():
    assert item_priority({"label": "x", "zone": "left"}) == priority.DEFAULT_PRIORITY
    assert item_tier({"label": "x", "zone": "left"}) == TIER_NORMAL
    assert item_priority({"announce": "both mode"}) == float("inf")
    assert item_tier({"announce": "both mode"}) == TIER_URGENT


def test_preempts_urgent_always():
    assert preempts(_item(0.1, TIER_URGENT), _item(9.0, TIER_NORMAL)) is True


def test_preempts_margin():
    current = _item(2.0)
    assert preempts(_item(2.0 + priority.PREEMPT_MARGIN + 0.01), current) is True
    assert preempts(_item(2.0 + priority.PREEMPT_MARGIN - 0.01), current) is False


def test_preempts_pure_margin_mode(monkeypatch):
    # With tier disabled, an urgent item that doesn't clear the margin won't preempt.
    monkeypatch.setattr(priority, "PREEMPT_USE_TIER", False)
    current = _item(5.0, TIER_NORMAL)
    assert preempts(_item(5.1, TIER_URGENT), current) is False
    assert preempts(_item(5.0 + priority.PREEMPT_MARGIN + 0.1, TIER_URGENT), current) is True


# ============================================================
# Interruptible worker (fake espeak)
# ============================================================
class _FakeProc:
    """Stand-in for a Popen espeak process with controllable completion."""

    def __init__(self, polls_until_done):
        self._polls_until_done = polls_until_done
        self._polls = 0
        self.terminated = False

    def poll(self):
        if self.terminated:
            return -15
        self._polls += 1
        return 0 if self._polls >= self._polls_until_done else None

    def terminate(self):
        self.terminated = True


class _FakeUserData:
    def __init__(self, mailbox):
        self.tts_queue = mailbox
        self.shutdown_event = threading.Event()


def _patch_espeak(monkeypatch, factory):
    spoken = []
    procs = []

    def fake_start(text):
        spoken.append(text)
        p = factory(len(procs))
        procs.append(p)
        return p

    monkeypatch.setattr(tw, "_start_espeak", fake_start)
    monkeypatch.setattr(tw, "_POLL_SECONDS", 0.0)  # don't actually sleep
    return spoken, procs


def test_worker_speaks_once_when_uninterrupted(monkeypatch):
    mb = PriorityMailbox()
    ud = _FakeUserData(mb)
    spoken, procs = _patch_espeak(monkeypatch, lambda i: _FakeProc(polls_until_done=3))

    tw._speak_interruptible(_item(2.0, label="person", zone="left"), mb, ud)

    assert spoken == ["person left"]      # exactly one utterance, no layering
    assert procs[0].terminated is False   # finished naturally, not killed


def test_worker_preempted_by_urgent(monkeypatch):
    mb = PriorityMailbox()
    ud = _FakeUserData(mb)
    # First utterance would run ~forever; the second (preemptor) finishes quickly.
    spoken, procs = _patch_espeak(
        monkeypatch, lambda i: _FakeProc(polls_until_done=(2 if i > 0 else 10_000))
    )

    # Urgent car is pending while a normal person is being spoken.
    mb.offer(_item(5.0, TIER_URGENT, label="car", zone="center"))
    tw._speak_interruptible(_item(2.0, TIER_NORMAL, label="person", zone="center"), mb, ud)

    assert spoken[0] == "person center"
    assert procs[0].terminated is True    # the in-progress utterance was cut
    assert spoken[1] == "car center"      # switched to the urgent item
    assert procs[1].terminated is False   # which then finished on its own
    assert mb.peek() is None              # mailbox drained


def test_worker_not_preempted_by_lower_priority(monkeypatch):
    mb = PriorityMailbox()
    ud = _FakeUserData(mb)
    spoken, procs = _patch_espeak(monkeypatch, lambda i: _FakeProc(polls_until_done=3))

    # A lower-priority normal item pending must NOT interrupt the current one.
    pending = _item(2.0, TIER_NORMAL, label="chair", zone="left")
    mb.offer(pending)
    tw._speak_interruptible(_item(5.0, TIER_NORMAL, label="person", zone="center"), mb, ud)

    assert spoken == ["person center"]    # only the current one spoke
    assert procs[0].terminated is False
    assert mb.peek() is pending           # the pending item is still waiting its turn
