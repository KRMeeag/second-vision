#!/usr/bin/env python3
"""Snapshot the UAT workbook into docs/testing/uat-snapshot.yaml.

The Excel tracker is the authoritative UAT document. This tool only READS it:
it copies every module and test case verbatim into a YAML snapshot that agents
and teammates can read without Excel, and fingerprints each case's text so the
derived documents (REQUIREMENTS.md, TRACEABILITY.md) can tell when the
workbook has been edited underneath them.

    python3 docs/testing/tools/extract_uat.py            # refresh the snapshot
    python3 docs/testing/tools/extract_uat.py --check    # report only, exit 1 on drift
    python3 docs/testing/tools/extract_uat.py --xlsx PATH

Every run prints which cases were added, removed, or had their steps / input /
expected text changed since the previous snapshot. Each one listed is a case
whose requirement and traceability rows must be re-checked by a person.

Standard library plus PyYAML (present in system python3, my_hailo_env and the
Poetry env). No spreadsheet library is needed: an .xlsx is zipped XML.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import re
import sys
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_XLSX = REPO_ROOT / "tests" / "Group2-SV_Test-Tracker.xlsx"
DEFAULT_OUT = REPO_ROOT / "docs" / "testing" / "uat-snapshot.yaml"

SCENARIOS_SHEET = "Test Case Scenarios"
CASES_SHEET = "Test Cases"

NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
      "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
      "pr": "http://schemas.openxmlformats.org/package/2006/relationships"}

CASE_ID = re.compile(r"^TC-\d{3}-\d{3}$")
SCENARIO_ID = re.compile(r"^TC-(\d+)$")
CELL_REF = re.compile(r"^([A-Z]+)(\d+)$")

# Labels in column A of the Scenarios sheet that may continue onto the next rows
# (column A blank, column B holding the next numbered item).
MULTI_ROW_FIELDS = {"Prerequisites", "Environmental Information"}


# ---------------------------------------------------------------- xlsx reading

def _text_of(el: ET.Element) -> str:
    return "".join(t.text or "" for t in el.iter(f"{{{NS['m']}}}t"))


def read_sheets(path: Path) -> dict[str, dict[int, dict[str, str]]]:
    """Return {sheet name: {row number: {column letter: cell text}}}.

    Formulas are ignored in favour of their cached values; empty cells are
    omitted. Cell text is returned exactly as stored.
    """
    z = zipfile.ZipFile(path)
    shared = []
    if "xl/sharedStrings.xml" in z.namelist():
        for si in ET.fromstring(z.read("xl/sharedStrings.xml")).findall("m:si", NS):
            shared.append(_text_of(si))
    wb = ET.fromstring(z.read("xl/workbook.xml"))
    rels = {r.get("Id"): r.get("Target") for r in
            ET.fromstring(z.read("xl/_rels/workbook.xml.rels")).findall("pr:Relationship", NS)}
    sheets: dict[str, dict[int, dict[str, str]]] = {}
    for sh in wb.find("m:sheets", NS).findall("m:sheet", NS):
        target = rels[sh.get(f"{{{NS['r']}}}id")].lstrip("/")
        part = target if target.startswith("xl/") else f"xl/{target}"
        rows: dict[int, dict[str, str]] = {}
        for c in ET.fromstring(z.read(part)).iter(f"{{{NS['m']}}}c"):
            col, row = CELL_REF.match(c.get("r")).groups()
            t, v = c.get("t"), c.find("m:v", NS)
            if t == "s" and v is not None:
                val = shared[int(v.text)]
            elif t == "inlineStr":
                val = _text_of(c)
            elif v is not None:
                val = v.text
            else:
                continue
            if val is not None and val.strip() != "":
                rows.setdefault(int(row), {})[col] = val
        sheets[sh.get("name")] = rows
    return sheets


def _clean(value: str | None) -> str | None:
    if value is None:
        return None
    value = value.rstrip()
    return value if value.strip() else None


def _excel_date(value: str | None) -> str | None:
    """Excel serial day number -> ISO date; anything else is returned as written."""
    if value is None:
        return None
    try:
        serial = float(value)
    except ValueError:
        return value
    return (dt.date(1899, 12, 30) + dt.timedelta(days=int(serial))).isoformat()


def _text_hash(*parts: str | None) -> str:
    """Fingerprint of a case's wording, insensitive to whitespace-only edits."""
    norm = "\x1f".join(" ".join((p or "").split()) for p in parts)
    return hashlib.sha256(norm.encode()).hexdigest()[:12]


