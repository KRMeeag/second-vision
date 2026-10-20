"""Tester fake of gpiod (v2 API as perf_sample uses it): GPIO6 level from FAKE_GAUGE_STATE ("ac": "1"/"0"/"busy")."""
import json, os
class _Value:
    ACTIVE, INACTIVE = "active", "inactive"
class _Direction:
    INPUT, OUTPUT = "input", "output"
class _line:
    Direction = _Direction
    Value = _Value
line = _line
LOG = os.environ.get("FAKE_GPIOD_LOG")
def LineSettings(**k):
    return k
class _Req:
    def __init__(self, level): self.level = level
    def __enter__(self): return self
    def __exit__(self, *a): return False
    def get_values(self): return [_Value.ACTIVE if self.level == "1" else _Value.INACTIVE]
def request_lines(path, consumer=None, config=None):
    with open(os.environ["FAKE_GAUGE_STATE"]) as fh:
        s = json.load(fh)
    if LOG:
        with open(LOG, "a") as fh:
            fh.write(json.dumps({"path": path, "consumer": consumer, "config": {str(k): v for k, v in (config or {}).items()}}) + "\n")
    if s.get("ac") == "busy":
        raise OSError(16, "Device or resource busy")
    return _Req(s["ac"])
