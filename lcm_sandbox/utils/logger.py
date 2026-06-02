"""Structured JSON logger.

Every record carries: timestamp, level, phase, step, sandbox_id (when known),
plus any extra fields passed via `logger.info("msg", extra={...})`.
"""

from __future__ import annotations

import logging
import sys
from typing import Any

try:
    from pythonjsonlogger import jsonlogger
except ImportError:  # graceful fallback if dep missing
    jsonlogger = None  # type: ignore[assignment]


_RESERVED = {
    "name", "msg", "args", "levelname", "levelno", "pathname", "filename",
    "module", "exc_info", "exc_text", "stack_info", "lineno", "funcName",
    "created", "msecs", "relativeCreated", "thread", "threadName",
    "processName", "process", "message",
}


class _PlainJsonFormatter(logging.Formatter):
    """Minimal JSON formatter used if python-json-logger is unavailable."""

    def format(self, record: logging.LogRecord) -> str:
        import json
        payload: dict[str, Any] = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        for k, v in record.__dict__.items():
            if k not in _RESERVED and not k.startswith("_"):
                payload[k] = v
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def configure(level: int = logging.INFO) -> None:
    """Install a JSON handler on the root logger. Idempotent."""
    root = logging.getLogger()
    if any(getattr(h, "_lcm_sandbox", False) for h in root.handlers):
        return

    handler = logging.StreamHandler(stream=sys.stderr)
    if jsonlogger is not None:
        fmt = jsonlogger.JsonFormatter(
            "%(asctime)s %(levelname)s %(name)s %(message)s"
        )
    else:
        fmt = _PlainJsonFormatter()
    handler.setFormatter(fmt)
    handler._lcm_sandbox = True  # type: ignore[attr-defined]
    root.addHandler(handler)
    root.setLevel(level)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
