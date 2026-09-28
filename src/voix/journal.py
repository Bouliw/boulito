"""Local log: commands, actions and the latency of each step. Never any audio.

One JSON Lines file per day in logs/, deleted after `retention_days` days.
"""

import json
import os
import time
from contextlib import contextmanager
from datetime import date, datetime, timedelta
from pathlib import Path

from . import config


class Timings:
    """Times the steps of a command (stt, router, llm, action), in ms."""

    def __init__(self) -> None:
        self.steps: dict[str, float] = {}

    @contextmanager
    def step(self, name: str):
        start = time.perf_counter()
        try:
            yield
        finally:
            self.steps[name] = round((time.perf_counter() - start) * 1000, 1)


def log_dir() -> Path:
    return config.project_path(config.load()["journal"]["dir"])


def ensure_log_dir() -> Path:
    """The logs folder, readable by the user only (0700): the logs quote what was said."""
    folder = log_dir()
    folder.mkdir(mode=0o700, parents=True, exist_ok=True)
    if folder.stat().st_mode & 0o777 != 0o700:
        folder.chmod(0o700)  # created by an older version
    return folder


def private(path, flags: int) -> int:
    """opener for open(): the file is readable by the user only (0600), even if it already existed."""
    fd = os.open(path, flags, 0o600)
    os.fchmod(fd, 0o600)
    return fd


def write(event: dict) -> None:
    """Adds an event to today's log."""
    folder = ensure_log_dir()
    line = {"ts": datetime.now().isoformat(timespec="milliseconds"), **event}
    with open(folder / f"{date.today()}.jsonl", "a", encoding="utf-8", opener=private) as f:
        f.write(json.dumps(line, ensure_ascii=False) + "\n")


def purge() -> int:
    """Deletes logs older than the retention period. Returns how many."""
    folder = log_dir()
    if not folder.exists():
        return 0
    limit = date.today() - timedelta(days=config.load()["journal"]["retention_days"])
    removed = 0
    for f in [*folder.glob("*.jsonl"), *folder.glob("*.log")]:  # logs, app output, downloads
        try:
            day = date.fromisoformat(f.name[:10])
        except ValueError:
            continue
        if day < limit:
            f.unlink()
            removed += 1
    return removed
