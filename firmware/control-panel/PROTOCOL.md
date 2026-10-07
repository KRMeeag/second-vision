# Control panel wire protocol — v1

Frozen contract between `src/control_panel.cpp` (ESP32) and
`src/second_vision/workers/config_reader.py` (Raspberry Pi).

This file lives in `firmware/control-panel/` on purpose: it is the only document
that travels with the firmware when the folder is copied to another machine, so
the folder is self-describing wherever it lands.

---

## Transport

| Property | Value |
|---|---|
| Medium | UART, 3.3 V logic, no level shifter |
| Baud | **9600**, 8N1 |
| Direction | **One-way, ESP32 → Pi.** The panel never reads |
| Flow control | None |
| Terminator | `\n` (0x0A) only — no `\r` |
| Encoding | ASCII; lowercase keys and values |
| Max line | **64 bytes** (longest defined line is 22) |

### Two transports, one binary

Every line is emitted **identically and in the same order** on both:

| Path | Pins | Purpose |
|---|---|---|
| `Serial2` | ESP32 GPIO23 → Pi **pin 21** (GPIO9, `uart3-pi5`) | deployment |
| `Serial` | USB CDC | bench debugging, and testing on a laptop with no Pi |

No build flag, no mode switch. The Pi link is deliberately **not** UART0: UART0
is the USB serial, and the ROM bootloader dumps its 115200 log there on every
reset. On its own UART the Pi never sees that garbage — but a USB listener
still will, so receiver rule 1 stays mandatory.

---

## Line types

### `V:panel:<n>` — boot only

    V:panel:1

First line after reset. `1` is this protocol version.

### `S:<key>:<value>` — settings

| Line | Values |
|---|---|
| `S:tts_enabled:<v>` | `0` \| `1` |
| `S:vibration_enabled:<v>` | `0` \| `1` |
| `S:motor_strength:<v>` | `0.00`–`1.00`, **always 2 decimals** — STRENGTH knob, GPIO34 |
| `S:tts_volume:<v>` | `0.00`–`1.00`, **always 2 decimals** — VOLUME knob, GPIO33 |

The two knobs set how STRONG an output channel is, never whether it is on —
that stays with the rockers (`tts_enabled`, `vibration_enabled`). The Pi maps
`0.00` to the weakest output that is still felt / heard, not to silence.

### `M:<mode>` — pipeline mode

`M:detection` \| `M:depth` \| `M:both` \| `M:none`

Derived from the two rocker positions, never from a counter:

| DETECT | DEPTH | mode |
|---|---|---|
| on | on | `both` |
| on | off | `detection` |
| off | on | `depth` |
| off | off | `none` |

`M:none` means **run nothing** — not "run both and mute". A model with no route
to the user is wasted Hailo time and battery.

### `B:<name>` — events

| Line | Meaning |
|---|---|
| `B:alive` | heartbeat, every 10 s |
| `B:status` | **no longer sent** — see below |

The STATUS button was removed in October 2026: the volume knob took its place
on the panel, and GPIO22 is free. Firmware from before that still sends
`B:status` when the button is pressed, so receivers must keep tolerating it.
The Pi never acted on it beyond logging `[CONFIG] Button: status`.

---

## Emission triggers and ordering

**`M:` is always the LAST line of a state burst.** Treat it as a commit marker:
when it arrives, everything before it in that burst is current. Never act on a
partial burst.

**Boot** — rocker positions are read from hardware, never assumed:

    V:panel:1
    S:tts_enabled:<v>
    S:vibration_enabled:<v>
    S:motor_strength:<v>
    S:tts_volume:<v>
    M:<mode>

**Either rocker moves** — both flags are sent, so the burst is self-contained:

    S:tts_enabled:<v>
    S:vibration_enabled:<v>
    M:<mode>

**A knob moves** (deadbanded, >80 counts of 4095) — standalone, no `M:`:

    S:motor_strength:<v>        STRENGTH knob
    S:tts_volume:<v>            VOLUME knob

**Every 10 s** — liveness, then a full state burst:

    B:alive
    S:tts_enabled:<v>
    S:vibration_enabled:<v>
    S:motor_strength:<v>
    S:tts_volume:<v>
    M:<mode>

### Why the heartbeat carries state

