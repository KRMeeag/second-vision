# Session B: latency instrumentation and the 20-minute real-time re-run

Run on 2026-11-12 (Pi clock, UTC+8), on branch `feat/latency-log` (from `85789d5`). Same device and software
as Step 1 and Session A: Raspberry Pi 5 8 GB with the active cooler, Hailo-8 with HailoRT 4.23.0, Debian 13,
Linux 6.12.62+rpt-rpi-2712. Detection model: the deployed 13-class `models/hef_v3/hef_deploy/best.hef`;
depth: `scdepthv3.hef`. `second-vision.service` was stopped for every run and is still stopped.

| Part | What | Status |
|---|---|---|
| B1 | 12 tracker cases on the 13-class model | **Blocked.** The tracker's images are not on the Pi (Step 1 §1.5, §8 item 3); open question to Kenth |
| B2 | Latency instrumentation, off by default | **Done.** `SV_LATENCY_LOG=1`; 13 new tests; full suite 1097 passed, 217 skipped (Poetry environment) |
| B3 | 20-minute real-time re-run against 18 Oct, plus haptic latency | **Done.** One run with the stand-in glasses, one with the real boards (both rockers on throughout) |
| B4 | Audio latency trials | **Not run.** Per the thesis writing plan Kenth relayed on 2026-11-12, speech delay goes in Limitations with no number; see below |

## B1: blocked

The brief's 12 cases (M01, TC-001-001 to TC-003-004 in the team's test tracker) need the tracker's test
images. They are not on the Pi, and the Pi's tracker copy (2026-10-06) differs from the brief's summary in
three cases (Step 1 `RESULTS.md` §5 item 3). To run B1 I need either the images, or approval to pick them
from `~/v3p_eval/test`, and the current tracker if it has been revised.

## B2: the latency log

**Switch.** `SV_LATENCY_LOG=1` turns it on; `SV_LATENCY_DIR` names the output folder (default
`/tmp/second-vision-latency/<time>-<pid>`). Off (the default), `latency_log.LOG` is `None`, and every hook in
the app is a single `is None` test. No threshold, model or per-frame behaviour changed.

**What it records** (`src/second_vision/core/latency_log.py`, all on `time.monotonic()`):

| File | One row per | Columns |
|---|---|---|
| `haptic.csv` | motor packet the serial worker wrote | capture of the depth frame it came from; queued by the depth callback; taken by the serial worker; `port.write()` returned (and whether it succeeded); the L/C/R duties before the strength knob |
| `acks.csv` | serial-worker pass that found an ACK from the glasses board | when the worker noticed it |
| `audio.csv` | detection announcement the speech worker started | track id, label, zone, phrase, tier; capture of the track's first qualifying frame; capture of the frame that put it in the mailbox; when it did; when the speech process had been started; engine |
| `clock.txt` | run | one monotonic ↔ Unix pair, to line the rows up with the app's log |

- **Capture time** is the callback's arrival time minus the frame's age on the pipeline clock
  (`_pipeline_latency_ms`), the same figure as the `lat` in the `[DEPTH]` line.
- **The queue contract is unchanged** (AGENTS.md Rule 2). A queued dict is linked to its timings by object
  identity in a side table, which holds a reference to each tagged object and is bounded (512).
- **Never from the GStreamer thread:** rows are buffered and written by the module's own thread every 5 s and
  at exit.
- **ACKs have no sequence number.** The worker notices an ACK at its first check after the *next* write,
  about one frame later. The analysis pairs each packet with the first ACK noticed at least 1 ms after its
  write.

**Hooks.**

| File | Hook |
|---|---|
| `pipeline/callbacks.py` | `on_depth_frame` stores the frame's capture time. `_publish_motors` tags the packet. `_process_real_detections` notes each track's first qualifying frame and tags the winning payload. The winner is now picked with `max(candidates, …)`, the same choice as before, so its index can be found. |
| `workers/serial_worker.py` | Dequeue and write: the write result is kept in `sent`. ACK observation. |
| `workers/tts_worker.py` | Speech start, right after `_start_espeak()` returns. |

The `[DET] Frame N | lat X ms` line under the same flag was committed in Session A (`6d84ad3`).

