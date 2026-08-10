"""Serial Worker — Consumes depth data, sends motor commands to ESP32."""

import errno
import queue
import struct
import time

# Guarded like every other optional dependency in this project: a missing
# pyserial must degrade to "no board attached", not take the whole pipeline
# down. The depth and haptics work runs on machines with no ESP32 and
# sometimes no pyserial at all.
try:
    import serial
    PYSERIAL_AVAILABLE = True
except ImportError:  # pragma: no cover — exercised only on machines without pyserial
    PYSERIAL_AVAILABLE = False

# Writes must not be able to wedge the worker thread. If the ESP32 stops
# draining its buffer the kernel's TX queue fills, and a blocking write() would
# park this thread forever — the motors would then hold their last command with
# nothing left running to correct it. A short timeout turns that into a dropped
# packet, which the next frame replaces anyway.
WRITE_TIMEOUT_SECONDS = 0.05
# Reads are polled from the same loop, so they must never block either.
READ_TIMEOUT_SECONDS = 0.01

# The firmware's acknowledgement, 0xAA 0xFF <byte>. Only the two-byte prefix is
# matched — the third byte is the firmware's business, and pinning it here would
# couple us to a detail neither side agreed to.
ACK_PREFIX = bytes([0xAA, 0xFF])

# A broken link fails every frame, i.e. ~30 times a second. Without throttling,
# one unplugged cable buries the depth log — which is exactly the log you need
# in order to notice the cable.
SERIAL_ERROR_LOG_SECONDS = 5.0
_last_serial_error_at = 0.0


def _log_serial_error(message: str) -> None:
    """Report a link failure at most once every SERIAL_ERROR_LOG_SECONDS."""
    global _last_serial_error_at
    now = time.monotonic()
    if now - _last_serial_error_at < SERIAL_ERROR_LOG_SECONDS:
        return
    _last_serial_error_at = now
    print(f"[SERIAL] {message} (throttled)")

# ============================================================
# INTERFACE CONTRACT (do not change):
#   Input:  user_data.serial_queue — dict with FIVE always-present fields:
#             left / center / right  int 0-255  MOTOR DUTY (see below)
#             hazard                 bool       debounced + latched ground break
#             hazard_severity        int 0-255  break SIZE; 0 when hazard is False
#   Output: Binary packets to ESP32 via USB serial
#   Config: config.motor_strength
#
#   The zone values arrive as PWM DUTY, already shaped by core/haptics.py — they
#   are not perception scores for this worker to interpret. This worker is
#   TRANSPORT: it must not add a curve, a floor or a threshold of its own.
#
#   Corrects an earlier version of this block that listed only four fields and
#   called "hazard" optional while the code below indexed "hazard_severity"
#   directly. The fields, types and ranges are unchanged; only the description
#   was wrong.
# ============================================================
# CAUTION — config.motor_strength multiplies these duties on the way out (below),
# which can push a shaped value back UNDER the motors' start threshold and undo
# the PWM floor. Scaling belongs in the haptic stage, where the floor is known.
# Left as-is for now: it is the existing behaviour and changing it is a UX
# decision, not a transport one.

def serial_worker(user_data, config):
    """Main serial writer loop."""
    port = _open_serial_port(config)
    ack_failures = 0

    while not user_data.shutdown_event.is_set():
        try:
            depth = user_data.serial_queue.get(timeout=1.0)
        except queue.Empty:
            # Send heartbeat during idle
            _send_heartbeat(port)
            continue

        if not config.get("vibration_enabled"):
            continue

        # Apply motor strength multiplier
        strength = config.get("motor_strength")
        left    = min(255, int(depth["left"] * strength))
        right   = min(255, int(depth["right"] * strength))
        center  = min(255, int(depth["center"] * strength))

        # Send motor update
        packet = _pack_motor_update(left, center, right)
        _send_packet(port, packet)

        # Check for hazard
        if depth.get("hazard"):
            hazard_pkt = _pack_hazard_alert(depth["hazard_severity"])
            _send_packet(port, hazard_pkt)

        # Non-blocking ACK check
        ack = _check_ack(port)
        if ack:
            ack_failures = 0
        else:
            ack_failures += 1
            if ack_failures > 5:
                # TODO: still only reports. The owned decision (see the V1 depth
                # handoff) is to zero the motors and go quiet on a dead link —
                # a device sending into a cable nobody is listening to is worse
                # than one that stops. Not yet implemented.
                _log_serial_error("5 consecutive ACK failures — ESP32 may be unresponsive")
                ack_failures = 0

    _close_port(port)

def _pack_motor_update(left, center, right) -> bytes:
    """Pack motor update into binary protocol."""
    msg_type = 0x01
    payload = struct.pack("BBB", left, center, right)
    checksum = msg_type ^ left ^ center ^ right
    return struct.pack("BB3sB", 0xAA, msg_type, payload, checksum)

def _pack_hazard_alert(severity, pattern=0x01) -> bytes:
    """Pack hazard alert into binary protocol."""
    msg_type = 0x04
    checksum = msg_type ^ severity ^ pattern
    return struct.pack("BBBBB", 0xAA, msg_type, severity, pattern, checksum)

def _pack_heartbeat() -> bytes:
    """Pack heartbeat packet."""
    msg_type = 0xFE
    return struct.pack("BBB", 0xAA, msg_type, msg_type) # checksum = type XOR nothing =type

