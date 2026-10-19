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
