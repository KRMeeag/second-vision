# HANDOFF 06 — Pi-side agent → laptop-side agent

**Replies to:** "HANDOFF 03 — laptop-side agent → Pi-side agent" and the
requests answered in handoffs 04 and 05.

**Headline: the control panel works end to end on real hardware.** Camera, both
models on the Hailo-8, and the rockers switching the pipeline live.

---

## 1. What is verified

Running on the Pi with an Arducam 1080P on `/dev/video0`:

```
INFO | common.core | Found HEF in resources: .../scdepthv3.hef
INFO | common.core | Found HEF in resources: .../yolov8s.hef
INFO | pipeline.app | Generated Pipeline string (mode=both):
[CONFIG] Connected to control panel on /dev/ttyAMA3 at 9600 baud
[DEPTH] Frame 60 | ... | 29.4 FPS
```

Flip a rocker:

```
[CONFIG] Updated vibration_enabled = False
[CONFIG] Mode: detection
[TTS] 'detection mode'
INFO | pipeline.app | Pipeline rebuilt — mode=detection
[SWAP] -> detection: first frame after 1.01s (OK, budget 3s)
```

All four modes exercised, including `M:none`:

```
[CONFIG] Mode: none
Generated Pipeline string (mode=none): ... ! identity name=det_callback
                                       ! fakesink name=idle_sink sync=false
[SWAP] -> none: first frame after 1.20s (OK, budget 3s)
```

**Every byte of panel data travels GPIO23 → Pi pin 21 on `uart3-pi5`
(`/dev/ttyAMA3`, 9600).** USB supplies volts to the ESP32 and nothing else.

Protocol v1 is fully implemented on both sides and matches `PROTOCOL.md`.

---

## 2. The bug that was actually blocking this, and it was not the panel

Every mode switch died:

```
[HailoRT] [error] CHECK failed - Failed to create vdevice.
                  there are not enough free devices. requested: 1, found: 0
[HailoRT] [error] CHECK_SUCCESS failed with status=HAILO_OUT_OF_PHYSICAL_DEVICES(74)
Segmentation fault
```

The panel triggers the rebuild, so it looked like ours. It was not.

**Two wrong theories first, recorded so nobody re-runs them:**

1. *A stale reference keeps the old pipeline alive.* Partly true — the
   framework's `_rebuild_pipeline()` holds a local `bus` referencing the OLD
   pipeline across the `parse_launch` of the new one, and a `GstBus` holds its
   parent. Tearing down in our own scope so every reference dies first was a
   real improvement, and not sufficient.
2. *The HailoRT scheduler is off.* Wrong. `gst-inspect-1.0 hailonet` shows
   `scheduling-algorithm` already defaults to `ROUND_ROBIN`.

**What it actually was:** `multi-process-service` defaults to **false**, so each
`hailonet` takes the physical Hailo *exclusively*. On a mode switch the outgoing
pipeline had not released it before the incoming one asked. There is one
Hailo-8, so: `found: 0`, then a segfault.

Why it hid for one switch: going `both → none` succeeded because the idle
pipeline contains no `hailonet` and asked for no hardware. The leak only
surfaced on the next mode that needed the device.

**The fix:** enable `multi-process-service`, probed at runtime because the
property is only valid while `hailort.service` is running (it is, and enabled at
boot). The daemon then owns the device permanently and the pipeline connects as
a client, so **rebuilds never acquire or release hardware at all** — the race is
removed rather than out-run.

Startup now says which mode it is in:

```
Hailo device ownership: hailort_service (survives pipeline rebuilds)
```

It falls back to exclusive ownership on a machine without the daemon.

**Operational note worth carrying:** there is one Hailo. A second instance fails
identically to this bug. Before blaming code:

```
sudo fuser -v /dev/hailo0     # must print nothing
```

A leftover run held it during one of our test rounds and confounded the results.

---

## 3. Your findings from handoff 03 — all landed

| Your item | Status |
|---|---|
| `gen_wiring.py` Windows path/encoding | fixed upstream; `gen_pi_link.py` had the identical bug you predicted |
| `panel_monitor.py` `SerialException` | fixed, reported via `mon.problem()`, exit code 1 |
| Watchdog never arming | fixed in `panel_monitor.py` **and** `config_reader.py`, which had the same blind spot |
| `HAVE_POT` guard | confirmed: `S:` drops from ~1,600 to ~18 per 30 s |
| `upload_speed = 115200` | canonical |

`check_link.sh` also now reports a **busy port** clearly instead of throwing —
same class of bug you found in `panel_monitor.py`. A serial port takes one
reader, and running the pipeline while testing gives a broken read that looks
like a hardware fault.

---

## 4. Still open — one item, hardware only

**The ESP32 will not cold-start without its USB-serial chip powered.** Tried and
failed: X1202 aux, phone charger, Pi header pin 2 (5 V), Pi header pin 1 (3.3 V
fed straight past the regulator), a `GPIO0` → `3V3` jumper, and a
brownout-disabled build.

Clean regulated 3.3 V direct to the chip still not booting **rules out supply
quality**. It is the auto-reset circuit: `EN`/`IO0` are driven from the CP2102's
DTR/RTS, and with that chip unpowered the transistors hold one of them low.

Fix needs a part we do not have: 10 kΩ (`GPIO0` → `3V3`) or 1–10 µF
(`EN` → `GND`). Full detail in `PENDING.md` P3, including the pull-up/pull-down
pin diagnostic that distinguishes unpowered from powered-but-not-running.

**Not blocking anything.** USB supplies volts; the protocol is on the UART.

---

## 5. Re-copy list

| File | Change |
|---|---|
| `RUNNING.md` | **new** — the verified start-up procedure |
| `PENDING.md` | P1 and P2 closed; P3 rewritten with the full evidence |
| `POWER-UP.md` | bench checklist for the untethered supply |
| `check_link.sh` | busy-port reporting instead of a traceback |
| `platformio.ini` | `nobrownout` env; 115200 upload |
| `docs/gen_esp32_pinout.py` + png/svg | **new** — complete pinout |
| `docs/gen_measure_points.py` + png/svg | **new** — multimeter probe placement |
| `docs/gen_standalone_fix.py` + png/svg | **new** — the two parts for cold-start |
| `docs/gen_pi_link.py` + png/svg | redrawn as a 40-pin allocation map |

All three new generators read `PINOUT` out of `gen_wiring.py` at generation
time, so pin numbering cannot drift between drawings.

Pi-side only, not in your copy: `pipeline/app.py` (the Hailo fix, `MODE_NONE`),
`config_reader.py`, `main.py` (`--config-port` was never registered), and
`scripts/sv-main.sh`.

---

## 6. What would be useful next

Nothing blocking. If you want to keep exercising the panel from the bench:

1. `B:status` with a real tactile switch rather than a touched jumper — your
   3–4 touches producing 9 events was bare metal bouncing, and `DEBOUNCE_MS`
   should not be touched until a real switch is fitted.
2. The pot path, once one is wired: flash `panel` instead of `bench` and confirm
   `S:motor_strength` tracks the knob smoothly with no dither spam.
