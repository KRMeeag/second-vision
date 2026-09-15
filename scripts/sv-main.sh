#!/bin/bash
# Second Vision — main entry point.
#
# The control panel lives on its own UART (uart3-pi5, GPIO8/9), NOT on
# /dev/serial0 — that one belongs to the motor-controller ESP32 on pins 6/8/10.
# Without --config-port the panel is silently ignored: the reader thread never
# starts and the rockers do nothing, with no error to explain why.
cd "$(dirname "$0")/.."

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
    echo "[sv-main] $INPUT does not exist — no camera attached."
    echo "[sv-main] Falling back to --mock so the panel can still be exercised."
    echo "[sv-main] Set INPUT=/dev/videoN to override."
    exec python3 src/second_vision/main.py --mock --config-port "$CONFIG_PORT"
fi

if [ ! -e "$CONFIG_PORT" ]; then
    echo "[sv-main] warning: $CONFIG_PORT missing — running without the control panel."
    exec python3 src/second_vision/main.py --input "$INPUT" \
        --width 640 --height 480 $FRAME_FLAG
fi

exec python3 src/second_vision/main.py --input "$INPUT" \
    --width 640 --height 480 $FRAME_FLAG --config-port "$CONFIG_PORT"
