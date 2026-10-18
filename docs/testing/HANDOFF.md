# Handoff: continuing the agent workflow

For whoever picks this up next: a teammate, or a Claude Code session on another account.
Written 2026-10-17, when the workflow moved from the shared account to Kenth's own account to
get around the plan's 5-hour usage limit. Check the dates: the board and the reports are the
live state; this page is a snapshot.

## 1. The short version

- The Lead / Developer / Tester workflow runs only in this checkout
  (`~/Projects/Second-Vision/worktrees/kenth`), from the local `.claude/` folder. How to use it:
  [AGENT-GUIDE.md](AGENT-GUIDE.md).
- **Everything it needs is in files on this Pi, not in any account.** The board, the reports,
  the agents' instructions and the past conversations (`~/.claude/projects/`) all stay put
  when you log in with a different account. Any account can continue.
- **Live state:** [TASK-BOARD.md](TASK-BOARD.md) ("Active task") and
  `reports/<task>/`. Read those before trusting anything below.

## 2. Switching accounts

1. In the VS Code Claude panel or a terminal, log out and log in with the other account. Check:
   ```bash
   C=$(ls -d ~/.vscode-server/extensions/anthropic.claude-code-*-linux-arm64 | sort -V | tail -1)/resources/native-binary/claude
   $C auth status        # shows the account email
   ```
   The workflow's scripts use the same login as the panel.
2. **Start a fresh Lead rather than resuming the old conversation.** A resume re-sends the whole
   earlier conversation, which is long and costs the new account a lot of its limit. A fresh Lead
   reads the board and the reports and continues the active task (AGENT-GUIDE section 3).
3. Usage limits are per account. When one account says "You've hit your session limit", the run
   stops; it does not restart by itself. Start it again later, or from the other account.

## 3. Where things stand (2026-10-17 04:30)

| Task | What | State |
|---|---|---|
| T-10a | Test setup: test levels, timeouts, GStreamer helpers, simulated camera unplug, evidence logs | **ACCEPTED** 2026-10-16. Ready to commit (list in `reports/T-10a/decision-2.md`) |
| T-03a | The program survives the camera being removed while it runs, and restarts vision when a camera returns (no alerts yet) | **In testing.** Developer done (`dev-report-1.md`). The Tester (Opus) was cut off at 04:18 by the old account's usage limit, partway through: interim results in `test-report-1.md` (all passing so far, including real simulated-unplug runs in every mode), logs in `evidence/`. Next: finish the Tester's run, then the Lead's review and decision |
| Next, in order | T-03b (remove the automatic switch to mock mode), T-00 (alert patterns and repeating speech), T-01, T-04, T-02, then P2 | BACKLOG |

Counts: none of the 110 workbook cases is VERIFIED yet. T-03a, once accepted, covers part of
TC-024-003.

**Uncommitted work** (the user commits; no agent writes to git): `git status` lists it. Commit
T-10a first. Leave T-03a's files until the Lead decides on it.

## 4. How to continue

```bash
cd ~/Projects/Second-Vision/worktrees/kenth
sudo systemctl stop second-vision                   # the agents need the camera and the Hailo chip
.claude/tools/lead_unattended.sh "Continue the active task from the board and the reports, then the plan."
python3 .claude/tools/lead_progress.py -f          # watch; Ctrl+C stops watching only
```

- Only one run at a time: a second start is refused while one is going.
- Add `--when-hardware-free` to wait until you stop the boot service. A reboot restarts that
  service, and also ends a run (the launcher does not survive reboots).
- Wi-Fi drops are resumed automatically. A usage limit or a reboot is not.
- To talk to the Lead instead of leaving it alone: `.claude/tools/lead.sh`.
- **Models (2026-10-17):** every role runs on Sonnet at extra-high effort. Opus only for very
  high-risk tasks, with the reason on the board (TASK-BOARD, "Model routing").

## 5. Waiting on a person

Decisions or fixes only the user can make. None blocks the current task.

1. **Hands-on steps.** Each task that reaches AWAITING-HANDS-ON is listed in
   [MANUAL-TESTS.md](MANUAL-TESTS.md), "Waiting for a person". T-03a will need one real camera
   unplug and replug.
2. **`pyproject.toml`: add `testpaths = ["tests"]`** under `[tool.pytest.ini_options]`. A bare
   `pytest` with no folder also runs `scripts/test_motor_packet.py`, which sends real motor
   packets on `/dev/serial0`. Agents may not edit that file. Rerun the suite after.
3. ~~**The camera simulator's timer**~~ Fixed 2026-10-18: an explicit `attach` now cancels the pending
   re-attach timer, so an old timer can no longer re-attach a later detach early. Tested with the earphones
   and the camera.
4. **Passwordless sudo** for the `sv-rpi5` user. The workflow's hook blocks sudo in typed
   commands, but code could still call it. Limiting sudo to the needed commands is a system
   setting and the user's call.
5. **Workbook revision.** [WORKBOOK-REVISIONS.md](WORKBOOK-REVISIONS.md) and
   `workbook-revisions.csv` are the handoff for revising the test-case sheet. After revising,
   the Lead walks through updating the requirements (AGENT-GUIDE section 8).
