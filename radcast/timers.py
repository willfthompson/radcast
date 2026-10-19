"""Turn timer rules into concrete airings with a start and end instant.

An Airing is one occurrence of a show: "Breakfast Show on Tuesday 20 October, 07:00
to 10:00 local". Its key (slug plus local date) identifies the recording everywhere:
the filename, the feed GUID and the already-recorded check.

All instants are UTC. Aware datetimes that share a tzinfo compare and subtract on
wall-clock time in Python, which goes wrong twice a year around DST changes, so the
local wall times from the config are converted to UTC as early as possible.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Iterator
from zoneinfo import ZoneInfo

from .config import Show

UTC = timezone.utc
_DAY_LABELS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


@dataclass(frozen=True)
class Airing:
    show: Show
    start: datetime  # UTC
    end: datetime    # UTC
    key: str         # "<slug>-<local date of start>"
    tz_name: str

    @property
    def duration(self) -> timedelta:
        return self.end - self.start

    @property
    def local_start(self) -> datetime:
        return self.start.astimezone(ZoneInfo(self.tz_name))

    @property
    def local_end(self) -> datetime:
        return self.end.astimezone(ZoneInfo(self.tz_name))

    @property
    def title(self) -> str:
        return self.show.title.format(name=self.show.name, date=self.local_start)

    def remaining(self, now: datetime) -> timedelta:
        return self.end - now.astimezone(UTC)

    def is_on_air(self, now: datetime) -> bool:
        now = now.astimezone(UTC)
        return self.start <= now < self.end


def airing_on(show: Show, day: date, tz: ZoneInfo) -> Airing:
    """The airing of `show` whose start falls on local date `day`."""
    local_start = datetime.combine(day, show.start, tzinfo=tz)
    end_day = day + timedelta(days=1) if show.crosses_midnight else day
    local_end = datetime.combine(end_day, show.end, tzinfo=tz)
    return Airing(
        show=show,
        start=local_start.astimezone(UTC),
        end=local_end.astimezone(UTC),
        key=f"{show.slug}-{day.isoformat()}",
        tz_name=str(tz),
    )


def upcoming(show: Show, tz: ZoneInfo, now: datetime, horizon_days: int = 10) -> Iterator[Airing]:
    """Airings of `show` that have not finished yet, soonest first.

    Starts from yesterday so a show that began before midnight and is still on air is
    found. Weekly rules recur within seven days, so a ten-day horizon always yields
    at least one airing.
    """
    now = now.astimezone(UTC)
    today = now.astimezone(tz).date()
    if show.on is not None:
        airing = airing_on(show, show.on, tz)
        if airing.end > now:
            yield airing
        return
    assert show.days is not None
    for offset in range(-1, horizon_days):
        day = today + timedelta(days=offset)
        if day.weekday() not in show.days:
            continue
        airing = airing_on(show, day, tz)
        if airing.end > now:
            yield airing


def next_airing(show: Show, tz: ZoneInfo, now: datetime) -> Airing | None:
    return next(upcoming(show, tz, now), None)


def airings_between(shows: list[Show], tz: ZoneInfo, start: datetime, end: datetime) -> list[Airing]:
    """Every airing starting in [start, end), across all shows, in start order."""
    start, end = start.astimezone(UTC), end.astimezone(UTC)
    horizon = (end - start).days + 2
    found = []
    for show in shows:
        for airing in upcoming(show, tz, start, horizon_days=horizon):
            if start <= airing.start < end:
                found.append(airing)
    return sorted(found, key=lambda a: a.start)


def describe_days(days: frozenset[int]) -> str:
    """'Mon-Fri', 'Sat, Sun', 'Daily', 'Mon, Wed-Fri'."""
    if len(days) == 7:
        return "Daily"
    ordered = sorted(days)
    runs: list[list[int]] = []
    for d in ordered:
        if runs and d == runs[-1][-1] + 1:
            runs[-1].append(d)
        else:
            runs.append([d])
    parts = []
    for run in runs:
        if len(run) >= 3:
            parts.append(f"{_DAY_LABELS[run[0]]}-{_DAY_LABELS[run[-1]]}")
        else:
            parts.extend(_DAY_LABELS[d] for d in run)
    return ", ".join(parts)


def describe_rule(show: Show) -> str:
    when = describe_days(show.days) if show.days is not None else show.on.strftime("%-d %b %Y")  # type: ignore[union-attr]
    end = show.end.strftime("%H:%M") + (" next day" if show.crosses_midnight else "")
    return f"{when} {show.start.strftime('%H:%M')}-{end}"
