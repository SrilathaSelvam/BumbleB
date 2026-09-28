"""Adapters for Person B (detector), D (generator, sink). ASSUMED interfaces - confirm with owners:
 B: factory() -> obj; obj.evaluate(snapshot) -> (metric_dict, alert_dict|None) [or {"metric","alert"}]; optional obj.reset()
 D gen: factory(log_file) -> obj; async/sync obj.inject(scenario); optional start()/stop()/reset()
 D sink: factory() -> obj; obj.publish(alert_dict) sync|async -> "sent"/"failed"/"queued"/True/False/None(=sent); raise=failed
Spec format: 'package.module:attr'."""
from __future__ import annotations
import asyncio, importlib, inspect, logging
from . import contract
from .fallbacks import MockDetector, MockGenerator, MockSink

log = logging.getLogger(__name__)

def _import(spec):
    mod, _, attr = spec.partition(":")
    return getattr(importlib.import_module(mod), attr)

async def _maybe(fn, *a):
    r = fn(*a)
    return await r if inspect.isawaitable(r) else r

def normalize_detector_output(out):
    if isinstance(out, dict) and "metric" in out:
        return out["metric"], out.get("alert")
    if isinstance(out, (tuple, list)) and len(out) == 2:
        return out[0], out[1]
    if isinstance(out, dict):
        return out, None
    raise TypeError(f"unsupported detector output: {type(out).__name__}")

class DetectorAdapter:
    def __init__(self, impl, method, label):
        self.impl, self.method, self.label = impl, method, label
    def evaluate(self, snap):
        return normalize_detector_output(getattr(self.impl, self.method)(snap))
    def reset(self):
        fn = getattr(self.impl, "reset", None)
        if callable(fn):
            fn()

def load_detector(spec, method="evaluate"):
    if spec not in ("", "mock"):
        try:
            impl = _import(spec)()
            if not callable(getattr(impl, method, None)):
                raise AttributeError(f"no method {method!r}")
            return DetectorAdapter(impl, method, spec)
        except Exception as e:
            log.error("DETECTOR %r failed to load (%s) - USING MOCK", spec, e)
            return DetectorAdapter(MockDetector(), "evaluate", f"mock (fallback, {spec} failed)")
    return DetectorAdapter(MockDetector(), "evaluate", "mock")

class GeneratorAdapter:
    def __init__(self, impl, label):
        self.impl, self.label, self._bg = impl, label, set()
    async def _call(self, name, *a):
        fn = getattr(self.impl, name, None)
        if callable(fn):
            await _maybe(fn, *a)
    async def start(self): await self._call("start")
    async def stop(self): await self._call("stop")
    async def reset(self): await self._call("reset")
    async def inject(self, scenario):
        fn = self.impl.inject
        t = asyncio.create_task(fn(scenario) if inspect.iscoroutinefunction(fn) else asyncio.to_thread(fn, scenario))
        self._bg.add(t)
        def done(t):
            self._bg.discard(t)
            if not t.cancelled() and t.exception():
                log.error("generator inject failed: %r", t.exception())
        t.add_done_callback(done)
        await asyncio.sleep(0)   # let sync/async errors that raise immediately surface in logs

def load_generator(spec, log_file, background=True):
    if spec not in ("", "mock"):
        try:
            return GeneratorAdapter(_import(spec)(log_file), spec)
        except Exception as e:
            log.error("GENERATOR %r failed to load (%s) - USING MOCK", spec, e)
    return GeneratorAdapter(MockGenerator(log_file, background), "mock")

def normalize_status(r):
    if r is None or r is True: return "sent"
    if r is False: return "failed"
    return r if r in contract.AWS_STATUSES else "failed"

class SinkAdapter:
    def __init__(self, impl, method, timeout, label):
        self.impl, self.method, self.timeout, self.label = impl, method, timeout, label
    async def deliver(self, alert):
        """Returns new aws_status, or None if no sink (alert stays 'pending')."""
        if self.impl is None:
            return None
        fn = getattr(self.impl, self.method)
        try:
            co = fn(alert) if inspect.iscoroutinefunction(fn) else asyncio.to_thread(fn, alert)
            return normalize_status(await asyncio.wait_for(co, self.timeout))
        except Exception as e:
            log.error("sink failed for %s: %r", alert.get("id"), e)
            return "failed"

def load_sink(spec, method="publish", timeout=30.0):
    if spec in ("", "none"):
        return SinkAdapter(None, method, timeout, "none")
    if spec == "mock":
        return SinkAdapter(MockSink(), "publish", timeout, "mock")
    try:
        return SinkAdapter(_import(spec)(), method, timeout, spec)
    except Exception as e:
        log.error("SINK %r failed to load (%s) - alerts stay 'pending'", spec, e)
        return SinkAdapter(None, method, timeout, f"none (fallback, {spec} failed)")
