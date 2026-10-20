"""Tester T-17 criterion 8, end to end: perf_run.py + perf_sample.py + perf_plot.py with a stand-in program, a fake gauge/charger line,
a fake sudo and grim. No real rundown; the Pi stays on the charger; no sudo runs."""
import json, os, re, subprocess, sys
from pathlib import Path
import pytest
HERE = Path(__file__).resolve().parent
PY = "/home/sv-rpi5/Projects/Second-Vision/worktrees/kenth/my_hailo_env/bin/python"

def scenario(tmp, name):
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
    p = subprocess.run(["nice", "-n", "19", PY, str(HERE / "perf_e2e.py"), name, str(tmp)], capture_output=True, text=True, env=env, timeout=500)
    last = [l for l in p.stdout.splitlines() if l.startswith("{")][-1]
    info = json.loads(last)
    console = (tmp / "perf_run-console.log").read_text()
    txt = (tmp / "out/perf-t17.txt").read_text() if (tmp / "out/perf-t17.txt").exists() else ""
    return info, console, txt

def test_rundown_by_voltage_stops_the_sampler_then_the_app_and_draws_the_chart(tmp_path):
    info, console, txt = scenario(tmp_path, "rundown-volts")
    assert info["perf_run_exit"] == 0 and not info["standin_left_running"], info
    assert info["wall_s"] < 150, info                         # ended by the threshold, not by --duration 300
    assert "ended at the battery threshold: 3.450 V" in txt and "stop: exited 0" in txt, txt
    assert (tmp_path / "out/perf-t17.png").exists()
    assert "--stop-at-volts" in (tmp_path / "out/perf-t17.txt").read_text()       # the command line is recorded

def test_rundown_by_percentage_also_stops(tmp_path):
    info, console, txt = scenario(tmp_path, "rundown-pct")
    assert info["perf_run_exit"] == 0 and not info["standin_left_running"] and info["wall_s"] < 150, info
    assert "ended at the battery threshold" in txt

def test_wait_mode_waits_for_the_unplug_then_starts_at_once(tmp_path):
    info, console, txt = scenario(tmp_path, "wait-mode")
    assert info["perf_run_exit"] == 0 and not info["standin_left_running"], (info, console[-1500:])
    assert "Waiting for the charger to be unplugged" in console and "the charger is out: on battery, starting" in console
    assert "app running" in console

def test_charger_in_during_the_run_never_stops_it_and_the_summary_says_so(tmp_path):
    info, console, txt = scenario(tmp_path, "charger-mid")
    assert info["perf_run_exit"] == 0 and not info["standin_left_running"], info
    assert "ended at" not in txt and "on the charger throughout" in txt, txt
    assert "'mid'" in txt                                    # not ended early: the half-way screenshot was attempted

def test_a_program_that_ignores_sigint_is_killed_after_20_s_and_nothing_is_left(tmp_path):
    info, console, txt = scenario(tmp_path, "ignore-sigint")
    assert info["perf_run_exit"] == 0 and not info["standin_left_running"], info
    assert "did NOT exit within 20 s of Ctrl+C: killed" in txt

def test_no_gauge_no_charger_line_the_tools_run_on_without_a_battery_column(tmp_path):
    info, console, txt = scenario(tmp_path, "no-gauge")
    assert info["perf_run_exit"] == 0 and not info["standin_left_running"], (info, console[-1500:])
    assert "battery (X1202 gauge): no readings" in txt
    assert (tmp_path / "out/perf-t17.png").exists()
    assert "Traceback" not in console

def test_a_rundown_with_the_charger_in_is_refused_and_nothing_is_started(tmp_path):
    info, console, txt = scenario(tmp_path, "refuse")
    assert info["perf_run_exit"] == 1 and info["standin_pid"] is None, info
    assert "NOT STARTED" in console and "the charger is connected" in console
