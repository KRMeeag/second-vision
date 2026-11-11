"""
Unit tests for the detection-branch measurement line (callbacks._log_det_window, SV_LATENCY_LOG=1).

What must hold:
  * Off by default: without SV_LATENCY_LOG=1 the detection callback prints no [DET] line.
  * On: exactly one "[DET] Frame N | lat X ms" line per DET_LOG_EVERY detection frames, with X the
    frame's age from the pipeline clock (the same arithmetic as the [DEPTH] figure).
  * It can never raise into the GStreamer callback.

Hardware-free: GStreamer element, buffer and user_data are duck-typed stand-ins. Run with:

    poetry run pytest tests/test_det_latency_log.py -v
"""

import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

from second_vision.pipeline import callbacks  # noqa: E402


class _Clock:
    def __init__(self, now_ns):
        self._now = now_ns

    def get_time(self):
        return self._now


class _Element:
    """A GStreamer element as _pipeline_latency_ms sees it: a clock and a base time."""

    def __init__(self, now_ns=None, base_ns=0):
        self._clock = None if now_ns is None else _Clock(now_ns)
        self._base = base_ns

    def get_clock(self):
        return self._clock

    def get_base_time(self):
        return self._base


class _UserData:
    def __init__(self, count):
        self._count = count

    def get_count(self):
        return self._count


def _det_lines(text):
    return [line for line in text.splitlines() if line.startswith("[DET]")]


@pytest.fixture
def no_processing(monkeypatch):
    """Keep on_det_frame to the logging step: the detection processing itself is not under test."""
    monkeypatch.setattr(callbacks, "HAILO_AVAILABLE", False)


def test_flag_is_off_unless_set():
    if os.environ.get("SV_LATENCY_LOG") == "1":
        pytest.skip("SV_LATENCY_LOG=1 is set in this shell")
    assert callbacks.LATENCY_LOG is False


def test_off_prints_nothing_even_on_a_logging_frame(monkeypatch, capsys, no_processing):
    monkeypatch.setattr(callbacks, "LATENCY_LOG", False)
    buffer = SimpleNamespace(pts=900_000_000)
    callbacks.on_det_frame(_Element(now_ns=1_000_000_000), buffer, _UserData(callbacks.DET_LOG_EVERY))
    assert _det_lines(capsys.readouterr().out) == []


def test_on_prints_once_per_window_with_the_frame_age(monkeypatch, capsys, no_processing):
    monkeypatch.setattr(callbacks, "LATENCY_LOG", True)
    every = callbacks.DET_LOG_EVERY
    # running time 1.000 s, captured at 0.900 s -> 100 ms old on arrival
    element = _Element(now_ns=1_500_000_000, base_ns=500_000_000)
    buffer = SimpleNamespace(pts=900_000_000)
    for count in range(1, 3 * every + 1):
        callbacks.on_det_frame(element, buffer, _UserData(count))
    lines = _det_lines(capsys.readouterr().out)
    assert lines == [f"[DET] Frame {n} | lat 100 ms" for n in (every, 2 * every, 3 * every)]


def test_no_clock_yet_says_n_a(monkeypatch, capsys, no_processing):
    monkeypatch.setattr(callbacks, "LATENCY_LOG", True)
    callbacks.on_det_frame(_Element(now_ns=None), SimpleNamespace(pts=0), _UserData(callbacks.DET_LOG_EVERY))
    assert _det_lines(capsys.readouterr().out) == [f"[DET] Frame {callbacks.DET_LOG_EVERY} | lat n/a"]


def test_frame_zero_is_not_logged(monkeypatch, capsys, no_processing):
    monkeypatch.setattr(callbacks, "LATENCY_LOG", True)
    callbacks.on_det_frame(_Element(now_ns=1_000_000_000), SimpleNamespace(pts=0), _UserData(0))
    assert _det_lines(capsys.readouterr().out) == []


def test_a_failing_counter_never_raises(monkeypatch, capsys, no_processing):
    monkeypatch.setattr(callbacks, "LATENCY_LOG", True)

    class _Broken:
        def get_count(self):
            raise RuntimeError("boom")

    callbacks.on_det_frame(_Element(now_ns=1_000_000_000), SimpleNamespace(pts=0), _Broken())
    assert _det_lines(capsys.readouterr().out) == []
