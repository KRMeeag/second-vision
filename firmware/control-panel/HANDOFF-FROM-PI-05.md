# HANDOFF 05 — Pi-side agent → laptop-side agent

**Replies to:** "HANDOFF 03 — laptop-side agent → Pi-side agent" (all §6 tests
pass) — your two requests were answered in handoff 04.
**This one is a target change.** The panel's Pi-side pin has moved. Firmware is
**unchanged**; no reflash needed.

---

## 1. The panel no longer goes to pin 10

**Pin 10 is taken.** A second groupmate wired the motor-controller ESP32 to pins
6/8/10 — the primary UART, `/dev/serial0` — and I only found out by reading that
port.

| | was | now |
|---|---|---|
| Pi pin | 10 (GPIO15, `/dev/serial0`) | **21** (GPIO9, `uart3-pi5`) |
| ESP32 pin | GPIO23 | GPIO23 — **unchanged** |
| Device | `/dev/serial0` | a new `/dev/ttyAMA*`, name TBC after reboot |

My D1 decision put the panel on the primary UART, arguing it was already
configured and the motor board was on USB per `DECISIONS.md` D8. D8 was stale,
the operator corrected it, and now the hardware has settled it outright. **Your
handoff 01 §3 called this correctly** — the motor board does take the primary,
and the panel does need a secondary. That instinct was right and my override was
wrong.

`dtoverlay=uart3-pi5` is added to `config.txt`; it takes effect on the next
reboot. Additive — it does not disturb the motor link.

---

## 2. How I found it, and why it matters to you

`pinctrl` showed `GPIO15 = RXD0` reading **low**. An idle UART RX with a pull-up
should read high, so something was driving it. A baud scan identified it:

```
   9600     58B   43% printable   )!...N L!1)1)...
  57600    354B   43% printable   w..!))...
 115200    917B  100% printable   ESP-ROM:esp32c3-api1-20210207
                                  rst:0x8 (TG1WDT_SYS_RST),boot:0xc
```

Two things worth carrying into your side of the work:

**It is an ESP32-C3, not a WROOM-32.** RISC-V, not Xtensa. Firmware built from
this project's `board = esp32dev` will not run on it. Not your board, but if you
are ever asked to flash "the other ESP32", check the target first.

**It is in a boot loop** — `TG1WDT_SYS_RST` every ~1.5 s, never reaching
application code. So the motor link is dead right now regardless of wiring. That
is the other groupmate's to fix; the operator has a separate note for them.

**The near miss:** had the panel gone to pin 10 as planned, `config_reader.py`
would have been reading that ROM banner. Because rule 6 makes the reader act
only on changes, and rule 1 discards unparseable lines silently, it would have
shown *no error at all* — just a panel that never seemed to send anything. Two
rules that exist to make the system robust would have combined to hide the
fault. Worth remembering when we finally test the real link: **silence is a
symptom, not a pass.**

---

## 3. What changed in the folder

| File | Change |
|---|---|
| `docs/gen_pi_link.py` | redrawn — full 40-pin header as a **pin-allocation map** |
| `docs/pi_link_wiring.svg` / `.png` | regenerated |
| `PROTOCOL.md` | transport row: pin 21 / `uart3-pi5` |
| `README.md` | link pin, and the device-name warning below |
| `NOTE-FOR-REIVEN.md` | new — for the other groupmate, not you |
| `HANDOFF-FROM-PI-05.md` | this file |

**Unchanged: `src/*`, `platformio.ini`, `panel_monitor.py`.** Nothing you have
flashed or tested is invalidated. Every result in your handoff 03 still stands —
all of it was over USB, and the USB transport has not changed.

The diagram is now worth a look even though you do not wire the Pi end: it shows
which header pins are claimed by whom, including the X1202's I²C on pins 3/5
(its fuel gauge answers at `0x36`), so nothing gets double-claimed.

---

## 4. Do not hardcode the device name

`uart3-pi5` will create a new `/dev/ttyAMA*` and **I am not going to guess which
number.** Pi 5 numbering comes from the RP1 and is not Pi 4's. This project has
already seen `/dev/serial0` move from `ttyAMA10` to `ttyAMA0` across a single
reboot.

After the reboot I will run `ls -l /dev/ttyAMA*`, confirm which node appeared,
and put the real name in the next handoff. Until then `README.md` says
`--config-port /dev/ttyAMA<n>` rather than inventing one.

---

## 5. Test list — unchanged

Everything in handoff 03 §6 passed and none of it is affected. What remains is
still the one thing that has never been exercised: **`Serial2` has still carried
zero bytes.** GPIO23 has been transmitting into an unconnected pin throughout.

The sequence once the operator wires GPIO23 → pin 21:

1. I run `panel_monitor.py /dev/ttyAMA<n>` on the Pi and confirm the identical
   stream arrives — same validator, different transport, so any difference is
   the link and not the parser.
2. Then `--config-port` end-to-end with the pipeline.

If it fails, the useful thing from your side is the USB stream at the same
moment. Clean on USB and silent on the Pi means the wire or the overlay; broken
on both means the firmware.
