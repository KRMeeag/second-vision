# Second Vision: normalized requirements

Derived from the UAT workbook `tests/Group2-SV_Test-Tracker.xlsx` (110 cases, 32
scenarios, 10 modules). This document restates what the UAT cases expect in a form a
developer can implement and a tester can check. Trace each requirement to its cases,
code, tasks and tests in [TRACEABILITY.md](TRACEABILITY.md).

## How to read this document

**Authority.** The workbook is authoritative. A requirement here never adds behaviour the
workbook doesn't state. The one exception is a decision recorded in
[DISAGREEMENTS.md](DISAGREEMENTS.md) (D-xx) that the code wins or that a case is out of
scope. Such requirements cite the D-entry, and the workbook is flagged for correction.

**Quotes.** Text in quotation marks is copied verbatim from the workbook's Expected
Results. Full case text, including steps and inputs, is in
[uat-snapshot.yaml](uat-snapshot.yaml).

**AMBIGUOUS** marks a requirement whose pass/fail can't be decided from the workbook. The
question listed must be answered by a person before the requirement is tested as
pass/fail. Agents must not resolve it by guessing.

**Resolved** marks an ambiguity that has been answered, with the date and who decided it:
- *team*: answered by the team.
- *delegated*: the team asked the agent to choose using common practice. Change it if it
  doesn't fit.

**The vision algorithms are verified, not changed** (team, 2026-10-12). The detection model
and the depth post-processing were tested by hand and are accepted. Sections 1, 2 and 6
check that behaviour; work arising from this document is about robustness and feedback,
never about re-tuning those algorithms.

**Steps and inputs** in the workbook may be changed when testing, as long as the
requirement and the case's intent stay the same. Every change is logged in
[DEVIATIONS.md](DEVIATIONS.md). Expected results are not changed that way; only the
workbook (or a D-entry) changes them.

| Field | Values |
|---|---|
| Type | functional · error-handling · edge-case · hardware · UI · integration |
| Scope | **IN**: testable now. **NOT-BUILT**: in scope, feature missing (see its task). **OUT-ESP32**: runs on the ESP32 itself; outside the agentic workflow. **OUT-SCOPE**: outside the project's scope. **DEFERRED**: set aside for now. |
| Priority | **P1**: critical failsafes (loss of connection, camera checks). Built and tested first (decision 2026-10-11). **P2**: everything else in scope. |

**Standard preconditions (P-std)**, from the workbook's module headers:
- Raspberry Pi 5 is on.
- Dependencies from `pyproject.toml` are installed.
- The `my_hailo_env` virtual environment is active.
- The tester is on the Pi directly or over SSH (same network or LAN cable if SSH).
- Environment: Debian 13 (Trixie); SSH client Windows 11 or macOS.

---

## 1. Object detection (DET): TC-001 to TC-003

Module M01 · *"Verify the reliability of solely the YOLOv8-enabled object detection
algorithm via its callback function"* · Preconditions: P-std. Each requirement runs still
images through the detection callback. The steps' `setup_env.sh` and `tests/obj_det_unit/`
don't exist (D-08).

**REQ-DET-01: Each model class is recognized**
- Statement: an image with a single, centred object of any one model class, in clear
  conditions, returns that class's own label with confidence > 0.5.
- Source: TC-001-001: "All 13 images return the correct class label with confidence > 0.5".
- Decision D-06: the model has **14** classes, so 14 images.
- Type: functional · Scope: IN · Priority: P2
- Acceptance: 14 images, one per class; each returns its own label at > 0.5.
- Resolved (team, 2026-10-12): reporting Bicycle and Tricycle as "vehicle" was a stop-gap
  for the earlier model. Each class gets its own label. Today this fails until task T-05.

**REQ-DET-02: Similar classes are not confused**
- Statement: images of visually similar classes each return their own label.
- Source: TC-001-002: "Each image returns its own correct label — no cross-labeling between
  classes like Motorcycle/Bicycle".
- Type: functional, edge-case · Scope: IN · Priority: P2
- Acceptance: every confusion-pair image returns its own class. The workbook names only
  Motorcycle/Bicycle; other pairs are the tester's choice, logged in DEVIATIONS.md.
- Note: depends on T-05, because Bicycle is currently reported as "vehicle".

**REQ-DET-03: Classes with the least training data are recognized too**
- Statement: images of the class that had the fewest training examples ("thinnest-sourced")
  return its own label with confidence > 0.5, the same bar as REQ-DET-01.
- Source: TC-001-003. Input: "Load test images for the thinnest-sourced classes and run each
  through the callback". The workbook's expected result is leftover template text: "System
  displays error 'Password must be at least 8 characters'; account is not created." (W-01).
- Resolved:
  - (delegated, 2026-10-12) The point of the case is that weakly represented classes aren't
    worse than the rest, so it uses REQ-DET-01's bar.
  - (team, 2026-10-15) The thinnest-sourced class is **Trash Bins**, with 1702 training
    images. Per-class counts aren't in the repo; `models/hef_deploy/metadata.yaml` lists names
    only.
- Type: functional, edge-case · Scope: IN · Priority: P2
- Acceptance: several Trash Bins images, each returning "trash bin" at confidence > 0.5. A
  single image would repeat REQ-DET-01, so test more than one.

**REQ-DET-04: Higher-priority class wins**
- Statement: when classes of different priority are in one frame, the more important class
  is the one output.
- Source: TC-002-001: "System prioritizes the more important class (person) as the output for
  that image" (person with a trash bin).
- Type: functional · Scope: IN · Priority: P2
- Acceptance: person + trash bin gives person. "Trash Bins" is a model class since `eda45cd`.

**REQ-DET-05: Nearest instance wins**
- Statement: among instances of the same class, the one closest to the camera is output.
- Source: TC-002-002: "System prioritizes the instance closest to te camera".
- Type: functional · Scope: IN · Priority: P2
- Acceptance: two people at different distances gives the nearer one.

**REQ-DET-06: Detections carry the correct direction**
- Statement: each detection is labelled left, center or right according to its position in
  the frame.
- Source: TC-002-003: "System must be able to correctly flag each instance with their
  respective directions".
- Type: functional · Scope: IN · Priority: P2
- Acceptance: three images (object left, centre, right) each get the matching zone.

**REQ-DET-07: Boundary band holds the previous zone**
- Statement: an object whose centre is inside a boundary band keeps the zone it had before
  entering the band. It is not dropped.
- Source: TC-002-004: "System must not flag any classes within this zone". **Superseded by
  D-05 (code wins)**; the workbook is to be corrected.
