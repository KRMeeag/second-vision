"""
T-03c1: the checks the device tests make on the speech log (tests/test_camera_alerts_device.py), tested
on their own, with made-up logs and no hardware.

Why they exist. On the device two things happen that the test does not control: an urgent object
warning ("person center") may cut a camera sentence that is being said (the alert scheduler's rule, core/alerts.py
"Not lost"; D-16 will change it later), and the camera may drop on its own with no unplug in progress. The
checks therefore read the program's own record of every loss and return, and allow for other speech. That
leniency must not hide a real fault, so each way the feature can go wrong is made up here and must still fail:
a late first sentence, a repeat that restarts its cadence, a reconnect said twice, said before the return is
confirmed, said late for no reason, the missing sentence going on after it was cleared, a camera sentence cut
by something that is not another announcement.
"""

import sys
from pathlib import Path

import pytest

TESTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(TESTS_DIR.parent / "src"))
sys.path.insert(0, str(TESTS_DIR))

from test_camera_alerts_device import (  # noqa: E402
    BACK, MISSING, build_episodes, busy_seconds, check_episode, collapse,
)

OTHER = "person center"


def utt(text, start, seconds=None, cut=False, pid=None):
    seconds = {MISSING: 2.75, BACK: 2.02}.get(text, 1.0) if seconds is None else seconds
    return {"text": text, "start": start, "end": start + seconds, "cut": cut,
            "pid": pid if pid is not None else int(start * 1000) + 1}


def ideal(loss=100.0):
    """One loss at `loss`, said at once and every 10 s, back at +22 s, cleared 1 s later, 'reconnected' at once."""
    ep = {"loss": loss, "back": loss + 22.0, "cleared": loss + 23.0, "startup": False}
    utts = [utt(MISSING, loss + 0.05), utt(MISSING, loss + 10.05), utt(MISSING, loss + 20.05),
            utt(BACK, loss + 23.05)]
    return ep, utts


def check(utts, ep, until=200.0, min_repeats=1):
    return check_episode(utts, ep, until, "t", min_repeats)


# ---- build_episodes ----------------------------------------------------------------------------
def test_an_episode_runs_from_the_loss_to_the_return_that_held_and_takes_the_clear():
    events = [(10.0, "lost"), (30.0, "back"), (31.0, "cleared"), (60.0, "lost"), (70.0, "back"), (71.0, "cleared")]
    assert build_episodes(events) == [
        {"loss": 10.0, "back": 30.0, "cleared": 31.0, "startup": False},
        {"loss": 60.0, "back": 70.0, "cleared": 71.0, "startup": False}]


def test_a_start_up_without_a_camera_is_an_episode_from_the_wait():
    episodes = build_episodes([(1.0, "wait"), (50.0, "back"), (51.0, "cleared")])
    assert episodes == [{"loss": 1.0, "back": 50.0, "cleared": 51.0, "startup": True}]


def test_starts_that_fail_at_once_are_part_of_the_same_episode():
    """The camera held by another process: back, then lost 3 ms later, again and again; the last back held."""
    events = [(10.0, "lost"), (16.0, "back"), (16.003, "lost"), (22.0, "back"), (22.4, "lost"),
              (28.0, "back"), (29.0, "cleared")]
    assert build_episodes(events) == [{"loss": 10.0, "back": 28.0, "cleared": 29.0, "startup": False}]


def test_a_camera_that_drops_again_after_the_return_held_is_a_second_episode():
    """Seen on the device: the camera went 2.7 s after a return with no unplug in progress."""
    events = [(10.0, "lost"), (20.0, "back"), (21.0, "cleared"), (22.7, "lost"), (27.0, "back"), (28.0, "cleared")]
    episodes = build_episodes(events)
    assert len(episodes) == 2 and episodes[0]["cleared"] == 21.0 and episodes[1]["loss"] == 22.7


def test_an_episode_still_open_has_no_return():
    assert build_episodes([(10.0, "lost")]) == [{"loss": 10.0, "back": None, "cleared": None, "startup": False}]


# ---- busy_seconds ------------------------------------------------------------------------------
def test_busy_seconds_counts_the_union_of_other_speech_inside_the_span():
    utts = [utt(OTHER, 1.0, 2.0), utt(OTHER, 2.0, 2.0), utt(OTHER, 10.0, 1.0)]       # 1-3 and 2-4 overlap; 10-11 outside
    assert busy_seconds(utts, 0.0, 5.0) == pytest.approx(3.0)
    assert busy_seconds(utts, 3.5, 10.5) == pytest.approx(1.0)
    assert busy_seconds(utts, 0.0, 5.0, exclude=(utts[0],)) == pytest.approx(2.0)


# ---- check_episode: a good history passes ------------------------------------------------------
def test_the_ideal_history_passes():
    ep, utts = ideal()
    result = check(utts, ep)
    assert result["repeats"] == 3 and result["cuts"] == 0 and result["gaps"] == pytest.approx([10.0, 10.0])
    assert result["delay"] == pytest.approx(1.05, abs=0.01)


def test_other_speech_at_the_loss_may_delay_the_first_sentence():
    ep, utts = ideal()
    utts = [utt(OTHER, 99.0, 2.5)] + [u for u in utts if u["text"] != MISSING or u["start"] > 105] + [utt(MISSING, 101.6)]
    utts += [utt(MISSING, 111.6), utt(MISSING, 121.6)]
    utts = [u for u in utts if not (u["text"] == MISSING and u["start"] in (110.05, 120.05))]
    check(utts, ep)


