# T-15 · Performance measurement, run 3: the battery rundown (3.6 hours)

> **For the paper, use [BATTERY-RESULTS.md](BATTERY-RESULTS.md)** (the citable version, with the limits and the figure). This file is the engineering report. **3.64 hours is a lower bound:** the stop at 3.5 V was a safety margin, and the Pi stayed up at least an hour more at idle.

- Run on the Pi on 2026-10-19, 21:09 to 00:47, at the user's request. Evidence: `evidence/perf-rundown*`
  (chart, summary, every 10 s sample, the app's console with a timestamp on every line, a screenshot).
- Not a Lead-reviewed task: no brief, no Tester. A measurement kept as evidence, like runs 1 and 2.
- Code measured: the development tree with the demo track's work in it (the evening's pattern-strength change in
  `core/haptic_patterns.py` and `workers/serial_worker.py` was uncommitted; see `perf-rundown-system.txt`).

## Set-up

- The same as runs 1 and 2: `perf_run.py`, the app started by `scripts/sv-main.sh` as the boot service starts it,
  headless, stand-in glasses board on a pseudo-terminal (so **no motor load**), no control panel (mode "both").
- **On battery throughout:** the HAT's power-loss line (GPIO6) said "on battery" in all 1,307 samples. It started on
  the charger's unplug (the tool waited for it) at 4.086 V, which the gauge called 90.2 %.
- **The stop was on voltage, not on the gauge's percentage:** two readings in a row at 3.5 V or under. The gauge's
  percentage drifts: it read 85 % at a full-cell 4.185 V the evening before, and 2.0 % at the 3.5 V stop. The vendor's
  own script shuts down on voltage (3.20 V), which is why.
- The scene was an ordinary room with a table in view; the camera was not touched for most of the run.

## Results

| Measure | Result |
|---|---|
| **Battery life under this load** | **3.64 hours** on battery, from 4.049 V (first sample) to **3.499 V**, 1,307 readings |
| What that leaves | about 10 to 15 % of the cell (3.5 V is well above the 3.20 V cut-off); **not measured below 3.5 V** |
| Frame rate per 15 frames (after the first minute) | median 30.0, 5th percentile 28.5, 1st 20.1, minimum 18.9; no window over 1 s. The same as runs 1 and 2 |
| Camera-to-callback delay | median 104 ms, p95 127, p99 132, max 154. The same as run 1 |
| App CPU | 74.7 % of one core on average (run 1: 73 %, run 2: 80 %) |
| Temperature | 55 to 67.8 C |
| Throttling | `0x0` in every sample; the undervoltage alarm never rose, **including at 3.5 V on battery** |
| CPU clock | 2400 MHz in 1,222 of 1,307 samples; 1,700 to 2,300 in 85 short dips (59 to 66 C, so not thermal) |
| Stability (REQ-DTA-09) | CPU 72 % first 3 min, 75 % last 3 min: PASS. **Memory 197 to 240 MB (+21.8 %, limit +5 %): FAIL** |
| Clean stop | Ctrl+C: exit code 0 in 0.6 s |

**What the life figure covers and does not:**
- It is the Pi, the camera, the Hailo chip, the stand-in glasses board and the earphones' speech. The **real glasses
  board's motors** have their own cell (not readable from the Pi); the **control panel** was not connected.
- It starts at 4.05 V, not from a freshly charged cell (about 4.18 V), so a full charge adds a little.
- A straight line from the gauge's percentage (24 % an hour, "3.9 hours from 100 to 5 %") is in the summary file; ignore
  it, since the percentage drifts. The measurement is the 3.64 hours to 3.5 V.

## What happened in the run, and what to look at

1. **The camera dropped on its own at 90.9 minutes, nobody touching it, twice in 20 seconds, and the failsafe worked.**
   The source reported `Could not read from resource` / `poll error 1: Invalid argument`. The program raised "Critical
   Error: Camera cannot be reached", played the camera pattern, found the camera again within a second ("Camera has
   been reconnected"), and went through it again six seconds later. No undervolt flag, no throttling, 3.88 V. This is the
   same drop as 2026-10-15 (memory note "camera drops without unplug", then a one-off): **it has now recurred.** Cause
   still unknown; this one is the first on battery.
2. **Memory is still growing, about 7 to 11 MB an hour, and has not levelled off** (196 MB at the start, 212 at 1.5 h,
   226 at 2.0 h, 240 at 3.6 h). Two vision restarts account for about 12 MB (T-12: 5.8 MB per restart); the rest is
   about 9 MB an hour. Over an 8-hour day that is about 70 MB on an 8 GB machine, so not urgent, but the 20-minute runs
   could not show it.
3. **The detection frame-rate figure in the `[DEPTH]` line is wrong after a vision restart:** it reads 293.8 shortly after
   the restart and falls towards 30 over the next hours (90.9, 64.3, 53.8 ...). The frame counter was not reset with the
   rest. It is the program's own display, not a measure of the detection rate (unverified); the summary's "detection
   FPS median 54.5" is this artefact. `pipeline/callbacks.py` is the detection owner's.
4. **A table in the centre of the view was announced about once every 1.3 seconds for 31 minutes** ("table still center",
   37 to 68 minutes): an urgent-tier object skips the repeat limit, so it is said again each time the last phrase ends.
   Heard by a wearer this would be constant. It is a detection tuning question for the detection owner, not changed.
5. **At 187.5 minutes the camera view check fired for real:** the picture went black (average brightness 0), and after
   three checks it said "Camera is blocked or it is too dark..." three times, then a bright flash (91 % at white) and a
   normal view, and "Camera view is back." It looks like a cover-and-flash test by hand; it behaved as designed.
6. **The Pi stays on after the run, by design** (nothing shuts it down; T-06 and T-07 are not built). At 01:26 it was
   still on battery at 3.461 V, 40 minutes after the stop, and falling.

## Evidence files (`evidence/`)

`perf-rundown.png` (the chart, with the battery panel), `perf-rundown.txt` (summary), `perf-rundown.csv` (every sample:
voltage, gauge percentage, power source), `perf-rundown-app.log` (the app's console), `perf-rundown-system.txt`,
`perf-rundown-screen-end.png`. The tool's own tests and mutation check: `rundown-tool-mutations.txt`.
