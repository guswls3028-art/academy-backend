# PATH: apps/domains/matchup/management/commands/backfill_problem_count.py
"""
MatchupDocument.problem_count를 실제 problems 수로 일괄 보정.

배경: callbacks.py가 bulk_create + ignore_conflicts로 unique 충돌 row를 drop했으나
problem_count는 dispatch한 수를 그대로 저장했음. UI 좌측 라벨 "N문제"가 그리드 카드
실제 수와 어긋나는 문제.

Usage:
  python manage.py backfill_problem_count                  # 전체
  python manage.py backfill_problem_count --tenant-id 1    # 특정 테넌트
  python manage.py backfill_problem_count --dry-run        # 미리보기
"""
from django.core.management.base import BaseCommand
from django.db.models import Count

from apps.domains.matchup.models import MatchupDocument


class Command(BaseCommand):
    help = "MatchupDocument.problem_count를 실제 row count로 보정"

    def add_arguments(self, parser):
        parser.add_argument("--tenant-id", type=int, default=None)
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args, **options):
        tenant_id = options["tenant_id"]
        dry_run = options["dry_run"]

        qs = MatchupDocument.objects.all()
        if tenant_id is not None:
            qs = qs.filter(tenant_id=tenant_id)

        # 한 쿼리로 실제 count 계산
        docs = qs.annotate(actual=Count("problems"))

        mismatches = []
        for d in docs:
            if d.problem_count != d.actual:
                mismatches.append((d.id, d.problem_count, d.actual, d.title[:30]))

        if not mismatches:
            self.stdout.write(self.style.SUCCESS("모든 문서의 problem_count 정합. 변경 없음."))
            return

        self.stdout.write(f"불일치 {len(mismatches)}건:")
        for doc_id, stored, actual, title in mismatches:
            self.stdout.write(f"  doc {doc_id}: stored={stored} → actual={actual} ({title})")

        if dry_run:
            self.stdout.write(self.style.NOTICE("dry-run — 변경 안 함"))
            return

        for doc_id, _, actual, _ in mismatches:
            MatchupDocument.objects.filter(id=doc_id).update(problem_count=actual)

        self.stdout.write(self.style.SUCCESS(f"✓ {len(mismatches)}건 보정 완료"))
