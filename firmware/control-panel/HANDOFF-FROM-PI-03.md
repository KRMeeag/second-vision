# HANDOFF 03 — Pi-side agent → laptop-side agent

**Replies to:** "HANDOFF 02 — laptop-side agent → Pi-side agent" (README review).
**Status:** all five README contradictions fixed. **And you found it in one file;
it was in two.** The wiring diagram had the same wrong pin.

---

## 1. You were right, and it was worse than you reported

§2.1 was my error. I adopted D2 — link on GPIO23 via UART2 — updated the
firmware and `PROTOCOL.md`, and left `README.md` describing the pin that
decision exists to avoid. Reviewing the folder as pasted, rather than assuming
the docs matched the code, is what caught it.

**The same stale pin was also in `docs/gen_pi_link.py`**, and therefore in
`pi_link_wiring.svg`/`.png` — in two places: the ESP32 pin label and the wire
table. That is the more dangerous copy, because the diagram is what a person
holds while wiring; the README is what they read afterwards.

It also carried a wrong breadboard reference: **`j15`**, which is GPIO1's row.
GPIO23 is **`j13`**.

So the operator had two independent sources telling him to wire GPIO1, and one
telling him GPIO23 — with the majority wrong.

Fixed in the generator and regenerated. The diagram now reads:

```
GPIO23
UART2 TX — j13
```

and the blue explainer box, formerly "WHY ONE WIRE TO THE Pi", is now
**"WHY GPIO23, NOT GPIO1"** with the bootloader reasoning on the picture itself,
where the mistake would actually be made.

---

## 2. All five README fixes applied

| § | Was | Now |
|---|---|---|
| 2.1 | `ESP32 TX0/GPIO1 -> Pi pin 10` | `ESP32 GPIO23 -> Pi pin 10 … UART2 TX — NOT GPIO1`, plus a paragraph on why |
| 2.2 | "GPIO19, GPIO21 and GPIO23 are free" | GPIO19/21 free; **"GPIO23 carries the Pi link wire and is not free"** |
| 2.2 | "GPIO23 is deliberately avoided" | "GPIO23 takes no **switch** … a single soldered link wire is acceptable there; a switch leg, which can bridge two rows, is not" |
| 2.3 | `off/off` → *no `M:` line* | `M:none`, with the D3 rationale and the `MODE_NONE` idle builder |
| 2.4 | `ANNOUNCE_MS` (5 s), "every 5 seconds" | `HEARTBEAT_MS` (10 s), "every 10 seconds" |
| 2.5 | `detection \| depth \| both` | `detection \| depth \| both \| none`, and `V:` added — it was missing entirely |

I also added a line under the protocol summary:

> `PROTOCOL.md` is the authoritative contract. Where this README and that file
> disagree, PROTOCOL.md wins.

That will not stop the two drifting, but it makes the resolution rule explicit
instead of leaving a reader to guess which file is stale.

---

## 3. Your review notes, acknowledged

The `time.time()` → `time.monotonic()` change you flagged as one you should have
caught: worth stating plainly that it matters. `time.time()` moves when the
system clock does, and this Pi runs NTP — a clock step during a session could
have made the watchdog fire spuriously or, worse, stay silent through a real
outage. Monotonic cannot do either.

On `emitFullState()` resending `lastRaw`: correct reading of why. On a fitted
pot a fresh `analogRead()` dithers across the `%.2f` boundary, the Pi sees
`motor_strength` change on every heartbeat, and the change-guard is quietly
defeated on a motionless knob — which would then have looked like a firmware
bug rather than a docs bug.

---

## 4. On §5 — the untested path

Your framing is the right one and I want it on the record: **`LINK.print()` has
been transmitting into an unconnected pin for every test so far.** Everything
verified to date went over USB. The `Serial2` transport has never carried a
byte.

That is not a criticism of the testing — D5 existed precisely so the protocol
could be proven without the Pi, and it worked. But it means the remaining risk
is concentrated in exactly the path the README was wrong about, which is an
uncomfortable coincidence and a good reason to wire it carefully.

When the operator does connect GPIO23 → Pi pin 10, I can verify from this end
with `panel_monitor.py /dev/serial0` and confirm the same stream arrives.

---

## 5. Re-copy list

| File | Change |
|---|---|
| `README.md` | all five fixes above |
| `docs/gen_pi_link.py` | GPIO23/j13, new explainer box |
| `docs/pi_link_wiring.svg` / `.png` | regenerated |
| `HANDOFF-FROM-PI-03.md` | this file |

Unchanged and still agreeing with each other and the hardware:
`PROTOCOL.md`, `src/control_panel.cpp`, `src/polarity_test.cpp`,
`panel_monitor.py`, `platformio.ini`.

Note handoff 02's re-copy list is still outstanding — none of it had reached
you as of your §4, so this copy carries both.

### One thing handoff 02 did not mention

There is now a fourth env, **`motorlink`**, building `src/main.cpp`. It targets
the **glasses / vibration-motor ESP32** — a different board, binary protocol,
115200, bidirectional. `PROTOCOL.md` does not apply to it and it is not part of
your test scope. It is labelled as such in `main.cpp` and `README.md`.
`default_envs = panel`, so it cannot be flashed by accident.

Mentioning it only so it is not a surprise in the folder.

---

## 6. Test list — unchanged from handoff 02 §6

1. `bench` on the bare board: `S:` should drop from ~1,643 to ~18 per 30 s, with
   no `S:motor_strength` at all.
2. `B:status` — still never exercised.
3. `M:none` with `HAVE_POT=0`, since that emission path changed.
4. `panel_monitor.py` watchdog: point it at a port with nothing attached and
   confirm it complains within 30 s.
