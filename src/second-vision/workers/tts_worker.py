""" TTS Worker - Consumes Detections, produces speech."""

import queue
import time

# ============================================================
# INTERFACE CONTRACT (do not change):
#   Input:  user_data.tts_queue (dict with "label", "zone", "confidence")
#   Output: Audio announcement via speaker
#   Config: config.tts_enabled, config.cooldown_seconds
# ============================================================


class CooldownManager:
    """Tracks per-label-zone cooldown timers."""
    def __init__(self, cooldown_seconds=3.0):
        self.cooldowns = {}
        self.cooldown_seconds = cooldown_seconds

    def should_announce(self, key: str) -> bool:
        now = time.time()
        last = self.cooldowns.get(key, 0)
        if now - last >= self.cooldown_seconds:
            self.cooldowns[key] = now
            return True
        return False

def tts_worker(user_data, config):
    """Main TTS worker loop."""
    cooldown = CooldownManager()

    while not user_data.shutdown_event.is_set():
        try:
            det = user_data.tts_queue.get(timeout=1.0)
        except queue.Empty:
            continue

        if not config.get("tts_enabled"):
            continue

        # Handle mode announcements (from pipeline switch)
        if "announce" in det:
            _speak(det["announce"])
            continue

        # Update cooldown from live config
        cooldown.cooldown_seconds = config.get("cooldown_seconds")

        label = det.get("label", "unknown")
        zone = det.get("zone", "center")
        cooldown_key = f"{label}-{zone}"
        
        if cooldown.should_announce(cooldown_key):
            phrase = f"{label} {zone}"
            _speak(phrase)


def _speak(text: str):
    # TODO: TTS Implementation
    """
    STUB — Replace with real TTS implementation.
    
    Real implementation:
        import subprocess
        subprocess.Popen(["espeak-ng", "-s", "160", text])
    
    Or with pyttsx3:
        engine = pyttsx3.init()
        engine.say(text)
        engine.runAndWait()
    """
    # TEMPORARY, but can be kept for tracking purposes
    print(f"[TTS STUB] 🔊 '{text}'") 