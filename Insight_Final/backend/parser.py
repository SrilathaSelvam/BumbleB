"""Log line: <ISO ts> <LEVEL> <service> <message>. ERROR/FATAL = errors."""
from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime, timezone

LEVELS = frozenset({"INFO", "WARN", "ERROR", "FATAL"})
ERROR_LEVELS = frozenset({"ERROR", "FATAL"})

@dataclass(frozen=True)
class ParsedLine:
    ts: float
    level: str
    service: str
    message: str
    @property
    def is_error(self) -> bool:
        return self.level in ERROR_LEVELS

def _ts(tok: str):
    if tok[-1:] in "Zz":
        tok = tok[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(tok)
    except ValueError:
        return None
    return (dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)).timestamp()

def parse_line(line: str) -> ParsedLine | None:
    parts = (line or "").strip().split(None, 3)
    if len(parts) < 3:
        return None
    ts, level = _ts(parts[0]), parts[1].upper()
    if ts is None or level not in LEVELS:
        return None
    return ParsedLine(ts, level, parts[2], parts[3] if len(parts) == 4 else "")
