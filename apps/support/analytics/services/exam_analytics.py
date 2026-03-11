# apps/support/analytics/services/exam_analytics.py
from __future__ import annotations

from typing import Any, Dict, List
from collections import Counter

from django.db.models import (
    Avg,
    Max,
    Min,
    Count,
    Sum,
    Case,
    When,
    IntegerField,
)

from apps.domains.results.models import Result, ResultItem, ResultFact
from apps.domains.progress.models import ProgressPolicy, SessionProgress
from apps.domains.lectures.models import Session
from apps.domains.students.models import Student


def _get_tenant_session(exam_id: int, tenant):
    """시험에 연결된 세션을 tenant 격리하여 조회. 없으면 None."""
    return (
        Session.objects
        .filter(exam__id=exam_id, lecture__tenant=tenant)
        .select_related("lecture")
        .first()
    )


_EMPTY_SUMMARY = lambda exam_id: {
    "target_type": "exam",
    "target_id": int(exam_id),
    "participant_count": 0,
    "avg_score": 0.0,
    "min_score": 0.0,
    "max_score": 0.0,
    "pass_count": 0,
    "fail_count": 0,
    "pass_rate": 0.0,
    "clinic_count": 0,
}


# ============================================================
# 시험 요약 통계 (관리자)
# ============================================================
def get_exam_summary(*, exam_id: int, tenant) -> Dict[str, Any]:
    """
    관리자 시험 요약 통계
    - tenant 격리: Session → lecture.tenant 기준
    """

    session = _get_tenant_session(exam_id, tenant)
    if not session:
        return _EMPTY_SUMMARY(exam_id)

    qs = Result.objects.filter(
        target_type="exam",
        target_id=exam_id,
    )

    agg = qs.aggregate(
        participant_count=Count("id"),
        avg_score=Avg("total_score"),
        min_score=Min("total_score"),
        max_score=Max("total_score"),
    )

    policy = (
        ProgressPolicy.objects
        .filter(lecture=session.lecture)
        .first()
    )

    pass_score = policy.exam_pass_score if policy else 0

    pass_count = qs.filter(total_score__gte=pass_score).count()
    fail_count = qs.filter(total_score__lt=pass_score).count()

    participant_count = agg["participant_count"] or 0
    pass_rate = (
        pass_count / participant_count
        if participant_count else 0.0
    )

    clinic_count = (
        SessionProgress.objects
        .filter(
            session=session,
            clinic_required=True,
        )
        .count()
    )

    return {
        "target_type": "exam",
        "target_id": int(exam_id),

        "participant_count": participant_count,

        "avg_score": float(agg["avg_score"] or 0.0),
        "min_score": float(agg["min_score"] or 0.0),
        "max_score": float(agg["max_score"] or 0.0),

        "pass_count": pass_count,
        "fail_count": fail_count,
        "pass_rate": round(float(pass_rate), 4),

        "clinic_count": clinic_count,
    }


# ============================================================
# 문항별 통계
# ============================================================
def get_question_stats(*, exam_id: int, tenant) -> List[Dict[str, Any]]:
    """
    문항별 통계 (관리자/교사용)
    - tenant 격리: Session → lecture.tenant 기준으로 exam 소속 검증
    """

    session = _get_tenant_session(exam_id, tenant)
    if not session:
        return []

    items = (
        ResultItem.objects
        .filter(
            result__target_type="exam",
            result__target_id=exam_id,
        )
        .values("question_id")
        .annotate(
            attempts=Count("id"),
            correct_count=Sum(
                Case(
                    When(is_correct=True, then=1),
                    default=0,
                    output_field=IntegerField(),
                )
            ),
            wrong_count=Sum(
                Case(
                    When(is_correct=False, then=1),
                    default=0,
                    output_field=IntegerField(),
                )
            ),
            avg_score=Avg("score"),
            max_score=Max("max_score"),
        )
        .order_by("question_id")
    )

    rows: List[Dict[str, Any]] = []

    for r in items:
        attempts = int(r["attempts"] or 0)
        correct = int(r["correct_count"] or 0)

        correct_rate = (
            correct / attempts
            if attempts else 0.0
        )

        rows.append({
            "question_id": int(r["question_id"]),
            "attempts": attempts,
            "correct_count": correct,
            "wrong_count": int(r["wrong_count"] or 0),
            "correct_rate": round(float(correct_rate), 4),
            "avg_score": float(r["avg_score"] or 0.0),
            "max_score": float(r["max_score"] or 0.0),
        })

    return rows


# ============================================================
# 오답 TOP
# ============================================================
def get_top_wrong_questions(*, exam_id: int, tenant, limit: int = 5) -> List[Dict[str, Any]]:
    """오답이 많은 문항 TOP N (tenant 격리)"""
    session = _get_tenant_session(exam_id, tenant)
    if not session:
        return []

    rows = get_question_stats(exam_id=exam_id, tenant=tenant)
    rows.sort(key=lambda r: r["wrong_count"], reverse=True)
    return rows[:limit]


# ============================================================
# 오답 분포
# ============================================================
def get_wrong_answer_distribution(
    *, exam_id: int, question_id: int, tenant, limit: int = 5
) -> Dict[str, Any]:
    """특정 문항의 오답 분포 (tenant 격리)"""
    session = _get_tenant_session(exam_id, tenant)
    if not session:
        return {"question_id": question_id, "distribution": []}

    wrong_items = (
        ResultItem.objects
        .filter(
            result__target_type="exam",
            result__target_id=exam_id,
            question_id=question_id,
            is_correct=False,
        )
        .values_list("student_answer", flat=True)
    )

    counter = Counter(wrong_items)
    top = counter.most_common(limit)

    return {
        "question_id": question_id,
        "distribution": [
            {"answer": ans or "", "count": cnt}
            for ans, cnt in top
        ],
    }


# ============================================================
# 관리자 성적 리스트
# ============================================================
def get_exam_results(*, exam_id: int, tenant) -> List[Dict[str, Any]]:
    """
    관리자 성적 테이블용 API
    - tenant 격리: Session → lecture.tenant 기준
    - Student.objects.all() → tenant 스코프로 제한
    """

    session = _get_tenant_session(exam_id, tenant)
    if not session:
        return []

    results = (
        Result.objects
        .filter(
            target_type="exam",
            target_id=exam_id,
        )
    )

    progress_map = {
        sp.enrollment_id: sp
        for sp in SessionProgress.objects.filter(session=session)
    }

    # tenant 스코프 학생만 조회 (cross-tenant 방지)
    student_map = {
        s.id: s
        for s in Student.objects.filter(tenant=tenant)
    }

    rows: List[Dict[str, Any]] = []

    for r in results:
        sp = progress_map.get(r.enrollment_id)

        student = student_map.get(
            getattr(sp, "student_id", None)
        )

        rows.append({
            "enrollment_id": r.enrollment_id,
            "student_name": student.name if student else "-",

            "total_score": r.total_score,
            "max_score": r.max_score,

            "passed": bool(sp and not sp.failed),
            "clinic_required": bool(sp and sp.clinic_required),

            "submitted_at": r.submitted_at,
        })

    return rows
