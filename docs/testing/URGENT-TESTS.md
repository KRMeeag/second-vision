# Urgent tests to run after the P1 failsafes

Added 2026-10-17 at the user's request, decisions the same day. The agents keep working through
the P1 tasks (TASK-BOARD.md) first and are not interrupted. **Order: P1 tasks and their tests, then
T-13 to T-15 below, then the rest of the workbook.** They are not in the UAT workbook yet (see
"Workbook" at the end).

**Status: agreed in principle, not on the board.** When the P1 tasks are done the Lead adds
T-13 to T-15 at the front of the queue. Models: all Sonnet (HANDOFF.md). None of this changes the
pipeline's start/stop or threading.

## 1. Camera blocked, too dark or too bright (T-13)

**What exists today:** nothing. No code looks at the camera image's brightness; the workbook's
TC-013-003 (covered lens) only says the device stays silent. Silence is also what the wearer hears
when everything is clear, so a blocked camera looks exactly like "no obstacles".

**Decision (user, 2026-10-17):** skip the "see what happens today" step. Build the check and test
that. A simple "is the picture mostly black" test is good enough; it cannot tell a blocked lens from
a dark room, and it doesn't need to, because the wearer should be told either way.

**Design (for the Developer to confirm against the Hailo documentation; the Lead or Developer checks
it without asking, AGENTS.md Rule 6):**
- **Where:** before any AI runs, on the camera's own frames, once a second, on a tiny grey picture
  (for example 64x48). A once-a-second look at about 3,000 pixels costs microseconds, and the pipeline
  keeps running at full rate in between.
- **"Mostly black":** most pixels very dark, for example the 95th-percentile brightness under about 40
  on the 0 to 255 scale, or the average under the paper's 14. The numbers come from real footage
  (cover the lens, a dark room, a normal room, a plain wall). A dark room with one bright lamp
  isn't flagged.
- **Confirmation:** three checks in a row (about 3 s), as in the paper, so a blink or a passing hand
  doesn't trigger it. It clears after three good checks in a row, so it doesn't flap.
- **What the wearer hears:** "Camera is blocked or it is too dark. Obstacle warnings may be missing."
  (the second sentence is what they need to know). Repeated every 30 s while it lasts, and
  "Camera view is back." once when it clears. The 30 s matches the other connection alerts: often
  enough that a wearer who missed it hears it again, not so often that it becomes noise. It uses the
  alert scheduler (T-00a, in development now), so the timing is not new code.
- **Too bright (same check, one more comparison):** "Camera is facing very bright light. Obstacle
  warnings may be missing." Included because it costs nothing extra; the user can drop it.
- **Not while the camera is missing or lost:** the camera-missing alert takes over (T-03c).
- **Tests:** unit tests on synthetic frames (black, dark, normal, bright, plain wall, a lamp in a dark
  room, noise); a device test that holds a cover over the real camera; the real-camera confirmation
  by a person (one per device, as agreed).
- **TC-013-003 is reworded** to "no detection messages; the camera-blocked warning is allowed".

**Compute limit (user, 2026-10-17):** only if it doesn't lag the system. Acceptance: with the check on,
the pipeline's FPS and camera-to-callback delay are unchanged within run-to-run noise, and the extra
CPU is under 1 % of one core. Measured before and after with `tools/perf_sample.py`.

## 2. Blurry camera (T-14)

Same machinery as section 1: the one tiny grey frame per second, the same three-in-a-row rule, the
same alert scheduler, the same compute limit.
- **Method:** the standard focus measure (variance of the Laplacian) compared with the device's own
  recent median. A sustained fall to a small fraction of it is a smeared or defocused lens. Not a
  fixed threshold, see the review below.
- **Wording:** "Camera image is blurry. Check the lens." Same repeat rule.
- **Safety valve:** if the thresholds from real footage still give false alarms on plain walls,
  it ships switched off (a setting) and the finding is reported, rather than warning the wearer
  wrongly. A wrong warning here costs trust in the real ones.
