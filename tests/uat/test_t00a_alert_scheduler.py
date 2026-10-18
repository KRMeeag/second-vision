"""
T-00a (repeating spoken system alerts), requirements REQ-CON-03, REQ-CON-05, REQ-CON-07, REQ-CON-11
(cadence rule) and REQ-DTA-08 (stop in about 1 s). Written by the Tester (tests/uat/); independent of the
Developer's tests/test_alerts*.py.

There is no UAT case of its own. The task is the enabler for TC-022-002..006, TC-023-002/004,
TC-024-002/003 and TC-025-002..006 (each alert's own task builds its wording and trigger). Requirement text
quoted from docs/testing/REQUIREMENTS.md (via the brief):
    REQ-CON-03: "the REQ-CON-02 alert repeats every 30 s while depth estimation is enabled on the controller
                 board. It is said once while depth estimation is disabled, including when depth is switched
                 off mid-run."
    REQ-CON-05: "It repeats every 5 minutes."          REQ-CON-07: "It repeats every 10 seconds."
    REQ-CON-11: "...the REQ-CON-10 pattern repeats every 30 s while object detection is enabled. It plays once
                 while detection is disabled, including when detection is switched off mid-run."

What is REAL in these tests: workers/tts_worker.py, core/priority.py PriorityMailbox, core/alerts.py
AlertScheduler, workers/alert_worker.py, core/config.py SystemConfig, core/turn_event.py TurnEvent.
What is a STAND-IN: the `espeak-ng` binary (a Python script first on PATH that records phrase, start and end
time, and logs a "cut" when it gets SIGTERM). Level: mock-integration. Nothing here proves that the alert is
heard through earphones.
Clocks: cadence of 10 s and 30 s runs in REAL time. 300 s and the "several alerts for 5 minutes" runs use a
clock injected into the scheduler (AlertScheduler(mailbox, clock)): a WarpClock (jumps forward on command) or a
ScaledClock (runs K times faster than real time). Only the scheduler's clock is virtual; the speech worker, the
mailbox and the stub run in real time.
"""
from __future__ import annotations

import json
import math
import os
import random
import re
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

REPO = Path(__file__).resolve().parents[2]
SRC = REPO / "src"
BASELINE_SRC = REPO / ".claude" / "baselines" / "T-00a" / "src"
sys.path.insert(0, str(SRC))

from second_vision.core.alerts import AlertScheduler  # noqa: E402
from second_vision.core.config import SystemConfig  # noqa: E402
from second_vision.core.priority import PriorityMailbox  # noqa: E402
from second_vision.core.turn_event import TurnEvent  # noqa: E402
from second_vision.workers import tts_worker as tw  # noqa: E402
from second_vision.workers.alert_worker import alert_worker  # noqa: E402

BASE_PATH = os.environ.get("PATH", "")

# The long real-time runs (about 17 minutes together) are OPT-IN so that `pytest tests/` stays fast:
#     T00A_LONG=1 python -m pytest tests/uat/test_t00a_alert_scheduler.py -v -rA -s
# The Tester's evidence runs set it. A skipped test is never a pass (docs/testing/reports/README.md).
long_run = pytest.mark.skipif(os.environ.get("T00A_LONG") != "1",
                              reason="long real-time run (opt-in): set T00A_LONG=1")

STUB = '''#!{py} -S
import json, os, signal, sys, time
text = sys.argv[-1]
def w(ev):
    with open(os.environ["STUB_LOG"], "a") as f:
        f.write(json.dumps({{"ev": ev, "text": text, "t": time.monotonic(), "pid": os.getpid(), "amp": (sys.argv[sys.argv.index("-a") + 1] if "-a" in sys.argv else None)}}) + "\\n")
def term(*_):
    w("cut")
    os._exit(143)
signal.signal(signal.SIGTERM, term)
dur = 0.3
try:
    d = json.load(open(os.environ["STUB_DUR_FILE"]))
    dur = d.get("default", dur)
    for pre, v in d.get("prefix", {{}}).items():
        if text.startswith(pre):
            dur = v
except Exception:
    pass
w("start")
end = time.monotonic() + dur
while time.monotonic() < end:
    time.sleep(0.01)
w("end")
'''


def det(phrase, tier="normal", priority=1.0):
    """A detection item exactly as callbacks.py builds it (Rule 2 contract)."""
    return {"label": "person", "zone": "left", "confidence": 0.9, "priority": priority, "tier": tier,
            "phrase": phrase}


def wait_until(pred, timeout, step=0.01):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        v = pred()
        if v:
            return v
        time.sleep(step)
    return pred()


class RecMailbox(PriorityMailbox):
    """The real mailbox that also records every offer() and every successful take()."""

    def __init__(self):
        super().__init__()
        self.offers = []
        self.takes = []
        self.config = None

    def offer(self, item):
        self.offers.append((time.monotonic(), item))
        super().offer(item)

    def take(self):
        item = super().take()
        if item is not None:
            enabled = self.config.get("tts_enabled") if self.config is not None else True
            self.takes.append((time.monotonic(), item, enabled))
        return item


class WarpClock:
    """The scheduler's clock: real monotonic plus a skew that the test can push forward."""

    def __init__(self):
        self.skew = 0.0
        self.hist = [(0.0, 0.0)]

    def __call__(self):
        return time.monotonic() + self.skew

    def warp(self, dt):
        self.skew += dt
        self.hist.append((time.monotonic(), self.skew))

    def virt(self, t_real):
        s = 0.0
        for tc, sk in self.hist:
            if tc <= t_real:
                s = sk
        return t_real + s


class ScaledClock:
    """The scheduler's clock running K times faster than real time."""

    def __init__(self, k):
        self.k = k
        self.t0 = time.monotonic()

    def __call__(self):
        return self.t0 + self.k * (time.monotonic() - self.t0)


def _pid_alive(pid):
    try:
        with open(f"/proc/{pid}/stat") as f:
            return f.read().rsplit(")", 1)[1].split()[0] != "Z"
    except OSError:
        return False


class Rig:
    """Real tts_worker + PriorityMailbox + AlertScheduler + alert_worker on a stub espeak-ng."""

    def __init__(self, tmp, monkeypatch, clock=None, default_dur=0.3, prefix_dur=None):
        bindir = tmp / "bin"
        bindir.mkdir(exist_ok=True)
        self.log = tmp / "stub.log"
        self.log.write_text("")
        self.durf = tmp / "dur.json"
        self.set_dur(default_dur, prefix_dur or {})
        stub = bindir / "espeak-ng"
        stub.write_text(STUB.format(py=sys.executable))
        stub.chmod(0o755)
        monkeypatch.setenv("PATH", str(bindir) + os.pathsep + BASE_PATH)
        monkeypatch.setenv("STUB_LOG", str(self.log))
        monkeypatch.setenv("STUB_DUR_FILE", str(self.durf))
        self.mailbox = RecMailbox()
        self.config = SystemConfig()
        self.mailbox.config = self.config
        self.clock = clock or time.monotonic
        self.alerts = AlertScheduler(self.mailbox, self.clock)
        self.ud = SimpleNamespace(tts_queue=self.mailbox, shutdown_event=threading.Event(),
                                  turn_event=TurnEvent(), alerts=self.alerts)
        self.threads = {}

    def set_dur(self, default=0.3, prefix=None):
        self.durf.write_text(json.dumps({"default": default, "prefix": prefix or {}}))

    def start(self, tts=True, alert=True):
        if tts:
            self.threads["tts"] = threading.Thread(target=tw.tts_worker, args=(self.ud, self.config),
                                                   daemon=True, name="tts")
        if alert:
            self.threads["alert"] = threading.Thread(target=alert_worker, args=(self.ud, self.config),
                                                     daemon=True, name="alert")
        for t in self.threads.values():
            if not t.is_alive() and t.ident is None:
                t.start()
        return self

    def stop(self, timeout=3.0):
        t0 = time.monotonic()
        self.ud.shutdown_event.set()
        for t in self.threads.values():
            t.join(timeout)
        return time.monotonic() - t0

    def alive(self):
        return [n for n, t in self.threads.items() if t.is_alive()]

    def events(self):
        out = []
        for line in self.log.read_text().splitlines():
            try:
                out.append(json.loads(line))
            except ValueError:
                pass
        return out

    def utts(self, text=None, startswith=None):
        """Utterances recorded by the stub: dicts text/start/end/outcome ('end', 'cut' or 'running')."""
        running, done = {}, []
        for e in self.events():
            if e["ev"] == "start":
                running[e["pid"]] = {"text": e["text"], "start": e["t"], "end": None, "outcome": "running",
                                     "pid": e["pid"], "amp": e.get("amp")}
            else:
                u = running.pop(e["pid"], None)
                if u:
                    u["end"], u["outcome"] = e["t"], e["ev"]
                    done.append(u)
        done.extend(running.values())
        done.sort(key=lambda u: u["start"])
        if text is not None:
            done = [u for u in done if u["text"] == text]
        if startswith is not None:
            done = [u for u in done if u["text"].startswith(startswith)]
        return done

    def count(self, text):
        return len(self.utts(text))

    def orphans(self):
        return [u["pid"] for u in self.utts() if u["outcome"] == "running" and _pid_alive(u["pid"])]

    def kill_orphans(self):
        for pid in self.orphans():
            try:
                os.kill(pid, signal.SIGKILL)
            except OSError:
                pass


