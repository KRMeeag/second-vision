# T-03a · Developer report, attempt 1

- Date / model: 2026-10-17 / developer-opus. This is a resumed run: a first run on 2026-10-16
  was cut off before it wrote a report. Its work was kept after review, and changed in three
  places (marked **[this run]** below).
- Summary: when the camera fails while the program runs, the program now stays up. A camera
  failure is an ERROR from the element named `source` directly in the live pipeline, or an
  end-of-stream on a USB-camera pipeline. It no longer calls `shutdown()`. The dead pipeline is
  torn down through `_release_old_pipeline()`, and one all-zero contract dict goes to
  `serial_queue`. A daemon thread then looks for a USB camera every 2 s. When one appears, the
  pipeline for the current mode starts on it, on the GLib main loop, in the same process. If the
  camera cannot start, the program goes back to waiting; if another element (Hailo) cannot start,
  it takes today's shutdown path. Only mock and gst-level evidence so far: no camera was
  unplugged, and the Hailo chip and the boards were not used.

## Inspected before changing

- **`src/second_vision/pipeline/app.py`** (all 1466 lines).
  - The inherited diff, all 426 changed lines, against `.claude/baselines/T-03a/`.
  - `trigger_rebuild`, `_rebuild_pipeline`, `_release_old_pipeline`, `shutdown`,
    `_force_exit_if_stuck`, `_connect_callback`, `_on_pipeline_rebuilt`, the four builders.
- **Callers, confirmed to exist.**
  - `main.py:240-272`: builds the app; `signal.signal(SIGTERM, app.shutdown)` at :250;
    `app.run()` at :272; `finally` joins the workers at :171-177.
  - `workers/config_reader.py:94-105`: `trigger_rebuild()` from the panel thread, after
    `tts_queue.offer({"announce": ...})` and `config.update`.
  - `mock/mode_cycler.py:69`: `trigger_rebuild()`, debug only.
  - `workers/serial_worker.py:139-275`: heartbeat, link state, zero on exit at :267-270; the
    strength multiply keeps 0 at 0.
  - `pipeline/callbacks.py:706-730` `_publish_motors`: drop-oldest, the same as `_stop_motors`.
- **Launch and service.**
  - `scripts/sv-main.sh`: passes `--input /dev/video0`, no `--enable-watchdog`.
  - `scripts/second-vision.service`: `KillSignal=SIGINT` (:48), `TimeoutStopSec=10` (:55),
    `Restart=always` (:57), `RestartSec=5` (:58).
- **Existing tests.**
  - `tests/test_pipeline_modes.py`: builds the app without `__init__`; stubs `gi` in the Poetry
    environment.
  - `tests/gst_helpers.py` and `tests/conftest.py`: unchanged from the baseline (`diff -q`).
- **This Pi, read-only.**
  - `udevadm info` for `/dev/video0` to `/dev/video3`: `video0` and `video2` are both
    `:capture:` nodes of the "Arducam 1080P Low Light"; `video1` and `video3` are not capture
    nodes.
  - 21 `/dev/video*` nodes.
  - `systemctl is-active second-vision.service` gave `inactive`. `pgrep -af "main.py|second_vision|Parallel-Depth"`
    found nothing but my own shell.
  - `hailort.service` is `active`, so pipelines use multi-process-service: the daemon owns the
    chip.

## Hailo docs consulted (Rule 6)

The installed library is read only. Its `gstreamer_app.py` is byte-identical to the workbench
copy (`diff -q`, 2026-10-17).

- **`gstreamer_app.py`, installed library.**
  - `bus_call` 475-495: any ERROR leads to `error_occurred = True` and `shutdown()`.
  - `on_eos` 497-505: on a non-file source it calls `shutdown()`; on a file source it calls
    `idle_add(_rebuild_pipeline)`.
  - `_rebuild_pipeline` 541-616: the step order that `_start_on_camera` copies (580-599), and
    `self.loop.quit()` on a PLAYING failure (599-603) or an exception (610-613).
  - `shutdown` 618-639: `self.pipeline.set_state(...)`, then `idle_add(loop.quit)`.
  - `run` 694-756: connects `self.bus_call` by name (698); cleanup calls
    `self.pipeline.set_state(NULL)` (742); the exit code comes from `error_occurred` (751-756).
  - `_watchdog_monitor` 394-442: schedules `_rebuild_pipeline` (435).
  - `disable_qos` is imported from `gstreamer_common` (66-68).