Panel and Pi share the X1202 UPS, so they power up **together** and the Pi is
~30 s from opening the port. A one-shot boot report is lost every time, leaving
`SystemConfig` disagreeing with switches the user can see and feel — the exact
failure reading latching switches at boot is meant to prevent. No delay the
panel could reasonably sit through would close that gap.

The re-announce also covers a dropped byte and any restart of `main.py` while
the panel stays powered, which is the common case in development.

---

## Receiver rules — REQUIRED

1. **Discard unparseable lines silently.** The ROM bootloader's 115200 log
   arrives as garbage at 9600 on the USB path. Normal; must never raise.
2. **Ignore unknown `S:` keys.** A future panel may send keys v1 does not define.
3. **Ignore unknown `M:` values**, holding the last known-good mode.
4. **Never crash on a malformed line.** Reader-thread survival beats any single
   message.
5. **Treat missing `B:alive` as a fault.** Silence >30 s means the panel is not
   responding; say so rather than acting on stale settings indefinitely.
6. **Act only on CHANGES.** This one is load-bearing, not hygiene: the panel
   re-announces its whole state every 10 s, and a receiver that acted
   unconditionally would tear down and rebuild the Hailo pipeline every 10
   seconds, with a TTS announcement each time.

### Semantics

- **`tts_enabled` and `vibration_enabled` change autonomously.** They are
  consequences of rocker position, not button presses, and move at boot and on
  every flip. Any logic assuming they only change on explicit user action is
  wrong.
- **The panel is authoritative** for these five values. It reports physical
  reality; the Pi must not hold a contradicting internal state.

---

## Build options

| Env | Use | Difference |
|---|---|---|
| `panel` | deployment | everything present, BOTH pots |
| `panel_no_volume` | volume pot not soldered yet | `-D HAVE_VOLUME_POT=0` |
| `bench` | laptop, bare board, no pots | `-D HAVE_POT=0 -D HAVE_VOLUME_POT=0` |
| `polarity` | bring-up | separate sketch: raw pin states |

**`HAVE_POT=0` omits `S:motor_strength` entirely, and `HAVE_VOLUME_POT=0`
omits `S:tts_volume`** — from the boot burst, the heartbeat burst, and the
knob handler. Receivers must therefore not *require* either key; the Pi simply
keeps its existing `motor_strength` / `tts_volume` (1.0 unless told otherwise). Ordering is
unchanged and `M:` is still the commit marker, so this is a build option, not a
protocol variant, and the version stays 1.

Why it exists: GPIO34 is input-only with no internal pull-up, so an unfitted pot
floats and the 80-count deadband — sized for a wired pot's dither — is exceeded
constantly. Measured on a bare board: **~1,625 spurious `S:motor_strength` lines
in 30 s** (~54/s), enough to saturate a 9600 link on both transports. GPIO33
floats the same way even though it has internal pulls: `analogRead()` switches
them off on every call (Arduino-ESP32 2.0.17), so the flag is the only fix.

## Testing without a Raspberry Pi

    pip install pyserial
    python3 panel_monitor.py /dev/ttyUSB0     # or COM7 on Windows

`panel_monitor.py` ships beside this file and depends on nothing but pyserial —
no Hailo, no GStreamer, no repo checkout. It validates rather than tolerates:
`M:` ordering, unknown keys, value ranges and decimal format, version mismatch,
the `B:alive` window, and whether the mode agrees with the flags in its own
burst. Add `--raw` to see every byte as received.

---

## Versioning

`V:panel:<n>` at boot. Bump for:

- a new or removed message type
- a changed value range or format
- a changed ordering guarantee

Adding a new `S:` key does **not** require a bump — receivers already ignore
unknown keys (rule 2). Neither does adding an emission trigger, provided the
line format and ordering are unchanged; that is why the 10 s re-announce is
still v1.

The October 2026 volume knob is still v1 for the same reasons: `S:tts_volume` is
a new `S:` key, and dropping the STATUS button removes one emission trigger of
an existing message type (`B:`), whose receivers already tolerate its absence.
No message type, value range, format or ordering changed.

---

## Out of scope

- **Pi → ESP32 messages.** One-way by design; a reverse channel is a version bump.
- **The `B:status` spoken payload** — Pi-side policy, not protocol.
- **The motor-controller link** (binary, 115200, separate UART) — unrelated.
