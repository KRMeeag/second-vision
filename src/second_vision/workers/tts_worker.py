"""TTS Worker — Consumes prioritized announcements, produces serialized,
interruptible speech.

Design (see .agents/handoff_v3.md):
  - One utterance at a time. The worker starts an espeak-ng subprocess and
    watches it to completion, so announcements never LAYER on top of each other
    (the old fire-and-forget Popen bug).
  - A higher-priority / urgent item may barge in: while speaking, the worker
    polls the mailbox (~50ms) and, if a pending item should preempt, terminates
    the current utterance and switches to the new one.
  - A minimum silence gap follows each utterance for a calm cadence; an urgent
    pending item skips the gap.

Object-priority cooldown/suppression (per-track recency) is NOT here — it
lives entirely in the callback + priority layer (core/priority.py). This
worker just speaks what it is handed.

Turn-based muting IS here, and is a different thing: it isn't about any
object's priority or recency, it's a reaction to a physical body-turn event
from core/turn_event.py (ESP32 -> serial_worker.py -> TurnEvent). See
_check_turn_mute() below.
"""

import math
import shutil
import subprocess
import time

from second_vision.core.alerts import CUT, DROPPED, SPOKEN, failsafe_level
from second_vision.core.priority import (
    MIN_UTTERANCE_GAP,
    TIER_URGENT,
    item_tier,
    preempts,
)

# ============================================================
# INTERFACE CONTRACT (do not change):
#   Input:  user_data.tts_queue — a PriorityMailbox (offer/take/peek), holding
#           dicts with "label"/"zone"/"confidence"/"priority"/"tier" and an
#           optional "phrase" override, or {"announce": str} for mode changes.
#           user_data.turn_event — optional core/turn_event.py TurnEvent;
#           absent (getattr default None) is tolerated the same way
#           serial_worker.py tolerates a user_data without one.
#           user_data.alerts — optional core/alerts.py AlertScheduler (system alerts
#           that repeat). The worker tells it what became of the items it takes
#           (take / drain / finish); the queue items themselves are unchanged.
#           Absent (getattr default None) -> exactly the behaviour without it.
#   Output: Audio announcement via speaker (espeak-ng, pyttsx3 fallback).
#   Config: config.tts_enabled, config.tts_volume (the panel's volume knob). A
#           failsafe alert (the scheduler's own item, D41) is spoken even with
#           tts_enabled off, at failsafe_level(tts_volume); see _item_amplitude().
# ============================================================

_ESPEAK_SPEED = 160
_POLL_SECONDS = 0.05    # how often to check for a preempting item while speaking
_IDLE_SECONDS = 0.02    # nap when the mailbox is empty
_pyttsx3_engine = None

# --- Volume: the control panel's volume knob ---------------------------------
# config.tts_volume (0.0-1.0) sets espeak-ng's amplitude (-a). The knob runs
# from _AMPLITUDE_FLOOR to _AMPLITUDE_MAX on an EVEN-DECIBEL scale, because the
# pot is linear and hearing is not: interpolated geometrically, each tenth of
# the knob is ~2 dB, instead of nearly all the audible change crowding into
# the bottom of the travel. Measured on espeak-ng 1.52.0, output level tracks
# 20*log10(a/100) closely: a=10 is -22.2 dB, a=25 -12.9 dB, a=50 -6.2 dB.
#
#   knob   0.00  0.15  0.25  0.50  0.75  1.00
#   -a       10    14    18    32    56   100
#   dB      -22   -17   -15   -10    -5     0
#
# 1.0 maps to 100, espeak-ng's own default, so a run without a panel sounds
# exactly as it always has. Fully down is the floor, never silence: muting is
# the DETECT rocker's job (tts_enabled), and a wearer who turned the knob all
# the way down must still hear "car ahead". The floor was 25 (-13 dB) in the
# first draft; 10 is the user's call (2026-10-09) that 0.15 must be clearly
# quiet, not merely softer. PLACEHOLDER all the same — the field test sets it
# (FIELD-TESTING.md U7): in street noise -22 dB may be inaudible. There is
# headroom above 100 if full volume proves too quiet: a=140 is +2.9 dB and does
# not clip; a=200 saturates.
_AMPLITUDE_MAX = 100
_AMPLITUDE_FLOOR = 10


def _espeak_amplitude(volume) -> int:
    """
    espeak-ng amplitude for knob position `volume` (0.0-1.0). Never raises:
    anything that is not a finite number is treated as 1.0, the default, and
    values outside 0-1 are clamped.
    """
    try:
        volume = float(volume)
    except (TypeError, ValueError):
        volume = 1.0
    if not math.isfinite(volume):
        volume = 1.0
    volume = min(1.0, max(0.0, volume))
    return round(_AMPLITUDE_FLOOR * (_AMPLITUDE_MAX / _AMPLITUDE_FLOOR) ** volume)


