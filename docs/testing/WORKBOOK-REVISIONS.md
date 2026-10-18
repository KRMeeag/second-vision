# Handoff: revising the UAT test cases in the team's sheet

From the Second Vision repository's audit of the UAT workbook, 2026-10-17. It covers all
110 test cases plus the sheet-level fixes. Every change here was either decided by the team
(dated in the repo's `docs/testing/DISAGREEMENTS.md` and `REQUIREMENTS.md`) or corrects a
plain error in the sheet.

## Your job

1. **Apply the sheet-level fixes** in section A.
2. **Apply the per-case changes** in section B. The same changes, one row per case, are in
   `workbook-revisions.csv`. Import it as a new tab ("Revision audit") so the team can tick
   off each row.
3. **Leave everything else alone.** In particular:
   - Change only the cells a row names. Columns: A = ID, B = Steps, C = Input,
     D = Expected, E = Actual, F = Status, G = Comments.
   - Don't change a recorded result (Actual, Status) unless the row says so. Those are the
     team's records.
   - Don't renumber any case except the three listed (TC-023-004, TC-023-007 and the
     second TC-028-001). The repo's traceability is keyed on the IDs.
4. **Find rows by case ID**, not by row number. Row numbers are from the 2026-10-06 copy and
   shift once rows are deleted.
5. **Check before you overwrite.** Each changed field shows its current text. If the sheet's
   text is different, the team has edited it since; don't overwrite it. List it for the
   person instead.
6. **Make every change reviewable.** Highlight each changed cell and add a cell note with the
   row's reference IDs (for example "D-13, REQ-CON-08").
7. **Leave "(to be added)" as written.** It marks a test the repo's coding agents haven't
   written yet. Its file name is filled in later.
8. **Report back** a list of the changes you applied, the rows you skipped and why.

### Terms used
- **D-xx / W-xx**: decision or workbook-defect entries in the repo's
  `docs/testing/DISAGREEMENTS.md`. **REQ-xxx**: the requirement a case maps to
  (`docs/testing/REQUIREMENTS.md`). **D36 / D37 / D30**: entries in the team's
  `documentation/DECISIONS.md`.
- **Repo status now**: where the coding agents stand on the case. **Repo-side work**: what
  the coding agents (Lead, Developer, Tester) will do for it. This is context for the paper,
  not something to type into the test-case cells.
- **Mock mode**: a testing-only mode that generates fake detections. A deployed device must
  never enter it.

## Summary

- 63 cases need text changes; 3 are removed; 5 are marked out of scope or deferred; 1 needs only a re-run note; 38 stay as they are.
- 3 IDs change: TC-023-004 → TC-023-003, TC-023-007 → TC-023-004, the 5 % TC-028-001 → TC-028-005.
- 1 case is added (G-15, battery rundown).
- 12 recorded results predate the code or decision they depend on. Each gets a comment, a new status, or is cleared, as its row says.

## A. Sheet-level fixes

