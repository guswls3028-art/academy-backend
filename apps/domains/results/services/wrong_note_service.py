# apps/domains/results/services/wrong_note_service.py
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from django.db.models import Q

from apps.domains.results.models import ResultItem
from apps.domains.results.services.answer_matching import format_answer_for_display
from apps.support.results.wrong_note_dependencies import (
    answer_key_map_for_effective_exam,
    exams_with_wrong_note_sessions_by_id,
    exam_questions_by_id,
    explanation_image_key,
    explanation_image_url,
    question_image_key,
    question_image_url,
    regular_exam_ids_by_lecture_and_order,
)


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
    to_session_order: Optional[int] = None

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


def _get_exam_ids_by_lecture_and_order(
    *,
    lecture_id: int,
    from_order: int,
    to_order: Optional[int],
) -> List[int]:
    """
    lecture_id + from_session_order로 exam_id 목록 구하기

    ✅ 규칙:
    - Exam ↔ Session 관계는 Exam.sessions M2M이 단일 진실
    - 강의/차시 필터는 세션의 lecture/order를 통해 계산
    """
    return regular_exam_ids_by_lecture_and_order(
        lecture_id=int(lecture_id),
        from_order=int(from_order),
        to_order=int(to_order) if to_order is not None else None,
    )


def _get_answer_key_map(exam_id: int, *, tenant_id: int) -> Dict[str, Any]:
    """
    AnswerKey v2 (고정):
      answers = { "123": "B", ... }  # key = ExamQuestion.id(str)
    """
    return answer_key_map_for_effective_exam(
        exam_id=int(exam_id),
        tenant_id=int(tenant_id),
    )


def _get_explanation_text(question: Any) -> str:
    if question is None:
        return ""
    try:
        return str(question.explanation.text or "")
    except Exception:
        return ""


