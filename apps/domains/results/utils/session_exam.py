# apps/domains/results/utils/session_exam.py
from __future__ import annotations

from typing import Any, List, Optional

from apps.support.results.progress_read_dependencies import (
    all_exams_for_session,
    live_exams_for_session,
    live_exams_for_session_id,
    live_sessions_for_exam,
)


# ---------------------------------------------------------------------
# Canonical API: Session -> live regular Exams
# ---------------------------------------------------------------------
def _live_regular_exam_filter() -> dict:
    from apps.support.results.progress_read_dependencies import live_regular_exam_filter

    return live_regular_exam_filter()


def get_exams_for_session(session: Any):
    """
    단일 진실: Session에 연결된 live regular Exam queryset 반환.

    비즈니스 정책:
    - template은 양식/콘텐츠 소스이며 차시 운영 시험이 아니다.
    - Exam.status(OPEN/CLOSED)는 legacy compatibility 필드다.
    - 차시 시험 노출 여부는 regular + is_active + Session M2M 연결로만 판단한다.
    """
    return live_exams_for_session(session)


def get_all_exams_for_session(session: Any):
    """
    Audit/repair 전용: archived/template 포함 원시 Session -> Exam 연결.

    운영 화면/성적/클리닉 판단에서는 get_exams_for_session()을 사용한다.
    """
    return all_exams_for_session(session)


def get_session_exams_for_session_id(session_id: int):
    """
    Session 인스턴스가 없을 때 쓰는 동일 SSOT queryset.
    """
    return live_exams_for_session_id(int(session_id))


def get_exam_ids_for_session(session: Any) -> List[int]:
    """
    ✅ Session -> exam_id list
    """
    return list(get_exams_for_session(session).values_list("id", flat=True))


# ---------------------------------------------------------------------
# ✅ Canonical API: Exam -> Sessions
# ---------------------------------------------------------------------
def get_sessions_for_exam(exam_id: int):
    """
    단일 진실: live regular exam이 속한 Session queryset 반환.
    """
    return live_sessions_for_exam(int(exam_id))


def get_primary_session_for_exam(exam_id: int) -> Optional[Any]:
    """
    ✅ 대표 session 반환
    """
    qs = get_sessions_for_exam(int(exam_id))
    if not qs.exists():
        return None

    return qs.first()


# ---------------------------------------------------------------------
# ✅ NEW: Canonical API (ProgressPipeline용)
# ---------------------------------------------------------------------
def get_session_ids_for_exam(exam_id: int) -> List[int]:
    """
    ✅ Exam -> session_id list (SSOT)

    - Progress / Result / 통계 / 알림 등에서
      "시험 결과 → 어떤 차시를 갱신해야 하는가"를
      판단할 때 사용하는 **유일한 함수**
    """
    return list(
        get_sessions_for_exam(int(exam_id))
        .values_list("id", flat=True)
    )
