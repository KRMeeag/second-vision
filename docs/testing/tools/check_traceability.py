#!/usr/bin/env python3
"""Consistency check for the UAT requirements layer in docs/testing/.

    python3 docs/testing/tools/check_traceability.py

Checks, without changing anything:
  - the workbook still matches the fingerprint recorded in uat-snapshot.yaml;
  - every snapshot case has exactly one row in TRACEABILITY.md, and no row is unknown;
  - every requirement ID cited in TRACEABILITY.md is defined in REQUIREMENTS.md;
  - every task ID cited in TRACEABILITY.md exists on TASK-BOARD.md;
  - the generated case list in MANUAL-TESTS.md is current (tools/manual_tests.py).
Prints the count of cases per status (to copy into TRACEABILITY.md's totals) and the
requirements still marked AMBIGUOUS. Exits 1 if any check fails.

Standard library plus PyYAML.
"""

from __future__ import annotations

import collections
import hashlib
import re
import sys
from pathlib import Path

import yaml

TESTING = Path(__file__).resolve().parents[1]
REPO = TESTING.parents[1]


def main() -> int:
    """Entry point; returns the process exit code."""
    snap = yaml.safe_load((TESTING / "uat-snapshot.yaml").read_text())
    trace = (TESTING / "TRACEABILITY.md").read_text()
    reqs = (TESTING / "REQUIREMENTS.md").read_text()
    board = (TESTING / "TASK-BOARD.md").read_text()
    problems: list[str] = []

    xlsx = REPO / snap["workbook"]["file"]
    if xlsx.exists():
        if hashlib.sha256(xlsx.read_bytes()).hexdigest() != snap["workbook"]["sha256"]:
            problems.append("workbook differs from the snapshot: run tools/extract_uat.py")
    else:
        print(f"note: workbook not present at {xlsx}; fingerprint not checked")

    matrix = trace.split("## Matrix", 1)[1].split("## Requirements with no UAT case", 1)[0]
    rows = [line for line in matrix.splitlines() if line.startswith("| TC-")]
    seen: collections.Counter[str] = collections.Counter()
    status: collections.Counter[str] = collections.Counter()
    req_refs: set[str] = set()
    task_refs: set[str] = set()
    for line in rows:
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        seen[cells[0].split(" ")[0]] += 1
        status[cells[5].split(" (")[0].strip()] += 1
        for grp in re.finditer(r"REQ-([A-Z]+)-(\d+)((?:,\s*\d+)*)", cells[1]):
            req_refs.add(f"REQ-{grp.group(1)}-{grp.group(2)}")
            for n in re.findall(r"\d+", grp.group(3)):
                req_refs.add(f"REQ-{grp.group(1)}-{int(n):02d}")
        task_refs.update(re.findall(r"T-\d{2}[a-z]?", cells[3]))

    cases = set(snap["cases"])
    defined = set(re.findall(r"REQ-[A-Z]+-\d{2}", reqs))
    board_tasks = set(re.findall(r"^\| (T-\d{2}[a-z]?) \|", board, re.M))
    for label, items in (
        ("cases missing from the matrix", sorted(cases - set(seen))),
        ("matrix rows for unknown cases", sorted(set(seen) - cases)),
        ("cases with more than one row", sorted(k for k, v in seen.items() if v > 1)),
        ("requirement IDs cited but not defined", sorted(req_refs - defined)),
        ("task IDs cited but not on the task board", sorted(task_refs - board_tasks)),
    ):
        if items:
            problems.append(f"{label}: {', '.join(items)}")

    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import manual_tests  # noqa: E402  (sibling tool)
    if manual_tests.current() != manual_tests.render():
        problems.append("MANUAL-TESTS.md case list is out of date: run tools/manual_tests.py")

    print(f"{len(rows)} matrix rows, {len(cases)} snapshot cases, {len(defined)} requirements")
    print("cases per status:")
    for key, count in sorted(status.items()):
        print(f"  {key}: {count}")
    untraced = sorted(defined - req_refs)
    print(f"requirements with no UAT case: {', '.join(untraced) or 'none'}")
    ambiguous = re.findall(r"^\*\*(REQ-[A-Z]+-\d{2})[^*\n]*AMBIGUOUS", reqs, re.M)
    print(f"requirements still AMBIGUOUS: {', '.join(ambiguous) or 'none'}")
    for p in problems:
        print(f"PROBLEM: {p}")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
