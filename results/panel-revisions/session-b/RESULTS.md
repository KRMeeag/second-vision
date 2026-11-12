# Session B: latency instrumentation and the 20-minute real-time re-run

Run on 2026-11-12 (Pi clock, UTC+8), on branch `feat/latency-log` (from `85789d5`). Same device and software
as Step 1 and Session A: Raspberry Pi 5 8 GB with the active cooler, Hailo-8 with HailoRT 4.23.0, Debian 13,
Linux 6.12.62+rpt-rpi-2712. Detection model: the deployed 13-class `models/hef_v3/hef_deploy/best.hef`;
depth: `scdepthv3.hef`. `second-vision.service` was stopped for every run and is still stopped.

| Part | What | Status |
|---|---|---|
| B1 | 12 tracker cases on the 13-class model | **Done** on images from the test split (Kenth's approval). 1 Passed, 11 Failed under strict all-images verdicts; per-image results in the B1 section |
| B2 | Latency instrumentation, off by default | **Done.** `SV_LATENCY_LOG=1`; 13 new tests; full suite 1097 passed, 217 skipped (Poetry environment) |
| B3 | 20-minute real-time re-run against 18 Oct, plus haptic latency | **Done.** One run with the stand-in glasses, one with the real boards (both rockers on throughout) |
| B4 | Audio latency trials | **Not run.** Per the thesis writing plan Kenth relayed on 2026-11-12, speech delay goes in Limitations with no number; see below |

## B1: the 12 object-detection tracker cases on the 13-class model

Run on 2026-11-12, 09:34–09:50.
- The Pi had rebooted at 08:18, and `second-vision.service` started at boot.
- Nobody was using it: no speech for an hour, no mode changes. It was stopped at 09:34 under Kenth's
  standing permission and is still stopped.
- The tracker's own images do not exist (Step 1 §1.5). **Kenth approved picking them from `~/v3p_eval/test`**
  (2026-11-12): the frozen test split the model was scored on, 6,403 images with labels.

### Method

1. **Choosing the images** (`b1/select_b1.py` → `b1/selection.json`).
   - Choices use the labels and simple pixel statistics only (brightness, sharpness, blur direction), never the
     model's output. Random draws use a fixed seed.
   - Two steps involved looking at images, both before the model ran:
     - The darkest images in the split are almost black (mean grey 1–3 of 255), so low light was given a
       second, moderate tier (mean grey 20–40).
     - Of the six least-sharp, strongly directional images, the first three that look motion-blurred were
       used. The shortlist is recorded.
   - The rule for each case is in `selection.json`.
2. **The model** (`b1/hef_predict.py`, the on-device script from the 2026-11-06 mAP work).
   - The deployed `models/hef_v3/hef_deploy/best.hef` runs on the Hailo-8, with HailoRT's NMS at the app's
     settings (score 0.3, IoU 0.45): 440 images in 10.1 s.
   - Input is letterboxed to 640 the way Ultralytics validates. The app's `hailocropper` letterboxes with a
     different resize; the difference is small but not zero.
3. **The system** (`b1/evaluate_b1.py`).
   - Each image's boxes go through `pipeline/callbacks.py`'s `_process_real_detections`, the function the app
     runs on every detection frame, using the repo's duck-typed Hailo test objects: the model's own labels,
     scores and boxes, one track id per box.
   - Each frame is given twice, 0.35 s apart, so the 0.3 s confirmation window passes as on a still scene.
   - The speech mailbox and the track table are the system's answer.
4. **Two confidence levels, both reported.**
   - **Model:** a box the NMS kept (≥ 0.3).
   - **System:** a box the callback acts on (≥ 0.70, `CONFIDENCE_THRESHOLD`): tracked, zoned, spoken.
   - A box finds a labelled object when it has the same class and IoU ≥ 0.5.
5. **Verdicts are strict:** a case passes only if every image in it passes. The per-image counts are the more
   useful numbers.

### Results (`b1/b1-results.csv`, the tracker's columns; per image: `b1/b1-details.csv`)

| Case | What it checks | Result | Verdict |
|---|---|---|---|
| TC-001-001 | One image per class: right label at > 0.5 | **11 of 13.** Chairs: right class at 0.35. Tables: the top box was Doors at 0.35. The others 0.52–0.93 | Failed |
| TC-001-002 | No cross-labelling: Motorcycle/Bicycle, Bicycle/Tricycle, Chairs/Bench | **No image drew a box of its paired class** (0 of 15). 13 of 15 had their own class above 0.5; the 2 others were right but low: Bicycle 0.37, Tricycle 0.37 | Failed (low confidence, not cross-labelling) |
| TC-001-003 | Thinnest classes (Doors 328, Trash Bins 401, Tricycle 493 instances in the split) | **8 of 9** right at > 0.5; one door at 0.43 | Failed |
| TC-002-001 | Person over a lower-priority class | **1 of 3.** Where both objects reached the callback (person 0.84, bench 0.77), the person was announced. In the other two the person scored below 0.70, so the callback never saw it: nothing was announced in the trash-bin image, and the bench was announced in the other | Failed |
| TC-002-002 | The nearer of two people | **2 of 3** announced the nearer person. In the third, neither person reached 0.70 and an unlabelled armchair (Chairs 0.85, real) was announced. In all three the farther person was below 0.70 | Failed |
| TC-002-003 | Left / centre / right | **2 of 3** right (vehicle centre, stairs right). The left image's door got no box at all | Failed |
| TC-002-004 | Object in a boundary band | **The tracker and the code disagree.** A new object in the band is announced in the outer zone (left or right). An object already tracked keeps its previous zone (2 of 2, the brief's wording). The tracker says "must not flag any classes", which the code never does | Failed against the tracker's text |
| TC-002-005 | Empty scenes: no false positives | 377 images with empty labels. Boxes ≥ 0.3 in 72, ≥ 0.5 in 30, ≥ 0.70 in 16; all 16 produced an announcement. **By eye, 14 of those show a real person, vehicle, table, chair or door the dataset never labelled. 2 are false alarms** (a pantry item and a figurine, both "person"): **2 of 363** really empty images | Failed |
| TC-003-001 | Low light | Darkest tier (mean grey 1–3): **1 of 3.** Moderate tier (mean grey 20–40): **2 of 3**; in the miss, the model found person, chairs and table at ≥ 0.3 but only chairs reached 0.70 | Failed |
| TC-003-002 | Motion blur | **3 of 3** (Trash Bins 0.86, Animals 0.93, Vehicle 0.88) | **Passed** |
| TC-003-003 | Crowded: ≥ 70 % of instances | **0 of 3.** Cinema seats ×85: 0 % at 0.70 (2 % at 0.3). Café chairs ×56: 12 % (36 %). People in a plaza ×56: 5 % (18 %). Median instance 0.04–0.38 % of the image: very small, distant objects | Failed |
| TC-003-004 | Partial occlusion | **1 of 3** found both overlapping objects (two motorcycles 0.80 / 0.75). In the other two, one of the pair was not found at 0.70 | Failed |

### What the results show

- **Wrong-class errors were rare.**
  - No cross-labelling in the confusion pairs.
  - One class error among the 13 per-class images: a table read as a door, at 0.35, below the callback's
    threshold.
- **Most failures are objects the model saw at 0.3–0.70, below the 0.70 the callback requires.** For example:
  - the moderate low-light image (person, chairs, table at the model level; only chairs in the system);
  - the crowded café (36 % against 12 %);
  - the people in TC-002-001 and TC-002-002.

  The 0.70 threshold was not changed (the brief: no threshold changes). These results show what it costs in
  missed objects.
- **The decision logic did what it is written to do whenever the objects reached it.**
  - The person beat the bench.
  - The nearer person won.
  - The zones matched.
  - Hysteresis kept the previous zone.
- **Not a model error:** 14 of the 16 empty-scene announcements were real, unlabelled objects.
- **The previous tracker results** (all dated 2026-08-03) cannot be compared directly. They were recorded on an
  older model with images that no longer exist.

### Where the tracker and the brief differ, and what was done

| Case | Difference | What was done |
|---|---|---|
| TC-001-002 | The tracker names one pair, "like Motorcycle/Bicycle"; the brief names three | All three tested |
| TC-001-003 | The tracker's expected result is leftover template text (a password message) | TC-001-001's criterion applied |
| TC-002-004 | Tracker: "must not flag any classes within this zone". Brief: "keeps its previous zone" | Marked against the tracker; the brief's version reported too |

**Limits.**
- **Test images:**
  - These are dataset still images, not the device's camera.
  - Track ids are assigned per box, and each image is two identical frames.
  - The split is stored at 640 px or less on the long side.
- **TC-002-004:** the band positions are a real detection (the centre image's vehicle, 0.84) moved across. The
  zone depends on the model's box, and neither band image put a box the callback acts on inside a band. The
  bench's box came out centred at 0.155, outside the band; the stairs got no box.
- **"Nearer"** means the larger box: the code's proximity proxy.
- **Judgements made by eye:** the blur picks and the empty-scene review (`b1/b1-empty-scene-review.csv`).
- **"Thinnest" classes** come from the test split's counts, because the training set is not on the Pi.

## B2: the latency log

**Switch.** `SV_LATENCY_LOG=1` turns it on; `SV_LATENCY_DIR` names the output folder (default
`/tmp/second-vision-latency/<time>-<pid>`). Off (the default), `latency_log.LOG` is `None`, and every hook in
the app is a single `is None` test. No threshold, model or per-frame behaviour changed.

**What it records** (`src/second_vision/core/latency_log.py`, all on `time.monotonic()`):

| File | One row per | Columns |
|---|---|---|
| `haptic.csv` | motor packet the serial worker wrote | capture of the depth frame it came from; queued by the depth callback; taken by the serial worker; `port.write()` returned (and whether it succeeded); the L/C/R duties before the strength knob |
| `acks.csv` | serial-worker pass that found an ACK from the glasses board | when the worker noticed it |
| `audio.csv` | detection announcement the speech worker started | track id, label, zone, phrase, tier; capture of the track's first qualifying frame; capture of the frame that put it in the mailbox; when it did; when the speech process had been started; engine |
| `clock.txt` | run | one monotonic ↔ Unix pair, to line the rows up with the app's log |

- **Capture time** is the callback's arrival time minus the frame's age on the pipeline clock
  (`_pipeline_latency_ms`), the same figure as the `lat` in the `[DEPTH]` line.
- **The queue contract is unchanged** (AGENTS.md Rule 2). A queued dict is linked to its timings by object
  identity in a side table, which holds a reference to each tagged object and is bounded (512).
- **Never from the GStreamer thread:** rows are buffered and written by the module's own thread every 5 s and
  at exit.
- **ACKs have no sequence number.** The worker notices an ACK at its first check after the *next* write,
  about one frame later. The analysis pairs each packet with the first ACK noticed at least 1 ms after its
  write.

**Hooks.**

| File | Hook |
|---|---|
| `pipeline/callbacks.py` | `on_depth_frame` stores the frame's capture time. `_publish_motors` tags the packet. `_process_real_detections` notes each track's first qualifying frame and tags the winning payload. The winner is now picked with `max(candidates, …)`, the same choice as before, so its index can be found. |
| `workers/serial_worker.py` | Dequeue and write: the write result is kept in `sent`. ACK observation. |
| `workers/tts_worker.py` | Speech start, right after `_start_espeak()` returns. |

The `[DET] Frame N | lat X ms` line under the same flag was committed in Session A (`6d84ad3`).

**Tests.**
- `tests/test_latency_log.py` has 13 tests:
  - off by default; headers; identity matching; bounded table;
  - the haptic, ACK and audio rows;
  - the motor tag keeps the contract keys;
  - the detection payload; the speech worker; the serial worker against a fake board.
- `tests/test_det_latency_log.py` has 6 (committed in Session A).
- Full suite in the Poetry environment, where the GStreamer-level tests skip: **1097 passed, 217 skipped,
  exit 0** (`pytest-b2-final.log`).

**Analysis scripts.**
- `analyse_latency.py <run>` writes `latency-analysis.txt` and `latency-audio-rows.csv`. It leaves out the
  first 60 s, as the frame-rate figures do, and gives every figure with and without camera losses.
- `compare_b3.py` writes `b3-comparison.md`.
- `ack_probe.py` times the board's reply directly.

## B3: the 20-minute runs

**Protocol, as on 18 Oct.**
- `run_b3.sh` starts `scripts/sv-main.sh` through `perf_run.py`, the way the boot service does: headless,
  with no preview.
- It samples every 10 s for 1200 s, then stops the app with Ctrl+C.
- Added for this session: `SV_LATENCY_LOG=1`, the Hailo chip temperature every 10 s (Session A's sampler),
  and `--no-screenshots`. No camera image was saved (Kenth, 2026-11-11).

| Run | Time | Boards | Mode | Notes |
|---|---|---|---|---|
| `b3-standin-20min` | 04:41–05:01 | stand-in glasses on a pty (it acknowledges like the board), no panel: as on 18 Oct | both | Camera facing something close, as on 18 Oct. The camera-blocked alert was spoken 40 times |
| `b3-realboards-20min-depthonly` | 05:04–05:24 | real glasses board and panel | **depth**: DETECT read OFF | **Superseded.** Only its haptic timings are used below |
| `b3-realboards-20min` | 06:47–07:07 | real glasses board and panel; both rockers ON from 06:46:07 | **both throughout**: no mode change, 2,376 `[DET]` and 2,372 `[DEPTH]` lines | No speech: no detection was announced and no alert raised. Volume knob at minimum, earphone sink at 0.25 (Kenth asked for it quieter). Strength knob ~0.4 (details below) |

### Against 18 Oct (`b3-comparison.md`)

| Measure | 18 Oct (14-class) | Stand-in | Real boards |
|---|---|---|---|
| Average FPS (depth frames / 1200 s) | 29.6 (35,550) | 29.6 (35,580) | 29.6 (35,565) |
| 15-frame windows after minute 1: median / p5 / p1 / min | 30.0 / 28.5 / 20.2 / 18.9 | 30.0 / 28.6 / 20.4 / 19.0 | 30.0 / 28.5 / 20.4 / 18.0 |
| Capture → depth callback: median / p95 / p99 / max (ms) | 104 / 127 / 132 / 147 | 106 / 129 / 134 / 164 | 106 / 129 / 136 / 158 |
| Capture → detection callback: median / p95 / p99 / max (ms) | not logged then | 92 / 116 / 123 / 136 | 91 / 117 / 124 / 152 |
| App CPU, average (% of one core) | 73 % | 78 % | 77 % |
| Busiest core, average / max | 28 / 38 % | 30 / 40 % | 31 / **64** % (see below) |
| App memory, minute 1 → end | 195 → 199 MB | 200 → 203 MB | 199 → 204 MB |
| SoC temperature, first / min / max | 59.5 / 58.4 / 65.5 °C | 52.9 / 52.9 / 61.7 °C | 51.8 / 51.8 / 63.9 °C |
| Hailo-8 chip, start / max | not logged | 52.2 / 60.9 °C | 51.3 / 60.7 °C |
| Throttle flags (every 10 s) | 0x0 | 0x0 | 0x0 |
| Camera losses | | 0 | 0 |
| Dip episodes below 25 FPS (start minute, length) | 3.6, 19.3 s; 9.4, 19.4 s; 15.7, 19.5 s | 3.0, 19.2 s; 9.2, 19.4 s; 15.5, 18.7 s | 5.2, 19.3 s; 11.2, 28.9 s; 17.7, 19.3 s |

- **The 13-class model holds the 18 Oct real-time figures,** with either set of boards.
- **The three dips, about six minutes apart,** are present in all three runs.
- **Logging and CPU:** the logging was on in both B3 runs and off on 18 Oct. The stand-in run also spoke
  40 alert lines. Neither difference was isolated: there is no run with the logging off.

**My own activity during the real-boards run.** I was working on the Pi while it ran.
- **06:51:** a whole-disk `find` (nice 19).
- **06:56–07:00:** writing new Python files, which the editor's language server on the Pi analyses.

It lines up with the samples that differ:

| Pi clock | Sample | What it shows |
|---|---|---|
| ≈06:52:29 | 320.9 s | All four cores at 57–64 % while the app itself dropped to 62 %: the 64 % maximum |
| 06:58:40–07:00:00 | 692–772 s | The busiest core at 42–60 % |

The 11.2-minute episode is:
- the periodic dip, 06:58:32–06:58:41;
- two separate one-second blips, at 06:58:23 and 06:58:50, which the 30-second merge rule joined into
  28.9 s.

The blips and the core peak line up with that activity. I can't prove the cause from these logs. The
averages, percentiles and the other two dips are not affected by it.

**Strength knob in the real-boards run.**
- At 06:47:27–28 it was turned up to 0.99 and back; after that it stayed at 0.35–0.44.
- At 06:57:07 it read 0.0 once, for 53 ms (pot noise or a touch).
- The haptic rows record the duties before the knob, so the latency figures below do not depend on it.

### Haptic latency (`latency-analysis.txt` in each run folder; first 60 s left out; outside camera losses)

The two both-mode runs had no camera losses. The depth-only run had four (05:04:25, 05:06:40, 05:08:54,
05:11:53; 3–6 s each), so its column is the analysis's "outside camera losses" section.

| ms | Stand-in, both | **Real boards, both** | Real boards, depth only |
|---|---|---|---|
| Capture → UART write: median / p95 / p99 / max | 107 / 133 / 141 / 174 | **108 / 135 / 143 / 219** | 80 / 101 / 104 / 140 |
| of which capture → depth callback queued it (median) | 106 | 106 | 80 |
| queued → taken by the serial worker: median / p99 | 0 / 10 | 0 / 10 | 0 / 0 |
| Capture → ACK noticed: median / p95 / p99 / max | 150 / 192 / 205 / 246 | 150 / 189 / 204 / 287 | 114 / 135 / 139 / 5,989* |
| Write → ACK noticed: median / p95 | 47 / 77 | 46 / 66 | 32 / 36 |
| Packets / failed writes / no ACK noticed after | 33,780 / 0 / 1 | 33,769 / 0 / 2 | 32,887 / 0 / 1 |

\* **An artefact of the pairing, not the board.**
- The two longest ACK gaps in that run, 5.90 s and 5.72 s, are exactly two of the camera losses:
  05:11:53–05:11:59 and 05:08:54–05:09:00.
- The last packet written before a loss is paired with the first ACK noticed after the camera returns.
- Its frame was captured before the "Camera lost" line, so the exclusion does not catch it.
- With all rows (losses included), that run's capture → write maximum is 569 ms.

**The board's own reply,** timed directly with the app stopped (`ack-probe/`, 06:04, 200 heartbeats one at
a time at 115200 baud):
- 200 of 200 answered;
- round trip from Pi write to ACK read: median 1.05 ms, p95 1.07, p99 1.23, max 2.29.

**Against the thesis target of ≤ 320 ms from capture to vibration** (Gao et al., 2025):
- in the real-boards run, the largest capture → UART write was 219 ms and the largest capture → ACK noticed
  was 287 ms, over 33,769 packets;
- these stop at the UART write or ACK, not at motor onset, which is not measured.

What the haptic numbers show:
- **Capture to UART write.** With both models running, a motor packet leaves the Pi a median 108 ms after
  the camera captured its frame (p99 143, max 219, over 33,769 packets). 106 ms of that is camera to depth
  callback. The hand-off to the serial worker adds a median 0 ms, p99 10.
- **"ACK noticed" is the worker's polling, not the board.** The board answers in about 1 ms, and the worker
  notices the reply one frame later. Capture → ACK noticed is therefore an upper bound on capture → board
  received; capture → write + ~1 ms is the better estimate.
- **Depth only** is 28 ms faster at the median, with one network on the chip.
- **Camera drops recurred.** The depth-only run lost the camera four times in eight minutes, with the same
  "Could not read from resource" error as the drops first seen on 2026-10-15. Whether anyone touched the
  camera then is not recorded. The two both-mode runs had none.

**Not measured:**
- motor onset, the mechanical response after the board receives the packet;
- sound at the ear;
- capture → speech start in B3. Neither both-mode run announced a detection, so `audio.csv` has no rows.
  That figure needs a scene with objects: B4.

## B4: not run

**Not run, on the thesis writing plan Kenth relayed on 2026-11-12.** That plan answers Mam Vanessa #1 with:
- the targets: ≥ 25 FPS, and ≤ 320 ms from capture to vibration;
- Session A's latency per model;
- B3's capture → motor packet figures.

Speech delay goes in Limitations, with no number. The protocol is kept below in case it is wanted later; the
B2 log already supports it.

**The brief's protocol:**
- at least 20 trials of a person stepping into view about 2 m ahead and then leaving;
- more than 10 s between trials, so the reminder rules don't suppress announcements;
- reported per trial and as median / p95 / max:
  - (a) first qualifying frame → speech start;
  - (b) decision frame → speech start;
- not judged against 320 ms; the team sets the audio target in writing.

The B2 log records (a) and (b) itself, so nobody times anything by hand. It would need a person to step in
and out, a clear, lit area about 2 m in front of the camera, and Kenth's go-ahead for a person to be in view.
No image is saved.

## Device state now

- **`second-vision.service` is stopped;** Kenth restarts it.
- **PipeWire earphone volume** was lowered from 0.78 to 0.25 at Kenth's request (`device-changes.txt`).
  Restore it with `XDG_RUNTIME_DIR=/run/user/1000 wpctl set-volume @DEFAULT_AUDIO_SINK@ 0.78`.
- **Panel knobs** are physical: strength ~0.4, volume at minimum.

## Draft decision entry, for Kenth to review

> ## D44 (draft): Latency is measured by an opt-in log; the 13-class model keeps the 18 Oct real-time figures
>
> *From Session B on the device, 2026-11-12 (`results/panel-revisions/session-b/`). Numbered after Session
> A's D43 draft; renumber if needed.*
>
> **Decision.**
> - End-to-end timing is measured by `core/latency_log.py`, switched on with `SV_LATENCY_LOG=1` and off by
>   default.
> - It links queued items to their timings by identity in a side table, so the queue contract (Rule 2) is
>   unchanged.
>
> **Measured** with the deployed 13-class model, both models, over 20 minutes, against 18 Oct:
> - 29.6 FPS average; 15-frame windows median 30.0, p1 20.4;
> - capture → depth callback median 106 ms, p99 136;
> - app CPU 77 %, no throttling.
>
> With the real boards:
> - capture → UART write of the motor packet: median 108 ms, p99 143, max 219;
> - the glasses board answers in ~1 ms (median 1.05 ms, 200/200).
>
> **The 12 detection tracker cases** were re-run on test-split images:
> - wrong-class errors were rare (no cross-labelling in the confusion pairs);
> - most misses were objects the model saw below the callback's 0.70 threshold;
> - the decision logic (priority, nearest instance, zones, hysteresis) behaved as written whenever objects
>   reached it.
>
> **Not measured:** motor onset, and sound at the ear (the speech delay goes in Limitations).

## Files (`session-b/`)

| File | What |
|---|---|
| `b1/select_b1.py`, `b1/hef_predict.py`, `b1/evaluate_b1.py` | B1: image selection, the model on the chip, scoring through the real callback |
| `b1/selection.json`, `b1/predictions.json` (+ `.txt`, `.meta.json`), `b1/b1-results.csv`, `b1/b1-details.csv`, `b1/b1-empty-scene-review.csv`, `b1/b1-console.txt` | B1 inputs and results. `b1/images/` (links into `~/v3p_eval`) and `b1/image-stats.csv` (477 kB) are regenerated by `select_b1.py` and stay out of git |
| `run_b3.sh`, `wait_then_realboards.sh` | Runners. The second waits for the panel to report `M:both`, reading only |
| `analyse_latency.py`, `compare_b3.py`, `ack_probe.py` | Analysis and probe |
| `b3-standin-20min/`, `b3-realboards-20min/`, `b3-realboards-20min-depthonly/` | Per run: app log, sampler CSV and summary, Hailo temperatures, latency CSVs, analyses, plot |
| `b3-standin-1min/` | Short check of the runner before the 20-minute runs |
| `b3-comparison.md`, `ack-probe/` | Results above |
| `pytest-b2.log`, `pytest-b2-final.log` | Test runs; the final one is after the ACK-pairing fix |
| `device-changes.txt`, `*-console.txt` | Device changes; runner consoles |

**Kept on the Pi, not in git** (the brief: large raw logs stay out):
- `*/latency/haptic.csv` (2.7–3.3 MB each) and `*/latency/acks.csv`;
- the app logs and pytest logs (`*.log` is gitignored).

The analyses above are derived from them, and `analyse_latency.py` regenerates them on the Pi.
