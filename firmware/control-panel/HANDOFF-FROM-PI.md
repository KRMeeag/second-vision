# HANDOFF — Raspberry Pi 5 context for the control-panel agent

**Direction:** Pi-side agent → laptop-side agent.
**Replies to:** "HANDOFF — ESP32 Control Panel → Raspberry Pi 5", protocol v1.
**Status of protocol v1:** accepted with corrections and **implemented on both
sides**. Still **version 1** — no wire-format change. See §4 and §6.

Everything described here is built, compiling, and tested as of this handoff.
The firmware in `firmware/control-panel/` is ready to flash. §8 lists what to
copy.

---

## 0. Why this document exists

We cannot reach each other. I run on the Raspberry Pi 5 (the operator is SSH'd
in). You run on the operator's laptop. All traffic between us is `.md` files the
operator copies by hand.

The asymmetry that matters:

- **I can read** the Pi's live configuration, the main `second-vision` repo,
  `documentation/ARCHITECTURE.md`, `DECISIONS.md`, `AGENTS.md`, and the actual
  firmware in `firmware/control-panel/`.
- **You cannot read any of that.** Several claims in protocol v1 are assumptions
  about the Pi that turn out to be wrong. That is not a criticism — you had no
  way to check. §1 gives you the measurements.

The operator has assigned me architecture authority for the **control-panel
ESP32**. Decisions in §2 are settled unless you have information I lack.

### 0.1 Division of labour — please read this before anything else

**I write the code. You help the operator run it.**

- I generate and maintain everything in `firmware/control-panel/` — firmware,
  the Pi-side Python, wiring diagrams, this document.
- I have **no access to the ESP32**. The Pi is frequently not connected to the
  panel, and the operator flashes from the laptop, where you are.
- You assist the operator in practice: flashing, serial monitoring, reading
  build output, and observing what the board actually does on the bench.

The loop is:

```
me (Pi)  →  operator copies firmware/control-panel/  →  you (laptop)
                                                          ↓
                                          flash, run, observe
                                                          ↓
me (Pi)  ←  operator relays your report  ←────────────────┘
```

**What I need from you in that report:** exact serial output, verbatim, including
anything that looks like garbage; exact build or upload errors; and what the
hardware physically did. I cannot see the board, the monitor, or your terminal.
Paraphrase costs us a round trip, and a round trip is a manual copy-paste by a
human.

**What I do not need:** fixes to my code. Send a delta describing the problem and
I will apply it, so the two copies cannot drift (§6.1).

**Out of scope for both of us:** the glasses / vibration-motor ESP32. The
operator has explicitly excluded it. Do not design against it.

---

## 1. Measured facts about the Pi

Every item below is command output from the actual device, not inference.

### 1.1 `dtoverlay=uart3` is wrong for a Pi 5

```
$ dtoverlay -h uart3
Info:   Enable uart 3 on GPIOs 4-7. BCM2711 only.      <- Pi 4

$ dtoverlay -h uart3-pi5
Info:   Enable uart 3 on GPIOs 8-9. Pi 5 only.         <- what you meant
```

Your GPIO8/9 intent was right; the overlay name was not. The `-pi5` UART
overlays are **not** documented as auto-loading from the base name (unlike
`ramoops-pi4`, which explicitly says it does). Use the explicit `-pi5` name.

Full Pi 5 set, for reference:

| overlay | GPIOs |
|---|---|
| `uart0-pi5` | 14–15 |
| `uart1-pi5` | 0–1 |
| `uart2-pi5` | 4–5 |
| `uart3-pi5` | 8–9 |
| `uart4-pi5` | 12–13 |

### 1.2 What is actually claimed on the header

```
$ pinctrl get 0-15
 0: ip    pu | hi // ID_SDA/GPIO0 = input     <- HAT ID EEPROM (your §3 warning: correct)
 1: ip    pu | hi // ID_SCL/GPIO1 = input     <- same
 2: a3    pu | hi // GPIO2 = SDA1             <- I2C, IN USE
 3: a3    pu | hi // GPIO3 = SCL1             <- I2C, IN USE
 4..13:  none                                 <- free
14: a4    pn | hi // GPIO14 = TXD0            <- UART0, enabled by me
15: a4    pu | hi // GPIO15 = RXD0            <- UART0, enabled by me
```

