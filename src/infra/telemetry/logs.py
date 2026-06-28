import json
import logging
from datetime import datetime, timezone

_logger = logging.getLogger("agent_platform")


def _log_entry(trace_id: str, level: str, event: str, **attrs) -> dict:
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "level": level,
        "trace_id": trace_id,
        "event": event,
    }
    entry.update(attrs)
    return entry


def info(trace_id: str, event: str, **attrs) -> None:
    entry = _log_entry(trace_id, "info", event, **attrs)
    _logger.info(json.dumps(entry))


def error(trace_id: str, event: str, **attrs) -> None:
    entry = _log_entry(trace_id, "error", event, **attrs)
    _logger.error(json.dumps(entry))


def warn(trace_id: str, event: str, **attrs) -> None:
    entry = _log_entry(trace_id, "warn", event, **attrs)
    _logger.warning(json.dumps(entry))
