# PATH: apps/domains/matchup/management/commands/fix_problem_numbers.py
# 자동분리 OCR 박스 누락으로 sequential offset 어긋난 problem number를
# 텍스트의 실제 번호로 일괄 PATCH 보정.
"""
사용:
  python manage.py fix_problem_numbers --tenant-id 1
  python manage.py fix_problem_numbers --tenant-id 1 --doc-id 153
  python manage.py fix_problem_numbers --dry-run --tenant-id 1

동작:
  - problem.text 첫 줄에서 "N." 또는 "[서답형 N]" 패턴 추출
  - DB number와 다르면 두 단계 PATCH (임시 번호 → 진짜 번호)로 충돌 회피
  - same-doc 안에서 number 충돌 시 임시 번호 슬롯(900+) 활용

Why: 운영 hakwonplus에서 doc 153 self-number-start 40%, doc 149 59% 등
self_start rate 낮은 자료가 많음. 자동분리 단계의 박스 누락 부작용을
런타임 PATCH로 보정해 매치업 그리드/검색 UX 정확도 향상.
"""
from __future__ import annotations

import re
from django.core.management.base import BaseCommand

from apps.domains.matchup.models import MatchupProblem


_NUM_RE = re.compile(r"^(\d{1,3})\s*[.,]\s")
_ESSAY_RE = re.compile(r"^\[\s*서\s*답형\s*(\d+)")


def extract_real_number(text: str) -> int | None:
    if not text:
        return None
    text = text.lstrip()
    m = _NUM_RE.match(text)
    if m:
        n = int(m.group(1))
        return n if 1 <= n <= 99 else None
    m = _ESSAY_RE.match(text)
    if m:
        n = 100 + int(m.group(1))
        return n if 100 <= n <= 199 else None
    return None


class Command(BaseCommand):
    help = "매치업 problem number를 텍스트 실제 번호로 일괄 보정 (OCR sequential offset 보정)."

    def add_arguments(self, parser):
        parser.add_argument("--tenant-id", type=int, required=True)
        parser.add_argument("--doc-id", type=int, default=None)
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args, **opts):
        tenant_id = opts["tenant_id"]
        doc_id = opts.get("doc_id")
        dry = opts["dry_run"]

        qs = MatchupProblem.objects.filter(tenant_id=tenant_id, source_type="matchup")
        if doc_id:
            qs = qs.filter(document_id=doc_id)

        # doc별로 그룹핑
        from collections import defaultdict
        by_doc: dict[int, list[MatchupProblem]] = defaultdict(list)
        for p in qs.only("id", "document_id", "number", "text"):
            if p.document_id:
                by_doc[p.document_id].append(p)

        total_changed = 0
        for did, problems in sorted(by_doc.items()):
            plans = []
            for p in problems:
                real = extract_real_number(p.text or "")
                if real is None or real == p.number:
                    continue
                plans.append((p, real))

            if not plans:
                continue

            self.stdout.write(f"== doc {did}: {len(plans)}건 보정 ==")
            if dry:
                for p, real in plans:
                    self.stdout.write(f"  prob {p.id}: #{p.number} → #{real} '{(p.text or '')[:30]}...'")
                continue

            # 1차: 임시 번호로 옮기기 (900+ 슬롯)
            tmp_base = 900
            for i, (p, real) in enumerate(plans):
                p.number = tmp_base + i
                p.save(update_fields=["number", "updated_at"])

            # 2차: 진짜 번호로 옮기기
            for p, real in plans:
                p.number = real
                p.save(update_fields=["number", "updated_at"])

            total_changed += len(plans)
            self.stdout.write(f"  보정 완료")

        self.stdout.write(f"\n총 {total_changed}건 보정.")