@pytest.fixture
def mk(tmp_path, monkeypatch):
    rigs = []

    def make(**kw):
        sub = tmp_path / f"r{len(rigs)}"
        sub.mkdir()
        r = Rig(sub, monkeypatch, **kw)
        rigs.append(r)
        return r

    yield make
    for r in rigs:
        r.stop()
        r.kill_orphans()


def offered_alert_items(rig, prefix="ALERT"):
    return [it for _, it in rig.mailbox.offers if "announce" in it and it["announce"].startswith(prefix)]


def alert_take_times(rig, prefix="ALERT"):
    return [(t, it, en) for t, it, en in rig.mailbox.takes if "announce" in it and it["announce"].startswith(prefix)]


# =====================================================================================================
# Criterion 1: spoken at once, then on the cadence (real time for 10 s and 30 s)
# =====================================================================================================
@pytest.mark.timeout(200)
@long_run
def test_req_con_07_and_req_con_03_cadence_10s_and_30s_together_in_real_time(mk):
    """10 s and 30 s alerts raised together: at once (1 s), then start-to-start every N s +-1 s, each to its
    end, never cut; ~95 s of real time (10 starts of the 10 s alert, 4 of the 30 s alert)."""
    rig = mk(default_dur=0.4).start()
    t0 = time.monotonic()
    rig.alerts.raise_alert("ten", "ALERT ten", interval=10.0)
    rig.alerts.raise_alert("thirty", "ALERT thirty", interval=30.0)
    time.sleep(t0 + 94.5 - time.monotonic())
    rig.stop()
    ten, thirty = rig.utts("ALERT ten"), rig.utts("ALERT thirty")
    print(f"[tester] 10 s alert: {len(ten)} utterances, 30 s alert: {len(thirty)} utterances")
    assert len(ten) >= 10 and len(thirty) >= 4
    assert ten[0]["start"] - t0 <= 1.0, "first utterance of the first alert not within 1 s of the raise"
    assert thirty[0]["start"] - t0 <= 1.5, "second alert due at the same instant should follow the first within ~1.5 s"
    worst = 0.0
    for seq, interval in ((ten, 10.0), (thirty, 30.0)):
        for a, b in zip(seq, seq[1:]):
            gap = b["start"] - a["start"]
            worst = max(worst, abs(gap - interval))
            assert abs(gap - interval) <= 1.0, f"start-to-start {gap:.2f} s for an interval of {interval} s"
        assert all(u["outcome"] == "end" for u in seq), "an alert utterance was cut with nothing to cut it"
    print(f"[tester] worst deviation from the interval: {worst:.3f} s")
    allu = rig.utts()
    for a, b in zip(allu, allu[1:]):
        assert a["end"] <= b["start"] + 0.02, "two utterances overlap"
    for it in offered_alert_items(rig):
        assert set(it.keys()) == {"announce"}, "queue item must be exactly {'announce': text}"
    assert rig.alive() == [], "a worker thread did not stop"


@pytest.mark.timeout(120)
def test_req_con_05_cadence_300s_on_a_warped_clock(mk):
    """The 5-minute alert (REQ-CON-05) on a WarpClock: spoken at once; not before 300 s; at 300 s +-1 s; 4 starts.
    Only the scheduler's clock is virtual (stated in the module docstring)."""
    clk = WarpClock()
    rig = mk(clock=clk, default_dur=0.3).start()
    t0 = time.monotonic()
    rig.alerts.raise_alert("battery", "ALERT five minutes", interval=300.0)
    assert wait_until(lambda: rig.count("ALERT five minutes") >= 1, 2.0), "not spoken within 2 s"
    starts = [rig.utts("ALERT five minutes")[0]["start"]]
    assert starts[0] - t0 <= 1.0
    for n in (2, 3, 4):
        # jump to 2 s (virtual) before due, then check it stays quiet, then let it fall due
        elapsed_v = clk.virt(time.monotonic()) - clk.virt(starts[-1])
        clk.warp(300.0 - 2.0 - elapsed_v)
        time.sleep(0.6)
        assert rig.count("ALERT five minutes") == n - 1, "spoken before its 300 s were up"
        assert wait_until(lambda: rig.count("ALERT five minutes") >= n, 4.0), "not repeated when due"
        starts.append(rig.utts("ALERT five minutes")[n - 1]["start"])
    gaps = [clk.virt(b) - clk.virt(a) for a, b in zip(starts, starts[1:])]
    print(f"[tester] 300 s alert, virtual start-to-start gaps: {[round(g, 2) for g in gaps]}")
    assert all(abs(g - 300.0) <= 1.0 for g in gaps), gaps
    assert wait_until(lambda: all(u["outcome"] != "running" for u in rig.utts("ALERT five minutes")), 3.0)
    assert all(u["outcome"] == "end" for u in rig.utts("ALERT five minutes"))


# =====================================================================================================
# Criteria 2, 3: once, repeat off, repeat back on  (REQ-CON-03 / REQ-CON-11 cadence rule)
# =====================================================================================================
def test_req_con_03_once_alert_is_spoken_exactly_once(mk):
    clk = WarpClock()
    rig = mk(clock=clk).start()
    rig.alerts.raise_alert("c", "ALERT once")
    assert wait_until(lambda: rig.count("ALERT once") == 1, 2.0)
    clk.warp(100000.0)
    time.sleep(1.5)
    assert rig.count("ALERT once") == 1
    assert rig.alerts.is_raised("c")


def test_req_con_03_repeat_off_after_it_was_spoken_is_not_spoken_again(mk):
    clk = WarpClock()
    rig = mk(clock=clk).start()
    rig.alerts.raise_alert("c", "ALERT depth", interval=30.0)
    assert wait_until(lambda: rig.count("ALERT depth") == 1, 2.0)
    time.sleep(0.5)
    rig.alerts.set_repeat("c", False)
    clk.warp(200.0)
    time.sleep(1.5)
    assert rig.count("ALERT depth") == 1


def test_req_con_03_repeat_off_before_the_first_utterance_finished_still_delivers_it_once(mk):
    clk = WarpClock()
    rig = mk(clock=clk, default_dur=1.5).start()
    rig.alerts.raise_alert("c", "ALERT depth", interval=30.0)
    assert wait_until(lambda: rig.utts("ALERT depth"), 2.0)
    rig.alerts.set_repeat("c", False)                       # utterance is under way
    assert wait_until(lambda: rig.utts("ALERT depth")[0]["outcome"] != "running", 4.0)
    assert rig.utts("ALERT depth")[0]["outcome"] == "end", "the utterance under way was cut"
    clk.warp(200.0)
    time.sleep(1.5)
    assert rig.count("ALERT depth") == 1


def test_req_con_03_repeat_off_before_the_first_utterance_started_still_delivers_it_once(mk):
    clk = WarpClock()
    rig = mk(clock=clk, prefix_dur={"DET": 2.0}).start()
    rig.mailbox.offer(det("DET busy phrase"))
    assert wait_until(lambda: rig.utts("DET busy phrase"), 2.0)
    rig.alerts.raise_alert("c", "ALERT depth", interval=30.0)
    rig.alerts.set_repeat("c", False)                       # the alert has not even been offered yet
    assert wait_until(lambda: rig.count("ALERT depth") == 1, 6.0), "the owed first utterance was lost"
    clk.warp(200.0)
    time.sleep(1.5)
    assert rig.count("ALERT depth") == 1


