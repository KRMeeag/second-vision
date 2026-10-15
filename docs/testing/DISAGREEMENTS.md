# UAT disagreements

Where the UAT workbook and the code (or the workbook and itself) disagree, and
what was decided. [REQUIREMENTS.md](REQUIREMENTS.md) cites these entries by ID.

- **Part 1 (D-xx)**: the workbook contradicts the code or the project scope.
- **Part 2 (W-xx)**: defects inside the workbook: copy errors, typos, broken formulas.

The workbook stays the authoritative UAT document. An entry here records a
decision about *which side changes*; it does not edit the workbook. Whoever
maintains the workbook applies the "Workbook change" lines.

## Status lifecycle

| Status | Meaning |
|---|---|
| **OPEN** | Needs a decision from the team. Agents must not pick a side. |
| **DECIDED** | Decided; the workbook, code or docs still need the change listed. |
| **CLOSED** | The change is made. For workbook changes, `tools/extract_uat.py` shows the case text changed. |

"Winner" says which side the decision favours. **CODE**: the workbook is
corrected to match the code. **WORKBOOK**: the code or docs are corrected.
**SCOPE**: the case leaves the agentic workflow's scope.

Decisions dated 2026-10-11 were made in the planning session that set up this
folder. Add a name to each when the team confirms it.

---

## Part 1: Workbook vs code and scope

### D-01 · Motor PWM cap · TC-011-001, TC-011-002 · DECIDED · Winner: CODE

- **Workbook:** firmware must cap PWM at 153 (about 3 V average). The comment
  reads "Prevents hardware destruction since physical resistors were removed".
- **Code:** both firmware copies set `MAX_PWM = 255` (`firmware/esp32_haptic_feedback/src/main.cpp`,
  here and on `feat/esp32-glasses`). The Pi sends up to `PWM_MAX = 255` (`src/second_vision/core/haptics.py`).
- **Decision (2026-10-11):** 255 is known to be safe. TC-011's premise is out of date.
  Pi-side tests that drive the motors may run at full scale.
- **Workbook change:** rewrite or remove TC-011-001 and TC-011-002 and their rationale.
  Both run on the ESP32, so they stay out of the agentic scope either way.

### D-02 · Glasses wire format · TC-010-001 to TC-012-003 · DECIDED · Winner: CODE

- **Workbook:** the Pi sends JSON strings (`{"left": 100, ...}`), parsed by ArduinoJson.
- **Code:** both sides use binary frames starting `0xAA`, with a checksum and an ACK
  (`src/second_vision/workers/serial_worker.py`, `firmware/esp32_haptic_feedback/src/main.cpp`).
  The firmware still includes ArduinoJson but never parses with it.
- **Decision (2026-10-11):** the code wins. These cases run on the ESP32 and are
  out of the agentic scope.
- **Workbook change:** express inputs as binary motor packets. Drop TC-012-001
  (missing JSON keys); fixed-length frames have no equivalent.

### D-03 · Ground hazards · TC-006-001, TC-006-002, TC-017-002 · DECIDED · Winner: SCOPE

- **Workbook:** a staircase reads as hazard "down", a curb or step as hazard "up",
  and a drop-off on a textured floor fires the haptics. Module M02's description
  promises "ground-hazard warnings".
- **Code:** `GROUND_HAZARD_ENABLED = False` (`src/second_vision/core/depth_utils.py`).
  This is recorded as D36 in `documentation/DECISIONS.md`.
- **Decision (2026-10-11):** drop-off and stair detection are out of scope because of difficulty.
- **Workbook change:** mark the three cases out of scope. Remove "ground-hazard
  warnings" from M02's description. Their recorded "Passed" results (TC-006-001
  and 006-002) predate D36.

### D-04 · Volume knob at 0 % · TC-031-002 · DECIDED · Winner: CODE

- **Workbook:** "The volume of the audio feedback should be silent".
- **Code:** the knob's minimum maps to espeak amplitude 10, not 0
  (`_AMPLITUDE_FLOOR` in `src/second_vision/workers/tts_worker.py`).
  `tests/test_tts_volume.py::test_knob_fully_down_is_quiet_not_silent` asserts this.
