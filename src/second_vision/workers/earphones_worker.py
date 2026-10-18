"""Earphones worker: looks once a second for the USB earphones and reports lost and back (F3).

All the logic is in EarphonesWatcher (core/connections.py); this thread only calls its step().
One step is a listing of /proc/asound (four small directories) and, on a change, a few short
calls into the alert scheduler and the pattern player, so it costs next to nothing and never
touches the camera pipeline.
"""

import time

from second_vision.core.connections import EarphonesWatcher

EARPHONES_POLL_SECONDS = 1.0    # how often the earphones are looked for (F3: every 1 to 2 s)


def earphones_worker(user_data, config) -> None:
    """Main presence-check loop. Signature follows the other workers: (user_data, config)."""
    # getattr for the same reason as in serial_worker: a user_data without a pattern player
    # still gets the alert and the sentences, just no vibration.
    watcher = EarphonesWatcher(user_data.alerts, getattr(user_data, "patterns", None), config)
    while not user_data.shutdown_event.is_set():
        try:
            watcher.step(time.monotonic())
        except Exception as e:  # never let the worker thread die
            print(f"[EARPHONES] Error in presence check: {e}")
        user_data.shutdown_event.wait(EARPHONES_POLL_SECONDS)
