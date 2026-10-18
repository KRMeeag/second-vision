# T-00a · Lead decision, attempt 1

- Date: 2026-10-18 08:20
- Decision: **ACCEPTED**, with one limit recorded and put to the user (F-1, below). The person
  hearing the alert is not required for this task (the brief defers it to the first task that raises
  an alert, T-01a's hands-on session).
- Models: Developer **developer-sonnet**, Tester **tester on Sonnet**. Opus bar not met and not needed.
- Based on: brief-1.md (with my Amendment A), dev-report-1.md, test-report-1.md (restarted after the
  Pi reboot of 2026-10-17; the Tester's 2.3-hour run is in `evidence/`), and my own review below.

## Summary in plain words

The device now has one place that speaks a system alert on a schedule. A task says what to say and
how often ("Controller board not connected" every 30 s while depth is on, once otherwise; "Battery
low" every 5 minutes; "Camera disconnected" once) and clears it when the condition ends; it writes no
timing code. The alert is spoken within about 0.1 s, repeats start to start on its interval (10 s, 30 s
and 300 s measured to within 0.1 s), stops the moment it is cleared, and never cuts or replaces another
alert: several are spoken one after another, each to the end. If a mode announcement or an urgent
detection cuts an alert, a head turn would have dropped it, or a mode announcement replaced it in the
queue, the alert is spoken again as soon as the speech is free (a once-only alert included). An alert
dropped because the wearer switched speech off counts as delivered and tries again at its next repeat.
No alert is raised by this task, so nothing new is spoken on the device yet.

## Acceptance criteria

| # | Met? | Evidence (report + level) |
|---|---|---|
| 1 Spoken at once, then on the cadence (10, 30, 300 s) | **Yes** | Tester rows 8, 17; dev-report. Raise to speech start median 78 ms (max 87 ms, n=30); 10 s alert 10 utterances and 30 s alert 4 in 94.5 s, worst deviation 0.10 s; 300 s gaps 300.05; item exactly `{"announce": text}`. mock-integration |
| 2 "Once", including switched off mid-run, and before the first utterance finished | **Yes** | rows 9, 17. mock-integration |
| 3 Repeat back on | **Yes**, built literally (see question 1) | row 10 |
| 4 Cleared means stopped; never anyone else's item | **Yes** | row 11 |
| 5 Several alerts never cut or lose each other, with announcements at random | **Yes** | row 12: order `a, b, c`, each to the end; two seeds clean |
| 6 Not lost: cut, head-turn drain, tie replace, speech switch off | **Yes** | rows 13, 18, 19. Tester's own virtual-clock model 60 runs per path, real `tts_worker` and stub espeak 20 runs per path, in both environments: **0 lost, doubled or cut by another alert**. Harness shown not vacuous by mutants |
| 7 Raise again | **Yes** | row 14: 300 re-raises of the same alert gave one utterance |
| 8 Threading and shutdown | **Yes** | row 15: daemon, stops in 0 to 1 ms, 24000 calls from 12 threads each under 1 ms, `main.py --mock` Ctrl+C stop times baseline vs new differ by at most 0.11 s with no shift, exit 0. The baseline's own mock-mode stop is already 0.2 to 1.95 s (the mock generator), not this task |
| 9 Nothing else changes | **Yes** | rows 5, 6, 16: all existing tests pass, only two worker-list assertions edited (Amendment A item 1); real-worker guard shows phrase, urgent cut, mode announcement, volume, speech switch and head-turn mute as before |
| 10 Usable by the next tasks | **Yes** | row 17: the docstring's block executed literally; "30 s while depth is on, once otherwise" works with no timing code |

Amendment A item 4 (delay from "raised" to "spoken" under continuous detection speech): normal-tier
detections every 0.1 s, median 0.46 s and **max 1.27 s**, within the 3 s bound. Urgent-tier: see F-1.

## My review (step 8)

- Per-task diff run by me (`changes-1.patch`, 3792 lines, 9 files): `core/alerts.py` and `workers/alert_worker.py`
  new; `main.py`, `workers/tts_worker.py`; tests: `test_alerts.py`, `test_alerts_speech.py`,
  `test_main_startup.py` (2 lines), and the Tester's two files under `tests/uat/`. This is exactly the Developer's
  and the Tester's list. No unreported file.
- `check_outside_writes.py`: installed libraries and environments nothing changed; other checkouts and the
  workbench nothing changed.
- `sudo`, `subprocess`, `os.system`, `os.popen`, `os._exit`, `sys.exit` in the added `src/` and `scripts/` lines: none.
  Tests start only `main.py --mock` and a stub espeak.
- Protected files: `core/depth_utils.py`, `core/haptics.py`, `pipeline/`, `core/priority.py`, `serial_worker.py`,
  `config_reader.py`, `core/config.py`, `firmware/`: not in the diff. Queue item stays `{"announce": str}`
  (Rule 2); worker signatures unchanged (Rule 3).
- Threading (Rule 4): read `core/alerts.py` in full. One lock, no call waits for the speech worker, the
  caller's `repeat` callables run outside the lock, `alert_worker` checks `shutdown_event` every 0.05 s. The
  `tts_worker` changes are all guarded by `alerts is not None`, so without the attribute the old branch runs.
- Rule 7 (callers exist): `take`, `drain`, `finish`, `release` are called from `tts_worker.py`, `poll` from
  `alert_worker.py`, the worker is started in `main.py`. Nothing yet raises an alert, as intended.
- Hailo docs: not pipeline-related, none consulted.

## F-1: under a never-ending stream of urgent object warnings, an alert is never heard to the end

What the Tester found (12 of 12 trials, both environments): if the camera sees an urgent-class object
(a vehicle, for example) that stays in view, the detection side offers a fresh urgent warning at the
frame rate (`callbacks.py:548-552`: an urgent object skips the 10 s "still there" floor). Every urgent
item cuts whatever is being spoken (`priority.preempts()`, today's rule). So the speech worker is either
never idle (the scheduler then never offers the alert) or cuts the alert after a fraction of a second,
over and over. The alert is heard as soon as the urgent stream pauses; with normal-tier detections the delay
is at most 1.27 s.

My decision: **accepted as a known limit for this task, put to the user, not fixed now.**
- It is not a coding slip and no requirement says a failsafe alert must beat an urgent object warning. The
  cut is today's rule: the Tester showed a mode announcement is cut the same way on the baseline, and the
  brief says preemption stays as today.
- Any real fix changes who may interrupt whom. The one that works (a detection item may not cut an alert
  in progress, and an alert gets a turn in a worker that is never idle) delays a "vehicle ahead" warning by
  the length of an alert (2 to 3 s). That is a safety trade-off between two warnings, the user's call.
- While an urgent stream lasts, the wearer is hearing urgent warnings, so they are not in silence. The
  alert is spoken when the stream pauses.
- The four urgent-tier tests are `xfail(strict=True)` in `tests/uat/test_t00a_alert_scheduler.py`. If a fix
  is made, they turn into failures that say "remove the marker", so the limit cannot be forgotten.

## Smaller limits (Tester F-2, F-3)

Both are caller-side hazards, nothing raises an alert yet:
- **F-2.** `raise_alert` accepts `interval=0.01`, which would starve all detection speech. **Left as a
  recorded limit:** every real alert uses 10 s or more, and a floor would break two tests in the Tester's
  `tests/uat/` file (a Developer may not edit them). If a floor is wanted, the Tester updates its two tests
  in the same task.
- **F-3.** A `repeat` callable runs on the one scheduler thread with no time limit; one that blocks delays
  every alert and the worker's stop. **Carried:** T-03c1's brief adds one docstring line to `core/alerts.py`
  ("return at once; never block"). No behaviour change.

## Questions for the user (nobody to answer now; none blocks the next task)

1. **Urgent object warnings versus system alerts (F-1).** Today an urgent object warning ("car center")
   cuts any speech already under way, including a system alert. If a vehicle stays in view, a system alert
   such as "controller board not connected" is not heard until the vehicle leaves. Options:
   (a) keep it: the object warning always wins;
   (b) let an alert in progress finish (2 to 3 s) before an urgent object warning can cut it, so an alert
   is always heard, at the cost of delaying an object warning by that long;
   (c) decide later, after the first alert is heard on the real device.
   My recommendation: (c), then (a) unless the hands-on session shows alerts going unheard in ordinary use.
   Recorded as D-16 in DISAGREEMENTS.md.
2. **Switching a repeat back on (criterion 3).** When the controller board is still missing and depth is
   switched off and then on again, the alert is spoken at once if its last utterance is more than 30 s old,
   then every 30 s. The workbook is silent on this; this is the literal reading of "repeats while depth is
   enabled". If you would rather it wait a full 30 s after switching on, it is one line in `_Alert.due_at`.
3. **Cadence after a cut.** A cut alert is spoken again as soon as the channel is free and its next repeat is
   counted from then, so the wearer never hears it twice in less than the interval. The alternative (keep the
   original schedule and add an extra utterance) could play it more often than every N s. Keep as built?
4. **Documentation (Rule 8).** Two settled choices are worth recording and I have written neither:
   `ARCHITECTURE.md`: the new `user_data.alerts` attribute and the feedback contract between `tts_worker` and the
   scheduler (and the shared-files map: `tts_worker.py` and `main.py` are now touched by the failsafe tasks);
   `DECISIONS.md`: "alerts are exempt from the head-turn drain; they obey the speech switch until D-15 is
   decided; an alert waits for a phrase in progress and is cut by an urgent detection like any announcement".
   Do you want them added?
5. The Developer's observation (R2): the head-turn drain also discards a mode announcement ("depth mode"
   lost if the wearer turns their head within a second of the switch). Not this task; not changed. Worth a
   small task for the control-panel workstream owner?

## Waiting for a person

None for the decision. When the first alert exists (T-01a), its hands-on session also confirms this task: one
alert with its real wording at 10 s, audible and intelligible at the knob settings in use, spoken to the end,
repeating about every 10 s, a mode change from the panel during it heard after it or in place of it, then the
alert heard again. Optionally F-1 by ear: with a vehicle held in the centre of the camera view, raise an
alert and listen whether it is ever heard to the end.

## Records updated

TASK-BOARD (state, active slot cleared), TRACEABILITY (no workbook case is directly tied to T-00a; totals
unchanged), DISAGREEMENTS (D-16), `changes-1.patch` saved. Files ready to commit: see the board row.
