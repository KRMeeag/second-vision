#!/bin/bash
# Second Vision — main entry point.
#
# The control panel lives on its own UART (uart3-pi5, GPIO8/9), NOT on
# /dev/serial0 — that one belongs to the motor-controller ESP32 on pins 6/8/10.
# Without --config-port the panel is silently ignored: the reader thread never
# starts and the rockers do nothing, with no error to explain why.
#
# This is also what second-vision.service runs at boot (scripts/install-service.sh).
# The service sets SV_SERVICE=1, which run.sh reads (it refuses to start a second
# copy while the service is active, unless this is set); this script does not.
#
# Mock mode is NEVER started from here, with or without SV_SERVICE, camera or no
# camera (decision D-13): it announces objects that are not there and drives the
# motors from fake depth, on a device nobody is watching. Ask for it on purpose,
# by running run.sh --mock yourself. With no camera attached the program is
# started anyway: it runs the glasses link, the panel and the speech from the
# first second, keeps looking for a USB camera and starts vision when one
# appears. So there is no wait for the camera here either; a camera that is
# still enumerating is found by the program's own look, every 2 s.
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

# run.sh activates my_hailo_env and puts src/ on PYTHONPATH before exec'ing
# main.py. Calling python3 directly skipped all of that: from a shell without
# the venv active there is no hailo_apps, and main.py refuses to start.
# SV_RUN_SH replaces it for a test that records the arguments with a stub; unset
# (always, in service and by hand) it is the line it was.
RUN="${SV_RUN_SH:-$REPO_ROOT/scripts/run.sh}"

# Two separate ESP32 boards, two separate ports — not competing values for one
# setting. /dev/serial0 is the haptics/BMI160 (motor) board, confirmed working
# across today's real hardware tests; CONFIG_PORT is the control-panel board
# wired in over uart3 (see the file-top comment).
SERIAL_PORT="${SERIAL_PORT:-/dev/serial0}"
CONFIG_PORT="${CONFIG_PORT:-/dev/ttyAMA3}"
INPUT="${INPUT:-/dev/video0}"

# --use-frame hands every frame to the callback for a cv2 preview window. Over
# SSH there is no DISPLAY to draw on, so nothing drains that queue: it fills,
# every frame is dropped, and throughput halves — "Frame queue is full" on
# repeat plus a Qt xcb error. Only ask for frames when something can show them.
FRAME_FLAG=""
if [ -n "$DISPLAY" ] || [ -n "$WAYLAND_DISPLAY" ]; then
    FRAME_FLAG="--use-frame"
else
    echo "[sv-main] headless (no DISPLAY) — preview disabled; depth, haptics and TTS still run."
fi

if [ ! -e "$INPUT" ]; then
    echo "[sv-main] $INPUT does not exist - no camera attached (yet). Starting anyway:"
    echo "[sv-main] the program keeps looking for a USB camera and starts vision when one appears."
    echo "[sv-main] Set INPUT=/dev/videoN to use another node."
fi

if [ ! -e "$CONFIG_PORT" ]; then
    echo "[sv-main] warning: $CONFIG_PORT missing — running without the control panel."
    exec "$RUN" --input "$INPUT" \
        --width 640 --height 480 $FRAME_FLAG --serial-port "$SERIAL_PORT"
fi

exec "$RUN" --input "$INPUT" \
    --width 640 --height 480 $FRAME_FLAG --serial-port "$SERIAL_PORT" --config-port "$CONFIG_PORT"
