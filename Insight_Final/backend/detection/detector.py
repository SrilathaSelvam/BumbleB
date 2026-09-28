"""Standalone anomaly detector. Usage: Detector().process_snapshot(snapshot)."""
import math
import uuid
from datetime import datetime

from .parameters import Params

_RANK = {"WARNING": 1, "HIGH": 2, "CRITICAL": 3}


def _epoch(ts):
    if isinstance(ts, (int, float)):
        v = float(ts)
        return v / 1000 if v > 1e11 else v
    return datetime.fromisoformat(str(ts).replace("Z", "+00:00")).timestamp()


class Detector:
    def __init__(self, params: Params = None):
        self.p = params or Params()
        self.reset()

    def reset(self):
        self.mean = None
        self.var = 0.0
        self.count = 0
        self.breaches = 0
        self.last_alert_t = None
        self.last_rank = 0

    def _std(self):
        return max(math.sqrt(self.var), self.p.std_floor)

    def _severity(self, z):
        p = self.p
        if z >= p.critical_z:
            return "CRITICAL"
        if z >= p.high_z:
            return "HIGH"
        if z >= p.warning_z:
            return "WARNING"
        return None

    def _update(self, x):
        if self.mean is None:
            self.mean, self.var = x, 0.0
            return
        a, d = self.p.ewma_alpha, x - self.mean
        self.mean += a * d
        self.var = (1 - a) * (self.var + a * d * d)

    def process_snapshot(self, s):
        p, ts = self.p, s["ts"]
        rate, total = s["error_rate"], s["total_lines"]
        self.count += 1
        t = _epoch(ts)
        warming = self.count <= p.warmup_snapshots or self.mean is None
        valid = total >= p.min_lines
        mean = None if warming else self.mean
        std = self._std()
        upper = None if warming else mean + p.upper_k * std
        metric = {
            "type": "metric", "ts": ts, "error_rate": rate,
            "baseline_mean": mean, "baseline_upper": upper,
            "total_lines": total, "error_lines": s["error_lines"],
            "warming_up": warming,
        }
        if not valid:                       # min-lines guard: ignore entirely
            self.breaches = 0
            return {"metric": metric, "alert": None}
        if self.mean is None or warming:    # warm-up: learn only
            self._update(rate)
            return {"metric": metric, "alert": None}

        z = (rate - self.mean) / std
        breach = z >= p.warning_z and rate > upper and rate >= p.error_floor
        in_cd = (self.last_alert_t is not None
                 and t - self.last_alert_t < p.cooldown_s)
        alert = None
        if breach:
            self.breaches += 1
            sev = self._severity(z)
            if self.breaches >= p.persistence:
                if not in_cd or _RANK[sev] > self.last_rank:
                    self.last_alert_t, self.last_rank = t, _RANK[sev]
                    alert = {
                        "type": "alert",
                        "id": f"alt-{int(t)}-{uuid.uuid4().hex[:6]}",
                        "ts": ts, "severity": sev, "error_rate": rate,
                        "baseline_mean": self.mean, "baseline_std": std,
                        "z_score": z, "total_lines": total,
                        "error_lines": s["error_lines"],
                        "sample_messages": list(s.get("sample_errors") or []),
                        "aws_status": "pending",
                    }
        else:
            self.breaches = 0
            if not in_cd:
                self.last_rank = 0
        # baseline freeze: no update on breach, or during cooldown
        if not breach and not in_cd:
            self._update(rate)
        return {"metric": metric, "alert": alert}
