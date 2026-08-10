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
                # TODO: To be changed for graceful handling of failed ESP32
                print("[SERIAL STUB] 5 Consecutive ACK Failures")
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

    print(f"[SERIAL] Connected to ESP32 on {port_path} at {baudrate} baud")
    return port


# ==================== STUBS BELOW ====================
# Still to do: the send and ACK paths. Opening and closing the port is real
# (above); nothing yet writes to it.

def _send_packet(port, packet: bytes):
    """
    STUB — Replace with:
        if port:
            port.write(packet)
    """
    hex_str = packet.hex(" ")
    # print(f"[SERIAL STUB] → {hex_str}")

def _send_heartbeat(port):
    """STUB — sends heartbeat packet."""
    # Don't print heartbeats to avoid console spam
    pass

def _check_ack(port) -> bool:
    """
    STUB — Replace with:
        if port and port.in_waiting >= 3:
            data = port.read(3)
            return data[0] == 0xAA and data[1] == 0xFF
        return False
    """
    return True  # Pretend we always get ACK

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




        
        