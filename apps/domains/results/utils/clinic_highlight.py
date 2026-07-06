# apps/domains/results/utils/clinic_highlight.py
"""
클리닉 대상 하이라이트 유틸리티 (SSOT)

name_highlight_clinic_target = True 조건:
  1. 해당 enrollment에 미해결(resolved_at IS NULL) 자동 ClinicLink 존재
  2. 해당 enrollment/session의 최종 진행 상태가 완료가 아님
  3. 해당 enrollment가 클리닉 세션에 출석(ATTENDED)한 적 없음

세션 스코프와 글로벌 스코프 두 가지 모드 제공:
  - session 지정: 해당 세션의 ClinicLink만 검사 (성적/시험/과제 탭)
  - session 미지정: 모든 미해결 ClinicLink 검사 (학생 목록 등)
"""

from __future__ import annotations

from typing import Any, Dict, Set

from apps.domains.results.utils.clinic import filter_live_source_links
from apps.support.results.progress_read_dependencies import (
    attended_clinic_enrollment_ids,
    completed_session_progress_pairs,
    unresolved_auto_clinic_links_for_enrollments,
)


def compute_clinic_highlight_map(
    *,
    tenant: Any,
    enrollment_ids: Set[int],
    session=None,
) -> Dict[int, bool]:
    """
    enrollment_id → name_highlight_clinic_target 매핑 반환.

    Args:
        tenant: 테넌트 (필수 — 격리)
        enrollment_ids: 검사 대상 enrollment ID 집합
        session: 특정 세션으로 스코프 (None이면 글로벌)

    Returns:
        {enrollment_id: True/False}
    """
    if not enrollment_ids:
        return {}

    # 1) 클리닉 대상 enrollment (미해결 자동 ClinicLink)
    # tenant 격리: ClinicLink.tenant FK로 직접 필터 (cross-tenant 누출 방어)
    clinic_qs = unresolved_auto_clinic_links_for_enrollments(
        tenant=tenant,
        enrollment_ids=enrollment_ids,
        session=session,
    )
    clinic_links = filter_live_source_links(
        clinic_qs.order_by("id"),
        tenant=tenant,
    )
    if not clinic_links:
        return {eid: False for eid in enrollment_ids}

    session_ids = list({int(link.session_id or 0) for link in clinic_links} - {0})
    clinic_enrollment_ids = list({int(link.enrollment_id or 0) for link in clinic_links} - {0})
    completed_pairs = completed_session_progress_pairs(
        session_ids=session_ids,
        enrollment_ids=clinic_enrollment_ids,
    )
    clinic_ids = {
        int(link.enrollment_id)
        for link in clinic_links
        if (int(link.enrollment_id), int(link.session_id)) not in completed_pairs
    }

    if not clinic_ids:
        return {eid: False for eid in enrollment_ids}

    # 2) 클리닉 출석 완료 enrollment
    attended_ids = attended_clinic_enrollment_ids(
        tenant=tenant,
        enrollment_ids=clinic_ids,
    )

    # 3) 결과: 대상이면서 미출석 → True
    return {
        eid: (eid in clinic_ids and eid not in attended_ids)
        for eid in enrollment_ids
    }