def test_req_con_03_repeat_back_on_resumes_the_cadence(mk):
    """Literal reading (to report to the Lead): repeat back on while the alert is still raised resumes; when the
    last utterance is more than N s ago it is spoken at once, then every N s."""
    clk = WarpClock()
    rig = mk(clock=clk).start()
    rig.alerts.raise_alert("c", "ALERT depth", interval=30.0)
    assert wait_until(lambda: rig.count("ALERT depth") == 1, 2.0)
    rig.alerts.set_repeat("c", False)
    clk.warp(100.0)
    time.sleep(1.0)
    assert rig.count("ALERT depth") == 1
    rig.alerts.set_repeat("c", True)
    assert wait_until(lambda: rig.count("ALERT depth") == 2, 2.0), "repeat back on did not resume"
    s2 = rig.utts("ALERT depth")[1]["start"]
    clk.warp(30.0 - 2.0 - (clk.virt(time.monotonic()) - clk.virt(s2)))
    time.sleep(0.6)
    assert rig.count("ALERT depth") == 2
    assert wait_until(lambda: rig.count("ALERT depth") == 3, 4.0)


def test_req_con_03_depth_switched_off_mid_run_speaks_once_via_callable(mk):
    """The documented usage: repeat=lambda: config.get('depth_enabled'). Depth on: repeats; switched off mid-run:
    no more; raised while depth already off: said once."""
    clk = WarpClock()
    rig = mk(clock=clk).start()
    rig.alerts.raise_alert("c", "ALERT controller", interval=30.0,
                           repeat=lambda: rig.config.get("depth_enabled"))
    assert wait_until(lambda: rig.count("ALERT controller") == 1, 2.0)
    clk.warp(31.0)
    assert wait_until(lambda: rig.count("ALERT controller") == 2, 3.0)
    rig.config.update(depth_enabled=False)                  # switched off mid-run
    time.sleep(0.3)
    clk.warp(500.0)
    time.sleep(1.5)
    assert rig.count("ALERT controller") == 2, "spoken after depth was switched off"
    rig.alerts.raise_alert("d", "ALERT other", interval=30.0, repeat=lambda: rig.config.get("depth_enabled"))
    assert wait_until(lambda: rig.count("ALERT other") == 1, 2.0)
    clk.warp(500.0)
    time.sleep(1.5)
    assert rig.count("ALERT other") == 1, "raised while depth off must be said once"


# =====================================================================================================
# Criterion 4: cleared means stopped
# =====================================================================================================
def test_req_con_03_clear_stops_at_once_and_clear_of_unknown_does_nothing(mk):
    clk = WarpClock()
    rig = mk(clock=clk).start()
    rig.alerts.raise_alert("c", "ALERT x", interval=30.0)
    assert wait_until(lambda: rig.count("ALERT x") == 1, 2.0)
    assert rig.alerts.clear("c") is True
    assert rig.alerts.clear("c") is False
    assert rig.alerts.clear("never-raised") is False
    clk.warp(500.0)
    time.sleep(1.5)
    assert rig.count("ALERT x") == 1


def test_req_con_03_clear_during_the_utterance_does_not_cut_it(mk):
    clk = WarpClock()
    rig = mk(clock=clk, default_dur=1.5).start()
    rig.alerts.raise_alert("c", "ALERT x", interval=30.0)
    assert wait_until(lambda: rig.utts("ALERT x"), 2.0)
    assert rig.alerts.clear("c")
    assert wait_until(lambda: rig.utts("ALERT x")[0]["outcome"] != "running", 4.0)
    assert rig.utts("ALERT x")[0]["outcome"] == "end"
    clk.warp(500.0)
    time.sleep(1.5)
    assert rig.count("ALERT x") == 1


def test_req_con_03_clear_removes_the_queued_item_and_nothing_is_spoken(mk):
    rig = mk().start()
    rig.ud.turn_event.push(5, time.monotonic())             # head-turn mute: the worker consumes nothing for 1 s
    time.sleep(0.15)
    rig.alerts.raise_alert("c", "ALERT queued", interval=30.0)
    assert wait_until(lambda: rig.mailbox.peek() is not None, 1.0), "alert never reached the slot"
    assert rig.alerts.clear("c")
    assert rig.mailbox.peek() is None, "queued item not removed on clear"
    time.sleep(1.4)
    assert rig.count("ALERT queued") == 0


def test_req_con_03_clear_never_removes_an_item_that_is_not_the_alerts(mk):
    rig = mk().start()
    rig.ud.turn_event.push(5, time.monotonic())
    time.sleep(0.15)
    rig.alerts.raise_alert("c", "ALERT queued", interval=30.0)
    assert wait_until(lambda: rig.mailbox.peek() is not None, 1.0)
    mode = {"announce": "MODE depth mode"}
    rig.mailbox.offer(mode)                                 # ties with the alert's item: replaces it
    assert rig.mailbox.peek() is mode
    assert rig.alerts.clear("c")
    assert rig.mailbox.peek() is mode, "clear removed somebody else's item"
    assert rig.alerts.clear("c") is False
    assert rig.mailbox.peek() is mode, "clear of a not-raised alert touched the slot"
    assert wait_until(lambda: rig.count("MODE depth mode") == 1, 3.0)
    assert rig.count("ALERT queued") == 0


# =====================================================================================================
# Criterion 5: several alerts never cut or lose each other
# =====================================================================================================
def test_req_con_05_three_alerts_due_together_are_spoken_one_after_the_other_each_to_the_end(mk):
    rig = mk(default_dur=0.6).start()
    for n, iv in (("a", 10.0), ("b", 30.0), ("c", 300.0)):
        rig.alerts.raise_alert(n, f"ALERT {n}", interval=iv)
    assert wait_until(lambda: len(rig.utts(startswith="ALERT")) == 3
                      and all(u["outcome"] != "running" for u in rig.utts(startswith="ALERT")), 6.0)
    us = rig.utts(startswith="ALERT")
    assert [u["text"] for u in us] == ["ALERT a", "ALERT b", "ALERT c"]
    assert all(u["outcome"] == "end" for u in us)
    assert all(a["end"] <= b["start"] + 0.02 for a, b in zip(us, us[1:]))


def _busy_time(utts, a, b):
    """Length of [a, b] covered by the union of the utterance intervals."""
    segs = sorted((max(u["start"], a), min(u["end"] if u["end"] else b, b)) for u in utts
                  if (u["end"] or b) > a and u["start"] < b)
    total, cur_end = 0.0, a
    for s, e in segs:
        s = max(s, cur_end)
        if e > s:
            total += e - s
            cur_end = e
    return total


def _union(windows):
    out = []
    for a, b in sorted(windows):
        if out and a <= out[-1][1]:
            out[-1] = (out[-1][0], max(out[-1][1], b))
        else:
            out.append((a, b))
    return out


def _overlap(windows, a, b):
    return sum(max(0.0, min(e, b) - max(s, a)) for s, e in windows)


