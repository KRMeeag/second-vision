#!/usr/bin/env python3
"""Run the automated tests and save a dated evidence log with one line per UAT case.

    my_hailo_env/bin/python scripts/uat_evidence.py --task T-03a
    my_hailo_env/bin/python scripts/uat_evidence.py --task T-03a --tests tests/uat/test_tc024_003.py \
        --label tc024 --out /tmp/somewhere
    my_hailo_env/bin/python scripts/uat_evidence.py --task T-03a --label unit -- -m "not device"
    my_hailo_env/bin/python scripts/uat_evidence.py --task T-03a --device --max-minutes 45

What it does:
  1. runs `<this python> -m pytest -v -rA --junitxml=<temp file> <tests>` (default: `tests/`);
     anything after a bare `--` on its command line is handed to pytest unchanged (`-m "not device"`);
     `--device` adds `--run-device`, which switches on the device tests (they are skipped without
     it; they may detach the camera and use the Hailo chip, so stop second-vision.service first);
  2. writes `<out>/<YYYY-MM-DD>-<label>.log` (default out: docs/testing/reports/<task>/evidence/,
     created if needed; an existing log is never overwritten, `-2`, `-3` ... is added);
  3. the log holds: a header (command line, date and time, git commit and whether the tree has
     uncommitted changes, Python and pytest versions, second-vision.service state, whether the
     device level was requested, which test levels were available), pytest's complete output,
     then a case table: one line per distinct UAT case ID found in test names (`tc024_003`,
     `tc_024_003`, `TC-024-003` all become `TC-024-003`) with PASS, FAIL or SKIP (reason), and a
     totals line with pytest's exit code.

The case table is never kinder than the tests: all of a case's tests passed -> PASS; any failed or
errored -> FAIL; all skipped -> SKIP; some passed and some skipped -> SKIP (partial ...), because
part of the case did not run. A skipped test is never reported as a pass.

Time limit: `--max-minutes N` (default 30). pytest is started in a process group of its own. When
the limit passes, that group (the pytest process this script started and what it started, never
a process found by name) gets SIGTERM, and SIGKILL 10 s later if anything is left. Everything
captured so far is written to the log with the line `TIMED OUT after N minutes: the run was
stopped` (in the output and at the top of the case table), and the exit code is 124. A killed
pytest writes no JUnit XML, so a timed-out log has no per-case verdicts; read pytest's output.

Its own exit code is pytest's (124 after a time-out). It runs only pytest (the same Python as
itself), read-only git commands and `systemctl is-active`: no sudo, no service control.
"""

from __future__ import annotations

import argparse
import math
import os
import re
import shlex
import signal
import subprocess
import sys
import tempfile
import threading
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SERVICE_NAME = "second-vision.service"
DEFAULT_MAX_MINUTES = 30.0
KILL_GRACE_SECONDS = 10.0            # between SIGTERM and SIGKILL
OUTPUT_DRAIN_SECONDS = 5.0           # how long to wait for the last output once pytest has gone
TIMEOUT_EXIT_CODE = 124              # the exit code `timeout(1)` uses
TASK_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")
# tc024_003 / tc_024_003 / TC-024-003. Not inside a longer word (etc024_003), not followed by a digit.
CASE_RE = re.compile(r"(?<![A-Za-z0-9])tc[-_]?(\d{3})[-_](\d{3})(?!\d)", re.IGNORECASE)
LEVEL_RE = re.compile(r"^sv-harness (gst|device) level: (.*)$", re.MULTILINE)


# ============================================================
# Parsing and aggregating (no pytest needed)
# ============================================================
def find_case_ids(text: str) -> list[str]:
    """Distinct UAT case IDs in `text`, normalised to `TC-NNN-NNN`, in order of first appearance."""
    seen: list[str] = []
    for match in CASE_RE.finditer(text):
        case_id = f"TC-{match.group(1)}-{match.group(2)}"
        if case_id not in seen:
            seen.append(case_id)
    return seen


