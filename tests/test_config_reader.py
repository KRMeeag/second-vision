"""
Unit tests for the control-panel reader (workers/config_reader.py), scoped to
the two knobs: S:motor_strength and S:tts_volume, both 0.00-1.00
(firmware/control-panel/PROTOCOL.md).

No panel required. The cast tests are pure functions; the end-to-end cases
open ptys (see test_serial_worker.py for why a pty rather than a mock) and play
the panel's side of the protocol by writing lines into one of them.

    python3 -m pytest tests/test_config_reader.py -v

Why the knobs get their own tests: they are the panel values that reach the
wearer as a NUMBER rather than a flag. A bad flag is a wrong setting; a bad
strength is a wrong vibration — or, before October 2026, a dead serial-writer
thread and motors that never moved again — and a bad volume is speech the
wearer cannot hear.
"""

import os
import queue
import select
import threading
import time
import sys
from pathlib import Path

import pytest

# No conftest/package install — put src/ on the path so `second_vision.*` resolves.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import second_vision.workers.serial_worker as sw
from second_vision.core.config import SystemConfig
from second_vision.core.haptics import PULSE_PWM, PWM_FLOOR
from second_vision.workers.config_reader import _cast_value, config_reader_worker


# --- casting ------------------------------------------------------------------

KNOBS = ["motor_strength", "tts_volume"]


@pytest.mark.parametrize("key", KNOBS)
def test_a_knob_is_a_float(key):
    assert _cast_value(key, "0.60") == 0.6


@pytest.mark.parametrize("key", KNOBS)
def test_both_ends_of_the_knob_are_accepted(key):
    assert _cast_value(key, "0.00") == 0.0
    assert _cast_value(key, "1.00") == 1.0


@pytest.mark.parametrize("key", KNOBS)
@pytest.mark.parametrize("raw", ["nan", "inf", "-inf", "-0.50", "1.01", "1e9"])
def test_out_of_range_knob_value_is_rejected(key, raw):
    """
    float() accepts every one of these. PROTOCOL.md allows 0.00-1.00 only, and
    raw / 4095.0 on the panel cannot produce anything else — so a line like
    this is corruption, and the caller must keep the last good value.
    """
    with pytest.raises(ValueError):
        _cast_value(key, raw)


@pytest.mark.parametrize("key", KNOBS)
def test_unparseable_knob_value_is_rejected(key):
    with pytest.raises(ValueError):
        _cast_value(key, "0.6x")


def test_the_range_check_is_for_the_knobs_only():
    """cooldown_seconds is a float key too, and is legitimately above 1.0."""
    assert _cast_value("cooldown_seconds", "3.5") == 3.5


def test_without_a_panel_speech_is_at_full_volume():
    """
    The default is what every run without a panel — and every panel_no_volume
    or bench build, which never send the key — uses: espeak-ng's own loudness.
    """
    assert SystemConfig().tts_volume == 1.0


# --- end to end over ptys -------------------------------------------------------

@pytest.fixture
def ptys():
    """Factory for real openable serial devices: returns (path, far_end_fd)."""
    opened = []

    def make():
        controller, peripheral = os.openpty()
        opened.extend((controller, peripheral))
        return os.ttyname(peripheral), controller

    yield make
    for fd in opened:
        try:
            os.close(fd)
        except OSError:
            pass


class Mailbox:
    """Stands in for the PriorityMailbox; config_reader only ever offer()s."""

    def __init__(self):
        self.items = []

    def offer(self, item):
        self.items.append(item)


class PanelUserData:
    """The slice of SecondVisionUserData that both workers touch."""

    def __init__(self):
        self.shutdown_event = threading.Event()
        self.serial_queue = queue.Queue(maxsize=10)
        self.tts_queue = Mailbox()


