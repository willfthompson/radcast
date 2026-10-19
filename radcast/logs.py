"""Logging to stdout plus an in-memory ring buffer for the /logs page."""
from __future__ import annotations

import logging
from collections import deque

LOG_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

buffer: deque[str] = deque(maxlen=400)


class _BufferHandler(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:
        buffer.append(self.format(record))


def setup_logging(level: int = logging.INFO) -> None:
    root = logging.getLogger()
    if any(isinstance(h, _BufferHandler) for h in root.handlers):
        return
    root.setLevel(level)
    formatter = logging.Formatter(LOG_FORMAT, datefmt=DATE_FORMAT)
    for handler in (logging.StreamHandler(), _BufferHandler()):
        handler.setFormatter(formatter)
        root.addHandler(handler)
    logging.getLogger("apscheduler").setLevel(logging.WARNING)
