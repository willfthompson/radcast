from datetime import date, time

import pytest

from radcast.config import ConfigError, load_settings, load_shows, parse_days, parse_shows, parse_time, slugify


def test_settings_happy_path(settings):
    assert settings.feed_url == "https://radio.example.net"  # trailing slash stripped
    assert settings.tz_name == "Europe/Amsterdam"
    assert settings.audio_mode == "auto"
    assert settings.max_episodes == 20
    assert settings.auth_enabled


def test_settings_reports_every_problem_at_once(env):
    env.update({"STREAM_URL": "", "TZ": "Mars/Olympus", "FEED_URL": "radio.example.net", "AUDIO_MODE": "loud",
                "MAX_EPISODES": "0", "FEED_PASSWORD": ""})
    with pytest.raises(ConfigError) as exc:
        load_settings(env)
    text = str(exc.value)
    for needle in ("STREAM_URL", "TZ", "FEED_URL", "AUDIO_MODE", "MAX_EPISODES", "FEED_USERNAME"):
        assert needle in text


def test_no_auth_is_allowed(env):
    env.pop("FEED_USERNAME"), env.pop("FEED_PASSWORD")
    assert not load_settings(env).auth_enabled


@pytest.mark.parametrize("value, expected", [
    ("mon-fri", {0, 1, 2, 3, 4}),
    ("Mon, Wed, Fri", {0, 2, 4}),
    (["sat", "sun"], {5, 6}),
    ("daily", set(range(7))),
    ("weekends", {5, 6}),
    ("weekdays", {0, 1, 2, 3, 4}),
    ("fri-mon", {4, 5, 6, 0}),
    ("Saturday", {5}),
])
def test_parse_days(value, expected):
    assert parse_days(value) == frozenset(expected)


def test_parse_days_rejects_nonsense():
    with pytest.raises(ValueError):
        parse_days("funday")


@pytest.mark.parametrize("value, expected", [("07:00", time(7, 0)), ("7:05", time(7, 5)), ("23:59", time(23, 59))])
def test_parse_time(value, expected):
    assert parse_time(value) == expected


@pytest.mark.parametrize("value", ["24:00", "7", "7pm", "07:60"])
def test_parse_time_rejects(value):
    with pytest.raises(ValueError):
        parse_time(value)


def test_slugify():
    assert slugify("The Late Session!") == "the-late-session"
    assert slugify("  Café   du Monde ") == "caf-du-monde"
    assert slugify("***") == "show"


def test_parse_shows_skips_bad_entries_and_keeps_good_ones():
    doc = {"shows": [
        {"name": "Breakfast Show", "days": "mon-fri", "start": "07:00", "end": "10:00"},
        {"name": "Broken", "days": "mon", "start": "seven", "end": "10:00"},
        {"name": "No schedule", "start": "07:00", "end": "08:00"},
        {"name": "Both", "days": "mon", "on": "2026-11-03", "start": "07:00", "end": "08:00"},
        {"name": "One off", "on": "2026-11-03", "start": "21:00", "end": "02:00", "stream": "https://x.example/a.m3u8"},
        {"name": "breakfast show", "days": "sat", "start": "08:00", "end": "09:00"},
    ]}
    shows, problems = parse_shows(doc)
    assert [s.name for s in shows] == ["Breakfast Show", "One off"]
    assert len(problems) == 4
    assert "Broken" in problems[0] and "HH:MM" in problems[0]
    assert "No schedule" in problems[1]
    assert "Both" in problems[2]
    assert "breakfast show" in problems[3] and "too similar" in problems[3]
    one_off = shows[1]
    assert one_off.on == date(2026, 11, 3) and one_off.days is None
    assert one_off.crosses_midnight and one_off.stream == "https://x.example/a.m3u8"


def test_parse_shows_accepts_bare_list_and_file_level_title_default():
    shows, problems = parse_shows([{"name": "A", "days": "sun", "start": "20:00", "end": "22:00"}])
    assert not problems and shows[0].title.startswith("{name}")
    shows, _ = parse_shows({"title": "{name} {date:%Y-%m-%d}", "shows": [{"name": "A", "days": "sun", "start": "1:00", "end": "2:00"}]})
    assert shows[0].title == "{name} {date:%Y-%m-%d}"


def test_parse_shows_rejects_wrong_shapes():
    assert parse_shows(None)[1] == ["the shows file is empty"]
    assert "shows:" in parse_shows({"nope": 1})[1][0]
    assert "shows:" in parse_shows("text")[1][0]


def test_load_shows_from_yaml_file(settings):
    settings.shows_file.write_text("shows:\n  - name: Night\n    days: [fri, sat]\n    start: '23:00'\n    end: '01:00'\n")
    shows, problems = load_shows(settings)
    assert not problems and shows[0].slug == "night" and shows[0].crosses_midnight


def test_load_shows_missing_file_is_a_problem_not_a_crash(settings):
    shows, problems = load_shows(settings)
    assert shows == [] and "not found" in problems[0]


def test_load_shows_from_json_env(env):
    env["SHOWS_JSON"] = '[{"name": "Env Show", "days": "daily", "start": "06:00", "end": "07:00"}]'
    shows, problems = load_shows(load_settings(env))
    assert not problems and shows[0].name == "Env Show"
    env["SHOWS_JSON"] = "{not json"
    assert "SHOWS_JSON" in load_shows(load_settings(env))[1][0]


def test_one_off_on_key_survives_yaml_boolean_parsing(settings):
    # PyYAML turns the bare key `on` into True; the example file and README both use it.
    settings.shows_file.write_text("shows:\n  - name: Special\n    on: 2026-11-03\n    start: '21:00'\n    end: '02:00'\n")
    shows, problems = load_shows(settings)
    assert not problems and shows[0].on == date(2026, 11, 3) and shows[0].days is None
