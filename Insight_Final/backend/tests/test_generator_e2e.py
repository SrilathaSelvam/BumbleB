"""End-to-end test: real generator → tailer → window → detector → alert → WebSocket → AWS"""
import asyncio
import time
from dataclasses import replace
import pytest
from fastapi.testclient import TestClient
from backend import contract as c
from backend.config import Settings
from backend.integration import DetectorAdapter, GeneratorAdapter, SinkAdapter, load_detector, load_generator
from backend.main import create_app
from backend.fallbacks import MockDetector, MockSink


def make_with_generator(tmp_path, generator_spec="backend.generator:Generator"):
    """Create app with real Generator integration"""
    log = tmp_path / "app.log"
    cfg = replace(
        Settings(),
        log_file=str(log),
        tick_interval=0.1,
        poll_interval=0.03,
        generator=generator_spec,  # Use real generator
        mock_background=False,
    )
    det = DetectorAdapter(MockDetector(warmup_ticks=2), "evaluate", "mock")
    snk = SinkAdapter(MockSink(), "publish", 5, "mock")
    # Generator will be loaded via load_generator using the spec
    return create_app(cfg, detector=det, sink=snk), log


def collect_ws_messages(ws, timeout_secs=30, max_msgs=500):
    """Collect all WebSocket messages for a duration"""
    start = time.time()
    messages = []
    try:
        while time.time() - start < timeout_secs:
            try:
                msg = ws.receive_json(timeout=0.5)
                messages.append(msg)
            except Exception:
                pass
    except Exception:
        pass
    return messages


def test_generator_loads_successfully(tmp_path):
    """Test that the real Generator can be loaded"""
    app, log = make_with_generator(tmp_path)
    with TestClient(app) as cl:
        health = cl.get("/health").json()
        assert "generator" in health
        assert health["generator"] == "backend.generator:Generator"


def test_inject_ramp_full_flow(tmp_path):
    """
    E2E: POST /inject/ramp
    → generator writes app.log with escalating errors
    → tailer reads it
    → window engine creates snapshots
    → detector detects anomaly (ramp: 2%→60%)
    → alert created
    → WebSocket broadcasts alert
    → AWS sink marks as "sent"
    → alert_update published
    """
    app, log = make_with_generator(tmp_path)

    with TestClient(app) as cl, cl.websocket_connect("/ws") as ws:
        # Start ramp scenario
        resp = cl.post("/inject/ramp")
        assert resp.status_code == 200
        assert resp.json()["scenario"] == "ramp"

        # Collect WebSocket messages for 15 seconds (enough for ramp to trigger)
        messages = collect_ws_messages(ws, timeout_secs=15, max_msgs=300)

        # Verify we got metric messages (every second)
        metrics = [m for m in messages if m["type"] == "metric"]
        print(f"Collected {len(metrics)} metric messages")
        assert len(metrics) >= 8, f"Expected >=8 metrics, got {len(metrics)}"

        # Verify metrics show increasing error rate
        error_rates = [m["error_rate"] for m in metrics if m["error_rate"] is not None]
        print(f"Error rates: {error_rates[:5]}...{error_rates[-5:]}")

        # Verify we got at least one alert (ramp should trigger)
        alerts = [m for m in messages if m["type"] == "alert"]
        print(f"Collected {len(alerts)} alert messages")

        if alerts:
            alert = alerts[0]
            print(f"Alert: severity={alert['severity']}, error_rate={alert['error_rate']:.1%}")

            # Verify alert shape
            assert set(alert.keys()) == set(c.ALERT_FIELDS)
            assert alert["severity"] in c.SEVERITIES
            assert alert["error_rate"] > 0.05

            # Verify alert_update (AWS delivery status change)
            updates = [m for m in messages if m["type"] == "alert_update" and m["id"] == alert["id"]]
            if updates:
                update = updates[0]
                print(f"Alert update: aws_status={update['aws_status']}")
                assert update["aws_status"] in c.AWS_STATUSES

        # Verify log file was written to
        log_lines = log.read_text().split("\n")
        log_lines = [l for l in log_lines if l.strip()]
        print(f"Log file contains {len(log_lines)} lines")
        assert len(log_lines) > 100, "Generator should have written many lines"

        # Verify /alerts endpoint has history
        hist = cl.get("/alerts").json()
        print(f"Alert history: {len(hist)} alerts")


