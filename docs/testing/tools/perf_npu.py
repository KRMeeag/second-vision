#!/usr/bin/env python3
"""Sample the Hailo NPU's load with `hailortcli monitor`, and summarise it.

    python3 docs/testing/tools/perf_npu.py --self-check
    python3 docs/testing/tools/perf_npu.py --parse <stem>-npu-raw.log     # rebuild <stem>-npu.csv

perf_run.py --npu starts the sampling (`sample_loop`) beside the app. It only works if the process that
owns the chip was started with HAILO_MONITOR=1. On this Pi that process is `hailort_service`, not the
app: its setting comes from /etc/default/hailort_service (HAILO_MONITOR=0), so a queued run turns it
on for one run and puts it back (docs/testing/tools/../../.claude/tools/queued_npu_run.sh).

Every INTERVAL seconds it runs `hailortcli monitor` for a couple of seconds under `script` (the monitor
draws a screen, so it needs a terminal), strips the screen codes, and keeps the raw text. The raw
log is the evidence; the parsed CSV is best effort, because the table layout was not known when this
was written (the parser reads the two tables the monitor prints: devices, then models).
"""
from __future__ import annotations

import csv
import re
import statistics
import subprocess
import sys
import time
from pathlib import Path

ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]|\x1b[()][A-Z0-9]|\r")
NO_FILES = "did not retrieve any files"


def capture(seconds: int = 3, use_sudo: bool = False) -> str:
    """One look at the monitor: its screen text, or '' if it could not run."""
    cmd = ["timeout", str(seconds), "script", "-qfc", "hailortcli monitor", "/dev/null"]
    if use_sudo:
        cmd = ["sudo", "-n", *cmd]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=seconds + 8).stdout
    except (OSError, subprocess.SubprocessError):
        return ""
    return ANSI.sub("", out)


def parse(text: str) -> dict:
    """Utilisation and rates from one capture. Keys: device_util, devices, models [(name, util, fps)].

    The last complete screen in the capture is used, since the monitor redraws about once a second.
    """
    screens = re.split(r"(?=Device ID\s+Utilization)", text)
    screen = next((s for s in reversed(screens) if "Device ID" in s and NO_FILES not in s), "")
    result: dict = {"device_util": None, "devices": [], "models": [], "empty": NO_FILES in text and not screen}
    if not screen:
        return result
    block = "dev"
    for raw in screen.splitlines():
        line = raw.strip()
        if not line or set(line) <= {"-", " "}:
            continue
        if line.startswith("Device ID"):
            block = "dev"
            continue
        if line.startswith("Model") and "Utilization" in line:
            block = "model"
            continue
        if line.startswith("Model") and "Stream" in line:
            block = "stream"
            continue
        cols = re.split(r"\s{2,}", line)
        nums = [c for c in cols if re.fullmatch(r"[0-9]+(\.[0-9]+)?", c)]
        if block == "dev" and len(cols) >= 2 and nums:
            result["devices"].append((cols[0], float(nums[0])))
        elif block == "model" and len(cols) >= 3 and len(nums) >= 2:
            result["models"].append((cols[0], float(nums[0]), float(nums[1])))
    if result["devices"]:
        result["device_util"] = result["devices"][0][1]
    return result


def sample_loop(stem: Path, started: float, duration: float, interval: float = 10.0) -> None:
    """Run until `duration` seconds after `started`; append raw captures and a CSV row per sample."""
    raw = open(f"{stem}-npu-raw.log", "w", buffering=1)
    fh = open(f"{stem}-npu.csv", "w", newline="", buffering=1)
    w = csv.writer(fh)
    w.writerow(["elapsed_s", "device_util_pct", "model_util_pct", "model_fps", "model", "monitor_ran_as_root"])
    use_sudo = False
    while time.monotonic() - started < duration:
        t = time.monotonic() - started
        text = capture(3, use_sudo)
        res = parse(text)
        if res["device_util"] is None and not use_sudo:           # maybe the files are root-only
            text2 = capture(3, True)
            res2 = parse(text2)
            if res2["device_util"] is not None:
                text, res, use_sudo = text2, res2, True
        raw.write(f"===== elapsed {t:.0f} s (root: {use_sudo}) =====\n{text}\n")
        mdl = res["models"][0] if res["models"] else ("", "", "")
        w.writerow([f"{t:.0f}", "" if res["device_util"] is None else res["device_util"], mdl[1], mdl[2], mdl[0],
                    int(use_sudo)])
        time.sleep(max(0.0, interval - (time.monotonic() - started - t)))
    raw.close()
    fh.close()


def summary(csv_path: Path) -> list[str]:
    rows = list(csv.DictReader(open(csv_path)))
    util = [float(r["device_util_pct"]) for r in rows if r["device_util_pct"] != ""]
    fps = [float(r["model_fps"]) for r in rows if r["model_fps"] != ""]
    if not util:
        return [f"NPU load: no readings in {len(rows)} attempts (see the raw log)"]
    out = [f"NPU utilisation (hailortcli monitor, {len(util)} of {len(rows)} readings): "
           f"min {min(util):.0f} %, median {statistics.median(util):.0f} %, mean {statistics.mean(util):.0f} %, "
           f"max {max(util):.0f} %"]
    if fps:
        out.append(f"NPU-reported model frame rate: median {statistics.median(fps):.1f}, min {min(fps):.1f}")
    return out


def self_check() -> int:
    empty = "Device ID   Utilization (%)   Architecture\n----\n\nMonitor did not retrieve any files.\n"
    assert parse(empty)["device_util"] is None and parse(empty)["empty"]
    good = ("\x1b[2JDevice ID                 Utilization (%)   Architecture\r\n"
            "----------------------------------------------\r\n"
            "0001:01:00.0                  37.5              HAILO8\r\n\r\n"
            "Model                         Utilization (%)   FPS     PID\r\n"
            "-----\r\n"
            "best                          21.3              29.6    1234\r\n"
            "scdepthv3                     16.2              29.5    1234\r\n")
    r = parse(ANSI.sub("", good))
    assert r["device_util"] == 37.5 and r["models"][0] == ("best", 21.3, 29.6) and len(r["models"]) == 2, r
    assert parse("")["device_util"] is None
    print("self-check ok")
    return 0


if __name__ == "__main__":
    if "--self-check" in sys.argv:
        sys.exit(self_check())
    if "--parse" in sys.argv:
        raw = Path(sys.argv[sys.argv.index("--parse") + 1])
        stem = Path(str(raw).replace("-npu-raw.log", ""))
        rows = []
        for chunk in raw.read_text().split("===== elapsed ")[1:]:
            head, _, body = chunk.partition("\n")
            res = parse(body)
            m = res["models"][0] if res["models"] else ("", "", "")
            rows.append([head.split()[0], "" if res["device_util"] is None else res["device_util"], m[1], m[2], m[0], 0])
        with open(f"{stem}-npu.csv", "w", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(["elapsed_s", "device_util_pct", "model_util_pct", "model_fps", "model", "monitor_ran_as_root"])
            w.writerows(rows)
        print("\n".join(summary(Path(f"{stem}-npu.csv"))))
        sys.exit(0)
    print(__doc__)
    sys.exit(2)
