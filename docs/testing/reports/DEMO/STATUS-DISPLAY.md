# The failsafe checks on screen and in the terminal

2026-10-19, one session (Opus), at the user's request: "for the validations that apply (especially F5), text in both
cv2 (via the text display function) and the terminal, so that we can test that it works; the panelists will see the
validations live on the TV." No git writes. **Not seen on the device yet;** what it looks like is in
`evidence/status-display-preview.png` (rendered through the real code on synthetic frames).

## What you see

**In the cv2 window (the camera picture, the window `--use-frame` opens):**

| Where | What | Colour |
|---|---|---|
| Bottom strip, always | `CAMERA OK  VIEW OK  GLASSES OK  EARPHONES OK  PANEL OK` | green working, **red** failing (`LOST`, `BLOCKED`, `BRIGHT`), yellow being confirmed (`2/3`), grey not in use (`-`) |
| Top, one per failing check | `CAMERA VIEW: BLOCKED OR TOO DARK`, `GLASSES BOARD: NOT RESPONDING`, `EARPHONES: MISSING`, `CONTROL PANEL: NOT RESPONDING`, `CAMERA: NOT CONNECTED`; under the camera view, its measured `brightness 0-255: average 6, top 5 %: 11` | red; the measurement in white |
| Top, while a change is confirmed | `Camera view: looks dark (2 of 3)` | yellow |
| Above the strip, 8 s | `Said: "Camera is blocked or it is too dark. Obstacle warnings may be missing."`: the last failsafe sentence, since only the wearer hears the earphones | cyan |

The detection overlay's `FPS:` stays top left. With no live picture (camera missing, depth only, both rockers off) the
window shows a dark screen with the same status and a line saying why, once a second, instead of freezing on an old
frame.

**In the terminal:** the whole status once at start, then one line per change:

```
[CHECK] Camera: connected | Camera view: not checked yet | Glasses board: connected | Earphones: connected | Control panel: connected
[CHECK] Camera view: looks dark (1 of 3) (brightness 0-255: average 6, top 5 %: 11)
[CHECK] Camera view: looks dark (2 of 3) (brightness 0-255: average 6, top 5 %: 11)
[CHECK] Camera view: BLOCKED OR TOO DARK (brightness 0-255: average 6, top 5 %: 11)
[CHECK] Camera view: BLOCKED OR TOO DARK (looks clear: 1 of 3) (brightness 0-255: average 117, top 5 %: 210)
[CHECK] Camera view: BLOCKED OR TOO DARK (looks clear: 2 of 3) (brightness 0-255: average 118, top 5 %: 214)
[CHECK] Camera view: clear (brightness 0-255: average 118, top 5 %: 214)
[CHECK] Glasses board: NOT RESPONDING
```

Coloured (red, green, yellow, grey) when the output is a terminal; plain in the journal. The existing lines (`[ALERT]`,
`[VIEW]`, `[TTS]`, `[SERIAL]`, `[CONFIG]`, `[EARPHONES]`, `[PATTERN]`) are unchanged.

## What changed

| File | Change |
|---|---|
| `src/second_vision/core/status_board.py` | **New.** `read_status()` (the five checks and the last sentence said, read from the alert scheduler and the camera view check; it only reads), `StatusReporter` (the terminal lines). No cv2. |
| `src/second_vision/core/status_view.py` | **New.** `status_layout()` (what goes where, every piece inside the frame), `draw_status()`, `status_canvas()`. Text through `core/depth_view.py`'s `put_plated` (the darkened plate) and `fit_text` (shrink, then clip). |
| `src/second_vision/workers/status_worker.py` | **New.** Twice a second: read the status, keep it on `user_data.status`, print the changes. |
| `src/second_vision/main.py` | `user_data.camera_view` (the F5 check, now made here so the app and the status share it), `user_data.status`, the status worker started. And a one-line fix in mock mode (below). |
| `src/second_vision/pipeline/callbacks.py` | `_draw_detection_overlay` draws the status on the picture before `set_frame`, under its own import guard; an error in it never costs the frame. Nothing else in the detection code changed. |
| `src/second_vision/pipeline/app.py` | The view check uses `user_data.camera_view` when there is one. The 1 s view tick also shows the dark status screen when no live picture carries the status. `_push_debug_banner` (`--cycle-modes`) gives way to it. |
| `src/second_vision/core/alerts.py` | `last_spoken()`: the text and time of the last alert the speech worker started. |
| `src/second_vision/core/camera_view.py` | `snapshot()`: the state, the change being confirmed and its count, the last mean and 95th percentile. |
| `tests/test_main_startup.py` | The two pinned worker lists end with `"status-board"` (a deliberate addition). |

