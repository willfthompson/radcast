"""Flask app: dashboard, feeds, media files, artwork, logs, health."""
from __future__ import annotations

import hashlib
import hmac
import logging
import os
import sys
from datetime import datetime, timezone
from functools import wraps
from pathlib import Path
from urllib.parse import quote

from flask import Flask, Response, abort, redirect, render_template, request, send_file, send_from_directory

from . import __version__, logs
from .config import ConfigError, Settings, Show, load_settings, load_shows
from .episodes import MEDIA_SUFFIXES, Catalogue
from .feed import build_feed
from .scheduler import Scheduler
from .timers import describe_rule

logger = logging.getLogger("radcast.web")


class Artwork:
    """Serves the cover image under a URL that changes whenever the file does.

    Podcast apps cache artwork by URL, so swapping the file behind a fixed URL never
    shows up. The content hash in the path makes a new image a new URL.
    """

    def __init__(self, path: Path | None):
        self.path = path
        self._mtime: float | None = None
        self._token: str | None = None

    def token(self) -> str | None:
        if self.path is None or not self.path.is_file():
            return None
        mtime = self.path.stat().st_mtime
        if mtime != self._mtime:
            self._token = hashlib.sha256(self.path.read_bytes()).hexdigest()[:12]
            self._mtime = mtime
        return self._token

    def url(self, base: str) -> str | None:
        token = self.token()
        return f"{base}/artwork-{token}{self.path.suffix.lower()}" if token else None  # type: ignore[union-attr]


def create_app() -> Flask:
    """gunicorn entrypoint: load configuration, start the scheduler, build the app."""
    logs.setup_logging()
    try:
        settings = load_settings()
    except ConfigError as e:
        logger.error("Radcast cannot start. Fix these in your .env and try again:\n%s", e)
        sys.exit(1)

    _ensure_writable(settings.recordings_dir)

    shows, problems = load_shows(settings)
    for problem in problems:
        logger.error("shows: %s", problem)
    if not shows:
        logger.warning("no valid shows configured, nothing will be recorded until that's fixed")
    if not settings.auth_enabled:
        logger.warning("FEED_USERNAME/FEED_PASSWORD not set: anyone who can reach this address can read the feed")

    catalogue = Catalogue(settings.recordings_dir, settings.max_episodes)
    scheduler = Scheduler(settings, shows, catalogue)
    scheduler.start()
    logger.info("Radcast %s listening, feed at %s/feed.xml", __version__, settings.feed_url)
    return make_app(settings, shows, problems, catalogue, scheduler)


def _ensure_writable(directory: Path) -> None:
    try:
        directory.mkdir(parents=True, exist_ok=True)
        probe = directory / ".write-test"
        probe.write_text("ok")
        probe.unlink()
    except OSError as e:
        uid = os.getuid() if hasattr(os, "getuid") else "?"
        logger.error(
            "Radcast cannot write to %s (%s). The container runs as uid %s; on the host run:\n"
            "    sudo chown -R %s:%s ./recordings", directory, e.strerror or e, uid, uid, uid)
        sys.exit(1)


def make_app(settings: Settings, shows: list[Show], show_problems: list[str],
             catalogue: Catalogue, scheduler: Scheduler) -> Flask:
    app = Flask(__name__)
    artwork = Artwork(settings.artwork)
    shows_by_slug = {s.slug: s for s in shows}

    def requires_auth(view):
        @wraps(view)
        def wrapper(*args, **kwargs):
            if not settings.auth_enabled:
                return view(*args, **kwargs)
            auth = request.authorization
            ok = (auth is not None and auth.type == "basic"
                  and hmac.compare_digest(auth.username or "", settings.username or "")
                  and hmac.compare_digest(auth.password or "", settings.password or ""))
            if not ok:
                return Response("Authentication required", 401,
                                {"WWW-Authenticate": f'Basic realm="{settings.feed_title}"'})
            return view(*args, **kwargs)
        return wrapper

    def feed_response(episodes, show=None):
        xml = build_feed(settings, episodes, artwork.url(settings.feed_url), show)
        return Response(xml, content_type="application/rss+xml; charset=utf-8")

    def with_credentials(path: str) -> str:
        if not settings.auth_enabled:
            return settings.feed_url + path
        scheme, rest = settings.feed_url.split("://", 1)
        return f"{scheme}://{quote(settings.username, safe='')}:{quote(settings.password, safe='')}@{rest}{path}"

    @app.get("/health")
    def health():
        return {"status": "ok", "version": __version__, "recording": len(scheduler.in_progress()),
                "pending": len(scheduler.pending())}

    @app.get("/")
    @requires_auth
    def dashboard():
        now = datetime.now(timezone.utc)
        in_progress = {a.show.slug: a for a in scheduler.in_progress()}
        timers = []
        for show in shows:
            nxt = scheduler.next_for(show)
            timers.append({
                "show": show,
                "rule": describe_rule(show),
                "next": nxt,
                "recording": show.slug in in_progress,
                "stream": show.stream,
            })
        return render_template(
            "dashboard.html",
            settings=settings,
            version=__version__,
            now=now.astimezone(settings.tz),
            timers=timers,
            tests=[a for a in scheduler.in_progress() if a.show.slug == "test"],
            problems=show_problems,
            stream_info=scheduler.stream_info,
            stream_error=scheduler.stream_error,
            stream_checked=scheduler.stream_checked.astimezone(settings.tz) if scheduler.stream_checked else None,
            last_failure=scheduler.last_failure,
            episodes=catalogue.list()[:15],
            feeds=[("All shows", with_credentials("/feed.xml"))]
                  + [(s.name, with_credentials(f"/feeds/{s.slug}.xml")) for s in shows],
            artwork_url=artwork.url(settings.feed_url),
            fmt_local=lambda dt: dt.astimezone(settings.tz).strftime("%a %d %b %H:%M"),
        )

    @app.post("/test")
    @requires_auth
    def test_recording():
        if request.headers.get("Sec-Fetch-Site") == "cross-site":
            abort(403)
        scheduler.start_test_recording(60)
        return redirect("/", code=303)

    @app.get("/feed.xml")
    @requires_auth
    def combined_feed():
        return feed_response(catalogue.list())

    @app.get("/feeds/<slug>.xml")
    @requires_auth
    def show_feed(slug: str):
        show = shows_by_slug.get(slug)
        if show is None:
            abort(404)
        return feed_response(catalogue.for_show(slug), show)

    @app.get("/recordings/<path:filename>")
    @requires_auth
    def recording(filename: str):
        if "/" in filename or not filename.lower().endswith(MEDIA_SUFFIXES):
            abort(404)
        # Python's mimetypes table doesn't know .m4a on every platform, so be explicit.
        mime = "audio/mpeg" if filename.lower().endswith(".mp3") else "audio/mp4"
        return send_from_directory(settings.recordings_dir.resolve(), filename, mimetype=mime, conditional=True)

    @app.get("/artwork-<token>.<ext>")
    def artwork_file(token: str, ext: str):
        if artwork.token() is None:
            abort(404)
        response = send_file(settings.artwork.resolve(), conditional=True)  # type: ignore[union-attr]
        response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        return response

    @app.get("/logs")
    @requires_auth
    def log_page():
        return Response("\n".join(logs.buffer), content_type="text/plain; charset=utf-8")

    return app
