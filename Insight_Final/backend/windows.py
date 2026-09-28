"""1s buckets, sliding window. Emits the S2 snapshot."""
from __future__ import annotations
import time
from collections import deque
from . import contract

class WindowEngine:
    def __init__(self, window=60, time_source="arrival", sample_size=5, clock=time.time):
        self.window, self.time_source, self.sample_size, self._clock = window, time_source, sample_size, clock
        self.reset()

    def reset(self):
        self._b: dict[int, list[int]] = {}
        self._s: deque = deque(maxlen=200)

    def _prune(self, now_sec):
        cut = now_sec - self.window + 1
        for k in [k for k in self._b if k < cut]:
            del self._b[k]
        while self._s and self._s[0][0] < cut:
            self._s.popleft()

    def add(self, p, now=None):
        now = self._clock() if now is None else now
        sec = int(p.ts if self.time_source == "log" else now)
        b = self._b.setdefault(sec, [0, 0])
        b[0] += 1
        if p.is_error:
            b[1] += 1
            self._s.append((sec, f"{p.level} [{p.service}] {p.message}".rstrip()))
        self._prune(int(now))

    def snapshot(self, now=None) -> dict:
        now = self._clock() if now is None else now
        ns = int(now)
        self._prune(ns)
        tot = sum(v[0] for k, v in self._b.items() if k <= ns)
        err = sum(v[1] for k, v in self._b.items() if k <= ns)
        samples = [m for s, m in self._s if s <= ns][-self.sample_size:]
        return {"ts": contract.format_ts(now), "ts_epoch": now, "total_lines": tot,
                "error_lines": err, "error_rate": (err / tot) if tot else 0.0, "sample_errors": samples}
