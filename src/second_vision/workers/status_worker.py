"""Status worker: the failsafe checks, read twice a second, for the screen and the terminal.

All the logic is in core/status_board.py; this thread only reads the status, keeps the latest on
user_data.status (the camera picture draws it: pipeline/callbacks.py and pipeline/app.py) and prints
each change to the terminal. It only reads the alert scheduler and the camera view check, so it can
neither delay nor change an alert.
"""

from second_vision.core.status_board import STATUS_SECONDS, StatusReporter, read_status


def status_worker(user_data, config) -> None:
    """Main status loop. Signature follows the other workers: (user_data, config)."""
    reporter = StatusReporter()
    while not user_data.shutdown_event.is_set():
        try:
            status = read_status(user_data.alerts, getattr(user_data, "camera_view", None), config)
            user_data.status = status          # one reference swap: readers see a whole status
            reporter.report(status)
        except Exception as e:  # never let the worker thread die
            print(f"[CHECK] Error reading the status: {e}")
        user_data.shutdown_event.wait(STATUS_SECONDS)
