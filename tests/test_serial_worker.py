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
import queue
import select
import struct
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
from second_vision.core.imu_telemetry import ImuTelemetry


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
    """
    End to end: pack a motor update, send it, read it off the other end.

    No port.flush() here: pyserial's write() already performs the blocking
    OS-level write, so the bytes are in the pty's buffer as soon as
    _send_packet() returns. flush() (termios tcdrain) additionally waits for
    the OS to consider the data "transmitted", which on a pty-backed port
    only happens once the far end (controller, read below) drains it — flush
    before that read is a real deadlock, not just a slow path.
    """
    path, controller = pty_port
    port = sw._open_serial_port(config_with(serial_port=path))
    try:
        packet = sw._pack_motor_update(11, 22, 33)
        assert sw._send_packet(port, packet) is True
        assert os.read(controller, len(packet)) == packet
    finally:
        sw._close_port(port)


def test_heartbeat_actually_sends_something(pty_port):
    """
    Regression test with teeth: _pack_heartbeat() previously had zero callers,
    so the ESP32's 3 s watchdog zeroed the motors during every quiet period.

    See test_bytes_actually_reach_the_wire for why there is no port.flush()
    before this read.
    """
    path, controller = pty_port
    port = sw._open_serial_port(config_with(serial_port=path))
    try:
        assert sw._send_heartbeat(port) is True
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


# --- link health / ACK-failure policy --------------------------------------

def test_link_starts_up():
    assert sw.LinkHealth().down is False


def test_startup_silence_is_not_a_dead_link():
    """
    Nothing has been sent yet at t=0, so there is nothing for the board to have
    acknowledged. Treating that as a failure would declare every run dead
    before it began.
    """
    link = sw.LinkHealth()
    assert link.update(False, 0.0) is None
    assert link.down is False


def test_link_goes_down_after_the_timeout():
    link = sw.LinkHealth()
    link.update(True, 0.0)
    assert link.update(False, sw.ACK_TIMEOUT_SECONDS + 0.1) == "down"
    assert link.down is True


def test_link_survives_normal_ack_latency():
    """
    The regression this policy replaces. ACKs are asynchronous, so at 30 FPS a
    healthy board routinely has not replied at the instant we look. The old
    counter declared a dead link after 5 such checks — a third of a second,
    well inside normal jitter.
    """
    link = sw.LinkHealth()
    link.update(True, 0.0)
    for i in range(1, 30):          # ~1 s of frames with no reply yet
        assert link.update(False, i / 30.0) is None
    assert link.down is False


def test_transitions_are_reported_once_not_every_frame():
    """
    Both actions are one-shot — sending the stop packet and logging. Driving
    them off the steady state would re-send and re-log on every frame.
    """
    link = sw.LinkHealth()
    link.update(True, 0.0)
    assert link.update(False, sw.ACK_TIMEOUT_SECONDS + 0.1) == "down"
    for i in range(10):
        assert link.update(False, sw.ACK_TIMEOUT_SECONDS + 0.2 + i) is None


def test_link_recovers_when_the_board_answers_again():
    link = sw.LinkHealth()
    link.update(True, 0.0)
    link.update(False, sw.ACK_TIMEOUT_SECONDS + 0.1)
    assert link.update(True, sw.ACK_TIMEOUT_SECONDS + 0.2) == "up"
    assert link.down is False


def test_recovery_is_also_reported_once():
    link = sw.LinkHealth()
    link.update(True, 0.0)
    link.update(False, sw.ACK_TIMEOUT_SECONDS + 0.1)
    link.update(True, sw.ACK_TIMEOUT_SECONDS + 0.2)
    assert link.update(True, sw.ACK_TIMEOUT_SECONDS + 0.3) is None


def test_no_board_mode_never_reports_a_dead_link():
    """
    _check_ack(None) reports success, so running without an ESP32 must not
    trip the policy — otherwise every development run would log a dead link.
    """
    link = sw.LinkHealth()
    for i in range(200):
        assert link.update(sw._check_ack(None), i * 0.1) is None
    assert link.down is False


