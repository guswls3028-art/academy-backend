# PATH: apps/domains/results/aggregations/global_results.py
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, Optional

from django.utils import timezone

from apps.domains.results.utils.clinic import get_clinic_enrollment_ids_for_session
from apps.domains.results.utils.session_exam import get_exams_for_session
from apps.domains.results.utils.result_queries import latest_results_per_enrollment
from apps.support.results.progress_read_dependencies import (
    session_progress_count_for_session_ids,
    sessions_by_ids,
    sessions_for_global_snapshot,
)


def _safe_int(v: Any, default: int = 0) -> int:
    try:
        return int(v)
    except Exception:
        return int(default)


def _safe_dt(v: Any) -> Optional[datetime]:
    if v is None:
        return None
    if isinstance(v, datetime):
        return v
    try:
        # "2026-02-02T00:00:00Z" 등 ISO 입력 방어
        return datetime.fromisoformat(str(v).replace("Z", "+00:00"))
    except Exception:
        return None


def build_global_results_snapshot(
    *,
    tenant_id: Optional[int] = None,
    lecture_id: Optional[int] = None,
    from_dt: Optional[Any] = None,
    to_dt: Optional[Any] = None,
) -> Dict[str, Any]:
    """
    ✅ 운영용 글로벌 요약 (대시보드/관리자 홈 입력)

    단일 진실:
    - participant_count: SessionProgress row count
    - clinic_count: ClinicLink(is_auto=True) enrollment distinct (세션 합계)
    - exam_result_count: Result (enrollment 중복 방어 latest_results_per_enrollment) 합계
      (여기서는 "시험 수 * 참가자 수" 성격이므로 단순한 '건수'로만 제공)

    반환(고정):
    {
      "scope": {"lecture_id": int|null, "from": iso|null, "to": iso|null},
      "session_count": int,
      "participant_count": int,
      "clinic_enrollment_distinct_count": int,
      "exam_latest_result_count": int,
      "generated_at": "iso"
    }
    """
    if tenant_id is None:
        raise ValueError("tenant_id is required for tenant-isolated global results snapshot")

    l_id = _safe_int(lecture_id) if lecture_id is not None else None
    fdt = _safe_dt(from_dt)
    tdt = _safe_dt(to_dt)

    sessions = sessions_for_global_snapshot(
        tenant_id=int(tenant_id),
        lecture_id=l_id,
        from_dt=fdt,
        to_dt=tdt,
    )

    session_ids = list(sessions.values_list("id", flat=True))
    session_count = len(session_ids)

    if not session_ids:
        return {
            "scope": {
                "lecture_id": l_id,
                "from": fdt.isoformat() if fdt else None,
                "to": tdt.isoformat() if tdt else None,
            },
            "session_count": 0,
            "participant_count": 0,
            "clinic_enrollment_distinct_count": 0,
            "exam_latest_result_count": 0,
            "generated_at": timezone.now().isoformat(),
        }

    participant_count = session_progress_count_for_session_ids(session_ids)

    # clinic enrollment distinct (세션 합계 기준, live source만)
    clinic_pairs: set[tuple[int, int]] = set()
    for session in sessions:
        for enrollment_id in get_clinic_enrollment_ids_for_session(
            session=session,
            include_manual=False,
        ):
            clinic_pairs.add((int(session.id), int(enrollment_id)))
    clinic_enrollment_distinct_count = len(clinic_pairs)

    # exam 최신 Result count (시험 건수 성격)
    exam_latest_result_count = 0
    try:
        # Session -> Exams 스캔
        # (많은 세션에서 N+1이 될 수 있으나 글로벌 요약은 운영에서 호출 빈도 낮다고 가정)
        exam_ids = set()
        for session in sessions_by_ids(session_ids):
            for ex in get_exams_for_session(session):
                exid = getattr(ex, "id", None)
                if exid:
                    exam_ids.add(int(exid))

        for exid in exam_ids:
            rs = latest_results_per_enrollment(target_type="exam", target_id=int(exid))
            exam_latest_result_count += rs.count()
    except Exception:
        exam_latest_result_count = 0

    return {
        "scope": {
            "lecture_id": l_id,
            "from": fdt.isoformat() if fdt else None,
            "to": tdt.isoformat() if tdt else None,
        },
        "session_count": int(session_count),
        "participant_count": int(participant_count),
        "clinic_enrollment_distinct_count": int(clinic_enrollment_distinct_count),
        "exam_latest_result_count": int(exam_latest_result_count),
        "generated_at": timezone.now().isoformat(),
    }
