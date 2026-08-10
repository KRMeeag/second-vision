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
import select
import time
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


# --- sending ---------------------------------------------------------------

def test_bytes_actually_reach_the_wire(pty_port):
    """End to end: pack a motor update, send it, read it off the other end."""
    path, controller = pty_port
    port = sw._open_serial_port(config_with(serial_port=path))
    try:
        packet = sw._pack_motor_update(11, 22, 33)
        assert sw._send_packet(port, packet) is True
        port.flush()
        assert os.read(controller, len(packet)) == packet
    finally:
        sw._close_port(port)


def test_heartbeat_actually_sends_something(pty_port):
    """
    Regression test with teeth: _pack_heartbeat() previously had zero callers,
    so the ESP32's 3 s watchdog zeroed the motors during every quiet period.
    """
    path, controller = pty_port
    port = sw._open_serial_port(config_with(serial_port=path))
    try:
        assert sw._send_heartbeat(port) is True
        port.flush()
        assert os.read(controller, 3) == sw._pack_heartbeat()
    finally:
        sw._close_port(port)


def test_send_without_a_port_is_a_success_not_a_failure():
    """
    No board is a supported mode. Reporting failure here would make the ACK
    bookkeeping count failures on a link that was never meant to exist.
    """
    assert sw._send_packet(None, b"\xaa\x01") is True
    assert sw._send_heartbeat(None) is True


def test_send_reports_failure_without_raising():
    class Exploding:
        def write(self, _):
            raise OSError("cable yanked")

    assert sw._send_packet(Exploding(), b"\xaa\x01") is False


# --- ACK parsing -----------------------------------------------------------

def write_to_port(controller, data):
    os.write(controller, data)
    time.sleep(0.05)  # let the tty deliver before we poll in_waiting


def select_readable(fd, timeout=0.1):
    """True if anything is waiting to be read on the far end of the pty."""
    return bool(select.select([fd], [], [], timeout)[0])


def test_ack_is_recognized(pty_port):
    path, controller = pty_port
    port = sw._open_serial_port(config_with(serial_port=path))
    try:
        write_to_port(controller, bytes([0xAA, 0xFF, 0x01]))
        assert sw._check_ack(port) is True
    finally:
        sw._close_port(port)


def test_silence_is_not_an_ack(pty_port):
    path, _ = pty_port
    port = sw._open_serial_port(config_with(serial_port=path))
    try:
        assert sw._check_ack(port) is False
    finally:
        sw._close_port(port)


def test_unrelated_chatter_is_not_an_ack(pty_port):
    """Firmware debug output must not be mistaken for an acknowledgement."""
    path, controller = pty_port
    port = sw._open_serial_port(config_with(serial_port=path))
    try:
        write_to_port(controller, b"boot ok, rev 3\r\n")
        assert sw._check_ack(port) is False
    finally:
        sw._close_port(port)


def test_ack_survives_a_desync(pty_port):
    """
    The reason this scans instead of reading a fixed 3 bytes. One stray leading
    byte would shift every later fixed-size read by one and turn a healthy link
    into a permanent stream of ACK failures.
    """
    path, controller = pty_port
    port = sw._open_serial_port(config_with(serial_port=path))
    try:
        write_to_port(controller, b"\x99" + bytes([0xAA, 0xFF, 0x01]))
        assert sw._check_ack(port) is True
    finally:
        sw._close_port(port)


def test_ack_split_across_two_reads_is_still_seen(pty_port):
    """The 0xAA and the 0xFF can land in different chunks; that is not a miss."""
    path, controller = pty_port
    port = sw._open_serial_port(config_with(serial_port=path))
    try:
        write_to_port(controller, bytes([0xAA]))
        sw._check_ack(port)                       # consumes the leading 0xAA
        write_to_port(controller, bytes([0xFF, 0x01]))
        assert sw._check_ack(port) is True
    finally:
        sw._close_port(port)


def test_ack_carryover_buffer_stays_bounded(pty_port):
    """A chatty board must not grow this buffer without limit."""
    path, controller = pty_port
    port = sw._open_serial_port(config_with(serial_port=path))
    try:
        for _ in range(20):
            write_to_port(controller, b"noise noise noise ")
            sw._check_ack(port)
        assert len(getattr(port, "_sv_ack_tail", b"")) < len(sw.ACK_PREFIX)
    finally:
        sw._close_port(port)


def test_check_ack_without_a_port_does_not_report_a_dead_link():
    assert sw._check_ack(None) is True


def test_check_ack_reports_failure_without_raising():
    class Exploding:
        @property
        def in_waiting(self):
            raise OSError("device went away")

    assert sw._check_ack(Exploding()) is False


# --- heartbeat timing ------------------------------------------------------

def test_heartbeat_waits_until_it_is_due(pty_port):
    path, controller = pty_port
    port = sw._open_serial_port(config_with(serial_port=path))
    try:
        now = time.monotonic()
        assert sw._heartbeat_if_due(port, now) == now  # too soon; timestamp unchanged
        port.flush()
        assert not select_readable(controller), "sent a heartbeat that was not due"
    finally:
        sw._close_port(port)


def test_heartbeat_fires_once_overdue(pty_port):
    path, controller = pty_port
    port = sw._open_serial_port(config_with(serial_port=path))
    try:
        stale = time.monotonic() - sw.HEARTBEAT_INTERVAL_SECONDS - 0.1
        assert sw._heartbeat_if_due(port, stale) > stale  # timestamp advanced
        port.flush()
        assert os.read(controller, 3) == sw._pack_heartbeat()
    finally:
        sw._close_port(port)


def test_heartbeat_interval_leaves_watchdog_margin():
    """
    The ESP32 zeroes the motors after 3 s of silence. The interval has to leave
    room for at least one lost heartbeat, or a single dropped write is felt as
    the device cutting out.
    """
    assert sw.HEARTBEAT_INTERVAL_SECONDS <= 1.5


def test_queue_poll_is_shorter_than_the_heartbeat_interval():
    """
    The loop can only notice a heartbeat is due once the queue poll returns, so
    a poll longer than the interval would cap how promptly one goes out.
    """
    assert sw.QUEUE_POLL_SECONDS < sw.HEARTBEAT_INTERVAL_SECONDS


def test_stale_boot_chatter_is_flushed_at_open(pty_port):
    """
    Opening the port resets most ESP32 boards, so its boot output is usually
    already waiting. Since ACK detection scans for a byte pair, leftover noise
    could otherwise read as an acknowledgement of a packet we never sent.
    """
    path, controller = pty_port
    write_to_port(controller, bytes([0xAA, 0xFF, 0x01]))
    port = sw._open_serial_port(config_with(serial_port=path))
    try:
        assert sw._check_ack(port) is False
    finally:
        sw._close_port(port)
