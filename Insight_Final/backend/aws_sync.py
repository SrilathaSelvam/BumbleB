"""AWS sink: CloudWatch Logs (+ optional SNS). Plugs in via SINK=backend.aws_sync:AwsSink
publish(alert) -> "sent" | "failed" | "queued"(dry-run). Credentials: standard AWS env vars only.
Env: AWS_REGION, CW_LOG_GROUP(/insight/alerts), CW_LOG_STREAM(alerts), SNS_TOPIC_ARN(optional), AWS_DRY_RUN(0/1)
Check: python -m backend.aws_sync   (publishes one dummy alert)"""
from __future__ import annotations
import json, logging, os, threading, time

log = logging.getLogger(__name__)

def _code(e):
    return getattr(e, "response", {}).get("Error", {}).get("Code", "")

class AwsSink:
    def __init__(self, cw=None, sns=None, *, dry_run=None, region=None, group=None,
                 stream=None, topic=None, retries=3, backoff=0.5):
        g = os.environ.get
        self.dry_run = dry_run if dry_run is not None else g("AWS_DRY_RUN", "0").lower() in ("1", "true", "yes")
        self.region = region or g("AWS_REGION") or g("AWS_DEFAULT_REGION")
        self.group, self.stream = group or g("CW_LOG_GROUP", "/insight/alerts"), stream or g("CW_LOG_STREAM", "alerts")
        self.topic = topic or g("SNS_TOPIC_ARN")
        self.retries, self.backoff = retries, backoff
        self._cw, self._sns, self._ready, self._lock = cw, sns, False, threading.Lock()

    def _client(self, attr, service):
        if getattr(self, attr) is None:
            import boto3  # lazy: not needed for dry-run/tests
            setattr(self, attr, boto3.client(service, region_name=self.region))
        return getattr(self, attr)

    def _ensure(self):
        with self._lock:
            if self._ready:
                return
            cw = self._client("_cw", "logs")
            for fn, kw in ((cw.create_log_group, {"logGroupName": self.group}),
                           (cw.create_log_stream, {"logGroupName": self.group, "logStreamName": self.stream})):
                try:
                    fn(**kw)
                except Exception as e:
                    if _code(e) != "ResourceAlreadyExistsException":
                        raise
            self._ready = True

    def _to_cw(self, alert, msg):
        self._ensure()
        self._client("_cw", "logs").put_log_events(
            logGroupName=self.group, logStreamName=self.stream,
            logEvents=[{"timestamp": int(time.time() * 1000), "message": msg}])

    def _to_sns(self, alert, msg):
        self._client("_sns", "sns").publish(
            TopicArn=self.topic, Subject=f"[{alert.get('severity')}] Insight anomaly"[:100], Message=msg)

    def _retry(self, name, fn, *a):
        for i in range(self.retries):
            try:
                fn(*a)
                return True
            except Exception as e:
                log.warning("%s attempt %d/%d failed: %r", name, i + 1, self.retries, e)
                if i < self.retries - 1:
                    time.sleep(self.backoff * 2 ** i)
        return False

    def publish(self, alert: dict) -> str:
        if self.dry_run:
            log.warning("AWS DRY-RUN: alert %s not sent", alert.get("id"))
            return "queued"
        msg = json.dumps(alert, separators=(",", ":"))
        steps = [("cloudwatch", self._to_cw)] + ([("sns", self._to_sns)] if self.topic else [])
        results = [self._retry(n, f, alert, msg) for n, f in steps]   # every target attempted
        return "sent" if all(results) else "failed"

if __name__ == "__main__":
    logging.basicConfig(level="INFO")
    print(AwsSink().publish({"type": "alert", "id": "alt-test", "severity": "WARNING", "aws_status": "pending"}))
