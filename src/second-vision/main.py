"""
Second Vision — Main Entry Point

Usage:
  python3 src/second_vision/main.py --input usb                    # Real pipeline, stub consumers
  python3 src/second_vision/main.py --mock                         # Full mock mode (no hardware)
  python3 src/second_vision/main.py --input usb --serial-port /dev/ttyUSB0  # Real serial
"""

import os
import signal
import sys
import threading
import queue
import time

os.environ["GST_PLUGIN_FEATURE_RANK"] = "vaapidecodebin:NONE"

from second_vision.core.config import SystemConfig

# Conditional imports — don't crash if hailo isn't installed (mock mode)
HAILO_AVAILABLE = True
try:
    from hailo_apps.python.core.common.hailo_logger import get_logger
    from hailo_apps.python.core.gstreamer.gstreamer_app import app_callback_class
except ImportError:
    HAILO_AVAILABLE = False
    import logging
    get_logger = logging.getLogger
    # Minimal stub of app_callback_class for mock mode
    class app_callback_class:
        def __init__(self):
            self.frame_count = 0
            self.use_frame = False
            self.running = True
        def increment(self): self.frame_count += 1
        def get_count(self): return self.frame_count

from second_vision.workers.tts_worker import tts_worker
from second_vision.workers.serial_worker import serial_worker
from second_vision.workers.config_reader import config_reader_worker
from second_vision.mock.data_generator import mock_detection_generator, mock_depth_generator

logger = get_logger(__name__) if HAILO_AVAILABLE else logging.getLogger(__name__)


class SecondVisionUserData(app_callback_class):
    """Shared state across all callbacks and workers."""
    
    def __init__(self, config: SystemConfig):
        super().__init__()
        self.config = config
        self.tts_queue = queue.Queue(maxsize=1)
        self.serial_queue = queue.Queue(maxsize=10)
        self.shutdown_event = threading.Event()


def main():
    import argparse
    
    # Minimal parser for mock mode (full parser comes from pipeline app in real mode)
    pre_parser = argparse.ArgumentParser(add_help=False)
    pre_parser.add_argument("--mock", action="store_true", help="Run in full mock mode (no hardware)")
    pre_args, remaining = pre_parser.parse_known_args()
    
    # 1. Shared config
    config = SystemConfig()
    
    # 2. Shared user data
    user_data = SecondVisionUserData(config)
    
    # 3. Start worker threads
    workers = []
    
    # TTS worker — always starts (stub or real, same interface)
    tts_thread = threading.Thread(
        target=tts_worker, args=(user_data, config),
        daemon=True, name="tts-worker"
    )
    tts_thread.start()
    workers.append(tts_thread)
    print("[MAIN] TTS worker started")
    
    # Serial worker — always starts (stub prints, real sends to ESP32)
    serial_thread = threading.Thread(
        target=serial_worker, args=(user_data, config),
        daemon=True, name="serial-writer"
    )
    serial_thread.start()
    workers.append(serial_thread)
    print("[MAIN] Serial worker started")
    
    # 4. Start pipeline OR mock generators
    if pre_args.mock or not HAILO_AVAILABLE:
        print("[MAIN] Running in MOCK mode (no hardware)")
        _run_mock_mode(user_data, config, workers)
    else:
        _run_pipeline_mode(user_data, config, workers, remaining)
    
    # 5. Shutdown
    print("[MAIN] Shutting down...")
    user_data.shutdown_event.set()
    for t in workers:
        t.join(timeout=2.0)
    print("[MAIN] Done.")


def _run_mock_mode(user_data, config, workers):
    """Replace the GStreamer pipeline with timer-based mock data."""
    
    # Mock detection generator (fires every 2 seconds)
    det_thread = threading.Thread(
        target=mock_detection_generator, args=(user_data,),
        daemon=True, name="mock-det"
    )
    det_thread.start()
    workers.append(det_thread)
    
    # Mock depth generator (fires every 100ms)
    depth_thread = threading.Thread(
        target=mock_depth_generator, args=(user_data,),
        daemon=True, name="mock-depth"
    )
    depth_thread.start()
    workers.append(depth_thread)
    
    print("[MOCK] Generators running. Press Ctrl+C to stop.")
    try:
        while not user_data.shutdown_event.is_set():
            time.sleep(0.5)
    except KeyboardInterrupt:
        pass


def _run_pipeline_mode(user_data, config, workers, cli_args):
    """Run the real GStreamer pipeline."""
    from second_vision.pipeline.app import SecondVisionApp
    
    app = SecondVisionApp(
        app_callback=None,
        user_data=user_data,
        config=config,
    )
    
    # Config reader (if --config-port provided)
    config_port = getattr(app.options_menu, "config_port", None)
    if config_port:
        config_thread = threading.Thread(
            target=config_reader_worker,
            args=(user_data, config, config_port, app),
            daemon=True, name="config-reader"
        )
        config_thread.start()
        workers.append(config_thread)
    
    print("[MAIN] Starting pipeline...")
    try:
        app.run()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