**Tests.**
- `tests/test_latency_log.py` has 13 tests:
  - off by default; headers; identity matching; bounded table;
  - the haptic, ACK and audio rows;
  - the motor tag keeps the contract keys;
  - the detection payload; the speech worker; the serial worker against a fake board.
- `tests/test_det_latency_log.py` has 6 (committed in Session A).
- Full suite in the Poetry environment, where the GStreamer-level tests skip: **1097 passed, 217 skipped,
  exit 0** (`pytest-b2-final.log`).

**Analysis scripts.**
- `analyse_latency.py <run>` writes `latency-analysis.txt` and `latency-audio-rows.csv`. It leaves out the
  first 60 s, as the frame-rate figures do, and gives every figure with and without camera losses.
- `compare_b3.py` writes `b3-comparison.md`.
- `ack_probe.py` times the board's reply directly.

## B3: the 20-minute runs

**Protocol, as on 18 Oct.**
- `run_b3.sh` starts `scripts/sv-main.sh` through `perf_run.py`, the way the boot service does: headless,
  with no preview.
- It samples every 10 s for 1200 s, then stops the app with Ctrl+C.
- Added for this session: `SV_LATENCY_LOG=1`, the Hailo chip temperature every 10 s (Session A's sampler),
  and `--no-screenshots`. No camera image was saved (Kenth, 2026-11-11).

| Run | Time | Boards | Mode | Notes |
|---|---|---|---|---|
| `b3-standin-20min` | 04:41–05:01 | stand-in glasses on a pty (it acknowledges like the board), no panel: as on 18 Oct | both | Camera facing something close, as on 18 Oct. The camera-blocked alert was spoken 40 times |
| `b3-realboards-20min-depthonly` | 05:04–05:24 | real glasses board and panel | **depth**: DETECT read OFF | **Superseded.** Only its haptic timings are used below |
| `b3-realboards-20min` | 06:47–07:07 | real glasses board and panel; both rockers ON from 06:46:07 | **both throughout**: no mode change, 2,376 `[DET]` and 2,372 `[DEPTH]` lines | No speech: no detection was announced and no alert raised. Volume knob at minimum, earphone sink at 0.25 (Kenth asked for it quieter). Strength knob ~0.4 (details below) |

### Against 18 Oct (`b3-comparison.md`)

| Measure | 18 Oct (14-class) | Stand-in | Real boards |
|---|---|---|---|
| Average FPS (depth frames / 1200 s) | 29.6 (35,550) | 29.6 (35,580) | 29.6 (35,565) |
| 15-frame windows after minute 1: median / p5 / p1 / min | 30.0 / 28.5 / 20.2 / 18.9 | 30.0 / 28.6 / 20.4 / 19.0 | 30.0 / 28.5 / 20.4 / 18.0 |
| Capture → depth callback: median / p95 / p99 / max (ms) | 104 / 127 / 132 / 147 | 106 / 129 / 134 / 164 | 106 / 129 / 136 / 158 |
| Capture → detection callback: median / p95 / p99 / max (ms) | not logged then | 92 / 116 / 123 / 136 | 91 / 117 / 124 / 152 |
| App CPU, average (% of one core) | 73 % | 78 % | 77 % |
| Busiest core, average / max | 28 / 38 % | 30 / 40 % | 31 / **64** % (see below) |
| App memory, minute 1 → end | 195 → 199 MB | 200 → 203 MB | 199 → 204 MB |
| SoC temperature, first / min / max | 59.5 / 58.4 / 65.5 °C | 52.9 / 52.9 / 61.7 °C | 51.8 / 51.8 / 63.9 °C |
| Hailo-8 chip, start / max | not logged | 52.2 / 60.9 °C | 51.3 / 60.7 °C |
| Throttle flags (every 10 s) | 0x0 | 0x0 | 0x0 |
| Camera losses | | 0 | 0 |
| Dip episodes below 25 FPS (start minute, length) | 3.6, 19.3 s; 9.4, 19.4 s; 15.7, 19.5 s | 3.0, 19.2 s; 9.2, 19.4 s; 15.5, 18.7 s | 5.2, 19.3 s; 11.2, 28.9 s; 17.7, 19.3 s |

- **The 13-class model holds the 18 Oct real-time figures,** with either set of boards.
- **The three dips, about six minutes apart,** are present in all three runs.
- **Logging and CPU:** the logging was on in both B3 runs and off on 18 Oct. The stand-in run also spoke
  40 alert lines. Neither difference was isolated: there is no run with the logging off.

**My own activity during the real-boards run.** I was working on the Pi while it ran.
- **06:51:** a whole-disk `find` (nice 19).
- **06:56–07:00:** writing new Python files, which the editor's language server on the Pi analyses.

It lines up with the samples that differ:

| Pi clock | Sample | What it shows |
|---|---|---|
| ≈06:52:29 | 320.9 s | All four cores at 57–64 % while the app itself dropped to 62 %: the 64 % maximum |
| 06:58:40–07:00:00 | 692–772 s | The busiest core at 42–60 % |

The 11.2-minute episode is:
- the periodic dip, 06:58:32–06:58:41;
- two separate one-second blips, at 06:58:23 and 06:58:50, which the 30-second merge rule joined into
  28.9 s.

The blips and the core peak line up with that activity. I can't prove the cause from these logs. The
averages, percentiles and the other two dips are not affected by it.

**Strength knob in the real-boards run.**
- At 06:47:27–28 it was turned up to 0.99 and back; after that it stayed at 0.35–0.44.
- At 06:57:07 it read 0.0 once, for 53 ms (pot noise or a touch).
- The haptic rows record the duties before the knob, so the latency figures below do not depend on it.

### Haptic latency (`latency-analysis.txt` in each run folder; first 60 s left out; outside camera losses)

The two both-mode runs had no camera losses. The depth-only run had four (05:04:25, 05:06:40, 05:08:54,
05:11:53; 3–6 s each), so its column is the analysis's "outside camera losses" section.

| ms | Stand-in, both | **Real boards, both** | Real boards, depth only |
|---|---|---|---|
| Capture → UART write: median / p95 / p99 / max | 107 / 133 / 141 / 174 | **108 / 135 / 143 / 219** | 80 / 101 / 104 / 140 |
| of which capture → depth callback queued it (median) | 106 | 106 | 80 |
| queued → taken by the serial worker: median / p99 | 0 / 10 | 0 / 10 | 0 / 0 |
| Capture → ACK noticed: median / p95 / p99 / max | 150 / 192 / 205 / 246 | 150 / 189 / 204 / 287 | 114 / 135 / 139 / 5,989* |
| Write → ACK noticed: median / p95 | 47 / 77 | 46 / 66 | 32 / 36 |
| Packets / failed writes / no ACK noticed after | 33,780 / 0 / 1 | 33,769 / 0 / 2 | 32,887 / 0 / 1 |

\* **An artefact of the pairing, not the board.**
- The two longest ACK gaps in that run, 5.90 s and 5.72 s, are exactly two of the camera losses:
  05:11:53–05:11:59 and 05:08:54–05:09:00.
- The last packet written before a loss is paired with the first ACK noticed after the camera returns.
- Its frame was captured before the "Camera lost" line, so the exclusion does not catch it.
- With all rows (losses included), that run's capture → write maximum is 569 ms.

**The board's own reply,** timed directly with the app stopped (`ack-probe/`, 06:04, 200 heartbeats one at
a time at 115200 baud):
- 200 of 200 answered;
- round trip from Pi write to ACK read: median 1.05 ms, p95 1.07, p99 1.23, max 2.29.

