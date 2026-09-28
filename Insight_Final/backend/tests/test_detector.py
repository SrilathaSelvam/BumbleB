import pytest
from backend.detection.detector import Detector
from backend.detection.parameters import Params


def snap(t, rate, total=1000, samples=("ERROR x",)):
    return {"ts": t, "total_lines": total, "error_lines": round(rate * total),
            "error_rate": rate, "sample_errors": list(samples)}


def warm(d, n=50, rate=0.02):
    for i in range(n):
        d.process_snapshot(snap(i, rate))
    return n


def feed(d, t0, rates, **kw):
    return [d.process_snapshot(snap(t0 + i, r, **kw)) for i, r in enumerate(rates)]


def alerts(rs):
    return [r["alert"] for r in rs if r["alert"]]


def test_normal_no_alert():
    d = Detector(); t = warm(d)
    assert not alerts(feed(d, t, [0.02, 0.021, 0.019] * 20))


def test_blip_no_alert():
    d = Detector(); t = warm(d)
    assert not alerts(feed(d, t, [0.02, 0.10, 0.02, 0.02, 0.02]))


def test_ramp_order():
    d = Detector(); t = warm(d)
    rs = feed(d, t, [0.04, 0.04, 0.05, 0.05, 0.07, 0.07, 0.07])
    assert [a["severity"] for a in alerts(rs)] == ["WARNING", "HIGH", "CRITICAL"]


def test_sustained_critical_with_cooldown():
    d = Detector(); t = warm(d)
    a = alerts(feed(d, t, [0.30] * 100))
    assert len(a) == 4 and all(x["severity"] == "CRITICAL" for x in a)
    ts = [x["ts"] for x in a]
    assert all(b - c >= 30 for c, b in zip(ts, ts[1:]))


def test_warmup():
    d = Detector()
    rs = feed(d, 0, [0.5] * 45)
    assert all(r["metric"]["warming_up"] and r["metric"]["baseline_mean"] is None
               and r["metric"]["baseline_upper"] is None and not r["alert"] for r in rs)
    r = d.process_snapshot(snap(45, 0.5))
    assert r["metric"]["warming_up"] is False and r["metric"]["baseline_mean"] is not None


def test_baseline_freezes():
    d = Detector(); t = warm(d); m = d.mean
    feed(d, t, [0.30] * 40)
    assert d.mean == pytest.approx(m)


def test_min_lines_guard():
    d = Detector(); t = warm(d); m = d.mean
    rs = feed(d, t, [0.5] * 5, total=50)
    assert not alerts(rs) and d.mean == m


def test_error_floor_guard():
    d = Detector(Params(std_floor=0.0001)); t = warm(d, rate=0.0)
    assert not alerts(feed(d, t, [0.0005] * 5))   # z=5 but below floor


def test_persistence_guard():
    d = Detector(); t = warm(d)
    rs = feed(d, t, [0.10, 0.10])
    assert rs[0]["alert"] is None and rs[1]["alert"] is not None


def test_std_floor_and_contract_fields():
    d = Detector(); t = warm(d)
    a = alerts(feed(d, t, [0.10, 0.10], samples=("E1", "E2")))[0]
    assert a["baseline_std"] == d.p.std_floor
    assert a["sample_messages"] == ["E1", "E2"] and a["aws_status"] == "pending"
    assert a["z_score"] == pytest.approx((0.10 - a["baseline_mean"]) / a["baseline_std"])