# ---------------------------------------------------------------- parsing

def parse_modules(rows: dict[int, dict[str, str]], defects: list[str]) -> dict[str, dict]:
    """Parse the Scenarios sheet: one block per module, starting at 'Type of Test'."""
    modules: dict[str, dict] = {}
    current: dict | None = None
    field: str | None = None
    for r in sorted(rows):
        a, b = _clean(rows[r].get("A")), _clean(rows[r].get("B"))
        if a == "Type of Test":
            current = {"name": None, "type_of_test": b, "case_range": None, "tester": None,
                       "description": None, "prerequisites": [], "environment": [],
                       "scenarios": {}}
            modules[f"M{len(modules) + 1:02d}"] = current
            field = None
            continue
        if current is None:
            continue
        if a is None:
            if b is not None and field in MULTI_ROW_FIELDS:
                key = "prerequisites" if field == "Prerequisites" else "environment"
                current[key].append(b)
            continue
        field = a
        if a == "Module Name":
            current["name"] = b
        elif a == "Test Case ID":
            current["case_range"] = b
        elif a == "Tester Name":
            current["tester"] = b
        elif a == "Test Case Description":
            current["description"] = b
        elif a == "Prerequisites" and b:
            current["prerequisites"].append(b)
        elif a == "Environmental Information" and b:
            current["environment"].append(b)
        elif m := SCENARIO_ID.match(a):
            sid = f"TC-{int(m.group(1)):03d}"
            current["scenarios"][sid] = b
            if sid != a:
                defects.append(f"Scenarios sheet row {r}: scenario ID written '{a}', read as {sid}")
    return modules


def parse_cases(rows: dict[int, dict[str, str]], modules: dict[str, dict],
                defects: list[str]) -> dict[str, dict]:
    """Parse the Test Cases sheet into cases, and attach each block header to its module."""
    module_of = {sid: mid for mid, mod in modules.items() for sid in mod["scenarios"]}
    cases: dict[str, dict] = {}
    block: dict | None = None
    block_field: str | None = None
    scenario_title: str | None = None
    for r in sorted(rows):
        cells = rows[r]
        a, b = _clean(cells.get("A")), _clean(cells.get("B"))
        if a == "Module Name:":
            block = {"label": b, "tester": _clean(cells.get("D")), "date_tested": None,
                     "description": None, "prerequisites": [], "environment": [],
                     "first_row": r, "module": None}
            block_field = None
            continue
        if block is None:
            continue
        if a is None:
            if b is not None and block_field in ("Prerequisites:", "Environmental Information:"):
                key = "prerequisites" if block_field == "Prerequisites:" else "environment"
                block[key].append(b)
            continue
        block_field = a
        if a == "Test Case ID:":
            block["date_tested"] = _excel_date(_clean(cells.get("D")))
        elif a == "Test Case Description:":
            block["description"] = b
        elif a == "Prerequisites:" and b:
            block["prerequisites"].append(b)
        elif a == "Environmental Information:" and b:
            block["environment"].append(b)
        elif a == "Test Scenario:":
            scenario_title = b
            if b is None:
                defects.append(f"Test Cases sheet row {r}: 'Test Scenario:' has no title")
        elif CASE_ID.match(a):
            sid = a[:6]
            mid = module_of.get(sid)
            if block["module"] is None and mid is not None:
                block["module"] = mid
                mod = modules[mid]
                mod["test_cases_sheet"] = {k: block[k] for k in
                                           ("label", "tester", "date_tested", "description",
                                            "prerequisites", "environment")}
                if block["label"] != mod["name"]:
                    defects.append(f"Test Cases sheet row {block['first_row']}: block labelled "
                                   f"'{block['label']}' holds {sid}, which the Scenarios sheet "
                                   f"puts under '{mod['name']}'")
            key = a
            n = 2
            while key in cases:
                key = f"{a}#{n}"
                n += 1
            if key != a:
                defects.append(f"Test Cases sheet row {r}: duplicate case ID {a}, keyed as {key}")
            steps, inp, expected = b, _clean(cells.get("C")), _clean(cells.get("D"))
            cases[key] = {
                "module": mid,
                "scenario": sid,
                "scenario_title": scenario_title or modules.get(mid, {}).get("scenarios", {}).get(sid),
                "steps": steps,
                "input": inp,
                "expected": expected,
                "actual": _clean(cells.get("E")),
                "status": _clean(cells.get("F")),
                "comments": _clean(cells.get("G")),
                "date_tested": block["date_tested"],
                "cell": f"{CASES_SHEET}!A{r}:G{r}",
                "text_hash": _text_hash(steps, inp, expected),
            }
    return cases