def test_ack_timeout_spans_more_than_one_heartbeat():
    """
    An idle link is only proved alive by heartbeat ACKs. A timeout shorter than
    the heartbeat interval would declare a healthy idle link dead.
    """
    assert sw.ACK_TIMEOUT_SECONDS > sw.HEARTBEAT_INTERVAL_SECONDS


# --- heartbeat timing ------------------------------------------------------

def test_heartbeat_waits_until_it_is_due(pty_port):
    """
    See test_bytes_actually_reach_the_wire for why there is no port.flush()
    here — select_readable() already bounds its own wait.
    """
    path, controller = pty_port
    port = sw._open_serial_port(config_with(serial_port=path))
    try:
        now = time.monotonic()
        assert sw._heartbeat_if_due(port, now) == now  # too soon; timestamp unchanged
        assert not select_readable(controller), "sent a heartbeat that was not due"
    finally:
        sw._close_port(port)


def test_heartbeat_fires_once_overdue(pty_port):
    """
    See test_bytes_actually_reach_the_wire for why there is no port.flush()
    before this read.
    """
    path, controller = pty_port
    port = sw._open_serial_port(config_with(serial_port=path))
    try:
        stale = time.monotonic() - sw.HEARTBEAT_INTERVAL_SECONDS - 0.1
        assert sw._heartbeat_if_due(port, stale) > stale  # timestamp advanced
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


# --- BMI160 telemetry (0x02) decode/scan/extraction -------------------------
#
# v2 follow-up to the 0x02 protocol added without dedicated tests. Mirrors the
# ACK-scanning tests above wherever the same concern applies (split reads,
# desync survival), since _extract_imu_telemetry shares _check_ack's single
# drain rather than reading the port a second time.

def imu_frame(state: int, pitch: int, yaw: int) -> bytes:
    """Build one valid, correctly-checksummed 0x02 frame, wire-format exact."""
    pitch_bytes = struct.pack(">h", pitch)
    yaw_bytes = struct.pack(">h", yaw)
    payload = bytes([state]) + pitch_bytes + yaw_bytes
    checksum = sw.MSG_IMU_TELEMETRY
    for b in payload:
        checksum ^= b
    return bytes([0xAA, sw.MSG_IMU_TELEMETRY]) + payload + bytes([checksum])


# --- 1. decode correctness (signed int16) -----------------------------------

def test_decode_negative_pitch_and_yaw():
    """
    The whole point of struct.unpack(">h", ...): a naive "combine two bytes"
    decode would turn a negative reading into a large positive one instead of
    handling two's complement.
    """
    frame = imu_frame(0, -1234, -1)
    decoded = sw._decode_imu_frame(frame)
    assert decoded == {"state": "normal", "raw_pitch": -1234, "raw_yaw": -1}


def test_decode_pins_the_wire_byte_order():
    """
    Wire format pinned explicitly, not just via the imu_frame() helper: -1234
    as a signed big-endian int16 is 0xFB 0x2E. If the firmware and this parser
    ever disagree on byte order, this is the test that catches it.
    """
    state, pitch_hi, pitch_lo, yaw_hi, yaw_lo = 0, 0xFB, 0x2E, 0xFF, 0xFF  # yaw = -1
    checksum = sw.MSG_IMU_TELEMETRY ^ state ^ pitch_hi ^ pitch_lo ^ yaw_hi ^ yaw_lo
    frame = bytes([0xAA, sw.MSG_IMU_TELEMETRY, state, pitch_hi, pitch_lo, yaw_hi, yaw_lo, checksum])
    decoded = sw._decode_imu_frame(frame)
    assert decoded["raw_pitch"] == -1234
    assert decoded["raw_yaw"] == -1


def test_decode_unknown_state_byte_does_not_raise():
    """Ignore-unknown-values, same rule the rest of this project's protocols follow."""
    frame = imu_frame(99, 0, 0)
    decoded = sw._decode_imu_frame(frame)
    assert decoded["state"] == "unknown"