@dataclass
class Outcome:
    """One test from the JUnit XML. `status` is passed, failed or skipped."""
    name: str
    status: str
    reason: str = ""



@dataclass
class CaseResult:
    """All tests of one UAT case, and the verdict for the case."""
    case_id: str
    status: str                       # PASS, FAIL or SKIP
    detail: str = ""                  # for SKIP: the reason(s)
    tests: list[Outcome] = field(default_factory=list)


def parse_junit(xml_text: str) -> list[Outcome]:
    """Every `<testcase>` in a pytest JUnit XML, as an Outcome. Raises ET.ParseError if malformed."""
    results = []
    for case in ET.fromstring(xml_text).iter("testcase"):
        classname, name = case.get("classname", ""), case.get("name", "")
        full = f"{classname}.{name}" if classname else name
        failed = case.find("failure") is not None or case.find("error") is not None
        skipped = case.find("skipped")
        if failed:
            node = case.find("failure") if case.find("failure") is not None else case.find("error")
            reason = (node.get("message") or "").strip()
            results.append(Outcome(full, "failed", reason))
        elif skipped is not None:
            reason = (skipped.get("message") or (skipped.text or "")).strip()
            if skipped.get("type") == "pytest.xfail":
                reason = f"expected failure: {reason}"
            results.append(Outcome(full, "skipped", reason))
        else:
            results.append(Outcome(full, "passed"))
    return results


def aggregate(results: list[Outcome]) -> list[CaseResult]:
    """Group tests by UAT case ID and give each case a verdict. Sorted by case ID."""
    by_case: dict[str, list[Outcome]] = {}
    for result in results:
        for case_id in find_case_ids(result.name):
            by_case.setdefault(case_id, []).append(result)
    cases = []
    for case_id in sorted(by_case):
        tests = by_case[case_id]
        passed = [t for t in tests if t.status == "passed"]
        skipped = [t for t in tests if t.status == "skipped"]
        if any(t.status == "failed" for t in tests):
            cases.append(CaseResult(case_id, "FAIL", "", tests))
            continue
        reasons = " | ".join(dict.fromkeys(t.reason or "no reason given" for t in skipped))
        if skipped and not passed:
            cases.append(CaseResult(case_id, "SKIP", reasons, tests))
        elif skipped:
            cases.append(CaseResult(case_id, "SKIP",
                                    f"partial: {len(passed)} passed, {len(skipped)} skipped: {reasons}", tests))
        else:
            cases.append(CaseResult(case_id, "PASS", "", tests))
    return cases


def render_case_table(cases: list[CaseResult], any_tests: bool = True) -> str:
    """The case table: one line per case, then the names of its tests."""
    if not cases:
        return ("No UAT case IDs (tc024_003 / tc_024_003 / TC-024-003) were found in the test names."
                if any_tests else "No test results were recorded.")
    lines = []
    for case in cases:
        verdict = case.status + (f" ({case.detail})" if case.detail else "")
        names = ", ".join(f"{t.name} [{t.status}]" for t in case.tests)
        lines.append(f"{case.case_id}  {verdict}\n    tests: {names}")
    return "\n".join(lines)


def totals_line(results: list[Outcome], cases: list[CaseResult], exit_code: int) -> str:
    """Final line: test and case totals, and pytest's exit code."""
    count = lambda status: sum(1 for r in results if r.status == status)  # noqa: E731
    by_case = lambda status: sum(1 for c in cases if c.status == status)  # noqa: E731
    return (f"TOTALS: {count('passed')} passed, {count('failed')} failed, {count('skipped')} skipped "
            f"({len(results)} tests); cases: {by_case('PASS')} PASS, {by_case('FAIL')} FAIL, "
            f"{by_case('SKIP')} SKIP ({len(cases)} cases); pytest exit code {exit_code}")


def parse_levels(pytest_output: str) -> dict[str, str]:
    """The `sv-harness gst|device level: ...` lines tests/conftest.py puts in pytest's header."""
    return {level: text.strip() for level, text in LEVEL_RE.findall(pytest_output)}