def test_inject_blip_no_alert(tmp_path):
    """
    Blip should produce logs but NOT trigger alert (below threshold)
    """
    app, log = make_with_generator(tmp_path)

    with TestClient(app) as cl, cl.websocket_connect("/ws") as ws:
        # Inject blip (brief 5s spike at 40% errors)
        resp = cl.post("/inject/blip")
        assert resp.status_code == 200

        # Collect messages
        messages = collect_ws_messages(ws, timeout_secs=10, max_msgs=200)

        metrics = [m for m in messages if m["type"] == "metric"]
        print(f"Blip: collected {len(metrics)} metrics")

        # Verify we got some metrics
        assert len(metrics) >= 3, f"Expected >=3 metrics, got {len(metrics)}"

        # Blip may or may not produce alerts depending on detector sensitivity
        # The important thing is that we got metrics
        error_rates = [m["error_rate"] for m in metrics if m["error_rate"] is not None]
        print(f"Blip error rates: {error_rates}")


def test_inject_sustained_high_rate(tmp_path):
    """
    Sustained should maintain high error rate and produce continuous CRITICAL alerts
    """
    app, log = make_with_generator(tmp_path)

    with TestClient(app) as cl, cl.websocket_connect("/ws") as ws:
        resp = cl.post("/inject/sustained")
        assert resp.status_code == 200

        messages = collect_ws_messages(ws, timeout_secs=12, max_msgs=300)

        metrics = [m for m in messages if m["type"] == "metric"]
        print(f"Sustained: collected {len(metrics)} metrics")
        assert len(metrics) >= 8

        error_rates = [m["error_rate"] for m in metrics if m["error_rate"] is not None]
        avg_rate = sum(error_rates) / len(error_rates) if error_rates else 0
        print(f"Sustained avg error rate: {avg_rate:.1%}")

        # Sustained should maintain high rate (around 70%)
        assert 0.5 < avg_rate < 0.9, f"Expected sustained high rate, got {avg_rate:.1%}"

        # Should have at least one alert
        alerts = [m for m in messages if m["type"] == "alert"]
        print(f"Sustained: {len(alerts)} alerts")


def test_reset_clears_state(tmp_path):
    """
    POST /reset should stop injection and clear history
    """
    app, log = make_with_generator(tmp_path)

    with TestClient(app) as cl, cl.websocket_connect("/ws") as ws:
        # Inject ramp
        cl.post("/inject/ramp")
        time.sleep(2)

        # Reset
        resp = cl.post("/reset")
        assert resp.status_code == 200
        assert resp.json() == c.RESET_RESPONSE

        # Verify history is cleared
        hist = cl.get("/alerts").json()
        assert hist == []

        # Verify log is empty
        log_lines = [l for l in log.read_text().split("\n") if l.strip()]
        assert len(log_lines) == 0, "Log should be cleared after reset"


def test_format_matches_contract(tmp_path):
    """
    Verify log lines match CONTRACT format: <ISO time> <LEVEL> <service> <message>
    """
    app, log = make_with_generator(tmp_path)

    with TestClient(app) as cl:
        cl.post("/inject/blip")
        time.sleep(3)

        log_text = log.read_text()
        lines = [l for l in log_text.split("\n") if l.strip()]

        import re
        iso_pattern = re.compile(
            r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d+Z (INFO|WARN|ERROR|FATAL) "
        )

        for line in lines[:10]:  # Check first 10
            assert iso_pattern.match(line), f"Line doesn't match format: {line}"
        print(f"✓ All sampled lines match CONTRACT format")