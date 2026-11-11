# Session A: YOLOv8 size comparison on the device

**Panel item (Sir Franze #3):** on this Raspberry Pi 5 + Hailo-8, running alongside SC-DepthV3, is YOLOv8s
the right YOLOv8 size?

Run on 2026-11-09 from 20:33 to 21:26 (Pi clock, UTC+8). It used the same device and software as Step 1:
HailoRT 4.23.0, firmware 4.23.0, Debian 13 and Linux 6.12.62+rpt-rpi-2712, Raspberry Pi 5 8 GB with the
active cooler. The models and their hashes are in `../step1/RESULTS.md` §1.3 and §2.

**What these numbers are.** `hailortcli` measures the accelerator alone, fed with random input. It does
not include the camera, GStreamer or Python. Every FPS figure below is therefore an **upper bound for the
app**, not what the app achieves.

## Method

1. **Service stopped.** `second-vision.service` was stopped at 20:28:47. Nothing held `/dev/hailo0`
   (`sudo -n fuser -v`), and no copy of the app was running. `hailort.service` stayed up and idle.
2. **Each model alone:** `hailortcli benchmark -t 15 <hef>`. This takes 15 s each of HW-only FPS,
   streaming FPS and HW latency, the method in Model Zoo `BENCHMARKS.rst` (saved in Step 1).
3. **Each model alongside depth:** `hailortcli run2 -t 60 --multi-process-service -j <json> set-net <hef>
   set-net scdepthv3.hef`. It gives FPS per network.
4. **Order and conditions.** Each measurement ran 3 times. The models were interleaved within each
   repetition, with 30 s idle after every run. SoC temperature (`thermal_zone0` and `vcgencmd
   measure_temp`), `vcgencmd get_throttled` and fan speed were logged before and after every run. A run
   was to be discarded and repeated if it showed current throttling or a new since-boot flag; **none
   did**.
5. **Depth-alone reference.** `scdepthv3` alone was benchmarked once per repetition, as a reference for
   the "depth FPS with det" column. **This is an addition to the brief.**

Commands: `run_session_a.sh` (the runner) and `parse_session_a.py` (raw outputs → `matrix.csv`). The
raw output of every run is in `raw/`: one `.txt` per run with the before/after readings, and one `.json`
per `run2`. `runs.csv` has one row per attempt. `run-console.txt` is the runner's console, and
`parse-output.txt` is the parser's.

Where the `hailortcli` usage was checked (AGENTS.md Rule 6): the hailo-apps workbench docs do not cover
`benchmark` or `run2`. I used the Model Zoo `BENCHMARKS.rst` at v2.17 (`../step1/model-zoo-docs-v2.17/`)
and the 4.23 CLI's own help (`cli-help/`).

### Deviations from the brief

- **`benchmark` opens the chip directly, not through `hailort_service`.** Its HW-only mode fails through
  the service: "ConfiguredNetworkGroup::get_input_streams_by_network function is not supported when using
  multi-process service", status `HAILO_INVALID_OPERATION`. That was seen in a probe whose console output
  was not saved. The direct run worked with the service running but idle.
- **`run2` goes through `hailort_service`**, the way the app reaches the chip. A 5 s probe of yolov8n plus
  depth gave nearly the same FPS either way: 133.8 / 134.4 through the service, 134.7 / 135.9 direct
  (`probes/`).
- **No latency figure alongside depth.** `run2` rejects `--measure-latency` and
  `--measure-overall-latency` with two networks: "When latency measurement is enabled, only one model is
  allowed". The HW latency below is for each model alone.
- **`run2` does not run the networks the way the app does.** It runs both networks as fast as possible,
  with HailoRT's default batch size and scheduler settings. The app instead uses:
  - detection batch 2, and depth batch 1 at scheduler priority 31;
  - a camera capped at 30 FPS.

  So "with depth" shows the chip's shared capacity, not the app's split. Under these defaults the two
  networks came out at almost the same FPS in every run.
- A 1-repetition quick check (3 s and 5 s timings) ran first to test the runner. It is kept in
  `raw-quick/`, `runs-quick.csv` and `matrix-quick.csv`, and **is not used** in the results.

## Results

Mean of 3 runs, with the range in brackets. Columns 2–6 are copied from the Model Zoo v2.17 file
(`../step1/model-zoo-v2.17-yolov8.csv`). "COCO mAP hardware" is **computed** as float − Hardware
Degradation, because the file gives no hardware mAP column and does not name which COCO mAP its "float"
column is. The other columns are measured here. The same table, with min / max / n per measured column,
is `matrix.csv`.

| Model | Params (M) | OPS (G) | COCO mAP float | COCO mAP hardware (computed) | Zoo FPS (batch 1) | FPS alone, HW-only | FPS alone, streaming | HW latency (ms) | Det FPS with depth | Depth FPS with det |
|---|---|---|---|---|---|---|---|---|---|---|
| yolov8n | 3.2 | 8.74 | 37.0 | 36.4 | 1036 | 431.6 (431.6–431.6) | 431.6 (431.5–431.6) | 3.35 (3.35–3.35) | 134.3 (134.2–134.3) | 134.4 (134.2–134.5) |
| yolov8s | 11.2 | 28.6 | 44.6 | 43.8 | 491 | 491.3 (491.3–491.3) | 491.2 (491.2–491.3) | 5.83 (5.83–5.83) | 121.7 (121.7–121.8) | 121.8 (121.8–121.9) |
| yolov8m | 25.9 | 78.93 | 49.9 | 49.2 | 67 | 31.5 (31.5–31.5) | 31.5 (31.5–31.5) | 25.84 (25.83–25.84) | 27.6 (27.6–27.6) | 27.7 (27.6–27.7) |
| yolov8l | 43.7 | 165.3 | 52.4 | 51.8 | 36 | 19.5 (19.5–19.5) | 19.5 (19.5–19.5) | 47.15 (47.14–47.17) | 17.9 (17.9–17.9) | 18.0 (18.0–18.0) |
| yolov8x | 68.2 | 258 | 53.5 | 52.9 | 18 | 13.4 (13.4–13.4) | 13.4 (13.4–13.4) | 68.70 (68.70–68.70) | 12.8 (12.7–12.8) | 12.8 (12.7–12.8) |
| **deployed 13-class** | — | — | — | — | — | 185.7 (185.7–185.7) | 185.7 (185.7–185.7) | 11.61 (11.61–11.61) | 89.1 (89.0–89.1) | 89.1 (89.0–89.1) |

**Depth alone** (`scdepthv3`, reference): 468.4 FPS HW-only (468.3–468.5), 468.2 streaming (468.0–468.3),
HW latency 4.77 ms (4.77–4.77).

### Which models keep both networks at the frame-rate floors, run together

The same models pass whether each model is judged on its lowest run or on its mean:

| Floor | Models with both networks at or above it in every run |
|---|---|
| **≥ 30 FPS** (the camera cap) | **yolov8n, yolov8s, the deployed 13-class model** |
| **≥ 25 FPS** (the thesis's real-time floor) | **yolov8n, yolov8s, yolov8m, the deployed 13-class model** |
| Below 25 FPS | yolov8l (17.9 / 18.0) and yolov8x (12.8 / 12.8) |

yolov8m sits between the two floors: 27.6 / 27.7 FPS.

These are accelerator-only upper bounds, as stated at the top. A model that passes here may still fall
short once the camera, GStreamer and Python are added. A model that fails here cannot do better in the app.

### Measured facts that bear on reading the table (not conclusions)

- **The chip's PCIe link is Gen3 ×1**: `current_link_speed` 8.0 GT/s, `current_link_width` 1, from
  `/sys/bus/pci/devices/0001:01:00.0/`; the maximum width is 4. The Model Zoo file does not say on what
  host its FPS was measured.
  - yolov8s measured the Zoo's figure: 491.3 vs 491.
  - yolov8n measured 431.6 vs 1036.
  - m, l and x measured below the Zoo figure too: 31.5 vs 67, 19.5 vs 36, 13.4 vs 18.
- **The deployed 13-class model is a different build from the Zoo's yolov8s**, and it measured slower:

  | Measure | Deployed 13-class | Zoo yolov8s |
  |---|---|---|
  | FPS alone | 185.7 | 491.3 |
  | HW latency | 11.61 ms | 5.83 ms |
  | Det FPS with depth | 89.1 | 121.7 |

  - Both files are single-context Hailo-8 builds with a 640×640 input.
  - They differ in class count (13 vs 80) and file size (9.7 MB vs 11.3 MB).
  - The Zoo file says its models were compiled with Dataflow Compiler v3.33.0. The deployed build's
    compiler version and settings are not recorded on the Pi.
  - **So this session does not establish why the deployed build is slower.** Comparing the Zoo yolov8s
    row to the deployed row compares builds as well as model sizes.
- **yolov8m, l and x are multi-context builds** (3, 4 and 5 contexts; `../step1/inventory.txt`). n, s and
  the deployed model are single-context.

### Run conditions

- **Runs used:** 39 attempts (3 depth references + 18 benchmarks + 18 with-depth), all used, with no
  repeats. Every run exited 0.
- **Temperature:** SoC 54.5 to 62.2 °C around the runs (`thermal_zone0`, before and after each run).
- **Throttling:** flags `0x0` before and after every run, so no under-voltage, frequency cap or
  throttling, and none since boot.
- **Run-to-run spread:** the largest gap between the highest and lowest of the three runs, over every
  model and column, was 0.33 FPS (scdepthv3 alone, streaming) and 0.035 ms (yolov8l, HW latency).

## What could not be measured

- **Latency with depth running alongside**: `run2` allows latency measurement only with one network.
- **Power**: `benchmark` measures power only "on supported platforms", and its summary printed none here.
  Step 1 already recorded that `measure-power` is unsupported on this board.
- **Any figure inside the app** (camera, GStreamer, Python). That belongs to Session B's 20-minute run,
  which uses the deployed model only.

## Service restored

- **Stop and start:** stopped 20:28:47; started again 21:26:55 with `sudo systemctl start second-vision`.
  It is **enabled and active** (new main PID 76622).
- **Startup log:** the app loaded `scdepthv3.hef` and `models/hef_v3/hef_deploy/best.hef` with its
  `labels.json`, which are the same files as before. The glasses board, earphones and control panel all
  reported connected. The serial link test passed. The pipeline came up in mode "both" and switched to
  "none" when the panel reported both rockers off.
- **Observation, outside this session's scope.** The camera dropped off USB and re-enumerated:
  - at **21:26:55**, the same second the service started. The app reported "Camera cannot be reached",
    then "Camera has been reconnected" 3 s later;
  - also at **17:17:16**, before Session A, while the service was running.

  In both cases the kernel logged `usb 1-1: USB disconnect` and then the Arducam again. The throttle
  flags show no under-voltage since boot, and the USB test tool was not running. The cause is not known.
  This morning's boot (05:00) had no such drop.

---

## Draft decision entry, for Kenth to review

In the style of `documentation/DECISIONS.md`; neither `docs/DECISIONS.md` nor a template exists. **Not
added to that file.** The wording states the measurements; the decision itself is the team's.

> ## D43 (draft): YOLOv8s kept as the detector size on RPi 5 + Hailo-8
>
> *Proposed after the thesis panel's question (Sir Franze #3), from on-device measurements on
> 2026-11-09 (`results/panel-revisions/session-a/`).*
>
> **What**: the detector stays a YOLOv8s.
>
> **Why**:
> - With SC-DepthV3 running beside it on the Hailo-8, the Model Zoo builds that kept both networks at or
>   above 30 FPS (the camera cap) in every run were only yolov8n (134.3 / 134.4 FPS) and yolov8s
>   (121.7 / 121.8). That was measured with `hailortcli run2`, accelerator only, so these are upper bounds.
> - yolov8m reached 27.6 / 27.7: above the 25 FPS real-time floor, below the cap. yolov8l and yolov8x
>   fell below 25.
> - Between the two that pass, the Model Zoo lists yolov8s at 44.6 COCO mAP (float) against 37.0 for
>   yolov8n.
> - The deployed 13-class build kept both networks at 89.1 FPS.
>
> **Cost**:
> - These are accelerator-only figures: the app's camera, GStreamer and Python cost is measured
>   separately (Session B).
> - The deployed build ran slower than the Model Zoo's yolov8s (185.7 vs 491.3 FPS alone), for a reason
>   not established here.
> - The COCO figures are the Model Zoo's, for its 80-class builds, not for the team's 13-class model.
