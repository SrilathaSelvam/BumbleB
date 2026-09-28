"""Log generator: append deterministic error/normal lines to app.log
Scenarios: blip (brief spike), ramp (WARNING→HIGH→CRITICAL), sustained (ongoing CRITICAL)
Usage: gen = Generator('app.log'); gen.inject('ramp'); time.sleep(120); gen.reset()
"""
import os
import time
from datetime import datetime, timedelta, timezone
import random
import threading

class Generator:
    """Append log lines to file matching <ISO time> <LEVEL> <service> <message>"""
    
    SERVICES = ["AuthService", "PaymentGateway", "OrderProcessor", "CheckoutController", 
                "DBConnPool", "CacheLayer", "NotificationQueue", "APIGateway"]
    
    ERROR_MESSAGES = {
        "AuthService": [
            "Session token expired",
            "Failed to validate credentials",
            "LDAP connection timeout"
        ],
        "PaymentGateway": [
            "Connection timed out after 3000ms",
            "PCI compliance check failed",
            "Rate limit exceeded"
        ],
        "OrderProcessor": [
            "DB Deadlock on table 'orders'",
            "Inventory service unreachable",
            "Transaction rollback"
        ],
        "CheckoutController": [
            "NullPointerException in calculateTaxes()",
            "Request body parse error",
            "Cart item not found"
        ],
        "DBConnPool": [
            "Connection pool exhausted (max=50)",
            "Query timeout after 30s",
            "Primary database unavailable"
        ],
        "CacheLayer": [
            "Redis connection refused",
            "Memory limit exceeded",
            "Eviction policy triggered"
        ],
        "NotificationQueue": [
            "Kafka broker offline",
            "Message serialization failed",
            "Queue depth > 10000"
        ],
        "APIGateway": [
            "Upstream service 503 Service Unavailable",
            "SSL certificate validation failed",
            "TLS handshake timeout"
        ]
    }
    
    INFO_MESSAGES = {
        "AuthService": ["User login successful", "Session created", "Token refreshed"],
        "PaymentGateway": ["Payment authorized", "Settlement processed", "Webhook delivered"],
        "OrderProcessor": ["Order created", "Inventory reserved", "Fulfillment initiated"],
        "CheckoutController": ["Cart updated", "Coupon applied", "Address validated"],
        "DBConnPool": ["Connection established", "Health check passed", "Pool size optimal"],
        "CacheLayer": ["Cache hit", "Entry evicted", "Replication acknowledged"],
        "NotificationQueue": ["Message enqueued", "Consumer lag < 1s", "Retention purged"],
        "APIGateway": ["Request routed", "Circuit breaker closed", "Rate limit OK"]
    }
    
    def __init__(self, log_file="app.log", seed=42):
        self.log_file = log_file
        self.seed = seed
        self.rng = random.Random(seed)
        self._stop_injection = False
        self._injection_thread = None
        self._base_ts = None
        self._scenario_state = {}
        
        # Init file
        if not os.path.exists(log_file):
            with open(log_file, 'w') as f:
                f.write("")
    
    def _now_iso(self):
        """ISO 8601 UTC timestamp"""
        if self._base_ts is None:
            self._base_ts = datetime.now(timezone.utc)
        return self._base_ts.isoformat().replace('+00:00', 'Z')
    
    def _line(self, level, service, message):
        """Format: <ISO> <LEVEL> <service> <message>"""
        return f"{self._now_iso()} {level} {service} {message}\n"
    
    def _append(self, line):
        """Append line to file, fsync immediately"""
        with open(self.log_file, 'a') as f:
            f.write(line)
            f.flush()
            os.fsync(f.fileno())
    
    def _normal_line(self):
        """Normal INFO/WARN line (~95% INFO, ~5% WARN)"""
        service = self.rng.choice(self.SERVICES)
        if self.rng.random() < 0.95:
            level = "INFO"
            msg = self.rng.choice(self.INFO_MESSAGES[service])
        else:
            level = "WARN"
            msg = "Performance degradation detected"
        return self._line(level, service, msg)
    
    def _error_line(self):
        """ERROR or FATAL line (~90% ERROR, ~10% FATAL)"""
        service = self.rng.choice(self.SERVICES)
        level = "FATAL" if self.rng.random() < 0.10 else "ERROR"
        msg = self.rng.choice(self.ERROR_MESSAGES[service])
        return self._line(level, service, msg)
    
    def _inject_worker(self, scenario, duration_secs, error_pattern_fn):
        """Background worker: append lines matching error_pattern_fn(elapsed_secs)"""
        start = time.time()
        lines_per_sec = 20  # Total log volume
        
        while not self._stop_injection:
            elapsed = time.time() - start
            if elapsed >= duration_secs:
                break
            
            # Determine how many lines and what % should be errors
            error_rate = error_pattern_fn(elapsed)
            error_rate = max(0.0, min(1.0, error_rate))
            
            # Append ~20 lines per second (deterministic)
            for _ in range(lines_per_sec):
                if self.rng.random() < error_rate:
                    self._append(self._error_line())
                else:
                    self._append(self._normal_line())
            
            # Advance base_ts by 1 second for next iteration
            self._base_ts += timedelta(seconds=1)
            time.sleep(1.0 / lines_per_sec)
    
    def inject(self, scenario):
        """
        Inject named scenario. Non-blocking (starts background thread).
        - 'blip': 5s of 40% error rate (below typical alert threshold), returns to ~2%
        - 'ramp': 90s gradual climb: 2% → 15% (WARNING) → 35% (HIGH) → 60% (CRITICAL), then return
        - 'sustained': 120s of constant 70% error rate (permanent CRITICAL)
        """
        if self._injection_thread and self._injection_thread.is_alive():
            self._stop_injection = True
            self._injection_thread.join(timeout=5)
        
        self._stop_injection = False
        self._scenario_state = {"scenario": scenario, "start": time.time()}
        
        if scenario == "blip":
            self._injection_thread = threading.Thread(
                target=self._inject_worker,
                args=("blip", 5.0, lambda e: 0.40 if e < 5 else 0.02),
                daemon=True
            )
        elif scenario == "ramp":
            # 0-30s: 2%→15% (WARNING), 30-60s: 15%→35% (HIGH), 60-90s: 35%→60% (CRITICAL)
            def ramp_error_rate(elapsed):
                if elapsed < 30:
                    return 0.02 + (elapsed / 30) * 0.13  # 2% → 15%
                elif elapsed < 60:
                    return 0.15 + ((elapsed - 30) / 30) * 0.20  # 15% → 35%
                elif elapsed < 90:
                    return 0.35 + ((elapsed - 60) / 30) * 0.25  # 35% → 60%
                else:
                    # Return to baseline after 90s
                    decay = max(0, 1 - (elapsed - 90) / 30)
                    return 0.60 * decay + 0.02 * (1 - decay)
            
            self._injection_thread = threading.Thread(
                target=self._inject_worker,
                args=("ramp", 120.0, ramp_error_rate),
                daemon=True
            )
        elif scenario == "sustained":
            # Constant 70% error rate for 120s
            self._injection_thread = threading.Thread(
                target=self._inject_worker,
                args=("sustained", 120.0, lambda e: 0.70),
                daemon=True
            )
        else:
            raise ValueError(f"Unknown scenario: {scenario}")
        
        self._injection_thread.start()
    
    def reset(self):
        """Stop injection and clear log file"""
        self._stop_injection = True
        if self._injection_thread:
            self._injection_thread.join(timeout=5)
        
        # Clear file and reset state
        with open(self.log_file, 'w') as f:
            f.write("")
        self._base_ts = None
        self._scenario_state = {}
    
    def warm_up(self, duration_secs=45):
        """Generate normal baseline for detector warm-up (INFO/WARN/occasional ERROR)"""
        start = time.time()
        while time.time() - start < duration_secs:
            if self.rng.random() < 0.02:  # ~2% error rate (baseline)
                self._append(self._error_line())
            else:
                self._append(self._normal_line())
            self._base_ts += timedelta(seconds=0.05)
            time.sleep(0.05)


