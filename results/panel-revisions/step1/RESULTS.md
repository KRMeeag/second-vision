# Step 1: setup and downloads, results

Done on 2026-11-09, 17:40 to 18:10 (Pi clock, PST = UTC+8), in Kenth's worktree
(`worktrees/kenth`, branch `feat/test-and-robustness` at `80a5c92`). Nothing was run on the Hailo,
the boot service was not touched, and no code was changed. No Filipino recordings were made or
downloaded.

Every value below is in a file in this folder. Two scripts re-create the inventory:

```bash
bash results/panel-revisions/step1/capture_inventory.sh      > results/panel-revisions/step1/inventory.txt 2>&1
bash results/panel-revisions/step1/capture_code_inventory.sh > results/panel-revisions/step1/code-inventory.txt 2>&1
```

| File | What it holds |
|---|---|
| `inventory.txt` | Versions, the boot service, model hashes and `parse-hef`, Model Zoo HEF checks, audio stack |
| `code-inventory.txt` | Speech call sites, panel handling, ACK format, 18 Oct tools and evidence, the tracker's 12 cases |
| `model-zoo-docs-v2.17/` | Copies of the Model Zoo table and two docs pages, with `SOURCE_URL.txt` and `SHA256SUMS` |
| `model-zoo-v2.17-yolov8.csv` | The five YOLOv8 rows of that table, cells copied as written (`extract_zoo_rows.py`) |
| `pytest-baseline.log` | Unit tests before any session changes code (not committed: `*.log` is ignored) |

The five Model Zoo HEFs (147 MB) are in `results/panel-revisions/downloads/model-zoo-v2.17.0/hailo8/`,
with their HTTP headers and `parse-hef` output. The repo's existing `downloads/` ignore rule keeps them
out of git, so `.gitignore` was not changed.

---

## 1. Inventory

### 1.1 Device and software