- **Decision (2026-10-11):** the code wins.
- **Workbook change:** expected result becomes "quiet but still audible".

### D-05 · Zone hysteresis band · TC-002-004 · DECIDED · Winner: CODE

- **Workbook:** "System must not flag any classes within this zone".
- **Code:** inside a boundary band an object keeps its previous zone instead of being
  dropped (`_get_detection_zone`, `src/second_vision/pipeline/callbacks.py`). This is D37 in `DECISIONS.md`.
- **Decision (2026-10-11):** the code wins.
- **Workbook change:** expected result becomes "keeps the zone it had before entering the band".

### D-06 · Detection classes · TC-001-001, TC-001-002, TC-002-001, TC-013-001, TC-013-002 · DECIDED · Winner: mixed

- **Workbook:** TC-001-001 uses "13 images (one per trained class)". TC-013-001 uses a
  cup and TC-013-002 a laptop. TC-002-001 uses a trash bin.
- **Code (models pulled in commit `eda45cd`):** `models/hef_deploy/labels.json` has 14
  classes. `DETECTION_CLASS_MAP` reports them under 12 names, because Bicycle and
  Tricycle are both announced as "vehicle". "Trash Bins" is a class. Cup and laptop are not.
- **Decisions (2026-10-11):**
  - The model's 14 classes are correct; the workbook's 13 is out of date.
  - Reporting Bicycle and Tricycle as "vehicle" **needs to be fixed** (task T-05). Confirmed
    2026-10-12: the merge was a stop-gap for the earlier model; each of the 14 classes gets
    its own spoken label.
  - Cup and laptop are replaced by in-model classes when tested, logged in DEVIATIONS.md.
- **Workbook change:** "13" becomes "14". Replace cup and laptop in TC-013-001 and 013-002.

### D-07 · Speech message format · TC-008-004, TC-013-001, TC-014-001 to TC-014-003 · DECIDED · Winner: CODE

- **Workbook:**
  - TC-008-004 feeds `{"class": "person", "distance": 2.0}` and expects "Person at two meters".
  - TC-013 and TC-014 treat queue items as plain strings ("Person ahead").
- **Code:** the speech mailbox carries the contract dict `{"label", "zone", "confidence",
  "priority", "tier", "phrase"?}` or `{"announce"}` (`AGENTS.md` Rule 2). It is spoken
  as `phrase` if present, else `"<label> <zone>"` (`_text_for`, `tts_worker.py`). The
  contract has no distance field.
- **Decision (2026-10-11):** the code wins; these cases misrepresent the design.
- **Workbook change:** rewrite inputs as contract dicts. TC-008-004's expected phrase
  becomes the label-and-zone form (e.g. "person left").

### D-08 · Referenced items that don't exist · several · DECIDED · Winner: mixed

| Item | Cases | Decision |
|---|---|---|
| `./scripts/setup_env.sh` | TC-001 to TC-003 | Misspelled. The project's launcher is `scripts/run.sh` (venv, Hailo environment and `PYTHONPATH`). `scripts/env.sh` only activates the venv, and only when `source`d. |
| `./tests/obj_det_unit/*.py` | TC-001 to TC-003 | Not in any repo on the Pi. Replaced by UAT tests under `tests/uat/` (planned); step changes logged in DEVIATIONS.md. |
| `scripts/init_audio.py`, `scripts/tts_test.py`, `scripts/play_alert.py` | TC-007 to TC-009 | Same as above. |
| `obstacle_beep.wav`, `sweep_200_4000Hz.wav` | TC-009-001, TC-009-002 | Origin unknown. The sweep can be generated for TC-009-002. No alert-sound feature exists for TC-009-001; see D-11. |
| `DE_Pipeline_Status` | TC-018-001, TC-018-002 | Not in the code. Probably to be built, but **deferred**. |
| Docker for SC-DepthV3 | TC-016 to TC-018 environment | **Out of scope.** Depth runs on the Hailo inside the GStreamer pipeline. |

