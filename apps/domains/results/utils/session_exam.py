# apps/domains/results/utils/session_exam.py
from __future__ import annotations

from typing import List, Optional

from django.db.models import QuerySet

from apps.domains.lectures.models import Session
from apps.domains.exams.models import Exam


def _has_relation(model, name: str) -> bool:
    """
    모델에 특정 field/relation이 존재하는지 검사.
    프로젝트 히스토리(세션-시험 관계가 바뀌는 과정)에서
    런타임에 안전하게 동작시키기 위한 방어 유틸.
    """
    try:
        return any(getattr(f, "name", None) == name for f in model._meta.get_fields())
    except Exception:
        return False


# ---------------------------------------------------------------------
# ✅ Canonical API: Session -> Exams
# ---------------------------------------------------------------------
def get_exams_for_session(session: Session) -> QuerySet[Exam]:
    """
    ✅ 단일 진실: Session에 연결된 Exam queryset 반환
    """
    # 1) Session.exams (M2M)
    if _has_relation(Session, "exams") and hasattr(session, "exams"):
        try:
            return session.exams.all()
        except Exception:
            pass

    # 2) Exam.sessions reverse (M2M)
    if _has_relation(Exam, "sessions"):
        return Exam.objects.filter(sessions__id=int(session.id)).distinct()

    # 3) Legacy fallback: Session.exam (FK)
    exam_id = getattr(session, "exam_id", None)
    if exam_id:
        return Exam.objects.filter(id=int(exam_id))

    return Exam.objects.none()


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
    exam_id = int(exam_id)

    # 1) Session.exams (M2M)
    if _has_relation(Session, "exams"):
        try:
            return Session.objects.filter(exams__id=exam_id).distinct()
        except Exception:
            pass

    # 2) Exam.sessions reverse (M2M)
    if _has_relation(Exam, "sessions"):
        return Session.objects.filter(exams__id=exam_id).distinct()

    # 3) legacy: Session.exam FK
    return Session.objects.filter(exam_id=exam_id).distinct()


def get_primary_session_for_exam(exam_id: int) -> Optional[Session]:
    """
    ✅ 대표 session 반환
    """
    qs = get_sessions_for_exam(int(exam_id))
    if not qs.exists():
        return None

    if hasattr(Session, "order"):
        try:
            return qs.order_by("order", "id").first()
        except Exception:
            pass

    return qs.order_by("id").first()


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