| # | Where | Problem | Change | Ref |
|---|---|---|---|---|
| G-01 | Summary sheet | Title is the template leftover "[Project Name] (Template)". | Replace with the project's title. | W-20 |
| G-02 | Summary sheet | Pass total =SUM(D18:D38) stops before TC-022 (shows 24 instead of 27). The Fail cell and the Total row are typed numbers. | Widen the range to cover every scenario row; make Fail and Total formulas. | W-06 |
| G-03 | Features sheet | Template leftover "TENTATIVE (inherited from sample xlsx file)". | Replace or remove. | W-20 |
| G-04 | Scenarios sheet, row 79 | Scenario written "TC-10", so the scenario count shows 31 instead of 32. | Write TC-010. | W-04 |
| G-05 | Scenarios sheet, row 43 (comment) | Reviewer note "we probably need a check to see if an audio device is connected" has no case. | Link it to TC-025 (earphones connection), which now covers it. | W-19 |
| G-06 | Test Cases sheet, row 155 | TC-015 has no scenario title. | "Pipeline Integration, Concurrency & Resource Management". | W-08 |
| G-07 | Test Cases sheet, row 329 | The TC-030 to TC-032 block is labelled "On-board Power Related Checks", its description is copied from the power block, and its case-count formula counts TC-027 to TC-029. | Label it "Controller Board Tests"; write its own description; fix the formula. | W-05 |
| G-08 | Block headers | "Date Tested" is 2026-08-03 on blocks never run, a week before the workbook existed. | Clear the date on blocks that haven't been run. | W-07 |
| G-09 | All module prerequisites | The environment is called "my-hailo-env". | "my_hailo_env". | W-17 |
| G-10 | M02 (Depth) header | Description promises "ground-hazard warnings", which were removed from scope (D36). How to read the values isn't stated. | Remove "and ground-hazard warnings". Add to the environment note: "Read the motor row of the [DEPTH] console line: 'warning' means that zone's motor value is non-zero; 'at or near 0' means it is zero. Distances are scene setup; the depth output is relative, not metres. Run at full vibration strength." | D-03, REQ section 2 |
| G-11 | M02 and M06 prerequisites | The Scenarios and Test Cases sheets disagree (M02's Test Cases block adds the camera; M06 lists 3 prerequisites on one sheet and 7 on the other). | Make both sheets list the same prerequisites. | W-18 |
| G-12 | M06 (Depth to haptics) environment | Lists Docker for SC-DepthV3. Depth runs on the Hailo inside the camera pipeline; Docker isn't used. | Remove Docker from the environment. | D-08 |
| G-13 | M06 scenario TC-006 title | "Ground hazard detection and output stability": only the stability case remains in scope. | Optional: "Output stability", or keep the title and mark 006-001/002 out of scope. | D-03 |
| G-14 | M04 (ESP32 firmware) description | Describes JSON messages parsed by ArduinoJson. | Describe the binary motor packet (0xAA, type, left, centre, right, XOR checksum) and its ACK. | D-02 |
| G-15 | New case (M09 Power or the performance tests) | The team's battery-rundown endurance test has no case. | Add: Steps "1. Charge to 100 %  2. Run the full system on battery until 2 %  3. Record FPS, temperature, throttling and battery life, and whether each battery announcement played". Expected: "Figures recorded and reported (no pass/fail limit); each battery announcement and the low-battery shutdown behave as in TC-028 and TC-029-002." Put it next to the manual thermal-throttling test; the ID is the team's choice. | W-23, REQ-PERF-01 |

## B. Per-case audit

Hardware names in the sheet (IMX291 camera, ESP32 boards, UART on the GPIO pins) are correct and need no change.

### M01 Object Detection

**TC-001-001** · row 16 · **Revise + re-run** · D-06, D-08, W-21, REQ-DET-01
- Finding: The model has 14 classes, not 13. Steps name ./scripts/setup_env.sh and ./tests/obj_det_unit/*.py; neither exists in any repo on the Pi. The Passed result was recorded before the 14-class model was added (2026-10-11). Bicycle and Tricycle are both spoken as "vehicle" today, so this fails until that is fixed.
- Steps (B) →  1. Open the second-vision repo  2. Activate the environment: source my_hailo_env/bin/activate  3. Run this case's UAT test in tests/uat/ (file name to be filled in when the test is added)
- Input (C) →  Load the 14-image batch (one image per model class: Person, Vehicle, Motorcycle, Pole, Animals, Doors, Chairs, Tables, Tricycle, Potholes, Trash Bins, Bicycle, Stairs, Bench; single centred object, clear conditions) and run each through the object detection callback.
- Expected (D) →  All 14 images return their own class label with confidence > 0.5.
- Actual/Status (E/F) →  Clear the recorded Passed (or add a comment: "Recorded before the 14-class model; re-run").
- Repo status now: TO-FIX: built; needs a fix first. Repo-side work: Task T-05: give every class its own spoken label (Bicycle and Tricycle stop being "vehicle"). Then: Write an automated test on the Pi that runs this case's images through the real detection callback, then verify it and save the log as evidence. No code change planned. Needs the team's test images (none are in the repo). Queued after the P1 (failsafe) tasks.

**TC-001-002** · row 17 · **Revise + re-run** · D-06, D-08, W-21, REQ-DET-02
- Finding: Steps name ./scripts/setup_env.sh and ./tests/obj_det_unit/*.py; neither exists in any repo on the Pi. Only one confusion pair is named. Passed was recorded before the 14-class model. Bicycle is spoken as "vehicle" until T-05.
- Steps (B) →  1. Open the second-vision repo  2. Activate the environment: source my_hailo_env/bin/activate  3. Run this case's UAT test in tests/uat/ (file name to be filled in when the test is added)
- Input (C) →  Load the confusion-pair test images (Motorcycle/Bicycle, Bicycle/Tricycle, Chairs/Bench) and run each through the callback.
- Actual/Status (E/F) →  Clear the recorded Passed, or comment "re-run needed".
- Repo status now: TO-FIX: built; needs a fix first. Repo-side work: Waits for T-05. Then: Write an automated test on the Pi that runs this case's images through the real detection callback, then verify it and save the log as evidence. No code change planned. Needs the team's test images (none are in the repo). Queued after the P1 (failsafe) tasks.

**TC-001-003** · row 18 · **Rewrite + re-run** · W-01, D-08, W-21, REQ-DET-03
- Finding: Expected result is leftover template text ("Password must be at least 8 characters; account is not created"), yet it is recorded as Passed. "Thinnest-sourced classes" is never named: the team confirmed it is Trash Bins (1702 training images). Steps name ./scripts/setup_env.sh and ./tests/obj_det_unit/*.py; neither exists in any repo on the Pi.
- Steps (B) →  1. Open the second-vision repo  2. Activate the environment: source my_hailo_env/bin/activate  3. Run this case's UAT test in tests/uat/ (file name to be filled in when the test is added)
- Input (C) →  Load several test images of Trash Bins, the class with the fewest training images (1702), and run each through the callback.
- Expected (D) →  Each image returns "Trash Bins" with confidence > 0.5, the same bar as TC-001-001.
- Actual/Status (E/F) →  Clear the recorded Passed: it was recorded against template text.
- Repo status now: READY: built; test not written yet. Repo-side work: Write an automated test on the Pi that runs this case's images through the real detection callback, then verify it and save the log as evidence. No code change planned. Needs the team's test images (none are in the repo). Queued after the P1 (failsafe) tasks.

**TC-002-001** · row 23 · **Fix steps + re-run** · D-08, W-21, REQ-DET-04
- Finding: Steps name ./scripts/setup_env.sh and ./tests/obj_det_unit/*.py; neither exists in any repo on the Pi. Passed was recorded before the 14-class model.
- Steps (B) →  1. Open the second-vision repo  2. Activate the environment: source my_hailo_env/bin/activate  3. Run this case's UAT test in tests/uat/ (file name to be filled in when the test is added)
- Actual/Status (E/F) →  Comment: "Recorded before the 14-class model; re-run".
- Repo status now: READY: built; test not written yet. Repo-side work: Write an automated test on the Pi that runs this case's images through the real detection callback, then verify it and save the log as evidence. No code change planned. Needs the team's test images (none are in the repo). Queued after the P1 (failsafe) tasks.

**TC-002-002** · row 24 · **Fix steps + typo** · D-08, REQ-DET-05
- Finding: Steps name ./scripts/setup_env.sh and ./tests/obj_det_unit/*.py; neither exists in any repo on the Pi. Typo "te camera".
- Steps (B) →  1. Open the second-vision repo  2. Activate the environment: source my_hailo_env/bin/activate  3. Run this case's UAT test in tests/uat/ (file name to be filled in when the test is added)
- Expected (D) →  System prioritizes the instance closest to the camera.
- Repo status now: READY: built; test not written yet. Repo-side work: Write an automated test on the Pi that runs this case's images through the real detection callback, then verify it and save the log as evidence. No code change planned. Needs the team's test images (none are in the repo). Queued after the P1 (failsafe) tasks.

**TC-002-003** · row 25 · **Fix steps + re-run** · D-08, W-21, REQ-DET-06
- Finding: Steps name ./scripts/setup_env.sh and ./tests/obj_det_unit/*.py; neither exists in any repo on the Pi. Passed was recorded before the 14-class model.
- Steps (B) →  1. Open the second-vision repo  2. Activate the environment: source my_hailo_env/bin/activate  3. Run this case's UAT test in tests/uat/ (file name to be filled in when the test is added)
- Actual/Status (E/F) →  Comment: "Recorded before the 14-class model; re-run".
- Repo status now: READY: built; test not written yet. Repo-side work: Write an automated test on the Pi that runs this case's images through the real detection callback, then verify it and save the log as evidence. No code change planned. Needs the team's test images (none are in the repo). Queued after the P1 (failsafe) tasks.

**TC-002-004** · row 26 · **Rewrite** · D-05, D-08, REQ-DET-07
- Finding: The expected result contradicts the design: inside a boundary band an object keeps the zone it had before, it is not dropped (team decision D37). A single still image cannot show this, because the rule depends on the previous zone. Typo "hysterisis". Steps name ./scripts/setup_env.sh and ./tests/obj_det_unit/*.py; neither exists in any repo on the Pi.
- Steps (B) →  1. Open the second-vision repo  2. Activate the environment: source my_hailo_env/bin/activate  3. Run this case's UAT test in tests/uat/ (file name to be filled in when the test is added)
- Input (C) →  Load two short frame sequences: an object moving from the centre zone into the centre/right hysteresis band, and an object moving from the right zone into the same band.
- Expected (D) →  An object inside the band keeps the zone it had before entering it: the first stays "center", the second stays "right". It is not dropped.
- Repo status now: READY: built; test not written yet. Repo-side work: Write an automated test on the Pi that runs this case's images through the real detection callback, then verify it and save the log as evidence. No code change planned. Needs the team's test images (none are in the repo). Queued after the P1 (failsafe) tasks.

**TC-002-005** · row 27 · **Fix steps + re-run** · D-08, W-21, REQ-DET-08
- Finding: Steps name ./scripts/setup_env.sh and ./tests/obj_det_unit/*.py; neither exists in any repo on the Pi. The Failed result was recorded before the 14-class model, so it no longer says anything about the current model.
- Steps (B) →  1. Open the second-vision repo  2. Activate the environment: source my_hailo_env/bin/activate  3. Run this case's UAT test in tests/uat/ (file name to be filled in when the test is added)
- Actual/Status (E/F) →  Comment: "Failed before the 14-class model; re-run".
- Repo status now: READY: built; test not written yet. Repo-side work: Write an automated test on the Pi that runs this case's images through the real detection callback, then verify it and save the log as evidence. No code change planned. Needs the team's test images (none are in the repo). Queued after the P1 (failsafe) tasks.

**TC-003-001** · row 32 · **Fix steps + re-run** · D-08, W-21, REQ-DET-09
- Finding: Steps name ./scripts/setup_env.sh and ./tests/obj_det_unit/*.py; neither exists in any repo on the Pi. Passed was recorded before the 14-class model.
- Steps (B) →  1. Open the second-vision repo  2. Activate the environment: source my_hailo_env/bin/activate  3. Run this case's UAT test in tests/uat/ (file name to be filled in when the test is added)
- Actual/Status (E/F) →  Comment: "Recorded before the 14-class model; re-run".
- Repo status now: READY: built; test not written yet. Repo-side work: Write an automated test on the Pi that runs this case's images through the real detection callback, then verify it and save the log as evidence. No code change planned. Needs the team's test images (none are in the repo). Queued after the P1 (failsafe) tasks.

**TC-003-002** · row 33 · **Fix steps + typo + re-run** · D-08, W-21, REQ-DET-10
- Finding: Steps name ./scripts/setup_env.sh and ./tests/obj_det_unit/*.py; neither exists in any repo on the Pi. Typo "iamge". Passed was recorded before the 14-class model.
- Steps (B) →  1. Open the second-vision repo  2. Activate the environment: source my_hailo_env/bin/activate  3. Run this case's UAT test in tests/uat/ (file name to be filled in when the test is added)
- Expected (D) →  System still manages to correctly identify the class contained within the motion-blur test image.
- Actual/Status (E/F) →  Comment: "Recorded before the 14-class model; re-run".
- Repo status now: READY: built; test not written yet. Repo-side work: Write an automated test on the Pi that runs this case's images through the real detection callback, then verify it and save the log as evidence. No code change planned. Needs the team's test images (none are in the repo). Queued after the P1 (failsafe) tasks.

**TC-003-003** · row 34 · **Clarify** · D-08, REQ-DET-11
- Finding: Steps name ./scripts/setup_env.sh and ./tests/obj_det_unit/*.py; neither exists in any repo on the Pi. It doesn't say where the 70 % is counted. The speech side announces only the most important detection at a time by design, so it can only be counted on the model's detections.
- Steps (B) →  1. Open the second-vision repo  2. Activate the environment: source my_hailo_env/bin/activate  3. Run this case's UAT test in tests/uat/ (file name to be filled in when the test is added)
- Expected (D) →  The model detects at least 70 % of the instances of that class visible in the image (one box of the right class per object, counted before the system chooses what to announce).
- Repo status now: READY: built; test not written yet. Repo-side work: Write an automated test on the Pi that runs this case's images through the real detection callback, then verify it and save the log as evidence. No code change planned. Needs the team's test images (none are in the repo). Queued after the P1 (failsafe) tasks.

**TC-003-004** · row 35 · **Fix steps + re-run** · D-08, W-21, REQ-DET-12
- Finding: Steps name ./scripts/setup_env.sh and ./tests/obj_det_unit/*.py; neither exists in any repo on the Pi. Passed was recorded before the 14-class model.
- Steps (B) →  1. Open the second-vision repo  2. Activate the environment: source my_hailo_env/bin/activate  3. Run this case's UAT test in tests/uat/ (file name to be filled in when the test is added)
- Actual/Status (E/F) →  Comment: "Recorded before the 14-class model; re-run".
- Repo status now: READY: built; test not written yet. Repo-side work: Write an automated test on the Pi that runs this case's images through the real detection callback, then verify it and save the log as evidence. No code change planned. Needs the team's test images (none are in the repo). Queued after the P1 (failsafe) tasks.

### M02 Depth Estimation

**TC-005-003** · row 59 · **Re-run** · W-22, REQ-DEP-06
- Finding: Recorded Failed before floor suppression was added to the depth processing. The text is fine.
- Actual/Status (E/F) →  Comment: "Failed before floor suppression was added; re-run".
- Repo status now: READY: built; test not written yet. Repo-side work: Assisted test: a person sets up the scene, the test reads the depth log and judges it automatically; existing unit tests back it up. No code change (the depth algorithm is not re-tuned). Queued after the P1 tasks.

**TC-006-001** · row 64 · **Mark out of scope** · D-03, REQ-DEP-07
- Finding: Drop-off (stair) detection by depth estimation was removed from scope (team decision D36). The recorded Passed predates that decision. Object detection can still name stairs it sees; that is a different feature.
- Actual/Status (E/F) →  Out of scope (D36: depth-based drop-off detection removed).
- Comments (G) →  Out of scope since D36. The recorded Passed predates the decision.
- Repo status now: OUT-SCOPE: out of scope. Repo-side work: None: out of scope.

**TC-006-002** · row 65 · **Mark out of scope** · D-03, REQ-DEP-08
- Finding: Step-up (curb) detection by depth estimation was removed from scope (D36). The recorded Passed predates it.
- Actual/Status (E/F) →  Out of scope (D36).
- Comments (G) →  Out of scope since D36. The recorded Passed predates the decision.
- Repo status now: OUT-SCOPE: out of scope. Repo-side work: None: out of scope.

**TC-006-003** · row 66 · **Clarify** · REQ-DEP-09
- Finding: "Constant" can't be checked literally: camera noise means values are never identical frame to frame. Needs a measurable bar.
- Expected (D) →  Over 60 s of a still scene, no zone switches between silent and active, and each zone's value stays within ±10 % of full scale (±26 of 255) of its own median: no blinking or jumping.
- Repo status now: READY: built; test not written yet. Repo-side work: Assisted test: a person sets up the scene, the test reads the depth log and judges it automatically; existing unit tests back it up. No code change (the depth algorithm is not re-tuned). Queued after the P1 tasks.

No change:

- **TC-004-001** (Keep): Sound. See the module note on how depth values are read (global change G-10). Repo status: READY: built; test not written yet. Repo-side work: Assisted test: a person sets up the scene, the test reads the depth log and judges it automatically; existing unit tests back it up. No code change (the depth algorithm is not re-tuned). Queued after the P1 tasks.
- **TC-004-002** (Keep): Sound. Repo status: READY: built; test not written yet. Repo-side work: Assisted test: a person sets up the scene, the test reads the depth log and judges it automatically; existing unit tests back it up. No code change (the depth algorithm is not re-tuned). Queued after the P1 tasks.
- **TC-004-003** (Keep): Sound. Repo status: READY: built; test not written yet. Repo-side work: Assisted test: a person sets up the scene, the test reads the depth log and judges it automatically; existing unit tests back it up. No code change (the depth algorithm is not re-tuned). Queued after the P1 tasks.
- **TC-005-001** (Keep): Sound. Repo status: READY: built; test not written yet. Repo-side work: Assisted test: a person sets up the scene, the test reads the depth log and judges it automatically; existing unit tests back it up. No code change (the depth algorithm is not re-tuned). Queued after the P1 tasks.
- **TC-005-002** (Keep): Sound. Repo status: READY: built; test not written yet. Repo-side work: Assisted test: a person sets up the scene, the test reads the depth log and judges it automatically; existing unit tests back it up. No code change (the depth algorithm is not re-tuned). Queued after the P1 tasks.

### M03 Bone Conduction Audio Feedback

**TC-007-002** · row 82 · **Revise** · D-08, W-17, REQ-AUD-02
- Finding: The venv is named my-hailo-env; it is my_hailo_env. scripts/init_audio.py does not exist. Speech is produced by espeak-ng through the device's speech worker.
- Steps (B) →  1. Activate the environment (source my_hailo_env/bin/activate).  2. Run the audio start-up test in tests/uat/ (to be added), which starts the device's speech output.
- Input (C) →  UAT audio start-up test (replaces python3 scripts/init_audio.py)
- Repo status now: READY: built; test not written yet. Repo-side work: Automated test on the Pi, verified by the Tester, log saved as evidence. No code change planned. Queued after the P1 tasks.

**TC-008-001** · row 88 · **Revise** · D-08, REQ-AUD-04
- Finding: scripts/tts_test.py does not exist. "Within <500ms" doesn't say from when to when, or over how many tries.
- Steps (B) →  1. Hand the phrase to the device's speech system with the UAT speech test (tests/uat/, to be added).  2. Measure when speech is first heard.
- Input (C) →  Phrase: "Object detected ahead", 20 trials
- Expected (D) →  Speech is clear, and is first heard within 500 ms of the phrase being handed to the speech system in at least 19 of 20 trials (95th percentile ≤ 500 ms).
- Repo status now: READY: built; test not written yet. Repo-side work: Automated test on the Pi, verified by the Tester, log saved as evidence. No code change planned. Queued after the P1 tasks. A person confirms the speech is clear.

**TC-008-002** · row 89 · **Revise expected** · D-10, D-08, REQ-AUD-05
- Finding: By design the speech system holds one pending message and drops less important ones (team decision D30), so not all 20 messages are spoken. scripts/tts_test.py does not exist.
- Input (C) →  20 rapid messages handed to the speech system under full detection and depth load (UAT stress test, to be added)
- Expected (D) →  Speech that does play has no buffer underruns, popping or distortion. Lower-priority messages may be dropped by design.
- Repo status now: READY: built; test not written yet. Repo-side work: A person listens and judges; the agents prepare the command and record the answer. No code change. Queued after the P1 tasks.

**TC-008-004** · row 91 · **Rewrite** · D-07, W-09, REQ-AUD-07
- Finding: The device's detection messages have no distance field. They carry label, zone, confidence, priority and tier, and are spoken as "<label> <zone>" (e.g. "person left") or as their phrase field when given. The row also has no Actual or Status cells.
- Steps (B) →  1. Create a detection message in the device's format, e.g. {"label": "person", "zone": "left", "confidence": 0.9, "priority": 1.0, "tier": "normal"}.  2. Put it in the speech mailbox.  3. Listen to and record the spoken phrase.
- Input (C) →  {"label": "person", "zone": "left", "confidence": 0.9, "priority": 1.0, "tier": "normal"}
- Expected (D) →  The speech worker says "person left" clearly through the transducers within 500 ms (as in TC-008-001).
- Actual/Status (E/F) →  Add the missing Actual and Status cells.
- Repo status now: READY: built; test not written yet. Repo-side work: Automated test on the Pi, verified by the Tester, log saved as evidence. No code change planned. Queued after the P1 tasks.

**TC-009-001** · row 96 · **Rewrite** · D-11, D-08, REQ-AUD-08
- Finding: The device has no sound-file alerts; everything is spoken. What it does have is interruption: an urgent detection cuts off the current sentence. The team decided this case is about that interruption. scripts/play_alert.py and obstacle_beep.wav don't exist.
- Steps (B) →  1. Start the device speaking a normal detection.  2. While it speaks, trigger an urgent detection (e.g. a vehicle straight ahead).
- Input (C) →  Urgent detection arriving mid-sentence
- Expected (D) →  The current sentence stops and the urgent item is spoken at once, clearly, over the bone conduction transducers.
- Repo status now: READY: built; test not written yet. Repo-side work: Interruption is built and unit-tested already. A person listens and judges; the agents prepare the command and record the answer. No code change. Queued after the P1 tasks.

**TC-009-002** · row 97 · **Keep (note)** · D-08, REQ-AUD-09
- Finding: Sound. The sweep file sweep_200_4000Hz.wav doesn't exist yet; it can be generated.
- Comments (G) →  Sweep file to be generated (e.g. with sox) before testing.
- Repo status now: READY: built; test not written yet. Repo-side work: A person listens and judges; the agents prepare the command and record the answer. No code change. Queued after the P1 tasks.

**TC-009-003** · row 98 · **Revise** · D-08, REQ-AUD-10
- Finding: The login is the placeholder pi@192.168.x.x (the Pi's user is sv-rpi5), scripts/tts_test.py doesn't exist, and "without network-induced delay" has no measurable bar.
- Input (C) →  ssh sv-rpi5@<Pi's address> running the UAT speech test (to be added), 10 runs; the same test run locally, 10 runs
- Expected (D) →  Audio comes out of the Pi itself, and the median time from the command starting to speech starting over SSH is within 50 ms of the local median.
- Repo status now: READY: built; test not written yet. Repo-side work: Automated test on the Pi, verified by the Tester, log saved as evidence. No code change planned. Queued after the P1 tasks.

No change:

- **TC-007-001** (Keep): Sound. The earphones in use are USB, so "3.5mm/USB audio adapter" could say USB only (optional). Repo status: READY: built; test not written yet. Repo-side work: Automated test on the Pi, verified by the Tester, log saved as evidence. No code change planned. Queued after the P1 tasks.
- **TC-007-003** (Keep): Sound. Repo status: READY: built; test not written yet. Repo-side work: A person listens and judges; the agents prepare the command and record the answer. No code change. Queued after the P1 tasks.
- **TC-008-003** (Keep): Sound. The input is spoken through the device's speech system (the script name in other cases doesn't exist). Repo status: READY: built; test not written yet. Repo-side work: A person listens and judges; the agents prepare the command and record the answer. No code change. Queued after the P1 tasks.

### M04 ESP32 Haptic Feedback

**TC-010-001** · row 113 · **Rewrite (wire format)** · D-02, REQ-HAP-01
- Finding: The Pi does not send JSON. Both sides use a 6-byte binary motor packet: 0xAA, type 0x01, left, centre, right, XOR checksum, answered by an ACK. The firmware never parses JSON.
- Steps (B) →  1. Open the serial monitor  2. Send a motor packet  3. Observe the PWM output on the pins
- Input (C) →  Motor packet: left 100, centre 50, right 0 (bytes AA 01 64 32 00 + checksum)
- Expected (D) →  ESP32 accepts the packet; left motor pulses strong, centre medium, right silent.
- Comments (G) →  Verifies baseline packet-to-PWM mapping.
- Repo status now: OUT-ESP32: ESP32 (outside the agents). Repo-side work: None. Runs on the glasses ESP32, outside the agents' scope; the firmware owner tests it by hand.

**TC-010-002** · row 114 · **Revise input** · D-02, REQ-HAP-02
- Finding: Input is JSON; the link uses binary motor packets.
- Steps (B) →  1. Open the serial monitor  2. Send a valid motor packet  3. Observe the ESP32's TX line right after
- Input (C) →  Motor packet: left 50, centre 50, right 50
- Repo status now: OUT-ESP32: ESP32 (outside the agents). Repo-side work: None. Runs on the glasses ESP32, outside the agents' scope; the firmware owner tests it by hand. The Pi side's ACK handling is already unit-tested.

**TC-010-003** · row 115 · **Revise input** · D-01, D-02, REQ-HAP-03
- Finding: Input is JSON, and "near-cap" 150 assumes the 153 PWM cap, which no longer applies: 255 was confirmed safe.
- Steps (B) →  1. Send an all-zero motor packet, confirm silence  2. Send all three motors at full scale  3. Monitor for voltage sag or reset during step 2
- Input (C) →  Motor packet 0, 0, 0; then 255, 255, 255
- Repo status now: OUT-ESP32: ESP32 (outside the agents). Repo-side work: None. Runs on the glasses ESP32, outside the agents' scope; the firmware owner tests it by hand.

**TC-011-001** · row 120 · **Remove** · D-01, REQ-HAP-04
- Finding: Premise is out of date: the firmware has no 153 cap (MAX_PWM = 255) and the team confirmed 255 is safe. TC-010-003 already covers full scale.
- Actual/Status (E/F) →  Remove the row (or mark Superseded: 255 confirmed safe).
- Repo status now: OUT-ESP32: ESP32 (outside the agents). Repo-side work: None. Runs on the glasses ESP32, outside the agents' scope; the firmware owner tests it by hand.

**TC-011-002** · row 121 · **Remove** · D-01, REQ-HAP-04
- Finding: Tests the 153 cap boundary, which no longer exists.
- Actual/Status (E/F) →  Remove the row (or mark Superseded).
- Repo status now: OUT-ESP32: ESP32 (outside the agents). Repo-side work: None. Runs on the glasses ESP32, outside the agents' scope; the firmware owner tests it by hand.

**TC-011-003** · row 122 · **Rewrite (move to the Pi side)** · D-02, REQ-HAP-05
- Finding: Each motor value is one byte (0 to 255) in the binary packet, so -50 or 9999 cannot reach the ESP32. The wraparound risk is on the Pi, before packing.
- Steps (B) →  1. Feed out-of-range motor values to the Pi's motor-intensity conversion  2. Inspect the packed bytes
- Input (C) →  Motor values -50 and 9999
- Expected (D) →  Both clamp to 0 and 255 respectively: no wraparound producing an unintended high PWM value.
- Repo status now: OUT-ESP32: ESP32 (outside the agents). Repo-side work: If moved to the Pi side: an existing unit test already covers this; the agents verify it and save evidence. Otherwise: None. Runs on the glasses ESP32, outside the agents' scope; the firmware owner tests it by hand.

**TC-012-001** · row 127 · **Remove** · D-02, REQ-HAP-06
- Finding: Tests missing JSON keys; a fixed-length binary packet has no equivalent. The comment "Simulates real walking speeds" is also a copy error.
- Actual/Status (E/F) →  Remove the row.
- Repo status now: OUT-ESP32: ESP32 (outside the agents). Repo-side work: None. Runs on the glasses ESP32, outside the agents' scope; the firmware owner tests it by hand.

**TC-012-003** · row 129 · **Revise expected** · D-02, REQ-HAP-08
- Finding: Expected result names deserializeJson(); the firmware checks the packet's header and checksum instead.
- Input (C) →  Garbled or truncated bytes mid-stream while the line stays connected
- Expected (D) →  The ESP32 rejects the bad frame (wrong header or checksum) and holds the last safe state or zeroes out: no crash, no unintended PWM spike. The next valid packet is applied.
- Repo status now: OUT-ESP32: ESP32 (outside the agents). Repo-side work: None. Runs on the glasses ESP32, outside the agents' scope; the firmware owner tests it by hand.

No change:

- **TC-012-002** (Keep): Sound. Repo status: OUT-ESP32: ESP32 (outside the agents). Repo-side work: None. Runs on the glasses ESP32, outside the agents' scope; the firmware owner tests it by hand.

### M05 Object Detection with Audio Feedback

**TC-013-001** · row 144 · **Revise** · D-06, D-07, REQ-DTA-01
- Finding: A cup is not one of the model's classes. Messages are dictionaries in the device's format, not strings.
- Steps (B) →  Place a single recognisable object of a model class (e.g. a chair) in front of the IMX291 camera.
- Input (C) →  Single object in clear view (Chair)
- Expected (D) →  The detection callback triggers and offers a detection message (label, zone, confidence, priority, tier) to the speech mailbox.
- Repo status now: READY: built; test not written yet. Repo-side work: Automated test on the Pi, verified by the Tester, log saved as evidence. No code change planned. Queued after the P1 tasks.

**TC-013-002** · row 145 · **Revise** · D-06, D-07, REQ-DTA-02
- Finding: A laptop is not a model class. The design is prioritized, not batched: the mailbox keeps the most important detection.
- Input (C) →  Multiple objects (Person, Chair, Table)
- Expected (D) →  The callback processes every bounding box and the speech mailbox keeps the most important detection.
- Repo status now: READY: built; test not written yet. Repo-side work: Automated test on the Pi, verified by the Tester, log saved as evidence. No code change planned. Queued after the P1 tasks.

**TC-014-001** · row 151 · **Revise input** · D-07, REQ-DTA-04
- Finding: Messages are dictionaries in the device's format, not plain strings.
- Input (C) →  Detection message: {"label": "person", "zone": "center", "confidence": 0.9, "priority": 1.0, "tier": "normal"}
- Repo status now: READY: built; test not written yet. Repo-side work: Automated test on the Pi, verified by the Tester, log saved as evidence. No code change planned. Queued after the P1 tasks.

**TC-014-002** · row 152 · **Revise input** · D-07, REQ-DTA-05
- Finding: Messages are dictionaries in the device's format, not plain strings.
- Input (C) →  5 detection messages in the device's format, 2 s apart
- Repo status now: READY: built; test not written yet. Repo-side work: Automated test on the Pi, verified by the Tester, log saved as evidence. No code change planned. Queued after the P1 tasks.

**TC-014-003** · row 153 · **Revise input + comment** · D-07, D-10, REQ-DTA-06
- Finding: Messages are dictionaries, not strings. The comment describes an old problem ("Consumer thread blocks… needs a debounce"); the speech mailbox now holds one message and drops less important ones by design.
- Input (C) →  50 detection messages in the device's format, 20 ms apart
- Comments (G) →  The speech mailbox holds one message and keeps the more important; lower-priority messages are dropped by design.
- Repo status now: READY: built; test not written yet. Repo-side work: Automated test on the Pi, verified by the Tester, log saved as evidence. No code change planned. Queued after the P1 tasks.

**TC-015-003** · row 160 · **Clarify** · REQ-DTA-09
- Finding: "Remains stable without memory leaks" has no measurable bar.
- Expected (D) →  Sampled every 10 s, ignoring the first minute: memory at the end is no more than 5 % above memory at minute 1, with no steady climb; average CPU over the last 3 minutes is within 10 percentage points of the first 3.
- Repo status now: READY: built; test not written yet. Repo-side work: Automated test on the Pi, verified by the Tester, log saved as evidence. No code change planned. Queued after the P1 tasks.

No change:

- **TC-013-003** (Keep): Sound ("queue" is the speech mailbox). Repo status: READY: built; test not written yet. Repo-side work: Automated test on the Pi, verified by the Tester, log saved as evidence. No code change planned. Queued after the P1 tasks.
- **TC-015-001** (Keep): Sound. Repo status: READY: built; test not written yet. Repo-side work: Automated test on the Pi, verified by the Tester, log saved as evidence. No code change planned. Queued after the P1 tasks. Long runs on the Pi.
- **TC-015-002** (Keep): Sound. Repo status: READY: built; test not written yet. Repo-side work: Already re-checked as a regression inside the camera-loss work (T-03a). Then: Automated test on the Pi, verified by the Tester, log saved as evidence. No code change planned. Queued after the P1 tasks.

### M06 Depth Estimation with Haptic Feedback

**TC-016-002** · row 176 · **Clarify** · REQ-DHP-02
- Finding: "Seamlessly" has no measurable bar.
- Expected (D) →  During a steady sweep, the vibration moves left, then centre, then right, crossing each boundary once (no back-and-forth), with no silent gap longer than 250 ms while the object is in view.
- Repo status now: READY: built; test not written yet. Repo-side work: Assisted test: a person sets up the scene, the test reads the depth log and judges it automatically; existing unit tests back it up. No code change (the depth algorithm is not re-tuned). Queued after the P1 tasks.

**TC-017-002** · row 183 · **Mark out of scope** · D-03, REQ-DHP-05
- Finding: Tests a real drop-off hazard, which depth estimation no longer detects (D36). Its comment points at TC-005-003's old failure, since fixed.
- Actual/Status (E/F) →  Out of scope (D36).
- Comments (G) →  Out of scope since D36 (depth-based drop-off detection removed).
- Repo status now: OUT-SCOPE: out of scope. Repo-side work: None: out of scope.

**TC-018-001** · row 189 · **Mark deferred** · D-08, REQ-DHP-06
- Finding: DE_Pipeline_Status doesn't exist, and depth isn't a separate thread that can be killed: it is a branch of the one camera pipeline. Building this was deferred.
- Actual/Status (E/F) →  Deferred.
- Comments (G) →  Deferred: no separate depth status or thread exists yet (task T-08).
- Repo status now: DEFERRED: deferred. Repo-side work: None for now (task T-08 is deferred).

**TC-018-002** · row 190 · **Mark deferred** · D-08, REQ-DHP-07
- Finding: Same as TC-018-001.
- Actual/Status (E/F) →  Deferred.
- Comments (G) →  Deferred with TC-018-001 (task T-08).
- Repo status now: DEFERRED: deferred. Repo-side work: None for now (task T-08 is deferred).

No change:

- **TC-016-001** (Keep): Sound. "3 m" and "1 m" are scene setup; depth output is relative (G-10). Repo status: READY: built; test not written yet. Repo-side work: Assisted test: a person sets up the scene, the test reads the depth log and judges it automatically; existing unit tests back it up. No code change (the depth algorithm is not re-tuned). Queued after the P1 tasks.
- **TC-016-003** (Keep): Sound. Repo status: READY: built; test not written yet. Repo-side work: Assisted test: a person sets up the scene, the test reads the depth log and judges it automatically; existing unit tests back it up. No code change (the depth algorithm is not re-tuned). Queued after the P1 tasks.
- **TC-017-001** (Keep): Sound. Repo status: READY: built; test not written yet. Repo-side work: Assisted test: a person sets up the scene, the test reads the depth log and judges it automatically; existing unit tests back it up. No code change (the depth algorithm is not re-tuned). Queued after the P1 tasks.
- **TC-017-003** (Keep): Sound. Repo status: READY: built; test not written yet. Repo-side work: Assisted test: a person sets up the scene, the test reads the depth log and judges it automatically; existing unit tests back it up. No code change (the depth algorithm is not re-tuned). Queued after the P1 tasks.
- **TC-018-003** (Keep): Sound. Built on the Pi (link-down detection, write timeouts). Repo status: READY: built; test not written yet. Repo-side work: P1. Assisted test: a person resets the glasses ESP32; the test checks the Pi never hangs and logs the link going down and up. Existing unit tests back it up.

### M07 BMI160 IMU (Ego-Motion / Head-Tracking)

No change:

- **TC-019-001** (Keep): No problem found from the Pi side. Repo status: OUT-ESP32: ESP32 (outside the agents). Repo-side work: None. Runs on the glasses ESP32, outside the agents' scope; the firmware owner tests it by hand.
- **TC-019-002** (Keep): No problem found from the Pi side. Repo status: OUT-ESP32: ESP32 (outside the agents). Repo-side work: None. Runs on the glasses ESP32, outside the agents' scope; the firmware owner tests it by hand.
- **TC-019-003** (Keep): No problem found from the Pi side. Repo status: OUT-ESP32: ESP32 (outside the agents). Repo-side work: None. Runs on the glasses ESP32, outside the agents' scope; the firmware owner tests it by hand.
- **TC-020-001** (Keep): No problem found from the Pi side. Repo status: OUT-ESP32: ESP32 (outside the agents). Repo-side work: None. Runs on the glasses ESP32, outside the agents' scope; the firmware owner tests it by hand.
- **TC-020-002** (Keep): No problem found from the Pi side. Repo status: OUT-ESP32: ESP32 (outside the agents). Repo-side work: None. Runs on the glasses ESP32, outside the agents' scope; the firmware owner tests it by hand.
- **TC-020-003** (Keep (open failure)): Recorded Failed; the case text is fine. The fix and re-test belong to the firmware owner. Repo status: OUT-ESP32: ESP32 (outside the agents). Repo-side work: None. Runs on the glasses ESP32, outside the agents' scope; the firmware owner tests it by hand.
- **TC-021-001** (Keep): No problem found from the Pi side. Repo status: OUT-ESP32: ESP32 (outside the agents). Repo-side work: None. Runs on the glasses ESP32, outside the agents' scope; the firmware owner tests it by hand.
- **TC-021-002** (Keep): No problem found from the Pi side. Repo status: OUT-ESP32: ESP32 (outside the agents). Repo-side work: None. Runs on the glasses ESP32, outside the agents' scope; the firmware owner tests it by hand.
- **TC-021-003** (Keep): No problem found from the Pi side. Repo status: OUT-ESP32: ESP32 (outside the agents). Repo-side work: None. Runs on the glasses ESP32, outside the agents' scope; the firmware owner tests it by hand.

### M08 External Components Connectivity Check

**TC-022-002** · row 243 · **Clarify** · REQ-CON-02, REQ-CON-03
- Finding: The team agreed the spoken wording: "Vibration is not working". Spelling: "Seed Xiao" is "Seeed Studio XIAO" (optional).
- Expected (D) →  The device says "Vibration is not working" (or that the vibration feedback cannot be reached), repeated every 30 seconds.
- Repo status now: NOT-BUILT: not built yet. Repo-side work: Task T-01 (P1): build the glasses-link alerts (on top of T-00, the shared alert patterns and repeating speech). Then an assisted test: a person unplugs the board; the test checks the timing from the logs.

**TC-022-003** · row 244 · **Fix typo** · W-10, REQ-CON-02, REQ-CON-03
- Finding: Step says "Raspberry Pi 6".
- Steps (B) →  1. Power on Raspberry Pi 5
- Expected (D) →  The device says "Vibration is not working" (or that the vibration feedback cannot be reached) once.
- Repo status now: NOT-BUILT: not built yet. Repo-side work: Task T-01 (P1): build the glasses-link alerts (on top of T-00, the shared alert patterns and repeating speech). Then an assisted test: a person unplugs the board; the test checks the timing from the logs.

**TC-022-004** · row 245 · **Clarify** · REQ-CON-02, REQ-CON-03
- Finding: Same wording as TC-022-002.
- Expected (D) →  Once the connection is removed, the device says "Vibration is not working" (or that the vibration feedback cannot be reached), repeated every 30 seconds.
- Repo status now: NOT-BUILT: not built yet. Repo-side work: Task T-01 (P1): build the glasses-link alerts (on top of T-00, the shared alert patterns and repeating speech). Then an assisted test: a person unplugs the board; the test checks the timing from the logs.

**TC-022-005** · row 246 · **Clarify** · REQ-CON-02, REQ-CON-03
- Finding: Same wording as TC-022-002.
- Expected (D) →  Once the connection is removed, the device says "Vibration is not working" (or that the vibration feedback cannot be reached) once.
- Repo status now: NOT-BUILT: not built yet. Repo-side work: Task T-01 (P1): build the glasses-link alerts (on top of T-00, the shared alert patterns and repeating speech). Then an assisted test: a person unplugs the board; the test checks the timing from the logs.

**TC-022-006** · row 247 · **Rewrite** · W-11, REQ-CON-03
- Finding: Steps say the ESP32 is connected, the input says it isn't. Expects a repeat every 30 s with depth switched off, which contradicts TC-022-003 and 022-005 (once when depth is off). The team decided: once.
- Steps (B) →  1. Power on Raspberry Pi 5 with the ESP32 connected and depth estimation on  2. Disconnect the ESP32  3. On the control panel, turn the depth estimation switch off
- Input (C) →  ESP32 disconnected while running, then depth estimation switched off mid-run
- Expected (D) →  After the switch is turned off, the device says once that the vibration feedback cannot be reached, and does not repeat it while depth estimation stays off.
- Repo status now: NOT-BUILT: not built yet. Repo-side work: Task T-01 (P1): build the glasses-link alerts (on top of T-00, the shared alert patterns and repeating speech). Then an assisted test: a person unplugs the board; the test checks the timing from the logs.

**TC-022-007** · row 248 · **Clarify** · REQ-CON-04
- Finding: The team defined the reconnect feedback: a "beep beep" on all three motors plus speech.
- Expected (D) →  On reconnection, all three motors give a short "beep beep" and the device says that everything is connected.
- Repo status now: NOT-BUILT: not built yet. Repo-side work: Task T-01 (P1): build the glasses-link alerts (on top of T-00, the shared alert patterns and repeating speech). Then an assisted test: a person unplugs the board; the test checks the timing from the logs.

**TC-023-001** · row 253 · **Clarify** · REQ-CON-01
- Finding: "ESP32" is ambiguous here: this module is about the control panel.
- Input (C) →  Control panel (ESP32) is connected
- Repo status now: READY: built; test not written yet. Repo-side work: P1. Assisted start-up check (already works; no code change).

**TC-023-002** · row 254 · **Clarify** · REQ-CON-05
- Finding: "Previously used settings" means the last session's settings (team). Today nothing is saved between sessions, so this needs building.
- Input (C) →  Control panel (ESP32) is disconnected before the device starts up.
- Expected (D) →  The device says the controller board cannot be reached and that it will use the settings from the last session, repeated every 5 minutes.
- Repo status now: NOT-BUILT: not built yet. Repo-side work: Task T-02 (P1): build the control-panel alerts and save/restore settings between sessions. Then an assisted test.

**TC-023-004** · row 255 · **Renumber + clarify** · W-03, REQ-CON-05
- Finding: IDs in this scenario run 001, 002, 004, 007 (copied from TC-022's numbering).
- ID (A) →  TC-023-003
- Expected (D) →  On disconnection, the device says the controller board cannot be reached and that it will keep using the current settings, repeated every 5 minutes.
- Repo status now: NOT-BUILT: not built yet. Repo-side work: Task T-02 (P1): build the control-panel alerts and save/restore settings between sessions. Then an assisted test.

**TC-023-007** · row 256 · **Renumber** · W-03, REQ-CON-06
- Finding: See TC-023-004.
- ID (A) →  TC-023-004
- Repo status now: NOT-BUILT: not built yet. Repo-side work: Task T-02 (P1): build the control-panel alerts and save/restore settings between sessions. Then an assisted test.

**TC-024-002** · row 262 · **Rewrite pipeline paragraph** · D-13, REQ-CON-07, REQ-CON-08
- Finding: "Proceed in mock mode" is wrong for a deployed device: mock mode generates fake detections and is for testing only, entered only by the --mock flag. The team decided the device keeps running without the camera and keeps looking for one.
- Expected (D) →  A quick "beep beep" vibration on all three motors and the spoken alert "Critical Error: Camera cannot be reached", repeated every 10 seconds. The device does not crash: every other part keeps running, it keeps checking the USB ports for a camera, and the vision pipeline starts when one appears. It never switches to mock (test) mode on its own.
- Repo status now: NOT-BUILT: not built yet. Repo-side work: Task T-03 (P1): keep the device running without a camera and keep checking for one (T-03a, in progress), add the camera alerts, and remove the automatic switch to mock mode (T-03b). The camera unplug is simulated in software for the automated tests, plus one real unplug by a person.

**TC-024-003** · row 263 · **Rewrite pipeline paragraph** · D-13, REQ-CON-07, REQ-CON-08
- Finding: Same as TC-024-002.
- Expected (D) →  A quick "beep beep" vibration on all three motors and the spoken alert "Critical Error: Camera cannot be reached", repeated every 10 seconds. The device does not crash: every other part keeps running, it keeps checking the USB ports for a camera, and vision resumes when one is plugged in. It never switches to mock (test) mode on its own.
- Repo status now: NOT-BUILT: not built yet. Repo-side work: Task T-03 (P1): keep the device running without a camera and keep checking for one (T-03a, in progress), add the camera alerts, and remove the automatic switch to mock mode (T-03b). The camera unplug is simulated in software for the automated tests, plus one real unplug by a person. This is the case being worked on now.

**TC-024-004** · row 264 · **Fix steps** · W-15, REQ-CON-09
- Finding: Steps start with the camera connected; the input says it starts disconnected. Pick one.
- Steps (B) →  1. Power on Raspberry Pi 5 with the USB camera connected  2. Disconnect the camera and wait for the camera alert  3. Reconnect the camera
- Input (C) →  USB camera disconnected while running, then reconnected
- Expected (D) →  A quick "camera has been reconnected" spoken message; detection and depth feedback resume as normal.
- Repo status now: NOT-BUILT: not built yet. Repo-side work: Task T-03 (P1): keep the device running without a camera and keep checking for one (T-03a, in progress), add the camera alerts, and remove the automatic switch to mock mode (T-03b). The camera unplug is simulated in software for the automated tests, plus one real unplug by a person.

**TC-025-003** · row 273 · **Fix wording** · REQ-CON-10, REQ-CON-11
- Finding: A vibration is not "stated".
- Expected (D) →  A brief "strong weak, strong weak, strong weak" vibration on all three motors to indicate an error, played once.
- Repo status now: NOT-BUILT: not built yet. Repo-side work: Task T-04 (P1): build the earphone-loss vibration alert and the reconnect confirmation. Then an assisted test.

**TC-025-004** · row 274 · **Fix wording** · W-13, REQ-CON-10, REQ-CON-11
- Finding: Says the earphones are disconnected "from GPIO"; they are USB.
- Input (C) →  USB earphones are unplugged after the device starts up. Object detection on the control panel is enabled.
- Repo status now: NOT-BUILT: not built yet. Repo-side work: Task T-04 (P1): build the earphone-loss vibration alert and the reconnect confirmation. Then an assisted test.

**TC-025-005** · row 275 · **Fix input** · W-12, W-13, REQ-CON-11
- Finding: Same input as TC-025-004 (detection enabled) but the opposite expected result. The team confirmed it means detection disabled. Also "from GPIO".
- Input (C) →  USB earphones are unplugged after the device starts up. Object detection on the control panel is disabled.
- Repo status now: NOT-BUILT: not built yet. Repo-side work: Task T-04 (P1): build the earphone-loss vibration alert and the reconnect confirmation. Then an assisted test.

**TC-025-006** · row 276 · **Rewrite** · W-13, REQ-CON-11
- Finding: Input says "not connected to the GPIO pins" (they are USB), the steps never unplug them, and it repeats every 30 s with detection off, which contradicts TC-025-003. The team decided: once.
- Steps (B) →  1. Power on Raspberry Pi 5 with the USB earphones connected and object detection on  2. Unplug the earphones  3. On the control panel, turn the object detection switch off
- Input (C) →  USB earphones unplugged while running, then object detection switched off mid-run
- Expected (D) →  After the switch is turned off, a brief "strong weak, strong weak, strong weak" vibration on all three motors plays once and does not repeat while object detection stays off.
- Repo status now: NOT-BUILT: not built yet. Repo-side work: Task T-04 (P1): build the earphone-loss vibration alert and the reconnect confirmation. Then an assisted test.

**TC-025-007** · row 277 · **Rewrite steps** · W-14, REQ-CON-12
- Finding: Steps are copied from TC-022-007 (they remove the ESP32's pins) while the input is about the earphones.
- Steps (B) →  1. Power on Raspberry Pi 5 with the USB earphones connected  2. While the pipeline runs, unplug the earphones  3. After a while, plug them back in
- Expected (D) →  On reconnection, all three motors give a short "beep beep" and the device says that everything is connected.
- Repo status now: NOT-BUILT: not built yet. Repo-side work: Task T-04 (P1): build the earphone-loss vibration alert and the reconnect confirmation. Then an assisted test.

No change:

- **TC-022-001** (Keep): Sound. Spelling: "Seed Xiao" is "Seeed Studio XIAO" (optional). Repo status: READY: built; test not written yet. Repo-side work: P1. Assisted start-up check (already works; no code change).
- **TC-024-001** (Keep): Sound. Repo status: READY: built; test not written yet. Repo-side work: P1. Assisted start-up check (already works; no code change).
- **TC-025-001** (Keep): Sound. Repo status: READY: built; test not written yet. Repo-side work: P1. Assisted start-up check (already works; no code change).
- **TC-025-002** (Keep): Sound. Repo status: NOT-BUILT: not built yet. Repo-side work: Task T-04 (P1): build the earphone-loss vibration alert and the reconnect confirmation. Then an assisted test.
- **TC-026-001** (Keep): Sound; recorded Passed. Repo status: READY: built; test not written yet. Repo-side work: None needed (hardware, no code). A person may repeat it for fresh evidence.
- **TC-026-002** (Keep): Sound; recorded Passed. The battery script is the X1202 vendor's, not in the repo. Repo status: READY: built; test not written yet. Repo-side work: None needed (vendor script). A person may repeat it for fresh evidence.
- **TC-026-003** (Keep): Sound; recorded Passed. Repo status: READY: built; test not written yet. Repo-side work: None needed (hardware, no code).

### M09 On-board Power Related Checks

**TC-028-001** · row 313 · **Clarify** · REQ-PWR-02
- Finding: Doesn't say when the announcement is made. The team decided: at power-on, and whenever the level drops past it while running.
- Expected (D) →  The device says "Battery at 80%" when powered on at this level, and when the level falls to it while running.
- Repo status now: NOT-BUILT: not built yet. Repo-side work: Task T-06: build battery announcements. Tested by running the battery down while a script records each announcement (combined with the rundown test, G-12).

**TC-028-002** · row 314 · **Clarify** · REQ-PWR-02
- Finding: Doesn't say when the announcement is made. The team decided: at power-on, and whenever the level drops past it while running.
- Expected (D) →  The device says "Battery at 60%" when powered on at this level, and when the level falls to it while running.
- Repo status now: NOT-BUILT: not built yet. Repo-side work: Task T-06: build battery announcements. Tested by running the battery down while a script records each announcement (combined with the rundown test, G-12).

**TC-028-003** · row 315 · **Clarify** · REQ-PWR-02
- Finding: Doesn't say when the announcement is made. The team decided: at power-on, and whenever the level drops past it while running.
- Expected (D) →  The device says "Battery at 40%" when powered on at this level, and when the level falls to it while running.
- Repo status now: NOT-BUILT: not built yet. Repo-side work: Task T-06: build battery announcements. Tested by running the battery down while a script records each announcement (combined with the rundown test, G-12).

**TC-028-004** · row 316 · **Clarify** · REQ-PWR-02
- Finding: Doesn't say when the announcement is made. The team decided: at power-on, and whenever the level drops past it while running.
- Expected (D) →  The device says "Battery at 20%. Recharge your Second Vision Device Soon." when powered on at this level, and when the level falls to it while running.
- Repo status now: NOT-BUILT: not built yet. Repo-side work: Task T-06: build battery announcements. Tested by running the battery down while a script records each announcement (combined with the rundown test, G-12).

**TC-028-001 (5 %, sheet row 317)** · row 317 · **Renumber + clarify** · W-02, REQ-PWR-02
- Finding: Duplicate ID: the 5 % case is a second TC-028-001.
- ID (A) →  TC-028-005
- Expected (D) →  The device says "Battery at 5%. Device might shut down soon. Recharge as soon as possible." when powered on at this level, and when the level falls to it while running.
- Repo status now: NOT-BUILT: not built yet. Repo-side work: Task T-06, as above.

**TC-029-001** · row 324 · **Clarify** · REQ-PWR-03
- Finding: Mixes two actions. The "momentary push button" is the power button itself. Holding it forces power off (nothing can be said); double-pressing it shuts down cleanly, and that is when the announcement plays.
- Input (C) →  Hold the power button (forced off) / double-press the power button (clean shutdown)
- Expected (D) →  Raspberry Pi shuts down. On a double-press (clean shutdown) it first says "Device shutting down."; a forced power-off by holding the button says nothing.
- Repo status now: NOT-BUILT: not built yet. Repo-side work: Task T-07: speak "Device shutting down." on a clean shutdown. Then an assisted test.

No change:

- **TC-027-001** (Keep): Sound. The boot service is installed and enabled. Repo status: READY: built; test not written yet. Repo-side work: Assisted check that the boot service starts the pipeline. No code change planned.
- **TC-029-002** (Keep): Sound. Repo status: NOT-BUILT: not built yet. Repo-side work: Task T-06: build the low-battery shutdown. Tested during the battery rundown.

### M10 Controller Board Tests

**TC-030-001** · row 345 · **Clarify** · REQ-PNL-01
- Finding: Silence only means something with an object in view.
- Input (C) →  Off position for object detection, with a recognisable object in view
- Repo status now: READY: built; test not written yet. Repo-side work: Assisted test: a person sets the switch or knob; the test checks the device's response from its logs; one hands-on confirmation per device. No code change planned. Queued after the P1 tasks.

**TC-030-002** · row 346 · **Fix wording** · REQ-PNL-02
- Finding: "be reflected" is missing a verb.
- Input (C) →  On position for object detection, with a recognisable object in view
- Expected (D) →  Audio feedback for identified objects is heard in the earphones.
- Repo status now: READY: built; test not written yet. Repo-side work: Assisted test: a person sets the switch or knob; the test checks the device's response from its logs; one hands-on confirmation per device. No code change planned. Queued after the P1 tasks.

**TC-030-003** · row 347 · **Fix steps** · W-16, REQ-PNL-03
- Finding: Step reads "Power on depth estimation switch to the off position".
- Steps (B) →  1. Power on Raspberry Pi 5  2. Turn the depth estimation switch to the off position
- Input (C) →  Off position for depth estimation, with an obstacle about 1 m ahead
- Repo status now: READY: built; test not written yet. Repo-side work: Assisted test: a person sets the switch or knob; the test checks the device's response from its logs; one hands-on confirmation per device. No code change planned. Queued after the P1 tasks.

**TC-030-004** · row 348 · **Clarify** · REQ-PNL-04
- Finding: Vibration only appears with an obstacle in view.
- Input (C) →  On position for depth estimation, with an obstacle about 1 m ahead
- Repo status now: READY: built; test not written yet. Repo-side work: Assisted test: a person sets the switch or knob; the test checks the device's response from its logs; one hands-on confirmation per device. No code change planned. Queued after the P1 tasks.

**TC-031-002** · row 356 · **Revise expected** · D-04, REQ-PNL-06
- Finding: At the knob's minimum the device is deliberately quiet, not silent (team decision: the code wins).
- Expected (D) →  The audio feedback is quiet but still audible.
- Repo status now: READY: built; test not written yet. Repo-side work: Assisted test: a person sets the switch or knob; the test checks the device's response from its logs; one hands-on confirmation per device. No code change planned. Queued after the P1 tasks.

**TC-031-003** · row 357 · **Clarify** · REQ-PNL-07
- Finding: "Not too loud, not too small" has no bar. The code places the middle half-way between minimum and maximum as the ear hears it (in decibels); the team accepted that.
- Expected (D) →  The volume is audibly between the minimum and the maximum: about half-way as the ear hears it.
- Repo status now: READY: built; test not written yet. Repo-side work: Assisted test: a person sets the switch or knob; the test checks the device's response from its logs; one hands-on confirmation per device. No code change planned. Queued after the P1 tasks.

**TC-032-002** · row 365 · **Revise expected** · D-14, W-24, REQ-PNL-09
- Finding: The team decided to keep the current design: the strength knob multiplies every motor command, so at minimum the motors are off.
- Expected (D) →  The device does not vibrate.
- Repo status now: READY: built; test not written yet. Repo-side work: Assisted test: a person sets the switch or knob; the test checks the device's response from its logs; one hands-on confirmation per device. No code change planned. Queued after the P1 tasks.

**TC-032-003** · row 366 · **Revise expected** · D-14, W-24, REQ-PNL-10
- Finding: Under the same decision, the middle setting halves every motor command.
- Expected (D) →  The vibration is about half as strong as at the maximum.
- Repo status now: READY: built; test not written yet. Repo-side work: Assisted test: a person sets the switch or knob; the test checks the device's response from its logs; one hands-on confirmation per device. No code change planned. Queued after the P1 tasks.

No change:

- **TC-031-001** (Keep): Sound. Repo status: READY: built; test not written yet. Repo-side work: Assisted test: a person sets the switch or knob; the test checks the device's response from its logs; one hands-on confirmation per device. No code change planned. Queued after the P1 tasks.
- **TC-032-001** (Keep): Sound. Repo status: READY: built; test not written yet. Repo-side work: Assisted test: a person sets the switch or knob; the test checks the device's response from its logs; one hands-on confirmation per device. No code change planned. Queued after the P1 tasks.

## C. After the revision (for the person)

- Download the revised sheet as .xlsx and save it as `tests/Group2-SV_Test-Tracker.xlsx` in
  the repo, then run `python3 docs/testing/tools/extract_uat.py`. It lists every case whose
  text changed.
- The Lead agent will notice the workbook changed and stop, as designed. Ask it to update the
  requirements and traceability from the new snapshot (the renumbered IDs included) and to
  close the D-xx and W-xx entries that the revision applied.