if __name__ == "__main__":
    import sys
    
    # Quick self-test
    test_file = "/tmp/test_generator.log"
    gen = Generator(test_file, seed=42)
    
    print("=== TEST 1: Warm-up ===")
    gen.warm_up(5)
    with open(test_file, 'r') as f:
        lines = f.readlines()
    print(f"✓ Generated {len(lines)} lines in warm-up")
    assert len(lines) > 0, "No lines generated"
    assert all(" INFO " in l or " WARN " in l or " ERROR " in l or " FATAL " in l for l in lines), "Invalid format"
    
    print("\n=== TEST 2: Blip scenario (5s) ===")
    gen.reset()
    gen.inject("blip")
    time.sleep(6)
    with open(test_file, 'r') as f:
        blip_lines = f.readlines()
    print(f"✓ Blip generated {len(blip_lines)} lines")
    errors = sum(1 for l in blip_lines if " ERROR " in l or " FATAL " in l)
    error_rate = errors / len(blip_lines) if blip_lines else 0
    print(f"  Peak error rate: {error_rate:.1%}")
    
    print("\n=== TEST 3: Ramp scenario (120s, sample 10s) ===")
    gen.reset()
    gen.inject("ramp")
    time.sleep(10)
    gen._stop_injection = True
    gen._injection_thread.join()
    with open(test_file, 'r') as f:
        ramp_lines = f.readlines()
    errors = sum(1 for l in ramp_lines if " ERROR " in l or " FATAL " in l)
    error_rate = errors / len(ramp_lines) if ramp_lines else 0
    print(f"✓ Ramp sampled {len(ramp_lines)} lines in 10s, error rate: {error_rate:.1%}")
    
    print("\n=== TEST 4: Sustained scenario (sample 10s) ===")
    gen.reset()
    gen.inject("sustained")
    time.sleep(10)
    gen._stop_injection = True
    gen._injection_thread.join()
    with open(test_file, 'r') as f:
        sustained_lines = f.readlines()
    errors = sum(1 for l in sustained_lines if " ERROR " in l or " FATAL " in l)
    error_rate = errors / len(sustained_lines) if sustained_lines else 0
    print(f"✓ Sustained sampled {len(sustained_lines)} lines in 10s, error rate: {error_rate:.1%}")
    
    print("\n=== TEST 5: Line format validation ===")
    gen.reset()
    gen.warm_up(2)
    with open(test_file, 'r') as f:
        sample = f.readline().strip()
    print(f"Sample line: {sample}")
    parts = sample.split(" ", 2)
    assert len(parts) >= 3, "Not enough parts"
    assert parts[1] in ["INFO", "WARN", "ERROR", "FATAL"], f"Invalid level: {parts[1]}"
    print("✓ Format valid: <ISO> <LEVEL> <service> <message>")
    
    print("\n=== TEST 6: Interface check ===")
    assert hasattr(gen, 'inject'), "Missing inject()"
    assert hasattr(gen, 'reset'), "Missing reset()"
    assert hasattr(gen, 'warm_up'), "Missing warm_up()"
    assert callable(gen.inject), "inject not callable"
    assert callable(gen.reset), "reset not callable"
    print("✓ Generator interface complete")
    
    print("\n✅ ALL TESTS PASSED")
    os.remove(test_file)