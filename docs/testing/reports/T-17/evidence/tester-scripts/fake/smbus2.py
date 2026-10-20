"""Tester fake of smbus2 for perf_sample: the MAX17048 registers from a JSON state file (FAKE_GAUGE_STATE)."""
import json, os
def _state():
    with open(os.environ["FAKE_GAUGE_STATE"]) as fh:
        return json.load(fh)
def _swap(w): return ((w & 0xFF) << 8) | (w >> 8)
class SMBus:
    def __init__(self, bus): self.bus = bus
    def __enter__(self): return self
    def __exit__(self, *a): return False
    def read_word_data(self, addr, reg):
        s = _state()
        assert addr == 0x36 and self.bus == 1, (addr, self.bus)
        if s.get("gauge") == "missing":
            raise OSError(121, "Remote I/O error")
        if reg == 2:
            raw = round(s["volts"] / (1.25 / 1000 / 16))
        elif reg == 4:
            raw = round(s["pct"] * 256)
        else:
            raise OSError("bad reg")
        return _swap(raw)          # SMBus returns the low byte first
