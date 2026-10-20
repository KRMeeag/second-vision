"""The measuring tools in docs/testing/tools: their own self-checks, and the battery reader's fallback.

perf_sample needs psutil, which only my_hailo_env has; in the Poetry environment these are skipped."""
import importlib
import sys
import types
from pathlib import Path

import pytest

TOOLS = Path(__file__).resolve().parent.parent / "docs/testing/tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

pytest.importorskip("psutil")
perf_sample = importlib.import_module("perf_sample")
perf_npu = importlib.import_module("perf_npu")


def test_perf_sample_self_check_passes():
    """Log parsing, frame windows, the stability rule, the gauge's byte order, the drain rate, the battery stop."""
    assert perf_sample.self_check() == 0


def test_perf_npu_self_check_passes():
    assert perf_npu.self_check() == 0


def test_an_unreadable_battery_gauge_leaves_the_columns_empty_and_never_stops_the_run(monkeypatch):
    class Broken:
        def __init__(self, bus):
            raise OSError("no device at 0x36")
    monkeypatch.setitem(sys.modules, "smbus2", types.SimpleNamespace(SMBus=Broken))
    assert perf_sample.battery() == ("", "")
    row = {"battery_pct": "", "battery_v": ""}
    assert not perf_sample.battery_stop(row, 5)
    assert perf_sample.battery_lines([dict(row, elapsed_s=0.0)]) == ["battery (X1202 gauge): no readings"]


def test_the_gauge_is_read_at_its_address_and_registers(monkeypatch):
    reads = []

    class Bus:
        def __init__(self, n):
            reads.append(("bus", n))

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read_word_data(self, addr, reg):
            reads.append((hex(addr), reg))
            return {2: 0x00D1, 4: 0x3A63}[reg]          # 4.18 V and 99.2 %, high byte first on the wire
    monkeypatch.setitem(sys.modules, "smbus2", types.SimpleNamespace(SMBus=Bus))
    assert perf_sample.battery() == (99.23, 4.18)
    assert reads == [("bus", 1), ("0x36", 2), ("0x36", 4)]


# ============================================================
# The charger line and the rundown's start (2026-10-19)
# ============================================================
perf_run = importlib.import_module("perf_run")


def test_the_charger_line_is_one_with_the_adapter_in_zero_on_battery_and_empty_when_unreadable(monkeypatch):
    class Value:
        ACTIVE, INACTIVE = "active", "inactive"

    def fake_gpiod(level):
        class Req:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def get_values(self):
                return [level]
        return types.SimpleNamespace(
            request_lines=lambda *a, **k: Req(),
            LineSettings=lambda **k: None,
            line=types.SimpleNamespace(Direction=types.SimpleNamespace(INPUT="in"), Value=Value))
    monkeypatch.setitem(sys.modules, "gpiod", fake_gpiod(Value.ACTIVE))
    assert perf_sample.ac_power() == "1"
    monkeypatch.setitem(sys.modules, "gpiod", fake_gpiod(Value.INACTIVE))
    assert perf_sample.ac_power() == "0"

    def broken(*a, **k):
        raise OSError("the line is busy")
    monkeypatch.setitem(sys.modules, "gpiod", types.SimpleNamespace(request_lines=broken, LineSettings=lambda **k: None,
                                                                    line=types.SimpleNamespace(Direction=types.SimpleNamespace(INPUT="in"),
                                                                                               Value=Value)))
    assert perf_sample.ac_power() == ""


def test_the_wait_ends_the_moment_the_charger_is_out():
    seen = iter(["1", "1", "1", "0"])
    slept = []
    assert perf_run.wait_for_battery(read=lambda: next(seen), sleep=slept.append, clock=lambda: 0.0) is True
    assert slept == [2.0, 2.0, 2.0]


def test_the_wait_gives_up_after_its_limit_with_the_charger_still_in():
    now = [0.0]
    assert perf_run.wait_for_battery(read=lambda: "1", sleep=lambda s: now.__setitem__(0, now[0] + s),
                                     clock=lambda: now[0], limit=60.0) is False
    assert now[0] >= 60.0