# --- 2. checksum rejection ---------------------------------------------------

def test_bad_checksum_is_dropped_not_decoded():
    frame = bytearray(imu_frame(1, 500, -500))
    frame[-1] ^= 0xFF  # corrupt the checksum byte only
    assert sw._decode_imu_frame(bytes(frame)) is None


def test_bad_checksum_frame_does_not_update_imu_telemetry(pty_port):
    """
    End to end through the real read path: a corrupted frame must reach
    ImuTelemetry not at all, leaving whatever reading was already there
    untouched — exactly what serial_worker's loop relies on (it only calls
    .update() when _take_imu_telemetry() returns non-None).
    """
    path, controller = pty_port
    port = sw._open_serial_port(config_with(serial_port=path))
    telemetry = ImuTelemetry()
    telemetry.update(state="normal", raw_pitch=111, raw_yaw=222, received_at=999.0)
    try:
        frame = bytearray(imu_frame(1, 500, -500))
        frame[-1] ^= 0xFF
        write_to_port(controller, bytes(frame))
        sw._check_ack(port)
        result = sw._take_imu_telemetry(port)

        assert result is None
        if result is not None:  # mirrors serial_worker's own guard
            telemetry.update(received_at=time.monotonic(), **result)
        assert telemetry.state == "normal"
        assert telemetry.raw_pitch == 111
        assert telemetry.raw_yaw == 222
        assert telemetry.received_at == 999.0
    finally:
        sw._close_port(port)


# --- 3. frame split across two reads ----------------------------------------

def test_telemetry_frame_split_across_two_reads_is_still_decoded(pty_port):
    """Mirrors test_ack_split_across_two_reads_is_still_seen for the 0x02 path."""
    path, controller = pty_port
    port = sw._open_serial_port(config_with(serial_port=path))
    try:
        frame = imu_frame(2, 12345, -12345)
        write_to_port(controller, frame[:3])
        sw._check_ack(port)
        assert sw._take_imu_telemetry(port) is None  # incomplete so far

        write_to_port(controller, frame[3:])
        sw._check_ack(port)
        result = sw._take_imu_telemetry(port)

        assert result == {"state": "wrong_pitch", "raw_pitch": 12345, "raw_yaw": -12345}
    finally:
        sw._close_port(port)


# --- 4. stale partial frame abandoned ---------------------------------------

def test_stale_partial_telemetry_frame_is_abandoned(pty_port):
    """
    A partial frame older than IMU_TELEMETRY_STALE_SECONDS must not be
    prepended to whatever arrives next — that would misalign a perfectly good
    new frame against dead bytes from a packet that is never coming. Times are
    injected directly (no real sleep) for a fast, deterministic test, the same
    way LinkHealth's tests hand it timestamps instead of sleeping.
    """
    path, controller = pty_port
    port = sw._open_serial_port(config_with(serial_port=path))
    try:
        port._sv_telemetry_tail = bytes([0xAA, sw.MSG_IMU_TELEMETRY, 0x00, 0x01])
        port._sv_telemetry_tail_started_at = (
            time.monotonic() - (sw.IMU_TELEMETRY_STALE_SECONDS + 0.1)
        )

        frame = imu_frame(0, 42, -42)
        write_to_port(controller, frame)
        sw._check_ack(port)
        result = sw._take_imu_telemetry(port)

        assert result == {"state": "normal", "raw_pitch": 42, "raw_yaw": -42}
    finally:
        sw._close_port(port)


# --- 5. ACK and telemetry coexist in one drain -------------------------------

