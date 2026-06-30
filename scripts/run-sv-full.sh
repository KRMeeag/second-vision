#!/bin/bash
python hailo_apps/python/pipeline_apps/custom_depth_detection/sv_dual_pipeline.py --input rawusb:///dev/video0 --width 640 --height 360 --frame-rate 25
q