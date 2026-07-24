#!/bin/bash
cd "$(dirname "$0")/.."
python3 src/second_vision/main.py --input /dev/video0 --width 640 --height 480 --use-frame
