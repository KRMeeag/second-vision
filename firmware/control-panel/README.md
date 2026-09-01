# Second Vision — ESP32 Control Panel (PlatformIO)

Firmware for the physical settings panel. Emits the line protocol parsed by
`src/second_vision/workers/config_reader.py`:

    S:<key>:<value>    setting   e.g. S:motor_strength:0.60
    M:<mode>           pipeline mode: detection | depth | both
    B:<name>           button event

Wiring diagrams:

| File | Covers | Regenerate with |
|------|--------|-----------------|
| `docs/breadboard_wiring.svg` | controls → ESP32, on the breadboard | `python3 docs/gen_wiring.py` |
| `docs/pi_link_wiring.svg`    | ESP32 → Raspberry Pi 5, over UART   | `python3 docs/gen_pi_link.py` |

Edit the board constants at the top of `gen_wiring.py` before re-running it.

## Environments

| Env        | File                   | Use |
|------------|------------------------|-----|
| `polarity` | `src/polarity_test.cpp`| Bring-up. Raw pin states + ADC + implied mode. |
| `panel`    | `src/control_panel.cpp`| The real firmware. Default env. |
| `bench`    | `src/control_panel.cpp`| Same firmware, `-D HAVE_POT=0`. For a bare board with no breadboard — an unfitted pot floats GPIO34 and floods the link. |

    pio run -e polarity -t upload -t monitor
    pio run -e panel    -t upload -t monitor
    pio run -e bench    -t upload -t monitor    # laptop, no breadboard

Upload is 115200: this DevKit fails at both 921600 and 460800 once esptool
switches to the high rate for the stub. Hold IO0 for the ENTIRE upload — the
auto-reset is unreliable.

## Controls

Two latching rockers, one per capability. Each owns a model **and** the output
channel that model feeds — a model with no route to the user is just heat.

| Rocker   | Model                  | Output channel              |
|----------|------------------------|-----------------------------|
| `DETECT` | YOLOv8 object detection| speech (`tts_enabled`)      |
| `DEPTH`  | SC-DepthV3 depth       | motors (`vibration_enabled`)|

The pair *is* the pipeline mode — there is no separate mode control, so the
panel can never show a position that is not what is running:

| DETECT | DEPTH | emitted        |
|--------|-------|----------------|
| on     | on    | `M:both`       |
| on     | off   | `M:detection`  |
| off    | on    | `M:depth`      |
| off    | off   | *no `M:` line* |

Both off emits no mode because `app.py` has only three and falls back to
`both` for anything else — sending a fourth would silently start everything
with both switches physically OFF. Nothing is announced instead: with
`tts_enabled` and `vibration_enabled` both 0, whichever models keep running
have no way to reach the user, so the panel reads OFF and the device is silent.

## Pin map

| Control        | Pin     | Notes |
|----------------|---------|-------|
| DETECT rocker  | GPIO17  | latching, INPUT_PULLUP, active LOW |
| DEPTH rocker   | GPIO18  | latching, INPUT_PULLUP, active LOW |
| Status button  | GPIO22  | momentary, active LOW |
| B10K pot       | GPIO34  | ADC1, input-only, no pull-up |

GPIO19, GPIO21 and GPIO23 are free — the mode button and KY-004 are gone. Both
wrote settings the rockers now own, and two controls writing one setting is how
a panel ends up lying about its state.

**Both rocker pins are chosen so a mis-plugged jumper cannot short the supply.**
GPIO17 and GPIO18 have plain GPIOs on either side (GPIO5/GPIO16 and
GPIO19/GPIO5), so a wire one hole off reads wrong rather than drawing current.
GPIO23 is deliberately avoided: it is one row from the GND pin, and that row
carries 3V3 on the opposite side of the board — a one-hole slip there is a dead
short across the regulator, which shows up as a hot module and garbage on every
pin at once.

## Shared power, and the heartbeat

Panel and Pi are powered together by the X1202, so they boot together and the
Pi is ~30 s from opening the port. A one-shot report at boot would be lost,
leaving `SystemConfig` disagreeing with switches the user can see and feel.

So `control_panel.cpp` re-announces its whole state every `ANNOUNCE_MS` (5 s),
forever. That survives the boot race, a dropped byte, and any restart of
`main.py` while the panel stays powered — the common case in development.

This is only safe because `config_reader.py` acts on **changes**: an
unconditional reader would call `trigger_rebuild()` on every heartbeat and tear
the Hailo pipeline down every 5 seconds. If you ever touch either side, that
invariant is the one to preserve.

## Serial

9600 baud, to match `_open_config_port()` on the Pi. The ESP32's ROM boot log
is 115200, so a burst of garbage on reset is expected and harmless.

At runtime the panel reaches the Pi over the GPIO UART, not USB — USB is only
for flashing, because the GPIO UART has no DTR/RTS to drive the bootloader.
Three wires, `docs/pi_link_wiring.svg`:

    X1202 5V         ->  ESP32 VIN              power (XH2.54 header)
    X1202 GND        ->  ESP32 GND              also the Pi's ground
    ESP32 TX0/GPIO1  ->  Pi pin 10  (GPIO15, RXD)

Power comes from the Geekworm X1202 UPS, not the Pi's header. The ESP32's own
`3V3` pin still feeds the breadboard `+` rail — the X1202's 5 V goes to VIN and
nowhere else, so the pot keeps a safe 3.3 V reference. Because the X1202
feeds the Pi, its GND and the Pi's GND are one node — grounding the ESP32 there
already references it to the Pi, so only the signal wire touches the 40-pin
header. Unplug VIN before flashing over USB.

The Pi's serial login console was removed from `cmdline.txt` and
`enable_uart=1` added to `config.txt`, so `/dev/serial0` (→ `ttyAMA0`) is free
for the panel. Run the pipeline with `--config-port /dev/serial0`.
