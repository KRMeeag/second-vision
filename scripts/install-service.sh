#!/bin/bash
# Install (or remove) second-vision.service: the app starts when the Pi boots
# and stops cleanly when it shuts down — power button included.
#
#   ./scripts/install-service.sh               # install + enable (starts at next boot)
#   ./scripts/install-service.sh --now         # ...and (re)start it right away
#   ./scripts/install-service.sh --uninstall   # stop, disable and remove it
#
# Run it as your normal user, not with sudo: the service runs as whoever runs
# this, and the script calls sudo itself for the system-level steps. Re-run it
# after moving the repo — the unit holds absolute paths.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
UNIT="second-vision.service"
TEMPLATE="$REPO_ROOT/scripts/$UNIT"
TARGET="/etc/systemd/system/$UNIT"

if [[ $EUID -eq 0 ]]; then
    echo "ERROR: run this as your normal user, not root — it calls sudo itself." >&2
    exit 1
fi

MODE="${1:-}"
case "$MODE" in
    --uninstall)
        sudo systemctl disable --now "$UNIT" 2>/dev/null || true
        sudo rm -f "$TARGET"
        sudo systemctl daemon-reload
        echo "Removed $UNIT. Linger was left as it is (loginctl disable-linger to undo)."
        exit 0
        ;;
    ""|--now) ;;
    *)
        echo "usage: $0 [--now | --uninstall]" >&2
        exit 2
        ;;
esac

SV_USER="$(id -un)"
SV_UID="$(id -u)"

# --- Preflight: what the service needs but this script should not fix silently.
if [[ ! -x "$REPO_ROOT/my_hailo_env/bin/python3" ]]; then
    echo "ERROR: $REPO_ROOT/my_hailo_env is missing — create the venv first." >&2
    exit 1
fi
if ! systemctl cat hailort.service >/dev/null 2>&1; then
    echo "WARNING: hailort.service not found — the app will take the Hailo device" \
         "exclusively, and mode switches will contend for it." >&2
fi
for group in dialout video audio; do
    if ! id -nG "$SV_USER" | tr ' ' '\n' | grep -qx "$group"; then
        echo "WARNING: $SV_USER is not in the '$group' group — sudo usermod -aG $group $SV_USER" >&2
    fi
done

# Linger starts user@UID.service — and with it PipeWire, which TTS speaks
# through — at boot, whether or not anyone logs in.
if [[ "$(loginctl show-user "$SV_USER" -p Linger --value 2>/dev/null)" != "yes" ]]; then
    sudo loginctl enable-linger "$SV_USER"
    echo "Enabled linger for $SV_USER."
fi

# --- Render, check, install.
staging="$(mktemp -d)"
trap 'rm -rf "$staging"' EXIT
sed -e "s|@REPO@|$REPO_ROOT|g" -e "s|@USER@|$SV_USER|g" -e "s|@UID@|$SV_UID|g" \
    "$TEMPLATE" > "$staging/$UNIT"
if grep -q "@[A-Z]*@" "$staging/$UNIT"; then
    echo "ERROR: unfilled placeholder in the rendered unit:" >&2
    grep -n "@[A-Z]*@" "$staging/$UNIT" >&2
    exit 1
fi
systemd-analyze verify "$staging/$UNIT"

sudo install -m 644 "$staging/$UNIT" "$TARGET"
sudo systemctl daemon-reload
sudo systemctl enable "$UNIT"
if [[ "$MODE" == "--now" ]]; then
    sudo systemctl restart "$UNIT"
fi

cat <<EOF

Installed $TARGET (runs as $SV_USER from $REPO_ROOT).
It starts at every boot$([[ "$MODE" == "--now" ]] && echo " and is running now").

  sudo systemctl stop second-vision      stop it (do this before running the app by hand)
  sudo systemctl start second-vision     start it again
  systemctl status second-vision         is it running?
  journalctl -u second-vision -f         live log
  $0 --uninstall
EOF

# While the desktop runs, it takes the power key away from systemd-logind:
# one press only opens the shutdown dialog, a second press shuts down.
if systemd-inhibit --list --no-pager 2>/dev/null | grep -q "handle-power-key"; then
    cat <<'EOF'

NOTE: the desktop is holding the power button (pwrkey/pishutdown). One press
only opens an on-screen dialog; press it a SECOND time to shut down. On a
headless device nobody sees that dialog, so the wearer must double-press.
Either way the shutdown itself is clean: systemd stops this service first.
EOF
fi
