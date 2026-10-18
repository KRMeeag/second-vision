# T-00a · Repeating spoken system alerts · Brief, attempt 1

- Date: 2026-10-17
- Requirements (quoted from REQUIREMENTS.md; this task builds the shared mechanism for their
  *cadence* and *speech*, not any of the alerts themselves):
  - REQ-CON-03: "the REQ-CON-02 alert repeats every 30 s while depth estimation is enabled on the
    controller board. It is said once while depth estimation is disabled, including when depth is
    switched off mid-run." (Team 2026-10-12: switched off mid-run = **once**, W-11.)
  - REQ-CON-05: "It repeats every 5 minutes."
  - REQ-CON-07: "It repeats every 10 seconds."
  - REQ-CON-11: "the REQ-CON-10 pattern repeats every 30 s while object detection is enabled. It
    plays once while detection is disabled, including when detection is switched off mid-run."
    (That one is a vibration pattern; the cadence rule is the same and T-00b/T-04 reuse it.)
- UAT cases: none directly. Enabler for TC-022-002 to 006, TC-023-002 and 004, TC-024-002 and 003,
  TC-025-002 to 006, whose cadences it implements. Verbatim text in `uat-snapshot.yaml`.
- Priority / depends on: P1 / none (T-03a and T-03b are in the tree, untouched by this task).
- Developer: **developer-sonnet**. Opus bar checked: no GStreamer or Hailo lifecycle, no motors;
  threading is one small daemon worker. Not met.
- Tester model: **sonnet**.
- Baseline: `.claude/baselines/T-00a/` (taken 2026-10-17 19:37, before attempt 1, with T-03a/T-03b in it).
  Per-task diff: `for d in src tests scripts; do diff -ruN -x __pycache__ -x '*.pyc' .claude/baselines/T-00a/$d $d; done`.

## Goal

One mechanism that every failsafe task will use to speak a system alert on a schedule:
- an alert is raised with its text and a repeat interval (or "once"), and is spoken at once;
- it repeats every N seconds while it stays raised, or never repeats if "once";
- its repeat can be switched off and on while it is still raised (depth switched off mid-run);
- it is cleared on demand and then stops at once;
- several alerts at the same time are spoken one after another, each to the end, none lost.

No alert is created by this task (nothing new is spoken on the device). T-01a, T-02, T-03c/d,
T-04 and T-06 raise their alerts through it.

## What exists today (Lead's reading of the code, 2026-10-17; **not verified: check each point first**)

1. **The channel.** `user_data.tts_queue` is a `PriorityMailbox`: a **single slot**, `offer()` keeps
   the higher priority of {stored, incoming} and **ties go to the incoming item** (`core/priority.py:176-205`).
   `{"announce": str}` has priority `+inf` and tier urgent. The contract (AGENTS.md Rule 2) does not
   change: an alert is exactly `{"announce": text}`.
2. **Ways an alert can be lost or cut today, found by reading `workers/tts_worker.py`:**
   - A second `announce` offered while the first is still in the slot **replaces** it (tie, fresher wins).
   - While an `announce` is being spoken, another `announce` is urgent, so `preempts()` is true and the
     worker **terminates the utterance** and speaks the new one (`_speak_interruptible`). Two alerts
     would cut each other off mid-sentence.
   - `_check_turn_mute()` (`tts_worker.py:108-138`) **drains the mailbox** (`mailbox.take()`) whenever a
     head-turn event arrives, and mutes speech for 1 s without consuming anything. A pending alert in
     the slot is discarded by a head turn.
   - `config.tts_enabled` off: `tts_worker` takes the item and `continue`s, dropping it silently.
   - A mode announcement from the panel (`{"announce": "depth mode"}`) is the same shape and priority.
   Find any other way (read the whole worker) and report it.
3. **There is no feedback.** Nothing tells the producer whether an item was spoken to the end, was cut,
   was drained or was dropped. A scheduler that only "offers on a timer" would lose alerts silently.
4. **Pacing.** After each utterance the worker waits `MIN_UTTERANCE_GAP` (0.5 s) unless an urgent item is
   pending (`_pace`).

## In scope

- A new module under `src/second_vision/core/` (suggested: `alerts.py`) holding the scheduler, with
  a docstring that tells the next task how to use it (what to call, from which thread, what the
  guarantees are). Pure Python and thread-safe: it will be called from the serial worker, the panel
  reader, the camera watcher and the main loop.
- One small daemon worker (or equivalent) that runs the scheduler, started from `main()` next to the
  other workers, with the same signature style `worker(user_data, config)`, `shutdown_event` checks and
  bounded waits (Rule 4). The scheduler object lives on `user_data` (like `imu_telemetry` and
  `turn_event`), created in `SecondVisionUserData.__init__`.
