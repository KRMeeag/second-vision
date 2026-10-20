"""Tester T-17 criterion 8 (8a to 8d): the perf tools' battery logic on synthetic input. Run with my_hailo_env (psutil)."""
import csv, importlib, os, sys, types
from pathlib import Path
import pytest

REPO = Path(os.environ.get("T17_REPO", "/home/sv-rpi5/Projects/Second-Vision/worktrees/kenth"))
TOOLS = REPO / "docs/testing/tools"
sys.path.insert(0, str(TOOLS))
ps = importlib.import_module("perf_sample")
pr = importlib.import_module("perf_run")

def row(t=0.0, pct=50.0, v=3.9, ac="0", **kw):
    r = {"elapsed_s": t, "battery_pct": pct, "battery_v": v, "ac_power": ac}
    r.update(kw); return r

# ---------------------------------------------------------------- 8b battery_stop
@pytest.mark.parametrize("r,thr,volts,expect", [
    (row(v=3.50), None, 3.5, True),                    # exactly at V stops
    (row(v=3.501), None, 3.5, False),                  # 0.001 above does not
    (row(v=3.499), None, 3.5, True),
    (row(v=3.2, ac="1"), None, 3.5, False),            # charger in never stops
    (row(v=3.2, ac=""), None, 3.5, True),              # charger line unreadable: still protects the Pi
    (row(v=3.2, ac="0"), None, 3.5, True),
    (row(v="", pct=1.0), None, 3.5, False),            # unread voltage never stops on volts
    (row(v=3.0, pct=""), None, 3.5, True),
    (row(pct=5.0, v=3.9), 5, None, True),              # percentage boundary
    (row(pct=5.01, v=3.9), 5, None, False),
    (row(pct="", v=3.9), 5, None, False),              # unread percentage never stops
    (row(pct=1.0, ac="1", v=3.9), 5, None, False),     # charger in beats the percentage too
    (row(pct=50, v=3.9), None, None, False),           # no threshold set
    (row(pct=1.0, v=3.0), None, None, False),
    (row(pct=60, v=3.4), 5, 3.5, True),                # either condition
    (row(pct=4, v=3.9), 5, 3.5, True),
    ({}, 5, 3.5, False),                               # empty row
    ({"battery_v": 3.0}, None, 3.5, True),             # no ac_power key at all (old row)
])
def test_battery_stop_table(r, thr, volts, expect):
    assert ps.battery_stop(r, thr, volts) is expect

# ---------------------------------------------------------------- scripted sampler runs
def scripted(monkeypatch, tmp_path, samples, stop_v="3.5", stop_pct=None, duration="100000"):
    it = iter(samples)
    taken = []
    def fake_sample(proc, started, use_sudo=False):
        s = next(it, None)
        if s is None:
            raise KeyboardInterrupt
        taken.append(s)
        base = {"elapsed_s": float(len(taken) * 10), "temp_c": 50.0, "cpu_mhz": 2400.0, "cpu_max_mhz": 2400.0,
                "undervolt": "0", "fan_rpm": "", "core_max_pct": 10.0, "cores_pct": "", "mem_used_mb": 900.0,
                "app_cpu_pct": "", "app_rss_mb": "", "throttled": "unavailable"}
        base.update(s); return base
    monkeypatch.setattr(ps, "sample", fake_sample)
    monkeypatch.setattr(ps.time, "sleep", lambda s: None)
    argv = ["perf_sample.py", "--duration", duration, "--interval", "1", "--label", "t", "--out", str(tmp_path),
            "--match", "no-such-app-anywhere-t17"]
    if stop_v is not None: argv += ["--stop-at-volts", stop_v]
    if stop_pct is not None: argv += ["--stop-at-battery", str(stop_pct)]
    monkeypatch.setattr(sys, "argv", argv)
    assert ps.main() == 0
    return len(taken), list(csv.DictReader(open(tmp_path / "perf-t.csv"))), (tmp_path / "perf-t.txt").read_text()

def S(v, ac="0", pct=50.0): return {"battery_v": v, "ac_power": ac, "battery_pct": pct}

def test_two_in_a_row_on_battery_ends_at_the_second(monkeypatch, tmp_path):
    n, rows, text = scripted(monkeypatch, tmp_path, [S(3.9), S(3.4), S(3.39), S(3.3)])
    assert n == 3 and "ended at the battery threshold: 3.390 V" in text

def test_one_low_reading_between_good_ones_never_ends(monkeypatch, tmp_path):
    n, rows, text = scripted(monkeypatch, tmp_path, [S(3.4), S(3.9), S(3.4), S(3.9), S(3.4), S(3.9)])
    assert n == 6 and "ended at" not in text

