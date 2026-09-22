# Second Vision — ESP32 Control Panel (PlatformIO)

Firmware for the physical settings panel. Emits the line protocol parsed by
`src/second_vision/workers/config_reader.py`:

    V:panel:<n>        protocol version, boot only
    S:<key>:<value>    setting   e.g. S:motor_strength:0.60
    M:<mode>           pipeline mode: detection | depth | both | none
    B:<name>           event: status | alive

`PROTOCOL.md` is the authoritative contract. Where this README and that file
disagree, PROTOCOL.md wins.

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
| `motorlink`| `src/main.cpp`         | **A DIFFERENT BOARD** — see below. |

### `motorlink` is not the control panel

`src/main.cpp` targets the glasses / vibration-motor ESP32. It shares this
project only because both boards are `esp32dev` with the same toolchain;
`build_src_filter` compiles exactly one sketch per env.

It is a **link check, not motor firmware** — no PWM, no MOSFETs, no motor
logic. It waits for `0xAA` from the Pi and replies with the `ACK_PREFIX` that
`workers/serial_worker.py` matches. Binary, 115200, bidirectional — a different
link from the panel's text/9600/one-way one, driven by `--serial-port` rather
than `--config-port`.

`PROTOCOL.md` documents the **panel** protocol only and does not apply to it.

`default_envs = panel`, so a bare `pio run -t upload` can never flash this by
accident; you have to ask for `-e motorlink` explicitly.

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

| DETECT | DEPTH | emitted       |
|--------|-------|---------------|
| on     | on    | `M:both`      |
| on     | off   | `M:detection` |
| off    | on    | `M:depth`     |
| off    | off   | `M:none`      |

`M:none` means **run nothing** — not "run both and mute". A model with no route
to the user is wasted Hailo time and battery. `app.py` has a matching
`MODE_NONE` with an idle pipeline builder that keeps the camera running but
detaches both inference branches, so flipping a rocker back is near-instant
rather than paying a camera cold-start on top of the rebuild blackout.

## Pin map

| Control        | Pin     | Notes |
|----------------|---------|-------|
| DETECT rocker  | GPIO17  | latching, INPUT_PULLUP, active LOW |
| DEPTH rocker   | GPIO18  | latching, INPUT_PULLUP, active LOW |
| Status button  | GPIO22  | momentary, active LOW |
| B10K pot       | GPIO34  | ADC1, input-only, no pull-up |

GPIO19 and GPIO21 are free — the mode button and KY-004 are gone. Both wrote
settings the rockers now own, and two controls writing one setting is how a
panel ends up lying about its state.

**GPIO23 carries the Pi link wire** (UART2 TX) and is not free.

**Both rocker pins are chosen so a mis-plugged jumper cannot short the supply.**
GPIO17 and GPIO18 have plain GPIOs on either side (GPIO5/GPIO16 and
GPIO19/GPIO5), so a wire one hole off reads wrong rather than drawing current.
GPIO23 takes no *switch*: it is one row from the GND pin, on a row carrying 3V3
on the opposite side of the board, so a one-hole slip is a dead short across the
regulator — a hot module and garbage on every pin at once. A single soldered
link wire is acceptable there; a switch leg, which can bridge two rows, is not.

## Shared power, and the heartbeat

Panel and Pi are powered together by the X1202, so they boot together and the
Pi is ~30 s from opening the port. A one-shot report at boot would be lost,
leaving `SystemConfig` disagreeing with switches the user can see and feel.

So `control_panel.cpp` emits `B:alive` and then re-announces its whole state
every `HEARTBEAT_MS` (10 s), forever. That survives the boot race, a dropped
byte, and any restart of `main.py` while the panel stays powered — the common
case in development.

This is only safe because `config_reader.py` acts on **changes**: an
unconditional reader would call `trigger_rebuild()` on every heartbeat and tear
the Hailo pipeline down every 10 seconds. If you ever touch either side, that
invariant is the one to preserve.

## Serial

9600 baud, to match `_open_config_port()` on the Pi. The ESP32's ROM boot log
is 115200, so a burst of garbage on reset is expected and harmless.

At runtime the panel reaches the Pi over the GPIO UART, not USB — USB is only
for flashing, because the GPIO UART has no DTR/RTS to drive the bootloader.
Three wires, `docs/pi_link_wiring.svg`:

    X1202 5V     ->  ESP32 5V pin               power (XH2.54 header)
    X1202 GND    ->  ESP32 GND                  also the Pi's ground
    ESP32 GPIO23 ->  Pi pin 21  (GPIO9, uart3-pi5 RX)   NOT pin 10

**The link is pin 21, not pin 10.** The primary UART (`/dev/serial0`, pins
6/8/10) belongs to the motor-controller ESP32-C3 — verified by reading that
port, which carries its ROM banner. The panel gets a second UART instead:
`dtoverlay=uart3-pi5` in `config.txt`, GPIO8/GPIO9 on pins 24/21. GPIO9 is also
SPI0 MISO, so it is free only while `dtparam=spi` stays off.

**And the ESP32 end is GPIO23, not GPIO1.** GPIO1 is UART0 TX — the USB serial — and the
ROM bootloader dumps its 115200 log there on every reset, which would land
straight in `config_reader.py`. GPIO23 is UART2: the Pi never sees that garbage,
and USB stays usable for flashing and monitoring at the same time.

Power comes from the Geekworm X1202 UPS, not the Pi's header. The ESP32's own
`3V3` pin still feeds the breadboard `+` rail — the X1202's 5 V goes to the 5V
pin and nowhere else, so the pot keeps a safe 3.3 V reference. Because the X1202
feeds the Pi, its GND and the Pi's GND are one node — grounding the ESP32 there
already references it to the Pi, so only the signal wire touches the 40-pin
header. Unplug the 5 V feed before flashing over USB.

**Pin name depends on the board.** The 38-pin DevKit used here labels its 5 V
input `5V`; 30-pin boards label the same pin `VIN`. They are the regulator
input either way — never the `3V3` pin, which bypasses the regulator and puts
5 V straight on the module.

The Pi's serial login console was removed from `cmdline.txt` and
`enable_uart=1` added to `config.txt`. `/dev/serial0` (→ `ttyAMA0`) is the
**motor** link; `dtoverlay=uart3-pi5` adds the panel's own UART on GPIO8/9.

Confirm the device name after rebooting — Pi 5 numbering is not Pi 4's:

    ls -l /dev/ttyAMA*

then run the pipeline with `--config-port /dev/ttyAMA<n>` for whichever node
`uart3-pi5` created. Do **not** assume it; the primary already moved from
`ttyAMA10` to `ttyAMA0` once across a reboot in this project.