- Type: edge-case · Scope: IN · Priority: P2
- Acceptance: an object moving from centre into the centre/right band is still "center"; one
  moving from right into the same band is still "right".

**REQ-DET-08: No detections on an empty scene**
- Statement: a frame with no object of a model class produces no detection output.
- Source: TC-002-005: "Confirms no false positives on frames with no trained-class object".
  The live equivalent is REQ-DTA-03.
- Type: error-handling, edge-case · Scope: IN · Priority: P2
- Workbook result: **Failed** (pre-dates the 14-class model; W-21).

**REQ-DET-09: Low light**
- Statement: expected classes in a low-light image are still detected; lower confidence is
  acceptable.
- Source: TC-003-001: "System still manages to detect the expected classes despite the
  lighting condition, although with lower confidence".
- Type: edge-case · Scope: IN · Priority: P2

**REQ-DET-10: Motion blur**
- Statement: the class in a motion-blurred image is still correctly identified.
- Source: TC-003-002: "System still manages to correctly identify the class contained within
  the motion blur test iamge".
- Type: edge-case · Scope: IN · Priority: P2

**REQ-DET-11: Crowded scenes**
- Statement: in an image with many instances of one class, the detector finds at least 70 %
  of them.
- Source: TC-003-003: "System manages to identify at least 70% of the number of instances of
  that particular class in the image".
- Resolved (delegated, 2026-10-12): counted on what the model detects (one box per object, of
  the right class), before the callback chooses what to say. The speech side announces only
  the single most important detection at a time by design (D30), so "70 % announced" can never
  hold. The model's detections are the only meaningful place to count.
- Type: edge-case · Scope: IN · Priority: P2
- Acceptance: boxes of the right class / instances visible in the image ≥ 0.70.

**REQ-DET-12: Partial occlusion**
- Statement: classes are still identified when partly blocked by other objects.
- Source: TC-003-004: "System still manages to identify classes even if they are partially
  blocked by other instances or classes".
- Type: edge-case · Scope: IN · Priority: P2

## 2. Depth estimation (DEP): TC-004 to TC-006

Module M02 · *"converts the camera feed into per-zone proximity readings … via its callback
function"* · Preconditions: P-std plus IMX291 camera. Workbook environment note: "Depth output
is in relative units, not metres — thresholds are still being calibrated."

**How the depth cases are judged.** Resolved (team, 2026-10-12): depth estimation is built,
was tested by hand, and the team is satisfied, so these cases are judged by what the current
code does. The code is not re-tuned.
- **Where to look.** Every 15 frames the program prints a `[DEPTH]` line with two rows: what
  the camera saw per zone (`L= C= R=`, 0 to 255), and what the motors were told
  (`motor L=… C=… R=…`). The second row is what the wearer feels.
- **"Warning" or "trigger"** means that zone's motor row is non-zero: the wearer would feel it.
- **"At or near 0" or "no warning"** means the motor row is zero. The code deliberately turns
  faint readings into exact silence (a noise floor of 0.18 on a 0-to-1 scale in
  `core/haptics.py`), so the camera row may show small values while the motors stay off.
  That counts as zero.
- **Distances in metres** ("3 m", "1 m") are instructions for setting up the scene, not
  limits to measure. The depth output is relative, not metres.
- **Run depth checks at full strength.** The motor row is printed *before* the panel's
  strength knob is applied, so it matches what the wearer feels only at full strength. That
  is the default when no panel is connected.

**REQ-DEP-01: Open space is silent**
- Statement: with nothing nearby, all three zones stay at zero.
- Source: TC-004-001: "All three zone values stay at or near 0 — open space produces no
  warning" (open corridor, nothing within 3 m).
- Type: functional · Scope: IN · Priority: P2
- Acceptance: the motor row is zero in all three zones for the whole observation.

**REQ-DEP-02: Readings rise and fall with distance**
- Statement: approaching an obstacle raises its zone value steadily; backing away lowers it.
  The value never drops while still closing in.
- Source: TC-004-002: "Zone values rise steadily as you approach and fall as you back away;
  the value never drops while you are still closing in".
- Type: functional · Scope: IN · Priority: P2
- Acceptance: the sequence is non-decreasing on approach and non-increasing on retreat.

**REQ-DEP-03: Only the obstacle's zone reacts**
- Statement: an obstacle on one side raises only that side's zone; the opposite side stays
  at or near zero.
- Source: TC-004-003: "Only the left zone rises for the left placement, only the right zone for
  the right placement; the opposite side stays at or near 0".
- Type: functional · Scope: IN · Priority: P2
- Acceptance: the obstacle's zone has a non-zero motor value; the opposite side's is zero.

**REQ-DEP-04: Blank walls are obstacles**
- Statement: a plain, textureless wall filling the view reads as a strong centre warning, and
  the debug screen tags that zone as a near surface. It is not read as open space.
- Source: TC-005-001: "The centre zone reads a strong warning and the debug screen tags the zone
  as a near surface — the wall is not read as open space".
- Type: functional, UI · Scope: IN · Priority: P2
- Acceptance: the centre motor value is non-zero and not lower than either side, and the debug
  screen tags the centre zone as a near surface. How strong is "strong" is the team's judgement
  at the scene, as in their own manual test.

**REQ-DEP-05: Thin obstacles are caught**
- Statement: a thin vertical object (pole, cane) to one side raises a warning in its zone,
  even though most of the zone is far.
- Source: TC-005-002: "The zone containing the pole raises a warning even though the object is
  narrow and most of the zone is far".
- Type: edge-case · Scope: IN · Priority: P2
- Acceptance: the motor value of the pole's zone is non-zero.
- Note: the dedicated thin-object detector is off by default
  (`test_thin_is_off_by_default_and_still_auditable`). That affects whether this passes, not
  what it requires.

**REQ-DEP-06: Texture is not an obstacle**
- Statement: a textured but open scene (furniture no closer than 3 m) raises no warning; floor
  and furniture texture produce no false near reading.
- Source: TC-005-003: "No zone raises a warning; furniture and floor texture do not produce a
  false near reading".
- Type: edge-case · Scope: IN · Priority: P2
- Acceptance: the motor row is zero in all three zones.
- Workbook result: **Failed**, before floor suppression was added (W-22).

**REQ-DEP-07: Drop-off hazard: OUT-SCOPE (D-03)**
- Source: TC-006-001: "A hazard is detected in the proper direction of “down” and a severity
  value; a drop-off banner appears in the debug screen."

**REQ-DEP-08: Step-up hazard: OUT-SCOPE (D-03)**
- Source: TC-006-002: "A hazard is reported with direction "up". The warning appears within a
  fraction of a second, not on the very first frame".

