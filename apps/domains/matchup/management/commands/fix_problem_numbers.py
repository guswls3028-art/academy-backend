# PATH: apps/domains/matchup/management/commands/fix_problem_numbers.py
# 자동분리 OCR 박스 누락으로 sequential offset 어긋난 problem number를
# 텍스트의 실제 번호로 일괄 PATCH 보정.
"""
사용:
  python manage.py fix_problem_numbers --tenant-id 1
  python manage.py fix_problem_numbers --tenant-id 1 --doc-id 153
  python manage.py fix_problem_numbers --dry-run --tenant-id 1
  python manage.py fix_problem_numbers --tenant-id 2 --cleanup-temp  # 985~999 leftover만 정리

동작:
  - problem.text 첫 줄에서 "N." 또는 "[서답형 N]" 패턴 추출
  - DB number와 다르면 두 단계 PATCH (임시 번호 → 진짜 번호)로 충돌 회피
  - same-doc 안에서 number 충돌 시 임시 번호 슬롯(800+) 활용
  - 2차 PATCH에서 충돌이 발생하면 임시 번호로 보존하지 않고 가장 가까운 빈
    번호로 폴백 (985~990 stuck slot 누적 방지)

Why: 운영 T2에서 920~990 범위에 192건 stuck (이전 fix 2차가 부분 실패하여
잔류). 본 명령 재실행 시 기본적으로 그 stuck row까지 함께 다시 매핑한다.
"""
from __future__ import annotations

import re
from django.core.management.base import BaseCommand
from django.db import IntegrityError, transaction

from apps.domains.matchup.models import MatchupProblem


_NUM_RE = re.compile(r"^(\d{1,3})\s*[.,]\s")
_ESSAY_RE = re.compile(r"^\[\s*서\s*답형\s*(\d+)")
# 임시 슬롯 (1차 PATCH가 잠깐 사용). 운영 problem.number 정상 범위(1~460)와
# 사용자 paste 가능 범위(~999)와 명확히 구분되도록 800~879로 분리.
_TMP_SLOT_BASE = 800
_TMP_SLOT_MAX = 880  # 80 슬롯이면 한 doc 안 동시 충돌 처리에 충분.


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


def _free_number_for_collision(taken: set[int], real: int) -> int:
    """real 번호가 이미 다른 row에 잡혀 있을 때 가까운 빈 번호로 fallback.

    선택형(1~99): real ± 1 부터 99 까지 차례로 시도 후 그래도 없으면 임시 슬롯.
    서답형(100~199): real ± 1 부터 199 까지.
    """
    space_lo, space_hi = (100, 199) if real >= 100 else (1, 99)
    for delta in range(1, max(space_hi - space_lo + 1, 100)):
        for cand in (real + delta, real - delta):
            if space_lo <= cand <= space_hi and cand not in taken:
                return cand
    # 동일 space 다 차면 임시 슬롯에서 빈 번호.
    for cand in range(_TMP_SLOT_BASE, _TMP_SLOT_MAX):
        if cand not in taken:
            return cand
    return _TMP_SLOT_MAX  # 마지막 보루 — 사실상 unreachable.


class Command(BaseCommand):
    help = "매치업 problem number를 텍스트 실제 번호로 일괄 보정 (OCR sequential offset + temp slot 누적 정리)."

    def add_arguments(self, parser):
        parser.add_argument("--tenant-id", type=int, required=True)
        parser.add_argument("--doc-id", type=int, default=None)
        parser.add_argument("--dry-run", action="store_true")
        parser.add_argument(
            "--cleanup-temp",
            action="store_true",
            help="임시 슬롯(800~880) + 과거 stuck range(900~999)에 잠긴 problem만 추출 → 텍스트로 재매핑",
        )

    def handle(self, *args, **opts):
        tenant_id = opts["tenant_id"]
        doc_id = opts.get("doc_id")
        dry = opts["dry_run"]
        cleanup = opts["cleanup_temp"]

        qs = MatchupProblem.objects.filter(tenant_id=tenant_id, source_type="matchup")
        if doc_id:
            qs = qs.filter(document_id=doc_id)
        if cleanup:
            # 800~999 stuck range만 대상 (정상 1~460 범위는 건드리지 않음)
            qs = qs.filter(number__gte=_TMP_SLOT_BASE, number__lte=999)

        # doc별로 그룹핑 — 충돌 검사용으로 doc 전체 problem도 함께 로드.
        from collections import defaultdict
        target_by_doc: dict[int, list[MatchupProblem]] = defaultdict(list)
        for p in qs.only("id", "document_id", "number", "text"):
            if p.document_id:
                target_by_doc[p.document_id].append(p)

        if not target_by_doc:
            self.stdout.write("대상 없음.")
            return

        total_changed = 0
        total_unresolved = 0
        for did in sorted(target_by_doc.keys()):
            target = target_by_doc[did]
            # doc 전체 problem (변경 대상 + 그대로) — 충돌 set 계산용
            doc_all = list(MatchupProblem.objects.filter(document_id=did).only("id", "number"))
            taken: set[int] = {p.number for p in doc_all if p.number is not None}

            plans = []
            for p in target:
                real = extract_real_number(p.text or "")
                if real is None:
                    continue
                if real == p.number:
                    continue
                plans.append((p, real))

            if not plans:
                continue

            self.stdout.write(f"== doc {did}: {len(plans)}건 보정 ==")
            if dry:
                for p, real in plans:
                    self.stdout.write(f"  prob {p.id}: #{p.number} → #{real} '{(p.text or '')[:30]}...'")
                continue

            # 1차: 임시 슬롯으로 옮기기 (충돌 회피)
            for i, (p, _real) in enumerate(plans):
                slot = _TMP_SLOT_BASE + i
                if slot >= _TMP_SLOT_MAX:
                    # 매우 큰 doc — 안전상 skip
                    continue
                # 자기 자신을 taken에서 제외
                taken.discard(p.number)
                p.number = slot
                with transaction.atomic():
                    p.save(update_fields=["number", "updated_at"])
                taken.add(slot)

            # 2차: 진짜 번호로 옮기기 (충돌 시 인근 빈 번호로 fallback)
            unresolved_doc = 0
            for p, real in plans:
                taken.discard(p.number)
                target_num = real if real not in taken else _free_number_for_collision(taken, real)
                try:
                    with transaction.atomic():
                        p.number = target_num
                        p.save(update_fields=["number", "updated_at"])
                    taken.add(target_num)
                except IntegrityError:
                    unresolved_doc += 1
                    # taken 갱신 안 됨 — temp 슬롯에 남음
            total_unresolved += unresolved_doc
            total_changed += len(plans) - unresolved_doc
            self.stdout.write(f"  보정 완료 (unresolved={unresolved_doc})")

        self.stdout.write(f"\n총 {total_changed}건 보정. 잔여 stuck: {total_unresolved}건.")