6. **Rule 8 question still open:** should the evidence rule (automated test per case, plus one
   hands-on confirmation per device) be added to D39 in `documentation/DECISIONS.md`?
7. **Merge warning (D40 / D-14):** merging `feat/depth-callback` brings commit `3797182`, which
   silently reverses the strength-knob decision. Keep this branch's scaling in
   `serial_worker.py` when merging.

## 6. Things that went wrong, and what to watch for

| Seen | What it was | Now |
|---|---|---|
| Run ended "API Error: Can't reach the API server (EAI_AGAIN)" | Wi-Fi dropped (2026-10-16 06:25 and 10:46) | The launcher waits for the network and resumes |
| Run gone after the Pi restarted | The launcher doesn't survive a reboot | Start it again; the reboot also restarted the boot service, so stop it first |
| Two runs on the same session | Started twice by accident (2026-10-17 03:16) | Second one killed; the launcher now refuses a second run |
| "You've hit your session limit" | Account's 5-hour usage limit | Wait for the reset, or switch accounts (section 2) |
| `[role_guard] Blocked: …` | The safety hook working | Nothing to do |
| Camera errors with nobody touching it | Seen once on 2026-10-15 (5 v4l2 read errors) | Being watched; report if it happens again |

## 7. For a Claude Code session that isn't the Lead

- Read `AGENTS.md` (project rules), then [AGENT-GUIDE.md](AGENT-GUIDE.md) and this page.
- The local memory notes for this project are in
  `~/.claude/projects/-home-sv-rpi5-Projects-Second-Vision-second-vision-repo/memory/`
  (`uat-agent-workflow.md` has the workflow's history). They stay on the Pi across accounts.
- The setup conversation that built this workflow is
  `~/.claude/projects/-home-sv-rpi5-Projects-Second-Vision-worktrees-kenth/05729c48-f9a8-4cad-95ef-4d80ca2dde0f.jsonl`.
  The Lead's own runs are logged in `.claude/runs/<date-time>/`.
- Standing rules from the user: no git writes, and no Claude co-author line in commits; never
  edit installed libraries, the UAT workbook, ESP32 firmware or the vision algorithms; mock mode
  only through `--mock`; sudo only as allowed in AGENT-GUIDE section 6; explain task and case IDs
  in plain words when asking the user anything.

## 8. Queued after the P1 tasks

[URGENT-TESTS.md](URGENT-TESTS.md): the user's priority list for after the critical failsafes —
camera blocked / too dark / too bright (T-13), blurry camera (T-14) and the performance run (T-15).
Order: P1 tasks and their tests, then these, then the rest of the workbook. The user agreed the
design (a once-a-second tiny-frame check before any AI, three checks in a row, alerts through the
T-00a scheduler, only if it adds no measurable lag) and approved the wording and repeat rate in
the file. The Lead adds them to the board when the P1 tasks are done. The sampling tool for T-15
already exists: `docs/testing/tools/perf_sample.py`.

## 9. The simulator now does the USB earphones too (2026-10-18)

`sudo .claude/tools/usb_sim.py detach earphones --for N` / `attach earphones`, same safe mechanism as the
camera (the kernel's per-device USB `authorized` flag; port power untouched; self re-attach). The earphones
are found by what they do (a non-hub USB device whose sound card can play audio and has no video node), so
the camera's own microphone is never taken for them. Tested live; the hook allows the new argument.
Measured facts for T-04 (earphones missing):
- With the earphones off, `aplay -l` loses `card2` and PipeWire's default output becomes a **Dummy Output**.
  `espeak-ng` still exits 0 and prints nothing, so speech goes nowhere silently. The program can't learn
  the earphones are gone from a failed speech call; it has to look at the device.
- On re-attach `card2` returns and the sink returns with its volume (0.78).
- The earphones are `3-1` (SPACETOUCH USB Audio, 0666:0880); the camera is `1-1` and has a capture-only
  microphone (`card3`).
Policy recorded in `documentation/DECISIONS.md` as D41 (failsafe alerts override the switches at 30 % or
more, one time only).

## 10. Where the agents were stopped (2026-10-18 14:40)

The user stopped the Lead run at once so a teammate could use the device for a depth test. T-03c1 (spoken
camera alerts) is IN-DEV: the Developer had finished the code and tests and was on its final full-suite
runs (Poetry environment: 754 passed, 213 skipped, 0 failed; the Hailo-environment run was cut off); its
evidence is in `reports/T-03c1/evidence/` and there is **no `dev-report-1.md`**. The next Lead should inspect
the tree against `.claude/baselines/T-03c1/`, have a Developer write the report (rerun the Hailo-environment
suite once), then the Tester and the review. The NPU measurement was cancelled and nothing was changed on
the system (`hailort_service` still has HAILO_MONITOR=0); `.claude/tools/queued_npu_run.sh` can run it later.

## 11. Demo track (2026-10-18): the agents are paused

Demo day is the next day. The remaining critical items are done in a fast lane instead of the agent loop:
[DEMO-TRACK.md](DEMO-TRACK.md). **Do not start the Lead** until the user says so; after demo day the Lead
reconciles the board from that file.

