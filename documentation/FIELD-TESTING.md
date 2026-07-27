# FIELD-TESTING.md — Real-World Scenarios for Tuning the TTS Priority System

Purpose: extensively test and tune the announcement prioritization / preemption /
pacing behavior (the queue revamp — see `.agents/handoff_v3.md`) outside a single
room, in schools, malls, and streets. Best case, worst case, edge cases, and
deliberately-unaccounted cases.

Every tunable lives in one file: `src/second_vision/core/priority.py` (plus
`CONFIDENCE_THRESHOLD`, `MIN_CONFIRMATION_SECONDS`, `MIN_REPEAT_INTERVAL`,
`STALE_TRACK_FRAMES`, `HEAD_TURN_*` in `src/second_vision/pipeline/callbacks.py`).
Change a value, re-run, re-test the relevant scenario.

---

## 0. Before you go — safety, caveats, setup

**Safety (read first):**
- The device is **under test** — do **not** rely on it for real obstacle avoidance.
- Prefer **sighted testers** wearing the rig. If a visually-impaired teammate tests,
  they keep their **white cane** and have a **dedicated spotter** the whole time.
- **Streets/traffic**: stay on the sidewalk. Start with **parked or slow-moving**
  vehicles. Never step into a roadway to "trigger" a car detection. One person's only
  job is watching for real hazards, not the device.
- Malls/schools: get permission where needed; don't film bystanders' faces
  identifiably if your school/mall rules forbid it.

**Current scope caveats:**
- **TTS only.** Depth → haptic motors is still a stub, so unclassifiable obstacles
  (poles, glass doors, curbs, walls, hanging signs) are **silent** right now. That's
  expected — note them, but they're the depth channel's job later.
- Announcements are limited to the detector's classes (YOLO/COCO). Anything off-list
  is invisible to the TTS channel.

**Recording ground truth (so you can correlate what it SAW vs SAID):**
- Run with a **timestamped stdout log** on the Pi, e.g.:
  ```bash
  ./scripts/run.sh --input usb 2>&1 | while IFS= read -r l; do echo "$(date +%H:%M:%S.%3N) $l"; done | tee ~/svtest_$(date +%F_%H%M).log
  ```
  Every announcement prints as `[TTS] '...'`; the log is your transcript.
- Simultaneously record the **scene** (a phone/GoPro pointed roughly where the camera
  looks) with **audio**, so you can line up "what was in front" with "what it said."
- If a display is attached, the cv2 overlay shows the zone lines, per-object IDs, and
  FPS — useful for confirming zone/boundary behavior.
- After each run, note **subjective feel**: too talkative? too quiet? did it interrupt
  well? did it miss something important?

---

## 1. How to read results — symptom → parameter tuning map

Use this table after every session. Symptoms are what you HEAR/observe; the fix is the
direction to nudge the constant.

| Symptom observed | Likely parameter | Adjust |
|---|---|---|
| Machine-gun / rapid-fire chatter | `MIN_UTTERANCE_GAP` | ↑ (e.g. 0.5 → 0.8) |
| Feels laggy / misses fast events | `MIN_UTTERANCE_GAP` | ↓ |
| Same object nags too often ("still" spam) | `MIN_REPEAT_INTERVAL` | ↑ (10 → 15) |
| Forgets a persistent object is still there | `MIN_REPEAT_INTERVAL` | ↓ |
| In a crowd, same object dominates; others never spoken | `RECENCY_PENALTY_MAX` ↑ / `RECENCY_DECAY_SECONDS` ↑ | stronger/longer suppression of just-spoken items |
| A just-spoken object never comes back even when alone | `RECENCY_DECAY_SECONDS` | ↓ |
| Interrupts too eagerly on trivial changes (twitchy barge-in) | `PREEMPT_MARGIN` | ↑ (0.75 → 1.2) |
| Something clearly more important won't cut in | `PREEMPT_MARGIN` ↓ / add to `URGENT_CLASSES` | |
| Too many "urgent" barge-ins, feels alarmist | `URGENT_ABS_THRESHOLD` | ↑ (3.2 → 3.6) |
| A very close centered object DOESN'T cut through | `URGENT_ABS_THRESHOLD` ↓ / check `URGENT_CLASSES` | |
| Wrong things ranked as important | `CLASS_WEIGHTS`, `ZONE_WEIGHTS`, `W_*` | rebalance |
| Announces phantoms / flickery false positives | `CONFIDENCE_THRESHOLD` ↑ / `MIN_CONFIRMATION_SECONDS` ↑ | |
| Misses real objects (small/far/poor light) | `CONFIDENCE_THRESHOLD` ↓ | |
| Slow to announce a real fast-appearing object | `MIN_CONFIRMATION_SECONDS` | ↓ |
| Spams "leaving to X" when you turn your head | `HEAD_TURN_RATIO` ↓ / `HEAD_TURN_SUPPRESS_SECONDS` ↑ | |
| Zone flips back and forth for a boundary-standing object | zone hysteresis band (`*_BOUNDARY`) | widen the gap |
| Two same objects not called "multiple" | confirmation timing / `STALE_TRACK_FRAMES` | verify both confirmed |

