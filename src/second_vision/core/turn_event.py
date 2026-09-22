"""Latest unconsumed body-turn event from the ESP32 — shared across threads.

Deliberately NOT shaped like core/imu_telemetry.py's ImuTelemetry. That class
holds "what is true right now" — state/pitch/yaw persist until the next
reading overwrites them, and repeatedly reading the same value back is
correct (nothing changed). A turn event is different in kind: it represents
something that happened ONCE (the wearer's torso rotated past the threshold
on the ESP32 side). Holding it as persistent state and letting a consumer
read it over and over would misrepresent a single occurrence as an ongoing
condition. So this exposes push()/take() instead of update()/get()/snapshot():
a consumer either sees a fresh event exactly once, or sees nothing.

Written by workers/serial_worker.py as 0x03 packets arrive off the PiSerial
link (see _extract_turn_event/_take_turn_event there). Read by whatever
downstream consumer eventually acts on a turn — no such consumer exists yet;
that's a separate, not-yet-built task. This module only carries the event.
"""

import threading


class TurnEvent:
    """Thread-safe holder for the most recent, not-yet-consumed turn event."""

    def __init__(self):
        self._lock = threading.Lock()
        self._pending = None  # dict or None

    def push(self, delta_deg: int, received_at: float) -> None:
        """
        Record a newly-decoded turn event, overwriting any not-yet-taken one.

        Overwriting rather than queuing: if two events arrive before anything
        consumes the first, the older one is stale by the time anyone would
        see it — the wearer has already turned again. A queue would only
        delay delivery of the more current information.
        """
        with self._lock:
            self._pending = {"delta_deg": delta_deg, "received_at": received_at}

    def take(self):
        """
        Return and clear the pending event, or None if nothing new arrived
        since the last take(). The clearing is the point: a second take()
        with no new push() in between must not re-deliver the same event.
        """
        with self._lock:
            pending = self._pending
            self._pending = None
            return pending

    def peek(self):
        """Read the pending event WITHOUT consuming it. Mostly for tests/debugging."""
        with self._lock:
            return self._pending
