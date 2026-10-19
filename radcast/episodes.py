"""The episode catalogue: episodes.json in the recordings directory.

One entry per airing key. Newest first. Keeps at most `max_episodes`, deleting the
audio of anything that falls off the end.
"""
from __future__ import annotations

import json
import logging
import os
import threading
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger("radcast.episodes")

MEDIA_SUFFIXES = (".m4a", ".mp3")


@dataclass(frozen=True)
class Episode:
    key: str
    slug: str
    show: str
    title: str
    filename: str
    mime: str
    date: str               # ISO 8601 UTC, when the airing started
    duration_seconds: int
    file_size: int
    added_at: str

    @property
    def started(self) -> datetime:
        return datetime.fromisoformat(self.date)


class Catalogue:
    def __init__(self, recordings_dir: Path, max_episodes: int):
        self.dir = recordings_dir
        self.path = recordings_dir / "episodes.json"
        self.max_episodes = max_episodes
        self._lock = threading.Lock()

    def list(self) -> list[Episode]:
        with self._lock:
            return self._load()

    def for_show(self, slug: str) -> list[Episode]:
        return [e for e in self.list() if e.slug == slug]

    def keys(self) -> set[str]:
        return {e.key for e in self.list()}

    def add(self, episode: Episode) -> None:
        with self._lock:
            current = self._load()
            episodes = [e for e in current if e.key != episode.key]
            for stale in (e for e in current if e.key == episode.key and e.filename != episode.filename):
                logger.warning("replacing earlier recording of %s (%s)", stale.key, stale.filename)
                self._delete_file(stale.filename)
            episodes.insert(0, episode)
            episodes.sort(key=lambda e: e.date, reverse=True)
            keep, drop = episodes[: self.max_episodes], episodes[self.max_episodes:]
            for old in drop:
                logger.info("retention: removing %s (%s)", old.title, old.filename)
                self._delete_file(old.filename)
            self._save(keep)
        logger.info("catalogued %s: %s (%.1f MB)", episode.key, episode.title, episode.file_size / 1_048_576)

    # -- internals ---------------------------------------------------------------------

    def _load(self) -> list[Episode]:
        if not self.path.exists():
            return []
        try:
            raw = json.loads(self.path.read_text())
            return [Episode(**{k: v for k, v in item.items() if k in Episode.__dataclass_fields__}) for item in raw]
        except (json.JSONDecodeError, OSError, TypeError) as e:
            logger.error("could not read %s: %s", self.path, e)
            return []

    def _save(self, episodes: list[Episode]) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps([asdict(e) for e in episodes], indent=2))
        os.replace(tmp, self.path)

    def _delete_file(self, filename: str) -> None:
        try:
            (self.dir / filename).unlink()
        except FileNotFoundError:
            pass
        except OSError as e:
            logger.error("could not delete %s: %s", filename, e)


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()