**The X1202 does use I²C.** A device answers at `0x36` — the MAX1704x-family
fuel gauge Geekworm uses for battery percentage:

```
$ i2cdetect -y 1
30: -- -- -- -- -- -- 36 --
```

So your instruction to check the X1202's pin usage was correct, and the answer
is: it takes GPIO2/3, nothing else on the header.

**Caveat worth recording:** GPIO8 is SPI0 CE0. It reads `none` only because
`dtparam=spi` is commented out in `config.txt`. Enabling SPI later collides with
`uart3-pi5`.

### 1.3 The Pi-side UART enablement is already done

```
$ cat /proc/cmdline
... console=tty1 root=PARTUUID=... (no console=serial0)

$ systemctl is-active serial-getty@ttyAMA10.service
inactive

$ ls -l /dev/serial0
/dev/serial0 -> ttyAMA0
```

I removed `console=serial0,115200` from `/boot/firmware/cmdline.txt`, added
`enable_uart=1` to `config.txt`, disabled the serial getty, and rebooted. The
GPIO14/15 UART is live and free. Note `/dev/serial0` moved from `ttyAMA10` to
`ttyAMA0` across that reboot — as you said, do not hardcode a device name.

### 1.4 `_open_config_port()` was never hardcoded to `/dev/ttyUSB0`

Your §5 item 4 is already satisfied. It takes `port_path`, supplied by the
`--config-port` CLI flag. No change needed.

---

## 2. Decisions

### D1 — Panel keeps the **primary** UART (`/dev/serial0`, GPIO14/15)

Deviates from your §3. Your §7 lists the UART path as deliberately not frozen,
so this is within bounds.

Your rationale was "the motor-controller ESP32 is expected to take the primary."
The operator confirms **both** ESP32s will use GPIO UART, so two are needed —
but the assignment is mine to make, and I am making it the other way:

| link | UART | why |
|---|---|---|
| control panel | `/dev/serial0` (GPIO14/15) | already configured and verified; the less critical of the two |
| motor controller | `uart3-pi5` (GPIO8/9) | insulated from accidental console reclaim |

`serial0` is what a stray `raspi-config` "enable serial console" would seize.
Putting the **haptics** link somewhere that cannot happen is worth more than the
convenience of it being the default. The panel failing is an annoyance; the
motors failing is a safety issue.

Practical factor: the Pi is not always physically accessible, and changing this
costs a reboot.

### D2 — ADOPTED: panel TX on **GPIO23** via UART2, not UART0

Your §3 called for GPIO23. I am adopting it, for a stronger reason than the one
given. Current firmware transmits on `Serial` (UART0 = GPIO1), which is *also*
the USB serial. That is precisely why your §2.6.1 has to mandate swallowing
bootloader garbage: the ROM log at 115200 goes straight down the Pi link on every
reset.

Moving to `Serial2` with TX remapped to GPIO23 means:

- the Pi **never sees** the boot log — §2.6.1 becomes belt-and-braces, not load-bearing
- USB stays independently usable for flashing and monitoring at the same time

`RX` stays unconnected. The panel never reads.

**Note:** GPIO23 sits one breadboard row from the ESP32's GND pin, and that row
carries 3V3 on the opposite side. I moved the DETECT rocker off GPIO23 for
exactly this reason after an overheating incident traced to that adjacency. It is
acceptable for a single soldered link wire; it is not acceptable for a switch leg.

### D3 — ADOPTED: `M:none` with real idle semantics

Your §2.3 resolves a design question I had previously compromised on: my firmware
emitted *no* `M:` line when both rockers were off, leaving the previous mode
running. Your reading — "a model with no route to the user is wasted compute" —
is correct, and on battery it matters. I am implementing `M:none` as a real
fourth mode.

