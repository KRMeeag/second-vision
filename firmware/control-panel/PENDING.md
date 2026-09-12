# PENDING — control panel

Live list. Items move out when they are done and verified on hardware, not when
the code is written. Feeds the next handoff.

---

## P1 — CLOSED: camera attached and running

An Arducam 1080P Low Light (uvcvideo) is on `/dev/video0`. The real pipeline
builds and runs both models on the Hailo-8 at ~29 FPS headless.

## P2 — CLOSED: MODE_NONE verified on hardware

Observed live:

    [CONFIG] Mode: none
    Generated Pipeline string (mode=none):  ... ! identity name=det_callback
                                            ! fakesink name=idle_sink sync=false
    [SWAP] -> none: first frame after 1.20s (OK, budget 3s)

The idle pipeline builds, keeps the camera up, drops both inference branches,
and the frame counter keeps moving — no watchdog stall. Returning to `depth`
works from there.

## P3 — CLOSED: the panel runs untethered on X1202 power

Verified 2026-09-09 with **no USB attached anywhere** — `lsusb` showed only the
camera, no CP2102:

    1. UART present            PASS   /dev/ttyAMA3, GPIO9 = RXD3
    2. Anything driving it     PASS   held HIGH against a forced pull-down
    3. Protocol arriving       PASS   12 lines, two clean heartbeat bursts

**What it took, after a long detour:**

1. **A real connector.** Female Dupont jumpers on XH2.54 blades passed the few
   mA an LED needs and collapsed under the ~200 mA the ESP32 draws. A soldered
   protoboard with a proper JST pigtail fixed the supply — measured by the Pi
   never flinching: no short, no undervoltage alarm, no sag.

2. **A `GPIO0` -> `3V3` jumper.** The DevKit's auto-reset transistors are driven
   from the CP2102's DTR/RTS. With USB unplugged that chip is unpowered and
   holds `IO0` low, so the ESP32 boots into DOWNLOAD MODE and waits for a flash
   command that never comes — powered, floating TX, silent. Pulling GPIO0 high
   forces normal boot.

**Neither alone was enough**, which is why this took so many attempts: a good
supply with no jumper gives a powered board that never boots, and a jumper on a
bad supply gives a board that never powers properly. The diagnostic that
separates them:

    sudo pinctrl set 9 pu; pinctrl get 9     # lo = unpowered
    sudo pinctrl set 9 pd; pinctrl get 9     # hi = powered AND driving

  lo / lo  unpowered — supply problem
  hi / lo  powered, floating — boot problem (this was the GPIO0 case)
  hi / hi  running

**THE ONE RULE:** remove the `GPIO0` jumper before flashing over USB. Left in,
it fights the auto-reset and you get `Wrong boot mode detected (0x13)`. Put it
back afterwards. A 10 kOhm resistor would remove even that chore, but it is not
required — the bare jumper is electrically identical while it is in place.

## Done, kept here until the next handoff goes out

- `--config-port` registered — it was referenced but never added to any parser,
  so the panel reader never started
- `serial_worker` no longer prints `TEST SUCCESS` when no port was opened
- `config_reader` stubs implemented; `config.udpate` typo; `vibration_enabled`
  added to `bool_keys` (was stored as the string `"0"`, which is truthy, so the
  motors would not stop with the DEPTH rocker off)
- `MODE_NONE` in `app.py`, so both rockers off runs nothing
- Panel link verified on hardware: `check_link.sh` passes all three stages on
  `/dev/ttyAMA3`
