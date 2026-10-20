"""Tester T-17 criterion 2/3 end to end: demo_run.py against the program's REAL emitters (real_app.py), not the author's stand-in."""
import json, os, re, subprocess, sys
from pathlib import Path
import pytest
HERE = Path(__file__).resolve().parent
PY = "/home/sv-rpi5/Projects/Second-Vision/worktrees/kenth/my_hailo_env/bin/python"

def drive(tmp, *args):
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
    p = subprocess.run(["nice", "-n", "19", PY, str(HERE / "drive.py"), str(tmp), *args], capture_output=True, text=True, env=env, timeout=600)
    s = next((tmp / "evidence").glob("demo-*/summary.md")).read_text()
    rows = [l.split("|")[1:-1] for l in s.splitlines() if re.match(r"\| \d+ \|", l)]
    return p, s, [(r[1].strip(), r[2].strip(), r[3].strip(), r[4].strip()) for r in rows]

def test_full_run_all_parts_present_every_expectation_is_seen(tmp_path):
    p, s, rows = drive(tmp_path)
    assert p.returncode == 0, p.stdout[-3000:]
    assert "32 seen, 0 missed, 0 skipped" in s, s
    assert "exited 0" in s

def test_lens_run_where_the_flash_is_never_taken_away_misses_exactly_the_last_line(tmp_path):
    """A wrong OK is as bad as a wrong MISS: the 4th lens action's 'Camera view is back.' never came and must be MISSED."""
    p, s, rows = drive(tmp_path, "--steps", "lens", "--suppress", "view_ok2")
    misses = [r for r in rows if r[2] == "MISSED"]
    assert len(misses) == 1 and "Camera view is back." in misses[0][3], rows
    assert p.returncode == 1

def test_presenter_who_does_nothing_gets_misses_everywhere_and_no_false_ok(tmp_path):
    suppress = "detect,camera_off,camera_on,glasses_off,glasses_on,earphones_off,earphones_on,panel_off,panel_on,view_dark,view_ok,view_bright,view_ok2"
    p, s, rows = drive(tmp_path, "--steps", "camera,glasses,earphones,lens", "--suppress", suppress)
    oks = [r for r in rows if r[2] == "ok"]
    assert oks == [], oks
    assert p.returncode == 1

def test_PARTIAL_HARDWARE_earphones_absent_glasses_and_panel_returns_say_the_D17_sentence_and_the_tool_must_not_miss_them(tmp_path):
    p, s, rows = drive(tmp_path, "--steps", "glasses,panel", "--opts", "no-earphones")
    missed = [r for r in rows if r[2] == "MISSED"]
    assert not missed, missed          # FAILS today: the tool waits for "Everything is connected"; the program says "Vibration is working again" / "Controller board connected"