- **Tests:** unit tests on sharp and blurred versions of real photos; a plain wall must not alert;
  a device test with a smeared lens; one real confirmation by a person.

## 3. Review of the paper's method (brightness plus adjacent-pixel difference)

The excerpt: mean brightness must be between 14 and 240 (0 to 255 scale); lens damage is detected
by the 1D difference between adjacent pixels; if the problem is still there on the third check, tell
the user and reset the counter.

I tried it offline on three stock photos (sharp, then blurred at three strengths) and on synthetic
frames with camera-like noise. **This is not the Arducam's own footage**, so the thresholds below
are indicative only; the real measurements are part of T-13a/T-14a.

| Frame | Mean brightness | Adjacent difference | Laplacian variance |
|---|---|---|---|
| room photo, sharp | 131 | 11.1 | 1966 |
| same, blurred (small / medium / strong) | 131 | 6.3 / 3.3 / 1.9 | 131 / 9 / 2.8 |
| tree and sky, **sharp** | 215 | **4.6** | 280 |
| lens fully covered (noise only) | 2.8 | 1.9 | 58 |
| covered with a light leak (finger) | 44 | 2.3 | 83 |
| dim room | 25 | 2.3 | 83 |
| direct sun | 250 | 2.2 | 78 |

What this shows:
1. **The brightness band (14 to 240) is good for the extremes.** A covered lens (about 3) and direct
   sun (about 250) are both caught. Cheap and sound.
2. **It misses a partly covered lens.** A finger or hand with some light getting through gives a
   mean of 40 to 60, comfortably inside the band. A hand over the lens is the likelier real case
   than a perfectly black cover. Add the image's spread (standard deviation): a blocked lens is
   nearly flat (about 2) where normal scenes are 30 to 70.
3. **Adjacent difference does fall as blur rises** (11.1 down to 1.9), so the idea works for the same
   scene. **But one fixed threshold can't work across scenes:** a sharp tree-and-sky photo (4.6) scores
   lower than the same room only slightly blurred (6.3). A plain wall is a sharp image with almost no
   detail, so it would read as "damaged lens". The UAT cases themselves put the camera in front of
   a plain wall (TC-005-001), so the check would fire during a test that is meant to be normal.
4. **Noise sets a floor.** Noise alone gives about 1.9 to 2.3, the same as a heavily blurred photo,
   so the number cannot separate "blurred" from "dark and noisy" without the brightness check.

**Recommendation (for the Lead to confirm against real frames):**
- Occlusion and exposure: mean brightness band plus a minimum standard deviation, on a downscaled grey
  frame, with the paper's "three checks in a row" rule. Keep it.
- Blur: don't use a fixed adjacent-difference threshold. Compare the Laplacian variance (a standard
  focus measure) with the device's own recent median, and flag a sustained drop to, say, under a
  fifth of it. This adapts to the scene and treats a plain wall as normal, because the wall
  doesn't change the baseline suddenly. A sudden smear does. Thresholds come from real footage.
- Both only when the depth/detection result also looks empty or inconsistent, so a plain wall,
  which depth treats as an obstacle, is never reported as a fault.
- Cost: a grey frame at 160x120 is trivial next to the Hailo work, but the frame has to be read from
  the pipeline. The Hailo documentation must be consulted first (AGENTS.md Rule 6) for the
  right place to read frames without slowing the camera branch.

## 4. Performance and resource tests

The paper's list, checked against this device. **The method is a suggestion, not a requirement.**

