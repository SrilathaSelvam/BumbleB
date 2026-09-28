"""All env-var configuration."""
from __future__ import annotations
import logging, os
from dataclasses import dataclass

def _e(name, default, cast=str):
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        return cast(raw.strip())
    except ValueError:
        logging.warning("bad %s=%r, using %r", name, raw, default)
        return default

_bool = lambda s: s.lower() in ("1", "true", "yes", "on")

@dataclass(frozen=True)
class Settings:
    log_file: str = "app.log"            # LOG_FILE
    host: str = "0.0.0.0"                # HOST
    port: int = 8000                     # PORT
    poll_interval: float = 0.2           # POLL_INTERVAL (s)
    tick_interval: float = 1.0           # TICK_INTERVAL (s)
    window_seconds: int = 60             # WINDOW_SECONDS
    time_source: str = "arrival"         # TIME_SOURCE: arrival|log
    tail_from_start: bool = False        # TAIL_FROM_START
    history_size: int = 100              # HISTORY_SIZE
    cors_origins: tuple = ("*",)         # CORS_ORIGINS (comma list)
    detector: str = "backend.detection.detector:Detector"               # DETECTOR  mock | pkg.mod:factory
    detector_method: str = "process_snapshot"    # DETECTOR_METHOD
    generator: str = "mock"              # GENERATOR mock | pkg.mod:factory(log_file)
    mock_background: bool = True         # MOCK_BACKGROUND (mock generator normal traffic)
    sink: str = "none"                   # SINK none | mock | pkg.mod:factory
    sink_method: str = "publish"         # SINK_METHOD
    sink_timeout: float = 30.0           # SINK_TIMEOUT (s)
    log_level: str = "INFO"              # LOG_LEVEL

    @classmethod
    def from_env(cls):
        d = cls()
        return cls(
            _e("LOG_FILE", d.log_file), _e("HOST", d.host), _e("PORT", d.port, int),
            _e("POLL_INTERVAL", d.poll_interval, float), _e("TICK_INTERVAL", d.tick_interval, float),
            _e("WINDOW_SECONDS", d.window_seconds, int), _e("TIME_SOURCE", d.time_source),
            _e("TAIL_FROM_START", d.tail_from_start, _bool), _e("HISTORY_SIZE", d.history_size, int),
            tuple(x.strip() for x in _e("CORS_ORIGINS", "*").split(",")),
            _e("DETECTOR", d.detector), _e("DETECTOR_METHOD", d.detector_method),
            _e("GENERATOR", d.generator), _e("MOCK_BACKGROUND", d.mock_background, _bool),
            _e("SINK", d.sink), _e("SINK_METHOD", d.sink_method),
            _e("SINK_TIMEOUT", d.sink_timeout, float), _e("LOG_LEVEL", d.log_level),
        )
