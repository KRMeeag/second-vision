#!/bin/bash
cd "$(dirname "$0")/.."
python src/custom_depth_detection/sv_pipeline_v2.py --input rawusb:///dev/video0 --width 640 --height 360 --frame-rate 25