# ============================================================
# Header, log name
# ============================================================
def build_header(*, task: str, command_line: str, when: datetime, git_commit: str, dirty: bool | None,
                 python: str, pytest_version: str, service_state: str, levels: dict[str, str],
                 pytest_cmd: str, device_requested: bool, max_minutes: float) -> str:
    """The log's header. `dirty` is None when git could not say."""
    if dirty is None:
        git = f"{git_commit} (could not tell whether the tree has uncommitted changes)"
    else:
        git = f"{git_commit} with uncommitted task changes" if dirty else f"{git_commit} (clean tree)"
    unknown = "unknown (tests/conftest.py was not loaded by this run)"
    requested = ("yes (--device: pytest was given --run-device)" if device_requested else
                 "no (device tests are skipped; add --device to run them)")
    return "\n".join([
        "UAT EVIDENCE LOG",
        f"Task:                  {task}",
        f"Command line:          {command_line}",
        f"Date and time:         {when:%Y-%m-%d %H:%M:%S} (local)",
        f"Git commit:            {git}",
        f"Python:                {python}",
        f"pytest:                {pytest_version}",
        f"{SERVICE_NAME}: {service_state}",
        f"Device level requested: {requested}",
        f"gst level:             {levels.get('gst', unknown)}",
        f"device level:          {levels.get('device', unknown)}",
        f"Time limit:            {max_minutes:g} minutes (--max-minutes)",
        f"pytest command:        {pytest_cmd}",
    ])


def derive_label(tests: list[str], label: str | None) -> str:
    """The `<what>` part of the log name: --label, else `all` for tests/, else the test files' names."""
    if label:
        return re.sub(r"[^A-Za-z0-9._+-]+", "-", label).strip("-") or "run"
    if not tests:
        return "all"
    stems = [re.sub(r"[^A-Za-z0-9._-]+", "-", Path(t.split("::")[0]).stem or "tests") for t in tests]
    return "+".join(dict.fromkeys(stems))[:60] or "run"


def choose_log_path(out_dir: Path, label: str, day: str) -> Path:
    """`<out_dir>/<day>-<label>.log`, or `-2`, `-3` ... if that exists: logs are never overwritten."""
    candidate = out_dir / f"{day}-{label}.log"
    n = 2
    while candidate.exists():
        candidate = out_dir / f"{day}-{label}-{n}.log"
        n += 1
    return candidate


# ============================================================
# Read-only facts about the machine
# ============================================================
def _capture(command: list[str], cwd: Path | None = None, timeout: int = 20) -> tuple[int, str]:
    try:
        done = subprocess.run(command, capture_output=True, text=True, cwd=cwd, timeout=timeout)
    except (OSError, subprocess.SubprocessError) as exc:
        return 127, f"{type(exc).__name__}: {exc}"
    return done.returncode, ((done.stdout or "") + (done.stderr or "")).strip()


def git_facts() -> tuple[str, bool | None]:
    """(short commit, dirty?) from read-only git; dirty is None if git cannot say."""
    rc, commit = _capture(["git", "rev-parse", "--short", "HEAD"], REPO_ROOT)
    if rc != 0:
        commit = f"unknown ({commit})"
    rc, status = _capture(["git", "status", "--porcelain"], REPO_ROOT)
    return commit, (bool(status.strip()) if rc == 0 else None)


def service_state() -> str:
    """`systemctl is-active second-vision.service`, as printed (or why it could not be read)."""
    rc, out = _capture(["systemctl", "is-active", SERVICE_NAME], timeout=10)
    return out.splitlines()[0] if out else f"unknown (systemctl exit {rc})"


def pytest_version() -> str:
    """First line of `<this python> -m pytest --version`."""
    rc, out = _capture([sys.executable, "-m", "pytest", "--version"])
    return out.splitlines()[0] if out else f"unknown (exit {rc})"


