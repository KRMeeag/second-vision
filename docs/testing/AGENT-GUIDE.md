# Using the agent workflow: an end-to-end guide

How to run the Lead / Developer / Tester agents on the UAT tasks, from first setup to
committing an accepted task. It needs the local `.claude/` folder, which only
`worktrees/kenth` has at the moment (it is gitignored, so it doesn't travel through git).

## 1. What it is

You talk to one agent, the **Lead**. It picks the next task from
[TASK-BOARD.md](TASK-BOARD.md), writes a brief, and hands the work to two helpers it starts
itself:

| Role | Does | Model | Effort | May change |
|---|---|---|---|---|
| **Lead** | Plans, briefs, reviews, accepts or returns, keeps the records, talks to you | Sonnet; Opus if you start it with `--opus` | xhigh | `docs/testing/`, `documentation/` |
| **Developer** | Implements one task and its tests | Sonnet; Opus for pipeline, threading and high-risk tasks | xhigh | `src/`, `tests/`, `scripts/`, its report |
| **Tester** | Checks the work independently and tries to break it | Sonnet; Opus when the Developer was Opus | xhigh | `tests/uat/`, its report, `DEVIATIONS.md` |

One task at a time. Every task is tested. Nothing is committed by an agent: **you** review and
commit. You are also the person for the hands-on steps (unplugging hardware, listening,
feeling the motors).

## 2. One-time setup

1. **Work in the checkout that has `.claude/`**: `~/Projects/Second-Vision/worktrees/kenth`.
   Open it in VS Code and use the integrated terminal.
2. **Install the test tools into the app's environment** (agents may not change
   environments). Without them, nothing that touches GStreamer or the Hailo chip can be
   tested automatically:
   ```bash
   /home/sv-rpi5/Projects/Second-Vision/second-vision-repo/my_hailo_env/bin/pip install pytest pytest-timeout
   ```
   This only adds pytest, pluggy, iniconfig and pytest-timeout; it upgrades nothing.

## 3. Starting a session

From the worktree's root:

```bash
.claude/tools/lead.sh            # the Lead on Sonnet: most tasks
.claude/tools/lead.sh --opus     # the Lead on Opus: pipeline or threading tasks
```

The script sets extra-high effort and finds the Claude Code binary that comes with the VS Code
extension. On start, the Lead:
- checks that the UAT workbook hasn't changed, and stops if it has;
- reads the board;
- continues an unfinished task before anything new.

Tips:
- **Run one Lead per checkout.** Two would work on the same files.
- **Start a fresh session for each task.** The board and the reports hold everything the
  Lead needs, and a short conversation uses less of the plan's limits than one long one.
  `.claude/tools/lead.sh -c` continues the last conversation instead.
- **Before an Opus task, the Lead says so**, so you can restart it with `--opus` for that task.

## 4. Telling the Lead what to do

Plain requests work. Examples:

| You want | Say |
|---|---|
| An overview | "What's on the board, and what's next?" |
| One task | "Go ahead with T-10a." |
| A run of tasks | "Work through the P1 tasks in the suggested order. Stop whenever you need me." |
| A plan only | "Plan T-01 and write its brief, but don't start it." |
| A blocked task again | "I've done what T-03a was waiting for. Resume it." |
| Your own wording | Paste it. The Lead's rules still apply. |

## 5. What happens during a task

| Step | Who | What you see | Files |
|---|---|---|---|
| 1. Analyse and brief | Lead | Its plan, any questions for you | `reports/<task>/brief-1.md`, a board row |
| 2. Snapshot | Lead | — | `.claude/baselines/<task>/` (local) |
| 3. Implement | Developer | The Lead waits; the terminal shows the Developer working | `src/`, `tests/`, `reports/<task>/dev-report-1.md` |
| 4. Verify | Tester | The Lead waits; the terminal shows the Tester working | `tests/uat/`, `reports/<task>/test-report-1.md`, `reports/<task>/evidence/` |
| 5. Hands-on steps, if any | You, guided by the Lead | One step at a time | Your answers, quoted in the decision |
| 6. Review | Lead | Its findings | — |
| 7. Decide | Lead | ACCEPTED, RETURNED or BLOCKED | `reports/<task>/decision-1.md` |
| 8. Records | Lead | The list of files to commit | Board, traceability, `documentation/TASKS.md` |

**RETURNED** means the Tester or the Lead found a problem. The Lead writes attempt 2 with the
evidence, and the Developer and Tester go again; you don't need to do anything.
**BLOCKED** means something only you can provide is missing (section 6). The Lead sets the
task's changes aside ("parks" it), asks you, and may move on to the next task meanwhile.

## 6. When the Lead needs you

**A question.** Requirements are never guessed. If something is ambiguous, the Lead asks,
explaining what each option means, and records your answer, dated, in REQUIREMENTS.md or
DISAGREEMENTS.md.

