"""Alert scheduler: speak a system alert on a schedule, and never lose it.

A system alert is a sentence the device must say about itself ("controller board not
connected", "battery low") and keep saying while the condition lasts. Several tasks raise
alerts (camera, controller board, battery, ...). This module is the one place that knows
about timing, so none of them writes timing code: they say WHAT to say and HOW OFTEN, and
clear the alert when the condition ends.

Usage (from any thread; none of these calls waits for the speech worker)::

    alerts = user_data.alerts            # created in SecondVisionUserData.__init__

    # "wording X every 30 s while depth is on, once otherwise":
    alerts.raise_alert("controller-missing", "Controller board not connected",
                       interval=30.0, repeat=lambda: depth_on(config))
    # (core/connections.py depth_on: pipeline_mode "depth" or "both". Not
    #  config.get("depth_enabled"): the panel never sends it, so it stays True.)
    # ...the scheduler asks config each tick, so a depth switch-off mid-run needs no code.
    # Without a callable, switch it by hand:  alerts.set_repeat("controller-missing", False)

    alerts.raise_alert("battery-low", "Battery low", interval=300.0)   # every 5 minutes
    alerts.raise_alert("camera-lost", "Camera disconnected")           # once (interval=None)

    alerts.clear("controller-missing")   # the condition is over: nothing more is spoken for it

What it does, and what the callers can rely on:

  * Raised alert: spoken at once (the next scheduler tick, 0.05 s, as soon as the utterance in
    progress ends), then again `interval` seconds after the START of each previous utterance, for
    as long as it stays raised and its repeat is on. `interval=None` means once.
  * Repeat off: no more repeats. An utterance not yet delivered (the first one, or one that
    was cut) is still delivered, once. Repeat back on: the cadence resumes from the start
    of the last delivered utterance, so if that is more than `interval` ago the alert is
    spoken as soon as the channel is free (the workbook is silent on this case).
  * clear(): stops at once. An utterance already under way is not cut; a queued one is
    removed from the mailbox (only the scheduler's own item, never someone else's).
  * Several alerts: spoken one after another, each to the end, in the order they fall
    due; a second alert is never offered while one is queued or being spoken, so alerts
    cannot cut or replace each other.
  * An alert in progress finishes (D-16, user 2026-10-18): no object warning cuts it, however
    urgent, and an alert waiting in the slot cuts nothing; it is said when the utterance in
    progress ends (workers/tts_worker.py `_cuts`). An object warning cannot replace it in the
    slot either (an announcement outranks every detection).
  * Not lost: a mode announcement may still cut an alert or replace it in the mailbox slot.
    The scheduler is told what became of every item it offers (see "Speech worker side") and
    speaks the alert again as soon as the channel is free, a once-only alert included. A
    head-turn mute does not discard it: the alert waits in the slot and is spoken when the
    mute ends.
  * A silent alert (`speak=False`) is raised and counted but never spoken: see raise_alert().
  * `on_speak` (raise_alert) is called each time an utterance of the alert starts: the hook for a
    vibration that goes with the sentence.
  * Every alert raised here is a failsafe alert (D41): it is spoken even with the speech
    switch off (config.tts_enabled False), at the volume knob's level but never under 30 %
    (failsafe_level below). Neither changes a setting; detections and mode announcements still
    obey the switch and the knob. An item the worker drops after all (an error) counts as
    delivered (no storm of re-offers); the next repeat tries again as normal.
  * An alert is offered when it is due and the slot is empty or holds a detection item (which
    it replaces, as any announcement does): when the worker is idle, and also while it is
    speaking an object warning (the alert then waits in the slot). While an announcement is
    being spoken it waits for the end. It never overwrites an announcement.
  * raise_alert() with the same text and interval as the alert already raised changes
    nothing, so it can be called every loop. With a different text or interval it replaces
    the alert (no second copy).
  * A `repeat` callable runs on the scheduler's thread and must return at once: a blocking
    one delays every alert and the stop.

Speech worker side (workers/tts_worker.py; producers never call these): `take()` instead of
mailbox.take(), `drain()` instead of mailbox.take() in the head-turn drain, `is_failsafe(item)`
to tell an alert from everything else, and `finish(item, SPOKEN | CUT | DROPPED)` when an item
ends. They run under the scheduler's
lock, which is what makes "was my item taken, replaced or still waiting?" exact: the
scheduler never has to guess from timing.

Known limits: two races of a few microseconds remain because PriorityMailbox has no
"offer only if empty" and no "remove only this item": a mode announcement offered in the
instant between the scheduler's look at the slot and its offer is replaced by the alert
(the tie rule: the incoming item wins); in clear(), an item that slipped in at that moment
is taken out and put straight back. Neither can lose or double an alert.
"""

