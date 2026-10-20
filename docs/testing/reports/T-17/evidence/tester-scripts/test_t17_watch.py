"""Tester T-17: demo_run.watch() timing semantics with my own scripted LogBook and clock (acceptance criterion 3)."""
import importlib.util, os, sys, threading, time
from pathlib import Path
import pytest

REPO = Path(os.environ.get("T17_REPO", "/home/sv-rpi5/Projects/Second-Vision/worktrees/kenth"))
spec = importlib.util.spec_from_file_location("demo_run_under_test", REPO / "docs/testing/tools/demo_run.py")
demo = importlib.util.module_from_spec(spec); sys.modules["demo_run_under_test"] = demo; spec.loader.exec_module(demo)
E = demo.Expect

def book(*pairs):
    b = demo.LogBook()
    for t, line in pairs:
        b.add(t, line)
    return b

class Clock:
    """Scripted clock: each call returns the next value, then stays at the last."""
    def __init__(self, *vals): self.vals, self.i = list(vals), 0
    def __call__(self):
        v = self.vals[min(self.i, len(self.vals) - 1)]; self.i += 1; return v

START = 1000.0
FAR = lambda: START + 1e6          # a clock that is past every deadline

# ---- delays are counted from the Enter ---------------------------------------------------------
def test_delay_is_from_the_enter():
    [r] = demo.watch(book((START + 2.5, "x")), START, 0, [E("x", 10, "x")], clock=FAR)
    assert r.ok and r.hits == [2.5]

def test_a_negative_delay_counts_for_a_line_after_the_prompt():
    [r] = demo.watch(book((START - 0.4, "x")), START, 0, [E("x", 10, "x")], clock=FAR)
    assert r.ok and r.hits == [pytest.approx(-0.4)] and r.when() == "-0.4 s"

def test_a_line_from_before_the_prompt_never_counts_whatever_its_time():
    lines = book((START - 0.1, "x"), (START - 3, "x"), (START + 1, "other"))
    [r] = demo.watch(lines, START, 2, [E("x", 10, "x")], clock=FAR)        # prompt appeared at line index 2
    assert not r.ok

def test_a_line_exactly_at_within_counts_and_one_millisecond_after_does_not():
    [a] = demo.watch(book((START + 10.0, "x")), START, 0, [E("x", 10, "x")], clock=FAR)
    [b] = demo.watch(book((START + 10.001, "x")), START, 0, [E("x", 10, "x")], clock=FAR)
    assert a.ok and not b.ok

def test_count_two_needs_two_lines_and_reports_the_second_time():
    e = lambda: E("x", 25, "x", count=2)
    assert not demo.watch(book((START + 1, "x")), START, 0, [e()], clock=FAR)[0].ok
    [r] = demo.watch(book((START + 1, "x"), (START + 11, "x")), START, 0, [e()], clock=FAR)
    assert r.ok and r.when() == "11.0 s"
    [r] = demo.watch(book((START + 1, "x"), (START + 26, "x")), START, 0, [e()], clock=FAR)   # 2nd outside window
    assert not r.ok and r.when() == "not seen"

def test_count_two_with_a_burst_of_five_is_capped_at_two_hits():
    [r] = demo.watch(book(*[(START + 1 + i * 0.001, "x") for i in range(5)]), START, 0, [E("x", 25, "x", count=2)], clock=FAR)
    assert r.ok and len(r.hits) == 2

def test_absent_passes_only_if_the_line_never_comes_in_its_window():
    e = lambda: E("bad", 15, "no bad", absent=True)
    assert demo.watch(book((START + 3, "fine")), START, 0, [e()], clock=FAR)[0].ok
    assert not demo.watch(book((START + 14.9, "bad")), START, 0, [e()], clock=FAR)[0].ok
    assert not demo.watch(book((START + 15.0, "bad")), START, 0, [e()], clock=FAR)[0].ok       # boundary: at within = seen
    assert demo.watch(book((START + 15.01, "bad")), START, 0, [e()], clock=FAR)[0].ok         # after the window: not seen
    assert not demo.watch(book((START - 0.5, "bad")), START, 0, [e()], clock=FAR)[0].ok       # before the Enter, after the prompt

def test_burst_of_many_lines_all_considered():
    lines = [(START + 0.001 * i, f"noise {i}") for i in range(5000)] + [(START + 6, "needle")]
    [r] = demo.watch(book(*lines), START, 0, [E("^needle", 10, "n")], clock=FAR)
    assert r.ok and r.hits == [6.0]

def test_a_clock_that_jumps_forward_ends_the_watch_after_reading_what_is_there():
    # nothing yet, then the clock leaps 1 h: the watch must end, not wait for real time
    t = time.monotonic()
    [r] = demo.watch(demo.LogBook(), START, 0, [E("x", 30, "x")], clock=Clock(START + 3600))
    assert not r.ok and time.monotonic() - t < 1.0

