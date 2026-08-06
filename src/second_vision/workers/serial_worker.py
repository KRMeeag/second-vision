"""Serial Worker — Consumes depth data, sends motor commands to ESP32."""

import queue
import struct
import time

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

# ==================== STUBS BELOW ====================
# Replace these with real pyserial implementation

def _open_serial_port(config):
    """
    STUB — Replace with:
        import serial
        return serial.Serial(port_path, baudrate=115200, timeout=0.01)
    """
    print("[SERIAL STUB] 📡 Port opened (mock)")
    return None
    
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
    STUB — Replace with:
        if port:
            port.close()
    """
    print("[SERIAL STUB] 📡 Port closed (mock)")




        
        