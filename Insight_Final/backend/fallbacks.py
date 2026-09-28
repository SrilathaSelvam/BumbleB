"""Stand-ins so the backend always starts. NOT real detection / AWS."""
from __future__ import annotations
import asyncio, logging, random
from datetime import datetime, timezone

log = logging.getLogger(__name__)
_RANK = {"WARNING": 1, "HIGH": 2, "CRITICAL": 3}

class MockDetector:
    """Fixed-threshold stub (no baseline learning, no guards). Real logic = Person B."""
    def __init__(self, warmup_ticks=5, min_lines=20):
        self.warmup, self.min_lines = warmup_ticks, min_lines
        self.reset()
    def reset(self):
        self.n, self.last = 0, 0
    def evaluate(self, s):
        self.n += 1
        r, warm = s["error_rate"], self.n <= self.warmup
        m = {"ts": s["ts"], "error_rate": r, "baseline_mean": 0.02, "baseline_upper": 0.10,
             "total_lines": s["total_lines"], "error_lines": s["error_lines"], "warming_up": warm}
        sev = "CRITICAL" if r >= .5 else "HIGH" if r >= .25 else "WARNING" if r >= .10 else None
        if warm or s["total_lines"] < self.min_lines:
            sev = None
        if sev is None:
            self.last = 0
            return m, None
        if _RANK[sev] <= self.last:
            return m, None
        self.last = _RANK[sev]
        return m, {"severity": sev, "error_rate": r, "baseline_mean": 0.02, "baseline_std": 0.01,
                   "z_score": round((r - 0.02) / 0.01, 2)}

class MockSink:
    async def publish(self, alert):
        log.warning("MOCK sink: alert %s NOT actually delivered anywhere", alert.get("id"))
        await asyncio.sleep(0.2)
        return "sent"

_SVC = ("PaymentGateway", "CheckoutController", "OrderProcessingService")
_SCEN = {"blip": (5, lambda i, n: .6), "ramp": (90, lambda i, n: .02 + .9 * i / n), "sustained": (90, lambda i, n: .5)}

class MockGenerator:
    """Writes normal traffic + scenarios to the log. Real generator = Person D."""
    def __init__(self, log_file, background=True, tick=1.0, lines_per_tick=10, seed=None):
        self.path, self.background, self.tick, self.n = log_file, background, tick, lines_per_tick
        self.rng, self._bg, self._inc = random.Random(seed), None, None
    def _write(self, p_err):
        ts = datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
        rows = []
        for _ in range(self.n):
            svc = self.rng.choice(_SVC)
            if self.rng.random() < p_err:
                rows.append(f"{ts} {'FATAL' if self.rng.random() < .1 else 'ERROR'} {svc} request failed")
            else:
                rows.append(f"{ts} {'WARN' if self.rng.random() < .03 else 'INFO'} {svc} request ok")
        with open(self.path, "a") as f:
            f.write("\n".join(rows) + "\n")
    def _active(self):
        return self._inc is not None and not self._inc.done()
    async def _normal(self):
        while True:
            if not self._active():
                self._write(0.01)
            await asyncio.sleep(self.tick)
    async def _run(self, name):
        steps, fn = _SCEN[name]
        for i in range(steps):
            self._write(fn(i, steps))
            await asyncio.sleep(self.tick)
    async def start(self):
        if self.background and self._bg is None:
            self._bg = asyncio.create_task(self._normal())
    async def inject(self, name):
        await self.reset()
        self._inc = asyncio.create_task(self._run(name))
    async def reset(self):
        if self._active():
            self._inc.cancel()
        self._inc = None
    async def stop(self):
        await self.reset()
        if self._bg:
            self._bg.cancel()
            self._bg = None
