# T-15 · Performance measurement, run 1

- Run on the Pi at the user's request, before the Lead resumed.
- Not a Lead-reviewed task yet: no brief, no Tester. It is a first measurement made with the new
  tools (`docs/testing/tools/perf_run.py`, `perf_sample.py`, `perf_plot.py`), kept as evidence.
- Code measured: the development tree, with the uncommitted work of T-03a, T-03b and the unfinished
  T-00a (list in `evidence/perf-both-20min-system.txt`). T-00a's files are not on the
  per-frame path.

## Set-up (what was and wasn't the production configuration)

- Started with `scripts/sv-main.sh`, as the boot service does, headless (no preview window, the same
  as the service: its unit sets no display).
- **Stand-in glasses board on a pseudo-terminal that acknowledges every packet**, and **no control
  panel**, so the mode is "both" (detection plus depth). With the real boards the motors would have
  buzzed for 20 minutes, and the panel's switches (all off at the time) switched vision off.
- **A static scene:** the camera faced something very close (all three depth zones at 255, no
  detections). The run measures the two models and the pipeline at steady load. It does **not**
  include the extra work of a busy scene: several objects (tracker, speech) or changing depth.

## Results, 20 minutes (`perf-both-20min.*`)

| Measure | Result |
|---|---|
| Frame rate over the run (the app's average since start) | 29.6 FPS (35,550 frames in 1,200 s); the pipeline's target is 30 |
| Frame rate per 15-frame window (after the first minute, 2,250 windows) | median 30.0 FPS, 5th percentile 28.5, 1st percentile 20.2, **minimum 18.9**; no window longer than 0.79 s (15 frames take 0.50 s at 30 FPS) |
| Camera-to-callback delay (depth branch) | median 104 ms, 95th percentile 127 ms, 99th 132 ms, maximum 147 ms |
| App CPU | 73 % of one core on average (of four), busiest core 28 % on average, 38 % at most |
| Memory (app) | 195 MB at the start, 199 MB at the end (+2.2 %, in small steps at about 1, 3, 9.5 and 15 minutes) |
| Stability rule (REQ-DTA-09) | memory +2.2 % (limit +5 %): PASS; app CPU 72 % first 3 min, 75 % last 3 min (limit 10 points): PASS |
| Temperature | 58.4 C at the start, 65.5 C at most, 64.2 C after the app stopped |
| Cooling | the fan ran at 5,055 to 5,285 rpm |
| Throttling | `vcgencmd get_throttled` was `0x0` in every sample (no under-voltage, frequency cap or throttling, and none since boot); the undervoltage sensor never alarmed |
| CPU clock | 2400 MHz (maximum) in 114 of 120 samples; 1800 to 1900 MHz in 6 |
| Clean stop | Ctrl+C: exit code 0 in 0.5 s |

A second, 3-minute run (`HAILO_MONITOR=1` in the app, which had no effect, see below) gave the same picture (29.6 FPS average, 100 ms median delay, 62.8 C at
most; 5th percentile per-window rate 21.1 FPS, one drop of the same kind): `perf-npu.*`.

## What is not measured, and what looked odd

1. **NPU load: not available yet, cause verified.** `hailortcli monitor` printed "Monitor did not
   retrieve any files" with `HAILO_MONITOR=1` set in the app (`perf-npu-hailortcli-monitor.txt`). The chip
   is owned by `hailort_service` and the app is its client, so the setting has to be on the service. The
   running service has `HAILO_MONITOR=0` (from `/etc/default/hailort_service`; read from its environment with
   `sudo`). The fix is to turn it on for one run (a runtime override, not a permanent change) and restart
   `hailort.service`, then undo it. Planned for a quiet moment: the restart interrupts anything using the
   chip. `hailortcli measure-power` is not supported on this board (`HAILO_UNSUPPORTED_OPCODE`). The short
   run `perf-npu` was therefore not a monitored run: the variable had no effect.
2. **Delay to the motors and to speech is not measured.** Only camera-to-callback exists. The frame to
   motor packet and detection to spoken phrase delays need timestamps in the code.
3. **Three short frame-rate drops, about every 6 minutes.** The app's own FPS number is an average
   since start, so it hides them. Counted per 15 frames, the rate falls to about 19 to 20 FPS for roughly
   15 to 20 seconds at about 3.2, 9.6 and 15.5 minutes (chart, fifth panel). At the same moments the
   app's CPU dips and the camera-to-callback delay steps up by about 15 ms. The CPU clock also dipped to
   1,800 to 1,900 MHz in 6 of 120 samples (59 to 65 C, so not thermal; the throttle flag stayed `0x0`).
   Something periodic on the Pi competes with the app. **Not identified:** the system journal kept no
   entries for those minutes, and no systemd timer runs at that interval. A guess, not checked: the known
   Hailo driver log flood (journald load). Worth finding before the thesis claims a steady 30 FPS.
4. **Memory creeps up in steps** (+4 MB in 20 minutes). Within the rule, but worth watching in the
   long battery run (REQ-PERF-01).

## Evidence files (`evidence/`)

`perf-both-20min.png` (the chart), `-screen-mid.png` and `-screen-end.png` (the Pi's
desktop during and after the run), `.txt` (summary), `.csv` (every sample), `-app.log` (the app's console, a
timestamp on every line), `-system.txt` (kernel, HailoRT, the uncommitted files, model, the sudo reads). The `perf-npu`
files are the same set for the short run.