def test_a_reconnected_sentence_cut_by_an_object_warning_and_said_again_passes():
    ep, utts = ideal()
    cut = utt(BACK, 123.05, 0.15, cut=True)
    utts += [cut, utt(OTHER, 123.2, 1.5)]
    utts = [u for u in utts if not (u["text"] == BACK and not u["cut"])] + [utt(BACK, 124.8)]
    result = check(utts, ep)
    assert result["cuts"] == 1


def test_the_reconnected_sentence_waits_for_a_missing_sentence_under_way():
    ep = {"loss": 100.0, "back": 120.5, "cleared": 121.5, "startup": False}
    utts = [utt(MISSING, 100.05), utt(MISSING, 110.05), utt(MISSING, 120.05), utt(BACK, 122.85)]     # the third ends at 122.8
    check(utts, ep)


# ---- check_episode: each fault is still caught -------------------------------------------------
def test_a_first_sentence_that_is_late_for_no_reason_fails():
    ep, utts = ideal()
    utts[0] = utt(MISSING, 103.5)
    with pytest.raises(AssertionError, match="first missing sentence"):
        check(utts, ep)


def test_a_missing_sentence_never_said_fails():
    ep, utts = ideal()
    with pytest.raises(AssertionError, match="was not said"):
        check([u for u in utts if u["text"] != MISSING], ep)


def test_a_cadence_that_restarts_early_fails():
    """The mistake the confirmation window prevents: the alert withdrawn and raised again says it at once, 6.6 s after."""
    ep, utts = ideal()
    utts[1] = utt(MISSING, 106.65)
    utts[2] = utt(MISSING, 113.3)
    with pytest.raises(AssertionError, match="repeat gap"):
        check(utts, ep)


def test_a_gap_much_longer_than_ten_seconds_for_no_reason_fails():
    ep = {"loss": 100.0, "back": 120.0, "cleared": 121.0, "startup": False}
    utts = [utt(MISSING, 100.05), utt(MISSING, 119.0), utt(BACK, 121.05)]
    with pytest.raises(AssertionError, match="repeat gap"):
        check(utts, ep)


def test_too_few_repeats_for_the_time_away_fails():
    ep, utts = ideal()
    with pytest.raises(AssertionError, match="wanted"):
        check(utts[:1] + utts[3:], ep, min_repeats=1)


def test_the_missing_sentence_going_on_after_the_alert_was_cleared_fails():
    ep, utts = ideal()
    utts.append(utt(MISSING, 125.0))
    with pytest.raises(AssertionError, match="after the alert was cleared"):
        check(utts, ep)


def test_a_repeat_after_the_pipeline_ran_fails_when_nothing_is_owed():
    """The 10 s repeat falls due 0.55 s after the pipeline ran: it must not be said (the pause the app puts on it)."""
    ep = {"loss": 100.0, "back": 109.5, "cleared": 110.5, "startup": False}
    utts = [utt(MISSING, 100.05), utt(MISSING, 110.05), utt(BACK, 112.9)]
    with pytest.raises(AssertionError, match="after the pipeline ran"):
        check(utts, ep)


def test_the_same_late_sentence_passes_when_an_earlier_one_was_cut_and_is_owed():
    ep = {"loss": 100.0, "back": 109.5, "cleared": 110.5, "startup": False}
    utts = [utt(MISSING, 100.05), utt(MISSING, 106.0, 0.2, cut=True), utt(OTHER, 106.25, 1.5),
            utt(MISSING, 110.05), utt(BACK, 112.9)]
    check(utts, ep)


def test_a_reconnected_sentence_said_twice_fails():
    ep, utts = ideal()
    utts.append(utt(BACK, 130.0))
    with pytest.raises(AssertionError, match="2 reconnected sentences"):
        check(utts, ep)


def test_no_reconnected_sentence_fails():
    ep, utts = ideal()
    with pytest.raises(AssertionError, match="0 reconnected sentences"):
        check(utts[:3], ep)


def test_a_reconnected_sentence_before_the_return_is_confirmed_fails():
    ep, utts = ideal()
    utts[3] = utt(BACK, 122.2)
    with pytest.raises(AssertionError, match="before the return was confirmed"):
        check(utts, ep)


def test_a_reconnected_sentence_that_is_late_for_no_reason_fails():
    ep, utts = ideal()
    utts[3] = utt(BACK, 127.0)
    with pytest.raises(AssertionError, match="later than the confirmation"):
        check(utts, ep)


def test_a_camera_sentence_cut_by_nothing_known_fails():
    ep, utts = ideal()
    utts.insert(1, utt(MISSING, 105.0, 0.2, cut=True))
    with pytest.raises(AssertionError, match="not another announcement"):
        check(utts, ep)


def test_a_camera_sentence_cut_by_the_other_camera_sentence_fails():
    ep, utts = ideal()
    utts.insert(1, utt(MISSING, 104.0, 0.2, cut=True))
    utts.insert(2, utt(BACK, 104.25))
    with pytest.raises(AssertionError):
        check(utts, ep)


def test_an_episode_that_never_ended_or_was_not_cleared_fails():
    ep, utts = ideal()
    with pytest.raises(AssertionError, match="never came back"):
        check(utts, dict(ep, back=None))
    with pytest.raises(AssertionError, match="not cleared"):
        check(utts, dict(ep, cleared=None))


def test_collapse_merges_repeats_of_the_missing_sentence_only():
    sentences = [utt(MISSING, 1), utt(MISSING, 11), utt(BACK, 24), utt(MISSING, 50), utt(BACK, 70)]
    assert collapse(sentences) == [MISSING, BACK, MISSING, BACK]