**Against the thesis target of ≤ 320 ms from capture to vibration** (Gao et al., 2025):
- in the real-boards run, the largest capture → UART write was 219 ms and the largest capture → ACK noticed
  was 287 ms, over 33,769 packets;
- these stop at the UART write or ACK, not at motor onset, which is not measured.

What the haptic numbers show:
- **Capture to UART write.** With both models running, a motor packet leaves the Pi a median 108 ms after
  the camera captured its frame (p99 143, max 219, over 33,769 packets). 106 ms of that is camera to depth
  callback. The hand-off to the serial worker adds a median 0 ms, p99 10.
- **"ACK noticed" is the worker's polling, not the board.** The board answers in about 1 ms, and the worker
  notices the reply one frame later. Capture → ACK noticed is therefore an upper bound on capture → board
  received; capture → write + ~1 ms is the better estimate.
- **Depth only** is 28 ms faster at the median, with one network on the chip.
- **Camera drops recurred.** The depth-only run lost the camera four times in eight minutes, with the same
  "Could not read from resource" error as the drops first seen on 2026-10-15. Whether anyone touched the
  camera then is not recorded. The two both-mode runs had none.

**Not measured:**
- motor onset, the mechanical response after the board receives the packet;
- sound at the ear;
- capture → speech start in B3. Neither both-mode run announced a detection, so `audio.csv` has no rows.
  That figure needs a scene with objects: B4.

