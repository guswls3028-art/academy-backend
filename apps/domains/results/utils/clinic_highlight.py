# apps/domains/results/utils/clinic_highlight.py
"""
클리닉 대상 하이라이트 유틸리티 (SSOT)

name_highlight_clinic_target = True 조건:
  1. 해당 enrollment에 미해결(resolved_at IS NULL) 자동 ClinicLink 존재
  2. 해당 enrollment가 클리닉 세션에 출석(ATTENDED)한 적 없음

세션 스코프와 글로벌 스코프 두 가지 모드 제공:
  - session 지정: 해당 세션의 ClinicLink만 검사 (성적/시험/과제 탭)
  - session 미지정: 모든 미해결 ClinicLink 검사 (학생 목록 등)
"""

from __future__ import annotations

from typing import Any, Dict, Optional, Set

from apps.domains.progress.models import ClinicLink
from apps.domains.clinic.models import SessionParticipant


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
    clinic_qs = ClinicLink.objects.filter(
        is_auto=True,
        resolved_at__isnull=True,
        enrollment_id__in=enrollment_ids,
        tenant=tenant,
    )
    if session is not None:
        clinic_qs = clinic_qs.filter(session=session)

    clinic_ids = set(clinic_qs.values_list("enrollment_id", flat=True).distinct())

    if not clinic_ids:
        return {eid: False for eid in enrollment_ids}

    # 2) 클리닉 출석 완료 enrollment
    attended_ids = set(
        SessionParticipant.objects.filter(
            tenant=tenant,
            enrollment_id__in=clinic_ids,
            status=SessionParticipant.Status.ATTENDED,
        )
        .values_list("enrollment_id", flat=True)
        .distinct()
    )

    # 3) 결과: 대상이면서 미출석 → True
    return {
        eid: (eid in clinic_ids and eid not in attended_ids)
        for eid in enrollment_ids
    }
