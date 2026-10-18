# Handoff: performance test results, and how the methods were revised

Prepared from the Second Vision repository. Everything a writing agent needs to update
the thesis paper's performance-testing text is in this folder: this file and `evidence/`. You do not
need the repository.

## 1. Your task

Revise the paper's **Inference Latency Test** and **Evaluation of Real-Time Performance** passages so
that they:
1. describe the methods **as they were actually carried out** (section 3 gives, per method, what the
   paper said, what was done instead and why, and wording that is safe to use);
2. report the **results** in section 4, with the limits in section 6 stated, not hidden;
3. mark what is **still to be measured** (section 7) as planned, not as done.

Rules:
- **Use only numbers from this folder.** Do not round them up, extrapolate, or invent a comparison.
  If the paper needs a number not given here, list it as a gap for the author.
- The paper's authors decide the wording and the claims. Where this file says "not established", the
  paper should not claim it.
- The methods were meant as suggestions, not requirements (author, 2026-10-17), so changing the
  paper's description of them to match what was done is expected.
- Keep a plain, factual tone. Do not claim real-time safety beyond what section 4 shows.

## 2. The system under test

| Item | Value |
|---|---|
| Computer | Raspberry Pi 5, 8 GB RAM (8,062 MB), four Cortex-A76 cores, 2.4 GHz maximum |
| Operating system | Debian 13 (trixie), Linux 6.12.62+rpt-rpi-2712 (aarch64) |
| Accelerator | Hailo-8 (AI HAT+, 26 TOPS), HailoRT 4.23.0, used through the `hailort_service` |
| Detection model | **YOLOv8s**, 14 classes, 640x640 input, compiled for Hailo-8 (HEF file, 9.7 MB). The author confirms it is the "s" variant; the file's metadata does not name the variant |
| Depth model | SC-DepthV3 (Hailo resource `scdepthv3.hef`) |
| Camera | Arducam 1080P Low Light (USB, IMX291), run at 640x480 |
| Pipeline | one GStreamer pipeline, both models on the same camera frames; frame rate capped at 30 FPS |
| Cooling | active cooler; fan speed read 5,055 to 5,285 rpm during the run |
| Code | the project's development tree, including work not yet committed (camera-loss handling and an alert scheduler, none on the per-frame path) |
| Run mode | headless (no preview window), started with the same script the boot service uses, detection and depth both enabled |

## 3. The methods: what the paper says, what was done, and why

The paper's four items, one per row.

| Paper | What was actually done | Why it changed | Wording you can use |
|---|---|---|---|
| **Frames per second and latency, measured with `cv2.TickMeter` and `time`, from `cv2.read()` to the final trigger sent to the FeedbackEngine** | **Frame rate:** counted per 15 frames from the timestamps of the application's own console lines (`[DEPTH] Frame N ...`, one line every 15 frames), and also read from the application's own average-since-start counter. **Delay:** the application's own "camera-to-callback" figure for the depth branch: the age of the frame, in milliseconds, at the moment the depth callback receives it (pipeline clock minus the camera's capture timestamp). It includes every queue, the cropper, inference and the aggregator. It does **not** include the conversion to motor values, the serial send, or speech | The camera runs through a GStreamer pipeline, so `cv2.read()` is never called and `cv2.TickMeter` has nothing to wrap. The pipeline already stamps each frame at capture and the application already computes this age. There is **no class called FeedbackEngine**; the two real outputs are the motor packet (serial worker) and the spoken phrase (speech worker) | "Frame rate and delay were measured inside the running application: frame rate from the interval between its periodic log lines, delay as the age of each depth frame when it reaches the depth callback, computed from the GStreamer pipeline clock and the camera's capture timestamp." |
| **End-to-end delay from frame ingestion to the trigger sent to the feedback engine** | **Not measured.** Only the camera-to-callback part above | The motor and speech ends have no timestamps in the code yet | State it as future work, or as a limitation: the reported delay stops at the depth callback |
| **CPU and memory, per core, with `psutil`** | Done as written: `psutil` every 10 s: per-core CPU load, system memory, and the application's own CPU and resident memory. The application's CPU is the sum over its processes, in percent of one core | none | "Sampled every 10 seconds with psutil." |
| **NPU utilisation with `hailortcli`** | **Not obtained yet; the cause is known.** `hailortcli monitor` was tried with `HAILO_MONITOR=1` set in the application and reported "Monitor did not retrieve any files". `hailortcli measure-power` fails on this board (`HAILO_UNSUPPORTED_OPCODE`) | The Hailo chip is owned by a background program, `hailort_service`, and the application is only its client. The monitor reads from whichever process owns the chip, so the setting must be on the service, and the service's own setting is `HAILO_MONITOR=0` (read from `/etc/default/hailort_service`, checked on the running service). Turning it on means a brief restart of that service. It was not done because it interrupts anything using the chip, not because of a rule; it is planned | Report NPU utilisation as not measured, with this reason. Do not substitute the frame rate for it |
| **Temperature every 10 s with `vcgencmd measure_temp`** | Temperature read every 10 s from the kernel (`/sys/class/thermal/thermal_zone0/temp`), plus `vcgencmd measure_temp` before and after the run. **Throttling** was read every 10 s with `vcgencmd get_throttled` (flags for under-voltage, frequency cap and throttling, current and since boot). The CPU clock was read against its maximum | On this Pi `vcgencmd` only works as root even for a user in the `video` group, so the sampling script reads the temperature from the kernel and uses `sudo` for the two read-only `vcgencmd` queries | "Temperature was read every 10 seconds; throttling was checked with the firmware's throttle flags." |

