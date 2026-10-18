# Test deviations

A record of every place a UAT case was tested with **different steps or inputs** than the
workbook gives.

## The rule (2026-10-11)

When testing, a case's steps and inputs may be changed, or replaced entirely, only if both
of these hold:

1. **The case's intent stays the same**: the core thing it tests is still what gets tested.
2. **The core behaviour of the code is not changed** to make the case pass.

Expected results are **never** changed here. Only the workbook itself, or a decision in
[DISAGREEMENTS.md](DISAGREEMENTS.md), changes what a case expects. If testing a case seems
to need a different expected result, raise it as a disagreement instead.

Record the deviation **before** reporting the result, and reference it from the result.

## Entry template

```
### DV-NNN · TC-xxx-yyy · YYYY-MM-DD · <who: person, or agent role>
- Workbook step / input (verbatim): …
- Used instead: …
- Intent preserved: <the core thing the case tests, and why it is still tested>
- Why: <reason; cite D-xx / W-xx if related>
- Result: <where the run is recorded>
```

## Known in advance

These are already decided. They become entries when a case is actually run:

- Missing scripts are replaced (D-08). `setup_env.sh` becomes `scripts/run.sh`; the
  `tests/obj_det_unit/` scripts and `init_audio.py`, `tts_test.py` and `play_alert.py`
  become UAT tests under `tests/uat/`.
- Cup (TC-013-001) and laptop (TC-013-002) are replaced by classes the model has (D-06).
- TC-001-002's confusion pairs: the workbook names only Motorcycle/Bicycle. Any other pairs
  are the tester's choice.
- TC-009-002's sweep file doesn't exist; it may be generated.
- String payloads in TC-008-004 and TC-014 become contract dicts (D-07).
- TC-009-001's `obstacle_beep.wav` step becomes "trigger an urgent detection while the device
  is speaking" (D-11).
- TC-001-003's "thinnest-sourced classes" are Trash Bins, the class with the fewest training
  images (1702).
- TC-024-002 and TC-024-003's "proceed in mock mode" is not tested; the device must never enter
  mock mode on its own (D-13).

## Entries

### DV-001 · TC-024-003 · 2026-10-17 · Tester (T-03a, attempt 1)
- Workbook step / input (verbatim): steps "1. Power on Raspberry Pi 5 / 2. Disconnect USB
  Camera"; input "Initially have the USB camera connected, then simulate a disconnection".
