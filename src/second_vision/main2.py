"""
main2.py — TEMPORARY visual smoke test, not part of the real entry point.

Runs the actual GStreamer pipeline (pipeline/app.py's SecondVisionApp,
wired to pipeline/callbacks.py's on_det_frame/on_depth_frame) the way
main.py's _run_pipeline_mode would launch it, but skips starting the
tts_worker/serial_worker/config_reader threads. Point is to visually confirm
the merged pipeline+callbacks actually run end-to-end, without needing
espeak-ng or ESP32 hardware attached, and without their console spam.

tts_queue/serial_queue still get filled by the real callbacks — nothing
drains them here, but both queues drop-and-continue when full (see
callbacks.py's queue.Full handling), so that's harmless for this purpose.

Usage:
    python3 src/second_vision/main2.py --input usb
    python3 src/second_vision/main2.py --input usb --use-frame   # + cv2 zone overlay window

Delete this file once main.py's own pipeline-mode path is confirmed working
against the same SecondVisionApp/StandaloneUserData wiring app.py provides.
"""
import os
import sys
from pathlib import Path

os.environ["GST_PLUGIN_FEATURE_RANK"] = "vaapidecodebin:NONE"

sys.path.insert(0, str(Path(__file__).resolve().parent / "pipeline"))
import app as pipeline_app


def main():
    print("[MAIN2] Building pipeline (no tts/serial/config-reader workers)...")
    user_data = pipeline_app.StandaloneUserData()
    app = pipeline_app.SecondVisionApp(None, user_data)
    print("[MAIN2] Running. Ctrl+C to stop.")
    try:
        app.run()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
