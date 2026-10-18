# Demo device checklist: what was built, what was tested, what you do on the device

For the person running the device session. Demo day is **2026-10-19**. **Last updated 2026-10-19,
after item F5.** Whoever finishes an item updates its row in section 1 and adds or firms up its steps
in section 4 (the session that builds the item does this; see DEMO-HANDOFF.md).

**Which file says what**

| You want | Look at |
|---|---|
| What was built, what was tested, what is still unverified, and what to do on the device | **This file** |
| The plan, the rules, the user's decisions (what each item must do) | [DEMO-TRACK.md](DEMO-TRACK.md) |
| The detail behind an item (what changed, every test, known limits) | `reports/DEMO/F1.md`, `F2.md`, `F3.md` |
| Raw test output | `reports/DEMO/evidence/` |
| Where things stand for the next Claude session | [DEMO-HANDOFF.md](DEMO-HANDOFF.md) |

If this file and the code disagree, the code is right: tell the session, and fix the file.

---

## 1. Status at a glance

"Device check" means a person did it on the real glasses. **Nothing has been checked on the device yet.**

| Item | What it does for the wearer | Built? | Automated tests | Device check |
|---|---|---|---|---|
| **F0** Spoken camera alerts | Camera lost: "Critical Error: Camera cannot be reached" at once, then every 10 s. Back: "Camera has been reconnected" once. | Done, **reviewed 2026-10-19**: no defect found, two stale comments fixed | 161 camera tests run (unit and real-GStreamer) and 14 opt-in device tests **not yet run**. [F0.md](reports/DEMO/F0.md) | ☐ not done |
| **F1** Vibration patterns, and "alerts override the switches" | Three patterns (reconnect *beep beep*, camera *beep beep* longer, earphones *strong weak* ×3). A failsafe alert is spoken with the speech switch off, and a pattern plays with the vibration switch off, never under 30 % strength or volume. | Done | 63 new (42 + 21), 4 older tests rewritten. [F1.md](reports/DEMO/F1.md) | ☐ not done |
| **F2** Glasses board lost / back | Lost: "Vibration is not working" (every 30 s while depth is on, once while off). Back: reconnect *beep beep* and a "connected" sentence. | Done | 26 new. [F2.md](reports/DEMO/F2.md) | ☐ not done |
| **F3** Earphones lost / back | Lost: *strong weak* ×3 (every 30 s while detection is on, once while off), never spoken. Back: reconnect *beep beep* and a "connected" sentence through the returned earphones. | Done | 50 new. [F3.md](reports/DEMO/F3.md) | ☐ not done |
| **F4** Camera alert vibration | The camera *beep beep* (two long pulses) with each spoken camera alert, so every 10 s. None for the return sentence. | Done | 20 new. [F4.md](reports/DEMO/F4.md) | ☐ not done |
| **F5** Camera blocked / too dark / too bright | Lens blocked or too dark, or facing bright light, for about 3 s: a spoken warning that obstacle warnings may be missing, every 30 s; "Camera view is back." once. | Done; thresholds are **placeholders** until real footage | 50 new (3 on real GStreamer); one check costs about 1 ms. **Performance run pending.** [F5.md](reports/DEMO/F5.md) | ☐ not done |
| **F6** Control panel lost / back | Lost (silent for 30 s, or cannot be opened): "Controller board cannot be reached. Using the current settings." at once, then every 5 minutes. Back: a "connected" sentence. Settings stay as they were. | Done | 22 new. [F6.md](reports/DEMO/F6.md) | ☐ not done |
| **F7** (if time) An alert finishes before an urgent warning cuts it | — | Not built (optional) | 4 expected-failure tests wait for it (`tests/uat/test_t00a_alert_scheduler.py`) | no device check |
| **F8** The demo run | One written sequence and a rehearsal with you on the device. Replaces most of section 4 once it exists. | **Not started** | — | ☐ |