**REQ-DEP-09: Stable output on a still scene**
- Statement: with the camera and scene still, the zone values stay steady, with no flicker or
  jumps.
- Source: TC-006-003: "The zone values remain constant without any blinking or jumping to
  totally different values."
- Type: edge-case · Scope: IN · Priority: P2
- Resolved (delegated, 2026-10-12): camera noise means values are never bit-identical, so
  "constant" is checked over 60 s of a still scene:
  - no zone switches between silent and active ("blinking");
  - each zone's camera value stays within ±10 % of full scale (±26 of 255) of its own median
    ("jumping").

## 3. Audio output (AUD): TC-007 to TC-009

Module M03 · *"audio driver initialization, script execution, and sound output clarity"* ·
Preconditions: P-std plus bone-conduction earphones connected. Every script and `.wav` file the
steps reference is missing (D-08).

**REQ-AUD-01: Earphones are recognized**
- Statement: the system's audio hardware lists the connected earphones, with no kernel errors.
- Source: TC-007-001: "Audio hardware interface detects connected bone conduction output device
  cleanly without kernel errors."
- Type: hardware · Scope: IN · Priority: P2

**REQ-AUD-02: Audio starts without driver exceptions**
- Statement: initializing audio output in the project environment raises no driver exceptions.
- Source: TC-007-002: "Virtual environment loads required audio bindings from pyproject.toml and
  initializes audio device without driver exceptions."
- Type: hardware, functional · Scope: IN · Priority: P2
- Note: `init_audio.py` doesn't exist (D-08). Speech runs through espeak-ng
  (`workers/tts_worker.py`), so the test may drive it instead (log in DEVIATIONS.md).

**REQ-AUD-03: Clean tone through the transducers**
- Statement: a 440 Hz test tone plays cleanly through the bone-conduction transducers.
- Source: TC-007-003: "Clean 440Hz audible sine wave transmitted through bone conduction
  transducers with no clipping or driver latency."
- Type: hardware · Scope: IN · Priority: P2
- Acceptance: a listener's judgement (manual).

**REQ-AUD-04: Speech starts within 500 ms**
- Statement: a short phrase is synthesized and heard clearly through the transducers within
  500 ms.
- Sources:
  - TC-008-001: "Speech engine synthesizes text input and routes clear speech audio to
    transducers within <500ms."
  - TC-008-004: same bound.
- Type: functional · Scope: IN · Priority: P2
- Resolved (delegated, 2026-10-12), following the usual way speech latency is measured:
  - **Start:** the moment the message is handed to the speech system.
  - **End:** the moment speech is first audible.
  - **Pass:** 95th percentile ≤ 500 ms over 20 trials, so one slow run doesn't decide it and
    the typical case is still bounded.
  - **How:** the test layer chooses the measurement method (e.g. a microphone recording, or an
    instrumented stand-in) and logs it in DEVIATIONS.md.

**REQ-AUD-05: Speech under heavy load**
- Statement: under heavy CPU and Hailo load with rapid successive messages, whatever is spoken
  plays without buffer underruns, popping or distortion.
- Source: TC-008-002: "TTS pipeline queues and streams audio sequentially without buffer
  underruns, popping, or voice distortion."
- Resolved (team, 2026-10-12; D-10): the speech system keeps only the most important pending
  message and drops lower-priority ones on purpose, so not all 20 are spoken. The case checks
  audio quality, not that every message gets through.
- Type: functional, error-handling · Scope: IN · Priority: P2

**REQ-AUD-06: Numbers and symbols are spoken correctly**
- Statement: text containing numbers, decimals and symbols is spoken completely and correctly.
- Source: TC-008-003: "TTS engine normalizes numbers/symbols accurately and speaks the complete
  phrase cleanly."
- Type: edge-case · Scope: IN · Priority: P2
- Acceptance: a listener's judgement (manual).
- Note: the system's own phrases are `"<label> <zone>"`. This exercises espeak-ng itself.

**REQ-AUD-07: A detection message is spoken as its phrase**
- Statement: a detection message in the agreed contract (`{"label", "zone", …, "phrase"?}`) is
  spoken as its `phrase` if present, otherwise as `"<label> <zone>"` (e.g. "person left").
  The 500 ms bound of REQ-AUD-04 applies.
- Source: TC-008-004: "The translation function correctly parses the object keys, synthesizes
  the exact string "Person at two meters," and routes clear speech audio to transducers within
  <500ms." **Superseded by D-07 (code wins).**
- Type: functional, integration · Scope: IN · Priority: P2

**REQ-AUD-08: Urgent alerts interrupt current speech**
- Statement: when an urgent item arrives while the device is speaking, it stops the current
  sentence and speaks the urgent item at once, clearly.
- Source: TC-009-001: "High-priority alert sound plays instantaneously with clear transient
  response over bone conduction transducers."
- Resolved (team, 2026-10-15; D-11): the case is about the interruption, not a separate beep
  sound. No beep-sound feature is built. The workbook's step (playing `obstacle_beep.wav`) is
  replaced by triggering an urgent item mid-sentence, logged in DEVIATIONS.md.
- Type: functional, integration · Scope: IN · Priority: P2
- Note: the interruption is built and unit-tested (`test_worker_preempted_by_urgent`). What
  remains is the live check that it sounds right through the transducers.

**REQ-AUD-09: Speech frequencies are clear**
- Statement: across 200 to 4000 Hz, output stays audible and clear in the speech mid-range,
  without the frame buzzing.
- Source: TC-009-002: "Audio output remains audible and clear within mid-range speech
  frequencies without buzzing frame housing."
- Type: hardware · Scope: IN · Priority: P2
- Acceptance: a listener's judgement (manual). The sweep file is missing and may be generated
  (DEVIATIONS.md).

**REQ-AUD-10: No extra delay over SSH**
- Statement: a speech command started over SSH plays through the Pi's audio output without
  network-induced delay.
- Source: TC-009-003: "SSH remote command invocation delivers audio through physical RPi 5 port
  without network-induced playback delay."
- Type: hardware · Scope: IN · Priority: P2
- Resolved (delegated, 2026-10-12): the audio must come out of the Pi itself, not be streamed
  to the SSH client. Measured on the Pi, from the command starting to speech starting, over 10
  runs each, the median for SSH runs is within 50 ms of the median for local runs. The time the
  command spends crossing the network before it starts belongs to the network, not to playback,
  and isn't counted.

## 4. ESP32 haptic firmware (HAP): TC-010 to TC-012: OUT-ESP32

