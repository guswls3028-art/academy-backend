"""매치업 cross-attach 권한 게이트 (Phase 3 fix, 2026-05-12).

board.meta.matchup_report_ids 검증 SSOT:
  - 학원장 published LandingPage 의 hit_reports section items 에 등록된 보고서 ID만 허용
  - 학생/학부모가 API 직접 POST 로 학원장 미공개 보고서 ID 임의 박는 케이스 차단
  - 매치업 immutable 정책 (학원장 작성 데이터 자동 변경 금지) 측면에서 board.meta cross-reference
    형태로 학원장이 미공개로 둔 보고서가 외부 노출되는 우회 경로 차단

본 모듈은 LandingPage 모델만 의존(core read-only). 매치업 도메인 내부에 직접 의존 X.
"""
from __future__ import annotations

from typing import Iterable


MAX_MATCHUP_REPORTS_PER_POST = 3


def get_published_hit_report_ids(tenant) -> set[int]:
    """학원장 published landing 의 hit_reports section 에 등록된 모든 report_id set.

    None/빈 sections/disabled 모두 안전 처리. 미공개 학원이면 빈 set 반환.
    """
    try:
        from apps.core.models import LandingPage
        try:
            landing = LandingPage.objects.get(tenant=tenant, is_published=True)
        except LandingPage.DoesNotExist:
            return set()
        pub = landing.published_config or {}
        out: set[int] = set()
        for sec in (pub.get("sections") or []):
            if sec.get("type") != "hit_reports" or not sec.get("enabled"):
                continue
            for it in (sec.get("items") or []):
                try:
                    out.add(int(it.get("report_id")))
                except (TypeError, ValueError):
                    continue
        return out
    except Exception:
        # fail-closed — 검증 실패 시 빈 set으로 처리 → 모든 ID 거부
        return set()


def filter_allowed_report_ids(tenant, ids: Iterable) -> list[int]:
    """입력 ids 중 published landing 에 등록된 항목만 통과.

    중복 제거 + 최대 N 개 slice + 정수 변환 실패 silent skip.
    빈 list 반환 시 caller 가 meta.matchup_report_ids 자체를 dict 에서 빼는 게 정합.
    """
    if not ids:
        return []
    allowed = get_published_hit_report_ids(tenant)
    if not allowed:
        return []
    seen: set[int] = set()
    out: list[int] = []
    for raw in ids:
        try:
            v = int(raw)
        except (TypeError, ValueError):
            continue
        if v in seen or v not in allowed:
            continue
        seen.add(v)
        out.append(v)
        if len(out) >= MAX_MATCHUP_REPORTS_PER_POST:
            break
    return out
