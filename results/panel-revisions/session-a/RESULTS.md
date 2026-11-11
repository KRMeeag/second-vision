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
- **The data moved over PCIe per frame does not explain the FPS order** (`hef-streams.txt`, from
  `parse-hef --parse-streams`):
  - All five Model Zoo files move exactly the same data per frame: 1,228,800 B in and 1,221,120 B out,
    yet yolov8s (491.3 FPS) ran faster than yolov8n (431.6).
  - The deployed file moves less (652,960 B out, since it has 13 classes) and ran slower than both.
  - yolov8n is faster than yolov8s **per frame** (HW latency 3.35 vs 5.83 ms). It is only the sustained
    rate where s leads.
  - Why the n file sustains less than the s file on this Pi is not established. In the Model Zoo's own
    figures, n is faster (1036 vs 491).
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
> - Inside the app, with depth on, for 30 minutes (Part 2, 2026-11-11), yolov8m against the deployed
>   model:
>   - the depth branch (the haptics) below 29.5 FPS in 86 % of 15-frame windows, against 30 %;
>   - median detection delay 176 against 92 ms;
>   - the Hailo chip about 10 °C hotter (peak 76.2 against 64.9 °C).
>   Neither throttled: the chip's first throttling step is 104 °C, and the Pi's flags stayed 0x0.
>
> **Cost**:
> - These are accelerator-only figures: the app's camera, GStreamer and Python cost is measured
>   separately (Session B).
> - The deployed build ran slower than the Model Zoo's yolov8s (185.7 vs 491.3 FPS alone), for a reason
>   not established here.
> - The COCO figures are the Model Zoo's, for its 80-class builds, not for the team's 13-class model.

---

# Part 2: each model inside the app, with depth on (2026-11-11)

**Why this part was added.** Kenth asked for it on 2026-11-11. His reason for not choosing yolov8m was a
concern that, over long use, it could **thermally throttle** the device and **jitter in the real
pipeline**. Part 1 measured the chip alone. This part runs every model in the real app, the way the boot
service starts it.

## Method

- **App.** `scripts/sv-main.sh` started by `docs/testing/tools/perf_run.py`: headless, as on 18 Oct, with
  the stand-in glasses board on a pseudo-terminal that acknowledges every packet. There was no control
  panel, so the mode stayed "both".
- **Model swap.** `SV_RUN_SH=app-runs/run_with_model.sh` adds `--det-hef-path <model>` to the service's
  own arguments, with no change to the app's code. The model each run actually loaded is the
  "Using HEF from path" line in its app log (`app-runs/*-analysis.txt`, line 2).
- **New detection measurement** (`SV_LATENCY_LOG=1`, off by default). The app's existing log line times
  only the depth branch. Its "det FPS" is an average since start, so dips on the detection side were
  invisible.
  - With the flag set, `pipeline/callbacks.py` also prints `[DET] Frame N | lat X ms` every 15
    detection frames. X is the frame's age from camera capture to the detection callback, on the same
    clock as the depth figure.
  - Test: `tests/test_det_latency_log.py`, 6 tests, all passing. Two of them fail with the call removed.
- **Heat.**
  - The Pi is sampled every 10 s by `perf_sample.py`: temperature, clock and throttle flags.
  - The Hailo chip is sampled every 10 s by `app-runs/hailo_temp_sampler.py`, a firmware-control read of
    both on-die sensors.
  - The chip's own throttling thresholds were read once, after the runs (`app-runs/hailo-health-info.txt`).
- **Order and cool-down.**
  - Order: the deployed 13-class model for 30 min, then yolov8m for 30 min, then yolov8n, s, l and x for
    5 min each.
  - Before each run: at least 180 s idle, then until the Pi and the chip were within 2 °C of their idle
    readings (52.4 °C and 51.8 °C), for at most 900 s (`app-runs/run-console.txt`).
- **Analysis.** `app-runs/analyse_app_run.py` uses `perf_sample.py`'s own window method for depth, the
  18 Oct method, and the same method for detection. The first 60 s are left out. Figures below **exclude
  windows that overlap a camera drop**; the same figures with every window are in each `*-analysis.txt`.
