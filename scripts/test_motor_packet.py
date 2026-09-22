"""Bench-test only: sends one MSG_MOTOR_UPDATE (0x01) packet to the ESP32
over the Pi's UART, to verify the wired link independently of the full
second_vision pipeline. Matches serial_worker.py's _pack_motor_update().

Usage (on the RPi5): python3 scripts/test_motor_packet.py /dev/serial0
"""
import struct
import sys
import time

import serial

PORT = sys.argv[1] if len(sys.argv) > 1 else "/dev/serial0"
BAUD = 115200


def pack_motor_update(left, center, right):
    msg_type = 0x01
    payload = struct.pack("BBB", left, center, right)
    checksum = msg_type ^ left ^ center ^ right
    return struct.pack("BB3sB", 0xAA, msg_type, payload, checksum)


port = serial.Serial(PORT, BAUD, timeout=1)
time.sleep(0.2)  # let the line settle

for left, center, right in [(80, 0, 0), (0, 150, 0), (0, 0, 220), (0, 0, 0)]:
    packet = pack_motor_update(left, center, right)
    port.write(packet)
    print(f"sent left={left} center={center} right={right} -> {packet.hex()}")
    ack = port.read(3)
    print(f"ack bytes: {ack.hex() if ack else '<none received>'}")
    time.sleep(1)

port.close()