| Measure | Paper's method | What to do instead |
|---|---|---|
| Frames per second and per-frame delay | `cv2.TickMeter` around `cv2.read()` | The camera runs through GStreamer; `cv2.read()` is never called. The detection and depth callbacks already count FPS per branch, and the depth branch already logs latency (the `[DEPTH]` line, `LATENCY_WARN_MS`). Use those: no code change. Detection-to-speech delay isn't measured anywhere yet; the speech worker would need a timestamp, a small addition |
| "FeedbackEngine" | the final trigger | There is no such class. The ends are the motor packet (serial worker) and the spoken phrase (speech worker) |
| CPU and memory | `psutil`, per core | Works: psutil 7.0.0 is installed in `my_hailo_env`. `scripts/uat_evidence.py` and REQ-DTA-09 (10-minute stability) already point this way |
| NPU load | `hailortcli` | `hailortcli monitor` works only if the process that owns the chip has `HAILO_MONITOR=1`. On this Pi that is `hailort_service` (the app is its client), whose setting is `HAILO_MONITOR=0` (verified 2026-10-18). To measure: a runtime override of that variable and a brief `hailort.service` restart, one run, then undo. Not done: queued 2026-10-18, then cancelled by the user so a teammate could use the device for a depth test. `.claude/tools/queued_npu_run.sh` is ready to run it again |
| Temperature, every 10 s | `vcgencmd measure_temp` | `vcgencmd` fails here ("Can't open /dev/vcio_gencmd"): the device is root-only even though the user is in the `video` group, so the note in `scripts/power_monitor.py` is wrong on this system. Read `/sys/class/thermal/thermal_zone0/temp` instead (works without root, 57.9 C now). Throttling: compare `scaling_cur_freq` with `cpuinfo_max_freq` (both 2.4 GHz now) and read the undervoltage alarm at `/sys/class/hwmon/hwmon3/in0_lcrit_alarm`. `vcgencmd get_throttled` still gives the authoritative flags but needs `sudo`, which agents don't have; run it yourself if the panel wants it |
| Model name | YOLOv8**s** | Confirmed by the user: the model is YOLOv8**s**. `README.md`, `documentation/PROJECT.md` and `ARCHITECTURE.md` say YOLOv8**n**, so those docs are out of date. The HEF file agrees with the user: 9.7 MB compiled, which fits an 11 M-parameter v8s; a v8n would be about a third of that. The HEF's metadata doesn't name the variant |

**T-15, the measurement run: first run done**, see
[reports/T-15/measurement-1.md](reports/T-15/measurement-1.md) (29.6 FPS, no throttling, stability
rule passed; NPU load unavailable; a static scene only). [tools/perf_sample.py](tools/perf_sample.py), `perf_run.py` and `perf_plot.py` are written and tested
(2026-10-17/18): it samples every 10 s, for the chosen time, only by reading `/proc`, `/sys` and the app's
console log, so it never touches the camera, the Hailo chip or the app.
```bash
my_hailo_env/bin/python docs/testing/tools/perf_sample.py --duration 600 --label stability --app-log /tmp/app.log
```
It records temperature, CPU clock against its maximum (a busy core below the maximum means
throttling), the undervoltage flag, fan speed, per-core load, system memory, the app's own CPU and
memory, and reads FPS and camera-to-callback delay from the app's `[DEPTH]` lines. It applies
REQ-DTA-09's stability rule (memory within +5 %, CPU within 10 points) to runs of 7 minutes or
longer. **Not done:** NPU load (see the table), and `vcgencmd get_throttled` needs root, so that
column reads "unavailable" unless you run the script with `sudo`. The run serves REQ-DTA-09,
REQ-PERF-01 and TC-015-003 at once.

## 5. Can it run alongside the current workflow?

**Not the device tests.** (User agreed 2026-10-17: they go after the critical fixes.) Each needs the
camera, the Hailo chip and the CPU for minutes, and the agents' own device tests use the same ones;
each side would distort the other's numbers and timing checks. Already done without the device: the
sampling script, the offline image review above, and this plan.

## Workbook

If these are kept, they need workbook cases: the camera-blocked case and the blurry case (next to
TC-013-003), and a performance block (next to the thermal and battery tests, W-23). Add them to
`WORKBOOK-REVISIONS.md` section A once the wording above is agreed.