def test_an_unreadable_charger_line_never_counts_as_unplugged():
    now = [0.0]
    assert perf_run.wait_for_battery(read=lambda: "", sleep=lambda s: now.__setitem__(0, now[0] + s),
                                     clock=lambda: now[0], limit=10.0) is False


def test_a_rundown_is_refused_with_the_charger_in(monkeypatch, capsys, tmp_path):
    monkeypatch.setattr(perf_sample, "ac_power", lambda: "1")
    monkeypatch.setattr(sys, "argv", ["perf_run.py", "--duration", "60", "--stop-at-volts", "3.5",
                                      "--out", str(tmp_path)])
    assert perf_run.main() == 1
    assert "the charger is connected" in capsys.readouterr().out


def test_wait_for_battery_without_a_stop_condition_is_refused(monkeypatch, capsys, tmp_path):
    monkeypatch.setattr(sys, "argv", ["perf_run.py", "--duration", "60", "--wait-for-battery",
                                      "--out", str(tmp_path)])
    assert perf_run.main() == 1
    assert "needs --stop-at-volts" in capsys.readouterr().out


def _scripted_run(monkeypatch, tmp_path, volts, ac="0", stop_v="3.5"):
    """perf_sample.main() on scripted samples (no hardware, no waiting): returns the CSV rows and the summary text."""
    rows = iter(volts)

    def fake_sample(proc, started, use_sudo=False):
        v = next(rows, None)
        if v is None:
            raise KeyboardInterrupt             # the scripted samples ran out: end it as Ctrl+C would
        return {"elapsed_s": 0.0, "temp_c": 50.0, "cpu_mhz": 2400.0, "cpu_max_mhz": 2400.0, "undervolt": "0",
                "fan_rpm": "", "core_max_pct": 10.0, "cores_pct": "", "mem_used_mb": 900.0, "app_cpu_pct": "",
                "app_rss_mb": "", "throttled": "unavailable", "battery_pct": 50.0, "battery_v": v, "ac_power": ac}
    monkeypatch.setattr(perf_sample, "sample", fake_sample)
    monkeypatch.setattr(perf_sample.time, "sleep", lambda s: None)
    monkeypatch.setattr(sys, "argv", ["perf_sample.py", "--duration", "100000", "--interval", "1", "--label", "t",
                                      "--out", str(tmp_path), "--match", "no-such-app-anywhere", "--stop-at-volts", stop_v])
    assert perf_sample.main() == 0
    import csv
    return list(csv.DictReader(open(tmp_path / "perf-t.csv"))), (tmp_path / "perf-t.txt").read_text()


def test_the_rundown_ends_after_two_low_readings_in_a_row_not_one(monkeypatch, tmp_path):
    rows, text = _scripted_run(monkeypatch, tmp_path, [3.9, 3.45, 3.9, 3.45, 3.44, 3.9, 3.9])
    assert [r["battery_v"] for r in rows][-1] == "3.44" and len(rows) == 5, "one low reading must not end the run"
    assert "ended at the battery threshold: 3.440 V" in text


def test_a_run_on_the_charger_never_ends_on_voltage_and_says_so(monkeypatch, tmp_path):
    rows, text = _scripted_run(monkeypatch, tmp_path, [3.4, 3.4, 3.4, 3.4], ac="1")
    assert len(rows) == 4 and "ended at" not in text
    assert "says nothing about battery life" in text


def test_the_recorded_model_file_is_the_one_the_pipeline_defaults_to():
    """The evidence's 'model file' line must name the HEF the program really loads (app.py's DET_MODEL_VERSION)."""
    path = perf_run.det_model_file()
    assert path.is_file(), f"the recorded model file does not exist: {path}"
    assert path.parts[-3] == perf_run.re.search(r'^DET_MODEL_VERSION = "([^"]+)"$',
                                                (perf_run.REPO / "src/second_vision/pipeline/app.py").read_text(),
                                                perf_run.re.M).group(1)