def check_alert_invariants(rig, specs, t_end, mute_windows=(), k=1.0, slack=0.5, deliveries_extra=None,
                           per_handover=0.15, t_raise=None):
    """Black-box invariants over the stub's log. specs: {text: interval_virtual}. Times real; interval_r = iv/k.
    1. serial: no two utterances overlap. 2. an alert utterance that was cut was cut by a NON-alert item
    (never by another alert). 3. no doubling: consecutive deliveries of one alert >= interval_r - 0.2.
    4. no loss: consecutive deliveries <= interval_r + busy time in between (+ head-turn mutes, as a UNION of the
    windows) + slack + per_handover x (utterances in between: each hand-over costs the scheduler tick, the
    worker poll and the stub's start-up), and the last one not older than that at the end of the run."""
    allu = rig.utts()
    mutes = _union(mute_windows)
    probs = []
    for a, b in zip(allu, allu[1:]):
        if a["end"] is not None and a["end"] > b["start"] + 0.03:
            probs.append(f"overlap {a['text']} / {b['text']}")
    texts_alert = set(specs)
    for i, u in enumerate(allu):
        if u["text"] in texts_alert and u["outcome"] == "cut" and u["end"] < t_end - 1.0:
            nxt = allu[i + 1] if i + 1 < len(allu) else None
            if nxt is not None and nxt["text"] in texts_alert:
                probs.append(f"alert {u['text']} cut by alert {nxt['text']}")
    for text, iv in specs.items():
        interval_r = iv / k
        dels = [u["start"] for u in allu if u["text"] == text and u["outcome"] in ("end", "running")]
        if deliveries_extra:
            dels = sorted(dels + deliveries_extra.get(text, []))
        if t_raise is not None:                              # the first utterance is due at the raise
            first = dels[0] if dels else t_end
            busy = _busy_time(allu, t_raise, first)
            mute = _overlap(mutes, t_raise, first)
            hand = sum(1 for u in allu if t_raise <= u["start"] <= first)
            if first - t_raise > busy + mute + slack + per_handover * hand:
                probs.append(f"{text}: first utterance late or never delivered (raise to first {first - t_raise:.2f} s, "
                             f"busy {busy:.2f}, mute {mute:.2f})")
            if not dels:
                continue
        for a, b in zip(dels, dels[1:]):
            gap = b - a
            if gap < interval_r - 0.2:
                probs.append(f"{text}: doubled (gap {gap:.2f} < {interval_r:.2f})")
            busy = _busy_time(allu, a + interval_r, b)
            mute = _overlap(mutes, a + interval_r, b)
            hand = sum(1 for u in allu if a + interval_r - 0.5 <= u["start"] <= b)
            if gap > interval_r + busy + mute + slack + per_handover * hand:
                probs.append(f"{text}: late (gap {gap:.2f}, interval {interval_r:.2f}, busy {busy:.2f}, "
                             f"mute {mute:.2f}, {hand} utterances in between)")
        if dels:
            last = dels[-1]
            busy = _busy_time(allu, last + interval_r, t_end)
            mute = _overlap(mutes, last + interval_r, t_end)
            hand = sum(1 for u in allu if last + interval_r - 0.5 <= u["start"] <= t_end)
            if t_end - last > interval_r + busy + mute + slack + per_handover * hand:
                probs.append(f"{text}: silent at the end (last {t_end - last:.2f} s ago, interval {interval_r:.2f})")
        elif t_raise is None:
            probs.append(f"{text}: never delivered")
    return probs


def _producers(rig, rng, stop, label="MODE", with_turns=False):
    """Random mode announcements and detection phrases (both tiers) from a thread, like the real producers."""
    log = {"mode": 0, "det": 0}

    def run():
        n = 0
        while not stop.is_set():
            stop.wait(rng.uniform(0.2, 1.3))
            if stop.is_set():
                break
            n += 1
            r = rng.random()
            if r < 0.3:
                rig.mailbox.offer({"announce": f"{label} {n}"})
                log["mode"] += 1
            else:
                rig.mailbox.offer(det(f"DET {n}", tier="urgent" if r > 0.85 else "normal",
                                      priority=rng.uniform(0.5, 3.0)))
                log["det"] += 1

    th = threading.Thread(target=run, daemon=True)
    th.start()
    return th, log


@pytest.mark.timeout(240)
@pytest.mark.parametrize("seed", [11, 12])
@long_run
def test_req_con_05_three_alerts_for_5_minutes_with_random_announcements_and_detections(mk, seed):
    """10 s, 30 s and 300 s alerts raised at once on a ScaledClock (K=5: 330 virtual s in 66 real s) with random
    mode announcements and detection phrases (normal and urgent) arriving; every alert utterance to its end or cut
    only by a non-alert, never lost for the rest of the run, never doubled."""
    k = 5.0
    rng = random.Random(seed)
    rig = mk(clock=ScaledClock(k), default_dur=0.3, prefix_dur={"DET": 0.4, "MODE": 0.35}).start()
    stop = threading.Event()
    t0 = time.monotonic()
    specs = {"ALERT a10": 10.0, "ALERT b30": 30.0, "ALERT c300": 300.0}
    for i, (t, iv) in enumerate(specs.items()):
        rig.alerts.raise_alert(f"n{i}", t, interval=iv)
    th, log = _producers(rig, rng, stop)
    time.sleep(t0 + 330.0 / k - time.monotonic())
    stop.set()
    th.join(2)
    t_end = time.monotonic()
    rig.stop()
    probs = check_alert_invariants(rig, specs, t_end, k=k, slack=0.5)
    counts = {t: len(rig.utts(t)) for t in specs}
    cuts = {t: sum(1 for u in rig.utts(t) if u["outcome"] == "cut") for t in specs}
    print(f"[tester] seed {seed}: mode={log['mode']} det={log['det']} alert utterances={counts} cuts={cuts}")
    assert not probs, probs
    assert counts["ALERT a10"] >= 25 and counts["ALERT b30"] >= 9 and counts["ALERT c300"] >= 2


# =====================================================================================================
# Criterion 6: not lost  (deterministic forced paths, real worker)
# =====================================================================================================
def test_req_con_03_alert_cut_by_a_mode_announcement_is_spoken_again_after_it(mk):
    rig = mk(default_dur=1.2, prefix_dur={"MODE": 0.4}).start()
    rig.alerts.raise_alert("c", "ALERT x", interval=30.0)
    assert wait_until(lambda: rig.utts("ALERT x"), 2.0)
    time.sleep(0.3)
    rig.mailbox.offer({"announce": "MODE depth mode"})
    assert wait_until(lambda: len(rig.utts("ALERT x")) >= 2 and rig.utts("ALERT x")[-1]["outcome"] == "end", 6.0)
    seq = [(u["text"], u["outcome"]) for u in rig.utts()]
    assert seq == [("ALERT x", "cut"), ("MODE depth mode", "end"), ("ALERT x", "end")], seq


def test_req_con_03_once_alert_cut_by_an_urgent_detection_is_spoken_again(mk):
    rig = mk(default_dur=1.2, prefix_dur={"DET": 0.4}).start()
    rig.alerts.raise_alert("c", "ALERT once")
    assert wait_until(lambda: rig.utts("ALERT once"), 2.0)
    time.sleep(0.3)
    rig.mailbox.offer(det("DET urgent", tier="urgent"))
    assert wait_until(lambda: len(rig.utts("ALERT once")) >= 2 and rig.utts("ALERT once")[-1]["outcome"] == "end", 6.0)
    assert [u["outcome"] for u in rig.utts("ALERT once")] == ["cut", "end"]
    time.sleep(1.0)
    assert len(rig.utts("ALERT once")) == 2


def test_req_con_03_head_turn_does_not_discard_a_waiting_alert_but_does_discard_others(mk):
    rig = mk().start()
    rig.ud.turn_event.push(5, time.monotonic())             # mute 1 s
    time.sleep(0.15)
    rig.alerts.raise_alert("c", "ALERT waits", interval=30.0)
    assert wait_until(lambda: rig.mailbox.peek() is not None, 1.0)
    rig.ud.turn_event.push(5, time.monotonic())             # a 2nd turn drains the slot
    assert wait_until(lambda: rig.count("ALERT waits") >= 1, 4.0), "alert lost to the head-turn drain"
    # today's behaviour for others: a detection and a mode announcement in the slot ARE drained
    rig2 = mk().start()
    rig2.ud.turn_event.push(5, time.monotonic())
    time.sleep(0.15)
    rig2.mailbox.offer(det("DET drained"))
    rig2.ud.turn_event.push(5, time.monotonic())
    time.sleep(0.2)
    rig2.mailbox.offer({"announce": "MODE drained"})
    rig2.ud.turn_event.push(5, time.monotonic())
    time.sleep(2.0)
    assert rig2.count("DET drained") == 0 and rig2.count("MODE drained") == 0


def test_req_con_03_alert_replaced_in_the_slot_by_a_tie_is_spoken_after_the_replacement(mk):
    rig = mk(prefix_dur={"MODE": 0.3}).start()
    rig.ud.turn_event.push(5, time.monotonic())
    time.sleep(0.15)
    rig.alerts.raise_alert("c", "ALERT x", interval=30.0)
    assert wait_until(lambda: rig.mailbox.peek() is not None, 1.0)
    rig.mailbox.offer({"announce": "MODE depth mode"})
    assert wait_until(lambda: rig.count("ALERT x") >= 1, 5.0), "replaced alert never spoken"
    assert [u["text"] for u in rig.utts()] == ["MODE depth mode", "ALERT x"]


