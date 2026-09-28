"""Offset-polling tailer. Buffers partial lines; resets on truncation."""
from __future__ import annotations
import logging, os
log = logging.getLogger(__name__)

class Tailer:
    def __init__(self, path, from_start=False, max_read=1 << 20, max_line=1 << 16):
        self.path, self.max_read, self.max_line = path, max_read, max_line
        self._from_start = from_start
        self._existed = os.path.exists(path)
        self._off: int | None = None
        self._buf = b""

    def poll(self) -> list[str]:
        try:
            size = os.stat(self.path).st_size
        except OSError:
            return []
        if self._off is None:  # first sight: skip old content unless asked
            self._off = 0 if (self._from_start or not self._existed) else size
        if size < self._off:
            log.warning("log truncated; restarting at 0")
            self._off, self._buf = 0, b""
        if size == self._off:
            return []
        with open(self.path, "rb") as f:
            f.seek(self._off)
            data = f.read(min(size - self._off, self.max_read))
        self._off += len(data)
        *whole, self._buf = (self._buf + data).split(b"\n")
        if len(self._buf) > self.max_line:
            log.warning("dropping over-long partial line")
            self._buf = b""
        out = (b.decode("utf-8", "replace").rstrip("\r") for b in whole)
        return [l for l in out if l.strip()]
