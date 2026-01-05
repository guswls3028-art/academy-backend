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


# ============================================================
# 시험 요약 통계 (관리자)
# ============================================================
def get_exam_summary(*, exam_id: int) -> Dict[str, Any]:
    """
    관리자 시험 요약 통계
    - Result + ProgressPolicy + clinic flag 기준
    """

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

    # -----------------------------
    # 커트라인 기준
    # -----------------------------
    session = (
        Session.objects
        .filter(exam__id=exam_id)
        .select_related("lecture")
        .first()
    )

    policy = (
        ProgressPolicy.objects
        .filter(lecture=session.lecture)
        .first()
        if session else None
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
        if session else 0
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
def get_question_stats(*, exam_id: int) -> List[Dict[str, Any]]:
    """
    문항별 통계 (관리자/교사용)
    """

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
# 관리자 성적 리스트 (신규)
# ============================================================
def get_exam_results(*, exam_id: int) -> List[Dict[str, Any]]:
    """
    관리자 성적 테이블용 API
    - Submissions ❌
    - Results + SessionProgress 기준
    """

    results = (
        Result.objects
        .filter(
            target_type="exam",
            target_id=exam_id,
        )
        .select_related(None)
    )

    session = (
        Session.objects
        .filter(exam__id=exam_id)
        .first()
    )

    progress_map = {
        sp.enrollment_id: sp
        for sp in SessionProgress.objects.filter(session=session)
    }

    student_map = {
        s.id: s
        for s in Student.objects.all()
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
