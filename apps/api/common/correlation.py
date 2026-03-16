# apps/api/common/correlation.py
"""
Global request correlation ID middleware and logging filter.

- Reads X-Request-ID from incoming request (or generates UUID4).
- Stores in threading.local() for access anywhere in the request lifecycle.
- Adds X-Request-ID to every response.
- CorrelationIdFilter injects `correlation_id` into all log records.

stdlib only: uuid, threading, logging.
"""
from __future__ import annotations

import logging
import threading
import uuid

_local = threading.local()


def get_correlation_id() -> str:
    """Return the current request's correlation ID, or '-' if none is set."""
    return getattr(_local, "correlation_id", "-")


class CorrelationIdMiddleware:
    """
    Django middleware: propagate or generate a request correlation ID.

    Must be placed FIRST in the MIDDLEWARE list so the ID is available
    to all downstream middleware and views.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        # Django normalises headers: X-Request-ID -> HTTP_X_REQUEST_ID
        cid = request.META.get("HTTP_X_REQUEST_ID") or str(uuid.uuid4())
        _local.correlation_id = cid

        response = self.get_response(request)
        response["X-Request-ID"] = cid

        # Clean up after the request to avoid leaking across threads reused
        # by WSGI servers with thread pools.
        try:
            del _local.correlation_id
        except AttributeError:
            pass

        return response


class CorrelationIdFilter(logging.Filter):
    """Logging filter that injects ``correlation_id`` into every log record."""

    def filter(self, record):
        record.correlation_id = get_correlation_id()
        return True