Also describe the **procedure** once: the application was started as the boot service starts it;
sampling started when the first frame had been processed; the run lasted 20 minutes; the application
was then stopped with Ctrl+C. The first minute is excluded from frame-rate figures (the rate ramps up
from about 10 FPS while the pipeline starts).

## 4. Results

Run 1, 20 minutes, detection and depth together (`evidence/perf-both-20min.*`).

| Measure | Result |
|---|---|
| Frame rate over the run (average) | 29.6 FPS (35,550 frames in 1,200 s); the cap is 30 |
| Frame rate per 15-frame window, after the first minute (2,250 windows) | median 30.0 FPS; 5th percentile 28.5; 1st percentile 20.2; minimum 18.9; longest window 0.79 s (15 frames take 0.50 s at 30 FPS); no window over 1 s |
| Camera-to-callback delay (depth branch) | median 104 ms; 95th percentile 127 ms; 99th percentile 132 ms; maximum 147 ms (2,251 readings) |
| Application CPU | 73 % of one core on average, of four cores (range 56 to 76 %) |
| Busiest core | 28 % on average; 38 % at most |
| Application memory | 195 MB at the start, 199 MB at the end (+2.2 %), rising in small steps |
| System memory used | 2,982 to 3,056 MB of 8,062 MB |
| Temperature | 58.4 C at the start; 65.5 C at most; 64.2 C after the run |
| Throttling | `0x0` in every sample: no under-voltage, no frequency cap, no throttling, and none since boot. The under-voltage sensor never alarmed |
| CPU clock | at its 2,400 MHz maximum in 114 of 120 samples; 1,800 to 1,900 MHz in the other 6 |
| Stop | Ctrl+C: exit code 0, 0.5 s |

The author's stability rule (REQ-DTA-09) over 10 or more minutes, ignoring the first minute: memory at
the end no more than 5 % above memory at minute 1 (**+2.2 %: met**), and average application CPU over
the last 3 minutes within 10 percentage points of the first 3 (**72 % then 75 %: met**).

A second, 3-minute run (`evidence/perf-npu.*`) was started with `HAILO_MONITOR=1` in the application, which
had no effect (section 3), so it is simply a second short run. It gave the same picture: 29.6 FPS average, 100 ms median delay, 62.8 C at most, throttle flags `0x0`.

## 5. What the results show, and two things the text must not smooth over

1. **Both models run together at the pipeline's cap for 20 minutes** with a stable average, flat CPU,
   a small memory increase, no throttling, a maximum temperature 15 C below where the Pi starts to
   reduce its clock (general Raspberry Pi guidance, about 80 C; not measured here, cite the
   manufacturer), and a median camera-to-callback delay of about 0.1 s.
