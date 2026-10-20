# The detection model is now hef_v3

2026-10-20, one session, at the user's request ("a new model, hef_v3, has been added; edit the current pipeline to use
it"). No git writes. **Not run on the device yet** (see "Left to do").

## What was found before changing anything

- The user had reorganised `models/` a few minutes earlier: `hef_deploy/` and `hef_eval/` moved under `hef_v2/`, and the
  new files arrived in `hef_v3/` (from `~/Downloads/hef_v3p-20261020T032810Z-1-001.zip`). So `app.py`'s default HEF path,
  `models/hef_deploy/best.hef`, **pointed at nothing**. The installed library treats a path that does not exist as a
  model *name* and looks it up among Hailo's own models, so the pipeline would not have loaded our model at all.
- **hef_v3 has 13 classes, not 14: "Pole" is gone**, and every class after Motorcycle moved down one index
  (Animals 4 to 3, Doors 5 to 4, ... Bench 13 to 12). The HEF agrees: `hailortcli parse-hef` shows `Classes: 13`, the same
  architecture (HAILO8), the same 640x640x3 input and the same on-chip NMS output as v2. Its baked NMS score threshold is
  0.01 (v2: 0.05); at runtime `hailonet`'s `nms-score-threshold=0.3` overrides it, as before.
- **hef_v3 shipped without a `labels.json`**, which v2 had beside each HEF. Without it the postprocess names classes from
  the 80 COCO names and detection goes silent (every label misses the class map). `get_hef_labels_json()` only knows
  Hailo's own models, so there is no fallback.

## What changed

| File | Change |
|---|---|
| `models/hef_v3/hef_deploy/labels.json`, `hef_eval/labels.json` | **New.** `"unlabeled"` and then the names from each folder's own `metadata.yaml` in index order, 4-space JSON. The same derivation reproduces v2's shipped files **byte for byte**, so it is the rule the project already used. 13 names. |
| `src/second_vision/pipeline/app.py` | New `DET_MODEL_VERSION = "hef_v3"`; `DEFAULT_DET_HEF_PATH` is built from it (`models/hef_v3/hef_deploy/best.hef`). Comments and the `--det-hef-path` help text no longer say "14 classes" or the old folder. **To switch models again: change that one constant and check the new folder has a `labels.json`.** |
| `tests/test_detection_classes.py` | The model folder comes from `app.py`'s constant (read as text, so the Poetry environment can run it). "Every model label is mapped" is now a **subset** check (it was equality, which would fail on the stale "Pole" key). New: the default HEF exists, a `labels.json` sits beside it, and its class count equals the HEF's NMS config (`nms_config.json`). 30 tests. |
| `docs/testing/tools/perf_run.py`, `tests/test_perf_tools.py` | The "model file" line in every measurement's evidence read the hard-coded old path. It now follows `DET_MODEL_VERSION` (`det_model_file()`, with a test). |
| `AGENTS.md`, `documentation/TASKS.md` | The repo-structure line and the T-05 line (14 classes in v2, 13 in v3). |

**Not changed on purpose:** `pipeline/callbacks.py` (the class map keeps its "Pole" key; the model simply never emits it, so
it is harmless, and a model that brings poles back works at once) and `core/priority.py`. A comment in `callbacks.py` still
says "custom 14-class HEF, models/hef_deploy"; it is the detection owner's file, so it is flagged here.

## Checked

- `hailortcli parse-hef` on both v2 and v3 (no device needed): see above.
- Rule 6 (the Hailo side): in the installed library, `INFERENCE_PIPELINE(config_json=...)` becomes the postprocess's
  `config-path`, and `resolve_hef_path` uses an existing path as a file path (`core/common/core.py`). The repo already ran
  this way with v2's sidecar; only data changed.
- **Mutation check:** 6 deliberate breakages of the model setup (no `labels.json`; labels in v2's order; a model label with
  no class-map entry; an NMS class count of 14; a version naming a missing folder; the default path back to the old
  folder), each in a scratch copy: **all 6 caught** (`evidence/MODEL-V3-mutations.txt`).
- Full suites: see DEMO-HANDOFF.md, "test baseline".

## What it means for the wearer

- **The detector can no longer announce poles.** Poles are not in v3's classes. The depth branch still sees them as
  obstacles (MANUAL-TESTS has a "thin pole at 1 m" depth case). Whether the model's owner meant to drop Pole is theirs to
  confirm.
- Spoken labels for the other 13 classes are unchanged: the class map is keyed on names, not indices.

## Left to do

1. **A device check, which needs the boot service stopped and the camera, the chip and the boards free** (it is stopped
   now). About 40 seconds: `scripts/sv-main.sh 2>&1 | tee /tmp/v3-smoke.log`, point the camera at a person, a chair or a
   table, then Ctrl+C. Look for `Using labels JSON beside the HEF: .../hef_v3/hef_deploy/labels.json` and
   `[TTS] 'person ...'`-style announcements. **This is the only proof that the labels line up with the chip's output.**
2. **Documents that still say 14 classes or the old folder,** for the team or the Lead (the Lead decides on the workbook and
   requirements): `docs/testing/REQUIREMENTS.md` (REQ-DET-01 "14 images", the `models/hef_deploy/metadata.yaml` path),
   `docs/testing/DISAGREEMENTS.md` D-06 ("the model has 14 classes"), TRACEABILITY's detection rows, and the `callbacks.py`
   comment above. The detection workbook cases were last recorded against another model (W-21) and need a re-run anyway.
3. **The model folders are untracked** (`models/hef_v2/`, `models/hef_v3/`, about 39 MB of HEFs) and git sees the old
   `models/hef_deploy` and `models/hef_eval` as deleted: yours to commit. **The boot service runs the base checkout,
   which still has `models/hef_deploy` (v2) and none of this change.**