def test_req_con_03_speech_switch_off_does_not_silence_the_alert_d41(mk):
    """D41 (F1, 2026-10-18): with the speech switch off the alert is still spoken, once per repeat (no
    storm), at the 30 % volume floor; switching speech on changes nothing for it. Until F1 this test
    checked the opposite (dropped while off), the behaviour T-00a kept while D-15 was open."""
    clk = WarpClock()
    rig = mk(clock=clk).start()
    rig.config.update(tts_enabled=False, tts_volume=0.0)
    rig.alerts.raise_alert("c", "ALERT x", interval=10.0)
    assert wait_until(lambda: rig.count("ALERT x") == 1, 2.0), "not spoken with the speech switch off"
    time.sleep(1.0)
    assert rig.count("ALERT x") == 1 and len(alert_take_times(rig)) == 1, "re-offered in a storm"
    assert rig.utts("ALERT x")[0]["amp"] == str(tw._espeak_amplitude(0.30)), "not at the 30 % floor"
    rig.config.update(tts_enabled=True)
    time.sleep(1.0)
    assert rig.count("ALERT x") == 1, "switching speech on re-offered the alert"
    clk.warp(11.0)
    assert wait_until(lambda: rig.count("ALERT x") == 2, 3.0)
    assert rig.config.get("tts_enabled") is True and rig.config.get("tts_volume") == 0.0


def test_req_con_03_tts_worker_error_while_the_alert_is_in_flight_does_not_wedge_or_storm(mk, monkeypatch):
    clk = WarpClock()
    rig = mk(clock=clk).start()
    real = tw._start_espeak
    calls = {"n": 0}

    def flaky(text, amplitude=100):
        calls["n"] += 1
        if calls["n"] == 1:
            raise OSError("espeak exploded")
        return real(text, amplitude)

    monkeypatch.setattr(tw, "_start_espeak", flaky)
    rig.alerts.raise_alert("c", "ALERT x", interval=10.0)
    time.sleep(1.5)
    assert len(alert_take_times(rig)) == 1 and rig.count("ALERT x") == 0, "storm or speech after the error"
    clk.warp(11.0)                                          # next repeat tries again as normal
    assert wait_until(lambda: rig.count("ALERT x") == 1, 3.0)
    rig.mailbox.offer({"announce": "MODE still works"})
    assert wait_until(lambda: rig.count("MODE still works") == 1, 3.0), "scheduler wedged the speech worker"


# =====================================================================================================
# Criterion 6/5: the loss paths, randomised, with the REAL worker (>= 20 runs per path)
# =====================================================================================================
def run_loss_path(mk, path, seed, real_seconds=6.0, k=8.0):
    """One randomised run with the REAL tts_worker + stub. The loss path is forced ONCE per alert item (not
    continuously), so the alerts still get a fair chance to be delivered."""
    rng = random.Random(seed)
    rig = mk(clock=ScaledClock(k), default_dur=0.25, prefix_dur={"MODE": 0.3, "DET": 0.3}).start()
    stop = threading.Event()
    specs = {"ALERT f10": 10.0, "ALERT g30": 30.0}
    t_raise = time.monotonic()
    rig.alerts.raise_alert("f", "ALERT f10", interval=10.0)
    rig.alerts.raise_alert("g", "ALERT g30", interval=30.0)
    prod, plog = _producers(rig, rng, stop, label="MODE")
    mutes, forced, toggles = [], {"n": 0}, []
    MUTE_SPAN = 1.0 + 0.5 + 0.4        # 1 s mute + a possible 0.5 s pace and a 0.4 s utterance before it is consumed

    def alert_in_slot():
        it = rig.mailbox.peek()
        return it if (it is not None and "announce" in it and it["announce"].startswith("ALERT")) else None

    def injector():
        n = 0
        last_take_seen = 0
        seen_ids = set()
        off_until = 0.0
        while not stop.is_set():
            time.sleep(0.003)
            now = time.monotonic()
            if path == "cut":
                tk = alert_take_times(rig)
                if len(tk) > last_take_seen:
                    last_take_seen = len(tk)
                    if rng.random() < 0.4:                      # cut the utterance that was just taken
                        time.sleep(rng.uniform(0.03, 0.12))
                        n += 1
                        rig.mailbox.offer({"announce": f"MODEX {n}"})
                        forced["n"] += 1
            elif path in ("drain", "tie"):
                if rng.random() < 0.0005:                       # a head turn now and then, as a user does
                    rig.ud.turn_event.push(5, now)
                    mutes.append((now, now + MUTE_SPAN))
                it = alert_in_slot()
                if it is not None and id(it) not in seen_ids:
                    seen_ids.add(id(it))
                    if rng.random() < 0.5:                      # force the loss path ONCE for this item
                        if path == "drain":
                            rig.ud.turn_event.push(5, time.monotonic())
                            mutes.append((time.monotonic(), time.monotonic() + MUTE_SPAN))
                        else:
                            n += 1
                            rig.mailbox.offer({"announce": f"MODEX {n}"})
                        forced["n"] += 1
            elif path == "off":
                if now > off_until and rng.random() < 0.004:
                    off_until = now + rng.uniform(0.3, 0.8)
                    rig.config.update(tts_enabled=False)
                    toggles.append(time.monotonic())
                    forced["n"] += 1
                elif off_until and now >= off_until:
                    rig.config.update(tts_enabled=True)
                    toggles.append(time.monotonic())
                    off_until = 0.0
        rig.config.update(tts_enabled=True)

    inj = threading.Thread(target=injector, daemon=True)
    inj.start()
    time.sleep(real_seconds)
    stop.set()
    inj.join(2)
    prod.join(2)
    t_end = time.monotonic()
    rig.stop()
    extra, probs_taken = {}, []
    # An alert item taken from the mailbox while the speech switch is ON must be followed by a stub utterance of
    # the same text within 0.8 s (stub start-up included): otherwise it was discarded (head-turn drain, a lost
    # item). Taken while the switch is OFF: dropped on purpose, counts as delivered (brief criterion 6).
    starts = [[u["text"], u["start"], False] for u in rig.utts()]
    for tt, it, en in alert_take_times(rig):
        hit = next((x for x in starts if x[0] == it["announce"] and not x[2] and -0.05 <= x[1] - tt < 0.8), None)
        if hit is not None:
            hit[2] = True
        elif not en:
            extra.setdefault(it["announce"], []).append(tt + 0.08)
        elif t_end - tt > 1.0 and not any(abs(tt - x) < 0.05 for x in toggles):
            probs_taken.append(f"{it['announce']} taken at {tt - t_raise:.2f} s with speech on and never spoken")
    probs = probs_taken + check_alert_invariants(rig, specs, t_end, mute_windows=mutes, k=k, slack=0.6,
                                                 deliveries_extra=extra, t_raise=t_raise)
    cuts = sum(1 for u in rig.utts(startswith="ALERT") if u["outcome"] == "cut")
    drops = sum(len(v) for v in extra.values())
    return probs, {"cuts": cuts, "forced": forced["n"], "dropped": drops, "mutes": len(mutes),
                   "ticks": len(rig.utts(startswith="ALERT"))}


@pytest.mark.timeout(900)
@pytest.mark.parametrize("path", ["cut", "drain", "tie", "off"])
@long_run
def test_req_con_03_loss_path_randomised_runs_with_the_real_worker(mk, path):
    """>= 20 randomised runs per loss path with the real tts_worker and the stub: no alert lost, doubled or cut
    by another alert. The path is forced on purpose by an injector thread."""
    runs = int(os.environ.get("T00A_RUNS", "20"))
    total = {"cuts": 0, "forced": 0, "dropped": 0, "mutes": 0, "ticks": 0}
    failures = []
    for seed in range(runs):
        probs, stats = run_loss_path(mk, path, 1000 * (1 + ["cut", "drain", "tie", "off"].index(path)) + seed)
        for key in total:
            total[key] += stats[key]
        if probs:
            failures.append((seed, probs))
    print(f"[tester] loss path '{path}': {runs} runs, {total}")
    assert not failures, failures[:3]
    if path == "cut":
        assert total["cuts"] >= runs, "the cut path was not really exercised"
    if path == "off":
        # D41 (F1): with the speech switch off an alert is spoken, never dropped. Until F1 this
        # asserted the opposite (dropped >= runs // 2) to prove the drop path was exercised.
        assert total["dropped"] == 0, "an alert was dropped with the speech switch off"
    assert total["forced"] >= runs, "the loss path was not forced"


