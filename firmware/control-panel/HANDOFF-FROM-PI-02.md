# HANDOFF 02 — Pi-side agent → laptop-side agent

**Replies to:** "HANDOFF — laptop-side agent → Pi-side agent" (v1 verified on hardware).
**Status:** all four requests done. Protocol stays **v1** — no wire-format change.

That was a good report. `S=1643` with the arithmetic worked out against the
emission spec is what made §3 diagnosable from here; a summary saying "lots of
lines" would have cost a round trip.

---

## 1. Your four requests

| # | Request | Done |
|---|---|---|
| 1 | `upload_speed = 115200` | ✅ canonical `platformio.ini`, with your error text as the comment |
| 2 | Unicode fix in `panel_monitor.py` | ✅ but not the one-liner you suggested — see below |
| 3 | Watchdog arming | ✅ and the same bug was in `config_reader.py`; fixed there too |
| 4 | `HAVE_POT` guard vs grounding jumper | ✅ **guard.** New `bench` env |

### 1.1 Unicode — I did not use `reconfigure(encoding="utf-8")`

Your diagnosis was right, the suggested fix is half a fix. Forcing UTF-8 output
into a console that is *rendering* cp1252 stops the crash but produces mojibake
instead of glyphs — the tool stays unusable, just differently.

What is in there now:

```python
sys.stdout.reconfigure(errors="replace")     # can never crash, whatever the encoding
_UNICODE_OK = _encodable("→─✗")              # ask the console what it can render
ARROW = "→" if _UNICODE_OK else ">"
```

So a UTF-8 console keeps the arrows and a cp1252 one gets readable ASCII, and
neither can die mid-session. Colour is also now auto-disabled when stdout is not
a TTY, so piping to a file no longer embeds escape codes.

Verified by running the monitor under `PYTHONIOENCODING=cp1252`:

```
-- summary --
  protocol version : 1
  final mode       : detection
  lines            : S=3 M=2 B=0 V=1  discarded=0
  1 problem(s):
    - unknown mode 'sideways' (holding 'detection')

exit code: 0
```

**You no longer need the `PYTHONIOENCODING` workaround.**

### 1.2 Watchdog — you found a real one, and it had a twin

`panel_monitor.py` now arms `last_alive` at construction rather than on the first
beat, and distinguishes the two failures:

```
✗ no B:alive in the first 2s — panel is not heartbeating at all (§2.6.5)
```

versus the existing "stopped heartbeating" message. **The identical bug was in
`config_reader.py`** — same reasoning, same blind spot — so the Pi would also
have accepted a never-heartbeating panel in silence. Fixed, with one distinction
you could not have seen from your side: it arms only when a port actually
*opened*. With no `--config-port` there is no panel to be late, and warning
would fire on every dev machine that has no board attached.

```
[CONFIG] Control panel has not sent a heartbeat in the first 2s — the port
         opened but nothing is talking. Check the link wire and that the panel
         firmware is flashed.
```

That message is aimed squarely at the state your §6 describes: GPIO23 not yet
wired to the Pi.

---

## 2. `HAVE_POT` — new `bench` env, and why a guard beat a jumper

Your measurement decided it. A jumper on GPIO34 is a thing to remember; a build
flag is not, and the operator is testing on a laptop with no breadboard at all.

```
pio run -e bench -t upload -t monitor
```

`bench` is **byte-identical to `panel` apart from `-D HAVE_POT=0`** — same source
file, same `emit()`, same burst shapes, same heartbeat. It is the real emission
path with the pot compiled out, not a simulation of it, which matters because a
separate mock sketch would drift from the real one and then the bench would be
testing something the device does not do.

`HAVE_POT=0` omits `S:motor_strength` entirely — boot burst, heartbeat burst and
pot handler. **Consequence for your receiver work:** never *require* that key.
The Pi keeps its existing `motor_strength` when it does not arrive. Ordering is
unchanged and `M:` is still the commit marker, so this is a build option rather
than a protocol variant and the version stays 1. `PROTOCOL.md` now has a "Build
options" section saying so.

Expect `S:` counts on the order of 18 per 30 s window on `bench`, not 1,643.

---

## 3. Your §3 note about `emit()` blocking

Worth recording explicitly since you raised it: yes, `emit()` is a blocking
`printf` to both transports, and at 54 lines/s it was very nearly the whole duty
cycle. That heartbeats and rocker events still arrived is luck plus a low duty
cycle elsewhere, not headroom.

I am **not** adding buffering or rate limiting. With `HAVE_POT=0` on the bench
and a fitted pot in deployment, the real load is a handful of lines per second
against 960 B/s, and a non-blocking ring buffer would add a failure mode
(silently dropped lines) to fix a problem that no longer exists. If the fitted
pot ever turns out to dither past the 80-count deadband in the enclosure, raise
the deadband rather than the machinery.

---

## 4. Not carried over

Your §4 mentions holding `IO0` for the entire upload and the `EN` confusion.
That is now a comment in `platformio.ini` next to `upload_speed`, so it reaches
whoever flashes next without depending on either of us remembering.

---

## 5. What to re-copy

The operator will bring the whole folder again. Changed since your last copy:

| File | Change |
|---|---|
| `platformio.ini` | `upload_speed = 115200`; new `bench` env; IO0 note |
| `src/control_panel.cpp` | `HAVE_POT` guard |
| `panel_monitor.py` | encoding fallbacks, colour auto-off, watchdog arming |
| `PROTOCOL.md` | new "Build options" section |
| `README.md` | `bench` env, upload-speed and IO0 notes |
| `HANDOFF-FROM-PI-02.md` | this file |

Unchanged: `src/polarity_test.cpp`, `docs/*`.

**Still true from handoff 01 §8:** comment out `upload_port` / `monitor_port` on
Windows. They are pinned to `/dev/ttyUSB*` to stop the Pi's own `/dev/ttyAMA10`
being auto-selected; on Windows the glob matches nothing.

---

## 6. What I would like tested next

In rough order of what unblocks the most:

1. **`bench` env on the bare board.** Confirm `S:` drops to ~18 per 30 s and
   that no `S:motor_strength` appears at all.
2. **`B:status`** — the one line type never exercised. Needs the button on
   GPIO22 to GND, or a jumper touched between them.
3. **`M:none` over USB** — you have seen it, but confirm it still emits with
   `HAVE_POT=0`, since that path changed.
4. **The `panel_monitor.py` watchdog**, now that it arms at connect: point it at
   a port with nothing attached and confirm it complains within 30 s.

The GPIO23 → Pi link and X1202 power stay on the operator's side; I will verify
those from here once the wire is in.

---

## 7. Unchanged working agreement

Still no edits to `control_panel.cpp` or `polarity_test.cpp` from your side —
you have kept to that and it has worked. Deltas in a handoff, verbatim output
over paraphrase, and ask me to run a command rather than inferring Pi behaviour.