# ---------------------------------------------------------------- output

class _BlockDumper(yaml.SafeDumper):
    pass


def _str_presenter(dumper: yaml.SafeDumper, data: str) -> yaml.Node:
    style = "|" if "\n" in data else None
    return dumper.represent_scalar("tag:yaml.org,2002:str", data, style=style)


_BlockDumper.add_representer(str, _str_presenter)


def build_snapshot(xlsx: Path) -> dict:
    """Read the workbook and return the full snapshot document."""
    sheets = read_sheets(xlsx)
    defects: list[str] = []
    modules = parse_modules(sheets[SCENARIOS_SHEET], defects)
    cases = parse_cases(sheets[CASES_SHEET], modules, defects)
    try:
        shown = str(xlsx.resolve().relative_to(REPO_ROOT))
    except ValueError:
        shown = str(xlsx)
    return {
        "workbook": {
            "file": shown,
            "sha256": hashlib.sha256(xlsx.read_bytes()).hexdigest(),
            "extracted_at": dt.date.today().isoformat(),
            "case_count": len(cases),
        },
        "modules": modules,
        "cases": cases,
        "defects": defects,
    }


def diff_report(old: dict | None, new: dict) -> list[str]:
    """Human-readable list of what changed between two snapshots."""
    if old is None:
        return [f"new snapshot: {len(new['cases'])} cases"]
    lines: list[str] = []
    o, n = old.get("cases", {}), new["cases"]
    for cid in sorted(set(n) - set(o)):
        lines.append(f"ADDED    {cid}")
    for cid in sorted(set(o) - set(n)):
        lines.append(f"REMOVED  {cid}")
    for cid in sorted(set(o) & set(n)):
        if o[cid].get("text_hash") != n[cid]["text_hash"]:
            lines.append(f"CHANGED  {cid}  (steps / input / expected) — re-check its requirement")
        for field in ("status", "actual", "comments", "date_tested"):
            if o[cid].get(field) != n[cid].get(field):
                lines.append(f"RESULT   {cid}  {field}: {o[cid].get(field)!r} -> {n[cid].get(field)!r}")
    return lines


def main() -> int:
    """Entry point; returns the process exit code."""
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--xlsx", type=Path, default=DEFAULT_XLSX)
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--check", action="store_true",
                    help="do not write; exit 1 if the workbook differs from the snapshot")
    args = ap.parse_args()
    if not args.xlsx.exists():
        print(f"workbook not found: {args.xlsx}", file=sys.stderr)
        return 2
    new = build_snapshot(args.xlsx)
    old = yaml.safe_load(args.out.read_text()) if args.out.exists() else None
    changes = diff_report(old, new)
    for line in changes or ["no changes to any case"]:
        print(line)
    if args.check:
        return 1 if (old is None or changes) else 0
    header = ("# GENERATED by docs/testing/tools/extract_uat.py from the UAT workbook.\n"
              "# Do not edit by hand: the workbook is authoritative. Re-run the tool instead.\n")
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(header + yaml.dump(new, Dumper=_BlockDumper, sort_keys=False,
                                           allow_unicode=True, width=100))
    print(f"wrote {args.out.relative_to(REPO_ROOT) if args.out.is_relative_to(REPO_ROOT) else args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