| Item | Value |
|---|---|
| Board | Raspberry Pi 5 Model B Rev 1.1, 8,062 MB RAM |
| OS, kernel | Debian 13 (trixie) 13.6; Linux 6.12.62+rpt-rpi-2712 aarch64 |
| HailoRT | `hailortcli` 4.23.0; firmware 4.23.0; `hailort`, `hailort-pcie-driver`, `python3-hailort` 4.23.0; `hailo-tappas-core` 5.1.0 |
| Accelerator | Hailo-8 (`fw-control identify`: Board Name Hailo-8, HAILO8), at PCIe 0001:01:00.0 |
| hailort_service | `hailort.service` active; `/etc/default/hailort_service` has `HAILO_MONITOR=0` |
| Python | 3.13.5 (system and venv) |
| Project venv | `/home/sv-rpi5/Projects/Second-Vision/second-vision-repo/my_hailo_env` (the worktree's `my_hailo_env` is a symlink to it), system site-packages on; `hailo-apps` 26.3.0 |
| Test interpreter | Poetry env `~/.cache/pypoetry/virtualenvs/second-vision-repo-wwT_Rtha-py3.13` (pytest 9.1.1). `my_hailo_env` now also has pytest 9.1.1 |
| Free disk | 74 GB of 117 GB on `/` |
| At capture | 57.1 °C (`vcgencmd`), throttle flags `0x0`, fan 2,936 rpm |

### 1.2 Boot service

| Item | Value |
|---|---|
| Name | `second-vision.service`: enabled, active (PID 1167) |
| Unit file | `/etc/systemd/system/second-vision.service` |
| ExecStart | `/home/sv-rpi5/Projects/Second-Vision/second-vision-repo/scripts/sv-main.sh` |
| WorkingDirectory | `/home/sv-rpi5/Projects/Second-Vision/second-vision-repo` |
| Environment | `SV_SERVICE=1`, `XDG_RUNTIME_DIR=/run/user/1000`, `PYTHONUNBUFFERED=1` |
| Checkout it runs | The **base repo**, branch `deploy/v2` at `80a5c92`, clean working tree (no changes, no untracked files) |
| Arguments (from `sv-main.sh`) | `--input /dev/video0 --width 640 --height 480 --serial-port /dev/serial0 --config-port /dev/ttyAMA3`, plus `--use-frame` only when a display is set |

Kenth's worktree is on the same commit. Its only uncommitted changes are four documents and a comment
in `pipeline/app.py`. Both checkouts set `DET_MODEL_VERSION = "hef_v3"`.

### 1.3 Deployed models

| Model | Path | sha256 | Checked |
|---|---|---|---|
| Detection (deployed) | `models/hef_v3/hef_deploy/best.hef` (9,745,854 B) | `648ddb167080c93f4651a015f3c5688eae9f63585d51dc58350ef35298a79d90` | `parse-hef`: HAILO8, **13 classes**, YOLOV8 NMS op, 640×640 input |
| Depth | `/usr/local/hailo/resources/models/hailo8/scdepthv3.hef` (13,595,791 B) | `5b88f3b8975bf5869d86f2c2d1c21cfbc24e548eaf14d945ec08531bbe445cae` | `parse-hef`: HAILO8, input 256×320×3, output 256×320×1 UINT16 |
| Detection, 14-class (18 Oct runs) | `models/hef_v2/hef_deploy/best.hef` (9,681,398 B) | `c6e4ec9830cb629293c09410bd39247cd32b6b1295d5adb5f8e4d2ab75bf2f78` | `parse-hef`: 14 classes |

- The running service loaded exactly these two files. Its log for this boot shows both paths and the
  `labels.json` beside the detection HEF.
- **13 classes, confirmed three ways:** `parse-hef` says 13, `nms_config.json` says `"classes": 13`, and
  `labels.json` lists `unlabeled` plus 13 names with no Pole. The app's class map
  (`callbacks.DETECTION_CLASS_MAP`) still has a `"Pole"` key. That key is unused and harmless.
- The 18 Oct and battery runs record their model as `models/hef_deploy/best.hef`, 9,681,398 bytes,
  dated Oct 11. The HEF committed at that path in `9dc392a` (2026-10-11) has the same sha256 as
  today's `hef_v2` file. So those runs used the 14-class model, as the brief says.
- The worktree's copy of the deployed HEF is identical to the base repo's (same sha256).

### 1.4 The 18 Oct real-time run and the battery run

Scripts (all in git, `docs/testing/tools/`): `perf_run.py` starts the app the way the service does and
stops it with Ctrl+C. It runs `perf_sample.py`, which samples every 10 s and parses the app's log
lines. `perf_plot.py` draws the chart, and `perf_npu.py` samples `hailortcli monitor`.

Evidence is in `docs/testing/reports/T-15/` and `docs/handoffs/performance-tests/`:

- **18 Oct, 20 min:** `perf-both-20min.{csv,txt,png}`, `-system.txt`, screenshots. The app's console
  log `perf-both-20min-app.log` (402 KB) is on disk but not in git (`*.log`).
- **19 Oct, NPU monitor, 20 min:** `perf-npu-20min.*`.
- **Battery run-down:** `perf-rundown.*` and `BATTERY-RESULTS.md`.

Check: re-running `perf_sample.py --from-csv` and `perf_plot.py` on copies of the 18 Oct data reproduced
every figure in the brief's "18 Oct" column. That covers FPS windows (median 30.0, p5 28.5, p1 20.2,
min 18.9), delay (104 / 127 / 132 / 147 ms), app CPU 73.3 %, busiest core 28.3 / 38.4 %, memory
195 → 199 MB (+2.2 %), and temperature 58.4 / 65.5 °C. The tools work unchanged for Session B.

How the 18 Oct run was set up (from `perf-both-20min-system.txt`):

- It started from **this worktree**, not the base repo.
- **The glasses board was a stand-in on a pseudo-terminal that acknowledged every packet**, and
  **there was no control panel**, so the mode stayed "both". See the question on B3 in section 7.
- The code was the development tree with uncommitted files. `cb0b20e` (2026-10-16) was the last
  commit before the run, which agrees with the brief.
- The scene was static: the camera faced something very close and no objects were detected
  (`docs/handoffs/performance-tests/HANDOFF.md` §6).

### 1.5 Object-detection unit tests and the tracker format

