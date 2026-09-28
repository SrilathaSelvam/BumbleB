"""SINGLE SOURCE OF TRUTH for wire shapes. Mirrors CONTRACT.md v1.0 (frozen).
Change CONTRACT.md -> change ONLY this file."""
from __future__ import annotations
import logging, time, uuid
from datetime import datetime, timezone

log = logging.getLogger(__name__)
SCENARIOS = ("blip", "ramp", "sustained")                       # CONTRACT 3.2
SEVERITIES = ("WARNING", "HIGH", "CRITICAL")                    # CONTRACT 2.2
AWS_STATUSES = ("pending", "sent", "failed", "queued")          # CONTRACT 2.3
METRIC_FIELDS = ("type", "ts", "error_rate", "baseline_mean", "baseline_upper",
                 "total_lines", "error_lines", "warming_up")    # CONTRACT 2.1
ALERT_FIELDS = ("type", "id", "ts", "severity", "error_rate", "baseline_mean", "baseline_std",
                "z_score", "total_lines", "error_lines", "sample_messages", "aws_status")  # 2.2
RESET_RESPONSE = {"status": "ok", "message": "Detector state reset."}     # CONTRACT 3.3
_warned: set = set()

def format_ts(epoch: float) -> str:
    """ISO 8601 UTC with ms and Z, e.g. 2026-09-28T14:00:00.000Z"""
    return datetime.fromtimestamp(epoch, timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")

def new_alert_id() -> str:
    return f"alt-{int(time.time())}-{uuid.uuid4().hex[:6]}"

def _shape(kind, fields, raw, fallback):
    out = {}
    for f in fields:
        if f == "type":
            out[f] = kind
        elif f in raw:
            out[f] = raw[f]
        elif f in fallback:
            out[f] = fallback[f]
        else:
            out[f] = None
            if (kind, f) not in _warned:
                _warned.add((kind, f))
                log.warning("detector output for %r is missing field %r (sent as null)", kind, f)
    return out

def make_metric(raw: dict, snap: dict) -> dict:
    fb = {k: snap[k] for k in ("ts", "error_rate", "total_lines", "error_lines")}
    m = _shape("metric", METRIC_FIELDS, raw or {}, fb)
    if m["warming_up"] is not False:          # must be null during warm-up (2.1)
        m["baseline_mean"] = m["baseline_upper"] = None
        m["warming_up"] = True
    return m

def make_alert(raw: dict, snap: dict) -> dict:
    fb = {k: snap[k] for k in ("ts", "error_rate", "total_lines", "error_lines")}
    fb.update(sample_messages=list(snap.get("sample_errors", [])), aws_status="pending", id=new_alert_id())
    a = _shape("alert", ALERT_FIELDS, raw or {}, fb)
    if a["severity"] not in SEVERITIES:
        log.warning("alert has invalid severity %r", a["severity"])
    if a["aws_status"] not in AWS_STATUSES:
        a["aws_status"] = "pending"
    return a

def make_alert_update(alert_id: str, aws_status: str) -> dict:
    return {"type": "alert_update", "id": alert_id, "aws_status": aws_status}

def alerts_response(alerts) -> list:            # 3.1: JSON array, order unspecified
    return list(reversed(list(alerts)))

def inject_response(scenario: str) -> dict:     # 3.2
    return {"status": "ok", "scenario": scenario, "message": "Scenario triggered successfully."}
