"""
Unit tests for the volume knob's last step: config.tts_volume -> espeak-ng's
amplitude (workers/tts_worker.py). No speaker, no espeak run: Popen and the
espeak lookup are replaced, so what is checked is the command line.

    python3 -m pytest tests/test_tts_volume.py -v

What the wearer depends on: full volume is exactly what the device always did,
the knob fully down is quiet but never silent (muting is the DETECT rocker's
job), turning the knob up never makes speech quieter, and no value off a serial
line can stop the TTS thread.
"""

import math
import threading
import time
import sys
from pathlib import Path

import pytest

# No conftest/package install — put src/ on the path so `second_vision.*` resolves.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import second_vision.workers.tts_worker as tw
from second_vision.core.config import SystemConfig
from second_vision.core.priority import PriorityMailbox

KNOB_POSITIONS = [i / 100 for i in range(101)]  # every value the panel can send


def test_full_volume_is_espeaks_own_default():
    """1.0 is the SystemConfig default: a run without a panel sounds as before."""
    assert tw._espeak_amplitude(1.0) == 100
    assert tw._espeak_amplitude(SystemConfig().tts_volume) == 100


def test_knob_fully_down_is_quiet_not_silent():
    assert tw._espeak_amplitude(0.0) == tw._AMPLITUDE_FLOOR
    assert tw._AMPLITUDE_FLOOR > 0


def test_half_knob_is_half_way_in_decibels():
    """Geometric, not linear: half travel is the geometric mean of floor and max."""
    assert tw._espeak_amplitude(0.5) == round((tw._AMPLITUDE_FLOOR * tw._AMPLITUDE_MAX) ** 0.5)


def test_a_low_knob_is_clearly_quiet():
    """
    The requirement as the user put it (2026-10-09): at 1 it is loud, at 0.15
    it is not. "Not loud" pinned as at least 12 dB below full — roughly half
    the perceived loudness or less — so a floor tuned later cannot quietly
    turn 0.15 back into "nearly as loud".
    """
    db = 20 * math.log10(tw._espeak_amplitude(0.15) / tw._AMPLITUDE_MAX)
    assert db <= -12.0


def test_turning_the_knob_up_never_makes_speech_quieter():
    amps = [tw._espeak_amplitude(v) for v in KNOB_POSITIONS]
    assert amps == sorted(amps)
    assert amps[0] < amps[-1]


@pytest.mark.parametrize("volume, expected", [
    (-0.5, tw._AMPLITUDE_FLOOR),  # under range: clamped to knob fully down
    (1.5, 100),            # over range: clamped to knob fully up
    (float("nan"), 100),   # not a finite number: the 1.0 default
    (float("inf"), 100),
    (None, 100),           # a config without the key
    ("loud", 100),
])
def test_out_of_contract_volume_never_raises(volume, expected):
    assert tw._espeak_amplitude(volume) == expected


def test_espeak_is_given_the_amplitude(monkeypatch):
    calls = []

    class FakePopen:
        def __init__(self, argv, **_):
            calls.append(argv)

    monkeypatch.setattr(tw.shutil, "which", lambda name: "/usr/bin/" + name)
    monkeypatch.setattr(tw.subprocess, "Popen", FakePopen)
    tw._start_espeak("car ahead", 50)
    argv = calls[0]
    assert argv[argv.index("-a") + 1] == "50"
    assert argv[-1] == "car ahead"          # the text stays last


class _UserData:
    def __init__(self):
        self.tts_queue = PriorityMailbox()
        self.shutdown_event = threading.Event()


def _run_worker_once(monkeypatch, volume):
    """Run the real tts_worker loop on one item; return the amplitude it used."""
    used = []

    class _Done:
        def poll(self):
            return 0

        def terminate(self):
            pass

    def fake_start(text, amplitude=None):
        used.append(amplitude)
        return _Done()

    monkeypatch.setattr(tw, "_start_espeak", fake_start)
    monkeypatch.setattr(tw, "_POLL_SECONDS", 0.0)
    cfg = SystemConfig()
    cfg.update(tts_volume=volume)
    ud = _UserData()
    ud.tts_queue.offer({"announce": "depth mode"})
    worker = threading.Thread(target=tw.tts_worker, args=(ud, cfg), daemon=True)
    worker.start()
    deadline = time.monotonic() + 3
    while not used and time.monotonic() < deadline:
        time.sleep(0.01)
    ud.shutdown_event.set()
    worker.join(timeout=3)
    return used


def test_the_worker_speaks_at_the_knobs_volume(monkeypatch):
    assert _run_worker_once(monkeypatch, 0.0) == [tw._AMPLITUDE_FLOOR]


def test_a_bad_volume_does_not_stop_the_worker(monkeypatch):
    """Regression guard: the knob value must never reach espeak as garbage."""
    assert _run_worker_once(monkeypatch, float("nan")) == [100]
