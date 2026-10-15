# docs/testing: UAT requirements layer

This folder turns the thesis UAT workbook into something developers and testers (people or
agents) can work from, without reinterpreting the spreadsheet each time.

| File | What it is | Who edits it |
|---|---|---|
| `tests/Group2-SV_Test-Tracker.xlsx` | **The UAT workbook: authoritative.** Gitignored; shared outside git. | People only |
| [uat-snapshot.yaml](uat-snapshot.yaml) | Verbatim copy of every module and case, with cell references and a fingerprint of each case's text | Generated: `tools/extract_uat.py` |
| [REQUIREMENTS.md](REQUIREMENTS.md) | Normalized requirements (REQ-xxx-NN): what each case expects, stated so it can be built and checked. Ambiguities are marked, not guessed. | Lead, recording the team's answers |
| [TRACEABILITY.md](TRACEABILITY.md) | UAT case → requirement → subsystem → task → test → status → evidence | Lead |
| [TASK-BOARD.md](TASK-BOARD.md) | Implementation tasks: state, priority, dependencies, Developer model, blockers, and the one active task | Lead |
| [reports/](reports/) | Per-task briefs, Developer reports, Tester reports, and Lead decisions, with templates | Each role writes its own |
| [DISAGREEMENTS.md](DISAGREEMENTS.md) | Where the workbook and the code disagree, the decisions, and workbook defects to fix | Lead; decisions come from the team |
| [DEVIATIONS.md](DEVIATIONS.md) | Every test run with different steps or inputs than the workbook | Tester |
| [AGENT-GUIDE.md](AGENT-GUIDE.md) | How to run the agent workflow end to end: setup, starting the Lead, what happens in a task, when it needs you, committing, troubleshooting | Hand-written |
| [MANUAL-TESTS.md](MANUAL-TESTS.md) | Every UAT case that needs a person (plugging hardware, listening, feeling), how a hands-on session runs, and each task's step-by-step procedure | Lead (procedures); the case list is generated |
| [tools/extract_uat.py](tools/extract_uat.py) | Reads the workbook (never writes it) and refreshes the snapshot | — |
| [tools/check_traceability.py](tools/check_traceability.py) | Read-only consistency check: every case traced once, every cited requirement and task defined, the workbook unchanged, the manual-test list current; prints the status counts | — |
| [tools/manual_tests.py](tools/manual_tests.py) | Regenerates the case list in MANUAL-TESTS.md from the snapshot and TRACEABILITY.md | — |

## The agent workflow

Where a checkout has the local `.claude/` workflow (see AGENTS.md, "Agent workflow"), these
files are run by three roles:

| Role | Does | May edit |
|---|---|---|
| **Lead**: main session, started with `.claude/tools/lead.sh` (Sonnet; `--opus` for pipeline tasks) | Splits requirements into small tasks, picks the Developer model, briefs, reviews, accepts / returns / blocks, keeps the records | `docs/testing/` (not the snapshot), `documentation/` |
| **Developer**: `developer-sonnet` or `developer-opus` | Implements one task, writes and runs tests, reports honestly | `src/`, `tests/`, `scripts/`, its report |
| **Tester**: `tester` (Sonnet; Opus when the Developer was Opus) | Verifies every task independently; PASS / FAIL / BLOCKED with evidence | `tests/uat/`, its report, `DEVIATIONS.md` |

The gate:
1. One task at a time.
2. The Lead briefs, then the Developer implements, then the Tester verifies, then the Lead
   decides.
3. Only an accepted task, or a blocked one that the Lead has parked (its changes set aside,
   the reason written down, the user asked), frees the slot for the next.

Nobody in the workflow runs git writes; the user commits. A hook
(`.claude/hooks/role_guard.py`) keeps each role to its areas, for the file tools and for shell
commands. In the shell it also blocks sudo, package installs, service control, killing processes by
name and git writes. sudo has two exceptions: `sudo fuser -v|-k /dev/hailo0` (`-k` only while
`second-vision.service` is stopped), and the simulated camera unplug
`sudo .claude/tools/usb_sim.py`. Reading command text can't catch everything, so after each
task the Lead also checks the installed libraries for changed files. Lifecycle states are in
TASK-BOARD.md and report templates in `reports/README.md`.

## Precedence

1. The workbook wins.
2. The exception is a DISAGREEMENTS.md decision that the code wins or that a case is out of
   scope. That holds until the workbook is corrected, then the entry is closed.
3. REQUIREMENTS.md never adds behaviour the workbook doesn't state.

## When the workbook changes

```bash
python3 docs/testing/tools/extract_uat.py           # refresh the snapshot, list what changed
python3 docs/testing/tools/extract_uat.py --check   # list only; exit 1 if anything differs
```

The tool prints one line per difference:
- `ADDED` / `REMOVED` / `CHANGED`: the case's steps, input or expected text differ. Update
  that case's requirement and traceability row **in the same commit** as the new snapshot.
- `RESULT`: only the recorded status, actual result or date changed. Copy it into the
  *Workbook* column of TRACEABILITY.md.

When a workbook edit applies a correction listed in DISAGREEMENTS.md, mark that entry CLOSED.

The tool needs only Python 3 and PyYAML. It looks for the workbook at the path above; pass
`--xlsx PATH` otherwise.

## Scope rules (decided 2026-10-11 and 2026-10-12)

- **Robustness and feedback only; the vision algorithms are not changed.** The detection
  model and inference, the depth post-processing math, and how depth becomes motor strength
  work and were tested by hand. Work from these documents verifies them and never re-tunes
  them.
- **Mock mode is for testing only.** It is reachable only through the `--mock` command-line
  flag; a deployed device must never switch into it on its own (D-13).
- **No library edits, Hailo docs first.** Installed libraries are never edited; every change
  stays in this repo. Anything touching the Hailo pipeline is checked against the hailo-apps
  docs and examples, not memory. These are AGENTS.md Rules 1 and 6, which apply to everyone.

- **ESP32 cases are outside the agentic workflow.** Cases that run on the ESP32 itself
  (TC-010 to 012, TC-019 to 021) are tested by hand.
- **Don't fight an unreachable board.** If the Pi can't reach an ESP32 after 3 attempts
  during a run, skip the cases that need it and record why. Never try to flash firmware;
  the board may simply not have the current firmware yet.
- **Steps and inputs may change; expected results may not.** See DEVIATIONS.md.
- **Ambiguous means ask.** A requirement marked AMBIGUOUS isn't tested pass/fail until a
  person answers its question.
- **P1 first.** The critical failsafes come first: loss of connection and camera checks
  (tasks T-01 to T-04, plus the cases already built). Everything else follows.
- **Motor duty up to 255 is safe** (D-01). Pi-side tests may drive the motors at full scale.

## Running the existing unit tests on the shared Pi

`poetry run pytest` doesn't find the project environment from a git worktree. Poetry ties
environments to the folder path, so it silently creates a new, empty one. Use the base
checkout's environment directly:

```bash
~/.cache/pypoetry/virtualenvs/second-vision-repo-wwT_Rtha-py3.13/bin/python -m pytest tests/ -q
```

This still tests the worktree's own code: the project isn't installed into that
environment, and each test file puts its own repo's `src/` first on the import path.
