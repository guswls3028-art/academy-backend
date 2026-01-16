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

    원칙:
    - 앞으로 'Session ↔ Exam 관계 해석'은 이 함수로만 한다.
    - 어떤 API에서도 다음을 직접 쓰지 말 것:
        session.exams / Exam.objects.filter(sessions__id=...) / Session.objects.filter(exams__id=...)
      → 파일마다 다르게 구현되며 "화면마다 결과 다름" 버그의 원인.
    """
    # 1) Session.exams (M2M) - 가장 canonical
    if _has_relation(Session, "exams") and hasattr(session, "exams"):
        try:
            return session.exams.all()
        except Exception:
            pass

    # 2) Exam.sessions reverse (M2M)
    if _has_relation(Exam, "sessions"):
        return Exam.objects.filter(sessions__id=int(session.id)).distinct()

    # 3) Legacy fallback: Session.exam (FK)
    #    (이미 1:N으로 리팩토링 했다고 했으니, 운영에서 없어도 무방하지만
    #     개발/마이그레이션 중 섞여 있을 수 있어 방어적으로 유지)
    exam_id = getattr(session, "exam_id", None)
    if exam_id:
        return Exam.objects.filter(id=int(exam_id))

    # 4) 못 찾으면 empty
    return Exam.objects.none()


def get_exam_ids_for_session(session: Session) -> List[int]:
    """
    ✅ Session -> exam_id list

    - 통계/집계에서 리스트가 필요할 때 사용.
    - queryset 대신 list[int]로 고정해서 호출측의 중복/오해를 줄임.
    """
    return list(get_exams_for_session(session).values_list("id", flat=True))


# ---------------------------------------------------------------------
# ✅ Canonical API: Exam -> Sessions
# ---------------------------------------------------------------------
def get_sessions_for_exam(exam_id: int) -> QuerySet[Session]:
    """
    ✅ 단일 진실: 특정 exam_id가 속한 Session queryset 반환

    (세션 1 : 시험 N 구조)
    - Session.exams(M2M)가 있으면 그걸 우선 사용
    - 없으면 Exam.sessions reverse 사용
    - 마지막으로 legacy Session.exam FK fallback
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
    ✅ '대표 session'이 필요할 때 사용 (예: legacy API)

    정책:
    - 여러 session이 걸릴 수 있으면 order가 가장 작은(빠른) session을 우선 반환.
    - order 필드가 없거나 정렬 실패 시 그냥 first().
    """
    qs = get_sessions_for_exam(int(exam_id))
    if not qs.exists():
        return None

    # order가 존재할 가능성이 높음(lecture session order)
    if hasattr(Session, "order"):
        try:
            return qs.order_by("order", "id").first()
        except Exception:
            pass

    return qs.order_by("id").first()
