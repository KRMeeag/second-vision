"""Failsafe vibration patterns: short buzzes the device plays about itself (D41).

A failsafe pattern tells a wearer who cannot see that a part of the device has stopped working
or has come back. Each pattern is a few steps of motor duty on all three motors, distinct by feel:

    reconnect   *beep beep*: two short pulses
    camera      *beep beep*, slower: two long pulses
    earphones   *strong weak*, three times

No firmware change: the serial worker (workers/serial_worker.py), the only writer to the glasses
board, plays a pattern by sending one ordinary motor packet at the start of each step. While it
plays, the pattern owns the motors and the depth stream waits (2.25 s at most, the earphones
pattern); the depth stream resumes on its next frame.

Usage (from any thread; play() never waits)::

    user_data.patterns.play(PATTERN_RECONNECT)      # created in SecondVisionUserData.__init__

What callers can rely on:

  * A pattern plays even with the vibration switch off, at the strength knob's level but never
    under 30 % (core/alerts.py failsafe_level), by the same plain multiply as the depth vibration:
    duty x level. It never changes a setting.
  * Patterns play one at a time, each to its end, in the order asked. Asking for one that is
    already waiting does nothing, so at most one of each waits.
  * While the glasses board is not answering, patterns are dropped, not kept: there is nothing
    to feel them with.
  * A late poll never skips a step: each step starts when it is handed out, so a step is always
    felt for its full length (the pattern stretches by the lateness, a few ms).

The timings and the weak duty are first guesses, chosen to be told apart by feel. The device
check sets them.
"""

import threading
from collections import deque
from typing import NamedTuple, Optional

PATTERN_RECONNECT = "reconnect"
PATTERN_CAMERA = "camera"
PATTERN_EARPHONES = "earphones"

STRONG_DUTY = 255
WEAK_DUTY = 150         # PLACEHOLDER: low enough to feel the step down, high enough to feel at all

# Steps of (seconds, duty at full strength), on all three motors. Every pattern ends on an off
# step: it leaves the motors stopped, and a gap before the depth stream or the next pattern.
PATTERNS = {
    PATTERN_RECONNECT: ((0.15, STRONG_DUTY), (0.15, 0), (0.15, STRONG_DUTY), (0.30, 0)),
    PATTERN_CAMERA: ((0.40, STRONG_DUTY), (0.20, 0), (0.40, STRONG_DUTY), (0.30, 0)),
    PATTERN_EARPHONES: ((0.25, STRONG_DUTY), (0.25, WEAK_DUTY), (0.25, 0)) * 3,
}


class PatternTick(NamedTuple):
    """What the serial worker does now while a pattern plays."""
    name: str
    duty: Optional[int]     # duty to send now, before the strength level; None = nothing new
    wake_at: float          # clock time the next step begins


class PatternPlayer:
    """
    The failsafe patterns waiting to be played, and the one playing.

    Producers call play(). The serial worker calls poll() on every pass of its loop, and abort()
    when the board stops answering. Thread-safe: every method takes one short lock.
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._waiting = deque()
        self._current = None        # name of the pattern playing, or None
        self._step = 0
        self._step_started = 0.0
        self._sent = False          # the current step's duty was handed out

    def play(self, name: str) -> bool:
        """
        Ask for the pattern `name`. True if it was queued; False if it is already waiting.
        Raises ValueError for an unknown name (a bug in the caller).
        """
        if name not in PATTERNS:
            raise ValueError(f"unknown vibration pattern {name!r}")
        with self._lock:
            if name in self._waiting:
                return False
            self._waiting.append(name)
        return True

    def busy(self) -> bool:
        """True while a pattern is playing or waiting."""
        with self._lock:
            return self._current is not None or bool(self._waiting)

    def poll(self, now: float) -> Optional[PatternTick]:
        """
        Advance to `now`. None when no pattern is playing (the motors are the depth stream's);
        otherwise the step in force, with its duty the first time that step is handed out.
        """
        with self._lock:
            if self._current is not None:
                steps = PATTERNS[self._current]
                if now >= self._step_started + steps[self._step][0]:
                    self._step += 1
                    self._step_started = now
                    self._sent = False
                    if self._step == len(steps):
                        print(f"[PATTERN] '{self._current}' done")
                        self._current = None
            if self._current is None:
                if not self._waiting:
                    return None
                self._current = self._waiting.popleft()
                self._step, self._step_started, self._sent = 0, now, False
                print(f"[PATTERN] playing '{self._current}'")
            seconds, duty = PATTERNS[self._current][self._step]
            tick = PatternTick(self._current, None if self._sent else duty, self._step_started + seconds)
            self._sent = True
            return tick

    def abort(self, reason: str) -> None:
        """Drop the pattern playing and every one waiting."""
        with self._lock:
            dropped = ([self._current] if self._current is not None else []) + list(self._waiting)
            self._current = None
            self._waiting.clear()
        if dropped:
            print(f"[PATTERN] dropped {dropped}: {reason}")
