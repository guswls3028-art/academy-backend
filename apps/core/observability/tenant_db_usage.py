from __future__ import annotations

import logging
import random
import time
from dataclasses import dataclass

from django.conf import settings
from django.db import connections

logger = logging.getLogger("academy.tenant_db_usage")

_WRITE_OPERATIONS = {
    "INSERT",
    "UPDATE",
    "DELETE",
    "MERGE",
    "CREATE",
    "ALTER",
    "DROP",
    "TRUNCATE",
}


@dataclass
class QueryUsage:
    query_count: int = 0
    write_query_count: int = 0
    db_duration_ms: float = 0.0

    def __call__(self, execute, sql, params, many, context):
        started = time.perf_counter()
        try:
            return execute(sql, params, many, context)
        finally:
            self.query_count += 1
            self.db_duration_ms += (time.perf_counter() - started) * 1000
            operation = (sql or "").lstrip().split(None, 1)
            if operation and operation[0].upper() in _WRITE_OPERATIONS:
                self.write_query_count += 1


class TenantDatabaseUsageMiddleware:
    """Measure DB time per resolved tenant without logging SQL or user data."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if not getattr(settings, "TENANT_DB_USAGE_ENABLED", False):
            return self.get_response(request)
        tenant = getattr(request, "tenant", None)
        if tenant is None:
            return self.get_response(request)

        sample_rate = max(
            0.0,
            min(1.0, float(getattr(settings, "TENANT_DB_USAGE_SAMPLE_RATE", 0.1))),
        )
        random_sampled = sample_rate > 0 and random.random() < sample_rate
        slow_ms = max(
            1,
            int(getattr(settings, "TENANT_DB_USAGE_SLOW_REQUEST_MS", 1000)),
        )
        usage = QueryUsage()
        status_code = 500
        started = time.perf_counter()
        try:
            with connections["default"].execute_wrapper(usage):
                response = self.get_response(request)
            status_code = getattr(response, "status_code", 200)
            return response
        finally:
            duration_ms = (time.perf_counter() - started) * 1000
            is_slow = duration_ms >= slow_ms
            is_failure = status_code >= 500
            if random_sampled or is_slow or is_failure:
                route = getattr(
                    getattr(request, "resolver_match", None),
                    "route",
                    None,
                )
                logger.info(
                    "tenant database usage",
                    extra={
                        "event": "tenant_db_usage",
                        "tenant_id": tenant.id,
                        "db_alias": "default",
                        "route_or_job_family": route or "unresolved",
                        "query_count": usage.query_count,
                        "write_query_count": usage.write_query_count,
                        "db_duration_ms": round(usage.db_duration_ms, 3),
                        "request_or_job_duration_ms": round(duration_ms, 3),
                        "status_class": f"{status_code // 100}xx",
                        "sample_rate": sample_rate,
                        "sample_weight": (
                            round(1 / sample_rate, 4)
                            if random_sampled and sample_rate > 0
                            else 1.0
                        ),
                        "sampling_reason": (
                            "random"
                            if random_sampled
                            else "failure"
                            if is_failure
                            else "slow"
                        ),
                    },
                )