- **Scene.** A lit room, with the camera pointed at Kenth, as he described it.
  - **No image of the scene was kept**, at his request for privacy, and `perf_run.py` now has
    `--no-screenshots`.
  - Whether he stayed in view throughout is not recorded. Room temperature was not measured.
- **Run times.** 21:14 to 23:15. Every run exited 0.

## Results

| Run | Det FPS median / p5 / p1 | Det windows <25 FPS | Det delay median / p95 / max (ms) | Depth FPS median / p5 / p1 | Depth windows <25 FPS | Depth delay median / p95 / max (ms) | Hailo chip start → max (°C) | Pi max (°C) | Throttle flags | Camera drops | Speech lines |
|---|---|---|---|---|---|---|---|---|---|---|---|
| deployed 13-class, 30 min | 30.0 / 28.5 / 20.8 | 62 of 3305 (1.9 %) | 92 / 114 / 143 | 30.0 / 28.6 / 20.4 | 62 of 3300 (1.9 %) | 104 / 126 / 157 | 54.6 → 64.9 | 65.5 | 0x0 | 9 | 452 |
| yolov8m, 30 min | 29.8 / 27.8 / 20.4 | 85 of 3422 (2.5 %) | 176 / 245 / 279 | 28.0 / 25.0 / 20.1 | 169 of 3168 (5.3 %) | 127 / 156 / 171 | 61.3 → 76.2 | 67.8 | 0x0 | 1 | 2 |
| yolov8n, 5 min | 29.9 / 23.5 / 19.6 | 24 of 467 (5.1 %) | 91 / 118 / 127 | 30.0 / 24.4 / 19.5 | 25 of 468 (5.3 %) | 102 / 120 / 136 | 55.4 → 57.7 | 61.7 | 0x0 | 0 | 6 |
| yolov8s, 5 min | 30.0 / 23.7 / 19.5 | 25 of 467 (5.4 %) | 93 / 119 / 127 | 30.0 / 22.1 / 19.7 | 25 of 468 (5.3 %) | 101 / 122 / 136 | 57.1 → 61.0 | 61.7 | 0x0 | 0 | 10 |
| yolov8l, 5 min | 21.7 / 20.8 / 20.5 | 355 of 355 (100 %) | 1309 / 1344 / 1360 | 21.4 / 20.8 / 19.2 | 352 of 352 (100 %) | 133 / 173 / 189 | 64.4 → 81.0 | 63.4 | 0x0 | 0 | 10 |
| yolov8x, 5 min | 15.8 / 14.0 / 13.9 | 239 of 239 (100 %) | 1880 / 1907 / 1932 | 15.3 / 14.1 / 14.0 | 239 of 239 (100 %) | 154 / 213 / 220 | 65.0 → 81.1 | 61.1 | 0x0 | 0 | 10 |

Hailo chip temperature over the two 30-minute runs, as 5-minute means (hotter sensor):

| Minutes | 0–5 | 5–10 | 10–15 | 15–20 | 20–25 | 25–30 | Trend over the last 10 min |
|---|---|---|---|---|---|---|---|
| deployed 13-class | 58.3 | 60.1 | 61.4 | 62.2 | 63.7 | 64.1 | +1.13 °C per 10 min |
| yolov8m | 68.9 | 73.3 | 75.0 | 74.5 | 73.2 | 75.1 | +2.89 °C per 10 min |

**The chip's own throttling thresholds**, read from the chip after the runs
(`hailo_health_info_t`; `app-runs/hailo-health-info.txt`):
- the first throttling step at **104 °C** (neural-network clock 400 → 350 MHz), then 108, 112 and
  116 °C;
- the orange zone at 104 °C and the red zone at 120 °C;
- current throttling level -1 when read.

The Pi's throttle flags stayed `0x0` in every 10-second sample of every run.

## What the numbers show, for yolov8m against the deployed model

**Heat.**
- Neither the Pi nor the Hailo chip throttled in 30 minutes, in this room.
- yolov8m ran the chip about **10 °C hotter** throughout: a peak of 76.2 against 64.9 °C. From minute 10
  it held at 73–75 °C (5-minute means), 28 °C below the chip's first throttling step.
- The Pi peaked at 67.8 °C against 65.5 °C. Its CPU clock was below maximum in 12 of 180 samples
  against 7.
