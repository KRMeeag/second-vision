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

# The ESP32 zeroes the motors after 3 s without traffic. Send something every
# second so two heartbeats can be lost — to a dropped write, a busy loop, a
# scheduling hiccup — before the wearer feels the device cut out. Raising this
# past ~1.5 s removes that margin; lowering it just spends bandwidth.
HEARTBEAT_INTERVAL_SECONDS = 1.0
# How long the loop parks waiting for a depth frame. Must stay comfortably
# under HEARTBEAT_INTERVAL_SECONDS: the loop can only notice a heartbeat is due
# once this returns, so a longer poll would cap how promptly one can be sent.
QUEUE_POLL_SECONDS = 0.25

# How long without any acknowledgement before the ESP32 is treated as gone.
# Measured in TIME, not in consecutive failed checks: ACKs are asynchronous, so
# a healthy board routinely has not replied yet at the instant we look, and at
# 30 FPS "5 failures in a row" is a third of a second — well inside normal
# round-trip jitter. Counting checks would have declared a working link dead.
# 2.5 s spans two missed heartbeats while still reacting inside the 3 s the
# board itself would take to notice us.
ACK_TIMEOUT_SECONDS = 2.5

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
    """
    Main serial writer loop.

    Two invariants this loop exists to hold, beyond forwarding packets:

      1. The link is never quiet for longer than HEARTBEAT_INTERVAL_SECONDS, no
         matter WHY there was nothing to say. Anything else lets the ESP32's
         watchdog zero the motors mid-use, which reads as a broken device.
      2. When the motors should not be running, that is said explicitly. Not
         sending is not the same as sending zero — the board holds its last
         command, so silence leaves the motors exactly where they were.
      3. If the board stops answering, the motors stop. Continuing to stream
         commands at something that is not listening produces a device the
         wearer still trusts and which is no longer telling them anything.
    """
    port = _open_serial_port(config)
    link = LinkHealth()
    # Seeded to now, not 0.0: at 0.0 the loop would fire a heartbeat on its very
    # first iteration, before anything has had a chance to need one.
    last_write_at = time.monotonic()
    vibration_was_enabled = True

    while not user_data.shutdown_event.is_set():
        # Link health is judged EVERY iteration, not only on frames we send.
        # Heartbeats are acknowledged too, so during an idle stretch they are
        # the only evidence the board is alive — checking solely after a motor
        # update would declare a perfectly healthy idle link dead.
        transition = link.update(_check_ack(port), time.monotonic())
        if transition == "down":
            print("[SERIAL] ESP32 stopped acknowledging — motors stopped. "
                  "Check power and cable; will resume automatically if it answers")
            _send_packet(port, _pack_motor_update(0, 0, 0))
            last_write_at = time.monotonic()
        elif transition == "up":
            print("[SERIAL] ESP32 responding again — resuming motor updates")

        try:
            depth = user_data.serial_queue.get(timeout=QUEUE_POLL_SECONDS)
        except queue.Empty:
            last_write_at = _heartbeat_if_due(port, last_write_at)
            continue

        if not config.get("vibration_enabled"):
            # Say "stop" once, then fall back to heartbeats. Previously this
            # path just `continue`d, which was survivable only because the
            # watchdog eventually zeroed the motors for us — and now that the
            # heartbeat keeps that watchdog fed, nothing would ever stop them.
            # Turning vibration off would have left the motors running forever.
            if vibration_was_enabled:
                _send_packet(port, _pack_motor_update(0, 0, 0))
                last_write_at = time.monotonic()
                vibration_was_enabled = False
            last_write_at = _heartbeat_if_due(port, last_write_at)
            continue

        vibration_was_enabled = True

        if link.down:
            # Keep heartbeating rather than going fully silent. The heartbeat
            # doubles as the probe that detects recovery — stop sending
            # entirely and there is nothing left for the board to answer, so a
            # link that came back would never be noticed.
            last_write_at = _heartbeat_if_due(port, last_write_at)
            continue

        # Apply motor strength multiplier
        strength = config.get("motor_strength")
        left    = min(255, int(depth["left"] * strength))
        right   = min(255, int(depth["right"] * strength))
        center  = min(255, int(depth["center"] * strength))

        # Send motor update
        packet = _pack_motor_update(left, center, right)
        _send_packet(port, packet)
        # Any packet resets the ESP32's watchdog, so a steady frame rate means
        # heartbeats are never needed — they exist for the gaps, not the flow.
        last_write_at = time.monotonic()

        # Check for hazard
        if depth.get("hazard"):
            hazard_pkt = _pack_hazard_alert(depth["hazard_severity"])
            _send_packet(port, hazard_pkt)

        # The ACK for this packet is read at the TOP of the next iteration, not
        # here. The board cannot have replied yet — checking immediately after
        # writing measures the round trip, not the link.

    # Leave the motors off rather than wherever the last frame put them. A
    # process that exits mid-warning would otherwise leave the wearer with a
    # vibration that never resolves, until the watchdog happens to clear it.
    _send_packet(port, _pack_motor_update(0, 0, 0))
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


class LinkHealth:
    """
    Is the ESP32 still answering?

    Separated from the worker loop so the policy can be tested by handing it
    timestamps, with no port, no board and no sleeping.

    update() reports only TRANSITIONS, because the actions taken on a link
    going down or coming back are one-shot: driving them off the steady state
    would re-send the stop packet every frame and log it forever.
    """

    def __init__(self, timeout: float = ACK_TIMEOUT_SECONDS):
        self.timeout = timeout
        self.down = False
        self._last_ack_at = None

    def update(self, ack_seen: bool, now: float):
        """
        Feed one observation. Returns "down" or "up" on a change, else None.

        The first call seeds the clock rather than treating "no ACK yet" as a
        failure: at startup nothing has been sent, so there is nothing for the
        board to have acknowledged.
        """
        if self._last_ack_at is None:
            self._last_ack_at = now

        if ack_seen:
            self._last_ack_at = now
            if self.down:
                self.down = False
                return "up"
            return None

        if not self.down and now - self._last_ack_at > self.timeout:
            self.down = True
            return "down"
        return None

    def reset(self) -> None:
        self.down = False
        self._last_ack_at = None


def _heartbeat_if_due(port, last_write_at: float) -> float:
    """
    Send a heartbeat if the link has been quiet too long. Returns the timestamp
    of the most recent write, for the caller to carry forward.

    Driven by TIME SINCE THE LAST WRITE rather than by "the queue was empty",
    because the watchdog does not care why we went quiet. Idle depth, vibration
    switched off, a run of dropped writes and a stalled producer all look
    identical to the ESP32, and only some of them used to reach the old
    queue-empty branch.
    """
    now = time.monotonic()
    if now - last_write_at < HEARTBEAT_INTERVAL_SECONDS:
        return last_write_at
    _send_heartbeat(port)
    return now


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




        
        