## B4: not run

**Not run, on the thesis writing plan Kenth relayed on 2026-11-12.** That plan answers Mam Vanessa #1 with:
- the targets: ≥ 25 FPS, and ≤ 320 ms from capture to vibration;
- Session A's latency per model;
- B3's capture → motor packet figures.

Speech delay goes in Limitations, with no number. The protocol is kept below in case it is wanted later; the
B2 log already supports it.

**The brief's protocol:**
- at least 20 trials of a person stepping into view about 2 m ahead and then leaving;
- more than 10 s between trials, so the reminder rules don't suppress announcements;
- reported per trial and as median / p95 / max:
  - (a) first qualifying frame → speech start;
  - (b) decision frame → speech start;
- not judged against 320 ms; the team sets the audio target in writing.

The B2 log records (a) and (b) itself, so nobody times anything by hand. It would need a person to step in
and out, a clear, lit area about 2 m in front of the camera, and Kenth's go-ahead for a person to be in view.
No image is saved.

## Device state now

- **`second-vision.service` is stopped;** Kenth restarts it.
- **PipeWire earphone volume** was lowered from 0.78 to 0.25 at Kenth's request (`device-changes.txt`).
  Restore it with `XDG_RUNTIME_DIR=/run/user/1000 wpctl set-volume @DEFAULT_AUDIO_SINK@ 0.78`.
- **Panel knobs** are physical: strength ~0.4, volume at minimum.

## Draft decision entry, for Kenth to review

> ## D44 (draft): Latency is measured by an opt-in log; the 13-class model keeps the 18 Oct real-time figures
>
> *From Session B on the device, 2026-11-12 (`results/panel-revisions/session-b/`). Numbered after Session
> A's D43 draft; renumber if needed.*
>
> **Decision.**
> - End-to-end timing is measured by `core/latency_log.py`, switched on with `SV_LATENCY_LOG=1` and off by
>   default.
> - It links queued items to their timings by identity in a side table, so the queue contract (Rule 2) is
>   unchanged.
>
> **Measured** with the deployed 13-class model, both models, over 20 minutes, against 18 Oct:
> - 29.6 FPS average; 15-frame windows median 30.0, p1 20.4;
> - capture → depth callback median 106 ms, p99 136;
> - app CPU 77 %, no throttling.
>
> With the real boards:
> - capture → UART write of the motor packet: median 108 ms, p99 143, max 219;
> - the glasses board answers in ~1 ms (median 1.05 ms, 200/200).
>
> **Not measured:** motor onset, sound at the ear, and the B1 tracker cases.

## Files (`session-b/`)

| File | What |
|---|---|
| `run_b3.sh`, `wait_then_realboards.sh` | Runners. The second waits for the panel to report `M:both`, reading only |
| `analyse_latency.py`, `compare_b3.py`, `ack_probe.py` | Analysis and probe |
| `b3-standin-20min/`, `b3-realboards-20min/`, `b3-realboards-20min-depthonly/` | Per run: app log, sampler CSV and summary, Hailo temperatures, latency CSVs, analyses, plot |
| `b3-standin-1min/` | Short check of the runner before the 20-minute runs |
| `b3-comparison.md`, `ack-probe/` | Results above |
| `pytest-b2.log`, `pytest-b2-final.log` | Test runs; the final one is after the ACK-pairing fix |
| `device-changes.txt`, `*-console.txt` | Device changes; runner consoles |

**Kept on the Pi, not in git** (the brief: large raw logs stay out):
- `*/latency/haptic.csv` (2.7–3.3 MB each) and `*/latency/acks.csv`;
- the app logs and pytest logs (`*.log` is gitignored).

The analyses above are derived from them, and `analyse_latency.py` regenerates them on the Pi.