def test_ack_and_telemetry_both_detected_in_one_drain(pty_port):
    """
    A single port.read() can legitimately contain both an ACK and a telemetry
    frame back to back. Both scans run over the same drained chunk (see
    _check_ack's docstring), so both must be found — this is the test that
    would catch a regression back to a second, competing read.
    """
    path, controller = pty_port
    port = sw._open_serial_port(config_with(serial_port=path))
    try:
        ack = bytes([0xAA, 0xFF, 0x01])
        frame = imu_frame(1, 100, -200)
        write_to_port(controller, ack + frame)

        assert sw._check_ack(port) is True
        assert sw._take_imu_telemetry(port) == {
            "state": "head_moving", "raw_pitch": 100, "raw_yaw": -200,
        }
    finally:
        sw._close_port(port)


# --- 6. ImuTelemetry thread-safety ------------------------------------------

def test_imu_telemetry_concurrent_access_does_not_corrupt_or_raise():
    """
    No dedicated SystemConfig concurrency test exists in this suite to mirror,
    so this follows SystemConfig's own get/update/snapshot shape directly:
    concurrent writers and a concurrent reader must neither raise nor produce
    a torn snapshot (e.g. a state from one update paired with a pitch from
    another).
    """
    telemetry = ImuTelemetry()
    errors = []

    def writer(tag):
        try:
            for i in range(200):
                telemetry.update(state="normal", raw_pitch=tag, raw_yaw=i,
                                  received_at=float(i))
        except Exception as exc:  # pragma: no cover — failure path only
            errors.append(exc)

    def reader():
        try:
            for _ in range(200):
                telemetry.snapshot()
                telemetry.get("raw_pitch")
        except Exception as exc:  # pragma: no cover — failure path only
            errors.append(exc)

    threads = [
        threading.Thread(target=writer, args=(1,)),
        threading.Thread(target=writer, args=(2,)),
        threading.Thread(target=reader),
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=5)

    assert errors == []
    snap = telemetry.snapshot()
    assert snap["state"] == "normal"
    assert snap["raw_pitch"] in (1, 2)  # whichever writer finished last


# --- motor strength: the control panel's knob --------------------------------
#
# config.motor_strength is the panel potentiometer (S:motor_strength:0.00-1.00,
# firmware/control-panel/PROTOCOL.md), applied by _apply_strength() on the way
# out. These pin what the wearer depends on: full strength changes nothing,
# silence stays silence, a felt tap is never turned down under the motors'
# start threshold, and no value off a serial line can stop this thread.

KNOB_POSITIONS = [i / 100 for i in range(101)]  # every value the panel can send


def test_full_strength_changes_nothing():
    """
    1.0 is the SystemConfig default, so it is every run without a panel: the
    wire must carry exactly what core/haptics.py decided, as before the knob.
    """
    for duty in (0, 1, PWM_FLOOR - 1, PWM_FLOOR, PWM_FLOOR + 1, 145, 200, 254, 255):
        assert sw._apply_strength(duty, 1.0) == duty


def test_silence_stays_silence_at_every_knob_position():
    for s in KNOB_POSITIONS:
        assert sw._apply_strength(0, s) == 0


def test_a_tap_is_never_turned_down_below_the_floor():
    """
    Regression test. A plain duty * strength took the rate-coded tap (always
    PULSE_PWM) under PWM_FLOOR for every knob position below ~0.35, and to 0
    at the bottom of the travel — motors still while the DEPTH rocker said ON.
    """
    for s in KNOB_POSITIONS:
        assert sw._apply_strength(PULSE_PWM, s) >= PWM_FLOOR, f"knob at {s:.2f}"


def test_knob_fully_down_is_the_weakest_felt_tap():
    assert sw._apply_strength(PULSE_PWM, 0.0) == PWM_FLOOR


def test_turning_the_knob_up_never_weakens_a_tap():
    duties = [sw._apply_strength(PULSE_PWM, s) for s in KNOB_POSITIONS]
    assert duties == sorted(duties)
    assert duties[0] < duties[-1]  # and it does actually change something


def test_a_duty_at_or_below_the_floor_passes_through():
    """Nothing above the start threshold to scale — e.g. amplitude-path level 1."""
    for s in (0.0, 0.5, 1.0):
        assert sw._apply_strength(PWM_FLOOR, s) == PWM_FLOOR
        assert sw._apply_strength(PWM_FLOOR - 10, s) == PWM_FLOOR - 10


