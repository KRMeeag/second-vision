"""
Priority model + mailbox for the TTS announcement channel.

This is the single home for the prioritization tunables. It is deliberately
pure-Python (threading / time / math only — no hailo, no gi) so it imports
cleanly in --mock mode and in unit tests.

Three concerns live here:
  1. PriorityMailbox — a single-slot, keep-highest-priority handoff between the
     detection callback (producer) and the TTS worker (consumer). Replaces the
     old queue.Queue(maxsize=1), whose drop-on-Full policy kept the STALE item
     and dropped the fresh one.
  2. Scoring — compute_priority() / compute_tier(): turn a detection into a
     scalar priority and an urgency tier. Used by the callback.
  3. Preemption + pacing helpers — preempts(), item_priority(), item_tier(),
     and the MIN_UTTERANCE_GAP constant. Used by the TTS worker.

Every threshold is a named module constant so it can be tuned by ear on the
device without touching logic. See .agents/handoff_v3.md for the design.
"""

import threading

# ============================================================
# Tiers
# ============================================================
TIER_NORMAL = "normal"
TIER_URGENT = "urgent"

# ============================================================
# Scoring weights — priority = sum of weighted, normalized terms, minus a
# soft recency penalty. confidence and area are already ~0-1; zone/class
# weights are ~0-1 too, so equal starting weights keep the terms comparable.
# Tune these on-device.
# ============================================================
W_CONF = 1.0
W_AREA = 1.0
W_ZONE = 1.0
W_CLASS = 1.0
W_APPROACH = 0.0   # Reserved hook: approach-velocity (bbox-area growth). Wire later
                   # by storing prev_area on the track_history entry and computing
                   # (area - prev_area)/dt. Kept at 0.0 so the term is inert for now.

# Center objects are more likely to be on the user's collision path (mirrors
# DECISIONS D7 — the center zone is the one that matters most).
ZONE_WEIGHT_DEFAULT = 0.5
ZONE_WEIGHTS = {
    "center": 1.0,
    "left": 0.5,
    "right": 0.5,
}

# Per-class importance. PLACEHOLDER values — the final detection class set isn't
# settled yet (see handoff_v2), so this ships as an easily-edited table, not a
# committed policy. Neutral default; a few likely-relevant classes nudged up.
CLASS_WEIGHT_DEFAULT = 0.5
CLASS_WEIGHTS = {
    "person": 0.7,
    "car": 1.0,
    "bicycle": 0.9,
    "motorcycle": 0.9,
    "bus": 1.0,
    "truck": 1.0,
}

# Soft cooldown: right after an object is announced its priority is knocked down
# by RECENCY_PENALTY_MAX, decaying linearly to 0 over RECENCY_DECAY_SECONDS, so a
# just-spoken object loses to fresher/urgent events but can resurface once quiet.
RECENCY_PENALTY_MAX = 1.0
RECENCY_DECAY_SECONDS = 8.0

# ============================================================
# Urgency tier thresholds
# ============================================================
# Classes that count as urgent when they appear in the CENTER zone (collision
# path). PLACEHOLDER, same caveat as CLASS_WEIGHTS.
URGENT_CLASSES = {"car", "bus", "truck", "motorcycle", "bicycle"}
# Absolute priority above which anything is urgent regardless of class/zone.
URGENT_ABS_THRESHOLD = 3.2

# ============================================================
# Preemption + pacing (consumed by the TTS worker)
# ============================================================
# A normal (non-urgent) item preempts the current utterance only if it clears
# the current one's priority by more than this margin — avoids twitchy barge-in
# on near-ties.
PREEMPT_MARGIN = 0.75
# True  -> urgent-tier OR margin (the user's chosen default).
# False -> pure relative-margin (the A/B alternative to test on-device).
PREEMPT_USE_TIER = True

# Minimum silence between consecutive utterances (calm cadence). An urgent
# PENDING item skips this wait — see tts_worker._pace().
MIN_UTTERANCE_GAP = 0.5

# Priority assigned to items that arrive without one (e.g. mock detections).
DEFAULT_PRIORITY = 1.0


# ============================================================
# Scoring
# ============================================================
def recency_penalty(last_announced, now):
    """Soft cooldown: full penalty right after announcing, decaying to 0."""
    if last_announced is None:
        return 0.0
    elapsed = now - last_announced
    if elapsed >= RECENCY_DECAY_SECONDS:
        return 0.0
    return RECENCY_PENALTY_MAX * (1.0 - elapsed / RECENCY_DECAY_SECONDS)


def compute_priority(confidence, area, zone, label, last_announced, now):
    """
    Scalar priority for one detection. Higher = more worth surfacing first.

    confidence: detector confidence (0-1). area: bbox area fraction (0-1), the
    existing proximity proxy. last_announced: monotonic-agnostic time.time() of
    this track's last announcement, or None if never announced.
    """
    zone_w = ZONE_WEIGHTS.get(zone, ZONE_WEIGHT_DEFAULT)
    class_w = CLASS_WEIGHTS.get(label, CLASS_WEIGHT_DEFAULT)
    approach_rate = 0.0  # reserved hook — see W_APPROACH

    score = (
        W_CONF * confidence
        + W_AREA * area
        + W_ZONE * zone_w
        + W_CLASS * class_w
        + W_APPROACH * approach_rate
    )
    score -= recency_penalty(last_announced, now)
    return score


def compute_tier(priority, label, zone):
    """Classify a detection's urgency. Urgent items can preempt and bypass gates."""
    if label in URGENT_CLASSES and zone == "center":
        return TIER_URGENT
    if priority >= URGENT_ABS_THRESHOLD:
        return TIER_URGENT
    return TIER_NORMAL


# ============================================================
# Item helpers — tolerate payloads without priority/tier (mock, announce)
# ============================================================
def item_priority(item):
    """Priority of a queued payload. Mode announcements always win (+inf)."""
    if "announce" in item:
        return float("inf")
    return item.get("priority", DEFAULT_PRIORITY)


def item_tier(item):
    """Tier of a queued payload. Mode announcements are urgent (always heard)."""
    if "announce" in item:
        return TIER_URGENT
    return item.get("tier", TIER_NORMAL)


def preempts(new_item, current_item):
    """
    Should `new_item` interrupt `current_item`, which is mid-utterance?

    Default model (PREEMPT_USE_TIER=True): urgent-tier OR relative-margin.
    Alternative (False): pure relative-margin — kept for on-device A/B testing.
    """
    if PREEMPT_USE_TIER and item_tier(new_item) == TIER_URGENT:
        return True
    return (item_priority(new_item) - item_priority(current_item)) > PREEMPT_MARGIN


# ============================================================
# PriorityMailbox — single-slot, keep-highest-priority handoff
# ============================================================
class PriorityMailbox:
    """
    One-slot mailbox between the detection callback (producer) and TTS worker
    (consumer), replacing queue.Queue(maxsize=1).

    offer(): if empty, store; else keep the higher-priority of {stored, incoming}
             (ties go to the incoming/fresher item). Never blocks, never raises —
             low-priority backlog cannot accumulate, and a fresher better item
             always wins, which is what "avoid stale phrases" actually wanted.
    take():  pop and return the stored item (or None) — used when the worker is idle.
    peek():  look without removing — used mid-utterance to check for preemption.
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._item = None

    def offer(self, item):
        with self._lock:
            if self._item is None or item_priority(item) >= item_priority(self._item):
                self._item = item

    def take(self):
        with self._lock:
            item, self._item = self._item, None
            return item

    def peek(self):
        with self._lock:
            return self._item