- 30 minutes cannot show what happens over longer use, in a hotter room, or inside an enclosure on a
  head. The yolov8m run also started about 2.6 °C warmer on the chip (53.7 against 51.1 °C at the end
  of the cool-down).

**Jitter in the real pipeline.**
- yolov8m saturates the chip, and the **depth branch pays for it**. Depth (the haptics) ran below
  29.5 FPS in 2725 of 3168 windows (86 %), against 996 of 3300 (30 %) for the deployed model. Its
  median was 28.0 FPS and its 5th percentile 25.0.
- **Detection delay roughly doubled:** median 176 against 92 ms, p95 245 against 114 ms. Detection
  frame rate varied more: standard deviation 2.32 against 1.50 FPS.
- The **yolov8m run did less work downstream than a real 13-class yolov8m would.** Hailo's model names
  its boxes in COCO terms, which the app's class map does not recognise. So nothing went on to tracking
  or speech: 2 speech lines, both system alerts, against 452 with the deployed model. Its CPU (80.6 %
  against 73.8 % of one core) therefore leaves out the speech work. Its 80-class NMS, meanwhile, is
  more host work than a 13-class model's.

**The other sizes.**
- yolov8n and yolov8s behave like the deployed model in the app: about 30 FPS on both branches and
  about 92 ms detection delay. The 30 FPS camera cap makes them indistinguishable here, as expected.
- yolov8l and yolov8x **cannot hold 25 FPS** in the app: 21.7 and 15.8 FPS for detection. Detection
  delay was 1.3 s and 1.9 s, as frames queue up. The chip reached 81 °C within 5 minutes and was still
  rising.

**The periodic dips** to about 20 FPS seen on 18 Oct appear with every model, and they set the p1
column. The 5-minute runs of n and s each caught one, which is why their p5 is lower. They are not
investigated here.

## Other observations (not about model size)

- **Camera drops without unplugging.** The app logged a camera loss 9 times in the deployed run and
  once in the yolov8m run ("Could not read from resource ... poll error 1: Invalid argument"). Each
  time it rebuilt the pipeline and came back within a few seconds: 48.8 s without vision in total in
  the deployed run. The kernel logged **no USB disconnect** at those times. The two runs differed in
  speech activity (452 against 2 lines), but this test cannot say whether that matters.
- **Memory steps up after camera drops**, not steadily with time. Between drops, the app's memory
  (including the speech processes it starts) stayed flat. Median memory 90 s before each group of drops
  against 30–120 s after:
  - deployed run: the drops at 373/377 s, −5 MB; at 1000/1005 s, +20; at 1222 s, +16; at
    1602–1621 s (4 drops), +38. Overall 212 → 279 MB;
  - yolov8m run: its one drop at 1414 s, +37 MB. Overall 223 → 270 MB.

  Whether this is a leak in the rebuild is not established here.
- **Batch check** (`app-runs/batch-check/`, 5 s per mode). yolov8n stays at 431 FPS HW-only at batch
  sizes 1, 2, 4 and 8, and yolov8s at 490.6. Queuing more frames does not speed n up. Why n is slower
  than s on this Pi remains unexplained.
- **Two Claude sessions drove these runs at once** that evening. One stopped the other's run, which was
  then relaunched. Both aborted attempts are kept in `app-runs/aborted-2026-11-11-2110/` and are not
  used. Two camera frames of the lit scene were saved by those attempts, at 21:06:33 and 21:11:07. Both
  were **deleted without being viewed**, as was every desktop screenshot. The runs above saved no images.

## Files (`app-runs/`)

| File | What it is |
|---|---|
| `run_app_runs.sh` | The runner: order, cool-down, model swap, samplers |
| `run_with_model.sh` | Adds `--det-hef-path` to the boot service's own arguments |
| `hailo_temp_sampler.py` | The chip temperature, every 10 s |
| `analyse_app_run.py` | Per-run figures |
| `summary.csv` | All runs, one row each |
| `run-console.txt` | The runner's console |
| `perf-app-<model>-<n>min.*` | Per run: app log (timestamped; `.log` is not committed), 10 s samples (`.csv`), summary (`.txt`), analysis, chip temperatures, the model file and its sha256, and a chart drawn from the logs (`.png`, no camera content) |
| `hailo-health-info.txt` | The chip's thermal thresholds |
| `quick/` | The 90 s check that ran first; not used |
