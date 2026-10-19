# Fix ranking: what to fix first among the in-scope test cases

2026-10-19, at the user's request. A ranking of the work left on the UAT workbook's cases, most critical
first. It reads the workbook through `uat-snapshot.yaml`, TRACEABILITY.md, TASK-BOARD.md and the demo track
(DEMO-TRACK.md), and each claim marked *checked* was confirmed in the code today. TRACEABILITY.md's own
totals date from 2026-10-15, before the demo track built most of the P1 failsafes, so its statuses lag
behind; this file does not change them (only the Lead edits TRACEABILITY.md).

**In scope:** 110 cases, minus 18 that run on the ESP32 (outside the agent workflow), 3 out of scope (D-03)
and 2 deferred (D-08): **87 cases.**

**How they are ranked**, in this order:
1. **Harm to the wearer.** The wearer uses no cane, so a silent failure of the obstacle sense is the worst
   kind: they walk on, trusting a device that has stopped warning them.
2. **Evidence of failure.** A recorded failure, or code that is known missing, before a case that is built
   and merely untested.
3. **How many cases one fix closes.**
4. **Effort**, as a tie-breaker.

## Before anything: what the device boots

`second-vision.service` runs the **base checkout** (`second-vision-repo`), not this worktree (*checked*:
`WorkingDirectory` and `ExecStart` both point there). None of the demo-track work (F0 to F6) runs at boot
until it is committed and merged into what the base checkout has. Every device case below, and TC-027-001
("power on, the pipeline starts"), depends on that. It is a merge step, not a code fix, but it comes first.

## Part 1: fixes that need code

| Rank | Fix | Why it matters | Cases | State now |
|---|---|---|---|---|
| 1 | **Low battery: warn, then shut down cleanly** (T-06, T-07) | Today the battery runs flat with no word of warning. The wearer loses the obstacle sense mid-walk, and the power cut can corrupt the SD card. | 7: TC-028-001 to 004, the 5 % case (TC-028-001#2), TC-029-001, TC-029-002 | **Not built** (*checked*: no battery code in `src/`). Now unblocked: the X1202's battery gauge reads without root (I2C 0x36: 4.18 V, 98.4 % on 2026-10-19). About 3 to 4 h with tests. |
| 2 | **Depth: open floor read as a near obstacle** | Either the motors buzz all the time, so the wearer learns to ignore them, or the floor's reading hides a real obstacle. A simulation on 2026-09-12, before floor suppression was added, found an open floor drove every zone to about 213 of 255, and a person on the right could not move the motors. | TC-005-003 (workbook: **Failed**), TC-017-001, TC-017-003 | Floor suppression is in this tree with unit tests (*checked*), but the recorded failure predates it and was never re-run (W-22). A further floor fix (commit 3797182) is only on `feat/depth-callback`, and merging it would reverse the strength-knob decision D-14. **Depth workstream's fix**: re-run TC-005-003 on the device, then decide the merge. |
| 3 | **Detection: objects announced in an empty scene** | Phantom announcements teach the wearer to tune the speech out, real warnings included. | TC-002-005 (workbook: **Failed**) | Built. The failure was recorded with the old model and scripts not in this repo (W-21). **Re-run with the 14-class model first**; fix only if it still fails. Detection owner. |
| 4 | **Memory grows about 6 MB with every vision restart** (T-12) | A loose camera connector restarts vision again and again; about 170 restarts cost 1 GB. | none directly (REQ-CON-08's robustness) | Measured by the T-03a Tester: 5.8 MB per restart over 15. Not fixed. |
| 5 | **Last session's panel settings are not saved** (T-02a) | A panel that is dead at power-on gives the built-in defaults. Low harm: the defaults are the safest settings, with everything on at full strength and volume (*checked*, `core/config.py`). | TC-023-004 (the settings half of REQ-CON-05) | Not built (*checked*: nothing saves the settings). The spoken alert half is done (F6). |
| 6 | **Bicycle and tricycle are both spoken as "vehicle"** (T-05) | A wrong word, but still a warning of something on wheels. | TC-001-001, TC-001-002 | To fix. Small change in the detection class map. |
| 7 | **Stopping right after a pipeline start can crash** (T-11) | Only on a stop within a fraction of a second of a start; mostly fixed by T-03a. | TC-015-002 (clean stop, no leftover processes) | Rare; re-test on the device. |
| 8 | **One frame of NaN depth silences the motors until a reset** | Silent loss of the obstacle sense, but the model has not been seen to produce NaN. | none directly | Probed 2026-10-19 by a depth session (all-NaN, half-NaN and all-inf frames raise, and the smoothing stays poisoned); `core/depth_utils.py` is the same file here. **Not re-run in this session**; depth workstream's code. |
| 9 | **The strength knob has no automated test** | A test gap, not a known bug. | TC-032-001 to 003 | Built; D-14 decided. |

## Part 2: built, waiting for one check on the device (the highest value today)

All P1, all built by the demo track with unit tests passing in both test environments. Each needs one
person's check on the real device. The F-items' steps are in [DEMO-CHECKLIST.md](DEMO-CHECKLIST.md).

| Order | What to check | Cases | Where |
|---|---|---|---|
| 1 | Camera missing: start without it, pull it while running, plug it back (spoken alert every 10 s, *beep beep*, "Camera has been reconnected") | TC-024-001 to 004 | F0, F4 |
| 2 | Camera covered, too dark, too bright (not a workbook case yet; the demo's own) | none yet | F5 |
| 3 | Glasses board lost and back ("Vibration is not working", the reconnect *beep beep*) | TC-022-001 to 007, TC-018-003 | F2, F1 |
| 4 | Earphones lost and back (*strong weak* x3; "Earphones connected") | TC-025-001 to 007 | F3 |
| 5 | Control panel lost and back ("Controller board cannot be reached..."; "Controller board connected") | TC-023-001, 002, 007 | F6 |
| 6 | Each vibration pattern by feel | (inside the cases above) | F1 |

## Part 3: built and untested (no known bug)

About 50 cases: detection accuracy (TC-001-003 to TC-003-004), audio (TC-007 to TC-009), depth zones
(TC-004 to TC-006), detection to speech (TC-013 to TC-015), depth to haptics (TC-016, TC-017), the panel
(TC-030 to TC-032) and the X1202 button (TC-026, already passed). Their order follows the scenario order
in TRACEABILITY.md. Many detection cases carry a stale workbook result (W-21) and need a re-run with the
14-class model.

**Measurements that serve these cases** (`docs/testing/tools/perf_run.py`): the 20-minute run covers
TC-015-003 (stability) and the frame-rate part of REQ-PERF-01. The battery rundown (REQ-PERF-01) is still
to do and would also exercise rank 1.