def _open_serial_port(config):
    """
    Open the ESP32 link, or return None to run without a board.

    None is a supported outcome, not a failure path: no --serial-port given, no
    pyserial installed, device absent, or permission denied all end up here and
    all leave the rest of the pipeline running normally. The alternative —
    refusing to start — would mean depth work could not be run on any machine
    without the hardware plugged in.

    Every None return prints WHY once at startup, with the fix where there is
    one. A device that silently does nothing is the single most expensive
    failure mode this project keeps hitting.
    """
    port_path = config.get("serial_port")
    if not port_path:
        print("[SERIAL] No --serial-port given — running without an ESP32 "
              "(packets are built but not sent)")
        return None

    if not PYSERIAL_AVAILABLE:
        print(f"[SERIAL] pyserial not installed — cannot open {port_path}. "
              "Fix: pip install pyserial")
        return None

    baudrate = config.get("serial_baudrate") or 115200
    try:
        port = serial.Serial(
            port_path,
            baudrate=baudrate,
            timeout=READ_TIMEOUT_SECONDS,
            write_timeout=WRITE_TIMEOUT_SECONDS,
        )
    except Exception as exc:
        # Dispatch on errno, NOT on exception type. pyserial catches the OSError
        # the kernel raised and re-raises its own SerialException, so `except
        # PermissionError` never fires here — it only looks like it would. The
        # errno survives the re-wrap, so that is what we read.
        code = getattr(exc, "errno", None)
        if code == errno.EACCES:
            # Overwhelmingly the most common first-run failure on the Pi, and
            # the fix is non-obvious: adding the group is not enough on its own.
            print(f"[SERIAL] Permission denied opening {port_path} — running without an ESP32. "
                  "Fix: sudo usermod -aG dialout $USER, then LOG OUT and back in "
                  "(check with `groups`)")
        elif code == errno.ENOENT:
            print(f"[SERIAL] {port_path} does not exist — running without an ESP32. "
                  "Check the cable, or `ls /dev/tty*` to find the right device")
        else:
            # Anything else the driver throws. Caught broadly on purpose: no
            # serial-port problem is worth killing a running pipeline over.
            print(f"[SERIAL] Could not open {port_path} ({exc!r}) — running without an ESP32")
        return None

    # Drop whatever was already sitting in the buffers. Opening the port resets
    # most ESP32 boards, so the first thing waiting to be read is usually its
    # boot chatter — and _check_ack() scans for a byte pair rather than reading
    # fixed-size frames, so leftover noise could read as an acknowledgement of a
    # packet this process never sent.
    try:
        port.reset_input_buffer()
        port.reset_output_buffer()
    except Exception as exc:  # non-fatal: a usable port with a dirty buffer
        print(f"[SERIAL] Could not flush buffers on {port_path}: {exc!r}")

    print(f"[SERIAL] Connected to ESP32 on {port_path} at {baudrate} baud")
    return port


def _send_packet(port, packet: bytes) -> bool:
    """
    Write one packet. Returns True if it went out.

    A None port is not a failure — it is the documented no-board mode, and it
    returns True so the ACK bookkeeping upstream does not start counting
    failures on a link that was never supposed to exist.

    Nothing here retries. These packets are a real-time stream: another motor
    frame is ~33 ms behind this one and describes the world better, so
    re-sending a stale command is strictly worse than dropping it.
    """
    if port is None:
        return True
    try:
        port.write(packet)
        return True
    except Exception as exc:
        # SerialTimeoutException (the ESP32 stopped draining its buffer) plus
        # anything else the driver raises — an unplugged adapter surfaces here
        # as an OSError mid-write. Never raises into the worker loop: dropping
        # this frame keeps the next one coming.
        _log_serial_error(f"write failed: {exc!r}")
        return False


def _send_heartbeat(port) -> bool:
    """
    Tell the ESP32 we are still alive.

    Its watchdog zeroes the motors after 3 s without traffic, which is the
    correct behaviour for a dead Pi and the wrong behaviour for an idle one.
    """
    return _send_packet(port, _pack_heartbeat())


def _check_ack(port) -> bool:
    """
    Has the ESP32 acknowledged anything since the last check?

    An ACK is 0xAA 0xFF <byte>. Rather than reading a fixed 3 bytes, this drains
    whatever has arrived and SCANS it for the 0xAA 0xFF pair, because a fixed
    read cannot recover from a desync: one spurious byte on the line — a boot
    message, line noise, a debug print from the firmware — would shift every
    subsequent read by one and turn a healthy link into a permanent stream of
    ACK failures. Scanning resynchronizes on its own.

    Bytes that arrive split across two calls are handled by carrying a short
    remainder on the port object, so an ACK straddling a read boundary is still
    seen. The buffer is capped: this is a liveness check, not a message queue,
    and unbounded growth on a chatty board would be a slow leak.

    A None port returns True — no link, so no link to have lost.
    """
    if port is None:
        return True
    try:
        waiting = port.in_waiting
        chunk = port.read(waiting) if waiting else b""
    except Exception as exc:
        _log_serial_error(f"read failed: {exc!r}")
        return False

    if not chunk:
        return False

    buffered = getattr(port, "_sv_ack_tail", b"") + chunk
    found = ACK_PREFIX in buffered
    # Keep only what could still be the front of a split ACK.
    port._sv_ack_tail = buffered[-(len(ACK_PREFIX) - 1):]
    return found

def _close_port(port):
    """
    Close the port on shutdown. Safe to call with None, and safe to call twice.

    Guarded because this runs during teardown, where an unhandled exception is
    both useless (we are exiting anyway) and actively harmful — it would skip
    the rest of shutdown and leave the device holding whatever it was last told.
    """
    if port is None:
        return
    try:
        port.close()
        print("[SERIAL] Port closed")
    except Exception as exc:
        print(f"[SERIAL] Error closing port: {exc!r}")




        
        