Module M04 · *"Validates the C++ logic on the Seeed Studio XIAO ESP32 in isolation"*. These run
on the ESP32 and are outside the agentic workflow. They are kept here so every case traces to a
requirement. Wire format per D-02: binary frames, not JSON.

| ID | Requirement | Source (verbatim expected) | Type | Scope |
|---|---|---|---|---|
| REQ-HAP-01 | Motor values drive proportional PWM per motor | TC-010-001: "ESP32 parses JSON; Left motor pulses strong, Center medium, Right silent" | functional | OUT-ESP32 (D-02) |
| REQ-HAP-02 | Each valid packet is acknowledged within a defined window | TC-010-002: "ESP32 transmits an acknowledgment back to the Pi within a defined window (e.g. <20ms) confirming packet receipt" | functional, integration | OUT-ESP32 |
| REQ-HAP-03 | All-zero means silence; all three motors near maximum together causes no brownout | TC-010-003: "Step 1: all motors off, no residual buzz. Step 2: all three fire concurrently with no voltage sag / brownout on the ESP32" | hardware, edge-case | OUT-ESP32 |
| REQ-HAP-04 | PWM capped at 153 | TC-011-001, TC-011-002: "…strictly caps output at 153 (60% duty cycle)…", "Mapping curve clips exactly at 153…" | error-handling, edge-case | **Superseded (D-01): 255 is safe** |
| REQ-HAP-05 | Out-of-range motor values clamp safely, with no wraparound | TC-011-003: "Both clamp safely to 0 or max — no integer wraparound producing an unintended high PWM value" | error-handling | OUT-ESP32 |
| REQ-HAP-06 | Missing fields default to 0 | TC-012-001: "Failsafe doc["left"] \| 0 triggers; Center actuates, Left/Right default 0. No crash" | error-handling | OUT-ESP32 (JSON-only; see D-02) |
| REQ-HAP-07 | Link loss stops the motors; the next valid packet resumes them without a reset | TC-012-002: "Disconnect: all PWM drops to 0 immediately. Reconnect: normal operation resumes on the next valid packet without requiring a manual ESP32 reset" | error-handling, hardware | OUT-ESP32 |
| REQ-HAP-08 | Corrupt bytes are discarded with no PWM spike | TC-012-003: "deserializeJson() returns an error; ESP32 discards the packet and holds last-safe state (or zeroes out) — no crash, no unintended PWM spike" | error-handling | OUT-ESP32 |

## 5. Detection to speech (DTA): TC-013 to TC-015

Module M05 · *"producer-consumer threads of the object detection callbacks and bone conduction
feedbacks"* · Preconditions: P-std plus earphones and IMX291 camera. Code involved: detection
callback, then the speech mailbox (`core/priority.py`), then the TTS worker.

**REQ-DTA-01: A single detection reaches the speech mailbox**
- Statement: with one object of a model class in view, the detection callback offers a detection
  message to the speech mailbox.
- Source: TC-013-001: "Object detection callback triggers and successfully enqueues the detection
  event string to the thread-safe queue."
- Decisions: per D-07 the message is the contract dict, not a string. Per D-06 a cup isn't a
  class, so an in-model class is used.
- Type: integration · Scope: IN · Priority: P2

**REQ-DTA-02: Multiple detections are prioritized**
- Statement: with several objects in view, the callback processes every box and the mailbox
  receives prioritized messages.
- Source: TC-013-002: "Callback processes all bounding boxes and enqueues batched or prioritized
  messages into the queue." The workbook accepts either; the design is prioritized (D30).
- Type: integration · Scope: IN · Priority: P2

**REQ-DTA-03: A covered lens produces no messages**
- Statement: with the lens covered, the detection side keeps running but offers nothing; the
  mailbox stays empty.
- Source: TC-013-003: "Producer thread remains active but does not enqueue any false positive
  messages; queue remains empty." Image-level equivalent: REQ-DET-08.
- Type: error-handling, edge-case · Scope: IN · Priority: P2

**REQ-DTA-04: The speech worker speaks what it receives**
- Statement: a message placed in the mailbox is taken by the speech worker and spoken through the
  earphones.
- Source: TC-014-001: "Consumer thread wakes up, dequeues the message, and triggers the bone
  conduction audio feedback." Message format per D-07.
- Type: integration · Scope: IN · Priority: P2

**REQ-DTA-05: Spaced messages are spoken in order**
- Statement: messages arriving 2 s apart are each spoken, in order, without overlapping,
  stuttering or crashing.
- Source: TC-014-002: "Consumer processes each sequentially, playing audio without overlapping,
  stuttering, or crashing."
- Type: integration · Scope: IN · Priority: P2

**REQ-DTA-06: A message flood doesn't hang the system**
- Statement: a flood of messages (50 in 1 s) is handled by dropping lower-priority messages or
  buffering, with feedback staying real-time and nothing hanging.
- Source: TC-014-003: "Consumer thread drops low-priority messages or buffers them gracefully,
  maintaining real-time feedback without system hang."
- Type: error-handling, edge-case · Scope: IN · Priority: P2

**REQ-DTA-07: No concurrency faults under a dynamic scene**
- Statement: for 3 minutes of objects moving in and out of frame, the detection and speech threads
  run with no race conditions, deadlocks or segmentation faults.
- Source: TC-015-001: "Both threads operate concurrently without race conditions, deadlocks, or
  segmentation faults."
- Type: integration · Scope: IN · Priority: P2

**REQ-DTA-08: Ctrl+C shuts down cleanly**
- Statement: Ctrl+C while detecting and speaking stops both threads after their current loop,
  with no zombie processes.
- Source: TC-015-002: "Both threads receive the stop signal, finish their current loop, and
  terminate gracefully without leaving zombie processes."
- Type: error-handling · Scope: IN · Priority: P2

**REQ-DTA-09: Stable resources over 10 minutes**
- Statement: over a continuous 10-minute run, CPU use stays stable and memory doesn't leak.
- Source: TC-015-003: "Combined CPU usage of the Python threads remains stable without memory
  leaks."
- Type: integration · Scope: IN · Priority: P2
- Resolved (delegated, 2026-10-12): sample the process's CPU and resident memory every 10 s and
  ignore the first minute (start-up allocations). After that:
  - memory at the end is no more than 5 % above memory at minute 1, with no steady upward climb;
  - average CPU over the last 3 minutes is within 10 percentage points of the first 3.
- Note: 10 minutes only catches fast leaks. The battery rundown in REQ-PERF-01 covers the long run.

## 6. Depth to haptics (DHP): TC-016 to TC-018

Module M06 · *"end-to-end pipeline between the SC-DepthV3 neural network and the ESP32
hardware"* · Preconditions: P-std plus glasses ESP32 on UART and IMX291 camera. Docker is out of
scope (D-08).

