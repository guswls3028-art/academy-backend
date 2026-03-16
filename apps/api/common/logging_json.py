"""
Structured JSON log formatter for production.

Outputs one JSON object per line to stdout, compatible with
CloudWatch / any log aggregator that parses JSON lines.

Integrates with the existing CorrelationIdFilter in
apps.api.common.correlation — the filter injects `correlation_id`
into every LogRecord, and this formatter includes it in the JSON output.

Usage (in Django LOGGING config):
    "formatters": {
        "json": {
            "()": "apps.api.common.logging_json.JsonFormatter",
        },
    }
"""

import json
import logging
import traceback
from datetime import datetime, timezone


class JsonFormatter(logging.Formatter):
    """
    Emit one JSON object per log line.

    Fields:
        ts              ISO-8601 timestamp (UTC)
        level           DEBUG / INFO / WARNING / ERROR / CRITICAL
        logger          logger name
        msg             formatted message
        correlation_id  request correlation id (injected by CorrelationIdFilter)
        extra           dict of any extra attrs passed via logger.info("x", extra={...})
    """

    # Keys that belong to the standard LogRecord — everything else is "extra".
    _BUILTIN_ATTRS = frozenset(
        {
            "args",
            "created",
            "exc_info",
            "exc_text",
            "filename",
            "funcName",
            "levelname",
            "levelno",
            "lineno",
            "message",
            "module",
            "msecs",
            "msg",
            "name",
            "pathname",
            "process",
            "processName",
            "relativeCreated",
            "stack_info",
            "taskName",
            "thread",
            "threadName",
            # Injected by CorrelationIdFilter — promoted to top-level field.
            "correlation_id",
        }
    )

    def format(self, record: logging.LogRecord) -> str:
        # Ensure record.message is populated.
        record.message = record.getMessage()

        payload: dict = {
            "ts": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.message,
        }

        # Correlation ID (injected by CorrelationIdFilter, defaults to "-").
        correlation_id = getattr(record, "correlation_id", None)
        if correlation_id and correlation_id != "-":
            payload["correlation_id"] = correlation_id

        # Collect extra fields (anything the caller passed that isn't a builtin).
        extra = {
            k: v
            for k, v in record.__dict__.items()
            if k not in self._BUILTIN_ATTRS and not k.startswith("_")
        }
        if extra:
            payload["extra"] = extra

        # Exception info
        if record.exc_info and record.exc_info[1] is not None:
            payload["exc_info"] = "".join(
                traceback.format_exception(*record.exc_info)
            )

        # Stack info (e.g. from logger.info("x", stack_info=True))
        if record.stack_info:
            payload["stack_info"] = record.stack_info

        return json.dumps(payload, default=str, ensure_ascii=False)
