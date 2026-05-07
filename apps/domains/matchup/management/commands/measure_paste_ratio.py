"""Stage 6.3V — paste 이미지 manual 문항 비중 측정 (read-only).

manual CLIP contract 위반 (matchup_manual_index.py:227, is_camera_capture=True 시
_preprocess_camera_image 결과를 CLIP 에 입력) 의 영향 정량화. paste 비중이 작으면
distribution shift 영향 작음, 크면 6.3S backfill 비용 큼.

사용:
    python manage.py measure_paste_ratio
    python manage.py measure_paste_ratio --tenant 1
    python manage.py measure_paste_ratio --by source_type

원칙: read-only. 어떤 mutation 도 0.
"""
from __future__ import annotations

from collections import defaultdict

from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "manual problem 중 paste/camera 입력 비중 측정 (Stage 6.3V instrumentation)"

    def add_arguments(self, parser):
        parser.add_argument("--tenant", type=int, default=None, help="tenant_id 필터 (생략 시 전체)")
        parser.add_argument(
            "--by",
            choices=["tenant", "source_type", "category"],
            default="tenant",
            help="집계 단위",
        )

    def handle(self, *args, **opts):
        from apps.domains.matchup.models import MatchupProblem

        qs = MatchupProblem.objects.filter(meta__manual=True)
        if opts.get("tenant"):
            qs = qs.filter(tenant_id=opts["tenant"])

        # tenant_id 별 묶기 / by 옵션 따라 group key 결정
        by = opts["by"]
        buckets: dict = defaultdict(lambda: {"total": 0, "paste": 0})

        # Python 측 집계 — meta__paste 가 jsonb 라 ORM Q 보다 단순 iter 가 안전
        # tenant N 페이지 단위 chunk 로 메모리 보호
        chunk_size = 1000
        offset = 0
        while True:
            rows = list(
                qs.values(
                    "id", "tenant_id", "document_id", "meta",
                    "document__source_type", "document__category",
                ).order_by("id")[offset:offset + chunk_size]
            )
            if not rows:
                break
            for row in rows:
                meta = row.get("meta") or {}
                key: str
                if by == "tenant":
                    key = f"tenant_{row['tenant_id']}"
                elif by == "source_type":
                    key = row.get("document__source_type") or "(none)"
                else:  # category
                    key = row.get("document__category") or "(none)"
                buckets[key]["total"] += 1
                if meta.get("paste") is True or meta.get("is_camera_capture") is True:
                    buckets[key]["paste"] += 1
            offset += chunk_size

        self.stdout.write(self.style.SUCCESS(
            "PASTE_RATIO_REPORT | by=%s | tenant_filter=%s" % (by, opts.get("tenant") or "all")
        ))
        self.stdout.write("=" * 80)
        self.stdout.write(f"{'group':<40} {'manual_total':>14} {'paste':>10} {'ratio_%':>10}")
        self.stdout.write("-" * 80)

        sorted_keys = sorted(buckets.keys(), key=lambda k: -buckets[k]["total"])
        grand_total = 0
        grand_paste = 0
        for k in sorted_keys:
            b = buckets[k]
            ratio = (b["paste"] / b["total"] * 100) if b["total"] else 0.0
            self.stdout.write(f"{k:<40} {b['total']:>14} {b['paste']:>10} {ratio:>9.2f}%")
            grand_total += b["total"]
            grand_paste += b["paste"]

        self.stdout.write("-" * 80)
        grand_ratio = (grand_paste / grand_total * 100) if grand_total else 0.0
        self.stdout.write(self.style.SUCCESS(
            f"{'TOTAL':<40} {grand_total:>14} {grand_paste:>10} {grand_ratio:>9.2f}%"
        ))
