"""
Mock data generators — simulate pipeline output without hardware.

Used with --mock flag for development without RPi5/Hailo/camera.
"""

import queue
import random
import time

from second_vision.core.haptics import HapticMapper


MOCK_LABELS = ["person", "car", "bicycle", "dog", "chair", "bottle"]
MOCK_ZONES = ["left", "center", "right"]


def mock_detection_generator(user_data):
    """Generates fake detections every 2 seconds."""
    print("[MOCK] Detection generator started (2s interval)")
    
    while not user_data.shutdown_event.is_set():
        time.sleep(2.0)
        
        n_objects = random.randint(0, 3)
        for _ in range(n_objects):
            zone = random.choice(MOCK_ZONES)
            confidence = round(random.uniform(0.5, 0.99), 2)
            det = {
                "label": random.choice(MOCK_LABELS),
                "zone": zone,
                "confidence": confidence,
                # Nominal priority/tier so the PriorityMailbox can rank mock items;
                # center nudged up to loosely mirror the real zone weighting.
                "priority": round(confidence + (0.5 if zone == "center" else 0.0), 2),
                "tier": "normal",
            }
            # tts_queue is a PriorityMailbox now — offer() never blocks or raises.
            user_data.tts_queue.offer(det)


def mock_depth_generator(user_data):
    """
    Generates fake depth zone values every 100ms, shaped exactly like the real
    callback shapes them.

    The random walk stands in for the DEPTH POST-PROCESSOR's output (perception,
    0-255), not for what goes on the wire — serial_queue carries motor duties.
    Running the mock through its own HapticMapper is what keeps mock mode an
    honest rehearsal: without it the mock would drive the motors with unshaped
    values, so the one mode you can run without a camera would be the one mode
    that never exercises the deadband, the PWM floor or the pulse.
    """
    print("[MOCK] Depth generator started (100ms interval)")

    haptics = HapticMapper()

    # Simulate slowly changing depth values
    left = 128
    center = 64
    right = 192

    while not user_data.shutdown_event.is_set():
        time.sleep(0.1)

        # Random walk
        left   = max(0, min(255, left   + random.randint(-10, 10)))
        center = max(0, min(255, center + random.randint(-10, 10)))
        right  = max(0, min(255, right  + random.randint(-10, 10)))

        motors = haptics.shape({"left": left, "center": center, "right": right},
                               time.monotonic())

        # Severity is rolled only when a hazard actually fired. Rolling it
        # unconditionally (the previous behaviour) put a non-zero break size on
        # a frame reporting no break, which is a contradiction the consumer only
        # tolerates because it happens to read severity inside `if hazard`.
        hazard = random.random() < 0.02  # 2% chance of hazard
        try:
            user_data.serial_queue.put_nowait({
                "left":   motors["left"],
                "center": motors["center"],
                "right":  motors["right"],
                "hazard": hazard,
                "hazard_severity": random.randint(100, 255) if hazard else 0,
            })
        except queue.Full:
            pass
