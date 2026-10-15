# Test deviations

A record of every place a UAT case was tested with **different steps or inputs** than the
workbook gives.

## The rule (2026-10-11)

When testing, a case's steps and inputs may be changed, or replaced entirely, only if both
of these hold:

1. **The case's intent stays the same**: the core thing it tests is still what gets tested.
2. **The core behaviour of the code is not changed** to make the case pass.

Expected results are **never** changed here. Only the workbook itself, or a decision in
[DISAGREEMENTS.md](DISAGREEMENTS.md), changes what a case expects. If testing a case seems
to need a different expected result, raise it as a disagreement instead.

Record the deviation **before** reporting the result, and reference it from the result.

## Entry template

```
### DV-NNN · TC-xxx-yyy · YYYY-MM-DD · <who: person, or agent role>
- Workbook step / input (verbatim): …
- Used instead: …
- Intent preserved: <the core thing the case tests, and why it is still tested>
- Why: <reason; cite D-xx / W-xx if related>
- Result: <where the run is recorded>
```

## Known in advance

These are already decided. They become entries when a case is actually run:

- Missing scripts are replaced (D-08). `setup_env.sh` becomes `scripts/run.sh`; the
  `tests/obj_det_unit/` scripts and `init_audio.py`, `tts_test.py` and `play_alert.py`
  become UAT tests under `tests/uat/`.
- Cup (TC-013-001) and laptop (TC-013-002) are replaced by classes the model has (D-06).
- TC-001-002's confusion pairs: the workbook names only Motorcycle/Bicycle. Any other pairs
  are the tester's choice.
- TC-009-002's sweep file doesn't exist; it may be generated.
- String payloads in TC-008-004 and TC-014 become contract dicts (D-07).
- TC-009-001's `obstacle_beep.wav` step becomes "trigger an urgent detection while the device
  is speaking" (D-11).
- TC-001-003's "thinnest-sourced classes" are Trash Bins, the class with the fewest training
  images (1702).
- TC-024-002 and TC-024-003's "proceed in mock mode" is not tested; the device must never enter
  mock mode on its own (D-13).

## Entries

*None yet.*
