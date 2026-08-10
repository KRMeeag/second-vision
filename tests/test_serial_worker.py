"""
Unit tests for the ESP32 serial link (workers/serial_worker.py).

No ESP32 required. The "real port" cases open a pty (pseudo-terminal) instead:
the kernel gives us a genuine character device that pyserial opens, configures
and writes to exactly as it would a USB adapter, so the open path is exercised
for real rather than against a mock. Run with:

    python3 -m pytest tests/test_serial_worker.py -v

WHAT THESE CHECK. The wire format is SHARED with the firmware owner and is not
this file's to change, so the packing tests pin the bytes exactly — if one of
them fails, either the protocol changed (which needs both sides) or something
broke. The transport tests instead pin one property: no serial-port problem may
ever raise into the caller. A missing board, a missing driver, a bad path or a
locked-down device must all degrade to "run without an ESP32", because the
pipeline has to keep working on development machines with no hardware attached.
"""

import os
import sys
from pathlib import Path

import pytest

# No conftest/package install — put src/ on the path so `second_vision.*` resolves.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import second_vision.workers.serial_worker as sw
from second_vision.core.config import SystemConfig


@pytest.fixture
def pty_port():
    """A real openable serial device, and the far end to inspect it with."""
    controller, peripheral = os.openpty()
    yield os.ttyname(peripheral), controller
    for fd in (controller, peripheral):
        try:
            os.close(fd)
        except OSError:
            pass


def config_with(**kwargs):
    cfg = SystemConfig()
    cfg.update(**kwargs)
    return cfg


# --- wire format (shared with the firmware — do not change unilaterally) ----

def test_motor_packet_bytes():
    assert sw._pack_motor_update(10, 20, 30) == bytes([0xAA, 0x01, 10, 20, 30, 0x01 ^ 10 ^ 20 ^ 30])


def test_hazard_packet_bytes():
    assert sw._pack_hazard_alert(200) == bytes([0xAA, 0x04, 200, 0x01, 0x04 ^ 200 ^ 0x01])


def test_heartbeat_packet_bytes():
    assert sw._pack_heartbeat() == bytes([0xAA, 0xFE, 0xFE])


def test_packets_are_distinguishable_by_type_byte():
    """The parser dispatches on byte 1, so the three types must not collide."""
    types = {sw._pack_motor_update(0, 0, 0)[1],
             sw._pack_hazard_alert(0)[1],
             sw._pack_heartbeat()[1]}
    assert len(types) == 3


def test_every_packet_starts_with_the_sync_byte():
    for packet in (sw._pack_motor_update(1, 2, 3), sw._pack_hazard_alert(4), sw._pack_heartbeat()):
        assert packet[0] == 0xAA


# --- opening: the no-board paths ------------------------------------------

def test_no_port_configured_returns_none():
    assert sw._open_serial_port(SystemConfig()) is None


def test_missing_device_returns_none_instead_of_raising():
    assert sw._open_serial_port(config_with(serial_port="/dev/definitely-not-a-port")) is None


def test_directory_as_port_returns_none():
    """A path that exists but is not a serial device must not raise either."""
    assert sw._open_serial_port(config_with(serial_port="/tmp")) is None


def test_failure_explains_itself(capsys):
    """
    Silence is the expensive failure mode here: a device that does nothing and
    says nothing is indistinguishable from a device that is working.
    """
    sw._open_serial_port(config_with(serial_port="/dev/definitely-not-a-port"))
    out = capsys.readouterr().out
    assert "/dev/definitely-not-a-port" in out
    assert "without an ESP32" in out


def test_missing_device_names_the_cable(capsys):
    """
    Regression test. pyserial re-wraps the kernel's OSError in its own
    SerialException, so dispatching on exception type silently loses this
    message — the handler looks correct and never runs.
    """
    sw._open_serial_port(config_with(serial_port="/dev/definitely-not-a-port"))
    assert "does not exist" in capsys.readouterr().out


def test_permission_denied_names_the_dialout_fix(tmp_path, capsys):
    """
    The same re-wrap hid this one, and it is the failure a new Pi hits first.
    The fix needs a re-login, so printing the errno alone would not be enough
    to unblock anyone.
    """
    blocked = tmp_path / "locked-device"
    blocked.touch()
    blocked.chmod(0o000)
    if os.access(blocked, os.R_OK):  # running as root — the OS won't refuse us
        pytest.skip("cannot produce EACCES as this user")
    sw._open_serial_port(config_with(serial_port=str(blocked)))
    out = capsys.readouterr().out
    assert "Permission denied" in out
    assert "dialout" in out


# --- opening: the real path ------------------------------------------------

def test_opens_a_real_device(pty_port):
    path, _ = pty_port
    port = sw._open_serial_port(config_with(serial_port=path))
    assert port is not None
    try:
        assert port.is_open
        assert port.baudrate == 115200
    finally:
        sw._close_port(port)


def test_baudrate_is_configurable(pty_port):
    path, _ = pty_port
    port = sw._open_serial_port(config_with(serial_port=path, serial_baudrate=57600))
    try:
        assert port.baudrate == 57600
    finally:
        sw._close_port(port)


def test_timeouts_are_set_so_the_worker_cannot_wedge(pty_port):
    """
    Both are required. A blocking write on a stalled ESP32 would park the only
    thread that can ever correct the motors, leaving them holding their last
    command with nothing left running to change it.
    """
    path, _ = pty_port
    port = sw._open_serial_port(config_with(serial_port=path))
    try:
        assert port.timeout == sw.READ_TIMEOUT_SECONDS
        assert port.write_timeout == sw.WRITE_TIMEOUT_SECONDS
    finally:
        sw._close_port(port)


def test_open_announces_the_connection(pty_port, capsys):
    path, _ = pty_port
    port = sw._open_serial_port(config_with(serial_port=path))
    try:
        assert path in capsys.readouterr().out
    finally:
        sw._close_port(port)


# --- closing ---------------------------------------------------------------

def test_close_accepts_none():
    sw._close_port(None)  # must not raise


def test_close_is_idempotent(pty_port):
    """Shutdown can reach this twice; the second call must not raise."""
    path, _ = pty_port
    port = sw._open_serial_port(config_with(serial_port=path))
    sw._close_port(port)
    sw._close_port(port)
    assert not port.is_open


def test_close_survives_a_broken_port():
    class Exploding:
        def close(self):
            raise OSError("device went away")

    sw._close_port(Exploding())  # must not raise
