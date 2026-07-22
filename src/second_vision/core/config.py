"""System configuration - shared across ALL threads."""

import threading

class SystemConfig:
    """Thread-safe shared configuration"""
    def __init__(self):    
        self._lock = threading.Lock()
        # Pipeline
        self.pipeline_mode = "both"     # "detection", "depth", "both"
        # Motors
        self.motor_strength = 1.0       # 0.0 - 1.0
        self.vibration_enabled = True
        # TTS
        self.tts_enabled = True
        self.cooldown_seconds = 3.0
        # Features
        self.depth_enabled = True
        self.detection_enabled = True

    # --- Basic R/W methods ---
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

        