**REQ-DHP-01: Centre motor ramps up on approach**
- Statement: an object approaching head-on from 3 m to 1 m gives no vibration at the start, then
  the centre motor scales up as the distance closes.
- Source: TC-016-001: "No feedback at 3m; center motor scales up as distance closes".
- Type: integration, functional · Scope: IN · Priority: P2
- Acceptance (section 2's rules): the motor row is zero at the starting position; the centre
  motor value then never falls while the object keeps closing in. "3 m" is scene setup.

**REQ-DHP-02: Vibration follows a sideways sweep**
- Statement: an object sweeping left to right at 1 m moves the vibration left, then centre, then
  right.
- Source: TC-016-002: "Vibration transitions Left→Center→Right seamlessly".
- Type: integration, functional · Scope: IN · Priority: P2
- Resolved (delegated, 2026-10-12): during a steady sweep, "seamlessly" means:
  - the felt zone goes left, centre, right in that order, crossing each boundary once (no
    back-and-forth);
  - there is no silent gap longer than 250 ms while the object is in view.
- Note: by design, an object straddling the centre and a side zone is felt on the centre only.
  The centre handover can therefore come a little late; that is expected, not a failure.

**REQ-DHP-03: Diagonal approach (uses REQ-DHP-01 and 02's acceptance)**
- Statement: during combined sideways and forward motion, zone and intensity stay correct
  throughout.
- Source: TC-016-003: "Zone classification and intensity scaling stay correct under compound
  motion".
- Type: integration, edge-case · Scope: IN · Priority: P2

**REQ-DHP-04: Patterned floors don't trigger haptics**
- Statement: walking for 30 s over a flat, highly textured surface produces zero haptic triggers.
  This holds on a second, different texture too.
- Sources:
  - TC-017-001: "0 false triggers logged over the 30s window".
  - TC-017-003: "Still 0 false triggers".
- Type: edge-case · Scope: IN · Priority: P2
- Acceptance: a trigger is any non-zero motor value (section 2's rules); zero of them over the 30 s.

**REQ-DHP-05: A real drop-off on a patterned floor: OUT-SCOPE (D-03)**
- Source: TC-017-002: "Haptic does fire correctly for the real hazard".

**REQ-DHP-06: Depth failure is isolated: DEFERRED (D-08)**
- Source: TC-018-001: "DE_Pipeline_Status False, motors stop, YOLOv8 continues".

**REQ-DHP-07: Depth recovers without a reboot: DEFERRED (D-08)**
- Source: TC-018-002: "DE_Pipeline_Status flips back True, haptic resumes automatically — no full
  reboot needed".

**REQ-DHP-08: An unresponsive ESP32 never hangs the Pi**
- Statement: when the glasses ESP32 browns out or resets mid-run, the Pi detects the unresponsive
  link by timeout, logs or queues an error, and never blocks waiting for acknowledgements.
- Source: TC-018-003: "Pi detects the unresponsive UART peer via timeout, logs/queues an error, and
  does not hang indefinitely waiting on ACKs".
- Type: error-handling, integration · Scope: IN · Priority: **P1** (loss of connection)
- Note: built in `workers/serial_worker.py` (link down/up detection, write timeouts).

## 7. BMI160 IMU (IMU): TC-019 to TC-021: OUT-ESP32

Module M07 · *"BMI160 read reliability, gyroscope deadband logic, and I2C fault recovery on the
ESP32-C3, in isolation"*. Runs on the ESP32; outside the agentic workflow.

| ID | Requirement | Source (verbatim expected) | Type | Scope |
|---|---|---|---|---|
| REQ-IMU-01 | Readings stay physically sane, with no stale values | TC-019-001: "All readings stay within sane physical ranges, no NaNs, no repeated stale/duplicate values" | functional, hardware | OUT-ESP32 |
| REQ-IMU-02 | Orientation within ±5° | TC-019-002: "Reported values track physical rotation within ±5° tolerance" | functional, hardware | OUT-ESP32 |
| REQ-IMU-03 | I2C read failures are detected | TC-019-003: "ESP32 detects the read failure (NACK/timeout) rather than silently returning garbage data" | error-handling | OUT-ESP32 |
| REQ-IMU-04 | Jitter below the deadband sends no turn signal | TC-020-001: "No false head-turn signal sent to the Pi" | functional, edge-case | OUT-ESP32 |
| REQ-IMU-05 | Turns past the deadband signal the correct direction and reset | TC-020-002: "Signal fires in the correct direction past threshold, resets cleanly at center" | functional | OUT-ESP32 |
| REQ-IMU-06 | Pitch beyond the limit clamps or is flagged | TC-020-003: "Output clamps or flags at the limit rather than producing an unbounded/out-of-spec signal" | edge-case | OUT-ESP32 |
| REQ-IMU-07 | A brief I2C brownout is recovered | TC-021-001: "ESP32 detects the failed read, doesn't hang the main loop, auto-reinitializes the BMI160 once restored" | error-handling | OUT-ESP32 |
| REQ-IMU-08 | An IMU fault never blocks haptics | TC-021-002: "ESP32's UART handling and PWM output continue unaffected — an IMU fault must never block haptic feedback" | error-handling | OUT-ESP32 |
| REQ-IMU-09 | Repeated I2C faults are survived | TC-021-003: "No crash, memory leak, or permanent failure state; IMU re-initializes successfully every cycle" | error-handling | OUT-ESP32 |

## 8. Peripheral connectivity and failsafes (CON): TC-022 to TC-026: P1

Module M08 · *"Verify the robustness of the connections between the Raspberry Pi and its Peripheral
Components"* · Preconditions: P-std plus the components connected.

Links as configured in `scripts/sv-main.sh`:
- glasses ESP32: UART `/dev/serial0` on the GPIO header
- control panel: `/dev/ttyAMA3`
- camera and earphones: USB

What's built today (2026-10-18):
- Link-down and link-up detection for the glasses ESP32 (`workers/serial_worker.py`).
- Detection of 30 s of panel silence (`workers/config_reader.py`, log only).
- The repeating spoken-alert scheduler (`core/alerts.py`, T-00a) and the camera no-crash core
  (T-03a, T-03b). The camera's spoken alert is in work (T-03c1). The vibration patterns and the
  glasses, panel and earphone alerts below do not exist yet.

**Not stated by the workbook:** what the device does when several peripherals fail at once.
D-17 (below) answers it for the reconnect confirmations only; the alerts themselves are
independent, each with its own cadence.

**Decided by the user, 2026-10-18 (DISAGREEMENTS.md D-15, D-16, D-17).** These apply to every
failsafe alert in this section (REQ-CON-02 to 12):
- **D-15, failsafe alerts override the switches, once.** A failsafe alert is spoken even when
  the speech switch is off, at the volume knob's current level but never below 30 % (0.30 on the
  knob's own 0.0 to 1.0 scale, `tts_volume`). A failsafe vibration pattern plays even when the
  vibration switch is off, at the strength knob's current level but never below 30 % (0.30 on
  `motor_strength`). The override is for that one alert or pattern only: afterwards the switch
  or knob is exactly as the wearer left it. A repeating alert is handled the same way each time
  it repeats. Object announcements, mode announcements and the depth vibration keep obeying the
  switches and knobs (D-04, D-14 unchanged).
- **D-16, an alert in progress finishes.** An urgent object warning does not cut a failsafe
  alert that is being spoken; the alert finishes (2 to 3 s) first. Acceptance tests: the four
  strict-`xfail` urgent-tier tests from T-00a.
- **D-17, "everything is connected" must be true.** The sentence is said only when no other
  failsafe alert is still raised. While another is raised, the device says a sentence about the
  part that returned instead ("Vibration is working again", "Controller board connected",
  "Earphones connected"). The camera's sentence stays "Camera has been reconnected" (REQ-CON-09).
- **Earphone detection (Lead's choice at the user's request, 2026-10-18, T-04).** The earphones
  are present when the Pi has a USB sound card that can play sound: a `/proc/asound/cardN` that
  has a `usbid` file and a `pcm*p` (playback) entry. Not the USB device list: the camera is a
  USB sound card too (capture only; `card 3 [Light]`), and matching the earphones' name or
  vendor id would break when the earphones are replaced. Plain file reads, no command run.
  Reason and the real values on this Pi: T-04's brief.

**Decided feedback per event** (team, 2026-10-12). Speech goes through the existing speech
system; vibration patterns play on all three motors.

| Event | Spoken | Vibration |
|---|---|---|
| Glasses ESP32 can't be reached | "Vibration is not working" (the workbook's "vibration feedback cannot be reached" is also acceptable) | — (the vibration board is the part that's missing) |
| Glasses ESP32 reconnects | Confirmation that everything is connected | *beep beep* |
| Control panel can't be reached | That the controller board can't be reached and the last session's settings are in use | — |
| Control panel reconnects | Confirmation that everything is connected | — |
| Camera can't be reached | "Critical Error: Camera cannot be reached" | A *beep beep* pattern, distinct from the reconnect one |
| Camera reconnects | "camera has been reconnected" | — |
| Earphones missing | — (nothing to speak through) | "strong weak, strong weak, strong weak" |
| Earphones reconnect | Confirmation that everything is connected | *beep beep* |

Exact timings of the patterns are a design choice. They must be told apart by feel: the
camera alert and the reconnect pattern may not feel the same. Firmware changes are outside
the agentic workflow, so the patterns are produced from the Pi using commands the glasses
firmware already accepts.

**REQ-CON-01: Normal start with all peripherals connected**
- Statement: with the peripheral connected, power-on proceeds into the main pipeline.
- Sources: TC-022-001 (glasses ESP32), TC-023-001 (control panel), TC-024-001 (camera),
  TC-025-001 (earphones); each says "Device will proceed as intended to main pipeline".
- Type: functional · Scope: IN · Priority: P1

**REQ-CON-02: Spoken alert when the glasses ESP32 can't be reached**
- Statement: when the glasses ESP32 can't be reached, whether absent at start-up or disconnected
  while running, the device says that the vibration isn't working.
- Sources: TC-022-002 to TC-022-005: "Audio output should indicate that the vibration feedback
  cannot be reached." / "…the audio feedback will relay that the vibration feedback cannot be
  reached."
- Resolved (team, 2026-10-12): spoken through the speech system, as "Vibration is not working"
  or the workbook's wording.
- Type: error-handling · Scope: NOT-BUILT (T-01) · Priority: P1

**REQ-CON-03: How often the glasses alert repeats**
- Statement: the REQ-CON-02 alert repeats every 30 s while depth estimation is enabled on the
  controller board. It is said once while depth estimation is disabled, including when depth is
  switched off mid-run.
- Sources:
  - TC-022-002 and TC-022-004: "relayed every 30 seconds" (depth enabled).
  - TC-022-003 and TC-022-005: "only stated once" / "relayed once" (depth disabled).
  - TC-022-006: depth switched off mid-run.
- Resolved (team, 2026-10-12): TC-022-006's "every 30 seconds" is a workbook error; it is once (W-11).
- Not stated by the workbook: what happens if depth is switched back on while the board is still
  missing.
- Type: error-handling · Scope: NOT-BUILT (T-01) · Priority: P1

**REQ-CON-04: Confirmation when the glasses ESP32 reconnects**
- Statement: on reconnection, all three motors give a *beep beep* and the device confirms by
  speech that everything is connected.
- Source: TC-022-007: "Upon reconnection, there should be a vibration feedback and audio
  confirmation that everything is connected."
- Resolved (team, 2026-10-12): the vibration is a *beep beep* on all three motors.
- Resolved (user, 2026-10-18; D-17): "everything is connected" only when no other failsafe alert
  is raised; otherwise "Vibration is working again". The *beep beep* plays either way, under the
  D-15 rule (switch off or knob low).
- Type: error-handling · Scope: NOT-BUILT (T-01) · Priority: P1

**REQ-CON-05: Spoken alert when the control panel can't be reached**
- Statement: when the control panel can't be reached (absent at start-up or disconnected while
  running), the device says that the controller board cannot be reached and that the previously
  used settings will be used. It repeats every 5 minutes. The settings in force are the last
  ones the panel set: within the session, or saved from the previous session if the panel is
  absent at power-on.
- Sources: TC-023-002 and TC-023-004: "Audio output should indicate that the controller board
  cannot be reached, and that the device will use the previously used settings. This feedback
  should be relayed every 5 minutes."
- Resolved (team, 2026-10-12): "previously used" means the last session's settings. Today
  nothing is saved between sessions (the built-in defaults are always used at start-up), so
  this needs settings to be saved and restored (T-02).
- Type: error-handling · Scope: NOT-BUILT (T-02) · Priority: P1
- Note: the panel already sends an "I'm alive" line every 10 s, and the Pi already notices 30 s
  of silence. Today it only writes that to the log.

**REQ-CON-06: Confirmation when the control panel reconnects**
- Statement: on reconnection, the device confirms by speech that everything is connected.
- Source: TC-023-007: "Upon reconnection, there should be audio confirmation that everything is
  connected."
- Resolved (user, 2026-10-18; D-17): "everything is connected" only when no other failsafe alert
  is raised; otherwise "Controller board connected".
- Type: error-handling · Scope: NOT-BUILT (T-02) · Priority: P1

**REQ-CON-07: Camera-missing alert**
- Statement: when the camera can't be reached (absent at start-up or disconnected while running),
  all three motors give a *beep beep* pattern and the device says "Critical Error: Camera cannot
  be reached". It repeats every 10 seconds.
- Sources: TC-024-002 and TC-024-003: "There should be a quick set of vibration pulse alongside a
  "Critical Error: Camera cannot be reached" audio error. This should be repeated every 10 seconds".
- Resolved (team, 2026-10-12): the pulses are a distinct *beep beep*, told apart by feel from
  the reconnect pattern.
- Type: error-handling · Scope: NOT-BUILT (T-03) · Priority: P1

**REQ-CON-08: A missing camera never crashes the device** (critical)
- Statement: when the device runs as deployed (started at boot) and the camera is absent at
  start-up or removed while running:
  - the program does not crash;
  - every other part keeps running: the glasses and panel links, speech, and the alerts above;
  - the device keeps checking the USB ports for a camera;
  - when a camera appears, the vision pipeline starts on it.

  It must never fall back to the testing mock mode.
- Sources: TC-024-002 and TC-024-003: "the system should catch the no devices exception and allow
  the device to proceed in mock mode temporarily. Device should also listen for cameras that may
  pop up in the list of devices to check if everything is okay."
- Resolved (team, 2026-10-12; D-13): the workbook's "mock mode" is wrong. Mock mode generates fake
  detections and depth for development. It is reachable only by the `--mock` command-line flag
  and must never be entered automatically: a deployed device speaking fake detections would be a
  serious design error.
- Why it is critical (team): removing the camera currently produces a Hailo "No available
  devices" error that crashes the whole program instead of letting it look for the camera again.
- Known so far:
  - The library's own USB-camera lookup (`--input usb`) ends the program with `exit(1)` when it
    finds no camera (`hailo_apps/python/core/gstreamer/gstreamer_app.py`).
  - The boot launcher instead passes `/dev/video0` directly.
  - Exactly where "No available devices" comes from still has to be reproduced and traced.
  - The fix must live in this repo's own code, since installed libraries are never edited
    (AGENTS.md Rule 1), and must follow the hailo-apps documentation (AGENTS.md Rule 6).
- Type: error-handling · Scope: NOT-BUILT (T-03) · Priority: P1

**REQ-CON-09: Confirmation when the camera reconnects**
- Statement: when the camera comes back, the device says the camera has been reconnected and
  returns to normal operation.
- Source: TC-024-004: "There should be a quick "camera has been reconnected" audio output and the
  device should return to its normal state."
- Type: error-handling · Scope: NOT-BUILT (T-03) · Priority: P1

**REQ-CON-10: Vibration alert when the earphones are missing**
- Statement: when the earphones are absent at start-up or disconnected while running, all three
  motors give a brief "strong weak, strong weak, strong weak" pattern.
- Sources: TC-025-002 to TC-025-006: "There should be a brief "strong weak, strong weak, strong
  weak" vibration output for all three vibration motors to indicate an error."
- Type: error-handling · Scope: NOT-BUILT (T-04) · Priority: P1
- Note: how the Pi detects the earphones isn't specified in the workbook (W-19). Decided
  2026-10-18 (Lead, at the user's request): a USB sound card with a playback stream in
  `/proc/asound` (see the decisions block at the top of this section).
- Resolved (user, 2026-10-18; D-15): the pattern plays even when the vibration switch is off,
  at the strength knob's level but not below 30 %.

**REQ-CON-11: How often the earphone alert repeats**
- Statement: the REQ-CON-10 pattern repeats every 30 s while object detection is enabled. It
  plays once while detection is disabled, including when detection is switched off mid-run.
- Sources:
  - TC-025-002 and TC-025-004: every 30 s (detection enabled).
  - TC-025-003 and TC-025-005: once (detection disabled).
  - TC-025-006: detection switched off mid-run.
- Resolved (team, 2026-10-12):
  - TC-025-005 was meant to have detection *disabled* (W-12).
  - TC-025-006 follows the same rule as the glasses alert: once (W-13).
- Type: error-handling · Scope: NOT-BUILT (T-04) · Priority: P1

**REQ-CON-12: Confirmation when the earphones reconnect**
- Statement: on reconnection, all three motors give a *beep beep* and the device confirms by
  speech that everything is connected.
- Source: TC-025-007: "Upon reconnection, there should be a vibration feedback and audio
  confirmation that everything is connected." Its steps are copied from TC-022-007 (W-14).
- Resolved (team, 2026-10-12): the vibration is the same *beep beep* reconnect pattern.
- Resolved (user, 2026-10-18; D-17): "everything is connected" only when no other failsafe alert
  is raised; otherwise "Earphones connected". (Speech through the returned earphones, so the
  wearer hears it.)
- Type: error-handling · Scope: NOT-BUILT (T-04) · Priority: P1

**REQ-UPS-01: The X1202 button turns the Pi on**
- Source: TC-026-001: "Raspberry Pi turns on".
- Type: hardware · Scope: IN (manual) · Priority: P2 · Workbook result: Passed

**REQ-UPS-02: The battery percentage can be read**
- Source: TC-026-002: "Device is reading the current battery percentage", using the X1202's
  built-in script (vendor-supplied, not in this repo).
- Type: hardware · Scope: IN (manual) · Priority: P2 · Workbook result: Passed

**REQ-UPS-03: Holding the X1202 button turns the Pi off**
- Source: TC-026-003: "Raspberry Pi turns off".
- Type: hardware · Scope: IN (manual) · Priority: P2 · Workbook result: Passed

## 9. Power (PWR): TC-027 to TC-029

Module M09 · *"Verify the safety of the startup sequence, the power down sequence, and any other
battery related concerns"* · Preconditions: P-std plus components connected.

**REQ-PWR-01: The pipeline starts at power-on**
- Statement: turning the Pi on starts the pipeline automatically.
- Source: TC-027-001: "Raspberry Pi will turn on and automatically start the pipeline".
- Type: functional, hardware · Scope: IN · Priority: P2
- Note: `scripts/second-vision.service` is installed and enabled; it runs the base checkout.

**REQ-PWR-02: Battery level is announced**
- Statement: the device announces the battery level as follows.

  | Level | Workbook phrase |
  |---|---|
  | 80 % | "Battery at 80%" |
  | 60 % | "Battery at 60%" |
  | 40 % | "Battery at 40%" |
  | 20 % | "Battery at 20%. Recharge your Second Vision Device Soon." |
  | 5 % | "Battery at 5%. Device might shut down soon. Recharge as soon as possible." |

- Sources: TC-028-001 to TC-028-004, and the second TC-028-001 (5 %, W-02).
- Resolved (team, 2026-10-12):
  - **When:** at power-on (the current level), and whenever the level drops past one of the
    thresholds.
  - **How it's tested:** by hand. The battery is run down while a script records whether each
    announcement actually played. This is combined with the battery rundown in REQ-PERF-01.
- Type: functional · Scope: NOT-BUILT (T-06) · Priority: P2

**REQ-PWR-03: Shutdown from the power button**
- Statement: double-pressing the power button shuts the device down cleanly, and it says
  "Device shutting down." first. Holding the button forces power off, and nothing can be said.
- Source: TC-029-001: "Raspberry Pi shuts down. In the case of the momentary push button, it will
  relay a "Device shutting down." audio output first." Input: "Hold the power button / Double click
  the momentary push button".
- Resolved (team, 2026-10-12; agrees with the code):
  - The "momentary push button" is the power button, which the operating system treats as the
    power key. No separate button exists in the code.
  - With the desktop running, one press opens a dialog and a second press shuts down; the
    install script `scripts/install-service.sh` notes this.
  - A clean shutdown stops the Second Vision service first, so that stop is where the
    announcement can be spoken.
- Type: functional, hardware · Scope: NOT-BUILT, spoken part only (T-07) · Priority: P2

**REQ-PWR-04: Low-battery shutdown**
- Statement: at 2 % battery the device says "Low battery, device shutting down" and shuts down all
  pipelines gracefully.
- Source: TC-029-002: "There should be a "Low battery, device shutting down" audio output. Device
  should gracefully shutdown all pipelines."
- Type: functional, error-handling · Scope: NOT-BUILT (T-06) · Priority: P2

## 10. Control panel (PNL): TC-030 to TC-032

Module M10 (the Test Cases sheet mislabels this block; W-05) · Preconditions: P-std plus the control
panel connected. Code involved: `workers/config_reader.py` updates `core/config.py`, which drives
the pipeline mode, `tts_volume` and `motor_strength`.

**REQ-PNL-01: Detection switch off silences object announcements**
- Source: TC-030-001: "Audio feedback for identified objects will remain silent".
- Type: functional · Scope: IN · Priority: P2

**REQ-PNL-02: Detection switch on enables object announcements**
- Source: TC-030-002: "Audio feedback for identified objects be reflected in the earphones".
- Type: functional · Scope: IN · Priority: P2

**REQ-PNL-03: Depth switch off silences vibration**
- Source: TC-030-003: "Vibration feedback from glasses will remain silent".
- Type: functional · Scope: IN · Priority: P2

**REQ-PNL-04: Depth switch on enables vibration**
- Source: TC-030-004: "Vibration feedback will become present".
- Type: functional · Scope: IN · Priority: P2
- Not stated by the workbook: behaviour with both switches off.

**REQ-PNL-05: Volume knob at maximum gives maximum volume**
- Source: TC-031-001: "The volume of the device should be at its maximum value".
- Type: functional, hardware · Scope: IN · Priority: P2

**REQ-PNL-06: Volume knob at minimum is quiet, not silent**
- Statement: at the knob's minimum, speech is quiet but still audible.
- Source: TC-031-002: "The volume of the audio feedback should be silent". **Superseded by D-04
  (code wins).**
- Type: functional · Scope: IN · Priority: P2

**REQ-PNL-07: Volume knob at middle**
- Source: TC-031-003: "The volume should not be too loud, but not too small as well".
- Type: functional · Scope: IN · Priority: P2
- Resolved (team, 2026-10-15): the current code is good enough and stays as it is. The knob's
  midpoint is half-way between the quietest and loudest settings *as the ear hears it* (in
  decibels), not half the raw volume number (`test_half_knob_is_half_way_in_decibels`).
- Acceptance: the code's mapping holds, and a listener confirms the middle is audibly between
  the minimum and the maximum.

**REQ-PNL-08: Strength knob at maximum gives the strongest vibration**
- Source: TC-032-001: "The vibration of the device should be at its highest".
- Type: functional, hardware · Scope: IN · Priority: P2

**REQ-PNL-09: Strength knob at minimum switches the vibration off**
- Source: TC-032-002: "The vibration of the device should be minimal".
- Type: functional · Scope: IN · Priority: P2
- Resolved (team, 2026-10-15, D-14): the code on this branch stays as it is. The knob's value
  multiplies every motor command, so at minimum the motors are **off**, and low settings can
  fall below the strength at which the motors start turning. The floor-preserving fix on
  `feat/depth-callback` (commit `3797182`) is not brought in. The workbook's "minimal" is
  corrected (W-24).
- Acceptance: with the knob at minimum, the panel reports a motor strength at or near 0.00 and no motor
  vibrates while an obstacle is in view.

**REQ-PNL-10: Strength knob at middle halves the vibration**
- Source: TC-032-003: "The vibration of the device should not be too strong, but not too weak".
- Type: functional · Scope: IN · Priority: P2
- Resolved (team, 2026-10-15): judged against the code's design, as with the volume knob
  (REQ-PNL-07). With D-14 decided, that design is the one on this branch: the middle setting
  halves every motor command.
- Acceptance: with the knob at the middle, the panel reports a strength near 0.50, each motor
  command sent is half the unscaled one, and the wearer confirms the vibration is clearly
  weaker than at maximum but still felt.

## 11. Performance and endurance (PERF): no workbook case yet

Requirements the team asked for that aren't in the workbook yet (W-23). They follow the same
rules as the rest of this document.

**REQ-PERF-01: Battery rundown under full load**
- Statement: with the full system running on battery, run from 100 % down to 2 %, recording:
  - the average frame rate (FPS);
  - the temperature, and whether the Pi throttled;
  - the total battery life.

  The same run checks the battery announcements (REQ-PWR-02) and the low-battery shutdown
  (REQ-PWR-04), with a script recording whether each one played.
- Source: team request, 2026-10-12. It sits alongside the team's manual thermal-throttling
  performance test.
- Type: hardware, performance · Scope: IN (manual) · Priority: P2
- Acceptance: a measurement with no pass/fail limit given. The figures are recorded and
  reported. Announcements and shutdown are judged by REQ-PWR-02 and REQ-PWR-04.
- Note: `scripts/power_monitor.py` already samples the 5 V supply and the throttling flags.
