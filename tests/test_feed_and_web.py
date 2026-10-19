import base64
import xml.etree.ElementTree as ET

from radcast.config import Show, load_shows
from radcast.episodes import Catalogue, Episode
from radcast.feed import build_feed
from radcast.scheduler import Scheduler
from radcast.web import make_app
from datetime import time

ITUNES = "{http://www.itunes.com/dtds/podcast-1.0.dtd}"


def episode(key="breakfast-show-2026-10-20", slug="breakfast-show"):
    return Episode(key=key, slug=slug, show="Breakfast Show", title="Breakfast Show, Tuesday 20 October 2026",
                   filename=f"{key}.m4a", mime="audio/mp4", date="2026-10-20T05:00:00+00:00",
                   duration_seconds=3*3600 + 61, file_size=123456, added_at="2026-10-20T08:01:00+00:00")


def test_combined_feed(settings):
    xml = build_feed(settings, [episode()], "https://radio.example.net/artwork-abc.jpg")
    root = ET.fromstring(xml)
    channel = root.find("channel")
    assert channel.findtext("title") == "Radcast"
    assert channel.findtext("link") == "https://radio.example.net"
    assert channel.find("{http://www.w3.org/2005/Atom}link").get("href") == "https://radio.example.net/feed.xml"
    assert channel.find(f"{ITUNES}image").get("href") == "https://radio.example.net/artwork-abc.jpg"
    assert channel.find(f"{ITUNES}block").text.lower() == "yes"
    item = channel.find("item")
    guid = item.find("guid")
    assert guid.text == "breakfast-show-2026-10-20" and guid.get("isPermaLink") == "false"
    enc = item.find("enclosure")
    assert enc.get("url") == "https://radio.example.net/recordings/breakfast-show-2026-10-20.m4a"
    assert enc.get("type") == "audio/mp4" and enc.get("length") == "123456"
    assert item.find(f"{ITUNES}duration").text == "03:01:01"


def test_per_show_feed_uses_show_name_and_description(settings):
    show = Show(name="Breakfast Show", slug="breakfast-show", start=time(7), end=time(10), days=frozenset({0}),
                description="Mornings with toast.")
    channel = ET.fromstring(build_feed(settings, [episode()], None, show)).find("channel")
    assert channel.findtext("title") == "Breakfast Show"
    assert channel.findtext("description") == "Mornings with toast."
    assert channel.find(f"{ITUNES}image") is None


def _app(settings, tmp_path):
    settings.shows_file.write_text("shows:\n  - name: Breakfast Show\n    days: mon-fri\n    start: '07:00'\n    end: '10:00'\n")
    shows, problems = load_shows(settings)
    catalogue = Catalogue(settings.recordings_dir, settings.max_episodes)
    settings.recordings_dir.mkdir()
    (settings.recordings_dir / "breakfast-show-2026-10-20.m4a").write_bytes(b"\0" * 2048)
    catalogue.add(episode())
    scheduler = Scheduler(settings, shows, catalogue)  # not started: no background threads in tests
    app = make_app(settings, shows, problems, catalogue, scheduler)
    app.testing = True
    return app.test_client()


def _auth(user="me", password="secret"):
    return {"Authorization": "Basic " + base64.b64encode(f"{user}:{password}".encode()).decode()}


def test_routes(settings, tmp_path):
    c = _app(settings, tmp_path)
    assert c.get("/health").json["status"] == "ok"
    assert c.get("/feed.xml").status_code == 401
    assert c.get("/feed.xml", headers=_auth("me", "wrong")).status_code == 401
    r = c.get("/feed.xml", headers=_auth())
    assert r.status_code == 200 and r.mimetype == "application/rss+xml"
    assert c.get("/feeds/breakfast-show.xml", headers=_auth()).status_code == 200
    assert c.get("/feeds/nope.xml", headers=_auth()).status_code == 404
    r = c.get("/recordings/breakfast-show-2026-10-20.m4a", headers={**_auth(), "Range": "bytes=0-99"})
    assert r.status_code == 206 and len(r.data) == 100  # podcast apps seek with Range requests
    assert r.mimetype == "audio/mp4"
    assert c.get("/recordings/episodes.json", headers=_auth()).status_code == 404  # catalogue is not served
    assert c.get("/recordings/../pyproject.toml", headers=_auth()).status_code == 404
    assert c.get("/artwork-abc.jpg").status_code == 404  # no artwork configured
    assert c.get("/logs", headers=_auth()).mimetype == "text/plain"
    page = c.get("/", headers=_auth()).data.decode()
    assert "Breakfast Show" in page and "Mon-Fri 07:00-10:00" in page
    assert "https://me:secret@radio.example.net/feed.xml" in page
    assert "https://me:secret@radio.example.net/feeds/breakfast-show.xml" in page


def test_artwork_url_changes_with_content(settings, tmp_path):
    c = _app(settings, tmp_path)
    settings.artwork.write_bytes(b"first")
    first = ET.fromstring(c.get("/feed.xml", headers=_auth()).data).find(f"channel/{ITUNES}image").get("href")
    assert c.get(first.replace("https://radio.example.net", "")).status_code == 200
    settings.artwork.write_bytes(b"second")
    import os, time as t
    os.utime(settings.artwork, (t.time() + 5, t.time() + 5))
    second = ET.fromstring(c.get("/feed.xml", headers=_auth()).data).find(f"channel/{ITUNES}image").get("href")
    assert first != second
