"""
Second Vision - Main Entry Point
Usage: python 3 src/second_vision/main.py --input usb [options]
"""

import os
import signal
import sys
import threading
import queue

# Hailo environment must be loaded before GStreamer imports
os.environ["GST_PLUGIN_FEATURE_RANK"] = "vaapidecodebin:NONE"

# --- Hailo library imports (from installed hailo_apps package) ---
from hailo_apps.python.core.hailo_logger import get_logger
from hailo_apps.python.core.gstreamer.gstreamer_app import app_callback_class

# --- Local project imports ---
#TBA: ADD IMPORTS HERE

logger = get_logger(__name__)

def main():
    # 1. Load Configs
    #TBA

    #2. Create user data
    #TBA

    # 3. Create the Gstreamer pipeline app
    #TBA

    # 4. Start worker threads BEFORE the pipeline
    #TBA

    # 4.1 TTS Worker (espeak-ng)
    # TBA

    # 4.2 Serial Writer (ESP32 motors), only starts if --serial-port is provided as an arg
    # TBA

    # 4.3 Config Reader (Arduino Physical Control Panel)
    # TBA

    # 5. Run the GStreamer Pipeline (MAIN LOOP)
    # TBA

    # 6. Handle shutdown signals (SIGINT/SIGTERM)
    # TBA
    # TBA: Add shutdown handlers that clean up the pipeline and worker threads

if __name__ == "main":
    main()