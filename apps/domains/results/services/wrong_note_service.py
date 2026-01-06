# apps/domains/results/services/wrong_note_service.py
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from django.db.models import Q
from django.utils import timezone

from apps.domains.results.models import ResultFact
from apps.domains.exams.models import ExamQuestion, AnswerKey, Exam


# ======================================================
# Request DTO
# ======================================================
@dataclass(frozen=True)
class WrongNoteQuery:
    """
    오답노트 조회 파라미터

    ✅ STEP 3-3 승격
    - lecture_id/from_session_order 필터를 서비스 책임으로 끌어올림
      (View/Worker/PDF 모두 같은 규칙 사용)

    - offset/limit은 단순 페이지네이션
    """
    exam_id: Optional[int] = None
    lecture_id: Optional[int] = None
    from_session_order: int = 2

    offset: int = 0
    limit: int = 50


# ======================================================
# Internal helpers
# ======================================================
def _safe_int(v: Any, default: Optional[int] = None) -> Optional[int]:
    try:
        return int(v)
    except Exception:
        return default


def _has_relation(model, name: str) -> bool:
    """
    Exam 모델에 특정 relation(name)이 존재하는지 검사
    """
    try:
        return any(getattr(f, "name", None) == name for f in model._meta.get_fields())
    except Exception:
        return False


def _get_exam_ids_by_lecture_and_order(*, lecture_id: int, from_order: int) -> List[int]:
    """
    lecture_id + from_session_order로 exam_id 목록 구하기

    ⚠️ 프로젝트별로 Exam ↔ Session reverse relation 이름이 다를 수 있음:
    - sessions / session / session_set ...
    그래서 가능한 후보들을 모두 검사해 안전하게 필터링.

    ✅ 규칙:
    - 관계를 못 찾으면 빈 리스트 반환(=안전하게 결과 없음)
    """
    exam_qs = Exam.objects.filter(lecture_id=int(lecture_id))

    # 우선순위 후보들
    # 1) sessions
    if _has_relation(Exam, "sessions"):
        exam_qs = exam_qs.filter(sessions__order__gte=int(from_order))
        return list(exam_qs.values_list("id", flat=True))

    # 2) session (1:1 혹은 FK)
    if _has_relation(Exam, "session"):
        exam_qs = exam_qs.filter(session__order__gte=int(from_order))
        return list(exam_qs.values_list("id", flat=True))

    # 3) session_set (Django default reverse name)
    if _has_relation(Exam, "session_set"):
        exam_qs = exam_qs.filter(session_set__order__gte=int(from_order))
        return list(exam_qs.values_list("id", flat=True))

    # 못 찾으면 안전하게 none
    return []


def _get_answer_key_map(exam_id: int) -> Dict[str, Any]:
    """
    AnswerKey v2 (고정):
      answers = { "123": "B", ... }  # key = ExamQuestion.id(str)
    """
    ak = AnswerKey.objects.filter(exam_id=int(exam_id)).first()
    answers = getattr(ak, "answers", None) if ak else None
    return answers if isinstance(answers, dict) else {}


# ======================================================
# Public API
# ======================================================
def list_wrong_notes_for_enrollment(
    *,
    enrollment_id: int,
    q: WrongNoteQuery,
) -> Tuple[int, List[Dict[str, Any]]]:
    """
    ✅ 현재 프로젝트의 ResultFact 구조에 맞는 “정석” 구현

    ResultFact = 문항 1개 이벤트(append-only)
      - question_id/answer/is_correct/score/max_score/meta/source 가 Fact에 직접 있음

    반환: (total_count, paged_items)
    """

    enrollment_id = int(enrollment_id)
    offset = max(int(q.offset or 0), 0)
    limit = max(min(int(q.limit or 50), 200), 1)

    base = ResultFact.objects.filter(
        enrollment_id=enrollment_id,
        target_type="exam",
        is_correct=False,          # 오답만
    )

    # 1) exam_id 필터
    if q.exam_id is not None:
        base = base.filter(target_id=int(q.exam_id))

    # 2) lecture_id + from_session_order 필터 (STEP 3-3 승격)
    if q.lecture_id is not None:
        exam_ids = _get_exam_ids_by_lecture_and_order(
            lecture_id=int(q.lecture_id),
            from_order=int(q.from_session_order or 2),
        )
        if not exam_ids:
            return 0, []
        base = base.filter(target_id__in=exam_ids)

    # 최신 오답 우선
    base = base.order_by("-id")

    total = base.count()

    facts = list(base[offset: offset + limit])

    # 질문 정보/정답키 붙이기 위해 question_ids, exam_ids 수집
    question_ids = [int(f.question_id) for f in facts]
    exam_ids = list({int(f.target_id) for f in facts})

    questions_map = (
        ExamQuestion.objects
        .filter(id__in=question_ids)
        .select_related("sheet")
        .in_bulk(field_name="id")
    )

    answer_key_cache: Dict[int, Dict[str, Any]] = {
        exid: _get_answer_key_map(exid) for exid in exam_ids
    }

    out: List[Dict[str, Any]] = []

    for f in facts:
        exid = int(f.target_id)
        qobj = questions_map.get(int(f.question_id))

        question_number = getattr(qobj, "number", None) if qobj else None
        answer_type = (getattr(qobj, "answer_type", "") or "") if qobj else ""

        correct_answer = ""
        if qobj:
            correct_answer = str(answer_key_cache.get(exid, {}).get(str(qobj.id)) or "")

        out.append({
            "exam_id": exid,
            "attempt_id": int(getattr(f, "attempt_id", 0) or 0),
            # attempt_created_at 필드가 따로 없으니 created_at을 사용
            "attempt_created_at": getattr(f, "created_at", None),

            "question_id": int(f.question_id),
            "question_number": _safe_int(question_number),
            "answer_type": str(answer_type),

            "student_answer": str(f.answer or ""),
            "correct_answer": str(correct_answer or ""),

            "is_correct": False,
            "score": float(f.score or 0.0),
            "max_score": float(f.max_score or 0.0),

            "meta": f.meta if f.meta is not None else {},
            "extra": {},
        })

    return total, out