- **`gstreamer_helper_pipelines.py`.**
  - `SOURCE_PIPELINE` 95-229: for `usb` with `no_webcam_compression=True` the source is
    `v4l2src device=<node> name=source ! video/x-raw, width=640, height=480` (148-154). That is
    one camera element, directly in the pipeline, with no decode elements.
  - `get_source_type` 40-61: `/dev/video*` is `"usb"`, `rawusb://` is `"rawusb"`.
  - `INFERENCE_PIPELINE`: the hailonet is named `<name>_hailonet`.
- **`gstreamer_common.py` `disable_qos`** 35-54.
- **`camera_utils.py` `get_usb_video_devices`** 391-418, Linux branch 421-448.
  - Returns **paths**, in `os.listdir` order.
  - Runs one `udevadm info` per `/dev/video*` node.
  - Logs "USB camera detected" at info level, only when it finds one.
- **Workbench `.hailo/` docs.**
  - `memory/camera_and_display.md:21-28`: never assume `/dev/video0`. This is why the watcher
    takes any USB capture node.
  - `memory/camera_and_display.md:19` and `toolsets/core-framework-api.md:98-103` say it returns
    `list[int]`. That is wrong on Linux (the source returns paths). The brief noted the first
    file; the second has the same error. Not mine to edit (workbench).
  - `instructions/gstreamer-pipelines.md:164`: `--enable-watchdog` "auto-restart on pipeline
    stall". That is the only mention; the mechanism is the library's `_rebuild_pipeline`.
  - `memory/common_pitfalls.md:525`: `HAILO_OUT_OF_PHYSICAL_DEVICES`, in the context of a
    pre-launch check.
  - `toolsets/gstreamer-elements.md:7-17`: hailonet properties, including
    `multi-process-service`. No document says at which state change hailonet takes the device.
  - A grep of `.hailo/` and `SV-Docu/` for unplug, disconnect or reconnect found no guidance.
  - No prototype in `hailo_apps/python/pipeline_apps/` overrides `bus_call`.
- **GStreamer behaviour, checked by running it** (GStreamer 1.26.2, my_hailo_env,
  `/tmp/t03a_probe/probe_start_failure.py`; no real device opened).
  - A pipeline whose element fails its start returns `FAILURE`.
  - `bus.pop_filtered(Gst.MessageType.ERROR)` then returns that element's ERROR, even with a
    signal watch attached.
  - `v4l2src device=/dev/video99 name=source` gave the error from `'source'`: "Cannot identify
    device '/dev/video99'.".
  - A `filesink` into a missing folder gave the error from `'sink'`: "Could not open file ...".
  - A normal live pipeline returned `async` with no ERROR.

## Files changed