def send_until(panel_fd, line, condition, timeout=4.0):
    """
    Write `line` as the panel would, repeating until `condition()` holds.

    Repeats because the reader flushes its input when it opens the port (the
    panel's ROM boot chatter), so a line written before that instant is
    legitimately thrown away. The real panel covers the same race with its
    10 s re-announce.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        os.write(panel_fd, line.encode())
        settle = time.monotonic() + 0.3
        while time.monotonic() < settle:
            if condition():
                return True
            time.sleep(0.02)
    return condition()


def wait_for(condition, timeout=3.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(0.02)
    return condition()


def next_motor(fd, buf, timeout=2.0):
    """
    (left, center, right) of the next non-zero 0x01 packet on the motor link,
    ACKing everything as the firmware does so the link stays up. `buf` is a
    one-element list carrying unread bytes between calls.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if select.select([fd], [], [], 0.05)[0]:
            buf[0] += os.read(fd, 256)
            os.write(fd, bytes([0xAA, 0xFF, 0x01]))
        while True:
            i = buf[0].find(bytes([0xAA, 0x01]))
            if i < 0 or len(buf[0]) < i + 6:
                break
            packet, buf[0] = buf[0][i:i + 6], buf[0][i + 6:]
            if any(packet[2:5]):
                return tuple(packet[2:5])
    return None


@pytest.mark.parametrize("key", KNOBS)
def test_a_bad_knob_line_keeps_the_last_good_value(ptys, key):
    panel_path, panel = ptys()
    cfg = SystemConfig()
    user_data = PanelUserData()
    reader = threading.Thread(target=config_reader_worker,
                              args=(user_data, cfg, panel_path, None), daemon=True)
    reader.start()
    try:
        assert send_until(panel, f"S:{key}:0.40\n",
                          lambda: cfg.get(key) == 0.4)
        for bad in ("nan", "-0.50", "1.50", "inf"):
            os.write(panel, f"S:{key}:{bad}\n".encode())
        # Lines are handled in order, so once this one lands every bad line
        # before it has been handled too.
        os.write(panel, b"S:tts_enabled:0\n")
        assert wait_for(lambda: cfg.get("tts_enabled") is False)
        assert cfg.get(key) == 0.4
    finally:
        user_data.shutdown_event.set()
        reader.join(timeout=3)


def test_the_two_knobs_do_not_touch_each_other(ptys):
    """Each key lands in its own setting — a volume line never moves the motors."""
    panel_path, panel = ptys()
    cfg = SystemConfig()
    user_data = PanelUserData()
    reader = threading.Thread(target=config_reader_worker,
                              args=(user_data, cfg, panel_path, None), daemon=True)
    reader.start()
    try:
        assert send_until(panel, "S:tts_volume:0.25\n",
                          lambda: cfg.get("tts_volume") == 0.25)
        assert cfg.get("motor_strength") == 1.0
        assert send_until(panel, "S:motor_strength:0.70\n",
                          lambda: cfg.get("motor_strength") == 0.7)
        assert cfg.get("tts_volume") == 0.25
    finally:
        user_data.shutdown_event.set()
        reader.join(timeout=3)


def test_the_knob_drives_the_motor_packet_end_to_end(ptys):
    """
    Panel line -> config_reader -> SystemConfig -> serial_worker -> wire bytes,
    with both real workers running. Knob fully down must still reach the board
    as a felt tap (PWM_FLOOR), never as 0.
    """
    panel_path, panel = ptys()
    motor_path, motor = ptys()
    cfg = SystemConfig()
    cfg.update(serial_port=motor_path)
    user_data = PanelUserData()
    workers = [
        threading.Thread(target=sw.serial_worker, args=(user_data, cfg), daemon=True),
        threading.Thread(target=config_reader_worker,
                         args=(user_data, cfg, panel_path, None), daemon=True),
    ]
    for t in workers:
        t.start()
    tap = {"left": 0, "center": PULSE_PWM, "right": 0, "hazard": False, "hazard_severity": 0}
    # The documented mapping, PWM_FLOOR + (duty - PWM_FLOOR) * strength, at
    # half travel — written out so the wire is checked against the spec.
    half = round(PWM_FLOOR + (PULSE_PWM - PWM_FLOOR) * 0.5)
    unread = [b""]
    try:
        for value, expected in (("0.00", PWM_FLOOR),
                                ("1.00", PULSE_PWM),
                                ("0.50", half)):
            assert send_until(panel, f"S:motor_strength:{value}\n",
                              lambda: cfg.get("motor_strength") == float(value))
            user_data.serial_queue.put(dict(tap))
            assert next_motor(motor, unread) == (0, expected, 0), f"knob at {value}"
    finally:
        user_data.shutdown_event.set()
        for t in workers:
            t.join(timeout=3)