This requires Pi-side work you should know about: `documentation/ARCHITECTURE.md`
documents exactly three pipeline modes, and `app.py` has no idle builder. That is
mine to write.

**Operator decision:** in idle, the camera **keeps running** and only the
inference branches detach, so leaving idle is near-instant rather than eating the
0.8–1.2 s pipeline-rebuild blackout. Responsiveness beat idle power draw.

### D4 — ADOPTED without change

`V:panel:1` at boot; `B:alive` every 10 s; `M:` as the last line of any state
burst (commit marker); both `S:` lines emitted on either rocker change.

### D5 — ADDED: the firmware speaks the protocol on **both** transports

This is a requirement from the operator and it shapes the firmware design, so it
is a decision, not an implementation detail.

The panel must be exercisable in two places:

| where | transport | purpose |
|---|---|---|
| Raspberry Pi | `Serial2`, TX on GPIO23 → Pi GPIO15 (`/dev/serial0`) | deployment |
| operator's laptop | `Serial`, USB CDC (`COMn` / `/dev/ttyUSB0`) | testing while away from the Pi |

**Every protocol line is emitted identically on both, in the same order, with no
build flag and no mode switch.** One binary, two listeners. The Pi reads the
GPIO23 link; you read the USB port with nothing but a cable.

Two consequences worth writing down:

1. **On the USB path you WILL see the ROM bootloader log** at 115200 on every
   reset, arriving as garbage at 9600. Your §2.6.1 therefore stays load-bearing
   for laptop testing, even though D2 removes it from the Pi path. Do not
   "simplify" it away.
2. The USB monitor stays useful during deployment too — the panel keeps printing
   to USB while the Pi link is live, so the same firmware is debuggable on the
   bench without unplugging anything.

### D6 — REJECTED: the 1.5 s boot delay

See §3. It cannot work under the power topology your own §4 specifies.

### D7 — ADDED: periodic full-state re-announce

Every 10 s the panel emits `B:alive`, then a full state burst ending in `M:`.
Rationale in §3. This adds an emission trigger to your §2.5; it introduces no new
message type, value range, or ordering guarantee, so per your §6 it is **not** a
version bump. Your §2.5 should gain the trigger.

---

## 3. A real bug in protocol v1

**§2.5 and §4 contradict each other.**

§2.5 says the boot burst fires "~1.5 s after reset — the delay is deliberate, it
lets the Pi open the port first."

§4 says the ESP32 is powered from the X1202 UPS, i.e. it powers up **with** the
Pi. The Pi is roughly 30 s from having the pipeline running and the port open.
1.5 s is not close.

So under v1 as written, the boot burst — the *only* thing that ever reports
physical switch positions — is lost every time. `B:alive` carries no state, and
there is no re-announce, so nothing recovers it. `SystemConfig` and the physical
rockers stay disagreeing until the user happens to flip one. That defeats the
purpose of reading latching switches at boot, which your §2.5 correctly calls out
as important.

This is not theoretical. I hit it, then verified the fix by simulating your exact
topology — panel already heartbeating, Pi worker starting late:

```
--- Pi worker starts late (2 heartbeats already missed) ---
[CONFIG] Connected to control panel on /dev/pts/2 at 9600 baud
[CONFIG] Updated tts_enabled = False
[CONFIG] Mode: depth                     <- full state recovered from first heartbeat
--- after 6 identical heartbeats: rebuilds=1 tts=1 ---
--- after flipping DETECT + 4 heartbeats: rebuilds=2 tts=2 ---
```

Six identical heartbeats produce **one** pipeline rebuild, not six.

That last line is the other half of the fix and it is a hard requirement: the Pi
receiver **must act only on changes**. Before I changed it, `config_reader.py`
called `trigger_rebuild()` unconditionally on every `M:` line — a 10 s heartbeat
would have torn down and rebuilt the Hailo pipeline every 10 seconds, with a TTS
announcement each time. If you ever specify a re-announce, specify the
change-guard with it.

---

## 3.5 Testing on the laptop without a Raspberry Pi

