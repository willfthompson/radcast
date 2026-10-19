"""Configuration: infrastructure from environment variables, timers from shows.yml.

Infrastructure settings (stream URL, timezone, feed URL, auth, audio handling) are
flat values and live in the environment. The list of shows to record is structured,
so it lives in a YAML file (or, for hosts where mounting a file is awkward, the same
structure as JSON in the SHOWS_JSON variable).
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from datetime import date, time
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import yaml

AUDIO_MODES = ("auto", "copy", "transcode")

_DAY_NAMES = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
_DAY_ALIASES = {
    "monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
    "friday": 4, "saturday": 5, "sunday": 6,
    "daily": "0-6", "everyday": "0-6", "all": "0-6",
    "weekdays": "0-4", "weekends": "5-6",
}
_TIME_RE = re.compile(r"^(\d{1,2}):(\d{2})$")
_BITRATE_RE = re.compile(r"^\d+k$")


class ConfigError(Exception):
    """Raised when the environment is unusable. Carries every problem found, not just the first."""

    def __init__(self, problems: list[str]):
        self.problems = problems
        super().__init__("\n".join(f"  - {p}" for p in problems))


def slugify(text: str) -> str:
    safe = re.sub(r"[^\w\s-]", "", text.lower(), flags=re.ASCII)
    return re.sub(r"[\s_-]+", "-", safe).strip("-") or "show"


@dataclass(frozen=True)
class Show:
    """One VCR-style timer.

    Either `days` (weekly, a set of weekday numbers, Monday = 0) or `on` (a single
    date) is set, never both. `end` earlier than or equal to `start` means the
    recording runs past midnight into the following day.
    """

    name: str
    slug: str
    start: time
    end: time
    days: frozenset[int] | None = None
    on: date | None = None
    stream: str | None = None
    title: str = "{name}, {date:%A %-d %B %Y}"
    description: str | None = None

    @property
    def crosses_midnight(self) -> bool:
        return self.end <= self.start


@dataclass(frozen=True)
class Settings:
    stream_url: str
    tz_name: str
    feed_url: str
    feed_title: str
    feed_description: str
    feed_author: str
    feed_language: str
    feed_category: str
    feed_email: str | None
    artwork: Path | None
    username: str | None
    password: str | None
    audio_mode: str
    transcode_bitrate: str
    max_episodes: int
    start_buffer: int
    end_buffer: int
    recordings_dir: Path
    shows_file: Path
    shows_json: str | None
    port: int

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.tz_name)

    @property
    def auth_enabled(self) -> bool:
        return bool(self.username)


def _int(env: dict, name: str, default: int, minimum: int, problems: list[str]) -> int:
    raw = env.get(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        problems.append(f"{name} must be a whole number, got {raw!r}")
        return default
    if value < minimum:
        problems.append(f"{name} must be at least {minimum}, got {value}")
    return value


def load_settings(env: dict | None = None) -> Settings:
    """Build Settings from the environment. Raises ConfigError listing every problem."""
    env = dict(os.environ if env is None else env)
    problems: list[str] = []

    stream_url = env.get("STREAM_URL", "").strip()
    if not stream_url:
        problems.append("STREAM_URL is required: the live stream to record (an HLS .m3u8, Icecast or similar URL)")
    elif "://" not in stream_url:
        problems.append(f"STREAM_URL does not look like a URL: {stream_url!r}")

    tz_name = env.get("TZ", "").strip()
    if not tz_name:
        problems.append("TZ is required so timers fire at the right local time, e.g. TZ=Europe/Amsterdam")
    else:
        try:
            ZoneInfo(tz_name)
        except (ZoneInfoNotFoundError, ValueError):
            problems.append(f"TZ {tz_name!r} is not a known timezone (use a name like Europe/Amsterdam or America/New_York)")

    feed_url = env.get("FEED_URL", "").strip().rstrip("/")
    if not feed_url:
        problems.append("FEED_URL is required: the public address podcast apps will use, e.g. https://pi.your-tailnet.ts.net")
    elif not feed_url.startswith(("http://", "https://")):
        problems.append(f"FEED_URL must start with http:// or https://, got {feed_url!r}")

    audio_mode = env.get("AUDIO_MODE", "auto").strip().lower() or "auto"
    if audio_mode not in AUDIO_MODES:
        problems.append(f"AUDIO_MODE must be one of {', '.join(AUDIO_MODES)}, got {audio_mode!r}")

    transcode_bitrate = env.get("TRANSCODE_BITRATE", "128k").strip() or "128k"
    if not _BITRATE_RE.match(transcode_bitrate):
        problems.append(f"TRANSCODE_BITRATE must look like 128k, got {transcode_bitrate!r}")

    username = env.get("FEED_USERNAME", "").strip() or None
    password = env.get("FEED_PASSWORD", "") or None
    if bool(username) != bool(password):
        problems.append("FEED_USERNAME and FEED_PASSWORD must be set together (or both left empty for no auth)")

    artwork_raw = env.get("ARTWORK", "/config/artwork.jpg").strip()
    artwork = Path(artwork_raw) if artwork_raw else None
    if artwork is not None and artwork.suffix.lower() not in (".jpg", ".jpeg", ".png"):
        problems.append(f"ARTWORK must be a .jpg or .png file, got {artwork_raw!r}")

    feed_title = env.get("FEED_TITLE", "").strip() or "Radcast"
    settings = Settings(
        stream_url=stream_url,
        tz_name=tz_name,
        feed_url=feed_url,
        feed_title=feed_title,
        feed_description=env.get("FEED_DESCRIPTION", "").strip() or "Radio recordings",
        feed_author=env.get("FEED_AUTHOR", "").strip() or feed_title,
        feed_language=env.get("FEED_LANGUAGE", "").strip() or "en",
        feed_category=env.get("FEED_CATEGORY", "").strip() or "Music",
        feed_email=env.get("FEED_EMAIL", "").strip() or None,
        artwork=artwork,
        username=username,
        password=password,
        audio_mode=audio_mode,
        transcode_bitrate=transcode_bitrate,
        max_episodes=_int(env, "MAX_EPISODES", 20, 1, problems),
        start_buffer=_int(env, "START_BUFFER", 30, 0, problems),
        end_buffer=_int(env, "END_BUFFER", 60, 0, problems),
        recordings_dir=Path(env.get("RECORDINGS_DIR", "").strip() or "./recordings"),
        shows_file=Path(env.get("SHOWS_FILE", "").strip() or "./shows.yml"),
        shows_json=env.get("SHOWS_JSON", "").strip() or None,
        port=_int(env, "PORT", 5000, 1, problems),
    )
    if problems:
        raise ConfigError(problems)
    return settings


# --- shows ---------------------------------------------------------------------------


def parse_days(value) -> frozenset[int]:
    """Accept 'mon-fri', 'sat,sun', 'daily', 'weekends', ['mon', 'wed'] and so on."""
    if isinstance(value, str):
        tokens = [t.strip() for t in value.split(",")]
    elif isinstance(value, list):
        tokens = [str(t).strip() for t in value]
    else:
        raise ValueError("days must be a string like 'mon-fri' or a list like [mon, wed, fri]")

    def day_index(token: str) -> int:
        t = token.lower()
        if t in _DAY_NAMES:
            return _DAY_NAMES.index(t)
        alias = _DAY_ALIASES.get(t)
        if isinstance(alias, int):
            return alias
        raise ValueError(f"unknown day {token!r}")

    days: set[int] = set()
    for token in tokens:
        if not token:
            continue
        alias = _DAY_ALIASES.get(token.lower())
        if isinstance(alias, str):  # daily / weekdays / weekends expand to a range
            token = alias
        if "-" in token:
            first, last = (p.strip() for p in token.split("-", 1))
            a = day_index(first) if not first.isdigit() else int(first)
            b = day_index(last) if not last.isdigit() else int(last)
            span = range(a, b + 1) if a <= b else list(range(a, 7)) + list(range(0, b + 1))
            days.update(span)
        else:
            days.add(day_index(token))
    if not days:
        raise ValueError("days is empty")
    return frozenset(days)


def parse_time(value) -> time:
    text = str(value).strip()
    m = _TIME_RE.match(text)
    if not m:
        raise ValueError(f"time must be HH:MM (24-hour), got {text!r}")
    hour, minute = int(m.group(1)), int(m.group(2))
    if hour > 23 or minute > 59:
        raise ValueError(f"time out of range: {text!r}")
    return time(hour, minute)


def _parse_show(raw: dict, defaults: dict) -> Show:
    if not isinstance(raw, dict):
        raise ValueError("each show must be a mapping with name, days (or on), start and end")
    # YAML 1.1 (PyYAML) reads a bare `on` key as the boolean True, so `on: 2026-11-03`
    # arrives as {True: date(...)}. Put the key back.
    raw = {("on" if k is True else k): v for k, v in raw.items()}
    name = str(raw.get("name", "")).strip()
    if not name:
        raise ValueError("name is required")
    missing = [k for k in ("start", "end") if raw.get(k) in (None, "")]
    if missing:
        raise ValueError(f"missing {', '.join(missing)}")
    has_days, has_on = raw.get("days") not in (None, ""), raw.get("on") not in (None, "")
    if has_days == has_on:
        raise ValueError("set either days (weekly) or on (a single date), not both and not neither")

    days = parse_days(raw["days"]) if has_days else None
    on = None
    if has_on:
        on_raw = raw["on"]
        if isinstance(on_raw, date):
            on = on_raw
        else:
            try:
                on = date.fromisoformat(str(on_raw).strip())
            except ValueError:
                raise ValueError(f"on must be a date like 2026-11-03, got {on_raw!r}") from None

    stream = str(raw["stream"]).strip() if raw.get("stream") else None
    if stream and "://" not in stream:
        raise ValueError(f"stream does not look like a URL: {stream!r}")

    title = str(raw.get("title") or defaults.get("title") or Show.title).strip()
    description = str(raw["description"]).strip() if raw.get("description") else None

    return Show(
        name=name,
        slug=slugify(name),
        start=parse_time(raw["start"]),
        end=parse_time(raw["end"]),
        days=days,
        on=on,
        stream=stream,
        title=title,
        description=description,
    )


def parse_shows(document) -> tuple[list[Show], list[str]]:
    """Turn a parsed YAML/JSON document into shows.

    Invalid entries are reported and skipped rather than failing the whole file, so one
    typo doesn't stop every other show from recording. Returns (shows, problems).
    """
    problems: list[str] = []
    if isinstance(document, list):
        entries, defaults = document, {}
    elif isinstance(document, dict):
        entries = document.get("shows")
        defaults = {k: v for k, v in document.items() if k != "shows"}
        if not isinstance(entries, list):
            return [], ["expected a top-level 'shows:' list"]
    elif document is None:
        return [], ["the shows file is empty"]
    else:
        return [], ["expected a top-level 'shows:' list"]

    shows: list[Show] = []
    seen: dict[str, str] = {}
    for i, raw in enumerate(entries):
        label = raw.get("name") if isinstance(raw, dict) and raw.get("name") else f"#{i + 1}"
        try:
            show = _parse_show(raw, defaults)
        except ValueError as e:
            problems.append(f"show {label!s}: {e}")
            continue
        if show.slug in seen:
            problems.append(f"show {label!s}: its name is too similar to {seen[show.slug]!r} (both become '{show.slug}')")
            continue
        seen[show.slug] = show.name
        shows.append(show)
    return shows, problems


def load_shows(settings: Settings) -> tuple[list[Show], list[str]]:
    """Load shows from SHOWS_JSON if set, otherwise from the shows file."""
    if settings.shows_json:
        try:
            document = json.loads(settings.shows_json)
        except json.JSONDecodeError as e:
            return [], [f"SHOWS_JSON is not valid JSON: {e}"]
        return parse_shows(document)

    path = settings.shows_file
    if not path.exists():
        return [], [f"shows file not found at {path} (copy shows.example.yml there, or set SHOWS_JSON)"]
    try:
        document = yaml.safe_load(path.read_text())
    except yaml.YAMLError as e:
        return [], [f"{path.name} is not valid YAML: {e}"]
    except OSError as e:
        return [], [f"could not read {path}: {e}"]
    return parse_shows(document)
