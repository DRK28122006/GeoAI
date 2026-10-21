"""Unit tests for the time planner.

The planner is what turns "tomorrow evening" into concrete hours, and the
rulebook's central claim - that the answer depends on both place and hour -
only holds if this is right.
"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from app.planner import resolve

IST = ZoneInfo("Asia/Kolkata")
NOW = datetime(2026, 10, 21, 21, 30, tzinfo=IST)  # Wednesday evening


def labels(window) -> list[str]:
    return [h.strftime("%a %d %H:%M") for h in window.hours]


def test_tomorrow_evening():
    w = resolve(raw_text="running tomorrow evening near Koregaon Park", now=NOW)
    assert w.label == "tomorrow evening"
    assert w.start.date().isoformat() == "2026-10-22"
    assert w.start.hour == 17
    assert w.end.hour == 21


def test_this_weekend_covers_saturday_and_sunday():
    w = resolve(raw_text="kids cricket this weekend in Whitefield", now=NOW)
    assert w.start.date().isoformat() == "2026-10-24"  # Saturday
    assert w.end.date().isoformat() == "2026-10-25"  # Sunday
    assert len(w.hours) == 30  # 06:00 to 20:00 on both days


def test_named_weekday_resolves_to_the_next_occurrence():
    w = resolve(raw_text="golf on Saturday within 40 km of Andheri", now=NOW)
    assert w.start.date().isoformat() == "2026-10-24"
    assert w.start.hour == 6  # daylight of that day


def test_clock_time_is_kept():
    w = resolve(raw_text="tennis at 6 pm near Hinjewadi", now=NOW)
    assert w.start.hour == 18
    assert w.end.hour == 19


def test_past_clock_time_rolls_to_tomorrow():
    w = resolve(raw_text="cycle to work at 8 AM", now=NOW)
    assert w.start.date().isoformat() == "2026-10-22"
    assert w.start.hour == 8
    assert any("already passed" in a for a in w.assumptions)


def test_past_part_of_day_rolls_to_tomorrow():
    w = resolve(raw_text="football near Koramangala this evening", now=NOW)
    assert w.start.date().isoformat() == "2026-10-22"
    assert w.start.hour == 17


def test_next_two_days_gives_two_mornings():
    w = resolve(
        raw_text="morning walk near Powai in the next two days", now=NOW
    )
    days = {h.date().isoformat() for h in w.hours}
    assert len(days) == 2
    assert all(h.hour in range(6, 12) for h in w.hours)


def test_distance_numbers_are_not_mistaken_for_clock_times():
    w = resolve(raw_text="cricket match within 15 km of Whitefield", now=NOW)
    assert not any(h.hour == 15 for h in w.hours)


def test_ordinals_are_not_clock_times():
    w = resolve(raw_text="park near JP Nagar 5th Phase", now=NOW)
    assert not any(h.hour == 5 for h in w.hours)


def test_no_time_signal_falls_back_to_the_rest_of_the_day():
    w = resolve(raw_text="where can I go running near Jayanagar", now=NOW)
    assert w.hours
    assert all(h >= NOW.replace(minute=0, second=0, microsecond=0) for h in w.hours)
    assert w.assumptions


def test_sunday_afternoon():
    w = resolve(raw_text="picnic near Cubbon Park on Sunday afternoon", now=NOW)
    assert w.start.date().isoformat() == "2026-10-25"
    assert w.start.hour == 12
    assert w.end.hour == 17


def test_today_is_kept_as_today():
    w = resolve(raw_text="where can I play today near HSR Layout", now=NOW)
    assert w.start.date().isoformat() == "2026-10-21"


def test_explicit_span_beats_a_hallucinated_day():
    """The model sometimes invents 'next weekend' for 'in the next two days'.

    The user's own wording wins, otherwise the answer lands a week out.
    """
    w = resolve(
        raw_text="morning walk near Powai in the next two days",
        day="next_weekend",
        part_of_day="morning",
        hour=21,
        days_ahead=1,
        now=NOW,
    )
    assert w.start.date().isoformat() == "2026-10-22"
    assert w.end.date().isoformat() == "2026-10-23"
    assert "the next 2 days" in w.label


def test_model_hour_is_ignored_when_the_question_has_no_clock():
    w = resolve(
        raw_text="a walk near Powai tomorrow morning",
        day="tomorrow",
        part_of_day="morning",
        hour=21,
        now=NOW,
    )
    assert all(h.hour < 12 for h in w.hours)
