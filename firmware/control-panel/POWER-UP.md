# Power-up checklist — X1202 → protoboard → ESP32

Follow in order. Every check happens **before** the ESP32 is in the circuit, so
a mistake costs a re-wire instead of a board.

You need a multimeter. There is no reliable way to verify polarity without one,
and reversed 5 V on the ESP32's `5V` pin destroys it instantly — most NodeMCU
boards have no reverse-polarity protection.

---

## 0. Before you start

- **ESP32 NOT connected to the protoboard.** Leave it out entirely.
- **USB unplugged from the ESP32.** USB 5 V and the `5V` pin are the same net on
  this DevKit, with no isolation diode. Never have both connected.
- Have the panel firmware already flashed (`bench` or `panel`) — do that over
  USB from the laptop first, then unplug.

---

## 1. Identify the XH2.54 polarity  — the step that saves the board

With the X1202 running and its output connector free:

1. Meter to **DC volts**, 20 V range.
2. Black probe on a known ground — the Pi's **pin 6**, or any header GND. The
   X1202 sits on the Pi's header, so its ground and the Pi's are one node.
3. Red probe on each XH2.54 pin in turn.

| Reading | Meaning |
|---|---|
| **+4.9 to +5.2 V** | this is the **5 V** pin |
| **0 V** | this is **GND** |
| **negative** | probes reversed — swap and re-read, do not "correct" it in wiring |
| **above 5.5 V** | STOP. Wrong connector, or the UPS is misconfigured |

**Write down which physical pin is which.** Do not rely on wire colour: XH2.54
leads are frequently red/black in either order, and the silkscreen can be
ambiguous under a stacked HAT.

---

## 2. Build the protoboard with no ESP32 in it

Two positive rails, kept apart:

| Rail | Source | Feeds |
|---|---|---|
| **5V** | X1202 XH2.54 | the ESP32's `5V` pin — nothing else |
| **3V3** | the ESP32's own `3V3` **output** | the potentiometer only |
| **GND** | X1202 GND | everything's return |

The 3V3 rail is fed *by* the ESP32, not by the X1202. It carries nothing until
the board is in and powered.

---

## 3. Continuity check — power OFF

Disconnect the X1202 feed. Meter to **continuity / beep**:

| Between | Must be |
|---|---|
| 5V rail ↔ GND rail | **silent.** A beep is a short — find it before powering |
| 5V rail ↔ 3V3 rail | **silent.** These must never join |
| 3V3 rail ↔ GND rail | silent (the pot's 10 kΩ may read as resistance, not a beep) |
| each rocker's two legs | beep in one switch position only |

A beep on the first two rows is exactly the fault that would put 5 V somewhere
it must not go. Fix it here.

---

## 4. Rails live, ESP32 still out

Reconnect the X1202 feed. **ESP32 still not inserted.**

| Measure | Expect |
|---|---|
| 5V rail → GND rail | +4.9 to +5.2 V |
| polarity | red probe on 5V rail reads **positive** |
| 3V3 rail → GND rail | **0 V** — nothing drives it yet |

If the 3V3 rail shows anything, it is joined to the 5V rail. Go back to step 3.

---

## 5. Insert the ESP32

Power off first. Insert, checking orientation against
`docs/breadboard_wiring.svg` — pin 1 (`3V3`) and the `5V` pin are at opposite
ends of the left column, so a board in backwards puts 5 V on the 3V3 pin.

Power on and, within about two seconds:

| Check | Expect |
|---|---|
| red power LED | **lit** |
| 3V3 rail → GND | now **+3.2 to +3.4 V** — the regulator is working |
| board temperature | cool. **Anything warm: power off immediately** |

A warm regulator means a short. That is the same symptom that turned up earlier
in this project when a switch leg bridged the 3V3/GND row.

---

## 6. Verify the link from the Pi

```bash
cd ~/Projects/Second-Vision/worktrees/feature-camera-feed
source my_hailo_env/bin/activate
cd firmware/control-panel
./check_link.sh
```

All three stages should pass **with no USB attached anywhere**. That is the
proof that the X1202 is genuinely powering the panel.

---

## If you have no multimeter

Do not guess the polarity. Options, best first:

1. **Borrow one.** Even the cheapest continuity/DC-volts meter is enough for
   every check above.
2. **Keep using USB power** for now. The panel works fully on USB — the link,
   the rockers and the protocol are all verified that way. Only the final
   untethered configuration needs the X1202 feed, and it can wait.

Option 2 is a legitimate hold: nothing in the software depends on where the 5 V
comes from.
