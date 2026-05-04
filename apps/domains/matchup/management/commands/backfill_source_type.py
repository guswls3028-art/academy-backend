"""matchup_document source_type title regex 자동 backfill.

배경: T2 운영 audit (2026-05-04 PHASE_FULL_AUDIT) 결과 source_type 부정합:
- 신민 출판 21 doc → academy_workbook 분류, but commercial_workbook 적합
- 학교 기출 29 doc → 26 academy_workbook, but school_exam_pdf 적합 (anchor 우선)
- 박철 26-1m 6 doc → commercial_workbook 분류 OK (자체 모의고사로 학원장 의도)

backfill 후 reanalyze 권장 — source_type 변경 시 splitter strategy 분기 영향.

규칙 (title regex priority 순서):
  1. "신민" → commercial_workbook (외부 출판)
  2. "기출 통과", "학년도 .* 중간/기말", "학년도 .* 모의고사" → school_exam_pdf
  3. "26-1m" 박철 자체 모의고사 → academy_workbook (이미 그러함, 명시 유지)

Usage:
  python manage.py backfill_source_type --tenant-id 2 --dry-run
  python manage.py backfill_source_type --tenant-id 2  # 실제 적용
"""
import re

from django.core.management.base import BaseCommand

from apps.domains.matchup.models import MatchupDocument


# title regex → source_type. 첫 매치 우선.
RULES = [
    (re.compile(r"신민"), "commercial_workbook", "신민 외부 출판"),
    (re.compile(r"^\d{4}\s.*(?:중간고사|기말고사|모의고사)"), "school_exam_pdf", "학년도 학교 시험"),
    (re.compile(r"기출\s*통과"), "school_exam_pdf", "학교 기출 통과"),
    (re.compile(r"학년도\s.*(?:중간|기말|모의고사)"), "school_exam_pdf", "학년도 시험"),
]


class Command(BaseCommand):
    help = "MatchupDocument.meta.source_type을 title regex로 자동 backfill"

    def add_arguments(self, parser):
        parser.add_argument("--tenant-id", type=int, default=None)
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args, **options):
        tenant_id = options["tenant_id"]
        dry_run = options["dry_run"]

        qs = MatchupDocument.objects.all()
        if tenant_id is not None:
            qs = qs.filter(tenant_id=tenant_id)

        changes = []
        for d in qs.only("id", "title", "meta", "tenant_id"):
            title = d.title or ""
            meta = dict(d.meta or {})
            current_src = meta.get("source_type") or meta.get("upload_intent") or "(unset)"

            new_src = None
            reason = None
            for pattern, target, desc in RULES:
                if pattern.search(title):
                    new_src = target
                    reason = desc
                    break

            if new_src and new_src != current_src:
                changes.append((d.id, current_src, new_src, reason, title[:50]))
                if not dry_run:
                    meta["source_type"] = new_src
                    meta["source_type_backfill_reason"] = reason
                    d.meta = meta
                    d.save(update_fields=["meta", "updated_at"])

        # 출력
        prefix = "[dry-run] " if dry_run else ""
        self.stdout.write(f"\n{prefix}Total changes: {len(changes)}")
        # source_type 별 분포
        from collections import Counter
        target_dist = Counter(c[2] for c in changes)
        for src, n in sorted(target_dist.items(), key=lambda x: -x[1]):
            self.stdout.write(f"  -> {src}: {n}")

        # 샘플 (각 reason당 처음 5개)
        from collections import defaultdict
        by_reason = defaultdict(list)
        for c in changes:
            by_reason[c[3]].append(c)
        for reason, items in by_reason.items():
            self.stdout.write(f"\n[{reason}] {len(items)}:")
            for did, cur, new, _, title in items[:5]:
                self.stdout.write(f"  doc={did:>4} {cur:<22} -> {new:<22} title={title}")
            if len(items) > 5:
                self.stdout.write(f"  ... +{len(items) - 5}")

        if dry_run:
            self.stdout.write(f"\n{prefix}DRY RUN — no changes applied. Re-run without --dry-run to apply.")
        else:
            self.stdout.write(self.style.SUCCESS(f"\nApplied {len(changes)} backfills."))
            self.stdout.write("Note: reanalyze 필요 — source_type 변경 시 splitter strategy 분기 영향")
