"""Stage 6.3V — status=done 문서 중 page_level_fallback 비율 측정 (read-only).

학원장 UI 가 status="done" 카운터를 problem_level + page_level_fallback 합산으로
보여주는 결함 정량화. processing_quality 분리 분포로 학원장 인지 갭 측정.

processing_quality 분류 (callbacks.py _handle_matchup_ai_result):
    precise_split  — bbox null < 30%
    coarse_split   — bbox null 30~50%
    needs_review   — bbox null 50~70%
    page_fallback  — bbox null 70%+
    no_problems    — problem_count = 0

사용:
    python manage.py measure_fallback_ratio
    python manage.py measure_fallback_ratio --tenant 1
    python manage.py measure_fallback_ratio --by source_type

원칙: read-only.
"""
from __future__ import annotations

from collections import defaultdict

from django.core.management.base import BaseCommand


_QUALITY_LEVELS = (
    "precise_split",
    "coarse_split",
    "needs_review",
    "page_fallback",
    "no_problems",
    "(unset)",
)


class Command(BaseCommand):
    help = "MatchupDocument status=done 의 processing_quality 분포 (Stage 6.3V instrumentation)"

    def add_arguments(self, parser):
        parser.add_argument("--tenant", type=int, default=None)
        parser.add_argument(
            "--by",
            choices=["tenant", "source_type", "category"],
            default="tenant",
        )

    def handle(self, *args, **opts):
        from apps.domains.matchup.models import MatchupDocument

        qs = MatchupDocument.objects.filter(status="done")
        if opts.get("tenant"):
            qs = qs.filter(tenant_id=opts["tenant"])

        by = opts["by"]
        # bucket[group][quality] = count
        buckets: dict = defaultdict(lambda: {q: 0 for q in _QUALITY_LEVELS})

        chunk_size = 500
        offset = 0
        while True:
            rows = list(
                qs.values(
                    "id", "tenant_id", "source_type", "category", "meta",
                ).order_by("id")[offset:offset + chunk_size]
            )
            if not rows:
                break
            for row in rows:
                meta = row.get("meta") or {}
                quality = meta.get("processing_quality") or "(unset)"
                if quality not in _QUALITY_LEVELS:
                    quality = "(unset)"
                if by == "tenant":
                    key = f"tenant_{row['tenant_id']}"
                elif by == "source_type":
                    key = row.get("source_type") or "(none)"
                else:
                    key = row.get("category") or "(none)"
                buckets[key][quality] += 1
            offset += chunk_size

        self.stdout.write(self.style.SUCCESS(
            "FALLBACK_RATIO_REPORT | by=%s | tenant_filter=%s" % (by, opts.get("tenant") or "all")
        ))
        header = f"{'group':<32} {'total':>7} " + " ".join(
            f"{q[:13]:>13}" for q in _QUALITY_LEVELS
        ) + f" {'fallback%':>10}"
        self.stdout.write("=" * len(header))
        self.stdout.write(header)
        self.stdout.write("-" * len(header))

        sorted_keys = sorted(
            buckets.keys(),
            key=lambda k: -sum(buckets[k].values()),
        )

        grand: dict = {q: 0 for q in _QUALITY_LEVELS}
        grand_total = 0
        for k in sorted_keys:
            b = buckets[k]
            total = sum(b.values())
            grand_total += total
            fallback_n = b["page_fallback"] + b["needs_review"] + b["no_problems"]
            ratio = (fallback_n / total * 100) if total else 0.0
            row_cells = " ".join(f"{b[q]:>13}" for q in _QUALITY_LEVELS)
            self.stdout.write(f"{k:<32} {total:>7} {row_cells} {ratio:>9.2f}%")
            for q in _QUALITY_LEVELS:
                grand[q] += b[q]

        self.stdout.write("-" * len(header))
        grand_fb = grand["page_fallback"] + grand["needs_review"] + grand["no_problems"]
        grand_ratio = (grand_fb / grand_total * 100) if grand_total else 0.0
        grand_row = " ".join(f"{grand[q]:>13}" for q in _QUALITY_LEVELS)
        self.stdout.write(self.style.SUCCESS(
            f"{'TOTAL':<32} {grand_total:>7} {grand_row} {grand_ratio:>9.2f}%"
        ))