**Golden rule of tuning:** change **one** parameter at a time, re-run the **same**
scenario, and log before/after. The two goals are always in tension — *inform enough*
vs *don't overwhelm* — so you're finding a balance, not a "correct" number.

---

## 2. Best-case / baseline (does it behave in the open?)

**B1 — Single approacher, open space** *(schoolyard, empty plaza)*
- Setup: wide open area, one person walks slowly toward the tester from ~10 m, straight
  down the center.
- Do: stand still, let them approach, pass, and leave.
- Expect: one clean first announcement once confirmed (~0.3 s in), periodic "person
  still center" at the repeat cadence, no chatter, no layering.
- Pass: exactly one voice at a time; "still" reminder roughly every `MIN_REPEAT_INTERVAL`.
- Tunes: `MIN_CONFIRMATION_SECONDS`, `MIN_REPEAT_INTERVAL`, `MIN_UTTERANCE_GAP`.

**B2 — Cross-and-leave** *(hallway, sidewalk)*
- Setup: person starts in your center, walks out to your left or right and away.
- Expect: "person leaving to left/right" once on the transition, then silence.
- Pass: the leaving phrase fires **once**, cleanly, not repeatedly.
- Tunes: zone boundaries/hysteresis, leaving-center logic.

**B3 — Calm stroll** *(quiet mall wing, park path)*
- Setup: tester walks a fixed 30–60 s route with a few scattered people/objects.
- Expect: occasional, calm announcements — informative but restful.
- Pass: you'd describe the cadence as "calm," not "nagging" or "silent."
- Tunes: `MIN_UTTERANCE_GAP`, recency, `MIN_REPEAT_INTERVAL`.

---

## 3. Worst-case / overwhelm & urgency (the hard stuff)

**W1 — Dense crowd (the overwhelm test)** *(mall concourse, school corridor between classes)*
- Setup: stand at the edge of a busy flow of people; many bodies, constant motion.
- Do: hold position 60–90 s, then walk through slowly.
- Watch for TWO failure modes:
  1. **Overwhelm** — non-stop talking, can't think. → raise `MIN_UTTERANCE_GAP`,
     strengthen recency, raise `MIN_REPEAT_INTERVAL`.
  2. **Starvation** — because only the single highest-priority item is kept per moment,
     is anything *important* going unspoken for too long? → check recency decay so the
     spotlight rotates; consider class/zone weights.
- Pass: the tester feels *aware of the busiest/closest threats* without being buried.
  This is the central best-vs-worst tradeoff — expect to spend the most time here.
- Tunes: `MIN_UTTERANCE_GAP`, `RECENCY_*`, `MIN_REPEAT_INTERVAL`, `ZONE_WEIGHTS`.

**W2 — Vehicle at a crossing (preemption / urgency)** *(quiet street corner, SAFELY from the curb)*
- Setup: tester on the sidewalk facing a driveway/parking exit. A teammate drives a car
  slowly into the center of view, or use cars already pulling out. **Spotter mandatory.**
- Ideal trigger: while the device is mid-"person left" for a pedestrian, the car enters
  center.
- Expect: the car (urgent class + center) **barges in** — the current utterance is cut
  and "car center" plays immediately.
- Pass: urgent car interrupts within a fraction of a second; it does NOT wait its turn.
- Tunes: `URGENT_CLASSES`, `URGENT_ABS_THRESHOLD`, `PREEMPT_MARGIN`, and try the
  `PREEMPT_USE_TIER = False` (pure-margin) variant here to compare feel.

**W3 — Head-scanning a busy scene** *(mall atrium, street with shops)*
- Setup: stand in a populated area and pan your head left↔right as a user naturally would.
- Expect: the collective apparent motion is recognized as a head turn — you do **not**
  get a flood of "leaving to left/right" for every object.
- Pass: panning is quiet; genuine object transitions still announced when you're still.
- Tunes: `HEAD_TURN_RATIO`, `HEAD_TURN_DELTA`, `HEAD_TURN_MIN_TRACKED`,
  `HEAD_TURN_SUPPRESS_SECONDS`.

**W4 — Rapid oscillation (the original layering bug, in the wild)**
- Setup: have a teammate walk quickly back and forth across your center↔side boundary,
  or weave in front of you.
- Expect: **no overlapping speech ever** — one utterance at a time; sensible
  leaving/still phrasing, not a stutter of half-spoken words.
- Pass: zero layering (this is the bug the revamp fixed — confirm it stays fixed).
- Tunes: mostly a correctness check; if phrasing thrashes, look at hysteresis and
  `MIN_UTTERANCE_GAP`.

---

## 4. Edge cases

