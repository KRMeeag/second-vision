#!/usr/bin/env python3
"""Tester T-17 8: one real, READ-ONLY comparison. The vendor script's own formulas (~/x120x/bat.py, copied verbatim)
read the MAX17048 registers; perf_sample.battery() reads them back to back. No write, no root, no GPIO drive."""
import struct, sys, time
sys.path.insert(0, "/home/sv-rpi5/Projects/Second-Vision/worktrees/kenth/docs/testing/tools")
import smbus2
import perf_sample as ps

def readVoltage(bus):                      # verbatim from ~/x120x/bat.py
     address = 0x36
     read = bus.read_word_data(address, 2)
     swapped = struct.unpack("<H", struct.pack(">H", read))[0]
     voltage = swapped * 1.25 /1000/16
     return voltage

def readCapacity(bus):                     # verbatim from ~/x120x/bat.py
     address = 0x36
     read = bus.read_word_data(address, 4)
     swapped = struct.unpack("<H", struct.pack(">H", read))[0]
     capacity = swapped/256
     return capacity

bus = smbus2.SMBus(1)
for i in range(6):
    v, c = readVoltage(bus), readCapacity(bus)
    pct, volts = ps.battery()
    print(f"{time.strftime('%H:%M:%S')} vendor formulas: {v:.4f} V {c:.3f} %   perf_sample.battery(): {volts} V {pct} %   "
          f"dV={abs(v - volts):.4f}  dP={abs(c - pct):.3f}   ac_power={ps.ac_power()!r}")
    time.sleep(1.0)
