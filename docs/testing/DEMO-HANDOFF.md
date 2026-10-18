# Demo handoff: start here in a new session

For a fresh Claude Code session in `worktrees/kenth`, written 2026-10-18 18:25. Demo day is
**2026-10-19**. Read this file, then [DEMO-TRACK.md](DEMO-TRACK.md), then do the item the user names.
Nothing else needs reading up front: not HANDOFF.md, the task board or old transcripts.

## Where things stand

- **The plan:** [DEMO-TRACK.md](DEMO-TRACK.md) holds items F0 to F8. It gives the status table, the rules,
  the user's decisions, where the code is, and what each item must do. **No item has been started.**
  F0's code exists but has not been reviewed.
- **The agents are paused.** No Lead, Developer or Tester run is alive. **Do not start the Lead** or
  `lead_unattended.sh` until the user says so.
- **The device is busy.** `second-vision.service` has been active since 16:24, and the app inside it was
  relaunched at 17:21 and again at 18:35. Someone is using it right now, most likely a teammate testing
  depth estimation. Check `pgrep -af Parallel-Depth` for the current state. It runs the **base checkout** (`second-vision-repo`), not this worktree, so
  this worktree's code is not what is on the device. Do not stop the service or the app. Ask the user
  first, every time.
- **The hardware is normal:**
  - the camera (USB 1-1) and the earphones (USB 3-1) are attached and authorized;
  - `hailort.service` runs as usual (HAILO_MONITOR=0, no override in `/run`);
  - nothing that this workflow queued is still waiting.

## The working tree (uncommitted, keep it)

Everything that the agents built is uncommitted in this worktree, and the F items build on it as it is.
Do not revert, stash or reset anything.

- **Accepted:**
  - the test levels and harness: `tests/conftest.py`, `tests/gst_helpers.py`, `scripts/uat_evidence.py`;
  - the alert scheduler: `core/alerts.py`, `workers/alert_worker.py`.
- **Built, waiting for a person's check:**
  - the camera-loss survival work in `pipeline/app.py`, `main.py`, `scripts/sv-main.sh` and
    `scripts/second-vision.service`;
  - their tests.
- **Built, unreviewed (F0):**
  - the spoken camera alerts, which changed only `pipeline/app.py` and `core/alerts.py`;
  - `tests/test_camera_alerts*.py`.
  - The tree as it was before this work is in `.claude/baselines/T-03c1/`; the brief is
    `reports/T-03c1/brief-1.md`.
- **Test baseline (last full run):** the Poetry suite gave 754 passed, 213 skipped, 0 failed. Two strict
  `xfail` tests in `tests/uat/test_t00a_alert_scheduler.py` are expected failures until F7.

## How this user works

- **No git writes.** The user commits. Never add a Claude co-author line.
- **Ask before any device step,** after checking `systemctl is-active second-vision` and
  `pgrep -af "Parallel-Depth|pytest"`.
  - sudo is allowed only for `sudo -n .claude/tools/usb_sim.py` (the simulated camera or earphones
    unplug) and for read-only checks.
  - The exception: before a device step on this worktree's code, the service has to be stopped. The user
    does that or approves it.
- **"Pause" or "stop cleanly"** means let the running work finish and stop at a clean point. **"Right now"**
  means stop at once. Say up front how long a clean stop will take.
- **Explain IDs in plain words.** Never ask about a bare REQ-, TC-, D- or F-number.
- **Checks against the Hailo docs need no permission** (AGENTS.md Rule 6). Do them, and say where you
  checked.
- **Never** edit an installed library or the vision algorithms, never switch into mock mode by itself, and
  never flash ESP32 firmware.
- **Keep sessions small:** send long output to `docs/testing/reports/DEMO/evidence/` and read only its
  summary lines. Do not edit `TASK-BOARD.md`; record status in DEMO-TRACK's table and notes in
  `reports/DEMO/<item>.md`.

## Order, and what to type

Code-only items come first, while the device is busy. **F1 first,** because F2, F3 and F4 use its
pattern player. The order:
1. F1, F2, F3, F4, F6 (code only, while the device is busy);
2. F0 (review; its device check can wait for the batch);
3. F5;
4. one batched device session for F0 to F6;
5. F8 (the rehearsal with the user).

To start an item, the user opens a new session and types:

> Read `docs/testing/DEMO-HANDOFF.md` and do item F1.

Use Sonnet for most items and Opus for F1 and F5. When an item is done, update its row in DEMO-TRACK's
table and the "Where things stand" section here, so the next session starts from the truth.

## Left for the user (not for the session)

- **Commit the work.** First run `git add -A docs/handoffs`: the index still holds the old dated
  evidence file names (shown as `AD` in `git status`).
- **The real camera pull and replug.** F8's rehearsal covers it.
- **The NPU measurement** was cancelled. Only on the user's request: `.claude/tools/queued_npu_run.sh`. It
  stops and restarts the Lead, so read it first.
- **After demo day:** the Lead reconciles the task board from DEMO-TRACK.md and resumes the workbook.
