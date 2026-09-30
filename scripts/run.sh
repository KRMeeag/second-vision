#!/bin/bash
# Second Vision launcher — the entry point referenced throughout documentation/.
#
# Handles the three environment steps main.py needs, then passes every argument
# straight through:
#   1. activate my_hailo_env       (hailo_apps lives here)
#   2. source the Hailo env        (HEF/resource paths)
#   3. put src/ on PYTHONPATH      (so `second_vision.*` imports resolve)
#
# Usage:
#   ./scripts/run.sh --mock                                  # no hardware needed
#   ./scripts/run.sh --input usb                             # real pipeline
#   ./scripts/run.sh --input /dev/video0 --width 640 --height 480 --use-frame
#
# NOTE ON FLAGS: --mock, --serial-port, --serial-baud and --config-port are
# handled by main.py's pre-parser (so they do not show in --help); everything
# else is parsed by the hailo_apps pipeline parser (--input, --width, --height,
# --use-frame, --show-fps, --disable-sync, ...) plus app.py's --labels-json /
# --det-hef-path. --headless and --debug-display are documented as planned but
# are NOT registered yet — passing them will fail argparse. See
# documentation/TASKS.md.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

# While the boot service runs it owns the camera and both UARTs. A second copy
# started by hand fights it for them — two writers on the motor UART means two
# streams of vibration commands — so refuse, and say how to free them.
# SV_SERVICE=1 marks the service's own launch (scripts/second-vision.service).
if [[ "${SV_SERVICE:-0}" != "1" ]] && command -v systemctl >/dev/null 2>&1 \
        && systemctl is-active --quiet second-vision.service 2>/dev/null; then
    echo "ERROR: second-vision.service (the boot service) is running and owns the camera and serial ports." >&2
    echo "       Stop it first:  sudo systemctl stop second-vision" >&2
    echo "       It starts again at the next boot, or now with: sudo systemctl start second-vision" >&2
    exit 1
fi

VENV_ACTIVATE="$REPO_ROOT/my_hailo_env/bin/activate"
HAILO_ENV="/usr/local/hailo/resources/.env"

if [[ ! -f "$VENV_ACTIVATE" ]]; then
    echo "ERROR: virtualenv not found at $VENV_ACTIVATE" >&2
    exit 1
fi

# shellcheck disable=SC1090
source "$VENV_ACTIVATE"

# Not fatal when missing: --mock needs no Hailo resources at all.
if [[ -f "$HAILO_ENV" ]]; then
    # shellcheck disable=SC1090
    source "$HAILO_ENV"
else
    echo "WARNING: Hailo env not found at $HAILO_ENV — only --mock will work" >&2
fi

export PYTHONPATH="$REPO_ROOT/src${PYTHONPATH:+:$PYTHONPATH}"

exec python3 src/second_vision/main.py "$@"
