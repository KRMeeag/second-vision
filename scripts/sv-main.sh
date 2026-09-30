#!/bin/bash
# Second Vision — main entry point.
#
# The control panel lives on its own UART (uart3-pi5, GPIO8/9), NOT on
# /dev/serial0 — that one belongs to the motor-controller ESP32 on pins 6/8/10.
# Without --config-port the panel is silently ignored: the reader thread never
# starts and the rockers do nothing, with no error to explain why.
#
# This is also what second-vision.service runs at boot (scripts/install-service.sh).
# The service sets SV_SERVICE=1, which turns the desk-friendly fallbacks below
# into waits and hard failures.
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

# run.sh activates my_hailo_env and puts src/ on PYTHONPATH before exec'ing
# main.py. Calling python3 directly skipped all of that: from a shell without
# the venv active there is no hailo_apps, and main.py quietly runs mock mode.
RUN="$REPO_ROOT/scripts/run.sh"

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

if [ "${SV_SERVICE:-0}" = "1" ]; then
    # Nobody watches the console on the wearer, so never fall back to mock:
    # it announces objects that are not there and drives the motors from fake
    # depth. Fail instead and let systemd retry (Restart=always).
    #
    # At boot the USB camera can enumerate after we start — give it a minute.
    CAMERA_WAIT_SECONDS="${CAMERA_WAIT_SECONDS:-60}"
    waited=0
    while [ ! -e "$INPUT" ]; do
        if [ "$waited" -eq 0 ]; then
            echo "[sv-main] waiting up to ${CAMERA_WAIT_SECONDS}s for $INPUT ..."
        fi
        if [ "$waited" -ge "$CAMERA_WAIT_SECONDS" ]; then
            echo "[sv-main] ERROR: $INPUT never appeared — is the camera plugged in? systemd will retry." >&2
            exit 1
        fi
        sleep 1
        waited=$((waited + 1))
    done

    # main.py treats an unimportable hailo_apps as "no hardware here" and runs
    # mock mode unasked. Check with the interpreter run.sh is about to use.
    if ! "$REPO_ROOT/my_hailo_env/bin/python3" -c \
            "import hailo_apps.python.core.common.hailo_logger, hailo_apps.python.core.gstreamer.gstreamer_app"; then
        echo "[sv-main] ERROR: hailo_apps does not import from my_hailo_env — refusing to start" \
             "(main.py would silently run MOCK mode)." >&2
        exit 1
    fi
fi

if [ ! -e "$INPUT" ]; then
    echo "[sv-main] $INPUT does not exist — no camera attached."
    echo "[sv-main] Falling back to --mock so the panel can still be exercised."
    echo "[sv-main] Set INPUT=/dev/videoN to override."
    exec "$RUN" --mock --serial-port "$SERIAL_PORT" --config-port "$CONFIG_PORT"
fi

if [ ! -e "$CONFIG_PORT" ]; then
    echo "[sv-main] warning: $CONFIG_PORT missing — running without the control panel."
    exec "$RUN" --input "$INPUT" \
        --width 640 --height 480 $FRAME_FLAG --serial-port "$SERIAL_PORT"
fi

exec "$RUN" --input "$INPUT" \
    --width 640 --height 480 $FRAME_FLAG --serial-port "$SERIAL_PORT" --config-port "$CONFIG_PORT"