**Not changed:** the depth window (`core/depth_view.py`'s drawing, the depth view process and its payload). It is the
depth code's, and the status would need a change to what the depth callback sends it. Say if you want it there too.

## Found on the way

- **Mock mode could die on a Ctrl+C sent just after "Generators running"** (exit -2 instead of 0). The line was printed
  before the `try:` that catches the Ctrl+C. A Ctrl+C in the moment after it, while the main thread waits for Python's
  lock behind the worker threads, escaped. A new test that sends Ctrl+C the moment it reads that line failed once in
  about 9 runs; with the print moved inside the `try:`, 15 of 15 passed. Mock mode only; the pipeline path is unchanged.
- **"Camera view: clear" before any frame was checked** would claim a look that never happened (mock mode, or just
  after start). It says "not checked yet" until the first check.

## Tested

New file `tests/test_status_display.py`, **39 tests**, in both environments:

- **The status:** everything working; each part out shows as a problem in words (the earphones' silent alert too);
  boards never asked for show "not in use"; the camera view in words for every case (not checked yet, clear,
  confirming 2 of 3, blocked, too bright, clearing); paused with both rockers off; the last sentence said and when;
  reading changes nothing.
- **The terminal:** all once, then only changes; brightness numbers alone do not print; each part lost and back
  printed; colour only on a terminal.
- **The drawing:** all well is one quiet strip; a problem is a red banner with its measurement; a change being
  confirmed is yellow; what was said shows for 8 s and wraps inside the frame; four problems at once fit; on a narrow
  frame the strip shrinks, then uses short labels, then leaves out what cannot fit; on a tiny frame the banners stop
  above the strip; the frame is changed in place and only where the text is; the dark screen.
- **The worker:** keeps the latest status and prints changes; survives a broken scheduler; stops at once.
- **The app:** the dark screen with no camera, in depth only, with both rockers off; none over a live picture; none
  without a display process or before the first status; the debug banner gives way; the app uses main's view check.
- **The overlay:** draws the status on the picture; a failing drawing never costs the frame.
- **The program:** `main.py --mock` starts the status worker, prints the first `[CHECK]` line ("Camera view: not
  checked yet": mock mode has no camera pipeline), and stops with exit 0 on Ctrl+C.
- **Mutation check:** 16 changes, each removing one behaviour in a scratch copy. The first run left one survivor
  (the short-label step on a narrow frame); its test now checks that the font stays larger. **All 16 caught**
  (`evidence/STATUS-mutations.txt`).
- **Repeats:** this file and F5's, 10 times in `my_hailo_env` and 10 in Poetry, all passed (`evidence/STATUS-repeat-10runs.txt`).
- **Full suites** (each is the previous total plus these 39 tests, 0 failed):
  - Poetry: 1021 passed, 216 skipped (`evidence/STATUS-poetry-full.txt`);
  - `my_hailo_env`: 1120 passed, 117 skipped (`evidence/STATUS-hailo-env-full.txt`).

## Device check (in the batched session)

With the program running from this worktree on a display (DEMO-CHECKLIST.md Part B), each part of the checklist now has
something to see as well as hear: the strip turns red for the part that is out, a banner names it, and `Said:` shows the
sentence. Check once that the TV is readable from where the panel sits; the sizes are set for 640 x 480 and can be raised.