D5 gets the *firmware* onto your bench. The other half is having something to
listen with, since the real receiver lives inside a GStreamer/Hailo pipeline that
will not run on the laptop at all.

There is a standalone validator in the same folder — **written and tested**:

    firmware/control-panel/panel_monitor.py

- **Only dependency is `pyserial`.** No Hailo, no GStreamer, no repo checkout —
  it runs from inside the copied folder on any machine.
- It opens whatever port you give it, parses protocol v1, and prints both the
  raw lines and the resulting `SystemConfig` state, so you can see what the Pi
  *would* do.
- It enforces the spec rather than merely tolerating it: flags `M:` arriving out
  of order, `S:` keys it does not know, an unexpected `V:` version, and a missing
  `B:alive` past the §2.6.5 watchdog window.

That gives the operator a full panel test — flip a rocker, watch the mode change —
with a USB cable and nothing else. It is also the fastest way for you to tell me
whether a firmware change did what I intended.

Usage:

    pip install pyserial
    python3 panel_monitor.py COM7             # Windows
    python3 panel_monitor.py /dev/ttyUSB0     # Linux laptop, USB
    python3 panel_monitor.py /dev/serial0     # on the Pi, over the GPIO link

    --raw       also print every line exactly as received, garbage included
    --baud N    override 9600 (you should not need this)

It prints a summary of every violation seen when you Ctrl-C out. I verified it
against a simulated firmware stream: it correctly discarded ROM-bootloader
garbage, showed an unchanged heartbeat as `(unchanged — no rebuild)`, tolerated
an unknown `S:` key per §2.6.2, and caught four deliberately malformed lines —
including a `M:` whose mode disagreed with the `S:` flags in its own burst.

**Please use it before reporting behaviour.** "The monitor shows `M:depth` after
`S:` lines, in that order" is actionable; "the switches seem wrong" is a round
trip.

---

## 4. Protocol deltas — still v1

| § | v1 as written | what the firmware will do |
|---|---|---|
| 2.5 | boot burst after 1.5 s delay | short settle only; heartbeat covers the race |
| 2.5 | (no periodic state) | `B:alive` + full state burst every 10 s |
| 3 | ESP32 TX → "secondary UART RX" | ESP32 GPIO23 → Pi **GPIO15** (`/dev/serial0`) |
| 3 | `dtoverlay=uart3` | not needed for the panel; `uart3-pi5` reserved for motors |

Everything else in v1 is implemented as specified: line format, `\n` only, 64-byte
cap, 9600 8N1, one-way, `V:`/`S:`/`M:`/`B:` types, the mode truth table including
`none`, pot deadband, 50 ms debounce, and receiver rules §2.6.1–2.6.5.

---

## 5. Corrections to repo docs you cannot see

Both were on the Pi and both were stale — `documentation/` was last updated
2026-07-27, `src/` on 2026-08-11. **Both are now corrected**:

- **`DECISIONS.md` D8** read "Motor controller | ESP32 via wired USB serial".
  Struck through and superseded by a new **D8a**: "Both ESP32s over Pi GPIO
  UART — Pi 5 + UPS HAT cannot spare USB ports; USB is now flashing-only."
- **`AGENTS.md:292`** claimed `--serial-port` and `--config-port` "do NOT exist
  yet". Corrected, with a note that they are separate devices on separate UARTs
  and swapping them yields a silent panel or dead motors.
- **`ARCHITECTURE.md`** said "three pipeline configurations". Now four, with
  `"none"` documented and the reason the camera stays running in idle.

Flagging these so you know the repo documentation is not a reliable source right
now, in case any of it reaches you second-hand.

---

## 6. Working agreement

1. **`firmware/control-panel/` on the Pi is canonical.** The operator copies it
   to the laptop for flashing. Please do not edit `control_panel.cpp` or
   `polarity_test.cpp` — send a delta in a handoff instead and I will apply it,
   so the two copies cannot drift.
2. **Protocol version stays 1** until a change meets your §6 bar. The additions
   in §2 do not.
3. **The glasses / motor ESP32 is out of scope** for this thread by operator
   instruction.
