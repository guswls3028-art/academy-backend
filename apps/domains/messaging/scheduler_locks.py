from __future__ import annotations

from contextlib import contextmanager
import hashlib
import logging

from django.db import connection

logger = logging.getLogger(__name__)


def _lock_key(name: str) -> int:
    digest = hashlib.sha256(name.encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big") & ((1 << 63) - 1)


@contextmanager
def advisory_lock(name: str):
    """
    PostgreSQL advisory lock for scheduled management commands.

    Non-PostgreSQL test/dev databases run without a distributed lock.
    """
    if connection.vendor != "postgresql":
        yield True
        return

    key = _lock_key(name)
    acquired = False
    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_try_advisory_lock(%s)", [key])
        acquired = bool(cursor.fetchone()[0])
    if not acquired:
        logger.info("advisory_lock skipped: name=%s key=%s", name, key)
        yield False
        return

    try:
        yield True
    finally:
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_advisory_unlock(%s)", [key])
