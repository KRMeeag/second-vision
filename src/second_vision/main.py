"""
Second Vision — Main Entry Point

Usage:
  python3 src/second_vision/main.py --input usb                    # Real pipeline, no board attached
  python3 src/second_vision/main.py --mock                         # Full mock mode (no hardware)
  python3 src/second_vision/main.py --input usb --serial-port /dev/ttyUSB0   # ESP32 over USB
  python3 src/second_vision/main.py --input usb --serial-port /dev/serial0   # ESP32 on the GPIO pins

Omit --serial-port to run with no ESP32: everything is still computed and
packed, it just isn't written anywhere. Opening a port needs membership of the
`dialout` group (check with `groups`); a re-login is required after adding it.
"""

import os
import sys
import threading
import queue
import time
from pathlib import Path

os.environ["GST_PLUGIN_FEATURE_RANK"] = "vaapidecodebin:NONE"

# Running this file directly (`python3 src/second_vision/main.py`) puts
# src/second_vision/ on sys.path, not src/ itself — so the `second_vision.*`
# absolute imports below can't resolve without src/ on the path too.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from second_vision.core.config import SystemConfig
from second_vision.core.priority import PriorityMailbox

# Conditional imports — don't crash if hailo isn't installed (mock mode)
HAILO_AVAILABLE = True
try:
    # pyrefly: ignore [missing-import]
    from hailo_apps.python.core.common.hailo_logger import get_logger
    # pyrefly: ignore [missing-import]
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

# user_app_callback_class only exists in callbacks.py when the real hailo/gi
# stack imported above did too — in mock mode there's no tracking state to
# carry, since the mock generators push straight into the queues and never
# go through on_det_frame at all.
if HAILO_AVAILABLE:
    from second_vision.pipeline.callbacks import user_app_callback_class
    _UserDataBase = user_app_callback_class
else:
    _UserDataBase = app_callback_class

logger = get_logger(__name__) if HAILO_AVAILABLE else logging.getLogger(__name__)


class SecondVisionUserData(_UserDataBase):
    """Shared state across all callbacks and workers."""

    def __init__(self, config: SystemConfig):
        super().__init__()
        self.config = config
        # TTS uses a priority mailbox (offer/take/peek), not a FIFO queue — see
        # core/priority.py. Serial stays a plain FIFO stream.
        self.tts_queue = PriorityMailbox()
        self.serial_queue = queue.Queue(maxsize=10)
        self.shutdown_event = threading.Event()


def main():
    import argparse
    
    # Minimal parser for mock mode (full parser comes from pipeline app in real mode)
    pre_parser = argparse.ArgumentParser(add_help=False)
    pre_parser.add_argument("--mock", action="store_true", help="Run in full mock mode (no hardware)")
    # Parsed HERE rather than in the pipeline app's parser because the serial
    # worker starts before the pipeline is built, and mock mode never builds one
    # at all — so this is the only place both paths pass through. parse_known_args
    # leaves everything else in `remaining` for the pipeline parser.
    pre_parser.add_argument(
        "--serial-port", default=None, metavar="DEV",
        help="Serial device for the ESP32, e.g. /dev/ttyUSB0 (USB) or "
             "/dev/serial0 (GPIO pins). Omit to run with no board attached.",
    )
    pre_parser.add_argument(
        "--serial-baud", type=int, default=None, metavar="RATE",
        help="Serial baud rate (default 115200; must match the ESP32 firmware)",
    )
    pre_args, remaining = pre_parser.parse_known_args()

    # 1. Shared config
    config = SystemConfig()
    if pre_args.serial_port:
        config.update(serial_port=pre_args.serial_port)
    if pre_args.serial_baud:
        config.update(serial_baudrate=pre_args.serial_baud)

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

    # SecondVisionApp builds its own parser and reads sys.argv directly, so the
    # flags main() already consumed (--mock, --serial-port, --serial-baud) would
    # reach it as unrecognized arguments and abort the run. cli_args is exactly
    # what parse_known_args left over, so hand the app that and nothing else.
    sys.argv = [sys.argv[0]] + list(cli_args)

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

    # DEBUG ONLY (--cycle-modes): stand in for the not-yet-built Arduino mode
    # switch by cycling pipeline_mode on a timer. Without the flag this block is
    # skipped entirely and the pipeline stays in "both".
    cycle_seconds = getattr(app.options_menu, "cycle_modes", 0.0)
    if cycle_seconds:
        from second_vision.mock.mode_cycler import mode_cycler_worker

        cycler_thread = threading.Thread(
            target=mode_cycler_worker,
            args=(user_data, config, app, cycle_seconds),
            daemon=True, name="mode-cycler"
        )
        cycler_thread.start()
        workers.append(cycler_thread)
        app.start_mode_monitor()

    print("[MAIN] Starting pipeline...")
    try:
        app.run()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
