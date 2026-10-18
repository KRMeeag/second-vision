# Manual tests: every UAT case that needs a person

Some UAT cases can't be judged by a program. Someone has to plug or unplug hardware, set up
a scene in front of the camera, listen to the earphones or feel the motors. This file lists
all of them, says how a hands-on session runs, and holds the step-by-step procedure for each
task that needs one.

- The **case list** at the end is generated from the workbook snapshot and
  [TRACEABILITY.md](TRACEABILITY.md) by `tools/manual_tests.py`; don't edit it by hand. It
  quotes the workbook verbatim. Where the team corrected a case
  ([DISAGREEMENTS.md](DISAGREEMENTS.md)) or a test ran with different steps
  ([DEVIATIONS.md](DEVIATIONS.md)), the requirement it cites wins.
- The **task procedures** are written by the Lead, from the Tester's "Manual steps needed",
  before asking anyone to do them.

## Before a hands-on session

1. **Stop the boot service.** The person running the session does it; agents never do:
   `sudo systemctl stop second-vision`. Otherwise it holds the camera, the Hailo chip and both
   serial ports. Check: `systemctl is-active second-vision.service` says `inactive`.
2. **No leftover pipeline.** `pgrep -af "main.py|Parallel-Depth"` prints nothing. If a stale
   process still holds the Hailo chip, an agent may run `sudo fuser -v /dev/hailo0` and then
   `sudo fuser -k /dev/hailo0`, the one sudo command agents are allowed.
3. **Connect what the procedure lists**: the camera, the glasses ESP32 (`/dev/serial0`), the
   control panel (`/dev/ttyAMA3`), the earphones.
4. **Afterwards, start the service again**: `sudo systemctl start second-vision`. Until then
   the device does nothing at boot.
5. **If the camera seems gone after a simulated unplug**, run
   `sudo .claude/tools/usb_sim.py attach --all`, or replug it. A reboot also restores it.
   The tool re-attaches by itself after its `--for` time anyway.

## How a session runs

- The Lead gives **one step at a time**, in plain words. The person does it and says what
  they saw, heard or felt.
- The Lead records each answer **quoted**, with the time, in the task's decision file
  (`reports/<task>/decision-<n>.md`) as `user-observed` evidence, next to what the logs
  showed at that moment.
- A step that can't be done (hardware missing, the person unsure) is recorded as `BLOCKED`,
  never guessed.
- Anything unexpected is reported to the person straight away, even if it's outside the
  step. Example: the camera dropping out when nobody unplugged it.

## Waiting for a person

The Lead adds a task here when every check that can run without a person has passed, and
only the person's confirmation of the real device is left. Oldest first; each links to its
procedure below. Do them in one hands-on session if you can.