# =====================================================================================================
# Criterion 7: raise again
# =====================================================================================================
def test_req_con_03_raise_again_same_text_and_interval_changes_nothing(mk):
    clk = WarpClock()
    rig = mk(clock=clk).start()
    for _ in range(300):
        rig.alerts.raise_alert("c", "ALERT x", interval=30.0)
        time.sleep(0.005)
    time.sleep(1.0)
    assert rig.count("ALERT x") == 1, "re-raising the same alert every loop produced extra utterances"
    assert len(offered_alert_items(rig)) == 1
    assert rig.alerts.raised() == ["c"]


def test_req_con_03_raise_again_with_a_different_text_or_interval_replaces_it_without_a_second_copy(mk):
    clk = WarpClock()
    rig = mk(clock=clk).start()
    rig.alerts.raise_alert("c", "ALERT old", interval=30.0)
    assert wait_until(lambda: rig.count("ALERT old") == 1, 2.0)
    rig.alerts.raise_alert("c", "ALERT new", interval=30.0)
    assert wait_until(lambda: rig.count("ALERT new") == 1, 2.0), "replacement not spoken at once"
    clk.warp(31.0)
    assert wait_until(lambda: rig.count("ALERT new") == 2, 3.0)
    time.sleep(0.5)
    assert rig.count("ALERT old") == 1, "the old alert was spoken again"
    assert rig.alerts.raised() == ["c"]
    rig.alerts.raise_alert("c", "ALERT new", interval=10.0)  # interval change
    assert wait_until(lambda: rig.count("ALERT new") == 3, 2.0)


def test_req_con_03_replace_while_the_old_one_is_queued_pulls_the_old_item(mk):
    rig = mk().start()
    rig.ud.turn_event.push(5, time.monotonic())
    time.sleep(0.15)
    rig.alerts.raise_alert("c", "ALERT old", interval=30.0)
    assert wait_until(lambda: rig.mailbox.peek() is not None, 1.0)
    rig.alerts.raise_alert("c", "ALERT new", interval=30.0)
    assert wait_until(lambda: rig.count("ALERT new") >= 1, 4.0)
    assert rig.count("ALERT old") == 0, "the replaced alert's old item was spoken"


# =====================================================================================================
# Criterion 8: threading, API safety, shutdown
# =====================================================================================================
@pytest.mark.timeout(120)
def test_req_dta_08_api_from_many_threads_never_blocks_and_ends_consistent(mk):
    """12 threads x 2000 mixed raise/clear/set_repeat/is_raised/raised calls while the real workers run and the
    stub is speaking: no exception, the slowest call is short, no leaked item, nothing is spoken after the last
    clear."""
    rig = mk(default_dur=0.2).start()
    errs, slow = [], []
    names = [f"n{i}" for i in range(6)]

    def hammer(seed):
        rng = random.Random(seed)
        for _ in range(2000):
            op = rng.random()
            nm = rng.choice(names)
            t = time.monotonic()
            try:
                if op < 0.4:
                    rig.alerts.raise_alert(nm, f"ALERT {nm} {rng.randint(0, 2)}", interval=rng.choice([None, 0.5, 5.0]),
                                           repeat=rng.choice([True, False, (lambda: True)]))
                elif op < 0.65:
                    rig.alerts.clear(nm)
                elif op < 0.8:
                    rig.alerts.set_repeat(nm, rng.random() < 0.5)
                elif op < 0.9:
                    rig.alerts.is_raised(nm)
                else:
                    rig.alerts.raised()
            except Exception as e:  # noqa: BLE001
                errs.append(repr(e))
            slow.append(time.monotonic() - t)
            if rng.random() < 0.05:
                time.sleep(0.001)

    ths = [threading.Thread(target=hammer, args=(i,), daemon=True) for i in range(12)]
    t0 = time.monotonic()
    for t in ths:
        t.start()
    for t in ths:
        t.join(60)
    assert not any(t.is_alive() for t in ths), "API call hung"
    print(f"[tester] 24000 calls in {time.monotonic() - t0:.1f} s, slowest call {max(slow) * 1000:.1f} ms, "
          f"p99 {sorted(slow)[int(len(slow) * 0.99)] * 1000:.2f} ms")
    assert not errs, errs[:3]
    assert max(slow) < 0.5, f"an API call took {max(slow):.3f} s"
    for nm in names:
        rig.alerts.clear(nm)
    time.sleep(1.2)
    n_before = len(rig.utts())
    time.sleep(1.5)
    assert len(rig.utts()) == n_before, "spoken after every alert was cleared"
    assert rig.mailbox.peek() is None, "a scheduler item leaked in the slot after every alert was cleared"
    assert rig.alerts.raised() == []
    # still usable after the storm
    rig.alerts.raise_alert("after", "ALERT after")
    assert wait_until(lambda: rig.count("ALERT after") == 1, 3.0)


def test_req_dta_08_raise_clear_storm_one_name_ends_in_the_last_state(mk):
    rig = mk(default_dur=0.15).start()
    for i in range(500):
        rig.alerts.raise_alert("c", "ALERT storm", interval=0.5)
        if i % 3 == 0:
            rig.alerts.clear("c")
        time.sleep(0.003)
    rig.alerts.raise_alert("c", "ALERT final", interval=5.0)
    assert wait_until(lambda: rig.count("ALERT final") == 1, 3.0)
    rig.alerts.clear("c")
    time.sleep(1.0)
    n = len(rig.utts())
    time.sleep(1.2)
    assert len(rig.utts()) == n and rig.mailbox.peek() is None
    allu = rig.utts()
    assert all(a["end"] <= b["start"] + 0.03 for a, b in zip(allu, allu[1:]))


def test_req_dta_08_the_alert_worker_checks_shutdown_within_0_25_s_when_idle_and_with_alerts_raised(mk):
    worst = 0.0
    for raised in (False, True):
        for _ in range(5):
            rig = mk(default_dur=0.2).start()
            if raised:
                rig.alerts.raise_alert("a", "ALERT a", interval=10.0)
                rig.alerts.raise_alert("b", "ALERT b", interval=30.0)
                time.sleep(0.1)
            t = time.monotonic()
            rig.ud.shutdown_event.set()
            rig.threads["alert"].join(2.0)
            worst = max(worst, time.monotonic() - t)
            assert not rig.threads["alert"].is_alive()
            rig.stop()
    print(f"[tester] alert worker stop time, worst of 10: {worst * 1000:.0f} ms")
    assert worst <= 0.25
    assert rig.threads["alert"].daemon


def test_req_dta_08_stop_while_an_alert_is_being_spoken_ends_both_workers_and_the_process_quickly(mk):
    rig = mk(default_dur=5.0).start()
    rig.alerts.raise_alert("a", "ALERT long", interval=30.0)
    assert wait_until(lambda: rig.utts("ALERT long"), 2.0)
    elapsed = rig.stop()
    print(f"[tester] stop while speaking: {elapsed * 1000:.0f} ms")
    assert rig.alive() == []
    assert elapsed <= 1.0
    time.sleep(0.3)
    assert rig.orphans() == [], "stub espeak process still running after shutdown"


def test_req_dta_08_raise_from_inside_a_repeat_callback_and_a_gst_style_thread_does_not_deadlock(mk):
    clk = WarpClock()
    rig = mk(clock=clk).start()
    seen = {"n": 0}

    def cb():
        seen["n"] += 1
        rig.alerts.raise_alert("child", "ALERT child")     # re-entrancy: the callable calls the API
        rig.alerts.set_repeat("child", True)
        rig.alerts.is_raised("parent")
        rig.alerts.raised()
        if seen["n"] == 3:
            rig.alerts.clear("child")
        return True

    rig.alerts.raise_alert("parent", "ALERT parent", interval=30.0, repeat=cb)
    assert wait_until(lambda: rig.count("ALERT parent") == 1 and seen["n"] >= 3, 3.0), "callback re-entrancy hung"
    assert rig.threads["alert"].is_alive()
    # a GStreamer-callback style caller while the worker is speaking
    rig.set_dur(2.0)
    rig.alerts.raise_alert("late", "ALERT late")
    t = time.monotonic()
    done = []
    th = threading.Thread(target=lambda: (rig.alerts.raise_alert("cb", "ALERT cb"), done.append(time.monotonic() - t)))
    th.start()
    th.join(1.0)
    assert done and done[0] < 0.05, "raise_alert blocked the caller"


