"""Alert worker: runs the alert scheduler (core/alerts.py) on a short tick.

All the logic is in AlertScheduler; this thread only calls its poll(). Each pass does a
few dictionary look-ups under one short lock, so a 0.05 s tick costs next to nothing and an
alert raised now is offered to the speech worker within 0.05 s (when the channel is free).
"""

_TICK_SECONDS = 0.05    # how often the scheduler looks; also how long a shutdown can take


def alert_worker(user_data, config) -> None:
    """Main alert-scheduler loop. Signature follows the other workers: (user_data, config)."""
    alerts = user_data.alerts
    while not user_data.shutdown_event.is_set():
        try:
            alerts.poll()
        except Exception as e:  # never let the worker thread die
            print(f"[ALERT] Error in scheduler: {e}")
        user_data.shutdown_event.wait(_TICK_SECONDS)
