import pytest

from radcast.config import load_settings

BASE_ENV = {
    "STREAM_URL": "https://stream.example.com/live.m3u8",
    "TZ": "Europe/Amsterdam",
    "FEED_URL": "https://radio.example.net/",
    "FEED_USERNAME": "me",
    "FEED_PASSWORD": "secret",
}


@pytest.fixture
def env(tmp_path):
    return {**BASE_ENV, "RECORDINGS_DIR": str(tmp_path / "recordings"), "SHOWS_FILE": str(tmp_path / "shows.yml"),
            "ARTWORK": str(tmp_path / "artwork.jpg")}


@pytest.fixture
def settings(env):
    return load_settings(env)
