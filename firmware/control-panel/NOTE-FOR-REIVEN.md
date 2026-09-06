# Note for Reiven — from the Pi

Kenzhu's agent runs on the Raspberry Pi itself, so it can read the live state of
the hardware. Two things turned up that you would have no way to see from your
machine. The first is more urgent than the pin question.

---

## 1. The board on pins 6/8/10 is in a boot loop

I read `/dev/serial0` directly. At 115200 it carries this, repeating roughly
every 1.5 seconds, continuously:

```
ESP-ROM:esp32c3-api1-20210207
Build:Feb  7 2021
rst:0x8 (TG1WDT_SYS_RST),boot:0xc (SPI_FAST_FLASH_BOOT)
Saved PC:0x40380082
SPIWP:0xee
mode:DIO, clock div:1
load:0x3fcd5810,len:0x438
...
entry 0x403cc710
```

`rst:0x8 (TG1WDT_SYS_RST)` is a **Timer Group 1 watchdog system reset**. It is
firing during early boot on every cycle, so the board never reaches application
code — it only ever emits the ROM banner. Nothing you flashed is running.

**The wiring is fine.** Electrical continuity is proven: bytes are arriving at
the Pi at the right voltage and a coherent baud. The fault is on the board.

### It is an ESP32-C3, not an ESP32-WROOM-32

`esp32c3-api1-20210207` is the C3's ROM. That is a **RISC-V** chip. The
ESP32-WROOM-32 used elsewhere in this project is **Xtensa** — a different
instruction set entirely.

If the firmware was built for `board = esp32dev` (ESP32-D0WD, Xtensa) and
flashed to this board, an early watchdog reset is exactly what you would expect.
Worth checking first, because it is both the most likely cause and the cheapest
to rule out:

```ini
; platformio.ini — for a C3 it must be
board = esp32-c3-devkitm-1     ; NOT esp32dev
```

Other things that produce `TG1WDT_SYS_RST` this early: a partially written flash
image, or a bootloader/partition table left over from a different chip target. A
full erase before reflashing clears both:

```
pio run -t erase
```

### One consequence for your baud

The banner is at 115200. If your Pi-side reader opens the port at a different
rate you will see it as garbage rather than as a recognisable boot loop, which
makes this much harder to spot. It reads as line noise, not as a crashing board.

---

## 2. On the pin advice — mostly right, one correction

Your pin geography is correct. I verified all three pairs against the overlay
descriptions on this Pi:

```
uart2-pi5   GPIOs 4-5     (pins 7, 29)
uart3-pi5   GPIOs 8-9     (pins 24, 21)
uart4-pi5   GPIOs 12-13   (pins 32, 33)
```

All six pins currently read `none` — genuinely free.

One correction, though. The recommendation was:

> *"Kenzhu should map … the Arduino control panel to the non-UART GPIOs"*

**The control panel is itself a UART device.** It is an ESP32 transmitting
9600 8N1 text — `V:`, `S:`, `M:`, `B:` lines — which the Pi reads with pyserial.
Putting it on ordinary GPIOs would mean bit-banging a software serial receiver.
It needs a real UART, not general-purpose pins.

So it takes **`uart3-pi5`**, which your list already identified:

| Link | UART | Pi pins |
|---|---|---|
| Your ESP32-C3 (motors) | `/dev/serial0` | 6, 8, 10 — unchanged |
| Kenzhu's control panel | `uart3-pi5` | **21** (GPIO9, RX) + shared ground |

One wire, because the panel never receives. Ground is already common: the X1202
UPS sits on the header, so its ground and the Pi's are the same node.

I have added `dtoverlay=uart3-pi5` to `/boot/firmware/config.txt`. It is
**additive and does not touch your link** — pins 6/8/10 and `/dev/serial0` are
unchanged. It takes effect on the next reboot.

### One pin to be aware of

**GPIO9 is also SPI0 MISO.** It is free only because `dtparam=spi=on` is
commented out in `config.txt`. If you later attach an SPI peripheral, that
collides with the panel. Worth treating pin 21 as claimed from now on.

---

## Current header allocation

| Pins | Owner |
|---|---|
| 3, 5 | I²C — the X1202's fuel gauge answers at `0x36` |
| 6, 8, 10 | Motor ESP32-C3, `/dev/serial0` (yours) |
| 21 | Control panel, `uart3-pi5` (Kenzhu's) |
| 2, 4 | 5 V — but the panel is powered from the X1202's XH2.54 output, not here |

Everything else on the header is unclaimed.

`docs/pi_link_wiring.png` in the control-panel folder now draws this as a
full 40-pin map, so both of us can see what is taken before claiming anything.
