"""Tester T-17 criterion 8: perf_plot draws the battery panel with battery data and without (and with an old CSV)."""
import csv, importlib, os, sys
from pathlib import Path
import cv2, pytest
REPO = Path("/home/sv-rpi5/Projects/Second-Vision/worktrees/kenth")
sys.path.insert(0, str(REPO / "docs/testing/tools"))
pp = importlib.import_module("perf_plot")

BASE = ["elapsed_s", "temp_c", "cpu_mhz", "cpu_max_mhz", "undervolt", "fan_rpm", "core_max_pct", "cores_pct",
        "mem_used_mb", "app_cpu_pct", "app_rss_mb", "throttled"]
def write(tmp, name, cols, n=60, battery=None):
    stem = tmp / name
    with open(f"{stem}.csv", "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=cols); w.writeheader()
        for i in range(n):
            r = {"elapsed_s": i * 10.0, "temp_c": 55.0, "cpu_mhz": 2400.0, "cpu_max_mhz": 2400.0, "undervolt": "0", "fan_rpm": "",
                 "core_max_pct": 30.0, "cores_pct": "1 2 3 4", "mem_used_mb": 900.0, "app_cpu_pct": 100.0, "app_rss_mb": 500.0,
                 "throttled": "0x0"}
            if battery is not None:
                r.update(battery(i))
            w.writerow({k: r.get(k, "") for k in cols})
    (tmp / f"{name}.txt").write_text(f"perf_sample {name}\ncommand: x\nsamples: {n} every 10 s; app found: yes\n")
    return stem

def draw(stem):
    sys.argv = ["perf_plot.py", str(stem), "--title", "t17"]
    assert pp.main() == 0
    return cv2.imread(f"{stem}.png")

def test_with_battery_data_there_is_an_extra_panel(tmp_path):
    with_b = draw(write(tmp_path, "b", BASE + ["battery_pct", "battery_v", "ac_power"], battery=lambda i: {"battery_pct": 90 - i * 0.2, "battery_v": 4.0 - i * 0.003, "ac_power": "0"}))
    without = draw(write(tmp_path, "nb", BASE))
    assert with_b.shape[0] == without.shape[0] + 280, (with_b.shape, without.shape)

def test_battery_columns_present_but_empty_draws_no_panel_and_does_not_crash(tmp_path):
    empty = draw(write(tmp_path, "e", BASE + ["battery_pct", "battery_v", "ac_power"], battery=lambda i: {"battery_pct": "", "battery_v": "", "ac_power": ""}))
    without = draw(write(tmp_path, "nb2", BASE))
    assert empty.shape == without.shape

def test_a_series_with_gaps_in_the_battery_data(tmp_path):
    img = draw(write(tmp_path, "g", BASE + ["battery_pct", "battery_v", "ac_power"],
                     battery=lambda i: {"battery_pct": "" if i % 3 == 0 else 80 - i * 0.1, "battery_v": 3.9, "ac_power": ""}))
    assert img is not None

def test_a_single_sample_does_not_crash(tmp_path):
    img = draw(write(tmp_path, "one", BASE + ["battery_pct", "battery_v", "ac_power"], n=1, battery=lambda i: {"battery_pct": 50.0, "battery_v": 3.9, "ac_power": "0"}))
    assert img is not None

def test_the_panel_title_says_a_rising_line_means_the_charger_was_in():
    src = (REPO / "docs/testing/tools/perf_plot.py").read_text()
    assert "a rising line means the charger was in" in src
