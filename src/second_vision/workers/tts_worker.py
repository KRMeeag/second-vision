"""TTS Worker - Consumes detections, produces speech."""

import queue
import shutil
import subprocess
import time

# ============================================================
# INTERFACE CONTRACT (do not change):
#   Input:  user_data.tts_queue (dict with "label", "zone", "confidence",
#           and an optional "phrase" override for the spoken text)
#   Output: Audio announcement via speaker
#   Config: config.tts_enabled, config.cooldown_seconds
# ============================================================

_ESPEAK_SPEED = 160
_pyttsx3_engine = None


class CooldownManager:
    """Tracks per-label-zone cooldown timers."""

    def __init__(self, cooldown_seconds: float = 3.0):
        self.cooldowns: dict[str, float] = {}
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

        try:
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
                # Callbacks may supply a more specific phrase (e.g. "person leaving
                # left"); fall back to the generic "label zone" when they don't.
                phrase = det.get("phrase") or f"{label} {zone}"
                _speak(phrase)
        except Exception as e:
            print(f"[TTS] Error processing detection: {e}")


def _speak(text: str) -> None:
    """Speak text via espeak-ng, with pyttsx3 fallback when unavailable."""
    print(f"[TTS] '{text}'")

    if shutil.which("espeak-ng"):
        subprocess.Popen(
            ["espeak-ng", "-s", str(_ESPEAK_SPEED), text],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return

    global _pyttsx3_engine
    try:
        import pyttsx3

        if _pyttsx3_engine is None:
            _pyttsx3_engine = pyttsx3.init()
            _pyttsx3_engine.setProperty("rate", _ESPEAK_SPEED)
        _pyttsx3_engine.say(text)
        _pyttsx3_engine.runAndWait()
    except Exception as e:
        print(f"[TTS] Speech fallback failed: {e}")