The "connected" sentence (decision D-17): **"Everything is connected"** is said only when nothing else is still
missing. If the camera, the glasses board or the earphones are still out, the part that returned says its own
sentence instead ("Vibration is working again", "Earphones connected"). The camera always says "Camera has been
reconnected".

**Full test suites, last run (after the status display):** the Poetry environment gave 1021 passed, 216 skipped, 0 failed;
`my_hailo_env` gave 1120 passed, 117 skipped, 0 failed (`reports/DEMO/evidence/STATUS-*-full.txt`).

---

## 2. What the automated tests prove, and what only the device can

**They prove** (with fake boards, a fake clock and fake sound-card folders): the wording, how often
each alert repeats, what happens when a switch is off or a knob is low, the "connected" sentences when several parts are
out, that no setting of yours is changed, and that the right motor packets go to a fake glasses board. Each F item also had its code
deliberately broken in a scratch copy to confirm a test notices (F0 14 ways, F1 8, F2 9, F3 20, F4 9, F5 24, F6 14; all caught).

**Only the device can tell you:**

- Whether you can **feel** each pattern, and tell the reconnect *beep beep* from the camera one. In particular, at the
  30 % floor the strong pulse is duty 76, below the motor start threshold the code assumes (90, never measured), so it
  may be too faint. This is the biggest open question from F1.
- Whether the **earphones really speak** the sentence just after they return (they may need a moment).
- Whether `usb_sim.py detach earphones` makes the sound card vanish the way a real unplug does (F3 assumes it does).
- Whether the glasses board really stops answering when its cable is pulled, and how long that takes.
- Whether the camera-view thresholds (F5) fit this camera: a covered lens flagged, a dim room not. And whether the
  once-a-second check costs any frame rate on the real pipeline (Part J).
- The real timing of all of it, and how loud 30 % is.

---

## 3. Before you start