| File | What changed | Why |
|---|---|---|
| `src/second_vision/pipeline/app.py` | **Constants** (150-181): `CAMERA_SOURCE_NAME="source"`, `CAMERA_SOURCE_TYPE="usb"`, `CAMERA_POLL_SECONDS=2.0`, `CAMERA_TEARDOWN_ALARM_SECONDS=3.0`, the four camera states, `MOTORS_OFF` (the 5-key contract dict, all 0/False). | Criteria 1-4; R6 |
| same | **Class defaults** (442-446): `_camera_state`, `_camera_watch_thread`, `_camera_bad_nodes`, `_camera_good_node` **[this run]**, `_frames_at_camera_start`. | An app built without `__init__` (tests) starts in the normal state |
| same | **`bus_call` override** (994). ERROR/EOS while the camera is not OK is logged and dropped. A camera failure goes to `_on_camera_lost`. Everything else goes to `super().bus_call` unchanged. | Criterion 1; R3; R5 |
| same | **`_is_camera_failure`** (1026). **`_is_camera_element`** (1049) **[this run, factored out]**: the element is named `source` and its parent is `self.pipeline`. EOS counts as a loss only on `"usb"`. | R3; design notes 2-3 |
| same | **`_on_camera_lost`** (1064): state LOST; the teardown is scheduled on a timer, spaced by `MIN_REBUILD_INTERVAL_SECONDS`. | Not inside the bus handler whose watch it removes |
| same | **`_release_after_camera_loss`** (1080): `_release_old_pipeline()`, then node bookkeeping, then `_wait_for_camera()`, then one zero dict. **[this run]** If frames came from the node, it is remembered as the good node and earlier failed nodes are forgotten. | Criteria 3-4 |
| same | **`_release_old_pipeline_with_alarm` / `_on_teardown_slow`** (1112, 1132): if the teardown passes 3 s, a timer thread zeroes the motors and logs an error. | R1 mitigation, not a bound |
| same | **`_wait_for_camera`** (1140): parks an empty `Gst.Pipeline("no_camera")`, sets state WAITING, starts the watcher. | Design note 7; R5: a later stop exits 0 with a clean log |
| same | **`_stop_motors`** (1156): `put_nowait`, drop-oldest on a full queue. | Criterion 3, existing contract |
| same | **`_camera_watch` thread / `_on_camera_found`** (1184, 1216). The thread looks every 2 s, only while WAITING, and hands the result over with `GLib.idle_add`. `_on_camera_found` respects the rebuild spacing. | Design note 6; R6; Rule 4 |
| same | **`_pick_camera`** (1229) **[this run]**: prefers the node frames last came from, not `self.video_source` (the node last tried); then the lowest number; failed nodes go last, and all are retried once every node has failed. | See "Changes made in this run" |
| same | **`_start_on_camera`** (1256). The library's rebuild steps, without `loop.quit()`. It sets `video_source` to the node found. A camera start failure goes back to WAITING. **[this run]** An ERROR from a non-camera element goes to `super().bus_call`, today's shutdown path (1314-1339). | Criterion 4; R4; brief "errors from other elements" |
| same | **`trigger_rebuild`** (715): no-op while the camera is not OK (the mode is already in config). **`_rebuild_pipeline`** (761): skips while not OK; if the camera node vanished, waits instead of building. | Criterion 2; R4; R7 |
| same | **`shutdown()` docstring** (918): text only. It said an unplugged camera lands there, which is no longer true. | Rule 7: keep the doc true |
| `tests/test_camera_loss.py` | New file, 804 lines, 17 test functions (23 cases), all marked `gst`. **[this run]** Two tests added, one extended, one updated (see the next table). | Brief "Testing notes" |

Nothing else changed. The diff over `src tests scripts` against the baseline lists only these
two files. `python3 .claude/tools/check_outside_writes.py .claude/baselines/T-03a/.stamp`
reports nothing changed for "Installed libraries and environments" or "Other checkouts and the
workbench". Protected files (`core/depth_utils.py`, `core/haptics.py`, `callbacks.py`, the
workers, `scripts/`, `firmware/`) were not touched.

### Changes made in this run, and why

1. **A restart that a non-camera element fails now shuts down, as before.** The inherited code
   treated every failed start as the camera's and retried every 2 s. If a hailonet could not get
   the device, the program would stay "alive" with no vision, and systemd would never restart it.
   The brief says errors from any element other than the camera keep today's path. The fix pops
   the failing element's ERROR from the new pipeline's bus (checked by running, above). If that
   element is not the camera, the ERROR goes to the library's `bus_call`, which sets
   `error_occurred` and calls `shutdown()`; systemd then restarts the program and the device is
   handed back cleanly.
2. **Node preference after a failure.** The inherited `_pick_camera` preferred `self.video_source`,
   which is the node last *tried*. After the camera's two nodes had both failed once, it went back
   to the wrong one (shown below: it returned `/dev/video2`). It now prefers the node frames last
   came from, and clears old failure marks when frames flow.
