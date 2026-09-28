from backend.aws_sync import AwsSink

class CW:
    def __init__(s, fail=0): s.fail, s.events, s.made = fail, [], []
    def create_log_group(s, **k): s.made.append("g")
    def create_log_stream(s, **k): s.made.append("s")
    def put_log_events(s, **k):
        if s.fail: s.fail -= 1; raise RuntimeError("net")
        s.events.append(k)

class SNS:
    def __init__(s): s.sent = []
    def publish(s, **k): s.sent.append(k)

A = {"id": "alt-1", "severity": "HIGH"}

def test_sent_and_created_once():
    cw = CW(); s = AwsSink(cw, backoff=0, group="g", stream="s")
    assert s.publish(A) == "sent" and s.publish(A) == "sent"
    assert cw.made == ["g", "s"] and len(cw.events) == 2 and '"alt-1"' in cw.events[0]["logEvents"][0]["message"]

def test_retry_then_sent_and_failed():
    assert AwsSink(CW(fail=2), backoff=0).publish(A) == "sent"
    assert AwsSink(CW(fail=9), backoff=0).publish(A) == "failed"

def test_dry_run_and_sns():
    cw = CW(); assert AwsSink(cw, dry_run=True).publish(A) == "queued" and not cw.events
    sns = SNS(); s = AwsSink(CW(), sns, topic="arn:x", backoff=0)
    assert s.publish(A) == "sent" and sns.sent[0]["Subject"] == "[HIGH] Insight anomaly"
