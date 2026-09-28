import asyncio, time
from dataclasses import replace
import pytest
from fastapi.testclient import TestClient
from backend import contract as c
from backend.config import Settings
from backend.fallbacks import MockDetector, MockGenerator
from backend.integration import (DetectorAdapter, GeneratorAdapter, SinkAdapter, load_detector,
                                 normalize_detector_output, normalize_status)
from backend.main import create_app
from backend.fallbacks import MockSink

def make(tmp_path, sink=True, detector=None, **kw):
    log = tmp_path / "app.log"
    cfg = replace(Settings(), log_file=str(log), tick_interval=.1, poll_interval=.03, **kw)
    gen = GeneratorAdapter(MockGenerator(str(log), background=False, tick=.02, lines_per_tick=30, seed=1), "mock")
    det = detector or DetectorAdapter(MockDetector(warmup_ticks=2), "evaluate", "mock")
    snk = SinkAdapter(MockSink() if sink else None, "publish", 5, "mock")
    return create_app(cfg, detector=det, generator=gen, sink=snk), log

def collect(ws, until, n=200):
    out = []
    for _ in range(n):
        out.append(ws.receive_json())
        if until(out):
            return out
    raise AssertionError("condition not met")

def test_rest_cors_and_errors(tmp_path):
    app, _ = make(tmp_path)
    with TestClient(app) as cl:
        h = {"Origin": "http://localhost:5500"}
        r = cl.get("/alerts", headers=h)
        assert r.status_code == 200 and r.json() == [] and r.headers["access-control-allow-origin"] == "*"
        assert cl.post("/reset", headers=h).json() == {"status": "ok", "message": "Detector state reset."}
        assert cl.post("/inject/ramp").json() == c.inject_response("ramp")
        assert cl.post("/inject/nope").status_code == 404
        pre = cl.options("/inject/blip", headers={**h, "Access-Control-Request-Method": "POST"})
        assert pre.headers["access-control-allow-origin"] == "*"

def test_ws_metrics_empty_log_then_growing_log(tmp_path):
    app, log = make(tmp_path)
    with TestClient(app) as cl, cl.websocket_connect("/ws") as ws:
        m = ws.receive_json()                                   # empty/missing log still streams
        assert m["type"] == "metric" and tuple(m) == c.METRIC_FIELDS and m["total_lines"] == 0
        assert m["warming_up"] is True and m["baseline_mean"] is None
        with open(log, "a") as f:
            f.write("2026-01-01T00:00:00Z INFO s ok\n2026-01-01T00:00:00Z ERROR s bad\nGARBAGE\n")
        got = collect(ws, lambda o: o[-1]["total_lines"] == 2)[-1]
        assert got["error_lines"] == 1 and got["error_rate"] == .5

def test_inject_sustained_alert_flow(tmp_path):
    app, _ = make(tmp_path)
    with TestClient(app) as cl, cl.websocket_connect("/ws") as ws:
        assert cl.post("/inject/sustained").status_code == 200
        msgs = collect(ws, lambda o: any(x["type"] == "alert_update" for x in o), 400)
        al = next(x for x in msgs if x["type"] == "alert")
        assert tuple(al) == c.ALERT_FIELDS and al["aws_status"] == "pending" and al["severity"] in c.SEVERITIES
        upd = next(x for x in msgs if x["type"] == "alert_update")
        assert upd["id"] == al["id"] and upd["aws_status"] == "sent"
        hist = cl.get("/alerts").json()
        assert hist and hist[0]["id"] == al["id"] and hist[0]["aws_status"] == "sent"
        cl.post("/reset")
        assert cl.get("/alerts").json() == []

def test_blip_no_alert(tmp_path):
    app, log = make(tmp_path)
    # blip = 5 ticks of 60% errors on 30 lines; stays below the 10% mock threshold only with normal traffic,
    # so pre-load normal traffic first
    with TestClient(app) as cl, cl.websocket_connect("/ws") as ws:
        with open(log, "a") as f:
            f.write("2026-01-01T00:00:00Z INFO s ok\n" * 3000)
        collect(ws, lambda o: o[-1]["total_lines"] >= 3000)
        cl.post("/inject/blip")
        msgs = collect(ws, lambda o: o[-1]["total_lines"] >= 3000 + 150, 100)
        assert not [m for m in msgs if m["type"] == "alert"]

def test_detector_failure_keeps_stream_alive(tmp_path):
    class Boom:
        def evaluate(self, s): raise RuntimeError("x")
    app, _ = make(tmp_path, detector=DetectorAdapter(Boom(), "evaluate", "boom"))
    with TestClient(app) as cl, cl.websocket_connect("/ws") as ws:
        m = collect(ws, lambda o: len(o) >= 3)
        assert all(x["type"] == "metric" and x["warming_up"] is True for x in m)

def test_no_sink_leaves_pending(tmp_path):
    app, _ = make(tmp_path, sink=False)
    with TestClient(app) as cl, cl.websocket_connect("/ws") as ws:
        cl.post("/inject/sustained")
        msgs = collect(ws, lambda o: any(x["type"] == "alert" for x in o), 400)
        time.sleep(.5)
        assert cl.get("/alerts").json()[0]["aws_status"] == "pending"

def test_adapters():
    assert normalize_detector_output(({"a": 1}, None)) == ({"a": 1}, None)
    assert normalize_detector_output({"metric": {"a": 1}, "alert": {"b": 2}}) == ({"a": 1}, {"b": 2})
    assert normalize_detector_output({"a": 1}) == ({"a": 1}, None)
    with pytest.raises(TypeError): normalize_detector_output(5)
    assert [normalize_status(x) for x in (True, None, False, "queued", "weird")] == ["sent", "sent", "failed", "queued", "failed"]
    assert load_detector("no.such:thing").label.startswith("mock (fallback")
    assert load_detector("backend.fallbacks:MockDetector").label == "backend.fallbacks:MockDetector"