**Tracker format.** The workbook (`tests/Group2-SV_Test-Tracker.xlsx`, sha256 `4735414e…`, the 2026-10-06
copy) is read through `docs/testing/uat-snapshot.yaml`, which was extracted on 2026-10-11. Each case has
columns ID, Steps, Input, Expected, Actual, Status, Comments, plus module, scenario and date tested.

**The 12 cases** are module M01: TC-001-001..003, TC-002-001..005 and TC-003-001..004.
`code-inventory.txt` lists them in full. Their status as recorded on 2026-08-03 is 8 Passed, 1 Failed
(TC-002-005, empty scene), and 3 blank.

What exists to run them: **nothing.**

- The steps call `./scripts/setup_env.sh` and `./tests/obj_det_unit/{class_coverage,det_behavior,physical_env_robustness}.py`.
  None of these exist on any branch or anywhere under `/home/sv-rpi5`.
- No test images are in the repo or elsewhere on the Pi.
- The repo's own hardware-free tests cover pieces of the logic only: 13 tests in
  `test_detection_classes.py` and 26 in `test_priority.py`.
- The Pi does hold a labelled test split, `~/v3p_eval/test`, with 6,158 image files and their labels.
  These are not the tracker's images, but they could supply them. See section 7.

### 1.6 Speech worker and its call sites

- **Engine.** `workers/tts_worker.py` starts an **`espeak-ng` subprocess**
  (`espeak-ng -s 160 -a <amplitude> <text>`). It interrupts by `proc.terminate()`, and the volume knob
  sets `-a`, from 10 to 100. pyttsx3 is only a fallback for when `espeak-ng` is missing, and **pyttsx3 is
  not installed** in the venv. espeak-ng 1.52.0 has no Filipino voice.
- **Behaviour to keep in Session C.** Speech goes out one utterance at a time. While speaking, the
  worker polls the mailbox every 50 ms for a preempting item. After each utterance it leaves a
  0.5 s gap (`MIN_UTTERANCE_GAP`), which an urgent item skips. A head turn mutes speech for 1.0 s and
  clears the mailbox, but keeps a pending system alert. A system alert being spoken is not cut by an
  object warning (D-16).
- **Who queues speech:**
  - `pipeline/callbacks.py:659` queues detections. The phrases are `"<label> <zone>"`,
    `"<label> leaving to <zone>"`, `"<label> still <zone>"` and `"multiple …"`. The labels come from
    `DETECTION_CLASS_MAP`, where Bicycle and Tricycle are both spoken as "vehicle".
  - `workers/config_reader.py:109` queues mode changes as `"<mode> mode"`.
  - The alert scheduler, through `raise_alert`, carries these sentences:
    - camera lost and back (`pipeline/app.py`);
    - camera view dark, bright and back (`core/camera_view.py`);
    - vibration lost and back, controller board lost and back, earphones back, and "Everything is
      connected" (`core/connections.py`);
    - earphones lost, which is raised silently (`speak=False`).
  - Mock-mode generators, which are for testing only.
- **Sentences the brief lists that the code does not have:** there is no battery, startup or
  "shutting down" sentence on any branch. "Device shutting down." is task T-07, not built. "Battery low"
  appears only as an example in a docstring of `core/alerts.py`.

### 1.7 Control panel

- **The Pi side** (`workers/config_reader.py`) acts on `S:` settings, `M:` modes, `B:alive` and `V:`
  lines. **Any other `B:<name>` line is only logged** (`[CONFIG] Button: <name>`), so the status button
  never had a function on the Pi.
- **The flashed firmware has no status button.** The running device receives `S:tts_volume`, which only
  the volume-knob firmware sends. That firmware is commit `73eab9b` (2026-10-07), which exists only on
  `feat/depth-callback`. Its source says: "There is no STATUS button any more: the volume knob took its
  place on the panel (2026-10), and GPIO22 is free". Its `PROTOCOL.md` says `B:status` is "no longer
  sent". The journal for this boot has 0 `Button:` lines.
- **The older firmware in this worktree does have a button (GPIO22), but sends `B:status` only on press.**
  It sends nothing on release, so the Pi could never time a press.
- **The firmware source has drifted.** This worktree and `deploy/v2` carry the pre-knob firmware source,
  while the board runs `73eab9b`.

