# T-00a · Developer report, attempt 1

- Date / model: 2026-10-17 / developer-sonnet
- Summary: A new alert scheduler (`core/alerts.py`) plus a 19-line worker thread (`workers/alert_worker.py`)
  raise a system alert, speak it at once, repeat it on a cadence (start to start), clear it on demand and
  repair every loss path, with `tts_worker` telling the scheduler what became of each alert it offered.
  The queue item stays exactly `{"announce": text}`; the feedback goes through a new attribute
  `user_data.alerts`. No alert is raised by this task. Nothing needed a device. Five things need the
  Lead's eye: see "Questions for the Lead" (the first two change how two tests and one rule read).

## Inspected before changing

- `workers/tts_worker.py` (read whole): the loop, `_check_turn_mute`, `_speak_interruptible`, `_pace`,
  `_text_for`, `_start_espeak`, `_speak_blocking`.
- `core/priority.py` 120-205 (`item_priority`, `item_tier`, `preempts`, `PriorityMailbox`), `core/turn_event.py`,
  `core/config.py`, `main.py` (whole: `SecondVisionUserData`, worker start, shutdown), `workers/config_reader.py:95-105`.
- Callers confirmed with grep over `src/`: the only consumer of `tts_queue` is `tts_worker` (`take`, `peek`);
  the producers are the detection callback (`callbacks.py:649`), the panel reader (`config_reader.py:101`,
  `{"announce": "<mode> mode"}`) and the mock generators. `_check_turn_mute` is called once, from `tts_worker`.
  `pipeline/app.py:1853` (`StandaloneUserData`, `python3 app.py` only) builds a `tts_queue` too but starts no
  worker; not touched.
- Tests read: `tests/test_tts_volume.py`, `tests/test_priority.py` (lines 175-335), `tests/test_main_startup.py`,
  `tests/conftest.py` (levels, markers, the 60 s default timeout when `pytest-timeout` is installed).
- Baseline state before any change: Poetry env `487 passed, 166 skipped`; `my_hailo_env` `564 passed, 89 skipped`.
  `systemctl is-active second-vision.service` = inactive, no `main.py`/`second_vision` process.

**Brief section "What exists today": checked point by point.** Nothing in it is contradicted.
1. Channel: confirmed. Single slot, `offer()` keeps the higher priority, ties go to the incoming item; `announce`
   is `+inf` and urgent.
2. Loss paths: the four named ones are real. Other ways found in the worker (asked for by the brief), all
   today's behaviour and left as they are:
   - an exception while handling an item (for example `Popen` failing) is caught by the loop's `except` and
     the item is gone, no trace;
   - the speech switch is read once per item **taken** in the main loop. An item claimed mid-utterance as the
     preemptor (`_speak_interruptible`) is spoken even if the switch was turned off in the meantime;
   - the pyttsx3 fallback swallows its own errors (`_speak_blocking`), so a failed fallback looks spoken;
   - the preempt check is `peek()` then `take()`: the slot can change in between, so the claimed item may not be
     the one that was peeked (harmless, same priority rules);
   - an announcement preempts another announcement (a mode change cuts a mode change). Also today's behaviour.
3. No feedback: confirmed. 4. Pacing: confirmed (`_pace` returns early on an urgent pending item).

**One contradiction with the brief's own text (R3).** Criterion 9 says the existing tests pass unchanged, and
`main.py` must start the new worker. `tests/test_main_startup.py` asserts the exact worker list
`["tts-worker", "serial-writer"]` in two tests. Starting a worker changes that list by definition, so I changed
those two expected lists (nothing else in the file), see "Files changed". Every other existing test passes unmodified.

## Hailo docs consulted

Not pipeline-related: no GStreamer, HailoRT or `hailo_apps` call is added or changed (the brief says so too).

## Design, and what I compared

**What "delivered" means.** The scheduler offers one item, a fresh `{"announce": text}` dict, and needs one fact
about it: was it spoken to the end (SPOKEN), cut (CUT), taken and not spoken (DROPPED), or did it vanish from
the slot untaken (replaced by another announcement)?