- **Workbook change:** point steps at `scripts/run.sh` and the `tests/uat/` tests once
  they exist. Remove Docker from the environment.

### D-09 · Hardware names · module environments · DECIDED · Winner: WORKBOOK

- **Workbook:** camera is the IMX291; the controller board is a generic ESP32; the
  glasses ESP32 connects over UART on the GPIO pins.
- **Docs:** `README.md` says "OV2640 USB Camera Module" and "ESP32 (wired USB serial)".
  `AGENTS.md` calls the control panel an "Arduino".
- **Code agrees with the workbook:** `firmware/control-panel/platformio.ini` targets `esp32dev`;
  `scripts/sv-main.sh` uses `/dev/serial0` (glasses) and `/dev/ttyAMA3` (panel).
- **Decision (2026-10-11):** the workbook wins.
- **Docs change:** fix README.md and AGENTS.md. Not yet done; this folder is the
  requirements layer only.

### D-10 · Speech under rapid load · TC-008-002 · DECIDED · Winner: CODE

- **Workbook:** with 20 rapid strings under heavy load, "TTS pipeline queues and streams
  audio sequentially without buffer underruns, popping, or voice distortion".
- **Code:** the speech mailbox holds one message and keeps whichever of {stored, incoming}
  is more important. Rapid lower-priority messages are dropped on purpose (D30 in
  `DECISIONS.md`). TC-014-003 already accepts dropping.
- **Decision (2026-10-12):** keep the current design; dropping lower-priority messages is
  intended. The case checks audio quality, not that every message is spoken.
- **Workbook change:** expected result becomes "speech that does play has no underruns,
  popping or distortion; lower-priority messages may be dropped".

### D-11 · Beep sound vs urgent speech · TC-009-001 · DECIDED · Winner: CODE

- **Decision (2026-10-15):** the case is about the **interruption**: urgent speech cuts off
  the current sentence. No beep-sound feature is built.
- **Workbook change:** replace "play `obstacle_beep.wav` while TTS is active" with "trigger an
  urgent detection while the device is speaking". Expected: the urgent item interrupts and
  is spoken clearly at once.

Original record:

- **Workbook:** a beep sound file (`obstacle_beep.wav`) is played while the device is
  speaking, and "High-priority alert sound plays instantaneously".
- **Code:** the device can't play sound files; everything it says goes through speech
  synthesis. What it does have is interruption: when something urgent appears (for example
  a vehicle straight ahead), it stops the current sentence and speaks the urgent item at
  once (`tests/test_priority.py::test_worker_preempted_by_urgent`).
- **Question:** should a separate beep sound be built, or is "urgent speech interrupts
  current speech" what this case is really testing? If the latter, the case's steps change
  to triggering an urgent detection mid-sentence (a DEVIATIONS.md entry).

### D-12 · Stairs and Potholes as detection classes · TC-001-001 (14-class coverage) · DECIDED · follow-up OPEN

- **Context:** D-03 puts ground hazards ("drop-off detection or stair detection") out of scope.
- **Code:** the new detection model includes `Stairs` and `Potholes`, spoken as "stairs" and
  "pothole" (`DETECTION_CLASS_MAP`).
- **Decision (2026-10-12):** in scope. The object detector may announce stairs and potholes
  it recognises in the picture. D-03 only removed the depth-based ground-break detector.
- **Follow-up (needs the team):** `README.md` tells every wearer the device "will **not**
  warn about stairs, curbs or drop-offs". D36 in `DECISIONS.md` requires every doc to say
  so plainly, because a device that seems to see stairs but misses most of them is more
  dangerous than one known not to. With stairs now announced *sometimes*, that statement
  needs rewording rather than deleting. One possible wording: "may announce stairs it
  recognises, but does not reliably detect drop-offs: never rely on it for stairs". D36 was
  a team decision, so the team should agree the wording.
