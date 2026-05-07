"""Stage 6.3V — find_similar_problems 후보 pool size 분포 측정 (read-only).

services.py:246 의 자료 source 카테고리 격리 (`document__category=source_category`)
가 박철T 같이 카테고리당 doc 1~2개 학원장에게 매칭 풀을 좁히는 영향 정량화.

측정:
    tenant × source_type × category 별 indexable 문제 수 (find_similar 후보 풀)
    + tenant 평균 / median / p10 / p90 pool size

사용:
    python manage.py measure_find_similar_pool_size
    python manage.py measure_find_similar_pool_size --tenant 2
    python manage.py measure_find_similar_pool_size --json

원칙: read-only — find_similar 실 호출 X. queryset count 만.
"""
from __future__ import annotations

import json
import statistics
from collections import defaultdict

from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "find_similar_problems 후보 pool size 분포 (Stage 6.3V instrumentation)"

    def add_arguments(self, parser):
        parser.add_argument("--tenant", type=int, default=None)
        parser.add_argument("--json", action="store_true", help="JSON 출력")
        parser.add_argument(
            "--top",
            type=int,
            default=20,
            help="가장 작은 pool 상위 N개 노출 (격리 영향 큰 카테고리 식별)",
        )

    def handle(self, *args, **opts):
        from apps.domains.matchup.models import MatchupDocument, MatchupProblem

        doc_qs = MatchupDocument.objects.filter(status="done")
        if opts.get("tenant"):
            doc_qs = doc_qs.filter(tenant_id=opts["tenant"])

        # tenant × source_type × category → indexable problem count
        # find_similar 격리 정책 시뮬레이션:
        #   - 시험지 source (school_exam_pdf, student_exam_photo): 격리 해제, author 격리만
        #   - 자료 source (academy_workbook 등): document__category=source_category 격리
        # 본 측정은 자료 source 의 격리 풀 size 만 — 시험지 source 는 풀이 더 넓어 영향 작음
        material_sources = {
            "academy_workbook", "commercial_workbook",
            "explanation", "answer_key", "other",
        }

        # group key = (tenant_id, source_type, category)
        problem_counts: dict = defaultdict(int)
        doc_counts: dict = defaultdict(int)

        chunk = 500
        offset = 0
        while True:
            rows = list(
                doc_qs.values("id", "tenant_id", "source_type", "category", "meta")
                .order_by("id")[offset:offset + chunk]
            )
            if not rows:
                break
            for row in rows:
                src = row.get("source_type") or "other"
                if src not in material_sources:
                    continue
                meta = row.get("meta") or {}
                if meta.get("indexable") is False:
                    continue  # find_similar 추천 풀 진입 자격 X — services._eligible
                key = (row["tenant_id"], src, row.get("category") or "(none)")
                doc_counts[key] += 1
            offset += chunk

        # 각 group 의 problem 풀 size — MatchupProblem.count(document__in=group docs)
        # 본 측정은 doc count 만 우선. problem count 까지 필요 시 후속 stage.
        # 격리 정책상 같은 (tenant, source_type, category) 안에서만 매칭 → doc count 가
        # pool size 의 1차 proxy (각 doc 당 평균 problem 수 곱하면 대략 풀).

        # 기본 표
        groups = sorted(doc_counts.keys(), key=lambda k: doc_counts[k])

        report = {
            "tenant_filter": opts.get("tenant"),
            "groups": [],
            "summary_per_tenant": {},
        }
        per_tenant: dict = defaultdict(list)
        for key in groups:
            tenant_id, src, cat = key
            per_tenant[tenant_id].append(doc_counts[key])
            report["groups"].append({
                "tenant_id": tenant_id,
                "source_type": src,
                "category": cat,
                "doc_count": doc_counts[key],
            })

        for t, vals in per_tenant.items():
            report["summary_per_tenant"][str(t)] = {
                "group_count": len(vals),
                "doc_min": min(vals),
                "doc_max": max(vals),
                "doc_mean": round(statistics.mean(vals), 2) if vals else 0,
                "doc_median": (
                    round(statistics.median(vals), 2) if vals else 0
                ),
                "doc_p10": (
                    round(statistics.quantiles(vals, n=10)[0], 2)
                    if len(vals) >= 10 else min(vals)
                ),
                "isolated_groups_le_2": sum(1 for v in vals if v <= 2),
                "isolated_groups_le_5": sum(1 for v in vals if v <= 5),
            }

        if opts.get("json"):
            self.stdout.write(json.dumps(report, indent=2, ensure_ascii=False))
            return

        self.stdout.write(self.style.SUCCESS(
            "FIND_SIMILAR_POOL_REPORT | tenant_filter=%s" % (opts.get("tenant") or "all")
        ))
        self.stdout.write("=" * 80)

        # 격리 영향 가장 큰 (가장 좁은 pool) 상위 N
        top_n = opts.get("top") or 20
        self.stdout.write(f"가장 좁은 격리 풀 top {top_n} (재료 source 만):")
        self.stdout.write(f"{'tenant':>8} {'source_type':<22} {'category':<32} {'doc_count':>10}")
        self.stdout.write("-" * 76)
        for entry in report["groups"][:top_n]:
            self.stdout.write(
                f"{entry['tenant_id']:>8} "
                f"{entry['source_type'][:22]:<22} "
                f"{entry['category'][:32]:<32} "
                f"{entry['doc_count']:>10}"
            )

        self.stdout.write("=" * 80)
        self.stdout.write("tenant 별 요약:")
        self.stdout.write(
            f"{'tenant':>8} {'groups':>8} {'min':>6} {'p10':>6} "
            f"{'median':>8} {'mean':>8} {'max':>6} {'≤2':>6} {'≤5':>6}"
        )
        self.stdout.write("-" * 76)
        for tid, summary in sorted(report["summary_per_tenant"].items()):
            self.stdout.write(
                f"{tid:>8} {summary['group_count']:>8} {summary['doc_min']:>6} "
                f"{summary['doc_p10']:>6} {summary['doc_median']:>8} "
                f"{summary['doc_mean']:>8} {summary['doc_max']:>6} "
                f"{summary['isolated_groups_le_2']:>6} "
                f"{summary['isolated_groups_le_5']:>6}"
            )
