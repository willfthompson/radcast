"""Podcast RSS generation: one combined feed, and one feed per show."""
from __future__ import annotations

from datetime import datetime

from feedgen.feed import FeedGenerator

from .config import Settings, Show
from .episodes import Episode


def build_feed(settings: Settings, episodes: list[Episode], artwork_url: str | None,
               show: Show | None = None) -> bytes:
    fg = FeedGenerator()
    fg.load_extension("podcast")

    title = show.name if show else settings.feed_title
    description = (show.description if show and show.description else settings.feed_description)
    link = settings.feed_url + (f"/feeds/{show.slug}.xml" if show else "/feed.xml")

    fg.title(title)
    fg.link(href=link, rel="self")
    fg.link(href=settings.feed_url, rel="alternate")  # last one wins for RSS <link>: the dashboard
    fg.description(description)
    fg.language(settings.feed_language)
    fg.podcast.itunes_author(settings.feed_author)
    fg.podcast.itunes_category(settings.feed_category)
    fg.podcast.itunes_explicit("no")
    fg.podcast.itunes_block("yes")  # ask directories not to list a private feed they stumble on
    if artwork_url:
        fg.podcast.itunes_image(artwork_url)
    if settings.feed_email:
        fg.podcast.itunes_owner(name=settings.feed_author, email=settings.feed_email)

    for ep in episodes:
        entry = fg.add_entry(order="append")
        entry.guid(ep.key, permalink=False)
        entry.title(ep.title)
        entry.description(ep.title if not show or not show.description else show.description)
        entry.published(datetime.fromisoformat(ep.date))
        entry.enclosure(f"{settings.feed_url}/recordings/{ep.filename}", str(ep.file_size), ep.mime)
        hours, rem = divmod(int(ep.duration_seconds), 3600)
        minutes, seconds = divmod(rem, 60)
        entry.podcast.itunes_duration(f"{hours:02d}:{minutes:02d}:{seconds:02d}")

    return fg.rss_str(pretty=True)