**E1 — Boundary loiterer.** Someone stands right on a zone line (~22–25% or 75–78% of
frame width). Expect a **stable** zone (hysteresis holds it), not flip-flopping. Tune the
boundary band if it jitters.

**E2 — "Multiple X."** Two+ people (or two of the same class) stand together in one zone.
Expect "multiple person center" once both are confirmed. If it says singular, they may
not both be clearing `MIN_CONFIRMATION_SECONDS`, or one keeps getting evicted
(`STALE_TRACK_FRAMES`).

**E3 — Blip / quick cross.** Someone darts across the far edge of view for <0.3 s.
Expect **no announcement** (the confirmation gate eats it). If it blurts a phantom, raise
`MIN_CONFIRMATION_SECONDS` or `CONFIDENCE_THRESHOLD`.

**E4 — Sudden close-up.** A person steps right in front of the tester, filling the center
of the frame. Expect this to cross `URGENT_ABS_THRESHOLD` (big area + center + confident)
and behave as **urgent** — barging in if something was talking. Verify the threshold
feels right: does "person right in my face" cut through? Should it?

**E5 — Priority duel.** Arrange a routine object (a person standing calmly to your left,
generating "still" reminders) and then introduce a higher-priority one (a car in center,
or someone stepping close-center). Expect the higher-priority one to win the moment and
preempt if needed. Tune `PREEMPT_MARGIN` / weights until the "who wins" calls feel right.

**E6 — Persistent companion.** A friend walks **alongside** the tester in center for a
full minute. Expect "person still center" every `MIN_REPEAT_INTERVAL`. Decide as a team:
is a steady companion worth repeating? This may motivate a future "quiet a stable,
non-approaching object" rule — log your judgment.

**E7 — Mode announcement over a busy scene** *(only if the Arduino config panel is wired)*.
Flip the mode switch during W1. Expect "…​mode" to be heard **immediately** (announcements
are urgent) even amid crowd chatter.

---

## 5. Unaccounted / discovery cases (find what the design doesn't handle)

These aren't expected to "pass" — they're to surface gaps. Log everything.

**U1 — Fast approacher from the side (the reserved-hook gap).** A cyclist/jogger/scooter
approaches quickly from your left or right. Because approach-velocity is **not yet wired**
(`W_APPROACH = 0`) and side zones are down-weighted, it likely won't be urgent until it's
already large/close. **Measure how late** the urgent call comes — this is the concrete
evidence for whether to prioritize wiring the approach term next.

**U2 — Unclassifiable obstacles.** Walk past glass doors, poles, planters, low bollards,
hanging signs, a wall you approach head-on. Expect **silence** (no class → no TTS). Note
which of these felt *dangerous to miss* — that's the requirements list for the depth/haptic
channel.

**U3 — False positives from imagery.** Posters/ads/mannequins/screens showing people or
cars; mirrored columns and glass storefronts (reflections of real people). Expect some
phantom announcements. Log frequency → informs `CONFIDENCE_THRESHOLD` /
`MIN_CONFIRMATION_SECONDS`, and whether certain locations are just hostile.

**U4 — Lighting extremes.** Direct outdoor sun / strong backlight (walking toward a bright
exit) vs dim indoor corners. Watch for confidence collapse (missed real objects) or
flicker. Note conditions where the detector struggles.

**U5 — Group moving as one.** A family or tour group walking together. Does it read as a
head-turn (collective motion) and over-suppress? Does "multiple" fire sensibly? Does one
person hog the spotlight?

**U6 — Quiet fast vehicles.** E-scooters / bikes on a shared path — fast, near-silent, and
a real hazard. Combine U1 + W2 observations; strong motivation for the approach term and
for `URGENT_CLASSES` membership.

**U7 — Audibility in noise.** In a genuinely loud mall/street, **can the tester even hear
the announcements** over ambient noise (bone-conduction vs the environment)? Not a code
parameter, but a real-world UX finding — note volume/clarity limits and whether long
phrases get lost.

**U8 — Track-ID churn under occlusion.** People repeatedly passing behind pillars/each
other. Watch for the same object being re-announced as "new," or stale state leaking. Ties
to `STALE_TRACK_FRAMES` and the tracker settings in `app.py`.

---

## 6. Per-run log template

Copy one block per scenario run:

```
Scenario: ____   Location: ____   Date/time: ____   Testers/spotter: ____
Params changed since last run: ____ (one at a time!)
What the scene contained: ____
What it announced (from log): ____
Layering observed? (Y/N): ____
Missed anything important? What: ____
Over-talked / under-talked? ____
Urgent/preemption behaved? ____
Subjective feel (1 calm – 5 overwhelming): ____
Next parameter to try: ____
```

Bring the filled logs back and update `src/second_vision/core/priority.py` (and the
`callbacks.py` constants) accordingly. Record the settled values and the reasoning in
`.agents/handoff_v3.md` so the next session knows why each number is what it is.