@pytest.mark.parametrize("strength, expected", [
    (-0.5, PWM_FLOOR),          # under range: clamped to knob fully down
    (1.5, PULSE_PWM),           # over range: clamped to knob fully up
    (float("nan"), PULSE_PWM),  # not a finite number: the 1.0 default
    (float("inf"), PULSE_PWM),
    (float("-inf"), PULSE_PWM),
    (None, PULSE_PWM),
    ("0.5x", PULSE_PWM),
])
def test_out_of_contract_strength_never_raises(strength, expected):
    assert sw._apply_strength(PULSE_PWM, strength) == expected


class FakeBoard:
    """
    The ESP32's end of a pty: collects the motor packets the worker sends and
    ACKs whatever arrives, as the firmware does, so LinkHealth stays up and
    the worker keeps forwarding frames instead of falling back to heartbeats.
    """

    def __init__(self, fd):
        self.fd = fd
        self.buf = b""

    def next_motor(self, timeout=2.0):
        """(left, center, right) of the next non-zero 0x01 packet, or None."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if select_readable(self.fd, 0.05):
                self.buf += os.read(self.fd, 256)
                os.write(self.fd, bytes([0xAA, 0xFF, 0x01]))
            while True:
                i = self.buf.find(bytes([0xAA, 0x01]))
                if i < 0 or len(self.buf) < i + 6:
                    break
                packet, self.buf = self.buf[i:i + 6], self.buf[i + 6:]
                if any(packet[2:5]):
                    return tuple(packet[2:5])
        return None


class WorkerUserData:
    """The slice of SecondVisionUserData that serial_worker() touches."""

    def __init__(self):
        self.serial_queue = queue.Queue(maxsize=10)
        self.shutdown_event = threading.Event()


TAP = {"left": 0, "center": PULSE_PWM, "right": 0, "hazard": False, "hazard_severity": 0}
# The documented mapping at half travel, written out rather than computed with
# _apply_strength() so the loop tests check the wire against the spec.
HALF_KNOB_TAP = round(PWM_FLOOR + (PULSE_PWM - PWM_FLOOR) * 0.5)


def test_turning_the_knob_changes_the_very_next_packet(pty_port):
    """
    The real worker loop: config is read per frame, so a knob change reaches
    the wire on the next frame — no restart, no queue to drain.
    """
    path, controller = pty_port
    cfg = config_with(serial_port=path, motor_strength=1.0)
    user_data = WorkerUserData()
    board = FakeBoard(controller)
    worker = threading.Thread(target=sw.serial_worker, args=(user_data, cfg), daemon=True)
    worker.start()
    try:
        for strength, expected in ((1.0, PULSE_PWM),
                                   (0.0, PWM_FLOOR),
                                   (0.5, HALF_KNOB_TAP),
                                   (1.0, PULSE_PWM)):
            cfg.update(motor_strength=strength)
            user_data.serial_queue.put(dict(TAP))
            assert board.next_motor() == (0, expected, 0), f"knob at {strength}"
    finally:
        user_data.shutdown_event.set()
        worker.join(timeout=3)


def test_a_bad_strength_does_not_stop_the_worker(pty_port):
    """
    Regression test: a NaN strength raised inside the loop and ended the
    thread, so the motors went silent for the rest of the run.
    """
    path, controller = pty_port
    cfg = config_with(serial_port=path, motor_strength=float("nan"))
    user_data = WorkerUserData()
    board = FakeBoard(controller)
    worker = threading.Thread(target=sw.serial_worker, args=(user_data, cfg), daemon=True)
    worker.start()
    try:
        user_data.serial_queue.put(dict(TAP))
        assert board.next_motor() == (0, PULSE_PWM, 0)
        assert worker.is_alive()
    finally:
        user_data.shutdown_event.set()
        worker.join(timeout=3)
