"""A small cross-process lock (lock file created with O_EXCL).

`--watch`, the dashboard's "Process new emails" and a manual `python agent.py` can run at the same
time. Only one of them may process emails and rewrite outbox.json at once; the others wait, then see
that the files are already processed (--only-new) and skip them.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path


class LockBusy(Exception):
    pass


class FileLock:
    def __init__(self, path: Path, timeout: float = 120.0, stale_after: float = 900.0, poll: float = 0.5):
        self.path = Path(path)
        self.timeout, self.stale_after, self.poll = timeout, stale_after, poll
        self.held = False

    def _stale(self) -> bool:
        try:
            age = time.time() - self.path.stat().st_mtime
        except FileNotFoundError:
            return False
        return age > self.stale_after  # a crashed run never released it

    def acquire(self) -> None:
        deadline = time.monotonic() + self.timeout
        self.path.parent.mkdir(parents=True, exist_ok=True)
        while True:
            try:
                fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                with os.fdopen(fd, "w") as fh:
                    json.dump({"pid": os.getpid(), "since": time.strftime("%Y-%m-%dT%H:%M:%S")}, fh)
                self.held = True
                return
            except FileExistsError:
                if self._stale():
                    self.path.unlink(missing_ok=True)
                    continue
                if time.monotonic() >= deadline:
                    raise LockBusy(f"another agent run holds {self.path.name} ({self.owner()})") from None
                time.sleep(self.poll)

    def owner(self) -> str:
        try:
            info = json.loads(self.path.read_text(encoding="utf-8"))
            return f"pid {info.get('pid')} since {info.get('since')}"
        except Exception:
            return "unknown owner"

    def release(self) -> None:
        if self.held:
            self.path.unlink(missing_ok=True)
            self.held = False

    def __enter__(self) -> FileLock:
        self.acquire()
        return self

    def __exit__(self, *exc) -> None:
        self.release()