def build_wrong_note_source_fingerprint(
    *,
    total: int,
    items: List[Dict[str, Any]],
) -> str:
    """Hash only document-affecting source data, excluding expiring URLs."""
    fingerprint_items = []
    for item in items:
        attempt_created_at = item.get("attempt_created_at")
        fingerprint_item = {
                "exam_id": item.get("exam_id"),
                "exam_title": item.get("exam_title"),
                "session_order": item.get("session_order"),
                "session_title": item.get("session_title"),
                "attempt_id": item.get("attempt_id"),
                "attempt_created_at": (
                    attempt_created_at.isoformat()
                    if hasattr(attempt_created_at, "isoformat")
                    else str(attempt_created_at or "")
                ),
                "question_id": item.get("question_id"),
                "question_number": item.get("question_number"),
                "answer_type": item.get("answer_type"),
                "student_answer": item.get("student_answer"),
                "correct_answer": item.get("correct_answer"),
                "is_correct": item.get("is_correct"),
                "include_in_wrong_note": item.get("include_in_wrong_note"),
                "score": item.get("score"),
                "max_score": item.get("max_score"),
                "question_image_key": item.get("_question_image_key"),
                "question_image_name": item.get("_question_image_name"),
                "explanation_image_key": item.get("_explanation_image_key"),
                "explanation_text": str(
                    (item.get("extra") or {}).get("explanation_text") or ""
                ),
            }
        # Keep the legacy single-enrollment fingerprint byte-for-byte stable
        # across rolling deploys. Selected-source rows opt into the new scope
        # fields explicitly, so queued jobs made by old instances stay valid.
        if any(
            key in item
            for key in ("source_type", "source_id", "enrollment_id")
        ):
            fingerprint_item.update(
                {
                    "source_type": item.get("source_type") or "exam",
                    "source_id": item.get("source_id") or item.get("exam_id"),
                    "enrollment_id": item.get("enrollment_id"),
                }
            )
        fingerprint_items.append(fingerprint_item)
    fingerprint_items.sort(
        key=lambda item: (
            item["session_order"]
            if item["session_order"] is not None
            else 2**31,
            str(item["exam_title"] or ""),
            item["question_number"]
            if item["question_number"] is not None
            else 2**31,
            int(item["question_id"] or 0),
            int(item["exam_id"] or 0),
        )
    )
    encoded = json.dumps(
        {"total": int(total), "items": fingerprint_items},
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


# ======================================================
# Public API
# ======================================================
def list_wrong_notes_for_enrollment(
    *,
    enrollment_id: int,
    q: WrongNoteQuery,
) -> Tuple[int, List[Dict[str, Any]]]:
    """
    대표 Result의 문항 snapshot을 기준으로 현재 틀린 문항만 반환한다.
    append-only ResultFact를 읽으면 재채점 전 오답이나 이미 맞힌 재시험 문항이
    중복 노출되므로 학생에게 전달하는 오답노트의 기준으로 사용할 수 없다.

    반환: (total_count, paged_items)
    """

    enrollment_id = int(enrollment_id)
    offset = max(int(q.offset or 0), 0)
    limit = max(min(int(q.limit or 50), 200), 1)

    base = (
        ResultItem.objects
        .filter(
            result__enrollment_id=enrollment_id,
            result__target_type="exam",
        )
        .filter(Q(is_correct=False) | Q(include_in_wrong_note=True))
        .select_related(
            "result__attempt",
            "result__enrollment",
            "question__sheet",
        )
    )

    # 1) exam_id 필터
    if q.exam_id is not None:
        base = base.filter(result__target_id=int(q.exam_id))

    # 2) lecture_id + from_session_order 필터 (STEP 3-3 승격)
    if q.exam_id is None and q.lecture_id is not None:
        exam_ids = _get_exam_ids_by_lecture_and_order(
            lecture_id=int(q.lecture_id),
            from_order=int(q.from_session_order or 2),
            to_order=q.to_session_order,
        )
        if not exam_ids:
            return 0, []
        base = base.filter(result__target_id__in=exam_ids)

    # 최신 오답 우선
    base = base.order_by("-result__submitted_at", "-id")

    total = base.count()

    facts = list(base[offset: offset + limit])
    if not facts:
        return total, []
    tenant_id = int(facts[0].result.enrollment.tenant_id)

    # 질문 정보/정답키 붙이기 위해 question_ids, exam_ids 수집
    question_ids = [int(item.question_id) for item in facts]
    exam_ids = list({int(item.result.target_id) for item in facts})

    questions_map = exam_questions_by_id(
        question_ids=question_ids,
        tenant_id=tenant_id,
    )
    exams_map = exams_with_wrong_note_sessions_by_id(
        exam_ids=exam_ids,
        lecture_id=q.lecture_id,
        tenant_id=tenant_id,
        from_order=(
            int(q.from_session_order or 1)
            if q.exam_id is None and q.lecture_id is not None
            else None
        ),
        to_order=(
            int(q.to_session_order)
            if q.exam_id is None and q.to_session_order is not None
            else None
        ),
    )

    answer_key_cache: Dict[int, Dict[str, Any]] = {
        exid: _get_answer_key_map(exid, tenant_id=tenant_id)
        for exid in exam_ids
    }

    out: List[Dict[str, Any]] = []

    for item in facts:
        exid = int(item.result.target_id)
        qobj = questions_map.get(int(item.question_id))
        exam = exams_map.get(exid)
        sessions = list(getattr(exam, "wrong_note_sessions", []) or [])
        session = sessions[0] if sessions else None

        question_number = getattr(qobj, "number", None) if qobj else None
        answer_type = (getattr(qobj, "question_kind", "") or "") if qobj else ""

        correct_answer = ""
        if qobj:
            correct_answer = format_answer_for_display(
                answer_key_cache.get(exid, {}).get(str(qobj.id)) or ""
            )
        problem_key = (
            question_image_key(question=qobj, tenant_id=tenant_id) if qobj else ""
        )
        solution_key = (
            explanation_image_key(question=qobj, tenant_id=tenant_id) if qobj else ""
        )
        explanation_text = _get_explanation_text(qobj)

        out.append({
            "exam_id": exid,
            "exam_title": str(getattr(exam, "title", "") or ""),
            "session_order": getattr(session, "regular_order", None),
            "session_title": str(getattr(session, "title", "") or ""),
            "attempt_id": int(getattr(item.result, "attempt_id", 0) or 0),
            "attempt_created_at": (
                getattr(getattr(item.result, "attempt", None), "created_at", None)
                or getattr(item.result, "submitted_at", None)
            ),

            "question_id": int(item.question_id),
            "question_number": _safe_int(question_number),
            "answer_type": str(answer_type),
            "question_image_url": (
                question_image_url(question=qobj, tenant_id=tenant_id) if qobj else ""
            ),
            "has_question_image": bool(
                qobj and (problem_key or getattr(qobj, "image", None))
            ),
            "explanation_image_url": (
                explanation_image_url(question=qobj, tenant_id=tenant_id)
                if qobj
                else ""
            ),
            "has_teacher_explanation": bool(
                qobj and (explanation_text or solution_key)
            ),

            "student_answer": str(item.answer or ""),
            "correct_answer": str(correct_answer or ""),

            "is_correct": bool(item.is_correct),
            "include_in_wrong_note": bool(item.include_in_wrong_note),
            "score": float(item.score or 0.0),
            "max_score": float(item.max_score or 0.0),

            "meta": {},
            "extra": {
                "explanation_text": explanation_text,
            },
            # PDF 생성 서비스에서만 사용하고 API serializer에서는 노출하지 않는다.
            "_question_image_key": problem_key,
            "_question_image_name": str(
                getattr(getattr(qobj, "image", None), "name", "") or ""
            ),
            "_explanation_image_key": solution_key,
        })

    return total, out
