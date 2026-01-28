# apps/domains/results/utils/clinic.py
from __future__ import annotations

from typing import Set

from apps.domains.lectures.models import Session
from apps.domains.progress.models import ClinicLink


def get_clinic_enrollment_ids_for_session(
    *,
    session: Session,
    include_manual: bool = False,
) -> Set[int]:
    """
    ✅ Clinic 단일 규칙 제공

    기본 정책(권장/안전):
    - 운영에서 clinic_required/clinic_rate는 '자동 트리거' 기준으로 통일한다.
      -> include_manual=False (default)

    왜냐하면:
    - 수동 클리닉(강사 추천/요청)은 UX/운영 정책에 따라 케이스가 달라서
      통계에 섞이면 화면마다 "왜 다르냐" 문제가 반복된다.

    필요하면 include_manual=True로
    수동까지 포함한 '전체 clinic 대상'을 만들 수 있다.
    """
    qs = ClinicLink.objects.filter(session=session)

    # ✅ 수정사항(추가): 예약 완료로 분리된 대상자는 clinic_required에서 제외
    qs = qs.filter(resolved_at__isnull=True)

    if not include_manual:
        qs = qs.filter(is_auto=True)

    return set(qs.values_list("enrollment_id", flat=True).distinct())


def is_clinic_required(
    *,
    session: Session,
    enrollment_id: int,
    include_manual: bool = False,
) -> bool:
    """
    ✅ enrollment 단위 clinic 여부 (단일 진실)
    """
    enrollment_id = int(enrollment_id)
    ids = get_clinic_enrollment_ids_for_session(
        session=session,
        include_manual=include_manual,
    )
    return enrollment_id in ids