def _item_amplitude(item, config, alerts=None):
    """
    espeak-ng amplitude to speak `item` at, or None when it must not be spoken.

    A failsafe alert (D41: the alert scheduler's own item) is spoken even with the speech switch
    off, at the volume knob's level but never under 30 % (failsafe_level). Everything else obeys
    the switch and the knob exactly as before. Reads config, never writes it: the override lasts
    for that one item.
    """
    if alerts is not None and alerts.is_failsafe(item):
        return _espeak_amplitude(failsafe_level(config.get("tts_volume")))
    if not config.get("tts_enabled"):
        return None
    return _espeak_amplitude(config.get("tts_volume"))

# --- Turn-based mute -------------------------------------------------------
# How long to hold off speaking after a 0x03 turn event.
# PLACEHOLDER — reasoned, not measured against a real wearer, same caveat as
# every other unmeasured constant in this project (e.g. TURN_EVENT_THRESHOLD_DEG
# in main.cpp). By the time a 0x03 event reaches here the physical turn has
# already settled on the ESP32 side — it only fires after
# TURN_SETTLE_DURATION_MS of steady rate, or the no-settle failsafe — so this
# window is mostly covering the wearer's brief re-orientation pause and the
# detection pipeline's first few frames of the new scene, not the turn
# itself. Too short and a pre-turn object still gets announced into the new
# scene; too long and the wearer loses real-time obstacle feedback right when
# they've just re-oriented and need it most.
TURN_MUTE_SECONDS = 1.0


def _check_turn_mute(turn_event, mailbox, now: float, mute_until: float, alerts=None) -> float:
    """
    Look for a fresh, not-yet-consumed turn event and return the (possibly
    updated) mute deadline.

    Pulled out of tts_worker()'s loop so the decision logic — as opposed to
    the sleep/loop mechanics around it — can be unit-tested directly with a
    real TurnEvent/PriorityMailbox and no espeak/subprocess/thread involved.

    No event pending: returns `mute_until` unchanged (idempotent to call every
    iteration).

    A fresh event: unconditionally sets the deadline to `now + TURN_MUTE_SECONDS`
    — even if a previous window is still active, since a second turn arriving
    mid-window means the wearer is still reorienting, not done. It also drains
    (and discards) whatever is currently sitting in `mailbox`: PriorityMailbox's
    offer() only replaces its stored item with an incoming one of EQUAL OR
    HIGHER priority, so a high-priority pre-turn item could otherwise survive
    un-overwritten for the whole window and be the very first thing spoken the
    instant it ends. The mailbox is deliberately left alone for the rest of the
    window — the detection pipeline keeps calling offer() on its own,
    unmodified, so whatever is parked there when the window closes reflects
    the current scene.

    `alerts` (the AlertScheduler, or None): the one item the drain leaves alone is a
    system alert of the scheduler's own that is waiting in the slot. A head turn makes
    a detection stale; it does not make a system alert stale, and the alert is spoken
    when the window ends. Every other item is drained as before, mode announcements
    included.
    """
    if turn_event is None:
        return mute_until
    turn = turn_event.take()
    if turn is None:
        return mute_until
    if alerts is not None:
        alerts.drain()
    else:
        mailbox.take()
    return now + TURN_MUTE_SECONDS


def tts_worker(user_data, config):
    """Main TTS worker loop."""
    mailbox = user_data.tts_queue
    turn_event = getattr(user_data, "turn_event", None)
    alerts = getattr(user_data, "alerts", None)
    mute_until = 0.0

    while not user_data.shutdown_event.is_set():
        mute_until = _check_turn_mute(turn_event, mailbox, time.monotonic(), mute_until, alerts)
        if time.monotonic() < mute_until:
            # Muted: don't consume from the mailbox at all. Checked once per
            # outer-loop iteration — the same cadence _IDLE_SECONDS/_POLL_SECONDS
            # already run at, so this adds no new latency or CPU cost. Does not
            # interrupt an utterance already in progress (see the report on this
            # change for why that's an intentional, disclosed scope limit).
            time.sleep(_IDLE_SECONDS)
            continue

        item = alerts.take() if alerts is not None else mailbox.take()
        if item is None:
            time.sleep(_IDLE_SECONDS)
            continue

        try:
            # Read per item, so a turn of the knob is heard from the next
            # utterance on.
            amplitude = _item_amplitude(item, config, alerts)
            if amplitude is None:                   # speech switch off, not a failsafe alert
                if alerts is not None:
                    alerts.finish(item, DROPPED)    # frees the channel for the scheduler
                continue

            _speak_interruptible(item, mailbox, user_data, amplitude,
                                 amplitude_for=lambda it: _item_amplitude(it, config, alerts))
            _pace(mailbox, user_data)
        except Exception as e:  # never let the worker thread die
            print(f"[TTS] Error processing announcement: {e}")
            if alerts is not None:
                alerts.release(DROPPED)             # never leave the scheduler waiting on it


