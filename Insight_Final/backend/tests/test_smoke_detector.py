"""Smoke: log lines -> tailer/windows -> Person B Detector (via DETECTOR config) -> bus -> /ws."""
from dataclasses import replace
from fastapi.testclient import TestClient
from backend.config import Settings
from backend.detection.detector import Detector
from backend.fallbacks import MockGenerator, MockSink
from backend.integration import GeneratorAdapter, SinkAdapter
from backend import contract as c
from backend.main import create_app


def test_log_to_detector_to_ws_alert(tmp_path):
    log = tmp_path / "app.log"
    cfg = replace(Settings(), log_file=str(log), tick_interval=.02, poll_interval=.01)
    gen = GeneratorAdapter(MockGenerator(str(log), background=False), "mock")
    app = create_app(cfg, generator=gen, sink=SinkAdapter(MockSink(), "publish", 5, "mock"))
    rt = app.state.runtime
    assert isinstance(rt.detector.impl, Detector) and rt.detector.method == "process_snapshot"
    ok = "2026-01-01T00:00:00Z INFO svc ok\n" * 990 + "2026-01-01T00:00:00Z ERROR svc bad\n" * 10
    bad = "2026-01-01T00:00:00Z ERROR [PaymentGateway] boom\n" * 3000
    seen, sent_bad = [], False
    with TestClient(app) as cl, cl.websocket_connect("/ws") as ws:
        with open(log, "a") as f:
            f.write(ok)
        for _ in range(600):
            m = ws.receive_json()
            seen.append(m)
            if m["type"] == "metric" and m["warming_up"] is False and not sent_bad:
                assert tuple(m) == c.METRIC_FIELDS and m["baseline_mean"] is not None
                with open(log, "a") as f:
                    f.write(bad)
                sent_bad = True
            if any(x["type"] == "alert_update" for x in seen):
                break
        al = next(x for x in seen if x["type"] == "alert")
        upd = next(x for x in seen if x["type"] == "alert_update")
        assert tuple(al) == c.ALERT_FIELDS and al["severity"] == "CRITICAL"
        assert al["sample_messages"] and al["z_score"] >= 8
        assert upd["id"] == al["id"] and upd["aws_status"] == "sent"
        assert any(a["id"] == al["id"] for a in cl.get("/alerts").json())