import math
import threading
import time
from typing import Callable, Optional, Union

SPOKEN = "spoken"      # the utterance ran to its end
CUT = "cut"            # the utterance was terminated for a preempting item
DROPPED = "dropped"    # the worker took the item and did not speak it (an error)

RepeatFlag = Union[bool, Callable[[], bool]]

# D41: a failsafe alert is spoken, and a failsafe vibration pattern played, even with the wearer's
# switch off, at the knob's own level but never under this. It is on the knob's 0.0-1.0 scale, the
# value the panel reports: tts_volume for speech, motor_strength for vibration.
FAILSAFE_MIN_LEVEL = 0.30


def failsafe_level(knob) -> float:
    """
    The level a failsafe alert or pattern uses for knob position `knob`: the knob's own level,
    never under FAILSAFE_MIN_LEVEL. Never raises: anything that is not a finite number counts as
    1.0, the knob's default, and values above 1.0 are clamped.
    """
    try:
        knob = float(knob)
    except (TypeError, ValueError):
        return 1.0
    if not math.isfinite(knob):
        return 1.0
    return max(FAILSAFE_MIN_LEVEL, min(1.0, knob))


class _Alert:
    """One raised alert: what to say, how often, and where it stands."""

    def __init__(self, name: str, text: str, interval: Optional[float],
                 repeat: RepeatFlag, seq: int, now: float, speak: bool = True,
                 on_speak: Optional[Callable[[], None]] = None):
        self.name = name
        self.text = text
        self.interval = interval
        self.speak = speak                  # False: raised and counted, never offered to speech
        self.on_speak = on_speak            # called each time the speech worker starts the utterance
        self.seq = seq                      # raise order, breaks ties between alerts due together
        self.pending = True                 # an utterance is owed that was not delivered yet
        self.pending_since = now            # ...since this time (raise time, or the time it was lost)
        self.last_start = None              # start of the last delivered utterance
        self.repeat = repeat
        self.repeat_value = False if callable(repeat) else bool(repeat)

    def due_at(self) -> Optional[float]:
        """When the next utterance falls due, or None if none is owed (a spent once-alert, a silent one)."""
        if not self.speak:
            return None
        if self.pending:
            return self.pending_since
        if self.interval is not None and self.repeat_value and self.last_start is not None:
            return self.last_start + self.interval
        return None


class _Flight:
    """The one item the scheduler has in the speech channel: offered, then speaking."""

    def __init__(self, alert: _Alert, item: dict):
        self.alert = alert
        self.item = item                    # the exact {"announce": text} dict put in the mailbox
        self.speaking = False               # True once the worker has taken it
        self.started_at = None              # clock time the worker took it: the utterance start