def test_boundary_two_readings_exactly_at_V_stop_and_0_001_above_do_not(monkeypatch, tmp_path):
    n, _, text = scripted(monkeypatch, tmp_path, [S(3.5), S(3.5), S(3.5)])
    assert n == 2 and "ended at" in text
    n, _, text = scripted(monkeypatch, tmp_path, [S(3.501), S(3.501), S(3.501)])
    assert n == 3 and "ended at" not in text

def test_charger_in_sample_between_two_low_ones_RESETS_the_count(monkeypatch, tmp_path):
    """Decision recorded in the report: low, low-with-charger, low  -> does not stop (the charger sample resets)."""
    n, _, text = scripted(monkeypatch, tmp_path, [S(3.4), S(3.4, ac="1"), S(3.4), S(3.9)])
    assert n == 4 and "ended at" not in text
    n, _, text = scripted(monkeypatch, tmp_path, [S(3.4), S(3.4, ac="1"), S(3.4), S(3.4)])
    assert n == 4 and "ended at" in text          # two on-battery lows in a row AFTER the charger sample

def test_unread_voltage_samples_do_not_count_and_reset(monkeypatch, tmp_path):
    n, _, text = scripted(monkeypatch, tmp_path, [S(3.4), S("", pct=""), S(3.4), S(3.9)])
    assert n == 4 and "ended at" not in text

def test_unreadable_charger_line_with_low_voltage_still_ends_the_run(monkeypatch, tmp_path):
    n, _, text = scripted(monkeypatch, tmp_path, [S(3.4, ac=""), S(3.4, ac="")])
    assert n == 2 and "ended at" in text

def test_percentage_stop_still_works(monkeypatch, tmp_path):
    n, _, text = scripted(monkeypatch, tmp_path, [S(3.9, pct=6.0), S(3.9, pct=5.0), S(3.9, pct=4.0)], stop_v=None, stop_pct=5)
    assert n == 3 and "ended at" in text      # 6.0 no; 5.0 yes; 4.0 yes -> stops at the third (two in a row)

def test_whole_run_on_the_charger_never_stops_and_says_so(monkeypatch, tmp_path):
    n, _, text = scripted(monkeypatch, tmp_path, [S(3.1, ac="1")] * 6)
    assert n == 6 and "ended at" not in text and "on the charger throughout" in text

def test_LATENT_a_stop_by_volts_with_an_empty_percentage_crashes_before_the_files_are_written(monkeypatch, tmp_path):
    """Not reachable with battery() (it returns both values or neither); documents the unguarded format in the stop message."""
    with pytest.raises(ValueError):
        scripted(monkeypatch, tmp_path, [S(3.4, pct=""), S(3.4, pct="")])

# ---------------------------------------------------------------- 8d battery_lines branches
def series(n=361, p0=90.0, rate_per_s=10 / 3600 * 3600 / 3600, v=3.9):
    return [row(t=i * 10.0, pct=p0 - i * 10 / 360, v=v) for i in range(n)]

def txt(rows): return "\n".join(ps.battery_lines(rows))

def test_falling_series_drain_rate():
    t = txt(series())
    assert "90.0 % -> 80.0 %" in t and "10.0 % per hour" in t and "about 9.5 h" in t

def test_flat_series_says_too_little():
    t = txt([row(t=i * 10.0, pct=80.0) for i in range(50)])
    assert "too little" in t and "per hour" not in t

def test_rising_series_without_ac_column_is_flagged_charger_in():
    rows = [dict(r) for r in series()]
    for r in rows: r.pop("ac_power")
    rows = [dict(r, battery_pct=70.0 + i * 0.05) for i, r in enumerate(rows)]
    t = txt(rows)
    assert "ROSE" in t and "charger was connected" in t

def test_old_csv_with_no_power_columns_at_all():
    rows = [{"elapsed_s": i * 10.0} for i in range(5)]
    assert ps.battery_lines(rows) == ["battery (X1202 gauge): no readings"]

def test_all_on_battery_has_no_charger_flag():
    t = txt(series())
    t2 = txt([dict(r, ac_power="0") for r in series()])
    assert "on battery in 361 of 361" in t2 and "CHARGER" not in t2

def test_all_charger_in():
    t = txt([dict(r, ac_power="1") for r in series()])
    assert "on battery in 0 of 361" in t and "THE CHARGER WAS IN" in t and "on the charger throughout" in t

def test_mixed_in_then_out_and_out_then_in():
    a = txt([dict(r, ac_power="1" if i < 100 else "0") for i, r in enumerate(series())])
    b = txt([dict(r, ac_power="0" if i < 100 else "1") for i, r in enumerate(series())])
    for t, nb in ((a, 261), (b, 100)):
        assert f"on battery in {nb} of 361" in t and "THE CHARGER WAS IN for the rest" in t

