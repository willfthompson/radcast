from pathlib import Path

from radcast.episodes import Catalogue, Episode


def ep(key, date, filename=None, size=5_000_000):
    return Episode(key=key, slug=key.rsplit("-", 3)[0], show="Show", title=key, filename=filename or f"{key}.m4a",
                   mime="audio/mp4", date=date, duration_seconds=3600, file_size=size, added_at=date)


def test_add_orders_newest_first_and_enforces_retention(tmp_path: Path):
    cat = Catalogue(tmp_path, max_episodes=2)
    for key, date in [("a-2026-01-01", "2026-01-01T07:00:00+00:00"), ("a-2026-01-03", "2026-01-03T07:00:00+00:00"),
                      ("a-2026-01-02", "2026-01-02T07:00:00+00:00")]:
        (tmp_path / f"{key}.m4a").write_bytes(b"x")
        cat.add(ep(key, date))
    assert [e.key for e in cat.list()] == ["a-2026-01-03", "a-2026-01-02"]
    assert not (tmp_path / "a-2026-01-01.m4a").exists()  # oldest fell off and its file went with it
    assert (tmp_path / "a-2026-01-03.m4a").exists()
    assert cat.keys() == {"a-2026-01-03", "a-2026-01-02"}


def test_same_key_replaces_and_deletes_the_old_file(tmp_path: Path):
    cat = Catalogue(tmp_path, max_episodes=5)
    (tmp_path / "old.mp3").write_bytes(b"x")
    cat.add(ep("a-2026-01-01", "2026-01-01T07:00:00+00:00", filename="old.mp3"))
    cat.add(ep("a-2026-01-01", "2026-01-01T07:00:00+00:00", filename="new.m4a"))
    assert [e.filename for e in cat.list()] == ["new.m4a"]
    assert not (tmp_path / "old.mp3").exists()


def test_for_show_filters_by_slug(tmp_path: Path):
    cat = Catalogue(tmp_path, max_episodes=5)
    cat.add(ep("a-2026-01-01", "2026-01-01T07:00:00+00:00"))
    cat.add(ep("b-2026-01-01", "2026-01-01T09:00:00+00:00"))
    assert [e.key for e in cat.for_show("a")] == ["a-2026-01-01"]


def test_corrupt_file_is_treated_as_empty(tmp_path: Path):
    (tmp_path / "episodes.json").write_text("{nope")
    assert Catalogue(tmp_path, 5).list() == []
