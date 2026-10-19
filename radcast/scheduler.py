"""Books one recording job per show and runs them.

There is no upstream schedule to poll: the timers are the schedule. For each show the
next unrecorded airing gets a one-shot job at (start - START_BUFFER). When it fires,
the job records until (end + END_BUFFER), catalogues the file, then books the show's
next airing. An hourly replan is a safety net (a failed job's airing is rebooked while
it is still on air, so a stream blip early in a show costs minutes, not the show), and
a startup plan picks up anything already on air after a restart.
"""
from __future__ import annotations

import logging
import threading
import time as _time
from datetime import datetime, timedelta, timezone

from apscheduler.schedulers.background import BackgroundScheduler

from . import recorder, timers
from .config import Settings, Show
from .episodes import Catalogue, Episode, now_iso
from .timers import Airing

logger = logging.getLogger("radcast.scheduler")

UTC = timezone.utc
MIN_WORTH_RECORDING = timedelta(seconds=60)
MISFIRE_GRACE_SECONDS = 600
MAX_ATTEMPTS = 3
RETRY_DELAY_SECONDS = 30
_SYSTEM_JOBS = {"replan", "stream_check"}


class Scheduler:
    def __init__(self, settings: Settings, shows: list[Show], catalogue: Catalogue):
        self.settings = settings
        self.shows = shows
        self.catalogue = catalogue
        self.tz = settings.tz
        self._aps = BackgroundScheduler(timezone=UTC)
        self._lock = threading.Lock()
        self._in_progress: dict[str, Airing] = {}
        # Dashboard state
        self.stream_info: recorder.StreamInfo | None = None
        self.stream_error: str | None = None
        self.stream_checked: datetime | None = None
        self.last_failure: str | None = None

    # -- lifecycle ---------------------------------------------------------------------

    def start(self) -> None:
        self._aps.add_job(self.plan, "interval", hours=1, id="replan", replace_existing=True)
        self._aps.add_job(self.check_stream, "interval", hours=6, id="stream_check", replace_existing=True,
                          next_run_time=datetime.now(UTC) + timedelta(seconds=2))
        self._aps.start()
        self.plan()
        logger.info("scheduler running: %d show(s), timezone %s", len(self.shows), self.settings.tz_name)

    def shutdown(self) -> None:
        self._aps.shutdown(wait=False)

    # -- planning ----------------------------------------------------------------------

    def plan(self, now: datetime | None = None) -> None:
        """Make sure every show has its next unrecorded airing booked."""
        now = now or datetime.now(UTC)
        recorded = self.catalogue.keys()
        for show in self.shows:
            for airing in timers.upcoming(show, self.tz, now):
                if airing.key in recorded or airing.key in self._in_progress:
                    continue
                if airing.remaining(now) < MIN_WORTH_RECORDING:
                    continue
                run_at = max(airing.start - timedelta(seconds=self.settings.start_buffer),
                             now + timedelta(seconds=2))
                if self._aps.get_job(airing.key) is None:
                    when = "now (already on air)" if airing.is_on_air(now) else run_at.astimezone(self.tz).strftime("%a %d %b %H:%M")
                    logger.info("booked %s: %s", airing.key, when)
                self._aps.add_job(self._run, "date", run_date=run_at, args=[airing], id=airing.key,
                                  replace_existing=True, misfire_grace_time=MISFIRE_GRACE_SECONDS)
                break  # one pending airing per show is enough

    def pending(self) -> list[Airing]:
        jobs = [j for j in self._aps.get_jobs() if j.id not in _SYSTEM_JOBS and j.args]
        return sorted((j.args[0] for j in jobs), key=lambda a: a.start)

    def in_progress(self) -> list[Airing]:
        with self._lock:
            return sorted(self._in_progress.values(), key=lambda a: a.start)

    def next_for(self, show: Show) -> Airing | None:
        for airing in self.pending() + self.in_progress():
            if airing.show.slug == show.slug:
                return airing
        return timers.next_airing(show, self.tz, datetime.now(UTC))

    # -- stream health -----------------------------------------------------------------

    def check_stream(self) -> recorder.StreamInfo | None:
        try:
            self.stream_info = recorder.probe(self.settings.stream_url)
            self.stream_error = None
            fmt = recorder.choose_format(self.stream_info, self.settings.audio_mode, self.settings.transcode_bitrate)
            logger.info("stream check: %s -> %s", self.stream_info.describe(), fmt.reason)
        except recorder.ProbeError as e:
            self.stream_info = None
            self.stream_error = str(e)
            logger.error("stream check failed: %s", e)
        self.stream_checked = datetime.now(UTC)
        return self.stream_info

    # -- recording ---------------------------------------------------------------------

    def _run(self, airing: Airing, end_buffer: int | None = None, minimum: timedelta = MIN_WORTH_RECORDING) -> None:
        with self._lock:
            self._in_progress[airing.key] = airing
        try:
            self.record_airing(airing, end_buffer, minimum)
        except Exception:
            logger.exception("unexpected error recording %s", airing.key)
        finally:
            with self._lock:
                self._in_progress.pop(airing.key, None)
            if airing.show.slug != "test":
                self.plan()

    def record_airing(self, airing: Airing, end_buffer: int | None = None,
                      minimum: timedelta = MIN_WORTH_RECORDING) -> Episode | None:
        url = airing.show.stream or self.settings.stream_url
        end_buffer = self.settings.end_buffer if end_buffer is None else end_buffer
        for attempt in range(1, MAX_ATTEMPTS + 1):
            now = datetime.now(UTC)
            remaining = airing.remaining(now)
            if remaining < minimum:
                logger.warning("skipping %s: only %ds of it left", airing.key, remaining.total_seconds())
                return None
            seconds = int(remaining.total_seconds()) + end_buffer

            info = None
            try:
                info = recorder.probe(url)
            except recorder.ProbeError as e:
                logger.warning("could not inspect stream before recording %s: %s", airing.key, e)
            fmt = recorder.choose_format(info, self.settings.audio_mode, self.settings.transcode_bitrate)
            dest = self.settings.recordings_dir / f"{airing.key}.{fmt.ext}"
            metadata = {
                "title": airing.title,
                "artist": airing.show.name,
                "album": self.settings.feed_title,
                "date": airing.local_start.strftime("%Y-%m-%d"),
            }
            logger.info("recording %s (%s), %d min left, attempt %d", airing.key, airing.title,
                        remaining.total_seconds() // 60, attempt)
            try:
                rec = recorder.record(url, fmt, seconds, dest, metadata)
            except recorder.RecordingError as e:
                self.last_failure = f"{airing.key}: {e}"
                logger.error("recording %s failed: %s", airing.key, e)
                if attempt < MAX_ATTEMPTS:
                    _time.sleep(RETRY_DELAY_SECONDS)
                continue

            episode = Episode(
                key=airing.key,
                slug=airing.show.slug,
                show=airing.show.name,
                title=airing.title,
                filename=rec.path.name,
                mime=fmt.mime,
                date=airing.start.isoformat(),
                duration_seconds=int(rec.duration) if rec.duration else seconds,
                file_size=rec.size,
                added_at=now_iso(),
            )
            self.catalogue.add(episode)
            return episode
        logger.error("giving up on %s after %d attempts", airing.key, MAX_ATTEMPTS)
        return None

    # -- test recording ----------------------------------------------------------------

    def start_test_recording(self, seconds: int = 60) -> Airing | None:
        """Record `seconds` from the default stream right now and add it to the combined feed."""
        now = datetime.now(UTC)
        show = Show(name="Test recording", slug="test", start=now.astimezone(self.tz).time().replace(second=0, microsecond=0),
                    end=(now + timedelta(seconds=seconds)).astimezone(self.tz).time(), days=frozenset(range(7)),
                    title="Test recording {date:%-d %b %H:%M}")
        local = now.astimezone(self.tz)
        airing = Airing(show=show, start=now, end=now + timedelta(seconds=seconds),
                        key=f"test-{local.strftime('%Y%m%d-%H%M%S')}", tz_name=self.settings.tz_name)
        with self._lock:
            if any(a.show.slug == "test" for a in self._in_progress.values()):
                return None
        # No end buffer and no "is it worth it" threshold: a test is short by design.
        self._aps.add_job(self._run, "date", run_date=now + timedelta(seconds=1),
                          args=[airing, 0, timedelta(seconds=5)], id=airing.key)
        logger.info("test recording requested: %ds from %s", seconds, self.settings.stream_url)
        return airing