# ============================================================
# The run
# ============================================================
def split_passthrough(argv: list[str]) -> tuple[list[str], list[str]]:
    """Split `argv` at the first bare `--`: (our own arguments, arguments for pytest)."""
    if "--" in argv:
        i = argv.index("--")
        return argv[:i], argv[i + 1:]
    return argv, []


def pytest_command(paths: list[str], junit_path: str, extra: list[str], device: bool = False) -> list[str]:
    """
    The pytest command line: this Python, verbose, all outcomes reported, JUnit XML, then the paths.

    `device` adds `--run-device` (before the passthrough arguments), which switches on the tests
    marked `device`; without it tests/conftest.py skips them.
    """
    flags = ["--run-device"] if device else []
    return [sys.executable, "-m", "pytest", "-v", "-rA", "--color=no", f"--junitxml={junit_path}",
            *flags, *extra, *paths]


@dataclass
class PytestRun:
    """What `run_pytest` saw: pytest's exit code, all its output, and whether the time limit stopped it."""
    exit_code: int
    output: str
    timed_out: bool = False


def _signal_group(pgid: int, sig: int) -> None:
    """
    Send `sig` to the process group `pgid`, which is the group this script created for pytest.

    A group that is already gone, or has a member we may not signal (a root-owned sudo), is
    not an error: the point is to stop what we can.
    """
    try:
        os.killpg(pgid, sig)
    except (ProcessLookupError, PermissionError):
        pass


def stop_process_group(process: subprocess.Popen, grace: float = KILL_GRACE_SECONDS) -> None:
    """
    Stop the pytest process this script started, and whatever it left in its process group.

    `process` must have been started with `process_group=0`, so its group id is its pid and the
    group holds only it and its children. SIGTERM goes to that group; if the process is still
    there after `grace` seconds, SIGKILL goes to the group. A final SIGKILL also covers a child
    that ignored SIGTERM after pytest itself had gone. Nothing is ever found or signalled by name.
    """
    _signal_group(process.pid, signal.SIGTERM)
    try:
        process.wait(timeout=grace)
    except subprocess.TimeoutExpired:
        pass
    _signal_group(process.pid, signal.SIGKILL)
    process.wait()


def run_pytest(command: list[str], max_seconds: float | None = None,
               grace: float = KILL_GRACE_SECONDS, echo=None) -> PytestRun:
    """
    Run pytest from the repository root, echoing its output as it comes.

    pytest gets a process group of its own. If `max_seconds` pass first, `stop_process_group`
    stops it and the result has `timed_out=True` and the output captured so far. On Ctrl-C the
    group is stopped too, and KeyboardInterrupt goes on.
    """
    echo = sys.stdout if echo is None else echo
    process = subprocess.Popen(command, cwd=REPO_ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                               text=True, errors="replace", bufsize=1, process_group=0)
    lines: list[str] = []

    def pump() -> None:
        assert process.stdout is not None
        for line in process.stdout:
            echo.write(line)
            echo.flush()
            lines.append(line)

    reader = threading.Thread(target=pump, daemon=True)
    reader.start()
    timed_out = False
    try:
        try:
            process.wait(timeout=max_seconds)
        except subprocess.TimeoutExpired:
            timed_out = True
            stop_process_group(process, grace)
    except BaseException:                     # Ctrl-C and anything else: never leave pytest running
        stop_process_group(process, grace)
        raise
    reader.join(timeout=OUTPUT_DRAIN_SECONDS)
    output = "".join(lines)
    if reader.is_alive():
        output += "\n[uat_evidence: pytest's output pipe was still open after it exited; the output may be cut]\n"
    return PytestRun(process.returncode, output, timed_out)


def timed_out_line(max_minutes: float) -> str:
    """The line the log carries when the time limit stopped the run."""
    return f"TIMED OUT after {max_minutes:g} minutes: the run was stopped"


def positive_minutes(text: str) -> float:
    """argparse type for --max-minutes: a number above zero (fractions allowed), not infinity."""
    try:
        value = float(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"{text!r} is not a number of minutes") from None
    if not math.isfinite(value) or value <= 0:
        raise argparse.ArgumentTypeError(f"{text!r} must be a number of minutes above 0")
    return value


