# apps/domains/results/utils/session_exam.py
from __future__ import annotations

from typing import List, Optional

from django.db.models import QuerySet

from apps.domains.lectures.models import Session
from apps.domains.exams.models import Exam


EXAM_SESSION_ORDERING = ("display_order", "created_at", "id")


# ---------------------------------------------------------------------
# ✅ Canonical API: Session -> Exams
# ---------------------------------------------------------------------
def get_exams_for_session(session: Session) -> QuerySet[Exam]:
    """
    ✅ 단일 진실: Session에 연결된 Exam queryset 반환.

    Session ↔ Exam 관계는 Exam.sessions M2M 하나만 사용한다.
    """
    return session.exams.all().order_by(*EXAM_SESSION_ORDERING)


def get_session_exams_for_session_id(session_id: int) -> QuerySet[Exam]:
    """
    Session 인스턴스가 없을 때 쓰는 동일 SSOT queryset.
    """
    return (
        Exam.objects
        .filter(sessions__id=int(session_id))
        .distinct()
        .order_by(*EXAM_SESSION_ORDERING)
    )


def get_exam_ids_for_session(session: Session) -> List[int]:
    """
    ✅ Session -> exam_id list
    """
    return list(get_exams_for_session(session).values_list("id", flat=True))


# ---------------------------------------------------------------------
# ✅ Canonical API: Exam -> Sessions
# ---------------------------------------------------------------------
def get_sessions_for_exam(exam_id: int) -> QuerySet[Session]:
    """
    ✅ 단일 진실: 특정 exam_id가 속한 Session queryset 반환
    """
    return (
        Session.objects
        .filter(exams__id=int(exam_id))
        .distinct()
        .order_by("order", "id")
    )


def get_primary_session_for_exam(exam_id: int) -> Optional[Session]:
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