- Used instead:
  - The program is started by hand the way the boot launcher starts it
    (`scripts/run.sh --input /dev/video0 --width 640 --height 480 --serial-port … --config-port …`,
    boot service stopped), not by powering on the Pi.
  - The disconnection is simulated in software with `.claude/tools/usb_sim.py detach camera`
    (the kernel's USB `authorized` switch: the driver lets go and the `/dev/video*` nodes vanish),
    then `attach camera`. The one real unplug and replug is the person's step in MANUAL-TESTS.md.
  - In all runs but one, the glasses ESP32 and the control panel are pseudo-terminals: a fake
    board that ACKs and records every packet, and a fake panel that sends `M:<mode>` lines. This
    makes the motor commands on the wire readable and mode switches possible without a person. One
    run uses the real ports (`/dev/serial0`, `/dev/ttyAMA3`).
  - "A camera that comes back under a different node" (brief criterion 4) is produced by holding
    the camera's old nodes open while it is away, so the kernel numbers it anew.
  - Only the removal half is tested (REQ-CON-08). The alert half (vibration pulses and "Critical
    Error: Camera cannot be reached" every 10 s, REQ-CON-07) is task T-03c and not built.
  - "Proceed in mock mode temporarily" is not tested; its opposite is asserted (D-13, listed above
    under "Known in advance").
  - Added by the Tester's resumed run (2026-10-17), beyond the workbook's single disconnection:
    a camera that comes back and drops again while vision restarts on it (a loose connector),
    where the camera's return is made by `usb_sim.py`'s own re-attach timer (`detach --for 6`);
    and Ctrl+C or SIGTERM landing during the teardown or the restart. Some runs set
    `PYTHONFAULTHANDLER=1` (Python prints its stacks if the process crashes; no other effect), and
    one diagnostic run started the program under `gdb` to get the native stack of a crash.
  - Added by the Tester for attempt 2 (2026-10-17), beyond the workbook's single disconnection:
    - small standalone probes (fresh process each, scratch only, not part of the repo) that build
      the app's own class and its own `both` pipeline and start it on a node that cannot be
      opened (`/dev/video99`, the camera's non-capture node `/dev/video1`) or on the camera while
      another process of the Tester's streams from it (`gst-launch-1.0 v4l2src ! fakesink`), after
      an earlier pipeline ran in the process; and teardown 0 to 1000 ms after a start;
    - a mode switch from the panel while the camera's restart is under way;
    - Ctrl+C 0 to 0.8 s after "Camera found on";
    - the camera lost 0 to 0.8 s after the program's first pipeline logs "Starting pipeline";
    - a finer sweep of the "camera drops again" offsets (0.0 to 1.5 s) and of the mode-switch gaps
      (1.0 to 1.6 s in 0.1 s steps);
    - the camera held by another process while it is "found";
    - (second Tester session, 2026-10-17) three randomised runs of 25 quick detach/attach flaps each in
      one process (a loose connector; the camera away 1 to 3 s, back for 0 to 2.6 s or 0 to 0.7 s after
      "Camera found on"; fixed seeds 11, 12, 13), and one 135 s absence to see the "Still no USB camera"
      line every 60 s and the idle CPU;
    - (same session) Ctrl+C, and a panel mode switch, 0.05 to 0.4 s after the program's first
      "[MAIN] Starting pipeline", run against this code and against a scratch copy of the baseline, and a
      memory-per-restart run against a scratch copy of attempt 1 (`SV_UAT_RUN_SH` points the run at that
      copy's `run.sh`). These are observations for board item T-11, not TC-024-003 requirements.
- Intent preserved: the camera disappears from the system while the program runs, and the
  program must survive it and keep listening for a camera to appear again.
- Why: automate before asking a person (team decision 2026-10-16, T-03a brief "Updated
  2026-10-16"); D-13.
- Result: reports/T-03a/test-report-1.md; logs in reports/T-03a/evidence/.

### DV-002 · TC-024-002 · 2026-10-17 · Tester (T-03b, attempt 1)
- Workbook step / input (verbatim): step "1. Power on Raspberry Pi 5"; input "USB Camera is not
  Connected".
- Used instead:
  - The program is started the way the boot service starts it, by hand with the service stopped:
    `scripts/sv-main.sh` (the real boot launcher, `SV_SERVICE=1`, no `DISPLAY`, its `INPUT` default
    `/dev/video0`; `SERIAL_PORT` / `CONFIG_PORT` set to the stand-ins), not by powering on the Pi.
  - "Camera is not connected" is simulated in software: `usb_sim.py detach camera` before the launch (the
    kernel's USB `authorized` switch; `/dev/video*` vanish), and "the camera is connected later" is
    `attach camera` at a chosen time after the launch. The one real boot with a camera unplugged is the
    person's step in MANUAL-TESTS.md.
  - The glasses ESP32 and the control panel are pseudo-terminals (fake board that ACKs and records, fake
    panel that sends `M:<mode>`), except one run on the real ports (`/dev/serial0`, `/dev/ttyAMA3`).
  - Only the REQ-CON-08 half is tested. The REQ-CON-07 half (vibration pulses and the spoken "Critical Error:
    Camera cannot be reached" every 10 s) is task T-03c and not built.
  - "Proceed in mock mode temporarily" is not tested; its opposite is asserted (D-13, "Known in advance").
  - Added by the Tester beyond the workbook's single start-up without a camera: the camera appearing at 1 s, 10 s
    and 90 s in each of the four modes, on the same and on another node; 25 fresh starts with the camera
    appearing 0.5 to 5 s later; a camera that appears and is held by another process or drops again; a panel
    mode switch before, during and after the start; Ctrl+C / SIGTERM while waiting and right after the camera
    appears; a sweep of the camera appearing from 1 s before to 1.5 s after the launch (the program's first look
    is about 0.5 s after launch); stops 0.2 to 0.7 s after launch against this tree and a scratch copy of
    the baseline (an observation for T-11, Amendment A of the brief); the launcher run against a stub
    `run.sh` for every branch, and `main.py` run with `hailo_apps` made un-importable (`sys.modules['hailo_apps'] =
    None`) with and without `--mock`; the dropping of an unstarted Hailo pipeline in 60 fresh processes
    (scratch scripts under the session scratchpad, not part of the repo).
- Intent preserved: the program is started with no USB camera present and must stay up, keep every other part
  running, keep looking for a camera and start vision when one appears, never entering mock mode.
- Why: automate before asking a person (team decision 2026-10-16); D-13.
- Result: reports/T-03b/test-report-1.md; logs in reports/T-03b/evidence/.
