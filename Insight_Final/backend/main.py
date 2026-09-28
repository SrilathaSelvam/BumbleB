"""Entry point: uvicorn backend.main:app --host 0.0.0.0 --port 8000"""
from __future__ import annotations
import asyncio, logging
from collections import Counter, deque
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from . import contract
from .bus import Bus
from .config import Settings
from .integration import load_detector, load_generator, load_sink
from .parser import parse_line
from .tailer import Tailer
from .windows import WindowEngine
from .ws import router as ws_router

log = logging.getLogger("backend")

class Runtime:
    def __init__(self, cfg, detector, generator, sink):
        self.cfg, self.detector, self.generator, self.sink = cfg, detector, generator, sink
        self.bus = Bus()
        self.windows = WindowEngine(cfg.window_seconds, cfg.time_source)
        self.tailer = Tailer(cfg.log_file, cfg.tail_from_start)
        self.history = deque(maxlen=cfg.history_size)
        self.stats, self._tasks, self._bg = Counter(), [], set()

    def ingest(self, lines):
        for line in lines:
            self.stats["lines_read"] += 1
            p = parse_line(line)
            if p is None:
                self.stats["lines_malformed"] += 1
                if self.stats["lines_malformed"] <= 5:
                    log.warning("malformed log line skipped: %r", line[:120])
            else:
                self.windows.add(p)

    def tick(self):
        snap = self.windows.snapshot()
        self.stats["ticks"] += 1
        try:
            m_raw, a_raw = self.detector.evaluate(snap)
        except Exception:
            self.stats["detector_errors"] += 1
            if self.stats["detector_errors"] % 30 == 1:
                log.exception("detector failed; sending warm-up metric")
            m_raw, a_raw = {"warming_up": True}, None
        self.bus.publish(contract.make_metric(m_raw, snap))
        if a_raw:
            alert = contract.make_alert(a_raw, snap)
            self.history.append(alert)
            self.stats["alerts"] += 1
            self.bus.publish(dict(alert))
            t = asyncio.create_task(self._deliver(alert))
            self._bg.add(t)
            t.add_done_callback(self._bg.discard)

    async def _deliver(self, alert):
        status = await self.sink.deliver(dict(alert))
        if status is not None:
            alert["aws_status"] = status
            self.bus.publish(contract.make_alert_update(alert["id"], status))

    async def _tail_loop(self):
        while True:
            try:
                self.ingest(self.tailer.poll())
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("tail loop error")
            await asyncio.sleep(self.cfg.poll_interval)

    async def _tick_loop(self):
        loop, nxt = asyncio.get_running_loop(), asyncio.get_running_loop().time()
        while True:
            try:
                self.tick()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("tick error")
            nxt += self.cfg.tick_interval
            await asyncio.sleep(max(0, nxt - loop.time()))

    async def start(self):
        await self.generator.start()
        self._tasks = [asyncio.create_task(self._tail_loop()), asyncio.create_task(self._tick_loop())]

    async def stop(self):
        for t in self._tasks:
            t.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        await self.generator.stop()

    async def reset(self):
        self.windows.reset()
        self.detector.reset()
        self.history.clear()
        await self.generator.reset()

def create_app(cfg=None, *, detector=None, generator=None, sink=None) -> FastAPI:
    cfg = cfg or Settings.from_env()
    logging.basicConfig(level=cfg.log_level, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    rt = Runtime(cfg, detector or load_detector(cfg.detector, cfg.detector_method),
                 generator or load_generator(cfg.generator, cfg.log_file, cfg.mock_background),
                 sink or load_sink(cfg.sink, cfg.sink_method, cfg.sink_timeout))

    @asynccontextmanager
    async def lifespan(app):
        log.info("detector=%s generator=%s sink=%s log=%s", rt.detector.label, rt.generator.label, rt.sink.label, cfg.log_file)
        await rt.start()
        yield
        await rt.stop()

    app = FastAPI(title="Insight backend", lifespan=lifespan)
    app.state.runtime = rt
    app.add_middleware(CORSMiddleware, allow_origins=list(cfg.cors_origins), allow_methods=["*"], allow_headers=["*"])
    app.include_router(ws_router)

    @app.get("/alerts")
    async def alerts():
        return contract.alerts_response(rt.history)

    @app.post("/inject/{scenario}")
    async def inject(scenario: str):
        if scenario not in contract.SCENARIOS:
            raise HTTPException(404, f"unknown scenario; use one of {list(contract.SCENARIOS)}")
        try:
            await rt.generator.inject(scenario)
        except Exception as e:
            log.exception("inject failed")
            raise HTTPException(500, f"inject failed: {e}")
        return contract.inject_response(scenario)

    @app.post("/reset")
    async def reset():
        await rt.reset()
        return contract.RESET_RESPONSE

    @app.get("/health")   # extra, debug only
    async def health():
        return {"detector": rt.detector.label, "generator": rt.generator.label, "sink": rt.sink.label,
                "subscribers": rt.bus.count, "stats": dict(rt.stats)}
    return app

app = create_app()

if __name__ == "__main__":
    import uvicorn
    s = Settings.from_env()
    uvicorn.run("backend.main:app", host=s.host, port=s.port)