def test_a_clock_that_jumps_backward_does_not_end_a_watch_early_or_hang_forever():
    # clock goes back to before the start: now - start negative -> absent never settles by time, deadline not reached
    # The loop must still end once the (real) deadline passes; here the clock comes back forward after a few calls.
    clk = Clock(START - 50, START - 50, START - 50, START + 100)
    t = time.monotonic()
    [r] = demo.watch(demo.LogBook(), START, 0, [E("x", 30, "x")], clock=clk)
    assert not r.ok and time.monotonic() - t < 3.0

def test_a_step_that_went_well_returns_at_once_with_real_time():
    b = demo.LogBook(); t = time.monotonic()
    threading.Timer(0.3, lambda: b.add(time.monotonic(), "x")).start()
    [r] = demo.watch(b, t, 0, [E("x", 30, "x")])
    assert r.ok and time.monotonic() - t < 1.5

def test_a_missed_expectation_keeps_the_step_open_to_its_longest_window_by_design():
    b = demo.LogBook(); t0 = time.monotonic()
    threading.Timer(1.2, lambda: b.add(time.monotonic(), "late")).start()
    short, long_ = demo.watch(b, t0, 0, [E("never", 0.3, "never"), E("late", 2.0, "late")])
    assert not short.ok and long_.ok and 1.9 < time.monotonic() - t0 < 2.6     # by design: a missed expectation is never 'settled', so it runs to the longest window

def test_the_watch_never_runs_past_the_longest_window():
    t0 = time.monotonic()
    rs = demo.watch(demo.LogBook(), t0, 0, [E("a", 0.6, "a"), E("b", 1.2, "b")])
    assert time.monotonic() - t0 < 2.2 and not any(r.ok for r in rs)

def test_absent_alone_waits_exactly_its_window_then_passes():
    t0 = time.monotonic()
    [r] = demo.watch(demo.LogBook(), t0, 0, [E("bad", 1.0, "no bad", absent=True)])
    assert r.ok and 0.9 <= time.monotonic() - t0 < 2.0

def test_a_positive_expectation_met_early_while_an_absent_one_still_has_window_keeps_watching_for_the_absent_one():
    t0 = time.monotonic(); b = demo.LogBook()
    b.add(t0 + 0.05, "obj")
    threading.Timer(0.6, lambda: b.add(time.monotonic(), "ALERT")).start()
    obj, alert = demo.watch(b, t0, 0, [E("obj", 30, "obj"), E("ALERT", 1.5, "no alert", absent=True)])
    assert obj.ok and not alert.ok            # the late alert inside the absent window must be seen

def test_late_line_outside_the_window_added_before_watch_returns_is_ignored():
    t0 = time.monotonic(); b = demo.LogBook()
    threading.Timer(0.9, lambda: b.add(time.monotonic(), "x")).start()
    [r] = demo.watch(b, t0, 0, [E("x", 0.5, "x")])
    assert not r.ok

def test_expect_list_empty_returns_at_once():
    assert demo.watch(demo.LogBook(), START, 0, [], clock=FAR) == []

def test_regex_search_not_match_so_a_prefix_anchor_is_needed():
    [r] = demo.watch(book((START + 1, "  [TTS] 'x'")), START, 0, [E(r"^\[TTS\]", 5, "x")], clock=FAR)
    assert not r.ok           # a line that does not start at column 0 does not match ^ patterns (information)


# ---------------------------------------------------------------- the written sequence (criterion 1) and the step-1 pattern (criterion 2, negative)
def test_the_sequence_is_in_the_order_the_requirement_gives():
    assert [s.key for s in demo.STEPS] == ["normal", "camera", "glasses", "earphones", "panel", "lens", "chart"]

def test_every_action_says_what_to_do_and_what_the_program_should_do_and_within_how_long():
    for s in demo.STEPS:
        assert s.say and s.title and s.actions
        for a in s.actions:
            assert a.do
            for e in a.expect:
                assert e.within > 0 and e.what and e.pattern

def test_step_one_object_pattern_matches_an_object_and_none_of_the_programs_failsafe_sentences():
    sys.path.insert(0, str(REPO / "src"))
    from second_vision.core import camera_view, connections
    import re
    app = (REPO / "src/second_vision/pipeline/app.py").read_text()
    sentences = [getattr(connections, n) for n in dir(connections) if n.endswith("_TEXT")]
    sentences += [getattr(camera_view, n) for n in dir(camera_view) if n.endswith("_TEXT")]
    sentences += [m.group(1) for m in re.finditer(r'^CAMERA_\w+_TEXT = "(.*)"$', app, re.M)]
    assert len(sentences) >= 11, sentences
    pat = demo.STEPS[0].actions[0].expect[0].pattern
    for s in sentences:
        assert not re.search(pat, f"[TTS] '{s}'"), f"a failsafe sentence counts as 'an object announced': {s!r}"
    assert re.search(pat, "[TTS] 'person left'") and re.search(pat, "[TTS] 'car center'")

def test_the_chart_step_names_files_that_exist():
    act = demo.STEPS[-1].actions[0].do
    for rel in (demo.CHART, demo.MONITOR_SHOT):
        assert rel in act and (REPO / rel).exists(), rel
