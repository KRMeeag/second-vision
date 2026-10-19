# T-15 · Performance measurement, run 2: the NPU (20 minutes)

- Run on the Pi on 2026-10-19, 06:46 to 07:06, at the user's request. The user started it from their
  terminal, because Claude Code's auto mode does not let an agent restart a system service.
- Not a Lead-reviewed task: no brief, no Tester. A measurement kept as evidence, like run 1.
- Code measured: the development tree, with the demo track's work in it (F0 to F6, and F5's
  camera-view check; the uncommitted files are listed in `evidence/perf-npu-20min-system.txt`). So this run
  is also F5's performance check (DEMO-CHECKLIST.md Part J).

## Set-up

- The same as run 1: `perf_run.py`, the app started by `scripts/sv-main.sh` as the boot service starts it,
  headless, stand-in glasses board on a pseudo-terminal, no control panel (mode "both").
- **The NPU monitor turned on for this run only.** `hailortcli monitor` reads files that the process
  owning the chip writes, and here that is `hailort_service` (the app is its client). Its
  `HAILO_MONITOR` was set to 1 by a runtime drop-in under `/run` (nothing under `/etc` was edited) and
  the service was restarted. Afterwards the drop-in was removed and the service restarted again. The
  log (`.claude/runs/npu_run.log`) records the service having `HAILO_MONITOR=1` before the run, and
  `HAILO_MONITOR=0` with the chip visible after it. The script was a copy of
  `.claude/tools/queued_npu_run.sh` without its Lead steps (the Lead stays paused on demo day).
- The scene: the camera on an ordinary lit room (the view check measured an average brightness of 111).

## Results (`perf-npu-20min.*`, compared with run 1's `perf-both-20min.*`)

| Measure | Run 2 (this run) | Run 1 (2026-10-18) |
|---|---|---|
| **NPU (Hailo-8) utilisation** | **median 85 %, mean 85 %, 72 % to 88 %** (59 readings, every 10 s, over the second half) | not measured |
| Per model | depth (`scdepthv3`) median 40.6 %, detection (`best`) median 45.0 % | — |
| Frame rate the NPU reports per model | 30.0 FPS median for each | — |
| Queues into and out of the chip | every stream averages 0.5 frames of 8 slots, at most 1 (screenshot): the chip keeps up, nothing waits | — |
| Frame rate (the app's average) | 29.4 depth, 29.5 detection | 29.6 |
| Frame rate per 15 frames (after the first minute) | median 30.0, 5th percentile 28.5, 1st 20.0, minimum 19.3; no window over 1 s | median 30.0, 5th 28.5, 1st 20.2, minimum 18.9 |
| Camera-to-callback delay | median 106 ms, p95 129, p99 135, max 189 | median 104, p95 127, p99 132, max 147 |
| App CPU | 79.9 % of one core on average | 73.3 % |
| App memory | 168 → 171 MB (+1.9 %) | 195 → 199 MB (+2.2 %) |
| Stability rule (REQ-DTA-09) | memory +1.9 % (limit +5 %): **PASS**; CPU 77 % first 3 min, 82 % last 3 min (limit 10 points): **PASS** | PASS, PASS |
| Temperature | 56.2 C at the start, 67.2 C at most, 63.7 C after the stop | 58.4 to 65.5 C |
| Throttling | `0x0` in every sample: no under-voltage, frequency cap or throttling, none since boot | `0x0` |
| CPU clock | 2400 MHz in 118 of 120 samples, 1800 MHz in 2 | 114 of 120 |
| Fan | 2,912 to 7,124 rpm | 5,055 to 5,285 rpm |
| Clean stop | Ctrl+C: exit code 0 in 0.6 s | 0 in 0.5 s |

**What it means:**
- **The NPU has about 15 % headroom** with both models at 30 FPS. A third model, or a higher frame
  rate, would not fit without giving something up.
- **F5's camera-view check costs no frame rate.** The frame rate and the delay match run 1 within run-to-run
  noise (DEMO-CHECKLIST J2).
- **The app uses 6.6 points more of one core than in run 1** (80 % against 73 %; 1.7 % of the whole
  four-core CPU). Between the two runs the demo track added several workers: the camera view check, the
  status board, the earphones watch, and the alert and pattern work. This run doesn't split the cost
  between them. The app's memory fell by 27 MB in the same time.

## What is not measured, and what looked odd

1. **The NPU numbers cover the second half only (9.6 to 19.4 minutes).** The run's own NPU sampler came
   back empty: `timeout` runs `hailortcli monitor` in the background, and from the run's terminal window
   it then read the terminal, was stopped by SIGTTIN and printed nothing (reproduced: 0 characters under a
   terminal, 9,345 without one). It was found 9 minutes in. A second sampler, the same code started from
   outside any terminal, took over (`perf-npu-20min-side-npu.*`). Fixed in `docs/testing/tools/perf_npu.py`
   (its input is `/dev/null`, checked under a terminal: 85.0 % read). The next run samples all of it.
   The run's own `perf-npu-20min-npu.csv` (empty) and `.png` (no NPU panel) are left as they came out;
   `perf-npu-20min-with-npu.png` is the same chart with the second sampler's readings on the run's timeline.
2. **The three short frame-rate drops of run 1 recur:** at about 1.5, 8 and 13.4 minutes here, about 6
   minutes apart again. Each time the rate per 15 frames falls to about 20 FPS for 15 to 20 seconds, the
   app's CPU dips and the delay steps up by about 15 ms. **New:** the one drop inside the NPU readings
   (about 13.7 minutes) is also the one NPU dip (72.5 %). The chip got less work, so the drops start before
   it: in the camera or on the CPU side, not on the NPU. Still not identified.
3. **The power the chip draws is not measured.** `hailortcli measure-power` is not supported on this board
   (run 1). The battery rundown (REQ-PERF-01) measures the whole device instead.
4. **A static, lit scene**, as in run 1: no objects to announce, so no speech load.

## Evidence files (`evidence/`)

| File | What it is |
|---|---|
| `perf-npu-20min-with-npu.png` | **The chart**, with the NPU panel (temperature, CPU clock, CPU load, memory, frame rate, delay, NPU) |
| `perf-npu-20min-screen-hailortcli-monitor.png` | **The live NPU monitor** during the run, 07:02: 86.0 %, both models at 30 FPS, the queues |
| `perf-npu-20min-screen-mid.png`, `-screen-end.png` | The Pi's desktop half-way through and after the run |
| `perf-npu-20min.png` | The run's own chart (no NPU panel: its NPU sampler was empty) |
| `perf-npu-20min.txt`, `.csv` | Summary, and every 10 s sample |
| `perf-npu-20min-side-npu.csv`, `-side-npu-raw.log` | The NPU readings (59), and the monitor's raw screens |
| `perf-npu-20min-npu.csv`, `-npu-raw.log` | The run's own NPU sampler: 120 empty captures (item 1) |
| `perf-npu-20min-app.log` | The app's console, a timestamp on every line |
| `perf-npu-20min-system.txt` | Kernel, HailoRT, the uncommitted files, model, `HAILO_MONITOR=1` on the service |
