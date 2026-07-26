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

Cooldown/suppression is NOT here anymore — it moved entirely into the callback +
priority layer (core/priority.py). This worker just speaks what it is handed.
"""

import shutil
import subprocess
import time

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
#   Output: Audio announcement via speaker (espeak-ng, pyttsx3 fallback).
#   Config: config.tts_enabled
# ============================================================

_ESPEAK_SPEED = 160
_POLL_SECONDS = 0.05    # how often to check for a preempting item while speaking
_IDLE_SECONDS = 0.02    # nap when the mailbox is empty
_pyttsx3_engine = None


def tts_worker(user_data, config):
    """Main TTS worker loop."""
    mailbox = user_data.tts_queue

    while not user_data.shutdown_event.is_set():
        item = mailbox.take()
        if item is None:
            time.sleep(_IDLE_SECONDS)
            continue

        try:
            if not config.get("tts_enabled"):
                continue

            _speak_interruptible(item, mailbox, user_data)
            _pace(mailbox, user_data)
        except Exception as e:  # never let the worker thread die
            print(f"[TTS] Error processing announcement: {e}")


def _speak_interruptible(item, mailbox, user_data):
    """
    Speak `item`. If a preempting item arrives mid-utterance, terminate the
    current espeak process and switch to it — looping until an utterance
    finishes naturally (or shutdown).
    """
    while item is not None and not user_data.shutdown_event.is_set():
        text = _text_for(item)
        print(f"[TTS] '{text}'")

        proc = _start_espeak(text)
        if proc is None:
            # No espeak-ng available — fall back to a blocking, NON-interruptible
            # engine (dev machines only; the device has espeak-ng).
            _speak_blocking(text)
            return

        next_item = None
        while proc.poll() is None:
            if user_data.shutdown_event.is_set():
                proc.terminate()
                return
            pending = mailbox.peek()
            if pending is not None and preempts(pending, item):
                proc.terminate()
                next_item = mailbox.take()  # claim the preempting item
                break
            time.sleep(_POLL_SECONDS)

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


def _start_espeak(text: str):
    """Start an espeak-ng subprocess (non-blocking). Returns the Popen, or None
    if espeak-ng isn't installed (caller falls back to a blocking engine)."""
    if shutil.which("espeak-ng"):
        return subprocess.Popen(
            ["espeak-ng", "-s", str(_ESPEAK_SPEED), text],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    return None


def _speak_blocking(text: str) -> None:
    """pyttsx3 fallback when espeak-ng is unavailable. Blocking, not interruptible."""
    global _pyttsx3_engine
    try:
        import pyttsx3

        if _pyttsx3_engine is None:
            _pyttsx3_engine = pyttsx3.init()
            _pyttsx3_engine.setProperty("rate", _ESPEAK_SPEED)
        _pyttsx3_engine.say(text)
        _pyttsx3_engine.runAndWait()
    except Exception as e:
        print(f"[TTS] Speech fallback failed: {e}")
