"""
Mock data generators — simulate pipeline output without hardware.

Used with --mock flag for development without RPi5/Hailo/camera.
"""

import queue
import random
import time


MOCK_LABELS = ["person", "car", "bicycle", "dog", "chair", "bottle"]
MOCK_ZONES = ["left", "center", "right"]


def mock_detection_generator(user_data):
    """Generates fake detections every 2 seconds."""
    print("[MOCK] Detection generator started (2s interval)")
    
    while not user_data.shutdown_event.is_set():
        time.sleep(2.0)
        
        n_objects = random.randint(0, 3)
        for _ in range(n_objects):
            det = {
                "label": random.choice(MOCK_LABELS),
                "zone": random.choice(MOCK_ZONES),
                "confidence": round(random.uniform(0.5, 0.99), 2),
            }
            try:
                user_data.tts_queue.put_nowait(det)
            except queue.Full:
                pass


def mock_depth_generator(user_data):
    """Generates fake depth zone values every 100ms."""
    print("[MOCK] Depth generator started (100ms interval)")
    
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
        
        try:
            user_data.serial_queue.put_nowait({
                "left": left,
                "center": center,
                "right": right,
                "hazard": random.random() < 0.02,  # 2% chance of hazard
                "hazard_severity": random.randint(100, 255),
            })
        except queue.Full:
            pass
