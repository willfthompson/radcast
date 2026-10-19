"""ffmpeg and ffprobe wrappers: inspect a stream, choose an output format, capture it.

The format decision is the one piece of audio knowledge in the project. Stream-copying
keeps the broadcast quality and costs no CPU, but only MP3 and AAC-LC are safe to copy
into a podcast file: HE-AAC copied as-is plays silence in some apps until the listener
seeks, and Ogg/Opus are not supported by Apple Podcasts at all. Anything else is
transcoded to AAC-LC.
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger("radcast.recorder")

MIN_BYTES_PER_SECOND = 2048  # ~16 kbps: below this the stream can't have delivered real audio
MIN_FILE_SIZE = 16 * 1024
PROBE_TIMEOUT = 40
COPYABLE = {"mp3", "aac"}


class ProbeError(Exception):
    pass


class RecordingError(Exception):
    pass


@dataclass(frozen=True)
class StreamInfo:
    codec: str
    profile: str | None
    bitrate: int | None       # bits per second
    sample_rate: int | None
    channels: int | None
    variants: int = 1         # audio streams found; >1 usually means a master playlist

    @property
    def is_he_aac(self) -> bool:
        return self.codec == "aac" and bool(self.profile) and "HE" in self.profile.upper()

    def describe(self) -> str:
        name = self.codec.upper()
        if self.codec == "aac" and self.profile:
            name = f"AAC-{self.profile}" if self.profile.upper() == "LC" else self.profile
        parts = [name]
        if self.bitrate:
            parts.append(f"{round(self.bitrate / 1000)} kbps")
        if self.sample_rate:
            parts.append(f"{self.sample_rate / 1000:g} kHz")
        if self.channels:
            parts.append({1: "mono", 2: "stereo"}.get(self.channels, f"{self.channels}ch"))
        text = ", ".join(parts)
        if self.variants > 1:
            text += f" ({self.variants} variants in this playlist, pick one)"
        return text


@dataclass(frozen=True)
class OutputFormat:
    ext: str          # "m4a" or "mp3"
    muxer: str        # ffmpeg -f value; needed because we write to a .part file
    mime: str
    copy: bool
    bitrate: str | None
    reason: str

    @property
    def codec_args(self) -> list[str]:
        if self.copy:
            return ["-c:a", "copy"]
        return ["-c:a", "aac", "-b:a", self.bitrate or "128k"]


def _is_http(url: str) -> bool:
    return url.startswith(("http://", "https://"))


def _http_input_args(url: str) -> list[str]:
    if not _is_http(url):
        return []
    return [
        "-reconnect", "1", "-reconnect_streamed", "1", "-reconnect_delay_max", "30",
        "-rw_timeout", "30000000",  # microseconds: a stalled read errors out and reconnects
    ]


def probe(url: str, timeout: int = PROBE_TIMEOUT) -> StreamInfo:
    """Inspect the first audio stream at `url`. Raises ProbeError if nothing usable is there."""
    cmd = [
        "ffprobe", "-v", "error", "-hide_banner",
        *_http_input_args(url),
        "-select_streams", "a",
        "-show_entries", "stream=codec_name,profile,bit_rate,sample_rate,channels:stream_tags=variant_bitrate:format=bit_rate",
        "-of", "json", url,
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError:
        raise ProbeError("ffprobe not found: install ffmpeg") from None
    except subprocess.TimeoutExpired:
        raise ProbeError(f"no response from the stream within {timeout}s") from None
    if result.returncode != 0:
        detail = (result.stderr or "").strip().splitlines()
        raise ProbeError(detail[-1] if detail else f"ffprobe exited with code {result.returncode}")
    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError:
        raise ProbeError("unexpected ffprobe output") from None
    streams = data.get("streams") or []
    if not streams:
        raise ProbeError("no audio stream found at that URL")
    first = streams[0]

    def to_int(value) -> int | None:
        try:
            return int(value) if value not in (None, "", "N/A") else None
        except (TypeError, ValueError):
            return None

    bitrate = (
        to_int(first.get("bit_rate"))
        or to_int((first.get("tags") or {}).get("variant_bitrate"))
        or (to_int((data.get("format") or {}).get("bit_rate")) if len(streams) == 1 else None)
    )
    return StreamInfo(
        codec=str(first.get("codec_name", "unknown")).lower(),
        profile=first.get("profile") or None,
        bitrate=bitrate,
        sample_rate=to_int(first.get("sample_rate")),
        channels=to_int(first.get("channels")),
        variants=len(streams),
    )


_M4A = dict(ext="m4a", muxer="ipod", mime="audio/mp4")
_MP3 = dict(ext="mp3", muxer="mp3", mime="audio/mpeg")


def choose_format(info: StreamInfo | None, mode: str, bitrate: str) -> OutputFormat:
    """Decide between copying the stream and transcoding it. See the module docstring."""
    transcode = lambda reason: OutputFormat(**_M4A, copy=False, bitrate=bitrate, reason=reason)  # noqa: E731
    if mode == "transcode":
        return transcode(f"AUDIO_MODE=transcode: encoding to AAC-LC {bitrate}")
    if info is None:
        return transcode("could not inspect the stream, so encoding to AAC-LC to be safe")
    if info.codec not in COPYABLE:
        return transcode(f"{info.describe()} can't go in a podcast file as-is, encoding to AAC-LC {bitrate}")
    if mode == "auto" and info.is_he_aac:
        return transcode(f"{info.describe()} would start silent in some podcast apps if copied, encoding to AAC-LC {bitrate}")
    container = _MP3 if info.codec == "mp3" else _M4A
    return OutputFormat(**container, copy=True, bitrate=None, reason=f"copying {info.describe()} as-is")


@dataclass(frozen=True)
class Recording:
    path: Path
    size: int
    duration: float | None  # seconds, as measured in the finished file


def record(url: str, fmt: OutputFormat, seconds: int, dest: Path, metadata: dict[str, str]) -> Recording:
    """Capture `seconds` of `url` into `dest`.

    Writes to a .part file and renames on success, so the recordings directory only
    ever holds finished files. Raises RecordingError on failure; the .part is removed.
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_name(dest.name + ".part")
    cmd = [
        "ffmpeg", "-hide_banner", "-loglevel", "warning", "-nostdin", "-y",
        *_http_input_args(url),
        "-i", url,
        "-t", str(seconds),
        "-vn",
        *fmt.codec_args,
    ]
    if fmt.muxer == "ipod":
        cmd += ["-movflags", "+faststart"]
    else:
        cmd += ["-id3v2_version", "3"]
    for k, v in metadata.items():
        cmd += ["-metadata", f"{k}={v}"]
    cmd += ["-f", fmt.muxer, str(part)]

    logger.info("ffmpeg starting: %ds -> %s (%s)", seconds, dest.name, fmt.reason)
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=seconds + 120)
    except FileNotFoundError:
        raise RecordingError("ffmpeg not found: install ffmpeg") from None
    except subprocess.TimeoutExpired:
        _discard(part)
        raise RecordingError(f"ffmpeg did not finish within {seconds + 120}s") from None

    if result.returncode != 0:
        _discard(part)
        tail = (result.stderr or "").strip().splitlines()[-3:]
        raise RecordingError(f"ffmpeg exited with code {result.returncode}: {' | '.join(tail) or 'no output'}")

    size = part.stat().st_size if part.exists() else 0
    if size < minimum_size(seconds):
        _discard(part)
        raise RecordingError(f"output was only {size} bytes for {seconds}s, the stream probably never delivered audio")

    os.replace(part, dest)
    duration = media_duration(dest)
    if duration is not None and duration < seconds * 0.9:
        logger.warning("%s is shorter than planned: %.0fs of %ds (the stream ended early or dropped)",
                       dest.name, duration, seconds)
    return Recording(path=dest, size=size, duration=duration)


def minimum_size(seconds: int) -> int:
    """Smallest plausible output for a capture of `seconds`; anything smaller is treated as a failure."""
    return max(MIN_FILE_SIZE, seconds * MIN_BYTES_PER_SECOND)


def media_duration(path: Path) -> float | None:
    cmd = ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "json", str(path)]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        return float(json.loads(result.stdout)["format"]["duration"])
    except (OSError, subprocess.TimeoutExpired, ValueError, KeyError, TypeError):
        return None


def _discard(path: Path) -> None:
    try:
        path.unlink()
    except FileNotFoundError:
        pass
    except OSError as e:
        logger.warning("could not remove %s: %s", path, e)