- *Alternative the brief hints at:* the worker publishes "last finished text + counter" and the scheduler matches
  text. Rejected: a text match cannot tell "taken, being spoken" from "replaced", so the scheduler would have to
  guess from timing, and an alert is easy to double or lose in the gap between the worker's `take()` and its
  report. It also confuses an alert with a mode announcement of the same words.
- *Chosen:* the worker reports by **identity** (the exact dict), and does its three mailbox takes through the
  scheduler (`alerts.take()`), which holds the scheduler's lock while it takes and notes it. The scheduler checks
  "is my item still in the slot, or was it taken?" under the same lock. So the answer is exact, with no timing
  heuristic. The worker side is four calls: `take()`, `drain()`, `finish(item, SPOKEN|CUT|DROPPED)`,
  `release()` (error path).

**Serialising.** One alert item is in flight at a time. The scheduler offers only when the worker is idle
(it knows: the worker is "handling" an item between `take()` and `finish()`) and the slot is empty or holds a
detection item (an alert replaces a waiting detection item exactly as any announcement would, so a busy scene
cannot starve an alert). It never overwrites an announcement. So alerts cannot cut or replace each other, and an
alert never cuts a mode announcement or a detection phrase that is being spoken: it waits for the end (a
deliberate choice; see question 3).

**Head-turn drain: keep the alert out of the drain** (design note 5, option 1). `tts_worker` calls
`alerts.drain()` instead of `mailbox.take()`; it takes whatever is in the slot as before **except** the
scheduler's own waiting item. A mode announcement or a detection item is drained exactly as today (R2 untouched).
Why this route and not detect-and-re-offer: the alert is not stale after a head turn, it is spoken when the
mute window ends, with no churn. Detect-and-re-offer is still there (it is how the tie case is repaired).

