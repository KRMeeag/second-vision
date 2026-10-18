# Demo track: the critical validations, ready for demo day

Set up 2026-10-18 with the user. Demo day is **tomorrow**; about **15 working hours** remain.

This replaces the Lead / Developer / Tester loop **for the items below only**, because that loop, built
for thesis-grade evidence, would take 3 to 5 days for them. Each item is done in a **fresh, short Claude
Code session** with the user in the loop: implement, unit-test, one device check, a short note. One
validation pass over the whole demo at the end replaces the per-item Tester. After demo day the agents go
back to the workbook at full rigour, starting from this file.

**To start an item, open a fresh session and say:** "Read `docs/testing/DEMO-HANDOFF.md` and do item F1."
(The handoff says where things stand: device, working tree, the user's preferences. It sends you here.)
(Sonnet for most items, to stretch the usage limit; F1 and F5 are the trickiest.)

## Status (update this table when an item moves)

| Item | What | Device needed? | Est. | Status |
|---|---|---|---|---|
| F0 | Finish the spoken camera alerts (T-03c1) | yes, for its check | 1 h | code done by the Developer, unreviewed; no report |
| F1 | Vibration patterns from the Pi, and the D-15 override for alerts | at the end | 2.5 h | not started |
| F2 | Glasses board lost / back | at the end | 1.5 h | not started |
| F3 | Earphones lost / back | at the end | 2 h | not started |
| F4 | Camera alert vibration (beep beep with the spoken alert) | at the end | 0.5 h | not started |
| F5 | Camera blocked / too dark / too bright | yes | 2 h | not started |
| F6 | Control panel lost / back (no saved settings) | at the end | 1 h | not started |
| F8 | The demo run: one script and one rehearsal on the real device, evidence logs | yes | 2 h | not started |
| F7 | (if time) An alert in progress finishes before an urgent warning cuts it (D-16) | no | 1 h | optional |

About 12.5 hours of work plus margin. **Order: F1, F2, F3, F4, F6 first.** They are code plus unit tests and
do not need the device, which a teammate is using now. Then F0, F5 and **one batched device session** that
checks F0 to F6 together, then F8. If time runs out, F7 goes first, then F5's "too bright" half.

## Rules (all items)

- Everything in `AGENTS.md` applies. In particular: never edit an installed library; never change the
  vision algorithms (detection processing, depth math, depth-to-motor mapping); nothing switches to mock
  mode by itself; check the hailo-apps docs before touching anything on the Hailo or GStreamer side
  (Rule 6), without asking the user.
- **No git writes** (the user commits). **Do not edit `TASK-BOARD.md`**; record here. After demo day the
  Lead reconciles the board from this file.
- **Device etiquette:** before any device step, check that nothing else is using it
  (`systemctl is-active second-vision`, `pgrep -af "Parallel-Depth|pytest"`) and ask the user, since a
  teammate may be testing. sudo only for `sudo -n .claude/tools/usb_sim.py` (camera and earphones unplug)
  and read-only checks.
- **Done means:** the item's behaviour below works; new unit tests pass; the whole suite passes in the
  Poetry environment (`~/.cache/pypoetry/virtualenvs/second-vision-repo-wwT_Rtha-py3.13/bin/python -m pytest tests/ -q`)
  and in `my_hailo_env` (`my_hailo_env/bin/python -m pytest tests/ -q`); a short note in
  `docs/testing/reports/DEMO/<item>.md` (what changed, what was tested, what wasn't); this table updated.
  Long test output goes to `docs/testing/reports/DEMO/evidence/`; read only its summary lines.
- Keep each session short: read what the item needs, not the whole repo.

## Decisions that apply (the user, 2026-10-18; `documentation/DECISIONS.md` D41, `DISAGREEMENTS.md` D-15 to D-17)

- **Failsafe alerts override the switches, one time only.** A failsafe alert is spoken even with the
  speech switch off, at `max(tts_volume, 0.30)`; a failsafe pattern plays even with the vibration switch
  off, at `max(motor_strength, 0.30)`. That one alert or pattern only: nothing else changes.
- **Reconnect wording:** "Everything is connected" only when no other failsafe alert is still raised
  (`alerts.raised()`); otherwise a sentence about the part that returned ("Vibration is working again",
  "Controller board connected", "Earphones connected"). The camera keeps its own sentence ("Camera has
  been reconnected").
- **Repeat rules** (REQUIREMENTS.md section 8): glasses alert every 30 s while depth is on, once while off;
  earphones pattern every 30 s while detection is on, once while off; panel alert every 5 minutes; camera
  alert every 10 s.

## Where the pieces are (verified 2026-10-18)

- Alert scheduler: `src/second_vision/core/alerts.py` (`raise_alert(name, text, interval=None, repeat=True)`,
  `repeat` may be a callable; `set_repeat`, `clear`, `is_raised`, `raised()`), its worker
  `workers/alert_worker.py`, created as `user_data.alerts`. Speech goes through the speech worker.
- Speech: `workers/tts_worker.py` drops every item while `tts_enabled` is off; volume from `tts_volume`.
- Glasses link: `workers/serial_worker.py`, class with `update(ack_seen, now)` returning `"down"`/`"up"`
  on a change (the link tracker). Motor packets `_pack_motor_update`, heartbeats keep the board's watchdog
  fed; `vibration_enabled` and `motor_strength` gate and scale motor duties.
- Panel: `workers/config_reader.py` notices 30 s without `B:alive` (`panel_missing`, `ever_alive`); today it
  only logs.
- Camera alerts (T-03c1, in the tree): `pipeline/app.py`, `CAMERA_ALERT_MISSING`, `CAMERA_ALERT_BACK`.
- Simulated unplug for tests: `sudo -n .claude/tools/usb_sim.py detach|attach camera|earphones --for N`.
  With the earphones off, speech goes to a "Dummy Output" and `espeak-ng` still exits 0: the program
  cannot learn the earphones are gone from speech; it must look at the USB device. The earphones are the
  USB device whose sound card can **play** audio (the camera has a capture-only microphone).

## The items

### F0 · Finish the spoken camera alerts
The Developer finished the code and tests (evidence in `reports/T-03c1/evidence/`, no report).
**Do:** review the diff against `.claude/baselines/T-03c1/` (read `reports/T-03c1/brief-1.md` for what it
must do), run both suites, write `reports/DEMO/F0.md`. **Device check** (batched): pull the camera while
the program runs: "Critical Error: Camera cannot be reached" at once and every 10 s; plug it back:
"Camera has been reconnected" once, vision back.

### F1 · Vibration patterns, and the D-15 override
**Do:** a small pattern player on the Pi that briefly takes the motors from the depth stream, plays a
pattern by sending timed motor packets (no firmware change), then hands the motors back. Patterns,
distinct by feel: **reconnect** *beep beep* (two short pulses, all three motors); **camera alert** a
different *beep beep* (for example longer pulses); **earphones** *strong weak* x3. Patterns play even
with the vibration switch off, at `max(motor_strength, 0.30)`. Spoken failsafe alerts are spoken even with
the speech switch off, at `max(tts_volume, 0.30)` (mark scheduler alerts as failsafe so object and mode
announcements still obey the switch). Neither changes the wearer's settings.
**Tests:** unit tests with a fake port (packet order and timing, hand-back to depth, switch-off and
low-knob cases, the depth stream resumes). **Device check:** feel each pattern once.

### F2 · Glasses board lost / back
**Do:** on link "down": raise "Vibration is not working" (repeat every 30 s while depth is on, once
while off; use a `repeat` callable). On "up": clear it, play the reconnect pattern, say the D-17 sentence.
Absent at start-up counts as down. **Tests:** link tracker transitions drive the alerts (fake clock).
**Device check:** unplug the glasses board's cable, then reconnect.

### F3 · Earphones lost / back
**Do:** a presence check every 1 to 2 s (the USB sound card that can play audio, the same rule as
`usb_sim.py`'s `earphones()`). Missing: the *strong weak* x3 pattern every 30 s while detection is on, once
while off (no speech: there is nothing to speak through). Back: the reconnect pattern and the D-17
sentence. Must not slow the pipeline (a sysfs read per second). **Tests:** fake sysfs or an injected
check. **Device check:** `usb_sim.py detach earphones --for 20`, then one real unplug.

### F4 · Camera alert vibration
**Do:** with the camera alert (F0), play the camera *beep beep* each time the alert is spoken (every
10 s). Small once F1 exists.

### F5 · Camera blocked, too dark or too bright (`URGENT-TESTS.md` section 1)
**Do:** once a second, before any AI decision, a tiny grey sample of the camera frame (for example every
16th pixel). "Blocked or dark": 95th-percentile brightness under about 40 (0 to 255), or the mean under 14.
"Too bright": the mean over 240. Three checks in a row to raise, three good checks to clear. Wording:
"Camera is blocked or it is too dark. Obstacle warnings may be missing." / "Camera is facing very bright
light. Obstacle warnings may be missing." every 30 s; "Camera view is back." once when it clears. Not
while the camera is missing (F0 takes over). Check the Hailo/GStreamer docs for the right place to read
the frame (Rule 6). **Must not lag:** run `docs/testing/tools/perf_run.py --duration 180` with the check
on and compare frame rate and delay with `reports/T-15/evidence/perf-both-20min.txt`; extra CPU under 1 %
of one core. **Device check:** cover the lens with a hand, then a cap; shine a torch.

### F6 · Control panel lost / back
**Do:** when the panel goes silent (or is absent at start-up): "Controller board cannot be reached. Using
the current settings." every 5 minutes. When it is back: clear it and say the D-17 sentence. Saving
settings between sessions (T-02a) is **not** in the demo. **Tests:** fake clock and fake lines.
**Device check:** unplug the panel, wait 30 s, plug it back.

### F8 · The demo run
**Do:** one written demo sequence (normal use; camera out and back; glasses board out and back; earphones
out and back; panel out and back; lens covered; the performance chart) and a small script that starts the
program, timestamps what it says and sends, and saves an evidence log per step. Rehearse it once with the
user on the real device: that rehearsal is also the per-device hands-on confirmation for T-03a, T-03b and
F0 to F6. Write `reports/DEMO/F8.md` with what was seen, heard and felt.

### F7 · (if time) Let an alert finish (D-16)
**Do:** a failsafe alert in progress is not cut by an urgent object warning until it finishes (2 to 3 s).
The four strict `xfail` tests from T-00a become passing tests.

## After demo day

The Lead reads this file, records F0 to F8 on the board (each as needing a Tester pass), and the normal
loop resumes for the workbook cases.
