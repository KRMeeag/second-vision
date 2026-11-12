# Handoff: writing up the panel revisions (device results, 2026-11-09 to 2026-11-12)

**For:** the agent revising the thesis (IMRaD) after the defense.
**From:** the device sessions on the Raspberry Pi (Kenth's worktree, `results/panel-revisions/`).
**Written:** 2026-11-12.

Every number below was read from a saved file, named next to it. Copy them as they are: don't round them
differently, average them or extend them. If the thesis needs a number that isn't here, it wasn't measured;
say so in the text rather than estimating.

---

## 0. The short version

| Panel item | Answer, in one line | Section |
|---|---|---|
| Sir Franze #3: is YOLOv8s the right size? | Yes. With depth beside it, only n and s keep both networks at 30 FPS on the chip. yolov8m slows the haptics and doubles detection delay in the app. l and x can't hold 25 FPS | §2 |
| Sir Franze #6: 13 vs 14 classes | The 13-class model matches the 14-class model's 18 Oct real-time figures. The 12 detection tracker cases were re-run on it | §3 |
| Mam Vanessa #1: target inference latency | Targets: ≥ 25 FPS, and ≤ 320 ms from capture to vibration. Measured: chip latency per model; camera → motor command median 108 ms, worst 219. Speech delay goes in Limitations, with no number | §4 |
| Sir Dante #5 (innovation) and Mam Vanessa #3 (learning curve) | Filipino speech by default, from pre-made clips; English as the fallback. Verified by functional tests only, not by the UAT. The audio tutorial was dropped | §5 |

Decision-log entries the thesis can cite: **D43** (detector size), **D44** (latency measurement and the 13-class
re-run), **D45** (Filipino speech), **D46** (bicycle and tricycle by name), in the repo's
`documentation/DECISIONS.md`.

---

## 1. The device and how it was measured (for Methods)

**Hardware and software:**
- Raspberry Pi 5 (8 GB, active cooler) with the Hailo-8 AI HAT+ (26 TOPS), HailoRT 4.23.0;
- Debian 13 (trixie), Linux 6.12.62+rpt-rpi-2712;
- camera: Arducam IMX291 USB at 640×480, capped at 30 FPS.

**Models:**
- detection: the team's 13-class YOLOv8s (`hef_v3`, no Pole);
- depth: Hailo's `scdepthv3.hef`.

Both run on the Hailo-8 as two branches of one GStreamer pipeline.

**Three kinds of measurement.** Keep them apart in the text; they answer different questions:

| Kind | Tool | What it includes | Used for |
|---|---|---|---|
| Chip only | `hailortcli benchmark` (one model) and `hailortcli run2` (detection + depth together) | The accelerator alone, fed random input. No camera, GStreamer or Python, so **upper bounds** for the app | §2 |
| Inside the app | The real app started the way the boot service starts it: headless, 30 FPS camera, depth and detection on. Logs every 15 frames, sampled every 10 s | Everything: camera, pipeline, Python, workers | §2, §3, §4 |
| Timing log | `SV_LATENCY_LOG=1` (opt-in, off by default; D44) | Per motor command and per announcement, from the frame's capture time | §4 |

**Frame rate:**
- computed per 15-frame window from timestamped log lines;
- the first minute of each run is left out;
- "15-frame windows" percentiles describe how steady it was.

---

## 2. Sir Franze #3: is YOLOv8s the right YOLOv8 size?

Source: `session-a/RESULTS.md` (Part 1 chip, 2026-11-09; Part 2 in the app, 2026-11-11), `session-a/matrix.csv`.

### 2.1 On the chip (mean of 3 runs; the run-to-run range was at most 0.3 FPS)

| Model | Params (M) | OPS (G) | COCO mAP float | COCO mAP hardware (computed) | Zoo FPS (batch 1) | FPS alone | Chip latency alone (ms) | Det FPS with depth | Depth FPS with det |
|---|---|---|---|---|---|---|---|---|---|
| yolov8n | 3.2 | 8.74 | 37.0 | 36.4 | 1036 | 431.6 | 3.35 | 134.3 | 134.4 |
| yolov8s | 11.2 | 28.6 | 44.6 | 43.8 | 491 | 491.3 | 5.83 | 121.7 | 121.8 |
| yolov8m | 25.9 | 78.93 | 49.9 | 49.2 | 67 | 31.5 | 25.84 | 27.6 | 27.7 |
| yolov8l | 43.7 | 165.3 | 52.4 | 51.8 | 36 | 19.5 | 47.15 | 17.9 | 18.0 |
| yolov8x | 68.2 | 258 | 53.5 | 52.9 | 18 | 13.4 | 68.70 | 12.8 | 12.8 |
| **Deployed 13-class** | — | — | — | — | — | 185.7 | 11.61 | 89.1 | 89.1 |

Depth alone (reference): 468.4 FPS, 4.77 ms.

How to read the columns:
- **Params, OPS, COCO mAP float and Zoo FPS** come from Hailo's Model Zoo v2.17 table for its **80-class COCO
  builds**, not the team's model.
- **"COCO mAP hardware" is computed** as float − the table's "hardware degradation"; label it as computed.
- **Every other column was measured here.**
- **"Chip latency alone"** is with that model alone on the chip. With depth running, `run2` reports frames per
  second but cannot measure latency (HailoRT 4.23). Latency with depth running comes from the app (§2.2).

**Floors, with both networks running together:**

| Floor | Models with both networks at or above it in every run |
|---|---|
| ≥ 30 FPS (the camera cap) | yolov8n, yolov8s, the deployed model |
| ≥ 25 FPS (the real-time floor) | the three above, plus yolov8m (27.6 / 27.7) |
| Below 25 FPS | yolov8l, yolov8x |

### 2.2 Inside the app, with depth on

The deployed model and yolov8m ran 30 minutes each; n, s, l and x ran 5 minutes each.

| Model | Det FPS median / p5 | Camera → detection, median / p95 (ms) | Depth (haptics) FPS median / p5 | Depth windows < 29.5 FPS | Hailo chip, start → peak (°C) | Throttling |
|---|---|---|---|---|---|---|
| **Deployed, 30 min** | 30.0 / 28.5 | **92** / 114 | 30.0 / 28.6 | **30 %** | 54.6 → **64.9** | none |
| **yolov8m, 30 min** | 29.8 / 27.8 | **176** / 245 | 28.0 / 25.0 | **86 %** | 61.3 → **76.2** | none |
| yolov8n, 5 min | 29.9 / 23.5 | 91 / 118 | 30.0 / 24.4 | — | 55.4 → 57.7 | none |
| yolov8s, 5 min | 30.0 / 23.7 | 93 / 119 | 30.0 / 22.1 | — | 57.1 → 61.0 | none |
| yolov8l, 5 min | 21.7 / 20.8 | 1,309 / 1,344 | 21.4 / 20.8 | — | 64.4 → 81.0 | none |
| yolov8x, 5 min | 15.8 / 14.0 | 1,880 / 1,907 | 15.3 / 14.1 | — | 65.0 → 81.1 | none |

The p5 of the 5-minute runs is lower only because each caught one of the periodic dips (§4.4).

**Heat.** The chip's own first throttling step is **104 °C** (read from the chip). yolov8m held 73–75 °C from
minute 10, about 28 °C below that step. The Pi's throttle flags stayed 0x0 in every run.

### 2.3 What to write

- **YOLOv8s is kept.** With depth on the same chip, only n and s keep both networks at the 30 FPS camera cap;
  s has the higher accuracy (44.6 against 37.0 COCO mAP float).
- **yolov8m didn't throttle in 30 minutes, but it costs the real pipeline:**
  - haptics below full frame rate 86 % of the time against 30 %;
  - detection delay doubled (176 against 92 ms);
  - the chip about 10 °C hotter.
- **l and x can't run in real time** beside depth.

**Don't write:**
- that the deployed model reaches the Model Zoo's yolov8s speed. It doesn't: 185.7 against 491.3 FPS alone,
  and the cause isn't established;
- that the COCO mAP figures describe the team's model;
- anything about long-term heat beyond 30 minutes in one room.

**The team's model's own accuracy**, if wanted: measured separately on 2026-11-05/06 on its frozen 6,403-image
test split, on the Hailo-8: mAP@0.5 **0.7597** and mAP@0.5:0.95 **0.5112** (the deployed build).
- Report it against the pre-registered 0.65 only.
- Don't present the gap to the FP32 figure (0.7714) as a quantization cost: the FP32 control has not been run
  through the same scoring.

Source: `~/Documents/Second-Vision-wctp-review/RETURN.md` on the Pi.

---

## 3. Sir Franze #6: 13 classes vs 14

### 3.1 Real time: the 20-minute run, 18 Oct (14-class) against now (13-class)

Source: `session-b/RESULTS.md`, `session-b/b3-comparison.md`. Same protocol as 18 Oct. The "real boards" run had
the real glasses board and control panel, with both switches on throughout.

| Measure | 18 Oct (14-class) | 13-class, stand-in board (as 18 Oct) | **13-class, real boards** |
|---|---|---|---|
| Average FPS | 29.6 | 29.6 | **29.6** |
| 15-frame windows: median / p5 / p1 / min | 30.0 / 28.5 / 20.2 / 18.9 | 30.0 / 28.6 / 20.4 / 19.0 | 30.0 / 28.5 / 20.4 / 18.0 |
| Camera → depth callback: median / p95 / p99 / max (ms) | 104 / 127 / 132 / 147 | 106 / 129 / 134 / 164 | 106 / 129 / 136 / 158 |
| Camera → detection callback: median / p95 / p99 / max (ms) | not logged | 92 / 116 / 123 / 136 | 91 / 117 / 124 / 152 |
| App CPU, average (% of one core) | 73 % | 78 % | 77 % |
| App memory, minute 1 → end | 195 → 199 MB (+2.2 %) | 200 → 203 MB (+1.6 %) | 199 → 204 MB (+2.8 %) |
| App CPU, first 3 min / last 3 min | 72 / 75 % | 75 / 80 % | 77 / 76 % |
| SoC temperature, first / max | 59.5 / 65.5 °C | 52.9 / 61.7 °C | 51.8 / 63.9 °C |
| Throttle flags | 0x0 | 0x0 | 0x0 |

**Stability criteria from the original test, both met:**
- memory at the end ≤ 5 % above minute 1;
- CPU in the last 3 minutes within 10 points of the first 3.

**Limits:**
- the timing log was on in the 13-class runs and off on 18 Oct;
- the higher CPU is not attributed to it or to anything else (not isolated).

### 3.2 The 12 detection tracker cases (M01, TC-001-001 to TC-003-004), re-run on the 13-class model

Source: `session-b/RESULTS.md` (B1), `session-b/b1/b1-results.csv` (tracker columns), `b1-details.csv` (per image).

**Method:**
- **Images:** the tracker's original images no longer exist. With Kenth's approval, images were chosen from the
  model's frozen test split (6,403 labelled images), by fixed rules on the labels and pixel statistics only,
  before the model ran.
- **Model:** the deployed model ran on the Hailo-8 with the app's NMS settings.
- **System:** each image's detections went through the app's real detection callback, twice, 0.35 s apart, so
  the 0.3 s confirmation window passes.
- **Two levels:**
  - **model:** a box kept at ≥ 0.3;
  - **system:** a box the callback acts on, ≥ 0.70: tracked, zoned, spoken.
- **Verdicts are strict:** a case passes only if every image passes.

| Case | Result | Tracker verdict |
|---|---|---|
| TC-001-001 per class | 11 of 13 right at > 0.5 (Chairs right but 0.35; Tables read as Doors at 0.35) | Failed |
| TC-001-002 confusion pairs | **No cross-labelling** (0 of 15). 2 of 15 right but below 0.5 (0.37) | Failed (low confidence, not confusion) |
| TC-001-003 thinnest classes | 8 of 9 | Failed |
| TC-002-001 person over another class | 1 of 3. The person won whenever both objects reached the callback; in 2 images the person scored below 0.70 | Failed |
| TC-002-002 the nearer person | 2 of 3 | Failed |
| TC-002-003 left / centre / right | 2 of 3 (the left image's door got no box) | Failed |
| TC-002-004 zone boundary band | The code keeps the previous zone (as designed); the tracker's text "must not flag any classes" disagrees with the design | Failed against the tracker's text |
| TC-002-005 empty scenes | 2 false alarms in 363 truly empty images. 14 other announcements were real people, cars, tables or doors the dataset never labelled (checked by eye) | Failed |
| TC-003-001 low light | Darkest images (mean grey 1–3 of 255): 1 of 3. Moderate low light: 2 of 3 | Failed |
| TC-003-002 motion blur | 3 of 3 | **Passed** |
| TC-003-003 crowded (≥ 70 % found) | 0 of 3. Very small, distant instances (median 0.04–0.38 % of the image): 0–12 % found at the system level, 2–36 % at the model level | Failed |
| TC-003-004 partial occlusion | 1 of 3 | Failed |

**What to write:**
- **Wrong-class errors were rare,** and there was no confusion between Motorcycle/Bicycle, Bicycle/Tricycle or
  Chairs/Bench.
- **Most failures are objects the model saw at 0.3–0.70,** below the 0.70 confidence the system requires
  before tracking or speaking: a deliberate trade of missed objects for fewer false announcements.
- **The decision logic** (priority, nearest instance, zones, hysteresis) did what it was designed to do
  whenever objects reached it.

**Don't write:**
- that these cases "passed" before. The 2026-08-03 results were on an older model, with images that no
  longer exist;
- any of these as UAT results. They are functional tests on dataset images, not the device's camera.

### 3.3 One change to how classes are spoken (D46)

Bicycle and Tricycle used to be announced as "vehicle". Now they're announced as "bicycle" and "tricycle", with
the same priority and urgency as before. The 13 spoken labels are:

> person, vehicle, bicycle, tricycle, motorcycle, animal, door, chair, table, pothole, trash bin, stairs, bench

---

## 4. Mam Vanessa #1: target inference latency

### 4.1 The targets

State them in Methods, as the writing plan (INC-09) says. They were already declared in the original
manuscript (pp. 141–142, "end-to-end alert latency"):
- **throughput:** at least 25 FPS (40 ms per frame);
- **collision warning:** at most **320 ms** from camera capture to vibration (Gao et al., 2025).

"40 ms per frame" is a frame-rate figure. Don't compare delays such as 106 ms against it.

### 4.2 Results against them

| Measure | Value | Source |
|---|---|---|
| Chip latency, deployed model alone | 11.61 ms (per model: §2.1) | session-a |
| Camera → detection result, in the app, with depth | median 91 ms, p99 124, max 152 (13-class, real boards) | session-b B3 |
| Camera → depth result | median 106 ms, p99 136, max 158 | session-b B3 |
| **Camera → motor command written to the glasses board** | **median 108 ms, p95 135, p99 143, max 219** (33,769 commands, 0 failed, 20 min, real boards, both models) | session-b `b3-realboards-20min/latency-analysis.txt` |
| of which: the hand-off from the depth callback to the serial link | median 0 ms, p99 10 | same |
| Glasses board's reply (acknowledgement) | median 1.05 ms, p95 1.07, p99 1.23, max 2.29; 200 of 200 | session-b `ack-probe/` |
| Frame rate | 29.6 FPS average; 15-frame windows median 30.0 | §3.1 |

**What to write:**
- **Inside the 320 ms target.** The collision warning's command leaves the Pi a median 108 ms after the camera
  captures the frame, and at worst 219 ms. Almost all of it is the camera and the two neural networks.
- **The glasses board** acknowledges in about 1 ms.
- **Throughput** meets 25 FPS on average (29.6), but not at every moment: see §4.4.

### 4.3 Limitations (no numbers here; say what wasn't measured)

- **Motor onset.** The measurement stops when the command is written to the glasses board's serial link (plus
  its ~1 ms reply). When the motor physically starts was not measured. Write "capture to motor command", not
  "capture to vibration", for the measured figure.
- **Speech delay.** Not measured: the device's 20-minute runs announced no objects, and the dedicated trials
  were not run. State it as a limitation, with no number. The timing log built for it (D44) records it if a
  later study wants it.
- **Camera exposure.** Capture time is the frame's timestamp in the pipeline, not the moment of exposure.

### 4.4 Two behaviours to disclose

- **Periodic dips.** About every 6 minutes the frame rate falls to about 18–20 FPS for about 19 s. This happened
  on 18 Oct and in every run since, with every model. In the 13-class real-boards run, 61 of 2,254 detection
  windows and 58 of 2,247 depth windows were below 25 FPS (about 2.6 %). The cause is not known (not
  investigated, by the brief's instruction).
- **Camera drops.** The USB camera dropped without being unplugged: 4 times in 8 minutes in one depth-only run,
  9 times in 30 minutes in one Session A run. The app recovered each time: in the depth-only run, each loss lasted
  3–6 s until the camera was back. Cause unknown. Mention it if the thesis discusses reliability.

---

## 5. Sir Dante #5 (innovation) and Mam Vanessa #3 (learning curve): Filipino audio

Source: `session-c/RESULTS.md`, D45.

**Why** (from the team's handoff):
- 3 of the 6 UAT interviewees asked for Filipino audio;
- 64 % of UAT participants found the system unnecessarily complex, and 76 % said they'd need technical help;
- eSpeak NG has no Filipino voice.

**What was built:**
- **Filipino is the default spoken language.** The device plays **pre-made audio clips**, made by a team member
  with an AI voice (engine and voice to be named from the clip set's `voice_info.txt`; see §6).
- **34 clips:**
  - **15 whole sentences:** 4 mode announcements and 11 system alerts;
  - **19 words:** 13 object names, the 3 directions, and "multiple", "still" and "leaving to".
- **Detection phrases are spliced from word clips:** e.g. "maraming tao pa rin sa kaliwa" from [maraming]
  [tao] [pa rin] [sa kaliwa]. 34 clips cover **all 208 detection phrases**: 13 objects × 8 phrase forms × with
  or without "multiple".
- **English is the fallback.** Anything the clips can't say completely is spoken in English (eSpeak NG), the
  whole sentence. It is never half Filipino, half English.
- **What didn't change:**
  - no speech model on the device, no internet, no new hardware;
  - the volume knob, the safety-alert rules and the interruption rules work the same in both languages;
  - `--language en` gives English only.
- **The audio tutorial mode was dropped.** It is not built; don't describe it.

**How it was verified: functional tests only, not part of the UAT** (the UAT is frozen; say so):
- 49 new automated tests. They check:
  - every sentence and every detection phrase resolves to the right clips;
  - anything unknown falls back to English;
  - the volume scaling;
  - that a playing clip is cut by an urgent warning and leaves no file behind.
- One test fails if anyone changes a spoken sentence without making its clip.
- Full test suite: **1260 passed, 117 skipped, 0 failed** (`session-c/pytest-c-full.log`).

**[PENDING]: fill in when the team's clips are installed** (device checks; they need the real clips):
- every clip audible at volume knob maximum and minimum; a safety alert at minimum still ≥ 30 %;
- how quickly an urgent warning cuts a playing clip;
- frame rate during a 3-minute run with Filipino speech;
- clip start-up time against eSpeak NG's.

Until these exist, don't state any audio-quality, timing or audibility result for Filipino.

**For the team to settle in the clip list** (it affects the wording quoted in the thesis):
- "center" is drafted as "sa harap" (in front), not the literal "sa gitna";
- the four mode sentences are meaning-based drafts.

---

## 6. Open items that affect the text

| Item | Status | What the text should do now |
|---|---|---|
| The team's Filipino clips (Drive folder) | Being made | Mark the device checks [PENDING] |
| Voice credit (`voice_info.txt`) | Comes with the clips | If ElevenLabs free plan: the clips are non-commercial, and ElevenLabs must be credited |
| Gap between spliced words | Tuned by ear once the clips arrive | Nothing yet |
| Why the deployed model is slower than the Model Zoo's yolov8s | Not established | Don't explain it; state the two numbers |
| Periodic dips, camera drops | Not investigated | Disclose (§4.4) |

## 7. Where everything is (in the repo, `results/panel-revisions/`)

| Folder | Contents |
|---|---|
| `step1/RESULTS.md` | Device inventory, Model Zoo files, decisions taken before testing |
| `session-a/RESULTS.md`, `matrix.csv`, `app-runs/` | §2 |
| `session-b/RESULTS.md`, `b3-comparison.md`, `b3-*/`, `ack-probe/`, `b1/` | §3, §4 |
| `session-c/RESULTS.md` | §5 |
| `documentation/DECISIONS.md` (repo root) | D43–D46 |