| Task | What the person does | Procedure | Since |
|---|---|---|---|
| T-03a | One real camera pull and replug, while the program runs (REQ-CON-08, the camera-removed case TC-024-003, no-crash part) | [T-03a](#t-03a--camera-removed-while-running--tc-024-003-its-no-crash-part) | 2026-10-17 |
| T-03b | One real start with the camera unplugged, then plugging it in (REQ-CON-08 absent-at-start-up half; TC-024-002 no-crash part). Same session as T-03a | [T-03b](#t-03b--start-up-without-a-camera--tc-024-002-its-no-crash-part) | 2026-10-17 |

## How much of this can be automated

Of the 66 cases in the workflow, most need a person only to *cause* something (pull a
cable, flip a switch, wait for the battery) or to *read* something a program can also read
(what was said, which motor command was sent). A test harness can do both (board task
T-10). That leaves people to confirm each real device once and to judge what only the senses
can.

Automating doesn't change the evidence rules: a test with a fake board never proves the
real board works (TRACEABILITY.md, "Evidence").

| Group | Cases | The person does today | Automated replacement | Still needs a person |
|---|---|---|---|---|
| Links and devices failing | TC-018-003, TC-022 (7), TC-023 (4), TC-024 (4), TC-025 (7): **23** | Unplugs and replugs a board, the camera or the earphones; listens and feels | Fake boards that stop answering; an injected camera error; a faked missing audio device. The test checks what is said and which motor packets go out (T-10) | Once per device: a real unplug and replug, to confirm the program sees what the fake produced |
| Control panel | TC-030 (4), TC-031-001, 002, TC-032-001, 002: **8** | Flips switches, turns knobs; listens and feels | A fake panel sends the switch and knob lines. The test checks the mode, the speech volume and the motor values (T-10b) | One quick pass with the real panel |
| Battery and power-off | TC-028 (5), TC-029 (2), TC-026-002: **8** | Waits for the battery to drain; presses the power button | Fake battery readings and a simulated shutdown signal; checks each announcement and the clean stop | The full rundown (REQ-PERF-01) and one real button press |
| Detection producer and stress | TC-013 (2), TC-015-001: **3** | Holds objects up; moves them in and out of view for 3 minutes | The real pipeline on the Hailo chip, fed a recorded video clip instead of the camera | Recording the clip, once |
| Depth scenes | TC-004 (3), TC-005 (3), TC-006-003, TC-016 (3), TC-017 (2): **12** | Sets up walls, chairs, poles and floors at set distances | Record each scene once with the depth capture tool (`SV_CAPTURE=1`), then replay the frames through the depth chain in tests. Most cases already have unit tests on synthetic scenes | One recording session |
| Senses | TC-007-003, TC-008 (3), TC-009 (3), TC-031-003, TC-032-003: **9** | Listens for clarity, popping and delay; feels the vibration strength | Record what is sent to the earphones to measure delay, clipping and gaps, and check the words spoken | The judgement of how it sounds and feels |
| Power hardware | TC-026-001, 003, TC-027-001: **3** | Powers the Pi on and off; reboots it | After the person acts, the agent checks the logs | The power action |

## Task procedures

### T-03a · Camera removed while running · TC-024-003 (its no-crash part)

*Status: **ready to run** (the Tester's automated checks passed, [decision-2](reports/T-03a/decision-2.md), 2026-10-17). Steps from the Lead's brief ([reports/T-03a/brief-1.md](reports/T-03a/brief-1.md)).
**Before step 2, switch the real panel's speech and vibration switches on**: in every automated run with the real panel they were off, so nothing could be heard or felt.
Run after the Tester's automated checks pass. The repeated unplugs, and stopping the program
while the camera is gone, are covered on the device by the simulated unplug
(`.claude/tools/usb_sim.py`). This is the one real-camera confirmation.*

Needs the camera, the Hailo chip, the glasses ESP32, the control panel and the earphones,
with the boot service stopped.

| # | Who | Step | Answer to record |
|---|---|---|---|
| 1 | Lead | Starts this branch the way the boot service would | — |
| 2 | Person | Hold an object about 0.5 m in front of the camera | Do you feel vibration? |
| 3 | Person | Unplug the camera, with the object still there | Does the vibration stop within a few seconds? (The Lead checks the program is still running.) |
| 4 | Person | Wait about 30 s, then flip a panel mode switch | Do you hear "<mode> mode"? |
| 5 | Person | Plug the camera into a **different** USB port, then show the object again | Does the vibration come back? |

### T-03b · Start-up without a camera · TC-024-002 (its no-crash part)

*Status: **ready to run** (the Tester's automated checks passed, [decision-1](reports/T-03b/decision-1.md), 2026-10-17). Do it in the same session as T-03a's, after it.
Steps from the Tester's "Manual steps needed" ([test-report-1](reports/T-03b/test-report-1.md)). The repeated starts, stops and camera
timings are covered on the device by the simulated unplug (`.claude/tools/usb_sim.py`); this is the one real power-on without a camera.*

Needs the glasses ESP32, the control panel and the earphones, with the boot service stopped and the camera **unplugged**.
**Switch the real panel's speech and vibration switches on first.**

| # | Who | Step | Answer to record |
|---|---|---|---|
| 1 | Person | With the camera unplugged, say "go"; the Lead starts the program the way the boot service does (`scripts/sv-main.sh`, service stopped) | — |
| 2 | Person | Wait about 2 minutes with the glasses on | Do the three motors stay still (no vibration at all)? Does the Lead see the program still running? |
| 3 | Person | After about 30 s of waiting, flip a panel mode switch | Do you hear "<mode> mode"? |
| 4 | Person | Plug the camera in, and hold an object about 0.5 m in front of it | Does vibration start within a few seconds, and is it in the mode the panel shows? |
| 5 | Person | Unplug the camera once more | Does the vibration stop, and does the Lead see the program waiting again (still running)? |
| 6 | Lead (optional) | One start with a screen attached (`DISPLAY` set) and no camera | Does it wait without errors? |

## Case list

<!-- BEGIN GENERATED by tools/manual_tests.py: do not edit by hand -->

### In the agent workflow (`assisted`: a person sets up the scene or plugs hardware; `manual`: a person judges by ear or feel): 66 cases

**M02 · Depth Estimation**

*TC-004 · Depth reading per zone*

#### TC-004-001 · assisted · REQ-DEP-01

- **Steps:** Run the pipeline with the camera pointed down an empty corridor; observe the L/C/R values in the depth console output
- **Input:** Open corridor, nothing within 3 m
- **Expected (workbook):** All three zone values stay at or near 0 — open space produces no warning

#### TC-004-002 · assisted · REQ-DEP-02

- **Steps:** Walk the camera slowly from about 3 m toward a wall until roughly 0.3 m, then back away
- **Input:** Blank wall, straight approach
- **Expected (workbook):** Zone values rise steadily as you approach and fall as you back away; the value never drops while you are still closing in

#### TC-004-003 · assisted · REQ-DEP-03

- **Steps:** Place a chair about 1 m away in the left of frame only, then move it to the right of frame
- **Input:** Chair at 1 m, left then right
- **Expected (workbook):** Only the left zone rises for the left placement, only the right zone for the right placement; the opposite side stays at or near 0

*TC-005 · Obstacle types and false alarms*

#### TC-005-001 · assisted · REQ-DEP-04

- **Steps:** Stand the camera about 1 m from a plain, textureless wall filling the view
- **Input:** Blank white wall at 1 m
- **Expected (workbook):** The centre zone reads a strong warning and the debug screen tags the zone as a near surface — the wall is not read as open space

#### TC-005-002 · assisted · REQ-DEP-05

- **Steps:** Hold a broomstick or cane vertically about 1 m in front of the camera, off to one side
- **Input:** Thin pole at 1 m
- **Expected (workbook):** The zone containing the pole raises a warning even though the object is narrow and most of the zone is far

#### TC-005-003 · assisted · REQ-DEP-06

- **Steps:** Point the camera at a textured but open scene, a room with furniture no closer than 3 m
- **Input:** Open room, nothing within 3 m
- **Expected (workbook):** No zone raises a warning; furniture and floor texture do not produce a false near reading

*TC-006 · Ground hazard detection and output stability*

#### TC-006-003 · assisted · REQ-DEP-09

- **Steps:** Go back to the indoor test scene and set the camera on a table pointing toward an unmoving scene
- **Input:** Indoor unmoving scene
- **Expected (workbook):** The zone values remain constant without any blinking or jumping to totally different values.

**M03 · Bone Conduction Audio Feedback**

*TC-007 · Audio Driver Initialization & Device Recognition*

#### TC-007-003 · manual · REQ-AUD-03

- **Steps:** 1. Send test sine wave tone to the audio sink using speaker-test.
- **Input:** Terminal Command: speaker-test -c2 -t sine -f 440
- **Expected (workbook):** Clean 440Hz audible sine wave transmitted through bone conduction transducers with no clipping or driver latency.

*TC-008 · Text-to-Speech (TTS) Engine Execution & Stream Clarity*

#### TC-008-001 · manual · REQ-AUD-04

- **Steps:** 1. Run TTS script with short string input. 2. Verify output.
- **Input:** Python Command: python3 scripts/tts_test.py --text "Object detected ahead"
- **Expected (workbook):** Speech engine synthesizes text input and routes clear speech audio to transducers within <500ms.

#### TC-008-002 · manual · REQ-AUD-05

- **Steps:** 1. Run TTS script under heavy CPU/Hailo-8 inference load with rapid sequential strings.
- **Input:** Stress Command: python3 scripts/tts_test.py --stress-test --count 20
- **Expected (workbook):** TTS pipeline queues and streams audio sequentially without buffer underruns, popping, or voice distortion.

#### TC-008-003 · manual · REQ-AUD-06

- **Steps:** 1. Test TTS execution with special characters, numbers, and boundary-length text strings.
- **Input:** Input Text: "Warning: 3 obstacles at 1.5 meters! Stop #001."
- **Expected (workbook):** TTS engine normalizes numbers/symbols accurately and speaks the complete phrase cleanly.

*TC-009 · Real-Time Audio Alerts & Transducer Frequency Calibration*

#### TC-009-001 · manual · REQ-AUD-08

- **Steps:** 1. Trigger concurrent high-priority audio alert wave file while TTS stream is active.
- **Input:** Terminal Command: python3 scripts/play_alert.py --file obstacle_beep.wav
- **Expected (workbook):** High-priority alert sound plays instantaneously with clear transient response over bone conduction transducers.

#### TC-009-002 · manual · REQ-AUD-09

- **Steps:** 1. Execute frequency sweep test across 200Hz - 4000Hz range.
- **Input:** Audio Sweep File: sweep_200_4000Hz.wav
- **Expected (workbook):** Audio output remains audible and clear within mid-range speech frequencies without buzzing frame housing.

#### TC-009-003 · manual · REQ-AUD-10

- **Steps:** 1. Measure audio output delay over SSH session vs. local console command execution.
- **Input:** Remote Command: ssh pi@192.168.x.x "python3 scripts/tts_test.py --text 'Test'"
- **Expected (workbook):** SSH remote command invocation delivers audio through physical RPi 5 port without network-induced playback delay.

**M05 · Object Detection with Audio Feedback**

*TC-013 · Producer Thread - Object Detection Callback Enqueueing*

#### TC-013-001 · assisted · REQ-DTA-01

- **Steps:** Place a single recognizable object (e.g., a cup) in front of the IMX291 camera.
- **Input:** Single object in clear view (Cup)
- **Expected (workbook):** Object detection callback triggers and successfully enqueues the detection event string to the thread-safe queue.

#### TC-013-002 · assisted · REQ-DTA-02

- **Steps:** Place multiple distinct objects simultaneously in the camera frame.
- **Input:** Multiple objects (Person, Chair, Laptop)
- **Expected (workbook):** Callback processes all bounding boxes and enqueues batched or prioritized messages into the queue.

*TC-015 · Pipeline Integration, Concurrency & Resource Management*

#### TC-015-001 · assisted · REQ-DTA-07

- **Steps:** Run the full pipeline and rapidly move objects in and out of frame for 3 minutes.
- **Input:** Continuous dynamic camera feed
- **Expected (workbook):** Both threads operate concurrently without race conditions, deadlocks, or segmentation faults.

**M06 · Depth Estimation with Haptic Feedback**

*TC-016 · Spatial Handoff & Distance*

#### TC-016-001 · assisted · REQ-DHP-01

- **Steps:** 1. Object 3m→1m in front
- **Input:** Z-axis movement
- **Expected (workbook):** No feedback at 3m; center motor scales up as distance closes

#### TC-016-002 · assisted · REQ-DHP-02

- **Steps:** 1. Object sweeps left→right at 1m
- **Input:** X-axis movement
- **Expected (workbook):** Vibration transitions Left→Center→Right seamlessly

#### TC-016-003 · assisted · REQ-DHP-03

- **Steps:** 1. Position object at upper-left of frame, ~2.5m away 2. Move it diagonally toward center-near (~1m), combining lateral and forward motion 3. Observe zone assignment and intensity scaling throughout
- **Input:** Combined Z+X movement
- **Expected (workbook):** Zone classification and intensity scaling stay correct under compound motion

*TC-017 · The Patterned Floor Illusion*

#### TC-017-001 · assisted · REQ-DHP-04

- **Steps:** 1. Aim the camera at a flat, highly textured surface 2. Walk forward continuously for 5m / 30s 3. Log every haptic trigger event during the walk
- **Input:** 2D visual noise.
- **Expected (workbook):** 0 false triggers logged over the 30s window

#### TC-017-003 · assisted · REQ-DHP-04

- **Steps:** 1. Repeat the TC-017-001 procedure on a second, visually distinct texture 2. Walk forward the same duration 3. Log trigger count
- **Input:** Different pattern
- **Expected (workbook):** Still 0 false triggers

*TC-018 · System Thread Failure*

#### TC-018-003 · assisted · REQ-DHP-08

- **Steps:** 1. Run the full pipeline normally 2. Force an ESP32 brownout/reset while Pi threads keep running 3. Monitor the Pi's UART handler for timeout/error behavior over the next several send cycles
- **Input:** Inverse fault direction
- **Expected (workbook):** Pi detects the unresponsive UART peer via timeout, logs/queues an error, and does not hang indefinitely waiting on ACKs

**M08 · External Components Connectivity Check**

*TC-022 · ESP32 Vibration Motor Connection*

#### TC-022-001 · assisted · REQ-CON-01

- **Steps:** 1. Power on Raspberry Pi 5
- **Input:** ESP32 Seed Xiao is connected via GPIO Pins
- **Expected (workbook):** Device will proceed as intended to main pipeline

#### TC-022-002 · assisted · REQ-CON-02, 03 · T-01a (speech), T-00c (speech switch off, D-15)

- **Steps:** 1. Power on Raspberry Pi 5
- **Input:** ESP32 Seed Xiao is disconnected from GPIO before the device starts up. Depth estimation in the controller board is enabled.
- **Expected (workbook):** Audio output should indicate that the vibration feedback cannot be reached. This feedback should be relayed every 30 seconds.

#### TC-022-003 · assisted · REQ-CON-02, 03 · T-01a, T-00c

- **Steps:** 1. Power on Raspberry Pi 6
- **Input:** ESP32 Seed Xiao is disconnected from GPIO before the device starts up. Depth estimation in the controller board is disabled.
- **Expected (workbook):** Audio output should indicate that the vibration feedback cannot be reached. This feedback should be only stated once.

#### TC-022-004 · assisted · REQ-CON-02, 03 · T-01a, T-00c

- **Steps:** 1. Power on Raspberry Pi 5 with ESP32 Connected 2. During inference in the pipeline, the ESP32's GPIO pins should be removed
- **Input:** ESP32 Seed Xiao is disconnected from GPIO after the device starts up. Depth Estimation in the controller board is enabled.
- **Expected (workbook):** Once the connection has been removed, the audio feedback will relay that the vibration feedback cannot be reached. This feedback should be relayed every 30 seconds.

#### TC-022-005 · assisted · REQ-CON-02, 03 · T-01a, T-00c

- **Steps:** 1. Power on Raspberry Pi 5 with ESP32 Connected 2. While doing inference in the pipeline, the ESP32's GPIO pins should be removed
- **Input:** ESP32 Seed Xiao is disconnected from GPIO after the device starts up. Depth Estimation in the controller board is disabled.
- **Expected (workbook):** Once the connection has been removed, the audio feedback will relay that the vibration feedback cannot be reached. This feedback should be relayed once.

#### TC-022-006 · assisted · REQ-CON-03 · T-01a, T-00c

- **Steps:** 1. Power on Raspberry Pi 5 with ESP32 Connected 2. in the control panel, the switch for the depth estimation must be turned off
- **Input:** ESP32 is not connected to the GPIO Pins, Depth Estimation must be disabled mid-inference
- **Expected (workbook):** Upon switching, the audio output would immediately inform the visually impaired that the vibration feedback cannot be reached via audio output. This will be done every 30 seconds.

#### TC-022-007 · assisted · REQ-CON-04 · T-00b (pattern), T-01b (sentence, D-17)

- **Steps:** 1. Power on Raspberry Pi 5 with ESP32 Connected 2. During inference in the pipeline, the ESP32's GPIO pins should be removed 3. After a while, the GPIO connection should be reinstated
- **Input:** ESP32 is reconnected after a disconnection.
- **Expected (workbook):** Upon reconnection, there should be a vibration feedback and audio confirmation that everything is connected.

*TC-023 · ESP32 Controller Board Connection*

#### TC-023-001 · assisted · REQ-CON-01

- **Steps:** 1. Power on Raspberry Pi 5
- **Input:** ESP32 is connected via GPIO Pins
- **Expected (workbook):** Device will proceed as intended to main pipeline

#### TC-023-002 · assisted · REQ-CON-05 · T-02b, T-00c

- **Steps:** 1. Power on Raspberry Pi 5
- **Input:** ESP32 is disconnected from GPIO before the device starts up.
- **Expected (workbook):** Audio output should indicate that the controller board cannot be reached, and that the device will use the previously used settings. This feedback should be relayed every 5 minutes.

#### TC-023-004 · assisted · REQ-CON-05 · T-02a (settings), T-02b, T-00c

- **Steps:** 1. Power on Raspberry Pi 5 with ESP32 Connected 2. During inference in the pipeline, the ESP32's GPIO pins should be removed
- **Input:** ESP32 is disconnected from GPIO after the device starts up.
- **Expected (workbook):** Upon disconnection, output should indicate that the controller board cannot be reached, and that the device will use the previously used settings. This feedback should be relayed every 5 minutes.

#### TC-023-007 · assisted · REQ-CON-06 · T-02c (D-17)

- **Steps:** 1. Power on Raspberry Pi 5 with ESP32 Connected 2. During inference in the pipeline, the ESP32's GPIO pins should be removed 3. After a while, the GPIO connection should be reinstated
- **Input:** ESP32 is reconnected after a disconnection.
- **Expected (workbook):** Upon reconnection, there should be audio confirmation that everything is connected.

*TC-024 · USB Camera Connection*

#### TC-024-001 · assisted · REQ-CON-01

- **Steps:** 1. Power on Raspberry Pi 5
- **Input:** USB Camera is Connected
- **Expected (workbook):** Device will proceed as intended to main pipeline

#### TC-024-002 · assisted · REQ-CON-07, 08 · T-03b (start-up half), T-03c1 (spoken alert, in work), T-03c2 (beep beep, needs T-00b), T-00c (speech switch off, D-15)

- **Steps:** 1. Power on Raspberry Pi 5
- **Input:** USB Camera is not Connected
- **Expected (workbook):** There should be a quick set of vibration pulse alongside a "Critical Error: Camera cannot be reached" audio error. This should be repeated every 10 seconds In terms of the pipeline, the system should catch the no devices exception and allow the device to proceed in mock mode temporarily. Device should also listen for cameras that may pop up in the list of devices to check if everything is okay.

#### TC-024-003 · assisted · REQ-CON-07, 08 · T-03a (removal half), T-03c1 (spoken alert, in work), T-03c2 (beep beep, needs T-00b), T-00c (speech switch off, D-15)

- **Steps:** 1. Power on Raspberry Pi 5 2. Disconnect USB Camera
- **Input:** Initially have the USB camera connected, then simulate a disconnection
- **Expected (workbook):** There should be a quick set of vibration pulse alongside a "Critical Error: Camera cannot be reached" audio error. This should be repeated every 10 seconds In terms of the pipeline, the system should catch the no devices exception and allow the device to proceed in mock mode temporarily. Device should also listen for cameras that may pop up in the list of devices to check if everything is okay.

#### TC-024-004 · assisted · REQ-CON-09 · T-03c1 (merged T-03d), T-00c

- **Steps:** 1. Power on Raspberry Pi 5 2. Disconnect USB Camera 3. Reconnect USB Camera
- **Input:** Have the USB camera disconnected initially, then connect it back to the RPI.
- **Expected (workbook):** There should be a quick "camera has been reconnected" audio output and the device should return to its normal state.

*TC-025 · Earphones Connection*

#### TC-025-001 · assisted · REQ-CON-01

- **Steps:** 1. Power on Raspberry Pi 5
- **Input:** USB Earphones is connected
- **Expected (workbook):** Device will proceed as intended to main pipeline

#### TC-025-002 · assisted · REQ-CON-10, 11 · T-04a (needs T-00b; D-18)

- **Steps:** 1. Power on Raspberry Pi 5
- **Input:** USB Earphones is not connected upon startup. In the controller panel, object detection should be enabled.
- **Expected (workbook):** There should be a brief "strong weak, strong weak, strong weak" vibration output for all three vibration motors to indicate an error. This feedback should be relayed every 30 seconds.

#### TC-025-003 · assisted · REQ-CON-10, 11 · T-04a (needs T-00b; D-18)

- **Steps:** 1. Power on Raspberry Pi 5
- **Input:** USB Earphones is not connected upon startup. In the controller panel, object detection should be disabled.
- **Expected (workbook):** There should be a brief "strong weak, strong weak, strong weak" vibration output for all three vibration motors to indicate an error. This feedback should be only stated once.

#### TC-025-004 · assisted · REQ-CON-10, 11 · T-04a (needs T-00b; D-18)

- **Steps:** 1. Power on Raspberry Pi 5 with USB earphones connected 2. During inference in the pipeline, the USB connection should be removed
- **Input:** USB Earphones is disconnected from GPIO after the device starts up. Object Detection in the controller board is enabled.
- **Expected (workbook):** Once the connection has been removed, there should be a brief "strong weak, strong weak, strong weak" vibration output for all three vibration motors to indicate an error. This feedback should be relayed every 30 seconds.

#### TC-025-005 · assisted · REQ-CON-10, 11 · T-04a (needs T-00b; D-18)

- **Steps:** 1. Power on Raspberry Pi 5 with USB earphones connected 2. During inference in the pipeline, USB connection should be removed
- **Input:** USB Earphones is disconnected from GPIO after the device starts up. Object Detection in the controller board is enabled.
- **Expected (workbook):** Once the connection has been removed, there should be a brief "strong weak, strong weak, strong weak" vibration output for all three vibration motors to indicate an error. This feedback should be relayed once.

#### TC-025-006 · assisted · REQ-CON-10, 11 · T-04a (needs T-00b; D-18)

- **Steps:** 1. Power on Raspberry Pi 5 with USB earphones connected 2. In the control panel, the switch for the object detection must be turned off
- **Input:** USB earphones is not connected to the GPIO Pins, Object Detection must be disabled mid-inference
- **Expected (workbook):** Upon switching, There should be a brief "strong weak, strong weak, strong weak" vibration output for all three vibration motors to indicate an error. This will be done every 30 seconds.

#### TC-025-007 · assisted · REQ-CON-12 · T-04b (needs T-00b; D-17)

- **Steps:** 1. Power on Raspberry Pi 5 with ESP32 Connected 2. During inference in the pipeline, the ESP32's GPIO pins should be removed 3. After a while, the GPIO connection should be reinstated
- **Input:** USB earphones reconnected after a disconnection.
- **Expected (workbook):** Upon reconnection, there should be a vibration feedback and audio confirmation that everything is connected.

*TC-026 · X1202 UPS Hat Connection*

#### TC-026-001 · manual · REQ-UPS-01

- **Steps:** 1. Turn on the Raspberry Pi via the on-board power button
- **Input:** X1202 built in power button
- **Expected (workbook):** Raspberry Pi turns on

#### TC-026-002 · manual · REQ-UPS-02

- **Steps:** 1. Power on Raspberry Pi 5 2. Check built in script for X1202 connection
- **Input:** Battery Check Script
- **Expected (workbook):** Device is reading the current battery percentage

#### TC-026-003 · manual · REQ-UPS-03

- **Steps:** 1. Turn off the Raspberry Pi via the on-board power button by hold clicking
- **Input:** X1202 built in power button
- **Expected (workbook):** Raspberry Pi turns off

**M09 · On-board Power Related Checks**

*TC-027 · Power Up Sequence Verification*

#### TC-027-001 · assisted · REQ-PWR-01

- **Steps:** 1. Power on Raspberry Pi 5
- **Input:** X1202 Power Button / Momentary Power Push Button
- **Expected (workbook):** Raspberry Pi will turn on and automatically start the pipeline

*TC-028 · Usual Battery Run Sequence*

#### TC-028-001 · assisted · REQ-PWR-02 · T-06

- **Steps:** 1. Power on Raspberry Pi 5
- **Input:** Have the power percentage be at 80%
- **Expected (workbook):** There should be an audio feedback that states "Battery at 80%"

#### TC-028-001#2 · assisted · REQ-PWR-02 · T-06

- **Steps:** 1. Power on Raspberry Pi 5
- **Input:** Have the power percentage be at 5%
- **Expected (workbook):** There should be an audio feedback that states "Battery at 5%. Device might shut down soon. Recharge as soon as possible. "

#### TC-028-002 · assisted · REQ-PWR-02 · T-06

- **Steps:** 1. Power on Raspberry Pi 5
- **Input:** Have the power percentage be at 60%
- **Expected (workbook):** There should be an audio feedback that states "Battery at 60%"

#### TC-028-003 · assisted · REQ-PWR-02 · T-06

- **Steps:** 1. Power on Raspberry Pi 5
- **Input:** Have the power percentage be at 40%
- **Expected (workbook):** There should be an audio feedback that states "Battery at 40%"

#### TC-028-004 · assisted · REQ-PWR-02 · T-06

- **Steps:** 1. Power on Raspberry Pi 5
- **Input:** Have the power percentage be at 20%
- **Expected (workbook):** There should be an audio feedback that states "Battery at 20%. Recharge your Second Vision Device Soon. "

*TC-029 · Power Off Sequence*

#### TC-029-001 · assisted · REQ-PWR-03 · T-07

- **Steps:** 1. Power on Raspberry Pi 5 2. Shut down the RPI
- **Input:** Hold the power button / Double click the momentary push button
- **Expected (workbook):** Raspberry Pi shuts down. In the case of the momentary push button, it will relay a "Device shutting down." audio output first.

#### TC-029-002 · assisted · REQ-PWR-04 · T-06

- **Steps:** 1. Power on Raspberry Pi 5
- **Input:** Have the power percentage be at 2%
- **Expected (workbook):** There should be a "Low battery, device shutting down" audio output. Device should gracefully shutdown all pipelines.

**M10 · Controller Board Tests**

*TC-030 · Detection Mode Switches*

#### TC-030-001 · assisted · REQ-PNL-01

- **Steps:** 1. Power on Raspberry Pi 5 2. Turn the object detection switch to the off position
- **Input:** Off Position for Object Detection
- **Expected (workbook):** Audio feedback for identified objects will remain silent

#### TC-030-002 · assisted · REQ-PNL-02

- **Steps:** 1. Power on Raspberry Pi 5 2. Turn the object detection switch to the on position
- **Input:** On Position for Object Detection
- **Expected (workbook):** Audio feedback for identified objects be reflected in the earphones

#### TC-030-003 · assisted · REQ-PNL-03

- **Steps:** 1. Power on depth estimation switch to the off position
- **Input:** Off Position for Depth Estimation
- **Expected (workbook):** Vibration feedback from glasses will remain silent

#### TC-030-004 · assisted · REQ-PNL-04

- **Steps:** 1. Power on Raspberry Pi 5 2. Turn the depth estimation switch to the on position
- **Input:** On Position for Depth Estimation
- **Expected (workbook):** Vibration feedback will become present

*TC-031 · Volume Knob Feedback*

#### TC-031-001 · assisted · REQ-PNL-05

- **Steps:** 1. Power on Raspberry Pi 5 2. Set the potentiometer to its maximum turning radius
- **Input:** Potentiometer value at 100%
- **Expected (workbook):** The volume of the device should be at its maximum value

#### TC-031-002 · assisted · REQ-PNL-06

- **Steps:** 1. Power on Raspberry Pi 5 2. Set the potentiometer to its minimum turning radius
- **Input:** Potentiometer value at 0%
- **Expected (workbook):** The volume of the audio feedback should be silent

#### TC-031-003 · manual · REQ-PNL-07

- **Steps:** 1. Power on Raspberry Pi 5 2. Set the potentiometer to its middle turning radius
- **Input:** Potentiometer value at 50%
- **Expected (workbook):** The volume should not be too loud, but not too small as well

*TC-032 · Vibration Strength Feedback*

#### TC-032-001 · assisted · REQ-PNL-08

- **Steps:** 1. Power on Raspberry Pi 5 2. Set the potentiometer to its maximum turning radius
- **Input:** Potentiometer value at 100%
- **Expected (workbook):** The vibration of the device should be at its highest

#### TC-032-002 · assisted · REQ-PNL-09

- **Steps:** 1. Power on Raspberry Pi 5 2. Set the potentiometer to its minimum turning radius
- **Input:** Potentiometer value at 0%
- **Expected (workbook):** The vibration of the device should be minimal

#### TC-032-003 · manual · REQ-PNL-10

- **Steps:** 1. Power on Raspberry Pi 5 2. Set the potentiometer to its middle turning radius
- **Input:** Potentiometer value at 50%
- **Expected (workbook):** The vibration of the device should not be too strong, but not too weak

### ESP32 cases: run by the team by hand, outside the agent workflow: 15 cases

**M04 · ESP32 Haptic Feedback**

*TC-010 · Normal Operations*

#### TC-010-001 · esp32-manual · REQ-HAP-01

- **Steps:** 1. Open Serial Monitor 2. Send standard depth JSON 3. Observe PWM output on pins
- **Input:** {"left": 100, "center": 50, "right": 0}
- **Expected (workbook):** ESP32 parses JSON; Left motor pulses strong, Center medium, Right silent

#### TC-010-002 · esp32-manual · REQ-HAP-02

- **Steps:** 1. Open Serial Monitor 2. Send a valid JSON packet 3. Observe ESP32's TX line immediately after send
- **Input:** {"left": 50, "center": 50, "right": 50}
- **Expected (workbook):** ESP32 transmits an acknowledgment back to the Pi within a defined window (e.g. <20ms) confirming packet receipt

#### TC-010-003 · esp32-manual · REQ-HAP-03

- **Steps:** 1. Send an all-zero JSON packet, confirm silence 2. Send all three motors near-cap simultaneously 3. Monitor for voltage sag or reset during step 2
- **Input:** {"left":0,"center":0,"right":0} then {"left":150,"center":150,"right":150}
- **Expected (workbook):** Step 1: all motors off, no residual buzz. Step 2: all three fire concurrently with no voltage sag / brownout on the ESP32

*TC-011 · Voltage Protection*

#### TC-011-003 · esp32-manual · REQ-HAP-05

- **Steps:** 1. Send JSON with a negative value for left motor 2. Send JSON with a value exceeding the expected range 3. Measure actual PWM output at each step
- **Input:** {"left":-50} then {"left":9999}
- **Expected (workbook):** Both clamp safely to 0 or max — no integer wraparound producing an unintended high PWM value

*TC-012 · Data Glitches & Disconnects*

#### TC-012-002 · esp32-manual · REQ-HAP-07

- **Steps:** 1. Physically pull the UART TX cable while motors are running 2. Observe PWM outputs immediately after 3. Reconnect the cable 4. Send a new valid packet and observe motor response
- **Input:** Physical disconnect, then reconnect
- **Expected (workbook):** Disconnect: all PWM drops to 0 immediately. Reconnect: normal operation resumes on the next valid packet without requiring a manual ESP32 reset

#### TC-012-003 · esp32-manual · REQ-HAP-08

- **Steps:** 1. Keep UART physically connected throughout 2. Inject a garbled/truncated byte sequence mid-stream 3. Send a valid packet immediately after
- **Input:** Garbled byte sequence / truncated JSON while line stays connected
- **Expected (workbook):** deserializeJson() returns an error; ESP32 discards the packet and holds last-safe state (or zeroes out) — no crash, no unintended PWM spike

**M07 · BMI160 IMU (Ego-Motion / Head-Tracking)**

*TC-019 · I2C Read Reliability*

#### TC-019-001 · esp32-manual · REQ-IMU-01

- **Steps:** 1. Power on ESP32-C3 with BMI160 wired via I2C 2. Continuously poll and print gyro + accel readings for 60 seconds 3. Inspect the log for NaNs, stuck values, or out-of-range readings
- **Input:** Stationary IMU on flat surface
- **Expected (workbook):** All readings stay within sane physical ranges, no NaNs, no repeated stale/duplicate values

#### TC-019-002 · esp32-manual · REQ-IMU-02

- **Steps:** 1. Rotate the IMU through known angles (e.g. 90° pitch, 90° yaw) 2. Record reported orientation at each stop 3. Compare against the physically measured angle
- **Input:** Manual controlled rotation
- **Expected (workbook):** Reported values track physical rotation within ±5° tolerance

#### TC-019-003 · esp32-manual · REQ-IMU-03

- **Steps:** 1. Slightly loosen the I2C SDA/SCL connection to simulate a marginal wire 2. Attempt to read from the BMI160 3. Observe ESP32 behavior
- **Input:** Degraded I2C connection
- **Expected (workbook):** ESP32 detects the read failure (NACK/timeout) rather than silently returning garbage data

*TC-020 · Gyroscope Deadband & Turning Detection*

#### TC-020-001 · esp32-manual · REQ-IMU-04

- **Steps:** 1. Hold the IMU still, introducing only small hand-tremor-level jitter 2. Observe whether a head-turn signal is generated 3. Repeat for 30 seconds
- **Input:** Sub-deadband jitter
- **Expected (workbook):** No false head-turn signal sent to the Pi

#### TC-020-002 · esp32-manual · REQ-IMU-05

- **Steps:** 1. Deliberately turn the IMU past the defined deadband threshold 2. Observe the generated signal 3. Return to center and repeat in the opposite direction
- **Input:** Deliberate turn left, then right
- **Expected (workbook):** Signal fires in the correct direction past threshold, resets cleanly at center

#### TC-020-003 · esp32-manual · REQ-IMU-06

- **Steps:** 1. Tilt the IMU to the defined pitch constraint limit 2. Continue tilting slightly past that limit 3. Observe reported output
- **Input:** Pitch angle at and beyond the defined constraint
- **Expected (workbook):** Output clamps or flags at the limit rather than producing an unbounded/out-of-spec signal

*TC-021 · I2C Brownout / Bus Recovery*

#### TC-021-001 · esp32-manual · REQ-IMU-07

- **Steps:** 1. Run the IMU read loop normally 2. Briefly interrupt power to the BMI160 (or momentarily disconnect SDA) to simulate a bus stall 3. Restore the connection
- **Input:** Simulated I2C brownout
- **Expected (workbook):** ESP32 detects the failed read, doesn't hang the main loop, auto-reinitializes the BMI160 once restored

#### TC-021-002 · esp32-manual · REQ-IMU-08

- **Steps:** 1. Simulate a sustained I2C bus lockup (hold SDA low) for several seconds 2. Attempt reads throughout 3. Release the lockup
- **Input:** Extended I2C bus stall
- **Expected (workbook):** ESP32's UART handling and PWM output continue unaffected — an IMU fault must never block haptic feedback

#### TC-021-003 · esp32-manual · REQ-IMU-09

- **Steps:** 1. Repeatedly cycle the I2C connection (disconnect/reconnect) 5 times in succession 2. Observe ESP32 stability and IMU re-acquisition each time
- **Input:** Repeated I2C fault cycling
- **Expected (workbook):** No crash, memory leak, or permanent failure state; IMU re-initializes successfully every cycle

<!-- END GENERATED -->
