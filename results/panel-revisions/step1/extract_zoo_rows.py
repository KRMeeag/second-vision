#!/usr/bin/env python3
"""Pull the YOLOv8 rows out of the saved Hailo Model Zoo table into a CSV.

    python3 -I results/panel-revisions/step1/extract_zoo_rows.py \
        results/panel-revisions/step1/model-zoo-docs-v2.17/HAILO8_object_detection.rst \
        > results/panel-revisions/step1/model-zoo-v2.17-yolov8.csv

Copies the cells as written; computes nothing. The file's own column names are kept: it gives
"float" and "Hardware Degradation", not a hardware mAP (see step1/RESULTS.md).
"""
import csv
import re
import sys

WANTED = {"yolov8n", "yolov8s", "yolov8m", "yolov8l", "yolov8x"}


def rows(path):
    lines = open(path, encoding="utf-8").read().splitlines()
    section, cur = None, None
    for i, line in enumerate(lines):
        if i + 1 < len(lines) and line.strip() and re.fullmatch(r"\^+", lines[i + 1]):
            section = line.strip()
        first = re.match(r"^   \* - (.*)$", line)
        if first:
            if cur:
                yield cur
            cur = {"section": section, "line": i + 1, "cells": [first.group(1).strip()]}
            continue
        more = re.match(r"^     - (.*)$", line)
        if more and cur:
            cur["cells"].append(more.group(1).strip())
    if cur:
        yield cur


def main() -> int:
    path = sys.argv[1]
    out = csv.writer(sys.stdout, lineterminator="\n")
    header = None
    for row in rows(path):
        cells = row["cells"]
        if cells[0] == "Network Name" and header is None:
            header = cells
            out.writerow(["section", "rst_line"] + [c for j, c in enumerate(cells) if j != 5])  # 5 = Links
            continue
        name = re.sub(r"\s*\|.*$", "", cells[0]).strip()
        if name in WANTED and row["section"] == "COCO":
            out.writerow([row["section"], row["line"], name] + [c for j, c in enumerate(cells) if j not in (0, 5)])
    return 0


if __name__ == "__main__":
    sys.exit(main())