2. **The frame rate is not perfectly steady.** Counting per 15 frames shows three drops to about
   19 to 20 FPS lasting roughly 15 to 20 seconds, at about 3.2, 9.6 and 15.5 minutes (about every 6
   minutes). At those moments the application's CPU dips and the delay steps up by about 15 ms. The
   average hides this (the application's own FPS figure is an average since start). The cause is **not
   identified**. The CPU clock dips at some of the same moments, at 59 to 65 C, so it is not thermal.
   Do not describe the frame rate as constant; describe the median and the dips.
3. **Application memory grew in small steps** (about 4 MB in 20 minutes, at about 1, 3, 9.5 and 15
   minutes). It meets the rule, but 20 minutes cannot show whether it keeps growing. The planned
   battery run (section 7) will.

## 6. Limits: what not to claim

- **One run per configuration,** on one day, one board, one camera. No repeats, so no confidence
  intervals.
- **The scene was static and simple.** The camera faced something very close: all three depth zones at
  their maximum and no objects detected. This measures the two models and the pipeline at steady
  load. It does **not** include the extra work of a busy scene (several objects, tracking, speech,
  changing depth). A run with people moving in front of the camera is still needed before saying the
  system sustains real use.
- **Stand-ins were used for some hardware.** The glasses' microcontroller board was replaced by a
  program on the Pi that acknowledges every packet (35,578 packets in the 20 minutes), so the motors
  stayed quiet, and the control panel was absent, so the mode was "both". The serial worker ran as in
  normal use.
- **Ambient temperature was not recorded.**
- **The delay figure stops at the depth callback.** It is not the delay to the motors or to speech, and
  it is the depth branch only.
- **No NPU figure,** see section 3.
- **The code was a development state,** not a tagged release (section 2).

## 7. Still to be measured (planned)

1. A busy-scene run (people or objects moving in front of the camera) of at least 10 minutes.
2. Delay from frame to motor packet and from detection to spoken phrase (needs timestamps in the code).
3. NPU utilisation: turn the monitor on in `hailort_service` for one run (a brief service restart), capture
   `hailortcli monitor`, then turn it off. Monitoring may itself add a little load, so report it as a separate run.
4. The battery run-down under full load, 100 % to 2 %: average frame rate, temperature, throttling,
   battery life and whether each battery announcement played. Long enough to settle the memory question.
5. Identify the cause of the periodic frame-rate drops, then repeat the 20-minute run.

## 8. Corrections to facts in the paper's text

- Model name: the paper's **YOLOv8s** is right. Some of the project's own documents still say YOLOv8n and
  are being corrected.
- `cv2.read()`, `cv2.TickMeter` and "FeedbackEngine" do not describe this system (section 3).
- `vcgencmd measure_temp` as a plain user does not work on this Pi (section 3).

## 9. How it was run (for the methods section or an appendix)

Three small scripts in the project's `docs/testing/tools/` folder produced the data, all standard
library plus `psutil` and `cv2`:
- `perf_run.py`: checks that the device is free, starts the application as the boot service does,
  starts the sampler, takes desktop screenshots half-way and at the end, stops the application with
  Ctrl+C, and draws the chart;
- `perf_sample.py`: every 10 s reads temperature, CPU clock, the under-voltage sensor, fan speed, per-core
  CPU, memory, the application's CPU and memory, and the throttle flags; and reads frame rate and delay
  from the application's console log;
- `perf_plot.py`: draws the chart.

Command for the 20-minute run: `perf_run.py --duration 1200 --label both-20min`.
The only elevated commands were `sudo -n vcgencmd get_throttled`, `sudo -n vcgencmd measure_temp` and
`sudo -n fuser -v /dev/hailo0` (lists what holds the chip; read-only).

## 10. Evidence files (`evidence/`)

| File | What it is |
|---|---|
| `perf-both-20min.png` | **The chart**: temperature, CPU clock, CPU load, memory, frame rate per 15 frames, delay, over the 20 minutes. Suitable for the paper |
| `perf-both-20min-screen-mid.png` | Screenshot of the Pi's desktop half-way through, showing the live sampler output |
| `perf-both-20min-screen-end.png` | Screenshot at the end of the run |
| `perf-both-20min.txt` | The run's summary, as quoted in section 4 |
| `perf-both-20min.csv` | Every 10-second sample (120 rows) |
| `perf-both-20min-system.txt` | Kernel, HailoRT version, code state, model file, the elevated reads |
| `perf-npu.png`, `.txt` | The same, for the 3-minute run |
| `perf-npu-hailortcli-monitor.txt` | The monitor's output: "Monitor did not retrieve any files" |

The full console log of the application (about 400 KB, a timestamp on every line) is kept in the
repository, not here.

## 11. Terms

- **FPS:** frames per second. **Camera-to-callback delay:** how old a frame is when the application's
  depth stage receives it.
- **Throttling:** the Pi lowering its clock to protect itself from heat or low supply voltage.
- **`0x0` throttle flag:** the firmware reports no under-voltage, no frequency cap and no throttling.
- **Both mode:** detection and depth estimation running together, the normal mode.
- **REQ-DTA-09:** the author's requirement that over a 10-minute run resource use stays stable.
