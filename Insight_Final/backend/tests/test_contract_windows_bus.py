import asyncio
from backend import contract as c
from backend.bus import Bus
from backend.parser import parse_line
from backend.windows import WindowEngine

SNAP = {"ts": "T", "error_rate": .1, "total_lines": 100, "error_lines": 10, "sample_errors": ["ERROR [a] x"]}

def test_metric_shape_and_warmup_nulls():
    m = c.make_metric({"baseline_mean": .02, "baseline_upper": .1, "warming_up": True}, SNAP)
    assert tuple(m) == c.METRIC_FIELDS and m["baseline_mean"] is None and m["baseline_upper"] is None
    m = c.make_metric({"baseline_mean": .02, "baseline_upper": .1, "warming_up": False}, SNAP)
    assert m["baseline_upper"] == .1 and m["type"] == "metric" and m["total_lines"] == 100

def test_alert_shape_defaults():
    a = c.make_alert({"severity": "HIGH", "baseline_mean": .02, "baseline_std": .01, "z_score": 8}, SNAP)
    assert tuple(a) == c.ALERT_FIELDS and a["id"].startswith("alt-") and a["aws_status"] == "pending"
    assert a["sample_messages"] == ["ERROR [a] x"] and a["type"] == "alert"

def test_misc_shapes():
    assert c.make_alert_update("i", "queued") == {"type": "alert_update", "id": "i", "aws_status": "queued"}
    assert c.inject_response("ramp") == {"status": "ok", "scenario": "ramp", "message": "Scenario triggered successfully."}
    assert c.RESET_RESPONSE == {"status": "ok", "message": "Detector state reset."}
    assert c.format_ts(0) == "1970-01-01T00:00:00.000Z"

def test_windows():
    now = 1000.0
    w = WindowEngine(60)
    assert w.snapshot(now)["error_rate"] == 0.0 and w.snapshot(now)["total_lines"] == 0   # empty
    for lv in ("INFO", "ERROR", "FATAL", "WARN"):
        w.add(parse_line(f"2026-01-01T00:00:00Z {lv} s m"), now)
    s = w.snapshot(now)
    assert (s["total_lines"], s["error_lines"], s["error_rate"]) == (4, 2, .5)
    assert s["sample_errors"][-1] == "FATAL [s] m"
    assert w.snapshot(now + 59)["total_lines"] == 4
    assert w.snapshot(now + 60)["total_lines"] == 0     # expired
    w.add(parse_line("2026-01-01T00:00:00Z ERROR s m"), now); w.reset()
    assert w.snapshot(now)["total_lines"] == 0

def test_bus_never_blocks_and_drops_oldest():
    async def go():
        b = Bus(3); q = b.subscribe()
        for i in range(10):
            b.publish(i)
        got = [q.get_nowait() for _ in range(3)]
        b.unsubscribe(q)
        return got, b.count
    assert asyncio.run(go()) == ([7, 8, 9], 0)
