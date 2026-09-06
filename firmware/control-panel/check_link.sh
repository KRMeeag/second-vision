#!/usr/bin/env bash
# Second Vision — control-panel link check (Pi side).
#
# Answers, in order, the only three questions that matter:
#   1. Is the Pi's UART actually there?
#   2. Is anything electrically driving the wire?
#   3. Are protocol lines arriving?
#
# Stops at the first failure, because every later test depends on the earlier
# ones passing. Re-run after any change.
#
#   ./check_link.sh              # default /dev/ttyAMA3, 12s listen
#   ./check_link.sh /dev/ttyAMA3 25

DEV="${1:-/dev/ttyAMA3}"
SECS="${2:-12}"
RXPIN=9                     # GPIO9 = Pi header pin 21 = uart3-pi5 RX

bold=$'\e[1m'; red=$'\e[31m'; grn=$'\e[32m'; yel=$'\e[33m'; off=$'\e[0m'
pass() { echo "  ${grn}PASS${off}  $*"; }
fail() { echo "  ${red}FAIL${off}  $*"; }
info() { echo "        $*"; }

echo "${bold}1. Is the UART present?${off}"
if [ ! -e "$DEV" ]; then
    fail "$DEV does not exist"
    info "dtoverlay=uart3-pi5 missing from /boot/firmware/config.txt, or not rebooted."
    exit 1
fi
FUNC=$(pinctrl get $RXPIN)
if ! grep -q "RXD3" <<<"$FUNC"; then
    fail "GPIO$RXPIN is not muxed to the UART:  $FUNC"
    info "Expected 'a2 ... RXD3'. The overlay did not take effect."
    exit 1
fi
pass "$DEV exists and GPIO$RXPIN is RXD3 (header pin 21)"

echo
echo "${bold}2. Is anything driving the wire?${off}"
# A pull-up reads high whether or not a device is attached, so it proves
# nothing. Force a pull-DOWN: a powered ESP32 idles its TX HIGH and will
# override it. If the line follows the Pi's own resistor, nothing is there.
if ! sudo -n true 2>/dev/null; then
    echo "  ${yel}SKIP${off}  needs sudo for 'pinctrl set'; run with sudo to include this test"
else
    sudo pinctrl set $RXPIN pd; sleep 0.4
    LEVEL=$(pinctrl get $RXPIN | grep -o '| *\(hi\|lo\)' | tr -d '| ')
    sudo pinctrl set $RXPIN pu
    if [ "$LEVEL" = "hi" ]; then
        pass "line held HIGH against a pull-down — the ESP32 is powered and connected"
    else
        fail "line followed the pull-down — nothing is driving GPIO$RXPIN"
        info ""
        info "Check these in order; each is cheaper than the next:"
        info ""
        info "  1. POWER. Is the board's red LED lit? Ground alone does not power it."
        info "     Note: a USB cable in a charger powers the board but gives the Pi"
        info "     no /dev/ttyUSB* — check with 'ls /dev/ttyUSB*' to tell which."
        info ""
        info "  2. FIRMWARE. Only panel and bench transmit on GPIO23; polarity and"
        info "     factory firmware do not. Plug USB into the PI and reflash:"
        info "       pio run -e bench -t upload -t monitor      (hold IO0 throughout)"
        info "     Protocol lines on /dev/ttyUSB0 prove power and firmware are fine."
        info ""
        info "  3. THE WIRE. If USB shows lines but this test still fails, the yellow"
        info "     wire is not on GPIO23. P22 sits beside it and would be silent."
        exit 1
    fi
fi

echo
echo "${bold}3. Are protocol lines arriving?  (${SECS}s — one heartbeat is 10s)${off}"
python3 - "$DEV" "$SECS" <<'PY'
import sys, time
try:
    import serial
except ImportError:
    sys.exit("  pyserial missing — activate my_hailo_env first")
dev, secs = sys.argv[1], float(sys.argv[2])
p = serial.Serial(dev, 9600, timeout=1)
end, lines, junk = time.time() + secs, [], 0
while time.time() < end:
    raw = p.readline()
    if not raw:
        continue
    t = raw.decode(errors="ignore").strip()
    if not t:
        continue
    (lines if t.split(":")[0] in ("V", "S", "M", "B") else [junk]) and None
    if t.split(":")[0] in ("V", "S", "M", "B"):
        lines.append(t)
    else:
        junk += 1
p.close()
G, R, O = "\033[32m", "\033[31m", "\033[0m"
if lines:
    print(f"  {G}PASS{O}  {len(lines)} protocol lines" + (f", {junk} discarded" if junk else ""))
    for t in lines[:8]:
        print(f"        {t}")
    print("\n  Link works. Now run:  python3 panel_monitor.py " + dev)
    print("  Flip a rocker — a changed value is marked with an arrow.")
elif junk:
    print(f"  {R}FAIL{O}  {junk} lines of non-protocol data — wrong baud, or the wire")
    print("        is picking up a different device.")
else:
    print(f"  {R}FAIL{O}  silence. The line is driven but nothing parses as protocol.")
    print("        Check which firmware is flashed: only panel and bench transmit.")
PY