### 1.8 Glasses link (for the Session B haptic timings)

The board answers every valid packet with `0xAA 0xFF <message type>`. **There is no sequence number.**
`serial_worker.py` matches only the 2-byte prefix and treats 2.5 s without an ACK as the board being
gone. A motor packet can therefore only be paired with the next ACK of the same type that arrives
after its write.

---

## 2. Model Zoo HEFs for hailo8

**Release.** The installed `hailo_apps` pins Hailo-8 to Model Zoo **v2.17.0** for HailoRT 4.23. See
`hailo_apps/config/config.yaml:62` ("H8/H8L: Always v2.17.0") and `defines.py:108`
(`VALID_H8_MODEL_ZOO_VERSION = ["v2.17.0"]`). This matches the brief.

Source: `https://hailo-model-zoo.s3.eu-west-2.amazonaws.com/ModelZoo/Compiled/v2.17.0/hailo8/<model>.hef`,
downloaded 2026-11-09 ~09:44–09:48 UTC.

| Model | Bytes | sha256 | MD5 = S3 ETag | `parse-hef` |
|---|---|---|---|---|
| yolov8n | 5,100,979 | `e893b0f9dcae366fe1bc9ebce25e32ad889acf2bc58cfe1f73a572f78f7ec055` | yes | HAILO8, single context, 80 classes |
| yolov8s | 11,283,950 | `1ac4c540b422a0df437904534eb6051d7b4bdff7930dfc06bd299d40c4a07a70` | yes | HAILO8, single context, 80 classes |
| yolov8m | 31,157,775 | `9481dbff7798d90302e170958943578d444b61c9833c67fef36075fe129efe7f` | yes | HAILO8, **3 contexts**, 80 classes |
| yolov8l | 44,001,728 | `502cdbf514790c78288db621ce745b103bb429968b224138292cdcfc20494dec` | yes | HAILO8, **4 contexts**, 80 classes |
| yolov8x | 55,096,837 | `73eadabc9454270f5cbeaad2ba232a1e65f585c9e7e96495600a603c65d7ce83` | yes | HAILO8, **5 contexts**, 80 classes |

- All five parse as HAILO8 builds. None was rejected, so no other release was needed.
- `parse-hef` only reads the file. Whether each one loads and runs on this HailoRT is shown by the
  Session A runs.
- The copies of yolov8n/s/m that hailo-apps had already installed in
  `/usr/local/hailo/resources/models/hailo8/` are byte-identical to these.
- All five are COCO 80-class builds with the YOLOv8 NMS op, which runs on the host CPU (as in the
  deployed model). The deployed model has 13 classes.

## 3. Model Zoo accuracy table

Saved copy: `model-zoo-docs-v2.17/HAILO8_object_detection.rst`, from tag **v2.17** (commit
`1bde1102a7706e43688c863330a93acbc0f4db90`) of `github.com/hailo-ai/hailo_model_zoo`:
`https://raw.githubusercontent.com/hailo-ai/hailo_model_zoo/1bde1102a7706e43688c863330a93acbc0f4db90/docs/public_models/HAILO8/HAILO8_object_detection.rst`.

The file's own YOLOv8 links point at exactly the five `Compiled/v2.17.0/hailo8/yolov8*.hef` files above.
`BENCHMARKS.rst` and `PUBLIC_MODELS.rst` from the same commit are saved beside it.

The YOLOv8 rows, copied as written (`model-zoo-v2.17-yolov8.csv`):

| Network | float | Hardware Degradation | FPS (batch 1) | FPS (batch 8) | Input | Params (M) | OPS (G) |
|---|---|---|---|---|---|---|---|
| yolov8n | 37.0 | 0.6 | 1036 | 1036 | 640x640x3 | 3.2 | 8.74 |
| yolov8s | 44.6 | 0.8 | 491 | 491 | 640x640x3 | 11.2 | 28.6 |
| yolov8m | 49.9 | 0.7 | 67 | 141 | 640x640x3 | 25.9 | 78.93 |
| yolov8l | 52.4 | 0.6 | 36 | 64 | 640x640x3 | 43.7 | 165.3 |
| yolov8x | 53.5 | 0.6 | 18 | 29 | 640x640x3 | 68.2 | 258 |