def _speak_interruptible(item, mailbox, user_data, amplitude=_AMPLITUDE_MAX, amplitude_for=None):
    """
    Speak `item` at espeak-ng `amplitude`. If a preempting item arrives
    mid-utterance, terminate the current espeak process and switch to it —
    looping until an utterance finishes naturally (or shutdown).

    `amplitude_for(item)` (the worker passes _item_amplitude) gives each preempting item its
    own amplitude, or None when it must not be spoken; such an item does not preempt. Without
    it, every item is spoken at `amplitude`. This is what keeps the failsafe override to the
    alert itself: with the speech switch off, a detection cannot cut an alert and then be
    spoken, nor take on the alert's raised volume.

    With user_data.alerts present, every item ends with a finish() call (SPOKEN, CUT, or
    DROPPED) so the alert scheduler knows what became of the alert it offered.
    """
    alerts = getattr(user_data, "alerts", None)

    def speakable(candidate) -> bool:
        return amplitude_for is None or amplitude_for(candidate) is not None

    while item is not None and not user_data.shutdown_event.is_set():
        text = _text_for(item)
        print(f"[TTS] '{text}'")

        proc = _start_espeak(text, amplitude)
        if proc is None:
            # No espeak-ng available — fall back to a blocking, NON-interruptible
            # engine (dev machines only; the device has espeak-ng).
            _speak_blocking(text, amplitude)
            if alerts is not None:
                alerts.finish(item, SPOKEN)
            return

        next_item = None
        cut = False
        while proc.poll() is None:
            if user_data.shutdown_event.is_set():
                proc.terminate()
                return
            pending = mailbox.peek()
            if pending is not None and preempts(pending, item) and speakable(pending):
                proc.terminate()
                cut = True
                # claim the preempting item
                next_item = alerts.take() if alerts is not None else mailbox.take()
                break
            time.sleep(_POLL_SECONDS)

        if next_item is not None and amplitude_for is not None:
            next_amplitude = amplitude_for(next_item)
            if next_amplitude is None:
                # The switch went off between the look and the take: the claimed item is
                # dropped, as it would have been on the idle path. The cut stays a cut.
                if alerts is not None:
                    alerts.finish(next_item, DROPPED)
                next_item = None
            else:
                amplitude = next_amplitude
        if alerts is not None:
            alerts.finish(item, CUT if cut else SPOKEN)
        item = next_item  # None -> utterance finished naturally, loop exits


def _pace(mailbox, user_data):
    """
    Enforce MIN_UTTERANCE_GAP of silence after an utterance for a calm cadence.
    An urgent PENDING item cuts the wait short so it isn't held back.
    """
    deadline = time.monotonic() + MIN_UTTERANCE_GAP
    while time.monotonic() < deadline:
        if user_data.shutdown_event.is_set():
            return
        pending = mailbox.peek()
        if pending is not None and item_tier(pending) == TIER_URGENT:
            return
        time.sleep(_IDLE_SECONDS)


def _text_for(item) -> str:
    """Resolve the spoken text for a queued payload."""
    if "announce" in item:
        return item["announce"]
    phrase = item.get("phrase")
    if phrase:
        return phrase
    return f"{item.get('label', 'unknown')} {item.get('zone', 'center')}"


def _start_espeak(text: str, amplitude: int = _AMPLITUDE_MAX):
    """Start an espeak-ng subprocess (non-blocking). Returns the Popen, or None
    if espeak-ng isn't installed (caller falls back to a blocking engine)."""
    if shutil.which("espeak-ng"):
        return subprocess.Popen(
            ["espeak-ng", "-s", str(_ESPEAK_SPEED), "-a", str(amplitude), text],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    return None


def _speak_blocking(text: str, amplitude: int = _AMPLITUDE_MAX) -> None:
    """pyttsx3 fallback when espeak-ng is unavailable. Blocking, not interruptible."""
    global _pyttsx3_engine
    try:
        import pyttsx3

        if _pyttsx3_engine is None:
            _pyttsx3_engine = pyttsx3.init()
            _pyttsx3_engine.setProperty("rate", _ESPEAK_SPEED)
        # pyttsx3 volume is 0.0-1.0, where 1.0 matches espeak-ng's default.
        _pyttsx3_engine.setProperty("volume", amplitude / _AMPLITUDE_MAX)
        _pyttsx3_engine.say(text)
        _pyttsx3_engine.runAndWait()
    except Exception as e:
        print(f"[TTS] Speech fallback failed: {e}")
