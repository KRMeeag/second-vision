"""Latest BMI160 reading from the ESP32 — shared across threads.

Mirrors core/config.py's SystemConfig: a lock-guarded holder with get/update/
snapshot, not a queue. A queue implies a consumer draining every item in
order; nothing here needs history, only "what did the IMU say most recently",
so a queued backlog would just be stale data waiting to be read.

Written by workers/serial_worker.py as 0x02 telemetry packets arrive off the
PiSerial link (see _check_ack/_take_imu_telemetry there). Read by whatever
downstream consumer eventually interprets these values — no such consumer
exists yet; that interpretation (the "turning-body algorithm") is a separate,
not-yet-built task. This module only carries the numbers.
"""

import threading


class ImuTelemetry:
    """Thread-safe holder for the most recent BMI160 reading."""

    def __init__(self):
        self._lock = threading.Lock()
        # None until the first valid 0x02 packet arrives — distinguishes
        # "never received a reading" from "received a normal reading",
        # which matters to any consumer deciding whether the link has ever
        # been up at all.
        self.state = None          # "normal" | "head_moving" | "wrong_pitch" | "unknown"
        self.raw_pitch = None      # int16, signed
        self.raw_yaw = None        # int16, signed
        self.received_at = None    # time.monotonic() of the last valid packet

    def get(self, key):
        with self._lock:
            return getattr(self, key, None)

    def update(self, **kwargs):
        with self._lock:
            for k, v in kwargs.items():
                if hasattr(self, k) and not k.startswith("_"):
                    setattr(self, k, v)

    def snapshot(self):
        with self._lock:
            return {k: v for k, v in self.__dict__.items()
                    if not k.startswith("_")}