Two things in this file differ from the brief's matrix:

- **There is no "hardware mAP" column.** The file gives `float` and `Hardware Degradation`. Hardware
  mAP would have to be computed as float − degradation. See section 7.
- **The file never names the metric.** The column is headed "float", and neither of the two other
  pages saved from that commit says which COCO mAP it is. The file does state that all models were
  compiled with Dataflow Compiler v3.33.0, and that "Benchmark and TAPPAS networks run in performance
  mode". yolov8m is marked as both.

## 4. System packages

| Package | Version | Action |
|---|---|---|
| `ffmpeg` | 7.1.5-0+deb13u1+rpt1 | already present. Has `loudnorm`, `silenceremove`, `aresample`, and decoders for aac, mp3, opus, vorbis, amr, flac and alac (phone formats) |
| `alsa-utils` | 1.2.14-1+rpt1 | already present |
| `espeak-ng` | 1.52.0+dfsg-5 | already present; no Filipino voice |
| `sox` | 14.4.2+git20190427-5+b3 | **installed now**: `sudo apt-get install -y --no-upgrade sox`. That added 4 packages (`sox`, `libsox3`, `libsox-fmt-base`, `libsox-fmt-alsa`), upgraded none and removed none; a dry run checked this first |

sox's base formats do not read m4a, so the clip importer will decode with ffmpeg and use sox only for
WAV work. `libsox-fmt-all` is not needed.

## 5. Session C playback: the choice

**Use `pw-play` (PipeWire), started and stopped as a subprocess exactly like `espeak-ng`.**

- **Why not `aplay`:** it goes through ALSA, and here ALSA's `default` device is the HDMI port.
  - There is no `/etc/asound.conf` or `~/.asoundrc`, and `pipewire-alsa` is not installed.
  - `aplay` aimed at the earphone card would bypass PipeWire. It would then compete with PipeWire, and
    with espeak-ng, for the same device.
- **Why `pw-play`:** espeak-ng reaches the earphones through PipeWire's pulse server. The default
  PipeWire sink is "USB Audio Analog Stereo", the SPACETOUCH earphones (card 2).
  - `pw-play` follows the same path.
  - Its `--volume` option (0–1.0) can follow the volume knob.
  - `terminate()` stops it the same way the worker stops espeak-ng today.
  - It comes from `pipewire-bin` 1.4.2, which is already installed. No new package and no Python
    library are needed.
- **Clip format:** 16-bit PCM mono WAV at **48,000 Hz**. PipeWire's graph clock is fixed at 48 kHz
  (`clock.allowed-rates [48000]`), and the earphones accept 44.1 or 48 kHz.
- **Not yet checked by ear.** No sound was played in Step 1, because the device was in service and may
  be in use. Session C's first test does this.

## 6. Analysis dependencies

Nothing needed installing. The 18 Oct tools need `psutil` 7.0.0, `cv2` 4.10.0 and `numpy` 2.2.4, and
the battery readers need `smbus2` 0.4.3 and `gpiod` 2.2.0. All are present in `my_hailo_env`, through
system site-packages. `grim` and `vcgencmd` are present, and `sudo -n vcgencmd` works. The re-run in
§1.4 confirms the tools work.

**Unit-test baseline** (Poetry interpreter, `python -m pytest tests/ -q -rs`): see `pytest-baseline.log`.
Result: **1078 passed, 217 skipped, exit 0** (302 s). The skips:

- 103 device tests, which are opt-in (`--run-device`);
- 95 GStreamer tests, because the Poetry env has no `gi`;
- 13 long real-time runs, which are opt-in;
- 4 tests that need `pytest-timeout`;
- 1 test that needs `psutil`;
- 1 opt-in cadence test.

None failed.

---

## 7. Deviations, gaps and decisions for Kenth

These come before the sessions start. Each one says what the brief assumed and what the device shows.

1. **Git writes.** The brief asks for a branch per session and commits. A standing rule from
   2026-10-04 says agents must not run git write commands in this repo, because the user commits.
   Nothing has been committed or branched.
   - Kenth decides whether the brief overrides that rule for these sessions.
   - If so, commits use `KRMeeag` and carry no Claude co-author line (also a standing rule).