| # | Who | Do | Expect |
|---|---|---|---|
| 1 | You | Ask the teammate if the device is free (they may be testing depth) | A yes |
| 2 | Session | `systemctl is-active second-vision` and `pgrep -af "Parallel-Depth\|pytest"` | Tells you who is using it |
| 3 | You | `sudo systemctl stop second-vision` (the boot service holds the camera, the Hailo chip and the glasses board's port) | `systemctl is-active second-vision` says `inactive` |
| 4 | You | Connect and put on: the camera, the glasses board, the control panel, the earphones | All attached |
| 5 | Session | Note the date, and that the program runs from `worktrees/kenth` (the boot service runs the *base* checkout, so it does not have this work) | — |

**When you finish:** `sudo systemctl start second-vision` (until then the device does nothing at boot). If the camera
or earphones seem gone after a simulated unplug: `sudo .claude/tools/usb_sim.py attach --all`, or replug them. The tool
also re-attaches by itself when its `--for` time is up.

---

## 4. The session, in order

**On screen and in the terminal** (STATUS-DISPLAY.md): every step below that is spoken also shows. The cv2 window's
bottom strip turns red for the part that is out (`GLASSES LOST`, `VIEW BLOCKED`, ...), a red banner at the top names it, and
`Said: "..."` shows the sentence for 8 s. The terminal prints one `[CHECK]` line per change. So a step can be confirmed
by eye as well as by ear, and the panel sees it on the TV.

"Session" = the Claude session runs the command. "You" = your hands, eyes, ears. Tick the box on each step;
write anything odd in the Notes line. For the narrative (what was heard and felt), also put a few lines
in the item's report under a "Device check result" heading.

### Part A · The patterns on the motors (F1). The program is **not** running yet

The bench script opens the glasses port itself, so do this before starting the program.

| # | Who | Do | Expect | ✓ |
|---|---|---|---|---|
| A1 | Session | Run the bench script in [F1.md → "Device check"](reports/DEMO/F1.md) (plays each pattern at strength 1.0, then at 0) | "UART Connection to ESP32 is WORKING!", then six patterns | ☐ |
| A2 | You | At strength 1.0: feel *reconnect*, *camera*, *earphones* | Three different feels. Reconnect = two short pulses; camera = two long pulses; earphones = strong, weak, three times | ☐ |
| A3 | You | At strength 0 (the 30 % floor): feel them again | Still felt, and still different. **If a pattern is not felt, say so: the fix is one line (F1.md, the scaling question)** | ☐ |
| A4 | You | Is the weak pulse of the earphones pattern felt? | Yes | ☐ |

Notes:

### Part B · Start the program, everything connected

| # | Who | Do | Expect | ✓ |
|---|---|---|---|---|
| B1 | Session | `cd ~/Projects/Second-Vision/worktrees/kenth && scripts/sv-main.sh 2>&1 \| tee docs/testing/reports/DEMO/evidence/DEVICE-SESSION.log` (one log for the whole session) | Log shows "Earphones worker started" and "UART Connection to ESP32 is WORKING!" | ☐ |
| B2 | You | Listen for a minute | **No** failsafe sentence and no failsafe pattern | ☐ |
| B3 | You | Flip a mode rocker | You hear the mode announced in the earphones (proves speech works through them) | ☐ |
| B4 | You | Look at the cv2 window and the terminal | A strip along the bottom: `CAMERA OK  VIEW OK  GLASSES OK  EARPHONES OK  PANEL OK`, all green (VIEW turns OK after its first check). The terminal printed one `[CHECK] Camera: connected | ...` line. Check it is readable on the TV from where the panel sits | ☐ |

Notes:

### Part C · Earphones lost and back (F3). DETECT rocker on

| # | Who | Do | Expect | ✓ |
|---|---|---|---|---|
| C1 | Session | `sudo -n .claude/tools/usb_sim.py detach earphones --for 70` | — | ☐ |
| C2 | Session | In another shell: `cat /proc/asound/cards` | The SPACETOUCH sound card is gone within a second or two. **If not, stop and tell the session**: the presence rule needs another look | ☐ |
| C3 | You | Feel and listen | *strong weak* ×3 on all three motors about 1 to 2 s after the detach, and **no speech**. Again at about 30 s and 60 s | ☐ |
| C4 | You | After the 70 s the earphones return | The reconnect *beep beep*, then **"Everything is connected" heard in the earphones**. Write down if it is cut off or missing while the *beep beep* still comes | ☐ |
| C5 | Session | `detach earphones --for 40`; **You** switch DETECT off at about 15 s | One pattern, none at 30 s | ☐ |
| C6 | Session | With DETECT off from the start: `detach earphones --for 20` | Exactly one pattern | ☐ |
| C7 | You | Vibration switch **off**; Session: `detach earphones --for 20` | The pattern is still felt (a failsafe pattern overrides the switch). Switch it back on afterwards | ☐ |
| C8 | You | One **real** unplug of the earphones' cable, then plug back | Same as C3/C4 | ☐ |

Log lines to look for: `[EARPHONES] not found`, `[ALERT] raised 'earphones-missing' ... (silent)`,
`[PATTERN] playing 'earphones'`, `[EARPHONES] found again`, `[PATTERN] playing 'reconnect'`,
`[ALERT] raised 'earphones-reconnected'`.

Notes:

### Part D · Glasses board lost and back (F2). DEPTH rocker on

| # | Who | Do | Expect | ✓ |
|---|---|---|---|---|
| D1 | You | Unplug the glasses board's cable (or its power) | About 2.5 s later: **"Vibration is not working"**. It repeats every 30 s. The motors stop | ☐ |
| D2 | You | Switch DEPTH off | No more repeats | ☐ |
| D3 | You | Plug the cable back in | Reconnect *beep beep* on all three motors, then "Everything is connected" | ☐ |
| D4 | You | Repeat D1 and D3 with the vibration switch **off** | The *beep beep* still comes on return. Switch it back on | ☐ |

Log lines: `[SERIAL] ESP32 stopped acknowledging`, `[ALERT] raised 'glasses-missing'`, `[PATTERN] playing 'reconnect'`.

Notes:

### Part E · Camera lost and back (F0), and its vibration (F4)

| # | Who | Do | Expect | ✓ |
|---|---|---|---|---|
| E1 | Session | `sudo -n .claude/tools/usb_sim.py detach camera --for 40` | — | ☐ |
| E2 | You | Listen | **"Critical Error: Camera cannot be reached"** at once, then every 10 s. Vision and depth vibration stop | ☐ |
| E3 | You | After the 40 s the camera returns | The repeating alert stops. **"Camera has been reconnected"** once, about a second after vision is running. Vibration and detection are back | ☐ |
| E4 | You | Again, with the **speech switch off** | The camera alert is still spoken (quieter if your volume knob is low, never below 30 %); object announcements stay silent. Switch speech back on | ☐ |
| E5 | You | During E2, feel the motors | **With each "Critical Error" sentence, two long pulses (the camera *beep beep*) on all three motors, starting with the sentence**, every 10 s (F4). Longer than the reconnect one from Part D | ☐ |
| E6 | You | At the return (E3) | "Camera has been reconnected" and **no** pulse | ☐ |
| E7 | You | Vibration switch **off**, `detach camera --for 25` | The pulses still come with each sentence. Switch it back on | ☐ |

Log lines for the camera: `[ALERT] raised 'camera-missing'` and `[PATTERN] playing 'camera'` (once per sentence).

**Optional automated run first** (Session; stop the program from Part B, since this starts its own copy):
`my_hailo_env/bin/python -m pytest tests/test_camera_alerts_device.py -v -s --run-device`. It does ten loss-and-return
cycles, ten one-second bounces, a held camera and Ctrl+C with the alert up, and takes minutes. See [F0.md](reports/DEMO/F0.md).

The real camera pull and replug, and a start with no camera, are the hands-on cases T-03a and T-03b in
[MANUAL-TESTS.md](MANUAL-TESTS.md); the final rehearsal (F8) covers them too.

Notes:

### Part F · Several parts out at once ("everything is connected" must be true)

The earphones are the only way you hear speech, so a sentence said while they are out cannot be heard. These
two orders make the sentence audible.

| # | Who | Do | Expect | ✓ |
|---|---|---|---|---|
| F-1 | Session | `detach camera --for 60`, then about 5 s later `detach earphones --for 20` | — | ☐ |
| F-2 | You | The earphones return at about 25 s (the camera is still out) | **"Earphones connected"**, not "Everything is connected" | ☐ |
| F-3 | You | The camera returns at 60 s | "Camera has been reconnected" | ☐ |
| F-4 | You | Unplug the glasses board's cable (keep it out at least 5 s), then Session: `detach earphones --for 30`; plug the cable back while the earphones are out; then the earphones return | The glasses *beep beep* comes (its sentence is said into nothing). When the earphones return: *beep beep*, then **"Everything is connected"** | ☐ |

Notes:

### Part G · Your switches and knobs are left alone (alerts override them once)

| # | Who | Do | Expect | ✓ |
|---|---|---|---|---|
| G1 | You | Set the volume knob to 0 and the speech switch off, then repeat E1 to E3 | The camera alerts are still heard, at a modest but audible level | ☐ |
| G2 | You | Afterwards, look at the panel | The switch and the knob are where you left them. Object announcements still obey the speech switch | ☐ |
| G3 | You | Strength knob at 0, vibration switch off: depth vibration | Depth vibration is **off** (the pattern override did not leak into the depth stream) | ☐ |

Notes:

### Part H · Control panel lost and back (F6)

The panel sends a heartbeat every 10 s and is called lost after more than 30 s of silence, so the sentence comes
**20 to 31 s after you unplug it**, and the return is noticed with its next heartbeat, **up to 10 s after you plug it in**.

| # | Who | Do | Expect | ✓ |
|---|---|---|---|---|
| H1 | You | Unplug the panel's wire (or its power). Note the time | About 20 to 31 s later: **"Controller board cannot be reached. Using the current settings."** Nothing else changes: depth and detection go on as the panel left them | ☐ |
| H2 | You | Speech switch off (only if you can still reach it; skip otherwise) and wait for the next time it would be said, or repeat H1 | The sentence still comes (a failsafe alert overrides the switch) | ☐ |
| H3 | You | Plug the panel back in | Within about 10 s: **"Everything is connected"**. No vibration | ☐ |
| H4 | Session | With the panel out, `detach camera --for 90`; plug the panel back first | **"Controller board connected"**, not "Everything is connected". Then "Camera has been reconnected" when the camera returns | ☐ |
| H5 | You | Optional: leave the panel out for 5 minutes | The sentence is said again at 5 minutes | ☐ |

Log lines: `[CONFIG] No heartbeat from the control panel`, `[ALERT] raised 'panel-missing'`,
`[CONFIG] Control panel is back`, `[ALERT] raised 'panel-reconnected'`.

Notes:

### Part I · Camera blocked, too dark or too bright (F5). Mode "both"

Each takes **three checks in a row, about 3 s**, to start or to stop. The thresholds are first guesses: copy the
`[VIEW]` lines (they hold the measured brightness) so they can be set from this camera.

| # | Who | Do | Expect | ✓ |
|---|---|---|---|---|
| I1 | You | Cover the lens with a hand | On screen first a yellow `Camera view: looks dark (1 of 3)`, `(2 of 3)`, then about 3 s later the red `CAMERA VIEW: BLOCKED OR TOO DARK` with the measured brightness, and **"Camera is blocked or it is too dark. Obstacle warnings may be missing."**, again every 30 s | ☐ |
| I2 | You | Uncover it | About 3 s later: **"Camera view is back."** once | ☐ |
| I3 | You | The same with the lens cap | Same as I1 and I2 | ☐ |
| I4 | You | Shine a torch straight into the lens | **"Camera is facing very bright light. Obstacle warnings may be missing."** If nothing comes, note the `[VIEW]` mean: the torch may not fill the view | ☐ |
| I5 | You | A dim room, then a normal room, some seconds each | **No** alert. Note any `[VIEW] the camera looks dark` line | ☐ |
| I6 | You | Both rockers off, lens covered | Nothing (no obstacle warnings to miss) | ☐ |
| I7 | You | Lens covered, then pull the camera | The view sentence stops; "Critical Error: Camera cannot be reached" follows | ☐ |

Log lines: `[VIEW] the camera looks dark (mean ..., 95th percentile ...)`, `[VIEW] camera view is now dark`,
`[ALERT] raised 'camera-view-dark'`, `[ALERT] raised 'camera-view-back'`.

Notes:

### Part J · Performance with the view check on (F5). Session; nothing else running

Stop the program from Part B first: this starts its own copy, the way the boot service does, for about 4 minutes.

| # | Who | Do | Expect | ✓ |
|---|---|---|---|---|
| J1 | Session | `my_hailo_env/bin/python docs/testing/tools/perf_run.py --duration 180 --label f5-view-check --out docs/testing/reports/DEMO/evidence` | A clean run and stop; its summary file | ☐ |
| J2 | Session | Compare with `reports/T-15/evidence/perf-both-20min.txt` (frame rate median 29.6 fps; camera-to-callback delay median 104 ms, p95 127 ms) | Both within run-to-run noise | ☐ |

---

## 5. After the session

1. `sudo systemctl start second-vision` (section 3).
2. Tell the session the results. It records them in each item's report under "Device check result" and ticks the rows in section 1.
3. A failed or doubtful step is recorded as it happened, with the log line, and never rounded up to a pass.
4. Anything unexpected goes to the session straight away, even if it is outside the step.