- `src/second_vision/workers/tts_worker.py`: **only** what is needed to give the scheduler feedback on
  what happened to an alert (spoken to the end, cut, drained, dropped), and to stop the head-turn drain
  from silently discarding an alert if you choose that route. Say exactly what you changed and why.
  The queue dictionary keeps its shape: feedback goes through a new attribute on `user_data`, not a new
  key on the item. Preemption, pacing, head-turn muting, volume and the `tts_enabled` drop behave as today.
- `src/second_vision/main.py`: create/start the worker only.
- `tests/`: new tests (see Verification). `tests/uat/` belongs to the Tester: do not edit it.

## Out of scope: must not change

- The queue dictionary keys and the `tts_queue` kind (Rule 2): `{"announce": str}` stays exactly that.
- `core/priority.py` scoring and tunables, `serial_worker.py`, `config_reader.py`, `pipeline/`,
  `core/config.py` public API, `firmware/`, installed libraries, and the protected vision files
  (`core/depth_utils.py`, `core/haptics.py`, the detection processing in `pipeline/callbacks.py`).
- Existing speech behaviour: a detection phrase, a mode announcement, the volume knob, the speech switch
  (`tts_enabled`: alerts obey it exactly like every announcement; whether failsafe alerts should override
  it is the user's open question D-15, deliberately not decided here) and the head-turn mute.
- No alert is raised by this task, no motor is touched, no wording is chosen (the wording belongs to each
  alert's task; the workbook gives it).

## Acceptance criteria

Each restates the requirements above. `t` is the time the alert is raised.

1. **Spoken at once, then on the cadence.** An alert raised with interval N is offered as exactly
   `{"announce": text}` within about 1 s of `t` (when the speech channel is free), and again N s
   after the start of each previous utterance (tolerance ±1 s), for as long as it stays raised and its
   repeat is on. Checked for N = 10 s, 30 s and 300 s.
2. **"Once".** An alert raised with no repeat is spoken exactly once. If its repeat is switched off after
   it has been spoken (depth switched off mid-run), it is not spoken again. If the repeat is switched
   off **before** the first utterance finished, the first utterance is still delivered, once.
3. **Repeat back on.** Switching the repeat on again while the alert is still raised resumes the cadence
   (REQ-CON-03 says "repeats ... while depth estimation is enabled"; the workbook is silent on switching
   it back on while the board is still missing, so this is the literal reading: report it in your
   report so the Lead can put it to the user).
4. **Cleared means stopped.** Clearing an alert stops it at once: nothing more is spoken for it. A
   utterance already under way is not cut; an item the scheduler put in the mailbox and that has not been
   taken is removed (never remove an item that is not the scheduler's own). Clearing an alert that is not
   raised does nothing.
5. **Several alerts never cut or lose each other.** With alerts of 10 s, 30 s and 300 s raised at once,
   and other announcements (a mode announcement, a detection phrase) arriving at random, every alert
   utterance is spoken to the end, in the order they fall due, at its own cadence; none is skipped for
   the rest of the run. (Two alerts due in the same instant are spoken one after the other.)
6. **Not lost.** An alert whose utterance was cut off (by a mode announcement, by anything urgent), or
   whose mailbox item was discarded (head-turn drain) or replaced, is spoken again as soon as the channel
   is free; a once-only alert is included. An alert dropped because the speech switch is off is **not**
   re-offered in a storm: it counts as delivered, and the next repeat tries again as normal.
7. **Raise again.** Raising an alert that is already raised with a different text or interval replaces
   it (no second copy); raising it with the same text and interval changes nothing (no extra utterance).
8. **Threading and shutdown (Rule 4).** The scheduler thread is a daemon, checks `shutdown_event` at
   least every 0.25 s, never blocks on the speech worker, and the program still stops in about 1 s with
   exit 0 (REQ-DTA-08). The API is safe to call from any thread, and never blocks the caller.
9. **Nothing else changes.** `tts_worker` behaviour for detection phrases, mode announcements, volume,
   speech switch, head-turn mute is identical (the existing tests pass unchanged, and a test shows a
   detection phrase and a mode announcement still behave as before with the scheduler running and idle).
10. **Usable by the next tasks.** The module docstring (or `reports/T-00a/dev-report-1.md` if you prefer)
    shows, in a few lines, how a task raises "wording X every 30 s while depth is on, once otherwise" and
    clears it, with no timing code of its own.

## Verification expectations

| Criterion | Minimum evidence level | Hardware needed |
|---|---|---|
| 1 to 7 logic | `unit`, with a virtual clock (no real waits), the mailbox and a fake speech feedback | none |
| 1 to 9 together | `mock-integration`: the **real** `tts_worker`, `PriorityMailbox` and scheduler, with a stub `espeak-ng` first on `PATH` that records each phrase with its start and end time and can be told to run for a set time (existing TTS tests stub `Popen` and `shutil.which`, see `tests/test_tts_volume.py`). Real time for cadence (10 s, 30 s: at least 3 repeats each) | none |
| 5, 6 | the same, with the loss paths forced on purpose: a mode announcement mid-utterance, a head-turn event (`TurnEvent`) with an alert in the slot, speech switch off, two alerts due together | none |
| 8 | the real program path: `main()` or a harness starts the worker, `shutdown_event` ends it; Ctrl+C timing | none |
| the person hears it | `user-observed`, once, **together with T-01a's hands-on session** (the first alert that exists); not required for this task's decision | earphones |

## Fixed means repeated

The loss paths are races between the scheduler thread, the speech thread and the producers. Before you
hand over, show with counts in the report:
- each loss path of criteria 5 and 6 (cut by a mode announcement, drained by a head turn, replaced by a
  tie, speech switch off): **at least 50 randomised runs at unit level (virtual clock) and at least 20 with
  the real `tts_worker` and the stub espeak**, in none of which an alert is lost, doubled or cut by
  another alert;
- the existing suite plus the new tests pass in both environments, and each new test is shown to **fail
  on the baseline** (scratch tree built from `.claude/baselines/T-00a/`, never git) or is explained as a
  guard that cannot.

## Design notes from the Lead (hints; you own the design)

1. **Start with what an alert "being delivered" means.** The scheduler needs one fact per offer: spoken to
   the end / cut / discarded / dropped. The smallest way is for `tts_worker` to publish a monotonically
   increasing record on `user_data` (for example the last finished announce text and a counter, written
   under a lock) and for the scheduler to match it against what it offered. Compare with alternatives
   and keep the smallest change to the worker.
2. **Serialising.** Only the scheduler offers alerts, one at a time, and only when the slot is empty and no
   alert is being spoken, so alerts cannot cut each other. Other producers (mode announcements,
   detection) still can cut an alert; criterion 6 repairs that.
3. **Cadence reference.** "Every 10 s" is start-to-start of utterances, not end-to-start.
4. **Time.** Use `time.monotonic()` and make the clock injectable for the virtual-clock tests.
5. **Head-turn drain.** Either keep the scheduler's item out of the drain, or detect the loss and
   re-offer. Whichever you pick, say why and keep today's behaviour for detection items.
6. **A tied replace** (`>=` in `offer()`) means the scheduler must not offer into an occupied slot, and
   must notice if its own item was overwritten by a mode announcement.

## References

- AGENTS.md rules 2 (contracts), 3 (worker signatures: keep them; the new worker follows the pattern),
  4 (threading), 7 (verify before you trust).
- `core/priority.py`, `workers/tts_worker.py`, `tests/test_tts_volume.py`, `tests/test_priority.py`,
  `main.py` (worker start), `core/turn_event.py`.
- DISAGREEMENTS D-15 (open: whether failsafe alerts override the speech switch).
- Not pipeline-related: no Hailo documentation is needed for this task; say so in the report.

## Open risks / questions: stop and report instead of guessing

- **R1. A change in `tts_worker` is a change to the speech path every detection announcement uses.** Keep
  it minimal, test it against the existing tests, and name every line you changed.
- **R2. The head-turn drain** (`mailbox.take()`) also discards mode announcements today. Do not change that
  for mode announcements; if you think it is a bug, say so in the report, do not fix it.
- **R3. Anything in "What exists today" that the code contradicts:** report the difference first.
- **R4. Do not weaken a test to make it pass.** If a Tester test expects the wrong thing, say so with
  evidence; the Tester fixes its own tests.
- **R5. Device:** none is needed. Do not use the camera, the Hailo chip or the serial ports. If you think
  you need one, stop and report.

## Amendment A (Lead, after dev-report-1; for the Tester and any attempt 2)

Answers to the Developer's questions, 2026-10-17:
1. Changing the two worker-list assertions in `tests/test_main_startup.py` is accepted (a new worker
   changes that list by definition). Criterion 9 means the existing speech and priority tests pass
   unchanged; the Tester checks that this is the only edit to an existing test.
2. The stop time in mock mode (0.9 to 1.8 s) is the baseline's. Criterion 8 means the alert worker adds
   nothing to it (measure the stop with and without the worker).
3. Resuming the cadence at once when the repeat is switched back on after the due time has passed is
   accepted as the literal reading of REQ-CON-03; the Lead puts it to the user.
4. An alert that waits for a phrase in progress (instead of cutting it) is accepted. The Tester measures
   the worst-case delay from "raised" to "spoken" under continuous detection speech and reports it; more
   than about 3 s is a finding.
5. `core/priority.py` stays untouched (the two microsecond races can at worst drop a mode announcement; they
   cannot lose or double an alert). No atomic methods on `PriorityMailbox` in this task.