- **Clarified (team, 2026-10-15):** object detection does recognise stairs. "Doesn't recognise
  stairs" referred to depth estimation, which was meant to detect drop-offs and no longer does
  (D36). The README sentence should say exactly that: the object detector may name stairs it
  sees, but the depth sensing does not detect drop-offs or steps. **README change still to
  be made** (not done in this folder's work).

### D-13 · Camera fallback "mock mode" · TC-024-002, TC-024-003 · DECIDED · Winner: WORKBOOK intent, wording corrected

- **Workbook:** when the camera is missing, "allow the device to proceed in mock mode
  temporarily".
- **Code:** `--mock` replaces the camera and the Hailo with generated fake detections and
  depth (`src/second_vision/mock/`), and they are spoken and vibrated like real ones.
- **Decision (2026-10-12):** mock mode is for testing only and is reachable only through the
  `--mock` command-line flag; a deployed device must never enter it on its own. On a missing
  or removed camera, the deployed device keeps every other part running and keeps checking
  the USB ports for a camera (REQ-CON-08).
- **Why it matters:** removing the camera currently produces a Hailo "No available devices"
  error that crashes the whole program.
- **Workbook change:** replace "proceed in mock mode temporarily" with "keep running without
  the camera and keep checking for one".
- **Follow-up (team, 2026-10-15):** two code paths still enter mock mode without `--mock`
  being typed, and both go in T-03b:
  - `scripts/sv-main.sh:69-74` passes `--mock` itself when `/dev/video0` is missing. Its guard
    for the boot service runs earlier, so the camera dropping between the two checks still
    reaches it. Decided: remove the fallback.
  - `main.py:160` runs mock mode when `hailo_apps` doesn't import. Under the same decision,
    that becomes a clear error instead.

### D-14 · Strength knob at minimum · TC-032-002, TC-032-003 · DECIDED · Winner: CODE

- **Workbook:** knob at 0 % → "The vibration of the device should be minimal"; at 50 % →
  "not too strong, but not too weak".
- **Code on this branch** (`feat/test-and-robustness`, and `deploy/v2`):
  - The knob's value (0.00 to 1.00) multiplies every motor command directly
    (`src/second_vision/workers/serial_worker.py`). At 0 the motors are **off**.
  - At low settings the commands can fall below the strength the motors need to start, and
    the code's own comment flags this as an unmade UX decision.
- **Code on `feat/depth-callback`:** commit `3797182` (2026-10-09, "make the knobs work … strength
  stays felt") scales only the part of each command above the lightest felt tap. Knob 0 = the
  lightest tap, never silence; switching off stays the depth switch's job. This branch doesn't
  contain that commit.
- **Question:** which behaviour is intended? If "minimal, never silent" (what the workbook
  says), `3797182`'s change needs to come into this branch. That would be a task for the
  workflow, or a merge by the team.
- **Decision (team, 2026-10-15): keep the code on this branch.** The knob multiplies every
  motor command; at minimum the motors are off, and at the middle every command is halved.
  `3797182` is not brought in, and the proposed task T-09 is withdrawn.
- **Watch at merge time:** merging `feat/depth-callback` into this branch would bring
  `3797182` and silently reverse this decision. Whoever merges must keep this branch's
  scaling in `serial_worker.py` or reopen D-14 with the team.
- **Workbook change:** see W-24.
- *This corrects REQ-PNL-09 as written on 2026-10-12, which said the code already agreed with
  "minimal". That is true only on `feat/depth-callback`.*

---

## Part 2: Workbook defects

The workbook owner fixes these; nothing in the code changes.

| ID | Where | Defect | Suggested correction |
|---|---|---|---|
| W-01 | TC-001-003 | Expected result is leftover template text: "System displays error 'Password must be at least 8 characters'; account is not created." It is still recorded as Passed. | Proposed (2026-10-12): "Each image returns its correct class label with confidence > 0.5", the same bar as TC-001-001. Also name the thinnest-sourced classes. |
| W-02 | Test Cases row 317 | Duplicate ID: the 5 % battery case is a second `TC-028-001`. | Renumber to TC-028-005. |
| W-03 | TC-023 | IDs run 001, 002, 004, 007, copied from TC-022's numbering. | Renumber 001 to 004. |
| W-04 | Scenarios row 79 | Scenario written `TC-10`, so the scenario count shows 31 instead of 32. | `TC-010`. |
| W-05 | Test Cases row 329 | The TC-030 to TC-032 block is labelled "On-board Power Related Checks". Its description is copied from the power block, and its case-count formula counts TC-027 to TC-029. | Relabel "Controller Board Tests"; fix the description and formula. |
| W-06 | Summary | Pass total `=SUM(D18:D38)` stops before TC-022, so it shows 24 instead of 27. The Fail cell and the Total row are typed numbers, not formulas. | Widen the range; use formulas. |
| W-07 | Block headers | "Date Tested" is 2026-08-03 on blocks never run, a week before the workbook was created (2026-08-10). | Clear until tested. |
| W-08 | Test Cases row 155 | TC-015 has no scenario title. | "Pipeline Integration, Concurrency & Resource Management". |
| W-09 | TC-008-004 | No Actual or Status cells, unlike the rest of its block. | Add them. |
| W-10 | TC-022-003 | Step says "Power on Raspberry Pi 6". | "Raspberry Pi 5". |
| W-11 | TC-022-006 | Steps say "with ESP32 Connected"; input says "ESP32 is not connected". It expects repeats every 30 s after depth is switched off, which contradicts TC-022-003 and 022-005 (once when depth is off). | Decided 2026-10-12: **once**. Align the steps and input (ESP32 disconnected, then depth switched off). |
| W-12 | TC-025-004 / 025-005 | Identical inputs (detection **enabled**), opposite expected results (every 30 s vs once). | Confirmed 2026-10-12: 025-005 means detection **disabled**. |
| W-13 | TC-025-004 to 025-006 | Earphones described as disconnected "from GPIO" or "not connected to the GPIO Pins"; they are USB. TC-025-006 repeats every 30 s after detection is switched off, which contradicts TC-025-003. | Fix the wording. Decided 2026-10-12: TC-025-006 is **once**, the same rule as W-11. |
| W-14 | TC-025-007 | Steps copied from TC-022-007 (removes the ESP32's GPIO pins) while the input is about the earphones. | Rewrite the steps for the earphones. |
| W-15 | TC-024-004 | Steps start with the camera connected; input says it starts disconnected. | Pick one starting state. |
| W-16 | TC-030-003 | Step reads "Power on depth estimation switch to the off position". | "1. Power on Raspberry Pi 5  2. Turn the depth estimation switch off". |
| W-17 | Prerequisites | The venv is called `my-hailo-env`; it is `my_hailo_env`. | Rename. |
| W-18 | M02, M06 prerequisites | The two sheets disagree. M02's Test Cases block adds the camera, missing on the Scenarios sheet. M06's Scenarios sheet lists 3 prerequisites, its Test Cases block 7. | Make the sheets match. |
| W-19 | Scenarios row 43 (comment) | Reviewer note: "we probably need a check to see if an audio device is connected". No case covers it directly; TC-007-001 and TC-025 come closest. | Add a case or link it to TC-025. |
| W-20 | Summary title, Features sheet | Template leftovers: "[Project Name] (Template)", and "TENTATIVE (inherited from sample xlsx file)". | Replace. |
| W-21 | TC-001 to TC-003 recorded results | Recorded before the 14-class model (commit `eda45cd`, 2026-10-11) was in the repo, using scripts that aren't in any repo here (D-08). | Re-run before relying on them. |
| W-22 | TC-005-003 recorded "Failed" | Floor suppression was added to the depth chain afterwards (`tests/test_depth_utils.py::test_open_receding_floor_does_not_fire`). | Re-run before relying on it. |
| W-23 | Missing case | The team's battery-rundown endurance test (100 % to 2 % under full load: average FPS, temperature and throttling, battery life, and whether each battery announcement played) has no case. It is written as REQ-PERF-01. | Add a case, next to the manual thermal-throttling performance test. |
| W-24 | TC-032-002, TC-032-003 | After D-14: the knob at minimum switches the motors off, not "minimal"; at the middle every motor command is halved. | TC-032-002 expected: "The device does not vibrate." TC-032-003 expected: "The vibration is about half as strong as at the maximum." |
