from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = "Summarize exported tenant_db_usage JSON logs without DB writes."

    def add_arguments(self, parser):
        parser.add_argument(
            "--input",
            action="append",
            required=True,
            help="JSON-lines file exported from the structured application log.",
        )

    def handle(self, *args, **options):
        totals = defaultdict(
            lambda: {
                "observed_requests": 0,
                "estimated_requests": 0.0,
                "query_count": 0.0,
                "write_query_count": 0.0,
                "db_duration_ms": 0.0,
                "request_duration_ms": 0.0,
            }
        )
        rejected = 0
        for raw_path in options["input"]:
            path = Path(raw_path)
            if not path.is_file():
                raise CommandError(f"input file not found: {path}")
            with path.open(encoding="utf-8") as handle:
                for line in handle:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        payload = json.loads(line)
                    except json.JSONDecodeError:
                        rejected += 1
                        continue
                    extra = payload.get("extra", payload)
                    if extra.get("event") != "tenant_db_usage":
                        continue
                    tenant_id = extra.get("tenant_id")
                    if not isinstance(tenant_id, int):
                        rejected += 1
                        continue
                    weight = float(extra.get("sample_weight") or 1)
                    row = totals[tenant_id]
                    row["observed_requests"] += 1
                    row["estimated_requests"] += weight
                    row["query_count"] += float(extra.get("query_count") or 0) * weight
                    row["write_query_count"] += (
                        float(extra.get("write_query_count") or 0) * weight
                    )
                    row["db_duration_ms"] += (
                        float(extra.get("db_duration_ms") or 0) * weight
                    )
                    row["request_duration_ms"] += (
                        float(extra.get("request_or_job_duration_ms") or 0) * weight
                    )

        total_db_ms = sum(row["db_duration_ms"] for row in totals.values())
        result = []
        for tenant_id, row in sorted(
            totals.items(),
            key=lambda item: item[1]["db_duration_ms"],
            reverse=True,
        ):
            result.append(
                {
                    "tenant_id": tenant_id,
                    "observed_requests": row["observed_requests"],
                    "estimated_requests": round(row["estimated_requests"], 2),
                    "query_count": round(row["query_count"], 2),
                    "write_query_count": round(row["write_query_count"], 2),
                    "db_duration_ms": round(row["db_duration_ms"], 2),
                    "db_time_share": (
                        round(row["db_duration_ms"] / total_db_ms, 4)
                        if total_db_ms > 0
                        else 0
                    ),
                    "request_duration_ms": round(
                        row["request_duration_ms"],
                        2,
                    ),
                }
            )
        self.stdout.write(
            json.dumps(
                {
                    "source_files": len(options["input"]),
                    "rejected_lines": rejected,
                    "tenant_count": len(result),
                    "tenants": result,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