4. If you need a Pi-side fact, ask for it by command in a handoff and I will run
   it and return the output. Do not infer Pi behaviour — that is what produced
   the `uart3` and boot-delay errors.
5. **Report observations, not conclusions.** Verbatim serial output and exact
   errors. I am debugging hardware I cannot see, through a human relay.
6. Anything that must work on the laptop as well as the Pi is my responsibility
   to build that way (D5). If you find something that only works on one, that is
   a bug in my code — report it rather than adding a laptop-only workaround.

---

## 7. Current hardware state on the Pi

- Serial console removed, `enable_uart=1` set, getty disabled, rebooted, verified.
- `/dev/serial0 → ttyAMA0`, `root:dialout`; operator is in `dialout`.
- Panel firmware builds clean for both PlatformIO envs (`panel`, `polarity`),
  speaking protocol v1 on both transports.
- Pi side implements v1: `M:none` → `MODE_NONE` with an idle pipeline builder,
  `V:` version check, `B:alive` watchdog (30 s), and the change-guard.
- Pi-side test suite: 162 passing.
- Wiring diagrams generated in `firmware/control-panel/docs/`:
  `breadboard_wiring.svg` (controls → ESP32) and `pi_link_wiring.svg`
  (ESP32 → Pi, powered from the X1202 XH2.54 5 V output).
- Power: X1202 5 V → ESP32 VIN. X1202 GND is the Pi's GND (it sits on the
  header), so the link needs only one signal wire and no separate ground run.
  The pot's supply is the ESP32's own 3V3 pin — its wiper feeds GPIO34, which is
  not 5 V tolerant.

---

## 8. What to copy, and what changed since your v1 spec

The operator copies the whole `firmware/control-panel/` folder. These are the
files that matter to you:

| File | What it is |
|---|---|
| `PROTOCOL.md` | **The frozen contract.** Read this first — it supersedes your v1 doc where they differ, and the differences are only the ones in §2 and §4 above |
| `src/control_panel.cpp` | The firmware. Protocol v1, dual transport |
| `src/polarity_test.cpp` | Bring-up sketch: raw pin states + the mode the rockers imply |
| `panel_monitor.py` | The validator (§3.5) |
| `platformio.ini` | Two envs: `panel` and `polarity` |
| `docs/*.svg`, `docs/*.png` | Wiring — breadboard and Pi link |
| `HANDOFF-FROM-PI.md` | This document |

**Note on `platformio.ini`:** `upload_port` and `monitor_port` are pinned to
`/dev/ttyUSB*`. That is a **Pi-specific** guard — the Pi always has its own
`/dev/ttyAMA10`, and bare auto-detection picks it, producing an ~11 s esptool
handshake against a port with no ESP32 behind it and a `FAILED` build. On a
Windows laptop the glob matches nothing, so **comment both lines out** — plain
auto-detection is correct there, since Windows has no `ttyAMA10` to trip over.

If `921600` fails mid-flash with checksum or timeout errors, drop `upload_speed`
to `460800`.

### Firmware changes since your v1 spec

| Change | Why |
|---|---|
| TX moved to GPIO23 via `Serial2` | D2 — keeps the ROM boot log off the Pi link |
| Every line also emitted on USB | D5 — one binary, testable on your bench |
| `M:none` emitted when both rockers are off | D3 — was previously "emit no `M:` at all" |
| `B:alive` + full state burst every 10 s | D7 — the boot race in §3 |
| Boot delay 1.5 s → 200 ms | D6 — the old delay could not work under shared power |
| Rocker burst sends **both** `S:` flags | §2.5, as you specified |
| DETECT rocker on GPIO17, not GPIO23 | GPIO23 now carries the link wire; GPIO17 has plain GPIOs either side, so a mis-plugged jumper reads wrong instead of shorting |

One bug worth knowing about because it is easy to reintroduce: the two rocker
debounce calls must **both** run every pass. `if (settled(a) || settled(b))`
short-circuits and freezes `b`'s timer whenever `a` moves — `settled()` is the
state machine, not a query. They are assigned to separate booleans first.
