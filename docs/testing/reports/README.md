# Task reports

One folder per task, named after its board ID: `reports/T-03a/`. Attempts are numbered;
a task returned for fixes gets attempt 2, then 3. Files are never overwritten; each attempt
adds new ones.

| File | Written by | When |
|---|---|---|
| `brief-<n>.md` | Lead | Before the Developer starts attempt *n* |
| `dev-report-<n>.md` | Developer | When attempt *n*'s implementation is done |
| `test-report-<n>.md` | Tester | When verification of attempt *n* is done |
| `decision-<n>.md` | Lead | After reviewing attempt *n*: ACCEPTED, RETURNED or BLOCKED |

**Baselines.** Before attempt 1 the Lead copies `src/`, `tests/` and `scripts/` to
`.claude/baselines/<task>/`. That folder is local and not committed. Later attempts reuse it.
The task's own changes, across all attempts, are then exactly:

```bash
for d in src tests scripts; do diff -ruN -x __pycache__ .claude/baselines/<task>/$d $d; done
```

This holds even when earlier accepted work hasn't been committed yet.

**Evidence levels**, as in TRACEABILITY.md:

| Level | Meaning |
|---|---|
| `unit` | Automated test, no hardware |
| `mock-integration` | Several components together, hardware replaced by mocks or fixtures |
| `device-integration` | Real Pi devices connected |
| `physical-measurement` | A measured physical quantity |
| `user-observed` | A person performed or judged it; the Lead quotes what they reported |

A mocked test never proves a physical component works.

---

## Template: `brief-<n>.md` (Lead)

```markdown
# <task> · <title> · Brief, attempt <n>

- Date: YYYY-MM-DD
- Requirements: REQ-… (quote each statement)
- UAT cases: TC-… (see uat-snapshot.yaml for the verbatim text)
- Priority / depends on:
- Developer: developer-sonnet | developer-opus. Why: <one or two sentences against the routing rubric>
- Baseline: .claude/baselines/<task>/ (taken YYYY-MM-DD HH:MM, before attempt 1)

## Goal
<one paragraph: the behaviour that must exist when this is done>

## In scope
<files or modules the Developer may change, and what kind of change>

## Out of scope: must not change
<interfaces, files, behaviour to preserve; always: the vision algorithms, installed libraries, firmware>

## Acceptance criteria
1. <checkable statement, tied to a REQ ID>
2. …

## Verification expectations
| Criterion | Minimum evidence level | Hardware needed |
|---|---|---|

## References
<AGENTS.md rules that matter; hailo-apps docs and examples to consult for pipeline work (paths);
earlier reports>

## Open risks / questions
<anything the Developer must stop and report on instead of guessing>

## For attempt > 1: failures to fix
<quote the failing checks from test-report-<n-1>.md or decision-<n-1>.md>
```

## Template: `dev-report-<n>.md` (Developer)

```markdown
# <task> · Developer report, attempt <n>

- Date / model: YYYY-MM-DD / developer-sonnet | developer-opus
- Summary: <two or three sentences>

## Inspected before changing
<files and functions read; callers confirmed; existing tests read>

## Hailo docs consulted
<path or URL, and what it confirmed; or "not pipeline-related">

## Files changed
| File | What changed | Why |
|---|---|---|

## Tests added or updated
| Test | What it checks | Fails on the baseline? |
|---|---|---|

## Commands run and their actual results
<command, then the real summary lines; nothing paraphrased as "all good">

## Acceptance criteria: self-check, not approval
| # | How it is addressed | Evidence level reached |
|---|---|---|

## Not done, limitations, deviations from the brief

## Untested or simulated behaviour
<everything that would need hardware or a person to confirm>

## Risks and questions for the Lead
```

## Template: `test-report-<n>.md` (Tester)

```markdown
# <task> · Verification report, attempt <n>

- Date: YYYY-MM-DD
- Overall: PASS | FAIL | BLOCKED
- Inputs: brief-<n>.md, dev-report-<n>.md, baseline .claude/baselines/<task>/

## Diff review
<diff command; files changed; any change NOT listed in the dev report; any change outside the
brief's scope; any change to protected files (vision algorithms, libraries, firmware)>

## Checks
| # | What (criterion / REQ / TC) | Method | Evidence level | Result | Evidence |
|---|---|---|---|---|---|
| 1 | | | | PASS / FAIL / BLOCKED | <command + output excerpt, or file> |

## Regression
<full unit-suite command and its actual result line>

## Edge cases and failure conditions tried

## Failures
<for each FAIL: steps to reproduce, expected, actual>

## Blocked
<for each BLOCKED: what is missing: hardware, access, a decision>

## Simulated only
<behaviour checked only against mocks or fixtures, named as such>

## Test-only changes
<permanent: path, and why it belongs; temporary: where, and whether removed>

## Deviations recorded
<DV-NNN in DEVIATIONS.md, or "none">

## Manual steps needed from a person
<numbered physical steps and what to observe, for the Lead to ask the user>
```

## Template: `decision-<n>.md` (Lead)

```markdown
# <task> · Lead decision, attempt <n>

- Date: YYYY-MM-DD
- Decision: ACCEPTED | RETURNED | BLOCKED
- Based on: brief-<n>.md, dev-report-<n>.md, test-report-<n>.md, own diff review

## Acceptance criteria
| # | Met? | Evidence (report + level) |
|---|---|---|

## User-observed evidence
<what the user was asked, and their answer quoted; or "none">

## Outstanding risks accepted, and why

## If RETURNED: what must be fixed
## If BLOCKED: what is needed, and from whom; whether its changes are kept or reverted (the user's choice)

## Records updated
- TASK-BOARD.md: <row>
- TRACEABILITY.md: <TC IDs and their new status/evidence>
- documentation/TASKS.md: <box ticked, or n/a>
- DISAGREEMENTS.md / REQUIREMENTS.md: <new entries, or none>

## For the user to commit
<files changed by this task; the user commits; agents never run git writes>
```
