from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from radcast.config import Show
from radcast.timers import airing_on, airings_between, describe_days, describe_rule, next_airing, upcoming

AMS = ZoneInfo("Europe/Amsterdam")
UTC = timezone.utc


def show(name="Breakfast Show", days="0-4", start=time(7, 0), end=time(10, 0), on=None, **kw):
    day_set = None if on else frozenset(int(d) for d in range(int(days[0]), int(days[-1]) + 1))
    return Show(name=name, slug=name.lower().replace(" ", "-"), start=start, end=end, days=day_set, on=on, **kw)


def test_airing_is_in_utc_with_local_key_and_title():
    a = airing_on(show(), date(2026, 10, 20), AMS)  # Tuesday, CEST (+02:00)
    assert a.start == datetime(2026, 10, 20, 5, 0, tzinfo=UTC)
    assert a.end == datetime(2026, 10, 20, 8, 0, tzinfo=UTC)
    assert a.key == "breakfast-show-2026-10-20"
    assert a.title == "Breakfast Show, Tuesday 20 October 2026"
    assert a.duration == timedelta(hours=3)


def test_end_before_start_means_next_day():
    s = show("Late", start=time(23, 0), end=time(1, 0))
    a = airing_on(s, date(2026, 10, 23), AMS)
    assert a.local_start.day == 23 and a.local_end.day == 24
    assert a.duration == timedelta(hours=2)
    assert a.key == "late-2026-10-23"  # keyed by the local date it starts on


def test_dst_change_keeps_wall_clock_time():
    # Clocks go back in Europe/Amsterdam on 2026-10-25; a 07:00 show is still 07:00 local on both sides.
    before = airing_on(show(days="0-6"), date(2026, 10, 24), AMS)
    after = airing_on(show(days="0-6"), date(2026, 10, 26), AMS)
    assert before.local_start.hour == after.local_start.hour == 7
    assert before.start.hour == 5 and after.start.hour == 6  # UTC offsets differ
    # A show that spans the change gets its real (not wall-clock) length
    overnight = airing_on(show("Night", days="0-6", start=time(1, 0), end=time(4, 0)), date(2026, 10, 25), AMS)
    assert overnight.duration == timedelta(hours=4)


def test_upcoming_finds_a_show_that_started_yesterday_evening():
    s = show("Late", days="0-6", start=time(23, 0), end=time(1, 0))
    now = datetime(2026, 10, 24, 0, 30, tzinfo=AMS)  # half past midnight, still on air
    first = next(upcoming(s, AMS, now))
    assert first.key == "late-2026-10-23" and first.is_on_air(now)
    assert first.remaining(now) == timedelta(minutes=30)


def test_upcoming_skips_finished_airings_and_wraps_the_week():
    now = datetime(2026, 10, 23, 11, 0, tzinfo=AMS)  # Friday after the show has ended
    nxt = next_airing(show(), AMS, now)
    assert nxt.local_start.date() == date(2026, 10, 26)  # Monday


def test_one_off_timer():
    s = show("Special", on=date(2026, 11, 3), start=time(21, 0), end=time(2, 0))
    before = datetime(2026, 11, 1, tzinfo=UTC)
    after = datetime(2026, 11, 5, tzinfo=UTC)
    assert next_airing(s, AMS, before).key == "special-2026-11-03"
    assert next_airing(s, AMS, after) is None


def test_airings_between_orders_across_shows():
    shows = [show(), show("Late", days="0-6", start=time(23, 0), end=time(1, 0))]
    start = datetime(2026, 10, 19, tzinfo=AMS)
    found = airings_between(shows, AMS, start, start + timedelta(days=2))
    assert [a.key for a in found] == [
        "breakfast-show-2026-10-19", "late-2026-10-19", "breakfast-show-2026-10-20", "late-2026-10-20",
    ]


def test_describe():
    assert describe_days(frozenset(range(5))) == "Mon-Fri"
    assert describe_days(frozenset({5, 6})) == "Sat, Sun"
    assert describe_days(frozenset({0, 2, 3, 4})) == "Mon, Wed-Fri"
    assert describe_days(frozenset(range(7))) == "Daily"
    assert describe_rule(show()) == "Mon-Fri 07:00-10:00"
    assert describe_rule(show("L", days="5-6", start=time(23, 0), end=time(1, 0))) == "Sat, Sun 23:00-01:00 next day"
    assert describe_rule(show("S", on=date(2026, 11, 3), start=time(21, 0), end=time(23, 0))) == "3 Nov 2026 21:00-23:00"
