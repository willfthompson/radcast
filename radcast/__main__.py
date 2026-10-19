"""Command line: radcast serve | check | probe | record | plan."""
from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import __version__, logs, recorder, timers
from .config import ConfigError, load_settings, load_shows


def _settings():
    try:
        return load_settings()
    except ConfigError as e:
        print(f"Configuration problems:\n{e}", file=sys.stderr)
        sys.exit(2)


def cmd_serve(_args) -> None:
    settings = _settings()
    argv = [
        "gunicorn", "--bind", f"0.0.0.0:{settings.port}", "--workers", "1", "--threads", "8",
        "--timeout", "120", "--log-level", "warning", "radcast.web:create_app()",
    ]
    os.execvp(argv[0], argv)


def cmd_check(_args) -> int:
    settings = _settings()
    shows, problems = load_shows(settings)
    print(f"Timezone {settings.tz_name}, feed at {settings.feed_url}, audio mode {settings.audio_mode}")
    for show in shows:
        print(f"  ok   {show.name}: {timers.describe_rule(show)}")
    for problem in problems:
        print(f"  FAIL {problem}")
    if not shows:
        print("No valid shows.")
    return 1 if problems or not shows else 0


def cmd_probe(args) -> int:
    url = args.url or os.environ.get("STREAM_URL", "").strip()
    if not url:
        print("Give a URL or set STREAM_URL", file=sys.stderr)
        return 2
    print(f"Probing {url} ...")
    try:
        info = recorder.probe(url)
    except recorder.ProbeError as e:
        print(f"FAIL: {e}")
        return 1
    mode = os.environ.get("AUDIO_MODE", "auto") or "auto"
    fmt = recorder.choose_format(info, mode, os.environ.get("TRANSCODE_BITRATE", "128k") or "128k")
    print(f"OK: {info.describe()}")
    print(f"Recordings will be .{fmt.ext} files: {fmt.reason}")
    return 0


def cmd_record(args) -> int:
    url = args.url or os.environ.get("STREAM_URL", "").strip()
    if not url:
        print("Give --url or set STREAM_URL", file=sys.stderr)
        return 2
    try:
        info = recorder.probe(url)
    except recorder.ProbeError as e:
        print(f"Could not inspect the stream ({e}); will transcode")
        info = None
    fmt = recorder.choose_format(info, os.environ.get("AUDIO_MODE", "auto") or "auto",
                                 os.environ.get("TRANSCODE_BITRATE", "128k") or "128k")
    dest = Path(args.out) if args.out else Path(f"test-recording.{fmt.ext}")
    print(f"Recording {args.seconds}s to {dest} ({fmt.reason}) ...")
    try:
        rec = recorder.record(url, fmt, args.seconds, dest, {"title": "Radcast test recording"})
    except recorder.RecordingError as e:
        print(f"FAIL: {e}")
        return 1
    dur = f"{rec.duration:.1f}s" if rec.duration else "unknown length"
    print(f"OK: {rec.path} ({rec.size / 1024:.0f} KB, {dur})")
    return 0


def cmd_plan(args) -> int:
    settings = _settings()
    shows, problems = load_shows(settings)
    for problem in problems:
        print(f"WARNING {problem}", file=sys.stderr)
    now = datetime.now(timezone.utc)
    airings = timers.airings_between(shows, settings.tz, now - timedelta(hours=12), now + timedelta(days=args.days))
    if not airings:
        print(f"Nothing scheduled in the next {args.days} days.")
        return 0
    for a in airings:
        flag = "  <- on air now" if a.is_on_air(now) else ("  (finished)" if a.end <= now else "")
        print(f"{a.local_start:%a %d %b %H:%M} - {a.local_end:%H:%M}  {a.title}{flag}")
    return 0


def main(argv: list[str] | None = None) -> None:
    logs.setup_logging()
    parser = argparse.ArgumentParser(prog="radcast", description="Record a live stream on a timer and serve it as a podcast.")
    parser.add_argument("--version", action="version", version=f"radcast {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("serve", help="run the web app and scheduler (what the container runs)").set_defaults(fn=cmd_serve)
    sub.add_parser("check", help="validate .env and the shows file").set_defaults(fn=cmd_check)
    p = sub.add_parser("probe", help="inspect the stream and say how it would be recorded")
    p.add_argument("url", nargs="?")
    p.set_defaults(fn=cmd_probe)
    p = sub.add_parser("record", help="make a short test recording to a local file")
    p.add_argument("seconds", type=int, nargs="?", default=30)
    p.add_argument("--url")
    p.add_argument("--out")
    p.set_defaults(fn=cmd_record)
    p = sub.add_parser("plan", help="print the upcoming airings")
    p.add_argument("--days", type=int, default=7)
    p.set_defaults(fn=cmd_plan)

    args = parser.parse_args(argv)
    sys.exit(args.fn(args) or 0)


if __name__ == "__main__":
    main()