3. **A stop just after the error, before the teardown runs.** The clean-stop test was extended to
   cover this window, and to assert that the teardown and the watcher then never run. This is
   new coverage of behaviour that was already correct: it passes on the inherited code too.

A test fix during this run: my first version of the bookkeeping test tore down the empty
placeholder pipeline. That printed two GStreamer CRITICALs ("Bus … has no signal watches
attached"). The real program cannot reach this, because a loss is only recorded from a real
camera pipeline's bus. The test now gives each loss a watched camera pipeline, and the raw
output (`-s`) has no CRITICAL and no traceback.

## Tests added or updated

All in `tests/test_camera_loss.py`, marked `gst` (they skip in the Poetry environment). Real
GStreamer and the real hailo_apps `GStreamerApp` are used; the app is built with `__new__`; a
`videotestsrc name=source` stands in for the camera; the camera lookup is scripted. Each
docstring says it covers the **removal half of TC-024-003 only**.

"Baseline" = run against `.claude/baselines/T-03a/src`. "Inherited" = run against the app.py the
first run left (sha256 `31bef133…`, saved at `/tmp/t03a_inherited/app.py`).

| Test (`test_tc024_003_con08_…`) | What it checks | Fails on baseline? | Fails on inherited? |
|---|---|---|---|
| `camera_error_keeps_program_up_stops_motors_and_looks_for_camera[both, depth, detection, none]` | Camera ERROR in each mode: no `shutdown()`, `error_occurred` False, workers not stopped, dead pipeline NULL, exactly one zero dict, lookup only on the `camera-watch` thread, no mock thread | yes (4) | no |
| `eos_from_live_camera_is_a_camera_loss` | EOS posted by the pipeline, on a USB source, is a loss | yes | no |
| `messages_after_the_camera_error_are_one_loss` | The follow-up ERRORs (source, sink) and EOS cause one loss, one zero, no shutdown | yes | no |
| `error_from_another_element_still_shuts_down` | Guard: a non-camera ERROR still shuts down, sets `error_occurred`, starts no watcher | no (guard) | no |
| `file_source_eos_keeps_library_looping` | Guard: EOS on a file source still loops through `_rebuild_pipeline` | no (guard) | no |
| `mode_switch_during_loss_is_recorded_not_built` | `trigger_rebuild()` from another thread and a queued `_rebuild_pipeline()` build nothing; the mode is recorded | yes | no |
| `panel_mode_switch_during_loss_is_announced` | The real `config_reader_worker` on a fake port: "depth mode" reaches `tts_queue`, the mode is recorded, the worker stays alive, nothing is built | yes | no |
| `glasses_link_keeps_heartbeat_and_motors_stop` | The real `serial_worker` on a fake ESP32: the zero packet is written after the last reading, at least 2 heartbeats follow, no "stopped acknowledging" | yes | no |
| `camera_return_restarts_in_current_mode_on_new_node` | The camera returns as video4 and video6 after a switch to depth during the loss: restart in depth on `/dev/video4`, frames flow, `watchdog_paused` cleared, no `loop.quit` | yes | no |
| `camera_node_order_prefers_previous_then_lowest` **[updated]** | Node order, including: both nodes failed and the last try was video2, so it starts over on video0 | yes | **yes** |
| `loss_records_which_node_gave_frames` **[new]** | At a loss: frames came, so the node is good and the failure marks are cleared; no frame and the node is still there, so it is marked; node gone, so not marked | yes | **yes** |
| `failed_restart_goes_back_to_waiting_and_retries` | A real v4l2src that cannot open `/dev/video99`: back to waiting, no `loop.quit`, then a start on the next node | yes | no |
| `restart_failed_by_another_element_shuts_down_as_before` **[new]** | The restart fails in a non-camera element (a `filesink` named `det_inference_hailonet` that cannot open its file): `shutdown()` once, `error_occurred` True, no retry | yes | **yes** |
| `three_losses_in_a_row_each_recover` | Three unplug and replug cycles: one zero each, 3 builds, never a shutdown | yes | no |
| `mode_switch_rebuild_after_camera_vanished_waits` | A queued mode-switch rebuild whose camera node is gone: waits, never the library's `loop.quit()` | yes | no |
| `slow_teardown_still_stops_motors` | A teardown over the alarm time queues a zero and logs; a fast one cancels the alarm | yes | no |
| `clean_stop_while_camera_missing_exits_0[sigint, sigterm × while-looking-for-camera, before-teardown]` **[extended]** | The library's real `run()`: the camera fails, then SIGINT or SIGTERM through `app.shutdown`. Exit code 0, `shutdown_event` set, watcher ended (or never started, before teardown), "Exiting successfully", no "Error during cleanup", no "MOCK" | yes (4) | no (new coverage of correct behaviour) |

## Commands run and their actual results

Worktree `/home/sv-rpi5/Projects/Second-Vision/worktrees/kenth`.

```
# Inherited code, before any change of mine
my_hailo_env/bin/python -m pytest tests/test_camera_loss.py -v
  19 passed in 19.43s
my_hailo_env/bin/python -m pytest tests/ -q -p no:cacheprovider
  433 passed, 1 skipped in 82.56s (0:01:22)
~/.cache/pypoetry/virtualenvs/second-vision-repo-wwT_Rtha-py3.13/bin/python -m pytest tests/ -q -p no:cacheprovider
  388 passed, 46 skipped in 37.63s

# Inherited tests against the baseline (/tmp/t03a_red = baseline src + current conftest, gst_helpers, test file, pyproject.toml)
my_hailo_env/bin/python -m pytest tests/test_camera_loss.py -q -p no:cacheprovider
  17 failed, 2 passed in 72.82s (0:01:12)

# Final code
my_hailo_env/bin/python -m pytest tests/test_camera_loss.py -q -s -p no:cacheprovider   (raw output grepped)
  23 passed in 24.97s; 0 lines matching critical|segmentation|Traceback
my_hailo_env/bin/python -m pytest tests/test_camera_loss.py -q -p no:cacheprovider   (three times in a row)
  23 passed in 24.93s / 23 passed in 24.88s / 23 passed in 24.84s

# Final tests against the baseline (/tmp/t03a_red; its app.py checked identical to the baseline with diff -q)
  21 failed, 2 passed in 75.82s (0:01:15)
  (passing: error_from_another_element_still_shuts_down, file_source_eos_keeps_library_looping)

# Final tests against the inherited app.py (/tmp/t03a_inh = current src with app.py sha256 31bef133...)
  3 failed, 20 passed in 29.17s
    :552 AssertionError: assert '/dev/video4' == '/dev/video6'                     (node order)
    :590 AttributeError: ... no attribute '_camera_good_node'                     (bookkeeping)
    :650 AssertionError: a restart failed by a non-camera element did not take the shutdown path
  inherited _pick_camera(["/dev/video2","/dev/video0"]), both failed, last try video2 -> /dev/video2

# Final full suite, both environments
my_hailo_env/bin/python -m pytest tests/ -q -p no:cacheprovider -rs
  SKIPPED [1] tests/conftest.py:415: device test skipped: the device level is opt-in; add --run-device ...
  437 passed, 1 skipped in 88.50s (0:01:28)
~/.cache/pypoetry/virtualenvs/second-vision-repo-wwT_Rtha-py3.13/bin/python -m pytest tests/ -q -p no:cacheprovider -rs
  SKIPPED [45] tests/conftest.py:419: gst test skipped: the GStreamer Python bindings (gi / Gst) are not installed ...
  SKIPPED [1] x4 tests/test_harness.py:577/588/623/1028: pytest-timeout is not installed in this environment
  SKIPPED [1] tests/conftest.py:415: device test skipped: the device level is opt-in ...
  388 passed, 50 skipped in 37.23s

# Cost of one camera look (R6), this Pi, camera attached, 21 nodes, 5 looks
  wall 96-97 ms, CPU 98-99 ms (self + udevadm children), found ['/dev/video2', '/dev/video0']
```

Why the baseline fails: 19 of the 21 fail on behaviour. Examples: "the program never started
looking for a camera"; "the run ended (exit 1) before the stop could be sent"; "the library's
rebuild quit the main loop (the program would end)"; "expected exactly one restart attempt …".
Two fail with `AttributeError` for helpers that do not exist there: `_pick_camera` and
`_release_after_camera_loss`.

## Acceptance criteria: self-check, not approval

| # | How it is addressed | Evidence level reached |
|---|---|---|
| 1 No crash | A camera ERROR or EOS never reaches the library's shutdown. The pipeline is torn down and the program waits, in all four modes, including "none". | mock-integration (gst level). Device and user evidence still needed. |
| 2 Everything else keeps running | The panel, glasses-link and speech threads are not stopped (`shutdown_event` stays clear). The heartbeat continues with the real serial worker. A panel switch is announced and recorded with the real panel worker, builds nothing, and never calls `loop.quit`. | mock-integration. The real `tts_worker` (espeak) was not run; the test stops at `tts_queue`. |
| 3 Motors not left on a stale reading | Exactly one `MOTORS_OFF` dict per loss, through `serial_queue`, after the teardown. The real serial worker writes `AA 01 00 00 00 01` after the last reading. | unit + mock-integration. Device and user evidence still needed. |
| 4 Keeps checking; restarts on a camera | Looks every 2 s on a thread. Restarts in the current mode, on the node found (another node works), in the same process. A failed camera start goes back to waiting. Hailo re-acquisition uses `_release_old_pipeline()` before the start, and `hailort.service` is active. | mock-integration. **Hailo re-acquisition is not tested at all** (no chip used). |
| 5 Never mock mode | No mock path in the new code. Tests assert no `mock*` thread and no "MOCK" output. | unit + mock-integration. A device log is still needed. |
| 6 Clean stop still works | Through the library's real `run()`: SIGINT and SIGTERM, both while looking for a camera and before the teardown, exit 0 with the watcher ended. Full suite passes in both environments. | mock-integration + unit suite. Not timed against `TimeoutStopSec=10`; "no leftover process" needs a real run. |

## Not done, limitations, deviations from the brief

- **The camera-return start does not use the library's `_rebuild_pipeline()`.**
  `_start_on_camera` repeats its steps in the same order, without `loop.quit()` on failure.
  Design note 5 allows this, and the brief's In-scope line ("rebuild through the existing
  `_rebuild_pipeline()` / `trigger_rebuild()` path") is met only for mode switches. Reason: R4,
  the library ends the program when that start fails.
- **Mode switches with the camera present are unchanged**: the interval, coalescing, release
  ordering and resets. The only additions are the two early returns and the node-missing check
  before `super()._rebuild_pipeline()`.
- **Messages from any element are dropped after the camera has failed** (state not OK), not only
  the camera's. They come from the pipeline that is being torn down (the source's own "Internal
  data stream error", EOS, downstream complaints). A Hailo ERROR that arrives *before* the
  source's error still takes the shutdown path, because classification is by element (R3).
- **Only `usb` sources are handled.** `rawusb://`, which is also a v4l2src, keeps the library's
  behaviour. The launcher passes `/dev/video0`, which is `usb`.
- **An exception while building the restart pipeline is treated as the camera's**: the program
  keeps looking and logs a warning each time. In these pipelines `parse_launch` does not open the
  device, so such an exception would be a bug, repeated every 2 s. See question 4.
- **Node heuristics beyond the brief**: `_pick_camera` and the good/failed node bookkeeping.
  They are there because this Pi's camera has two capture nodes and the lookup lists `video2`
  first.
- **Two zero commands instead of one** when the teardown passes 3 s (the alarm's and the normal
  one). Both are zero.
- **No periodic info-level log while waiting.** The lookup logs "No USB camera yet" only at
  debug level. At info, the journal shows "Camera lost: … Looking for a USB camera every 2 s"
  once, then "USB camera detected" and "Camera found on … — starting" when a camera returns.
  See question 5.

## Untested or simulated behaviour

Everything below needs the Tester's device level or a person.

- **The real unplug message on the bus.** Simulated by posting the 2026-10-15 log's error text
  from an element named `source`.
- **A real v4l2src teardown after an unplug**, the R1 deadlock and the R2 segfault. Not
  exercised: the tests stream from `videotestsrc`, and v4l2src only ever fails to open a missing
  node. No segfault and no hang were seen in any run.
- **Hailo release and re-acquisition**, `HAILO_OUT_OF_PHYSICAL_DEVICES`, and what a hailonet
  failure really looks like at start. The non-camera start failure is simulated with a `filesink`
  that cannot open its file.
- **Time from replug to vision.** Expected: up to 2 s to the next look, about 2 s spacing after
  the teardown, then the pipeline start. Not measured.
- **The real `tts_worker` and espeak**, the real UARTs, the glasses board holding zero, and the
  wearer feeling the vibration stop.
- **Whether `/dev/video2` can serve raw video.** The first run wrote "H.264 only". I did not
  re-check it, because that would mean opening the camera. The code does not depend on it.
- **Exit time and leftover processes** under the boot service. The `--use-frame` display process
  was not exercised.

## Risks and questions for the Lead

1. **R1: the teardown is not fully bounded, so I am reporting it as the brief asks.**
   - What is bounded: the camera-loss teardown is `_release_old_pipeline()`, the same code every
     mode switch uses. Its waits are bounded (`get_state` 5 s per hailonet and 5 s for the
     pipeline, plus 1 s settle).
   - What is not: `set_state(NULL)` itself cannot be interrupted.
   - If it deadlocks: the alarm zeroes the motors after 3 s and logs an error. Speech, the
     glasses link and the panel keep running (they are threads). Vision never comes back.
   - Stopping it then: Ctrl+C or SIGTERM is not handled until `set_state` returns, because
     Python signal handlers run on the main thread. Only the unit's `TimeoutStopSec=10`, then
     SIGKILL, ends it, with the motors already at zero.
   - I added no exit fallback (R1 forbids inventing one). Accept, or decide otherwise.
2. **R4 residual.** A queued mode-switch rebuild can fire after the camera has gone but before
   udev removes its node. It then still goes through the library's rebuild. If v4l2src cannot
   open the node, the library calls `loop.quit()` and the program exits with code 0.
   `Restart=always` brings it back 5 s later, which is today's behaviour. The window is "until
   the node disappears"; not measured. Closing it fully would mean replacing the library's
   rebuild in the mode-switch path too, which the brief keeps unchanged.
3. **A restart that a non-camera element fails now shuts down (exit 1).** This is my reading of
   "errors from any element other than the camera source keep today's behaviour". The
   alternative, retrying forever, would hide a stuck Hailo device from systemd. Confirm or
   overrule.
4. **Exceptions while building the restart pipeline**: keep looking (current), or shut down?
5. **Should there be an occasional info-level "still no camera" line** (for example every
   60 s)? Today, "keeps checking" is visible only at debug level. A line every 2 s would flood
   the journal, which already struggles with the Hailo driver warnings.
6. **Documentation (Rule 8)**, for the Lead to put to the user:
   - `documentation/ARCHITECTURE.md`: a camera loss no longer ends the program; how it waits and
     restarts.
   - `documentation/DECISIONS.md`: EOS on a USB source counts as a camera loss; non-camera
     errors still shut down; the 2 s look interval.
   - Separately, the workbench docs are wrong about the return type of `get_usb_video_devices()`
     (`camera_and_display.md:19`, `core-framework-api.md:100`).
7. **Scratch folders left for the Tester**, all under `/tmp`:
   - `/tmp/t03a_red`: baseline `src/` + current tests.
   - `/tmp/t03a_inh`: inherited `app.py` + current tests.
   - `/tmp/t03a_inherited/app.py`: the inherited copy.
   - `/tmp/t03a_probe`: the GStreamer probe.