class AlertScheduler:
    """
    Raised alerts, and the single-item offer protocol to the speech worker.

    `mailbox` is user_data.tts_queue (the PriorityMailbox the speech worker reads).
    `clock` returns seconds and must not go backwards; tests pass a virtual one.
    Thread-safe: every public method takes one short lock and never waits for anything else.
    """

    def __init__(self, mailbox, clock: Callable[[], float] = time.monotonic):
        self._mailbox = mailbox
        self._clock = clock
        self._lock = threading.Lock()
        self._alerts: dict = {}
        self._seq = 0
        self._flight: Optional[_Flight] = None   # at most ONE item of ours in the channel
        self._busy = None                        # the item the speech worker is handling now
        self._last_spoken = None                 # (text, clock time) of the last alert utterance started

    # ------------------------------------------------------------------
    # Producer API (any thread)
    # ------------------------------------------------------------------
    def raise_alert(self, name: str, text: str, interval: Optional[float] = None,
                    repeat: RepeatFlag = True, speak: bool = True,
                    on_speak: Optional[Callable[[], None]] = None) -> None:
        """
        Raise the alert `name` saying `text`; `interval` seconds between repeats, None = once.

        `repeat` is True/False, or a zero-argument callable returning a bool that the
        scheduler evaluates on every tick (an exception in it keeps the last value).
        Raising a name that is already raised with the same text and interval only updates
        `repeat`; with a different text or interval it replaces the alert, which is then
        spoken at once. Raises ValueError for an empty name or text, or an interval that
        is not a positive number (a bug in the caller, not a runtime condition).

        `speak=False` raises a silent alert: it is listed by raised() and is_raised() until
        clear(), and is never offered to the speech worker. For a part that is missing but has
        nothing to be said about it (the earphones: there is nothing to speak through), so that
        "everything is connected" can still see it (core/connections.py, D-17). `text` then only
        describes it, and `interval` and `repeat` have no effect.

        `on_speak`, if given, is called with no arguments each time the speech worker STARTS
        saying this alert (so on every repeat, and again if an utterance was cut and is said
        anew), on the speech worker's thread, after the scheduler's lock is released. It must
        return at once (a pattern player's play() does). An exception in it is logged and
        changes nothing. For a vibration that goes with the sentence (the camera's *beep beep*,
        core/haptic_patterns.py). Raising the same alert again unchanged replaces it.
        """
        if not isinstance(name, str) or not name:
            raise ValueError("alert name must be a non-empty string")
        if not isinstance(text, str) or not text.strip():
            raise ValueError("alert text must be a non-empty string")
        if interval is not None and not (isinstance(interval, (int, float))
                                         and math.isfinite(interval) and interval > 0):
            raise ValueError(f"alert interval must be a positive number or None, got {interval!r}")
        with self._lock:
            current = self._alerts.get(name)
            if (current is not None and current.text == text and current.interval == interval
                    and current.speak == speak):
                self._set_repeat_locked(current, repeat)
                current.on_speak = on_speak
                return
            if current is not None:
                self._withdraw_locked(current)
            self._seq += 1
            self._alerts[name] = _Alert(name, text, interval, repeat, self._seq, self._clock(), speak,
                                        on_speak)
        how = ("silent" if not speak else "once" if interval is None else f"every {interval:g} s")
        print(f"[ALERT] raised '{name}': '{text}' ({how})")

    def set_repeat(self, name: str, repeat: RepeatFlag) -> bool:
        """Switch the repeat of a raised alert on or off (or give it a callable). False if not raised."""
        with self._lock:
            alert = self._alerts.get(name)
            if alert is None:
                return False
            self._set_repeat_locked(alert, repeat)
            return True

    def clear(self, name: str) -> bool:
        """
        Stop the alert `name` at once. An utterance under way is not cut; a queued item is
        removed from the mailbox. True if it was raised, False (and nothing happens) if not.
        """
        with self._lock:
            alert = self._alerts.pop(name, None)
            if alert is None:
                return False
            self._withdraw_locked(alert)
        print(f"[ALERT] cleared '{name}'")
        return True

    def is_raised(self, name: str) -> bool:
        """True from raise_alert() until clear(); a once-alert stays raised after it was spoken."""
        with self._lock:
            return name in self._alerts

    def raised(self) -> list:
        """Names of the raised alerts, oldest first."""
        with self._lock:
            return [a.name for a in sorted(self._alerts.values(), key=lambda a: a.seq)]

    def last_spoken(self):
        """
        (text, clock time) of the last alert the speech worker started saying, or None. For the
        on-screen and terminal status (core/status_board.py): a bone-conduction earphone is heard
        by the wearer only, so the screen shows what was said.
        """
        with self._lock:
            return self._last_spoken

    # ------------------------------------------------------------------
    # The scheduler's own step (alert_worker calls it every 0.05 s; tests call it by hand)
    # ------------------------------------------------------------------
    def poll(self) -> None:
        """One scheduling step: decide whether an alert is due and the channel allows offering it."""
        with self._lock:
            watched = [(a, a.repeat) for a in self._alerts.values() if callable(a.repeat)]
        # The callables are the caller's code: run them outside the lock.
        results = []
        for alert, fn in watched:
            try:
                results.append((alert, fn, bool(fn())))
            except Exception as e:  # keep the last value; never let the scheduler thread die
                print(f"[ALERT] repeat check of '{alert.name}' failed: {e}")
        with self._lock:
            for alert, fn, value in results:
                if alert.repeat is fn:
                    alert.repeat_value = value
            self._step_locked()

    # ------------------------------------------------------------------
    # Speech worker side (workers/tts_worker.py)
    # ------------------------------------------------------------------
    def take(self):
        """Take the pending item from the mailbox, noting it as being handled. For mailbox.take()."""
        on_speak = name = None
        with self._lock:
            item = self._mailbox.take()
            if item is not None:
                self._busy = item
                flight = self._flight
                if flight is not None and flight.item is item:
                    flight.speaking = True
                    flight.started_at = self._clock()
                    on_speak, name = flight.alert.on_speak, flight.alert.name
                    self._last_spoken = (flight.alert.text, flight.started_at)
        if on_speak is not None:
            # Outside the lock, so the callback may call the scheduler and cannot hold it up.
            try:
                on_speak()
            except Exception as e:  # never let the speech worker die
                print(f"[ALERT] on_speak of '{name}' failed: {e}")
        return item

    def drain(self):
        """
        The head-turn drain: like mailbox.take() (the caller discards what it returns), but an
        alert of ours that is waiting in the slot stays there; a turn is no reason to lose it.
        """
        with self._lock:
            flight = self._flight
            if flight is not None and not flight.speaking and self._mailbox.peek() is flight.item:
                return None
            return self._mailbox.take()

    def is_failsafe(self, item) -> bool:
        """
        True if `item` is the alert this scheduler put in the speech channel. Every alert raised
        here is a failsafe alert (D41), spoken even with the speech switch off; anything else (a
        detection, a mode announcement) is not.
        """
        with self._lock:
            flight = self._flight
            return flight is not None and flight.item is item

    def finish(self, item, outcome: str) -> None:
        """The worker is done with `item`: SPOKEN (ran to its end), CUT, or DROPPED."""
        with self._lock:
            self._finish_locked(item, outcome)

    def release(self, outcome: str = DROPPED) -> None:
        """The worker failed while handling an item: close whatever it was handling."""
        with self._lock:
            if self._busy is not None:
                self._finish_locked(self._busy, outcome)

    # ------------------------------------------------------------------
    # Internals (the lock is held)
    # ------------------------------------------------------------------
    @staticmethod
    def _set_repeat_locked(alert: _Alert, repeat: RepeatFlag) -> None:
        alert.repeat = repeat
        if not callable(repeat):
            alert.repeat_value = bool(repeat)

    def _remove_item_locked(self, item) -> None:
        """Take `item` out of the mailbox if it is still the stored one; never anyone else's."""
        if self._mailbox.peek() is item:
            taken = self._mailbox.take()
            if taken is not None and taken is not item:   # something newer slipped in: put it back
                self._mailbox.offer(taken)

    def _withdraw_locked(self, alert: _Alert) -> None:
        """The alert is cleared or replaced: pull its queued item; leave an utterance under way."""
        flight = self._flight
        if flight is None or flight.alert is not alert or flight.speaking:
            return
        self._remove_item_locked(flight.item)
        self._flight = None

    def _finish_locked(self, item, outcome: str) -> None:
        if self._busy is item:
            self._busy = None
        flight = self._flight
        if flight is None or flight.item is not item:
            return                                      # not ours: an ordinary announcement
        self._flight = None
        alert = flight.alert
        if self._alerts.get(alert.name) is not alert:
            return                                      # cleared or replaced meanwhile: let it go
        if outcome == CUT:
            alert.pending = True
            alert.pending_since = self._clock()
            print(f"[ALERT] '{alert.name}' was cut; will be spoken again")
        else:                                           # SPOKEN, or DROPPED (counts as delivered)
            alert.pending = False
            alert.last_start = flight.started_at if flight.started_at is not None else self._clock()

    def _step_locked(self) -> None:
        now = self._clock()
        flight = self._flight
        if flight is not None:
            if flight.speaking:
                return                                  # being spoken: wait for finish()
            if self._mailbox.peek() is flight.item:
                return                                  # in the slot: the worker has not got to it
            # Not in the slot and never taken (the worker takes through take(), under this
            # lock): another announcement replaced it. The alert is still owed (it was not
            # delivered, so it is still pending or past its due time): it is offered again below.
            self._flight = None
            print(f"[ALERT] '{flight.alert.name}' was replaced in the queue; will be offered again")
        busy = self._busy
        if busy is not None and "announce" in busy:
            # An announcement (a mode change) is being spoken: wait for it to end, as before. It is
            # short, and one offered meanwhile would replace the alert in the slot (the mailbox's tie
            # rule), over and over in a burst of switch flips.
            return
        # Behind an object warning, though, the alert is offered at once (D-16): it waits in the
        # slot, cuts nothing (tts_worker._cuts) and is said when that utterance ends. Waiting for an
        # idle worker kept it out for good under urgent warnings that cut each other.
        occupant = self._mailbox.peek()
        if occupant is not None and "announce" in occupant:
            return                                      # never overwrite an announcement
        due = None
        for alert in self._alerts.values():
            at = alert.due_at()
            if at is not None and at <= now and (due is None or (at, alert.seq) < due[0]):
                due = ((at, alert.seq), alert)
        if due is None:
            return
        alert = due[1]
        item = {"announce": alert.text}
        self._mailbox.offer(item)
        self._flight = _Flight(alert, item)