def test_some_empty_ac_values_are_ignored_in_the_count():
    rows = [dict(r, ac_power=("" if i % 2 else "0")) for i, r in enumerate(series(11))]
    t = txt(rows)
    assert "on battery in 6 of 6 samples" in t and "CHARGER" not in t

def test_all_empty_ac_values_fall_back_to_the_percentage_logic():
    rows = [dict(r, ac_power="") for r in series()]
    t = txt(rows)
    assert "power source" not in t and "per hour" in t

def test_ROSE_is_not_said_when_the_power_source_was_recorded():
    rows = [dict(r, ac_power="1" if i < 180 else "0", battery_pct=(70 + i * 0.05 if i < 180 else 79 - (i - 180) * 0.0125))
            for i, r in enumerate(series())]
    assert "ROSE" not in txt(rows) and "THE CHARGER WAS IN" in txt(rows)

def test_charger_in_for_part_and_charge_rising_overall_message_is_not_misleading():
    # charger in for first half (charge rises) then battery: net rise -> 'under 1 %' branch?  Information for the report.
    rows = [dict(r, ac_power="1" if i < 180 else "0", battery_pct=(70 + i * 0.05 if i < 180 else 79 - (i - 180) * 0.0125))
            for i, r in enumerate(series())]
    print(txt(rows))

def test_summary_on_missing_ac_key_does_not_crash_whole_summary():
    rows = [{"elapsed_s": 0.0, "temp_c": 50.0, "cpu_mhz": 2400.0, "cpu_max_mhz": 2400.0, "undervolt": "0", "fan_rpm": "",
             "core_max_pct": 1.0, "cores_pct": "", "mem_used_mb": 1.0, "app_cpu_pct": "", "app_rss_mb": "", "throttled": "0x0",
             "battery_pct": 50.0, "battery_v": 3.9}]
    out = "\n".join(ps.summary(rows, [], 10.0))
    assert "battery (X1202 gauge)" in out

# ---------------------------------------------------------------- 8c wait_for_battery and refusals
def vclock():
    now = [0.0]
    return now, (lambda s: now.__setitem__(0, now[0] + s)), (lambda: now[0])

def test_wait_returns_true_only_on_zero_and_at_once():
    seq = iter(["1", "", "1", "0", "1"]); calls = []
    now, sleep, clock = vclock()
    assert pr.wait_for_battery(read=lambda: calls.append(1) or next(seq), sleep=sleep, clock=clock) is True
    assert len(calls) == 4 and now[0] == 6.0         # three sleeps of 2 s

def test_wait_gives_false_after_limit_charger_in_and_never_early():
    now, sleep, clock = vclock()
    assert pr.wait_for_battery(read=lambda: "1", sleep=sleep, clock=clock, limit=7200.0) is False
    assert 7200.0 <= now[0] <= 7204.0

def test_wait_boundary_charger_out_just_before_the_limit_is_still_true():
    now, sleep, clock = vclock()
    def read():
        return "0" if now[0] >= 7200.0 else "1"
    assert pr.wait_for_battery(read=read, sleep=sleep, clock=clock, limit=7200.0) is True

def test_wait_with_unreadable_line_the_whole_time_is_false_not_true():
    now, sleep, clock = vclock()
    assert pr.wait_for_battery(read=lambda: "", sleep=sleep, clock=clock, limit=100.0) is False

def test_wait_default_limit_is_two_hours():
    import inspect
    assert inspect.signature(pr.wait_for_battery).parameters["limit"].default == 7200

def run_main(monkeypatch, tmp_path, argv, ac, **patches):
    outs = []
    monkeypatch.setattr(pr, "_ac_power", lambda: ac)
    monkeypatch.setattr(pr, "sh", lambda cmd, **kw: "inactive" if cmd[:2] == ["systemctl", "is-active"] else ("" if cmd[0] == "pgrep" else "x"))
    for k, v in patches.items(): monkeypatch.setattr(pr, k, v)
    monkeypatch.setattr(sys, "argv", ["perf_run.py", "--out", str(tmp_path)] + argv)
    return pr.main()

def test_rundown_refused_with_charger_in_and_says_so(monkeypatch, tmp_path, capsys):
    for flag in (["--stop-at-volts", "3.5"], ["--stop-at-battery", "5"], ["--stop-at-volts", "3.5", "--stop-at-battery", "5"]):
        assert run_main(monkeypatch, tmp_path, ["--duration", "30"] + flag, "1") == 1
        o = capsys.readouterr().out
        assert "NOT STARTED" in o and "the charger is connected" in o and "--wait-for-battery" in o

