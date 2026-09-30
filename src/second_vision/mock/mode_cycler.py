"""
Mode Cycler — TEMPORARY debug driver. Delete once the Arduino control panel works.

This is a stand-in for the physical mode switch: a timer that walks
pipeline_mode through both -> depth -> detection -> both so hot-swapping can be
exercised before the control panel hardware exists.

It deliberately does exactly what config_reader_worker's "M:<mode>" branch does,
in the same order and from a worker thread, so the demo rehearses the real path
rather than a shortcut:

    announce over TTS  ->  config.update(pipeline_mode=...)  ->  app.trigger_rebuild()

The announcement goes first on purpose. Speaking the mode is a UX requirement —
the user must be told the system is changing — and it doubles as audible warning
before the rebuild blackout (DECISIONS D21).

Activated only by --cycle-modes; without that flag this module is never imported
into a run and the pipeline stays in "both" exactly as before.
"""

import time

# ============================================================
# INTERFACE CONTRACT (matches workers/config_reader.py's M: branch):
#   Input:  wall-clock timer
#   Output: config.pipeline_mode updates + app.trigger_rebuild()
#           + {"announce": str} offered to user_data.tts_queue
# ============================================================

# Order matters: starting from "both" (the boot mode), each step changes which
# models are loaded, and the final step returns to "both" so a full cycle both
# tears down and restores the dual pipeline.
MODE_CYCLE = ("depth", "detection", "both")


def mode_cycler_worker(user_data, config, app, interval_seconds: float) -> None:
    """
    Cycle pipeline_mode every `interval_seconds` until shutdown.

    Args:
        user_data: shared user data (needs tts_queue and shutdown_event)
        config: SystemConfig — the authority the pipeline builder reads
        app: SecondVisionApp — provides trigger_rebuild()
        interval_seconds: dwell time in each mode before switching
    """
    print(f"[CYCLER] DEBUG mode cycling every {interval_seconds:g}s: "
          f"both -> {' -> '.join(MODE_CYCLE)}")

    index = 0
    while not user_data.shutdown_event.is_set():
        # wait() rather than sleep() so Ctrl+C is acted on immediately instead of
        # after the full interval.
        if user_data.shutdown_event.wait(interval_seconds):
            break

        try:
            new_mode = MODE_CYCLE[index % len(MODE_CYCLE)]
            index += 1

            # Announcements carry no priority/tier: item_priority()/item_tier()
            # treat them as +inf / urgent, so this is heard even mid-utterance.
            user_data.tts_queue.offer({"announce": f"{new_mode} mode"})

            config.update(pipeline_mode=new_mode)
            print(f"[CYCLER] switching to {new_mode} at {time.strftime('%H:%M:%S')}")

            # Deferred onto the GLib main loop — never rebuild from this thread.
            app.trigger_rebuild()
        except Exception as e:  # never let the worker thread die
            print(f"[CYCLER] Error during mode switch: {e}")

    print("[CYCLER] stopped")