**Cadence** is from the start of the last *complete* utterance (the worker's `take()` time); a cut utterance does
not count as heard. After a cut or a replacement the alert is owed at once ("as soon as the channel is free").
So the heard utterances of one alert are never closer than `interval`, and never later than the due time plus
the time the channel was busy with other speech.

**`repeat` may be a function.** `raise_alert(..., repeat=lambda: config.get("depth_enabled"))`: the scheduler
asks it on every tick (outside its lock; an exception keeps the last value). This was not asked for; it is what
lets a task say "every 30 s while depth is on, once otherwise" with no code at the moment depth is switched, and
`set_repeat(name, bool)` does the same by hand. Remove it if you prefer the smaller surface.

**Usage** (also the module docstring, which is criterion 10):

```python
alerts = user_data.alerts
# "wording X every 30 s while depth is on, once otherwise":
alerts.raise_alert("controller-missing", "Controller board not connected",
                   interval=30.0, repeat=lambda: config.get("depth_enabled"))
alerts.clear("controller-missing")        # the condition is over; nothing more is spoken for it
alerts.raise_alert("camera-lost", "Camera disconnected")   # once (no interval)
```

## Files changed

| File | What changed | Why |
|---|---|---|
| `src/second_vision/core/alerts.py` (new, 323 lines) | `AlertScheduler`: producer API `raise_alert`, `set_repeat`, `clear`, `is_raised`, `raised`; `poll()`; worker-side `take`, `drain`, `finish`, `release`. Pure Python, injectable clock, one lock. Docstring = usage guide. | The mechanism (goal, criteria 1-7, 10). |
| `src/second_vision/workers/alert_worker.py` (new, 19 lines) | `alert_worker(user_data, config)`: calls `alerts.poll()` every 0.05 s until `shutdown_event`; an exception is printed, the loop goes on. | The small daemon worker (criterion 8). Same signature style as the others. A new file in `workers/` rather than a function inside `core/`, following the project layout. |
| `src/second_vision/main.py` (+16 lines) | import `AlertScheduler`, `alert_worker`; `self.alerts = AlertScheduler(self.tts_queue)` in `SecondVisionUserData.__init__`; one `threading.Thread(target=alert_worker, ..., daemon=True, name="alert-scheduler")` started right after the TTS worker, appended to `workers`. | Brief: create/start the worker only. Started in mock and pipeline mode alike (before the `if`). |
| `src/second_vision/workers/tts_worker.py` (+33 -5 lines, blank lines not counted) | Every changed place, all guarded by `alerts is not None` (`alerts = getattr(user_data, "alerts", None)`), so without the attribute the code takes exactly the old branch: (1) import `CUT, DROPPED, SPOKEN`; (2) contract comment lines; (3) `_check_turn_mute(..., alerts=None)`: `alerts.drain()` in place of `mailbox.take()`; (4) `tts_worker`: `alerts` read once, passed to (3); the main-loop `mailbox.take()` becomes `alerts.take()`; the speech-switch-off `continue` first calls `alerts.finish(item, DROPPED)`; the `except` branch calls `alerts.release(DROPPED)`; (5) `_speak_interruptible`: the preemptor claim `mailbox.take()` becomes `alerts.take()`, `cut` flag, `alerts.finish(item, CUT or SPOKEN)` after the inner loop, and `alerts.finish(item, SPOKEN)` on the no-espeak fallback path. | Feedback to the scheduler (R1). Preemption, pacing, head-turn mute, volume and the switch-off drop behave as before; the queue item has no new key. |
| `tests/test_alerts.py` (new, 59 tests) | Unit, virtual clock: criteria 1-8 and the 4 randomised loss paths. | Verification. |
| `tests/test_alerts_speech.py` (new, 22 tests, 1 opt-in) | Real `tts_worker` + `PriorityMailbox` + scheduler + `alert_worker` on a stub `espeak-ng`; the program path (`main.py --mock`). | Verification (criteria 1-9). |
| `tests/test_main_startup.py` (2 lines) | `["tts-worker", "serial-writer"]` became `["tts-worker", "alert-scheduler", "serial-writer"]` in `test_t03b_con08_mock_flag_still_runs_mock_mode_without_hailo` and `..._hailo_present_and_no_mock_flag_runs_the_pipeline`. | Not weakened: the assertion still pins the exact worker list; the list is longer because the brief adds a worker. See above (R3). |

Not touched: `core/priority.py`, `serial_worker.py`, `config_reader.py`, `pipeline/`, `core/config.py`, `firmware/`,
`scripts/`, `tests/uat/`, the vision files, installed libraries.

## Tests added or updated

Red-before-green was done two ways, both in scratch trees under the scratchpad, never with git:
**(A)** the baseline `src/` plus the new test files; **(B)** the new `src/` with the **baseline `tts_worker.py`**
put back (new scheduler, old worker), which shows the worker changes matter and that the tests fail for the
real reason. And **mutants** of `alerts.py` and `tts_worker.py`, one deliberate bug each, to show each test
group would catch it.

| Test (group) | What it checks | Fails on the baseline? |
|---|---|---|
| `test_alerts.py` cadence group (`test_spoken_at_once_then_every_interval[10/30/300]`, start-to-start, late raise) | criterion 1: at once (<= 1 s), exactly `{"announce": text}`, every N s | yes (A): `ModuleNotFoundError: second_vision.core.alerts` at collection; mutant M7 (end-to-start cadence) fails 18 tests |
| once / repeat group (6 tests) | criteria 2, 3: once, repeat off after, off before it finished, off before it started, callable repeat, flaky callable, back on (before and after the due time), no storm from flipping | yes (A); mutant M13 fails the "before it started" test |
| clear group (7 tests) | criterion 4: stops at once, utterance under way not cut, queued item removed, never anyone else's item (mode announcement or detection in the slot), clear then raise | yes (A); mutants M8, M9 fail 2 and 1 tests |
| several alerts group (7 tests) | criterion 5: one after the other, each to the end, 10/30/300 together for 620 s, never offered while one is in flight, never overwrites an announcement, waits for a detection phrase, replaces a waiting detection item | yes (A); mutants M4, M6, M11, M14 fail 2, 4, 34, 2 tests |
| not-lost group (11 tests) | criterion 6: cut by a mode announcement, cut by an urgent detection (once-alert too), head turn keeps the alert, drain unchanged for others (R2), replaced by a tie, speech off = delivered with no storm, error path; `drain()` API | yes (A); mutants M1, M2, M3, M5 fail 1, 5, 7, 4 tests |
| raise-again group (6 tests + 7 refused inputs) | criterion 7 | yes (A); mutant M10 fails 1 test |
| threading group (3 tests) | criterion 8: 6 threads x 1500 mixed calls, no block, no error; worker is a daemon and stops within 0.25 s; survives a failing `poll()` | yes (A) |
| `test_randomised_runs_never_lose_double_or_cut_an_alert[cut/drain/tie/off]` | 50 seeded runs per path, 330 s virtual each, three alerts of 10/30/300 s (+ a once-alert on even seeds), random mode announcements and detections; black-box log invariants (never cut by an alert, not doubled, not late beyond busy time, repaired after each loss) | yes (A); mutants M2, M3, M5, M7, M11 fail them |
| `test_alerts_speech.py` guards `..._behave_as_before[False/True]` (2 tests x 2) | criterion 9 with the real worker: a detection phrase, an urgent detection cutting it, a mode announcement cutting that, the volume applied, no gap before an urgent item, head-turn mute and drain, speech switch off | **no, by design**: they pass on the baseline for `with_alerts=False` (that is the proof of "as before"); the `True` variants (scheduler running and idle) fail on the baseline only because the scheduler does not exist |
| `test_alerts_speech.py` behaviour tests (11 tests) | cadence, three alerts one after another, clear, cut by mode announcement, once-alert cut by an urgent detection, head turn keeps the alert, drain still discards others, tie, speech-off no storm, failure while speaking, no-espeak fallback | yes (A), and yes (B): 11 of 11 fail with the baseline worker; worker mutants W1-W6 each fail 1-2 of them |
| `test_real_worker_randomised_runs_...[batch-path]` | the loss paths with the real worker and the stub, `SV_ALERT_RUNS` runs per path (default 1) | yes (A), yes (B): 4 of 4 fail with the baseline worker |
| `test_real_time_cadence_of_10_and_30_seconds_...` | brief's real-time cadence, 96 s, opt-in `SV_REAL_CADENCE=1 PYTEST_TIMEOUT=300` | skipped by default (a skip is not a pass) |
| `test_the_user_data_main_builds_has_a_scheduler_on_the_speech_mailbox`, `test_main_stops_with_exit_0_after_ctrl_c_and_starts_the_alert_worker` | `SecondVisionUserData.alerts` sits on the TTS mailbox; `main.py --mock` starts the worker, stops on Ctrl+C with exit 0 | yes (A); not (B) (they do not depend on the worker feedback) |
| `test_main_startup.py` (2 lines changed) | exact worker list | yes (A), by construction |

**Run (A), final test files:** `test_alerts_speech.py` + `test_main_startup.py` on the baseline `src/`:
`21 failed, 5 passed, 1 skipped` (the 5 are the 2 baseline guards and the 3 `test_main_startup` tests that do not
list workers); `test_alerts.py`: `1 error during collection` (`No module named 'second_vision.core.alerts'`).
**Run (B):** `15 failed, 6 passed, 1 skipped` (the 6 are the 4 guard variants and the 2 `main.py` tests).
**Mutants** (scratch copies, `core/alerts.py`, `tests/test_alerts.py`): M1 drain discards the alert 1 failed;
M2 lost flight not cleared 5; M3 cut counted as delivered 7; M4 overwrites an announcement 2; M5 dropped treated
as cut 4; M6 ignores a busy worker 4; M7 end-to-start cadence 18; M8 clear keeps the queued item 2; M9 clear removes
anyone's item 1; M10 same raise resets 1; M11 two in flight 34; M12 cleared alert replayed 1; M13 repeat-off
stops the owed utterance 1; M14 wrong order 2. All 14 caught. (Four survived the first version of the tests: M1, M2,
M9 and M12 were behaviourally equivalent through the redundancy of the design; I added `drain()` API tests and a
log-line test, and rewrote M2/M9 as non-equivalent mutants. I also deleted three lines in `_step_locked` that
the mutation run showed to be redundant.) Worker mutants (`tts_worker.py`, `tests/test_alerts_speech.py` minus the
randomised tests): W1 drain ignores the alerts 1 failed; W2 cut reported as spoken 2; W3 drop not reported 1;
W4 exception not released 1; W5 preempt claim bypasses the ledger 2; W6 fallback not reported 1. All 6 caught.

## Commands run and their actual results

Environments: `PY=~/.cache/pypoetry/virtualenvs/second-vision-repo-wwT_Rtha-py3.13/bin/python`, `H=my_hailo_env/bin/python`.
Service inactive, no process holding anything, no device used (R5).

```
baseline  $PY -m pytest tests/ -q -p no:cacheprovider        487 passed, 166 skipped in 45.26s
baseline  $H  -m pytest tests/ -q -p no:cacheprovider        564 passed, 89 skipped in 119.89s
final     $PY -m pytest tests/ -q -p no:cacheprovider        567 passed, 167 skipped in 128.50s
final     $H  -m pytest tests/ -q -p no:cacheprovider        644 passed, 90 skipped in 203.70s
```
(+80 passed = 59 unit + 21 speech; +1 skipped = the opt-in real-time cadence test, in both. One earlier $H run
failed on `main.py --mock` stopping 1.61 s after Ctrl+C against my bound of 1.6 s. Not caused by the change:
see below. I raised the bound to the baseline's envelope; the final run above is clean.)

Unit randomised runs (virtual clock, 50 seeds each; `-s` line printed by the test):
```
[T-00a] cut:   50 randomised runs, 1882 forced loss events, no alert lost, doubled or cut by another alert
[T-00a] drain: 50 randomised runs, 11398 forced loss events, ...        (head turns while an alert waits; counted per tick)
[T-00a] tie:   50 randomised runs, 1041 forced loss events, ...
[T-00a] off:   50 randomised runs, 1258 forced loss events, ...
```
Real worker + stub espeak, 20 runs per path, `SV_ALERT_RUNS=20`, each environment (16 tests of 5 runs; `16 passed in 490.72s`
Poetry, `16 passed in 492.57s` my_hailo_env). Forced loss events (a mode announcement injected during an alert
utterance; a head turn pushed with an alert in the slot; a mode announcement tying over an alert in the slot;
speech-switch-off windows), summed over the four batches:

| path | runs | Poetry | my_hailo_env |
|---|---|---|---|
| cut by a mode announcement | 20 | 78 | 78 |
| drained by a head turn | 20 | 77 | 82 |
| replaced by a tie | 20 | 141 | 141 |
| speech switch off | 20 | 59 | 59 |

None of the 160 runs lost, doubled or cut an alert by another alert (the same invariants as the unit runs, read
from the stub's start/end log). An earlier 20-run run in the Poetry env, before I split the test into batches of 5,
gave 78/80/137/59 events and the same result.

Real-time cadence (`SV_REAL_CADENCE=1`, alerts of 10 s and 30 s, 96 s, announcements, detections and head turns at
random, plus a thread that injects a mode announcement into half of the alert utterances), both environments:
```
[T-00a] real time: a10 spoken 8x, 19 cut and repeated, a30 spoken 4x, 4 cut and repeated    (1 passed, 101.7 s)
```
At least the first utterance plus 3 repeats each, in both. The 8 (not 10) is the cuts pushing the schedule back,
as designed: 19 of the alert's utterances were cut and every one was spoken again.

Program path: `main.py --mock` with a stub `espeak-ng`, Ctrl+C after 2.5 s, 6 trials per tree and environment
(seconds from Ctrl+C to exit, all exit 0):
```
my_hailo_env  baseline  1.80 1.64 1.46 1.30 1.12 0.97      with the alert worker  1.80 1.63 1.47 1.29 1.14 0.95
Poetry        baseline  1.75 1.59 1.41 1.25 1.07 0.91      with the alert worker  1.75 1.59 1.41 1.25 1.07 0.91
```
The alert worker adds nothing: identical distributions. Mock mode alone takes 0.91-1.80 s because the mock
detection generator sleeps 2 s between detections (`mock/`, out of scope); see question 5 about "about 1 s".
The alert worker itself stops within 0.05 s (`test_the_alert_worker_is_a_daemon...`, bound 0.25 s). Idle cost
with three alerts raised: 4.1 ms CPU in 5.0 s wall (0.08 % of one core).

Not shown above: `check_outside_writes.py .claude/baselines/T-00a/.stamp`: installed libraries and environments,
other checkouts and the workbench: "nothing changed".

## Acceptance criteria: self-check, not approval

| # | How it is addressed | Evidence level reached |
|---|---|---|
| 1 | Offered at the next 0.05 s tick as exactly `{"announce": text}`, again `interval` after the start of the last complete utterance. Checked at 10, 30, 300 s (virtual) and 1.5 s (real worker); 10 s and 30 s in real time, 96 s, both environments. | `unit` + `mock-integration` (real worker, stub espeak) |
| 2 | `interval=None` is once; repeat off after: no more; off before the first utterance finished, or before it even started: still delivered, once. | `unit` + `mock-integration` (the "before finished" case at unit level; real-worker covers once and clear) |
| 3 | `set_repeat(name, True)`/callable resumes; if the last utterance is more than `interval` ago it speaks at once, then every N. **Literal reading, to put to the user** (question 1). | `unit` |
| 4 | `clear()` stops at once; utterance under way not cut; queued item removed only if it is still the stored one (identity); not-raised clear does nothing. | `unit` + `mock-integration` |
| 5 | One in flight; offered only when the worker is idle and the slot holds no announcement; order by due time then raise order. Random announcements and detections in 50 virtual and 20 real runs per path, 96 s real time. | `unit` + `mock-integration` |
| 6 | Cut, drain, tie, speech-off: see Design; counts above. A once-alert is included. Speech-off counts as delivered. | `unit` + `mock-integration` |
| 7 | Same text and interval: nothing changes (only `repeat` is applied); different: replaced, its queued item pulled; one under way is allowed to finish. | `unit` |
| 8 | Daemon thread, `shutdown_event.wait(0.05)`, no call waits on the speech worker; 6 threads x 1500 calls tested; `main.py --mock` exits 0 on Ctrl+C. The "about 1 s" cannot be shown for mock mode (question 5). | `unit` + `mock-integration` |
| 9 | Guards `..._behave_as_before` with the real worker, scheduler absent (`False`) and running idle (`True`); `False` passes on the baseline. All existing tests pass (two list assertions edited, see above). | `mock-integration` |
| 10 | Module docstring and the snippet above; no timing code in the caller. | n/a (documentation) |

## Not done, limitations, deviations from the brief

- **Scope.** Two things outside the brief's file list: the new `workers/alert_worker.py` (the brief asks for the
  worker but lists only `core/` for the module), and the two-line edit of `tests/test_main_startup.py`.
- **`core/priority.py` not touched.** The mailbox has no "offer only if empty" and no "remove only this item", so two
  windows of a few microseconds remain: (a) a mode announcement offered in the instant between the scheduler's
  look at the slot (empty) and its own `offer()` is replaced by the alert (the tie rule: the incoming item wins);
  (b) in `clear()`, an item that slipped in between the look and the removal is taken out and put straight back
  (a non-atomic round trip, nothing lost unless a third item arrives in that moment). Neither can lose or
  double an alert, only a mode announcement in (a). If you want them exact, the fix is two small methods on
  `PriorityMailbox` (`offer_if_free(item)` and `discard(item)`, about 10 lines under its existing lock). I did not
  because the brief lists the file as out of scope. Say if you want it in attempt 2.
- **Beyond the brief:** `repeat` as a callable (above); `is_raised()` and `raised()`; the `release()` error path;
  input validation (`ValueError` for an empty name or text, or a non-positive interval).
- **Real-time tests are partly opt-in.** The default run uses 1 run per path and no 96 s test, to keep the suite
  about 80 s longer than before (not 10 minutes). `SV_ALERT_RUNS=20` and `SV_REAL_CADENCE=1` (with
  `PYTEST_TIMEOUT=300` in `my_hailo_env`) reproduce what this report quotes. The 96 s test is skipped by default,
  and a skip is not a pass.
- **Observation, not changed (R2):** the head-turn drain also discards a mode announcement, as before. I think
  that is a bug for the panel's confirmation ("depth mode" lost if you turn your head within a second of the
  switch), but it is not this task's.
- **Print under the lock.** `alerts.py` prints a line on raise, clear, cut and replace (rare events). The print
  inside the scheduler's lock could in theory block on a stuck stdout; `tts_worker` prints on every utterance,
  so the exposure is the same as today's. Easy to move out of the lock if you want.

## Untested or simulated behaviour

- **The person hearing it.** No earphones, no real `espeak-ng`: every speech test uses the stub (it records start
  and end and obeys SIGTERM like the real process). The brief wants a `user-observed` check once, with T-01a's
  hands-on session; not done, not required for this decision.
- **The pipeline path of `main()`** (a camera and the Hailo chip) was not run. The worker is started before the
  `if pre_args.mock` split, so it is the same code in both modes; only `--mock` was run end to end. The boot
  service and `scripts/` were not touched or exercised.
- **The panel's real mode announcement and a real head-turn packet:** simulated by offering the same dict and by
  pushing a `TurnEvent`. The serial worker and panel reader were not run.
- **Tolerances.** The real-worker checks allow 0.6 s of idle wait beyond the busy time and 0.15 s for the stub's
  Python start-up. The real `espeak-ng` starts faster than the stub; the cadence figures are for the stub.

## Risks and questions for the Lead

1. **Switching repeat back on (criterion 3, for the user).** Built literally: repeat on again while the board is
   still missing and the last utterance is more than N s old, it speaks at once, then every N. Flipping
   depth off and on quickly cannot cause a storm (the due time is fixed by the last utterance). If the user wants
   "N s after switching on", it is one line in `_Alert.due_at`.
2. **Cadence after a cut or loss.** Measured from the start of the last *complete* utterance, so a cut alert pushes
   its own schedule back by the time it was delayed. The alternative (keep the original grid, replay as an extra)
   would let the user hear the alert fewer than N s after the previous one. Say if the user would rather have that.
3. **An alert waits for a phrase in progress; it does not cut it.** Today's `announce` would cut a detection
   phrase or a mode announcement. I judged "an alert cuts the panel's confirmation" worse than a delay of a
   second or two. A safety alert that must cut speech is a one-line change (drop the busy gate).
4. **The two test-list assertions** in `test_main_startup.py` (above): confirm you accept an edit to a test from
   T-03b.
5. **REQ-DTA-08 "stops in about 1 s".** Measured: baseline mock mode alone already takes up to 1.8 s to stop
   (mock generator sleeps). Real pipeline mode was not measured here. Not caused by this task; may be worth a
   line in DISAGREEMENTS.
6. **Documentation checkpoint (Rule 8).** Two decisions are worth recording; I have not written either:
   `ARCHITECTURE.md` (a new `user_data.alerts` attribute and the worker feedback contract, and the shared-files
   map: `tts_worker.py` and `main.py` now touched by the failsafe tasks) and `DECISIONS.md` ("alerts are exempt
   from the head-turn drain; they obey the speech switch until D-15 is decided"). Do you want them added?
7. **D-15 (open)** is deliberately not decided: alerts are dropped when the speech switch is off and count as
   delivered, as the brief says.