def test_req_dta_08_a_repeat_function_that_raises_keeps_the_last_value_and_does_not_hurt_other_alerts(mk):
    clk = WarpClock()
    rig = mk(clock=clk).start()
    state = {"n": 0}

    def flaky():
        state["n"] += 1
        if state["n"] > 2:
            raise RuntimeError("boom")
        return True

    rig.alerts.raise_alert("bad", "ALERT bad", interval=30.0, repeat=flaky)
    rig.alerts.raise_alert("good", "ALERT good", interval=30.0)
    assert wait_until(lambda: rig.count("ALERT bad") == 1 and rig.count("ALERT good") == 1, 3.0)
    clk.warp(31.0)
    assert wait_until(lambda: rig.count("ALERT good") == 2 and rig.count("ALERT bad") == 2, 3.0), \
        "a repeat function that raises stopped the repeat or the other alert"
    assert rig.threads["alert"].is_alive()


def test_req_dta_08_a_blocking_repeat_function_stalls_all_alerts_and_the_stop(mk):
    """LIMIT, not a requirement of the brief: one blocking repeat callable blocks the one scheduler thread. Records
    how long the other alert and the stop are delayed (reported by the Tester)."""
    rig = mk().start()
    gate = threading.Event()
    rig.alerts.raise_alert("slow", "ALERT slow", interval=30.0, repeat=lambda: (gate.wait(1.5), True)[1])
    time.sleep(0.2)
    t = time.monotonic()
    rig.alerts.raise_alert("other", "ALERT other")
    assert wait_until(lambda: rig.count("ALERT other") == 1, 6.0)
    delay = rig.utts("ALERT other")[0]["start"] - t
    t = time.monotonic()
    rig.ud.shutdown_event.set()
    rig.threads["alert"].join(5.0)
    stop = time.monotonic() - t
    gate.set()
    print(f"[tester] with a repeat callable that blocks 1.5 s: other alert delayed {delay:.2f} s, alert worker stop {stop:.2f} s")
    assert not rig.threads["alert"].is_alive()


def test_req_con_07_a_tiny_interval_is_accepted_and_monopolises_the_channel(mk):
    """LIMIT, recorded: interval=0.01 is accepted (no minimum), the alert then speaks back to back and a waiting
    detection phrase is replaced by it again and again. A caller bug, not a requirement; reported."""
    rig = mk(default_dur=0.15, prefix_dur={"DET": 0.15}).start()
    rig.alerts.raise_alert("c", "ALERT tiny", interval=0.01)
    for i in range(10):
        time.sleep(0.4)
        rig.mailbox.offer(det(f"DET {i}"))
    time.sleep(0.5)
    n_alert, n_det = rig.count("ALERT tiny"), len(rig.utts(startswith="DET"))
    rig.alerts.clear("c")
    print(f"[tester] interval=0.01 s over 4.5 s: {n_alert} alert utterances, {n_det} detection phrases spoken of 10 offered")
    allu = rig.utts()
    assert all(a["end"] <= b["start"] + 0.03 for a, b in zip(allu, allu[1:])), "overlap"


@pytest.mark.parametrize("bad", [0, -1, float("nan"), float("inf"), "30", -0.5])
def test_req_con_03_invalid_intervals_are_refused_with_value_error(bad):
    a = AlertScheduler(PriorityMailbox())
    with pytest.raises(ValueError):
        a.raise_alert("x", "text", interval=bad)
    assert a.raised() == []


# =====================================================================================================
# Criterion 9: nothing else changes (real worker, scheduler present and idle)
# =====================================================================================================
def test_t00a_criterion_9_detection_phrase_mode_announcement_volume_switch_mute_unchanged_with_scheduler_idle(mk):
    """Brief criterion 9 (no workbook case): a detection phrase, an urgent cut, a mode announcement, the volume
    knob (espeak -a), the speech switch and the head-turn mute behave as before with the scheduler running idle."""
    rig = mk(default_dur=1.0, prefix_dur={"MODE": 0.3, "DET": 1.0}).start()
    rig.mailbox.offer(det("DET one"))                       # normal detection, spoken to the end
    assert wait_until(lambda: rig.utts("DET one"), 2.0)
    time.sleep(0.2)
    rig.mailbox.offer({"announce": "MODE depth mode"})      # announcement cuts the detection phrase
    assert wait_until(lambda: len(rig.utts()) >= 2 and rig.utts()[-1]["outcome"] == "end", 4.0)
    assert [(u["text"], u["outcome"]) for u in rig.utts()] == [("DET one", "cut"), ("MODE depth mode", "end")]
    rig.config.update(tts_volume=0.0)                       # volume knob: espeak -a 10 (floor)
    rig.mailbox.offer(det("DET quiet"))
    assert wait_until(lambda: rig.utts("DET quiet"), 3.0)
    assert rig.utts("DET quiet")[0]["amp"] == "10", "volume knob at 0 must give espeak -a 10"
    assert rig.utts("DET one")[0]["amp"] == "100"
    rig.config.update(tts_enabled=False)                    # speech switch: dropped
    n = len(rig.utts())
    time.sleep(1.5)
    rig.mailbox.offer(det("DET off"))
    time.sleep(1.0)
    assert len(rig.utts()) == n
    rig.config.update(tts_enabled=True)
    rig.ud.turn_event.push(5, time.monotonic())             # head-turn mute drains the slot
    time.sleep(0.1)
    rig.mailbox.offer(det("DET muted"))
    rig.ud.turn_event.push(5, time.monotonic())
    time.sleep(1.5)
    assert rig.count("DET muted") == 0
    assert rig.alerts.raised() == [] and rig.mailbox.peek() is None


# =====================================================================================================
# Criterion 10: the documented usage runs as written
# =====================================================================================================
def test_req_con_03_the_module_docstring_usage_block_runs_as_written():
    import second_vision.core.alerts as alerts_mod
    doc = alerts_mod.__doc__
    block = doc.split("Usage", 1)[1].split("::", 1)[1].split("What it does", 1)[0]
    code = "\n".join(line[4:] if line.startswith("    ") else line for line in block.splitlines())
    ud = SimpleNamespace(alerts=AlertScheduler(PriorityMailbox()))
    ns = {"user_data": ud, "config": SystemConfig()}
    exec(compile(code, "<docstring>", "exec"), ns)          # noqa: S102
    assert ud.alerts.raised() == ["battery-low", "camera-lost"], ud.alerts.raised()
    assert "alerts.set_repeat" in doc and "alerts.clear" in doc


# =====================================================================================================
# Worst-case delay "raised -> spoken" under continuous detection speech (Amendment A, point 4)
# =====================================================================================================
def measure_delays(mk, period, tier, n=8, det_dur=1.2, seed=5, give_up=20.0):
    rig = mk(default_dur=0.3, prefix_dur={"DET": det_dur}).start()
    rng = random.Random(seed)
    stop = threading.Event()

    def stream():
        i = 0
        while not stop.is_set():
            i += 1
            rig.mailbox.offer(det(f"DET {i}", tier=tier, priority=1.0))
            stop.wait(period)

    th = threading.Thread(target=stream, daemon=True)
    th.start()
    time.sleep(1.0)
    delays = []
    for i in range(n):
        time.sleep(rng.uniform(0.2, 2.5))
        t = time.monotonic()
        rig.alerts.raise_alert(f"d{i}", f"ALERT d{i}")
        ok = wait_until(lambda: any(u["outcome"] == "end" for u in rig.utts(f"ALERT d{i}")), give_up, step=0.02)
        delays.append(([u for u in rig.utts(f"ALERT d{i}") if u["outcome"] == "end"][0]["start"] - t)
                      if ok else float("inf"))
        rig.alerts.clear(f"d{i}")
    stop.set()
    th.join(2)
    return delays


