from __future__ import annotations

import re
from collections.abc import AsyncIterator, Iterator
from contextlib import contextmanager
from contextvars import ContextVar

from django.db import connections
from django.db.backends.signals import connection_created


SAFE_HTTP_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
_WRITE_OPERATIONS = frozenset(
    {
        "ALTER",
        "CREATE",
        "DELETE",
        "DROP",
        "INSERT",
        "MERGE",
        "REPLACE",
        "TRUNCATE",
        "UPDATE",
    }
)
_LEADING_SQL_COMMENT = re.compile(r"\A(?:\s|/\*.*?\*/|--[^\r\n]*(?:\r?\n|\Z))*", re.DOTALL)
_SQL_WORD = re.compile(r"[A-Z]+")
_SAFE_METHOD_CONTEXT: ContextVar[str | None] = ContextVar(
    "safe_method_database_write_method",
    default=None,
)


class SafeMethodWriteError(RuntimeError):
    """Raised before a safe HTTP request can mutate the application database."""


class _BlockedSafeMethodWrite(BaseException):
    """Escape Django's ORM rollback marker until the middleware boundary."""

    def __init__(self, *, method: str, operation: str):
        self.method = method
        self.operation = operation


def sql_write_operation(sql: str | None) -> str | None:
    """Return the mutating SQL operation without retaining SQL or parameters."""

    normalized = _LEADING_SQL_COMMENT.sub("", sql or "").upper()
    first_word = _SQL_WORD.match(normalized)
    if first_word is None:
        return None
    operation = first_word.group(0)
    if operation in _WRITE_OPERATIONS:
        return operation
    if operation != "WITH":
        return None

    # Django can emit a data-changing CTE whose first token is WITH. Keep this
    # deliberately conservative: any write keyword in a WITH statement makes a
    # safe request fail closed before the database sees it.
    for match in _SQL_WORD.finditer(normalized):
        if match.group(0) in _WRITE_OPERATIONS:
            return match.group(0)
    return None


class SafeMethodWriteBlocker:
    def __call__(self, execute, sql, params, many, context):
        method = _SAFE_METHOD_CONTEXT.get()
        if method is None:
            return execute(sql, params, many, context)
        operation = sql_write_operation(sql)
        if operation is not None:
            # Django marks an active transaction as broken whenever an Exception
            # escapes an ORM write.  This write never reached the database, so use
            # an internal BaseException until the ORM stack has unwound, then
            # translate it to the public error at the middleware boundary.
            raise _BlockedSafeMethodWrite(method=method, operation=operation)
        return execute(sql, params, many, context)


_SAFE_METHOD_WRITE_BLOCKER = SafeMethodWriteBlocker()


def _install_safe_method_write_blocker(*, connection, **_kwargs):
    if _SAFE_METHOD_WRITE_BLOCKER not in connection.execute_wrappers:
        connection.execute_wrappers.append(_SAFE_METHOD_WRITE_BLOCKER)


connection_created.connect(
    _install_safe_method_write_blocker,
    dispatch_uid="apps.core.safe_method_database_write_blocker",
    weak=False,
)
for _alias in connections:
    _install_safe_method_write_blocker(connection=connections[_alias])


class SafeMethodDatabaseWriteMiddleware:
    """Fail closed when GET, HEAD, or OPTIONS attempts an application DB write."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        method = (getattr(request, "method", "") or "").upper()
        if method not in SAFE_HTTP_METHODS:
            return self.get_response(request)

        with _block_database_writes(method):
            response = self.get_response(request)
        if not getattr(response, "streaming", False):
            return response

        content = response.streaming_content
        if getattr(response, "is_async", False):
            response.streaming_content = _guard_async_stream(content, method=method)
        else:
            response.streaming_content = _guard_stream(content, method=method)
        return response


@contextmanager
def _block_database_writes(method: str):
    token = _SAFE_METHOD_CONTEXT.set(method)
    try:
        yield
    except _BlockedSafeMethodWrite as blocked:
        raise SafeMethodWriteError(
            f"safe HTTP method {blocked.method} attempted database {blocked.operation}"
        ) from None
    finally:
        _SAFE_METHOD_CONTEXT.reset(token)


def _guard_stream(content: Iterator[bytes], *, method: str) -> Iterator[bytes]:
    with _block_database_writes(method):
        yield from content


async def _guard_async_stream(
    content: AsyncIterator[bytes],
    *,
    method: str,
) -> AsyncIterator[bytes]:
    with _block_database_writes(method):
        async for chunk in content:
            yield chunk