def main(argv: list[str] | None = None, *, kill_grace: float = KILL_GRACE_SECONDS) -> int:
    parser = argparse.ArgumentParser(description="Run the automated tests and save a dated UAT evidence log.")
    parser.add_argument("--task", required=True, help="task ID, for example T-03a (names the default output folder)")
    parser.add_argument("--tests", nargs="+", default=[], metavar="PATH",
                        help="test files, folders or node IDs (default: tests/)")
    parser.add_argument("--label", help="the <what> in the log name (default: all, or the test file names)")
    parser.add_argument("--out", help="output folder (default: docs/testing/reports/<task>/evidence/)")
    parser.add_argument("--device", action="store_true",
                        help="also run the device tests (adds --run-device to pytest). They may detach the "
                             "camera in software and use the Hailo chip: stop second-vision.service first")
    parser.add_argument("--max-minutes", type=positive_minutes, default=DEFAULT_MAX_MINUTES, metavar="N",
                        help=f"stop pytest if it runs longer than N minutes (default {DEFAULT_MAX_MINUTES:g}); "
                             f"the log says so and the exit code is {TIMEOUT_EXIT_CODE}")
    parser.epilog = "Arguments after a bare -- go to pytest unchanged, for example: -- -m \"not device\""
    own, extra = split_passthrough(sys.argv[1:] if argv is None else argv)
    args = parser.parse_args(own)
    if not TASK_RE.fullmatch(args.task):
        parser.error(f"--task {args.task!r} is not a plain ID such as T-03a")

    out_dir = Path(args.out) if args.out else REPO_ROOT / "docs" / "testing" / "reports" / args.task / "evidence"
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = args.tests or ["tests/"]
    started = datetime.now()

    with tempfile.TemporaryDirectory(prefix="uat_evidence_") as scratch:
        junit_path = Path(scratch) / "junit.xml"
        run = run_pytest(pytest_command(paths, str(junit_path), extra, args.device),
                         max_seconds=args.max_minutes * 60, grace=kill_grace)
        try:
            results = parse_junit(junit_path.read_text())
            table_note = ""
        except (OSError, ET.ParseError) as exc:
            results = []
            table_note = f"No usable JUnit XML was produced ({type(exc).__name__}: {exc}); see pytest's output above."

    exit_code = TIMEOUT_EXIT_CODE if run.timed_out else run.exit_code
    output = run.output
    cases = aggregate(results)
    commit, dirty = git_facts()
    header = build_header(
        task=args.task,
        command_line=shlex.join([sys.executable, *sys.argv]),
        when=started, git_commit=commit, dirty=dirty,
        python=f"{sys.executable} ({sys.version.split()[0]})",
        pytest_version=pytest_version(), service_state=service_state(), levels=parse_levels(output),
        pytest_cmd=shlex.join(["python", "-m", "pytest", "-v", "-rA", "--junitxml=<temp>",
                               *(["--run-device"] if args.device else []), *extra, *paths]),
        device_requested=args.device, max_minutes=args.max_minutes,
    )
    table = table_note or render_case_table(cases, any_tests=bool(results))
    stopped = timed_out_line(args.max_minutes)
    if run.timed_out:
        output = output.rstrip() + f"\n\n{stopped}\n"
        table = f"{stopped}\n{table}"
    body = "\n".join([header, "", "=" * 78, "PYTEST OUTPUT", "=" * 78, output.rstrip(), "",
                      "=" * 78, "UAT CASE TABLE", "=" * 78, table, "", totals_line(results, cases, exit_code), ""])
    log_path = choose_log_path(out_dir, derive_label(args.tests, args.label), f"{started:%Y-%m-%d}")
    log_path.write_text(body)
    print(f"\nEvidence log: {log_path}")
    if run.timed_out:
        print(stopped)
    print(totals_line(results, cases, exit_code))
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
