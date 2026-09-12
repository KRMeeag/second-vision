# Running Second Vision with the control panel

Verified working 2026-09-09: camera + both Hailo models + rockers switching
modes live.

---

## 0. Preflight — 10 seconds, saves an hour

```bash
ls /dev/video0        # camera        (Arducam on uvcvideo)
ls /dev/ttyAMA3       # panel UART    (uart3-pi5, GPIO8/9)
ls /dev/ttyUSB0       # ESP32 power   (USB only supplies volts)
sudo fuser -v /dev/ttyAMA3    # must print NOTHING
```

Any missing device, stop and fix that first — everything downstream depends on
these four lines.

**If `fuser` prints a process**, an old run still holds the panel port. A serial
port takes one reader. Kill it:

```bash
kill <PID>
```

---

## 1. Flash the right firmware

**`bench`** while no potentiometer is fitted. `panel` reads GPIO34, which floats
without a pot and emits ~54 spurious `S:motor_strength` lines per second — it
drowns the log and starves the switch events behind it.

```bash
cd firmware/control-panel
pio run -e bench -t upload
```

- Remove any `GPIO0` -> `3V3` jumper first, or you get
  `Wrong boot mode detected (0x13)`. That jumper is only for booting without USB.
- Hold `IO0` for the whole upload if auto-reset fails.
- Switch to `-e panel` once the pot is physically wired.

---

## 2. Verify the link before involving the pipeline

```bash
./check_link.sh
```

Three passes, ending with real protocol:

```
B:alive / S:tts_enabled:0 / S:vibration_enabled:0 / M:none
```

This isolates panel problems from pipeline problems. If it fails here, the
pipeline will not help — read the numbered guidance the script prints.

---

## 3. Run it

```bash
cd ~/Projects/Second-Vision/worktrees/feature-camera-feed
source my_hailo_env/bin/activate
./scripts/sv-main.sh
```

The script detects a missing camera (falls back to `--mock`) and a missing
`DISPLAY` (drops `--use-frame`), so it does the right thing over SSH.

Spelled out, if you prefer:

```bash
python3 src/second_vision/main.py \
  --input /dev/video0 --width 640 --height 480 \
  --config-port /dev/ttyAMA3
```

**Do NOT add `--use-frame` over SSH.** It hands every frame to a preview window
that cannot exist without a DISPLAY, so nothing drains the queue: throughput
halves and the log fills with `Frame queue is full; dropping frame`.

---

## 4. What a good start looks like

```
INFO | pipeline.app | Generated Pipeline string (mode=both):   <- both models
[CONFIG] Connected to control panel on /dev/ttyAMA3 at 9600 baud
[CONFIG] Panel booted, protocol v1
[DEPTH] Frame 15 | ... | 29.4 FPS
```

`hailo_display not found in pipeline` is expected and harmless — there is no
display sink by design; `callbacks.py` handles drawing.

---

## 5. Test the rockers — this is the acceptance test

Flip one and watch:

```
[CONFIG] Updated vibration_enabled = False
[CONFIG] Mode: detection
[TTS] 'detection mode'
INFO | pipeline.app | Pipeline rebuilt — mode=detection
[SWAP] -> detection: first frame after 1.01s (OK, budget 3s)
```

All four combinations:

| DETECT | DEPTH | Mode | Runs |
|---|---|---|---|
| on | on | `both` | YOLOv8 + SC-DepthV3 |
| on | off | `detection` | detection only, speech |
| off | on | `depth` | depth only, motors |
| off | off | `none` | nothing |

**A ~1 s blackout per switch is correct**, not a fault — the pipeline is being
torn down and rebuilt. The budget is 3 s.

---

## 6. To actually see the camera

The preview needs a display. Either use the Pi's own monitor, or forward X:

```bash
ssh -X sv-rpi5@100.119.250.2
```

`DISPLAY` is then set, `sv-main.sh` re-enables `--use-frame` by itself, and the
window opens on your laptop. Slow over the network, fine for a demo.

---

## Known-good state

| | |
|---|---|
| ESP32 power | USB from the Pi. **Volts only** — no panel data touches USB |
| Panel data | `GPIO23` -> Pi pin 21, `uart3-pi5`, `/dev/ttyAMA3`, 9600 |
| Panel ground | `GND` -> Pi pin 20 |
| Motor ESP32 | `/dev/serial0` (pins 6/8/10) — a different board, `--serial-port` |

Running the panel without USB needs a 10k resistor (`GPIO0` -> `3V3`) or a
capacitor (`EN` -> `GND`). See PENDING.md P3.