def test_wait_flag_without_a_rundown_is_refused_whatever_the_charger_says(monkeypatch, tmp_path, capsys):
    for ac in ("0", "1", ""):
        assert run_main(monkeypatch, tmp_path, ["--wait-for-battery"], ac) == 1
        assert "needs --stop-at-volts" in capsys.readouterr().out

def test_wait_that_times_out_returns_1_with_a_message_and_starts_nothing(monkeypatch, tmp_path, capsys):
    started = []
    monkeypatch.setattr(pr.subprocess, "Popen", lambda *a, **k: started.append(a) or (_ for _ in ()).throw(AssertionError("started!")))
    assert run_main(monkeypatch, tmp_path, ["--wait-for-battery", "--stop-at-volts", "3.5"], "1",
                    wait_for_battery=lambda: False) == 1
    o = capsys.readouterr().out
    assert "Waiting for the charger" in o and "NOT STARTED" in o and not started

def test_unreadable_line_in_wait_mode_message_is_misleading_information(monkeypatch, tmp_path, capsys):
    run_main(monkeypatch, tmp_path, ["--wait-for-battery", "--stop-at-volts", "3.5"], "", wait_for_battery=lambda: False)
    print(capsys.readouterr().out)

def test_wait_mode_with_an_unreadable_line_still_waits(monkeypatch, tmp_path):
    called = []
    monkeypatch.setattr(pr.subprocess, "Popen", lambda *a, **k: (_ for _ in ()).throw(AssertionError("started")))
    run_main(monkeypatch, tmp_path, ["--wait-for-battery", "--stop-at-volts", "3.5"], "",
             wait_for_battery=lambda: called.append(1) or False)
    assert called == [1]

def test_a_rundown_with_an_unreadable_line_is_not_refused_information(monkeypatch, tmp_path, capsys):
    # With ac == "" the rundown is not refused (only "1" is). It would go on to start the app: stop it right there.
    class Stop(Exception): pass
    def boom(*a, **k): raise Stop()
    monkeypatch.setattr(pr, "FakeGlasses", boom)
    monkeypatch.setattr(pr, "service_monitor_setting", lambda: "x")
    monkeypatch.setattr(pr, "_battery_note", lambda: "x")
    with pytest.raises(Stop):
        run_main(monkeypatch, tmp_path, ["--duration", "30", "--stop-at-volts", "3.5"], "")

def test_without_rundown_flags_the_charger_never_blocks(monkeypatch, tmp_path, capsys):
    class Stop(Exception): pass
    def boom(*a, **k): raise Stop()
    monkeypatch.setattr(pr, "FakeGlasses", boom)
    monkeypatch.setattr(pr, "service_monitor_setting", lambda: "x")
    monkeypatch.setattr(pr, "_battery_note", lambda: "x")
    with pytest.raises(Stop):
        run_main(monkeypatch, tmp_path, ["--duration", "30"], "1")

# ---------------------------------------------------------------- 8a ac_power with fake gpiod
def fake_gpiod(level=None, exc=None, calls=None):
    class Value: ACTIVE, INACTIVE = "A", "I"
    class Req:
        def __enter__(s): return s
        def __exit__(s, *a):
            calls.append("closed"); return False
        def get_values(s): return [level]
    def request_lines(path, consumer=None, config=None):
        calls.append(("request", path, consumer, config))
        if exc: raise exc
        return Req()
    return types.SimpleNamespace(request_lines=request_lines, LineSettings=lambda **k: dict(k),
                                 line=types.SimpleNamespace(Direction=types.SimpleNamespace(INPUT="INPUT", OUTPUT="OUTPUT"), Value=Value))

def test_ac_power_requests_gpio6_as_input_only_and_closes_the_line(monkeypatch):
    calls = []
    monkeypatch.setitem(sys.modules, "gpiod", fake_gpiod("A", calls=calls))
    assert ps.ac_power() == "1"
    req = calls[0]
    assert req[1] == "/dev/gpiochip0" and list(req[3]) == [6] and req[3][6] == {"direction": "INPUT"}, req
    assert calls[-1] == "closed"

def test_ac_power_zero_and_every_failure_returns_empty_not_a_crash(monkeypatch):
    c = []
    monkeypatch.setitem(sys.modules, "gpiod", fake_gpiod("I", calls=c)); assert ps.ac_power() == "0"
    for exc in (OSError(16, "busy"), PermissionError("no"), FileNotFoundError("nochip"), RuntimeError("x")):
        monkeypatch.setitem(sys.modules, "gpiod", fake_gpiod("A", exc=exc, calls=[])); assert ps.ac_power() == ""
    monkeypatch.setitem(sys.modules, "gpiod", None)               # import gpiod -> ImportError
    assert ps.ac_power() == ""
    monkeypatch.delitem(sys.modules, "gpiod")