2. **Session C trigger: blocked, and the brief says to stop and ask.**
   - The panel's status button no longer exists (§1.7).
   - Even the older firmware sent only a press, never a release, so a ≥3 s long press needs new panel
     firmware and a button wired back to GPIO22.
   - "Exit on a status-button press" has the same problem.
   - The tutorial script cannot describe a status button either.
   - Kenth chooses: reflash the panel with a button, use another trigger, or drop the button from the
     tutorial.
3. **B1 cannot run as written.**
   - The tracker's images and scripts do not exist (§1.5). The team needs to supply the images, or
     approve choosing them from `~/v3p_eval/test`.
   - **The brief and the repo disagree** on whether these cases ever ran on the 14-class model. The
     tracker dates every result 2026-08-03, before the 14-class model (2026-10-11). The repo's audit
     (`docs/testing/WORKBOOK-REVISIONS.md`) marks them stale.
   - **The Pi's tracker copy (2026-10-06) differs from the brief's summary:**
     - TC-001-003's expected result is leftover template text.
     - TC-002-004 expects "must not flag any classes within this zone", whereas the brief says an
       object in the band "keeps its previous zone".
     - TC-001-002 names one confusion pair, not three.
   - The brief says to follow the tracker where the two differ. Kenth needs to say whether the team's
     current Google Sheet has been revised, and send the current copy if so.
   - Bicycle and Tricycle are both *spoken* as "vehicle", so the confusion-pair case can only be
     judged on the model's own label.
4. **B3 cannot match 18 Oct exactly and also measure the real ACK.**
   - The 18 Oct run used a stand-in glasses board on a pseudo-terminal and no panel (§1.4). An ACK from
     the stand-in measures the Pi talking to itself.
   - The real ACK needs the real boards (`perf_run.py --real-boards`), with both rockers on.
   - Kenth chooses: real boards, the stand-in, or one run of each.
5. **"Speech start" for B2.**
   - The device does not use pyttsx3, so there is no `started-utterance` event.
   - The nearest equivalent the code has is the moment the `espeak-ng` process is started. That is
     not the moment sound reaches the earphones, and the report will say so.
6. **Sentences that do not exist** (battery %, startup, shutdown).
   - The manifest can only list what the code says (§1.6).
   - Kenth decides whether new sentences are in scope. They would be new behaviour, not translations.
7. **The Model Zoo table gives degradation, not hardware mAP, and never names the metric** (§3).
   - Proposal: report float and degradation as written, and add hardware = float − degradation,
     labelled as computed.
8. **Playback through `pw-play`, not `aplay`** (§5). This departs from the brief's stated preference,
   for the reason given there.
9. **Session A setup.**
   - `hailortcli benchmark` and `run2` both accept `--multi-process-service`, and `run2` has
     `--measure-latency` and `--measure-overall-latency`.
   - Plan: run through `hailort_service`, as the app does, so only `second-vision.service` needs
     stopping. Running without the service would also mean stopping `hailort.service`, which the
     brief does not cover.
10. **The device looks in use.** The panel's vibration knob changed at 17:27 and 17:29 today, about
    20 minutes before Step 1. Session A stops the service, so confirm first that no teammate is using
    the device.

## 8. Kenth's answers (2026-11-09, after this report)

| # | Question | Answer |
|---|---|---|
| 1 | Git writes | Allowed for these sessions: branches and commits, with **no Claude co-author line** |
| 2 | Tutorial trigger | **Drop the status button** from the tutorial (trigger, exit and content). There is no status button: the Pi never acted on one and the flashed panel has none. The firmware source on this branch and on `deploy/v2` still has the old button; only `73eab9b` (`feat/depth-callback`) matches the board. What starts the tutorial is still open |
| 3 | B1 images | Kenth asked what the "tracker" is and what is needed. Open |
| 4 | B3 boards | **One run of each**: the stand-in glasses (as on 18 Oct) and the real boards |
| 5 | New sentences (battery, startup, shutdown) | **No.** The manifest lists only what the code says |
| 6 | Model Zoo accuracy | **Yes.** Report float and degradation as written, plus hardware = float − degradation, labelled as computed |
