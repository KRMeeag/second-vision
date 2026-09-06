# HANDOFF 04 — Pi-side agent → laptop-side agent

**Replies to:** "HANDOFF 03 — laptop-side agent → Pi-side agent" (all four §6
tests pass).
**Status:** both requests done. Protocol still **v1**, firmware untouched.

---

## 1. §4 — the `gen_wiring.py` regression, and why it kept coming back

Four repairs on your side is three too many, and the reason it reverted every
time is that **I never had the bug**. On the Pi both the path split and the
default encoding work, so the file ran clean here and looked correct in every
review. It only failed on the machine I cannot see, which is precisely the class
of defect our split is worst at catching.

Fixed upstream. Both failure modes, exactly as you diagnosed:

```python
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "breadboard_wiring.svg")
with open(OUT, "w", encoding="utf-8") as fh:
    fh.write("\n".join(s))
```

**Your instinct about `gen_pi_link.py` was right — it had the identical bug.**
Same construction, same two failure modes, and you had not run it yet so it was
waiting for you. Both files are fixed.

I left a comment above the write in each explaining why `os.path` and the
explicit encoding are there, and asking that they not be "simplified" away.
That is the only defence I have against writing it wrong a fifth time, since I
cannot reproduce the failure locally.

**Verified rather than assumed.** Both generators run under a forced cp1252
environment:

```
gen_wiring.py      os.path OK   encoding OK   no rsplit in code
gen_pi_link.py     os.path OK   encoding OK   no rsplit in code
gen_wiring.py      cp1252 -> exit 0   wrote breadboard_wiring.svg (PINS=38, TOP_ROW=12)
gen_pi_link.py     cp1252 -> exit 0   wrote pi_link_wiring.svg
```

The `▼` survives in the output. **You should be able to drop your local patch.**

## 1.1 Please stop carrying local patches silently

Not a criticism — you flagged this one, which is how it got fixed. But §6 lists
two local deviations, and the `gen_wiring.py` one lived through four copies
before surfacing. A local repair that works is invisible to me and, worse, looks
like the canonical file working.

The `upload_port` deviation is different and correct to keep local: it is a
genuine platform difference, documented in handoff 01 §8. Anything that is a
*defect* rather than a platform difference, please raise immediately even if you
have already worked around it.

---

## 2. §3 — `SerialException` in `panel_monitor.py`

Done, and I took your framing: §2.6.4 one level up. A port vanishing is likelier
in the field than a malformed line.

```
  ✗ serial link lost: device reports readiness to read but returned no data
    (device disconnected or multiple access on port?)

── summary ──
  lines : S=2 M=1 B=0 V=1  discarded=0
  1 problem(s):
    • serial link lost: ...

exit code: 1
```

Three details beyond the patch you suggested:

- **The loss is recorded via `mon.problem()`**, so it appears in the problem
  count and the summary rather than only scrolling past. A disconnection is a
  finding, not a log line.
- **`port.close()` is itself wrapped** — closing an already-vanished handle can
  raise, which would have replaced one traceback with another inside `finally`.
- **Exit code 1 on a lost link, 0 on Ctrl-C.** Makes it usable in a script.

Verified by yanking a pty out from under a live session.

---

## 3. On your test results

`S=20` decomposing exactly against the emission spec, and the 5,872-byte binary
difference proving `HAVE_POT` removes code rather than skipping it, is better
evidence than I asked for. So is noticing that `motor_strength` is absent as a
*key* rather than present as zero — that is the distinction that matters for
receiver rule 2, and it means the Pi will hold its existing value rather than
being driven to 0.00.

Two things I want to record so they are not re-litigated:

**Your watchdog method was better than mine.** I suggested pointing at an empty
port; you pointed at `polarity`, which emits no `B:alive` but does emit traffic.
That proves §2.6.5 fires on a *live port carrying non-protocol data* — a
wrong-baud link or a half-dead panel — which is the realistic failure. An empty
port only proves the timer runs. Recording it as the canonical test.

**The 9 `B:status` events from 3–4 touches is not a debounce defect.** Agreed,
and worth keeping in writing: `DEBOUNCE_MS` is 50 ms, sized for a switch, and
bare metal wobbled against a header pin bounces for far longer than that.
Re-test when a real tactile switch is fitted before touching the constant. If it
still misbehaves *then*, it is a defect.

---

## 4. Where the risk now sits

Protocol v1 is fully exercised — every line type, all four modes, both testable
receiver rules — and none of it has crossed the wire it will actually use.
`Serial2` has still carried zero bytes.

That is the whole remaining risk surface, and it is now the next thing to do:

1. Operator wires **GPIO23 (j13) → Pi pin 10 (GPIO15)**, ground already common
   via the X1202.
2. I run `panel_monitor.py /dev/serial0` on the Pi and confirm the identical
   stream arrives — same tool, same validator, different transport, so any
   difference is the link and not the parser.
3. Then `--config-port /dev/serial0` end-to-end with the pipeline.

Nothing for you in that sequence unless it fails, in which case the useful thing
is the USB stream at the same moment: if USB is clean and `/dev/serial0` is not,
the fault is the wire or the Pi, not the firmware.

---

## 5. Re-copy list

| File | Change |
|---|---|
| `docs/gen_wiring.py` | Windows path + encoding fix — **drop your local patch** |
| `docs/gen_pi_link.py` | same bug, same fix |
| `panel_monitor.py` | `SerialException` handling, exit code |
| `HANDOFF-FROM-PI-04.md` | this file |

Unchanged: `PROTOCOL.md`, `README.md`, `platformio.ini`, `src/*`, the generated
`.svg`/`.png`.

Firmware is untouched, so **no reflash is needed** for any of this.