**Something needing root.** Agents can't use sudo, except to free the Hailo chip
(`sudo fuser -k /dev/hailo0`, only while the boot service is stopped) and to simulate a
camera unplug (`.claude/tools/usb_sim.py`). For anything else, the Lead shows you the exact
command and why. Run it yourself, or say no.

**Hands-on steps.** The procedure is in [MANUAL-TESTS.md](MANUAL-TESTS.md).
1. Do the checklist "Before a hands-on session". Most importantly, **you** stop the boot
   service: `sudo systemctl stop second-vision`.
2. The Lead gives one step at a time. Do it, then say what you saw, heard or felt in your own
   words, for example: "the vibration stopped after about two seconds".
3. Afterwards, start the service again: `sudo systemctl start second-vision`.

Each device is confirmed by hand **once per scenario**. Everything else is covered by
automated tests (TRACEABILITY.md, "Evidence").

**Committing.** After ACCEPTED, the Lead lists the changed files.
1. Look over `git diff`.
2. Optionally run the tests yourself:
   ```bash
   ~/.cache/pypoetry/virtualenvs/second-vision-repo-wwT_Rtha-py3.13/bin/python -m pytest tests/ -q
   ```
3. Commit them yourself, with no Claude co-author line.

The next task starts only after this.

## 7. Evidence for the thesis panel

- Every test run the Tester relies on is saved under `docs/testing/reports/<task>/evidence/`,
  with the command, date and time, commit, and per-test PASS/FAIL. Case IDs are in the test
  names. Screenshot it, or rerun the command shown in the log and screenshot the terminal.
- [TRACEABILITY.md](TRACEABILITY.md) links each UAT case to its test, status and evidence.
- Your hands-on answers are quoted, with times, in each task's `decision-<n>.md`.

## 8. When something goes wrong

| Symptom | What to do |
|---|---|
| The session stopped mid-task (usage limit, network, closed terminal) | Start the Lead again. It reads the board and continues the active task. |
| An agent shows `[role_guard] Blocked: …` | That is the safety hook working. The agent adapts or reports to the Lead. |
| You want to undo a task's changes | Ask the Lead to revert the task from its baseline, or use git yourself. |
| The camera seems gone after a test | `sudo .claude/tools/usb_sim.py attach --all`, or replug it, or reboot. The tool also re-attaches by itself after its `--for` time. |
| "No available devices" or the Hailo chip is busy | Check the boot service is stopped (`systemctl is-active second-vision`) and that no old pipeline runs (`pgrep -af "main.py\|Parallel-Depth"`). |
| The Lead says the workbook changed | Expected after you edit the Excel file. It walks you through updating the requirements first. |
| Tests can't find GStreamer or the Hailo chip | Do setup step 2, and check the boot service is stopped. |
| The camera drops out with nobody touching it | Tell the Lead or a teammate. It happened once on 2026-10-15 and is being watched. |

## 9. What the agents will never do

- Commit, push, check out or otherwise write to git.
- Use sudo, except for the two cases in section 6.
- Start or stop services, reboot, or kill processes by name.
- Install or remove packages, or change any installed library or environment.
- Change the vision algorithms (detection processing, depth math, the depth-to-motor mapping),
  ESP32 firmware, or the UAT workbook.
- Switch the device into mock mode on its own.
- Start a second implementation task while one is unfinished.

A hook enforces most of this, for edits and for typed shell commands. Code that an agent
writes could still run commands the hook doesn't see. So after every task the Lead checks
that no installed library changed, and that the diff calls `sudo` only through the camera
simulator.

## 10. Where things are

| What | Where |
|---|---|
| Tasks, priorities, the active task | [TASK-BOARD.md](TASK-BOARD.md) |
| What each UAT case requires | [REQUIREMENTS.md](REQUIREMENTS.md) |
| Case → requirement → task → test → status → evidence | [TRACEABILITY.md](TRACEABILITY.md) |
| Hands-on procedures, and which cases need a person | [MANUAL-TESTS.md](MANUAL-TESTS.md) |
| Decisions on workbook vs code | [DISAGREEMENTS.md](DISAGREEMENTS.md) |
| Briefs, reports, decisions, evidence logs | [reports/](reports/) |
| The agents' instructions, the hook, the tools (local) | `.claude/agents/`, `.claude/hooks/`, `.claude/tools/` |

## 11. Limits

- **The safety hook reads command text.** It catches mistakes, not a determined script; the
  Lead's checks after each task cover the rest. This Pi allows passwordless sudo for the
  `sv-rpi5` user. Limiting that to the commands the project needs would make this sturdier;
  it's a system setting, so it's yours to decide.
- **Starting the Lead from the VS Code chat panel** (the `agent` setting) hasn't been tested.
  Use the terminal command above.
- **Teammates have none of this** unless they copy `.claude/`. `AGENTS.md` and
  `docs/testing/` work without it.
