#!/usr/bin/env bash
# Live level on Pi pin 21 (GPIO9). Tests the WIRE, independent of firmware.
#
# With the yellow wire's ESP32 end moved by hand:
#
#   touch it to the ESP32's GND  -> should read LOW
#   touch it to the ESP32's 3V3  -> should read HIGH
#
# If both work, the wire is good AND it lands on Pi pin 21. Any remaining
# fault is then the ESP32 not transmitting: wrong firmware, or the wire is on
# the wrong ESP32 pin.
#
# If neither changes anything, the wire is broken, not seated, or not on pin 21.
#
# Ctrl-C to stop.

echo "Pin 21 (GPIO9) live level — pull-DOWN forced so an external HIGH is visible."
echo "Move the yellow wire's ESP32 end between GND and 3V3 and watch."
echo
sudo pinctrl set 9 pd 2>/dev/null || { echo "needs sudo"; exit 1; }
trap 'sudo pinctrl set 9 pu; echo; echo "restored pull-up"; exit 0' INT
prev=""
while true; do
    lvl=$(pinctrl get 9 | grep -o '| *\(hi\|lo\)' | tr -d '| ')
    if [ "$lvl" != "$prev" ]; then
        if [ "$lvl" = "hi" ]; then
            echo "  $(date +%H:%M:%S)  HIGH  <- something is driving it (3V3, or a live TX)"
        else
            echo "  $(date +%H:%M:%S)  low   <- nothing driving it (or tied to GND)"
        fi
        prev="$lvl"
    fi
    sleep 0.15
done