def _report(label, delays):
    fin = sorted(d for d in delays if math.isfinite(d))
    med = f"{fin[len(fin) // 2]:.2f} s" if fin else "none"
    mx = f"{max(delays):.2f} s" if fin and len(fin) == len(delays) else "never (not heard to the end within the wait)"
    print(f"[tester] delay raised->spoken, {label}: n={len(delays)} median={med} max={mx} "
          f"not heard={sum(1 for d in delays if not math.isfinite(d))}")


FINDING_URGENT = ("FINDING F-1 (Tester, T-00a): with a sustained stream of URGENT-tier detections the alert is never "
                  "heard to the end: the scheduler waits for the worker to be idle, and an alert it does get in is cut "
                  "again by the next urgent detection (priority.preempts(): any urgent item preempts anything). "
                  "strict xfail: when it is fixed this test turns into a failure, remove the marker then.")


@pytest.mark.timeout(240)
@long_run
def test_req_con_03_worst_case_delay_under_continuous_normal_detection_speech(mk):
    """Amendment A item 4: normal-tier detection phrases (1.2 s) offered every 0.1 s for the whole test; 8 alerts
    raised at random moments. Raised -> heard to the end must be <= 3 s (the wait for the phrase in progress)."""
    d = measure_delays(mk, period=0.1, tier="normal")
    _report("continuous normal-tier detections every 0.1 s (phrase 1.2 s)", d)
    assert max(d) <= 3.0, d


@pytest.mark.timeout(240)
@pytest.mark.xfail(strict=True, reason=FINDING_URGENT)
@long_run
def test_req_con_03_worst_case_delay_under_urgent_detections_that_leave_gaps(mk):
    """Urgent detections every 1.5 s, phrase 1.2 s (a 0.3 s gap between phrases): the alert (0.3 s stub utterance)
    is offered in the gap and cut by the next urgent detection, every time."""
    d = measure_delays(mk, period=1.5, tier="urgent", n=3, give_up=8.0)
    _report("urgent detections every 1.5 s (phrase 1.2 s: finishes before the next)", d)
    assert max(d) <= 3.0, d


@pytest.mark.timeout(240)
@pytest.mark.xfail(strict=True, reason=FINDING_URGENT)
@pytest.mark.parametrize("period", [0.8, 0.3, 0.1])
@long_run
def test_req_con_03_delay_under_urgent_detections_that_cut_each_other(mk, period):
    """Urgent detections arriving faster than a phrase lasts cut each other in the speech worker (today's
    preemption). The worker is then never idle and the alert is never offered (0.1 s = one offer per frame, which is
    what the detection callback does for an urgent object that stays in view)."""
    d = measure_delays(mk, period=period, tier="urgent", n=3, give_up=8.0)
    _report(f"urgent detections every {period} s (phrase 1.2 s: each cuts the previous)", d)
    assert max(d) <= 3.0, d


# =====================================================================================================
# The real program path: main.py --mock (criterion 8, REQ-DTA-08)
# =====================================================================================================
RUNNER = '''
import json, runpy, sys, threading, time
sys.path.insert(0, {src!r})
rec, inst = [], []
_init = threading.Thread.__init__
def init(self, *a, **k):
    _init(self, *a, **k)
    rec.append((self.name, self.daemon))
threading.Thread.__init__ = init
import second_vision.core.alerts as A
_ai = A.AlertScheduler.__init__
def ainit(self, *a, **k):
    _ai(self, *a, **k)
    inst.append(self)
A.AlertScheduler.__init__ = ainit
def raiser():
    while not inst:
        time.sleep(0.05)
    inst[0].raise_alert("runner", "ALERT from main path", interval=3.0)
threading.Thread(target=raiser, daemon=True).start()
sys.argv = [{main!r}, "--mock"]
try:
    runpy.run_path({main!r}, run_name="__main__")
finally:
    json.dump(rec, open({out!r}, "w"))
'''


def _run_main(tmp, src_dir, py, with_runner, ctrl_c_after=7.5):
    """Start main.py --mock with the stub espeak first on PATH, Ctrl+C after a while; return stop seconds."""
    d = tmp
    d.mkdir(exist_ok=True)
    bindir = d / "bin"
    bindir.mkdir(exist_ok=True)
    log = d / "stub.log"
    log.write_text("")
    (d / "dur.json").write_text(json.dumps({"default": 0.3, "prefix": {}}))
    stub = bindir / "espeak-ng"
    stub.write_text(STUB.format(py=sys.executable))
    stub.chmod(0o755)
    env = dict(os.environ, PATH=str(bindir) + os.pathsep + BASE_PATH, STUB_LOG=str(log),
               STUB_DUR_FILE=str(d / "dur.json"), PYTHONUNBUFFERED="1")
    env.pop("DISPLAY", None)
    main_py = src_dir / "second_vision" / "main.py"
    out = d / "threads.json"
    if with_runner:
        script = d / "runner.py"
        script.write_text(RUNNER.format(src=str(src_dir), main=str(main_py), out=str(out)))
        cmd = [py, str(script)]
    else:
        cmd = [py, str(main_py), "--mock"]
    p = subprocess.Popen(cmd, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, cwd=d)
    time.sleep(ctrl_c_after)
    t = time.monotonic()
    p.send_signal(signal.SIGINT)
    try:
        output, _ = p.communicate(timeout=15)
    except subprocess.TimeoutExpired:
        p.kill()
        output, _ = p.communicate()
        return None, -9, output, log, out
    return time.monotonic() - t, p.returncode, output, log, out


@pytest.mark.timeout(120)
def test_req_dta_08_main_mock_starts_the_alert_worker_speaks_an_alert_and_stops_with_exit_0(tmp_path):
    stop, rc, output, log, out = _run_main(tmp_path / "new", SRC, sys.executable, with_runner=True, ctrl_c_after=9.0)
    threads = dict(json.loads(out.read_text()))
    texts = [json.loads(l) for l in log.read_text().splitlines() if l.strip()]
    starts = [e["t"] for e in texts if e["ev"] == "start" and e["text"] == "ALERT from main path"]
    print(f"[tester] main.py --mock: exit {rc}, stop {stop:.2f} s, alert spoken {len(starts)}x, "
          f"alert thread daemon={threads.get('alert-scheduler')}")
    assert rc == 0, output[-500:]
    assert "[MAIN] Alert worker started" in output and "[MAIN] Done." in output
    assert threads.get("alert-scheduler") is True, "alert-scheduler thread missing or not a daemon"
    assert len(starts) >= 2, "the alert raised in the real program was not spoken (twice)"
    assert all(abs((b - a) - 3.0) <= 1.0 for a, b in zip(starts, starts[1:])), starts


@pytest.mark.timeout(600)
@pytest.mark.skipif(not BASELINE_SRC.exists(), reason="the T-00a baseline copy is not present")
@long_run
def test_req_dta_08_the_alert_worker_adds_nothing_to_the_stop_time_of_main_mock(tmp_path):
    """Ctrl+C at six phases of the mock generator's 2 s sleep, the baseline and the new tree at each phase: the
    stop time depends on the phase (mock/ is out of scope), so compare PAIRS. The alert worker must add nothing."""
    new, base = [], []
    phases = [2.3, 2.7, 3.1, 3.5, 3.9, 4.3]
    for i, ph in enumerate(phases):
        s, rc, *_ = _run_main(tmp_path / f"n{i}", SRC, sys.executable, with_runner=False, ctrl_c_after=ph)
        assert rc == 0
        new.append(s)
        s, rc, *_ = _run_main(tmp_path / f"b{i}", BASELINE_SRC, sys.executable, with_runner=False, ctrl_c_after=ph)
        assert rc == 0
        base.append(s)
    print(f"[tester] Ctrl+C at {phases} s after the start; stop seconds, baseline: {[round(x, 2) for x in base]}")
    print(f"[tester] stop seconds, with the alert worker (same phases):          {[round(x, 2) for x in new]}")
    print(f"[tester] difference new - baseline per phase: {[round(n - b, 2) for n, b in zip(new, base)]}")
    assert all(abs(n - b) <= 0.5 for n, b in zip(new, base)), (base, new)
    assert sum(new) / len(new) <= sum(base) / len(base) + 0.2
