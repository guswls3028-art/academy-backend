# apps/support/analytics/services/exam_analytics.py
from __future__ import annotations

from typing import Any, Dict, List
from collections import Counter

from django.db.models import Avg, Max, Count, Sum, Case, When, IntegerField

from apps.domains.results.models import Result, ResultItem, ResultFact


def get_exam_summary(*, exam_id: int) -> Dict[str, Any]:
    """
    results 기반 시험 요약 (읽기 전용)

    - Result 기준 집계
    - 채점/정답비교 없음
    """
    qs = Result.objects.filter(target_type="exam", target_id=exam_id)

    agg = qs.aggregate(
        participant_count=Count("id"),
        average_score=Avg("total_score"),
        max_score=Max("max_score"),
    )

    return {
        "target_type": "exam",
        "target_id": int(exam_id),
        "participant_count": int(agg["participant_count"] or 0),
        "average_score": float(agg["average_score"] or 0.0),
        "max_score": float(agg["max_score"] or 0.0),
    }


def get_question_stats(*, exam_id: int) -> List[Dict[str, Any]]:
    """
    results 기반 문항별 통계 (읽기 전용)

    기준:
    - ResultItem은 (result, question_id) 기준 snapshot 1개만 존재
    - 따라서 attempts = 해당 문항을 푼 학생 수
    """
    items = (
        ResultItem.objects
        .filter(result__target_type="exam", result__target_id=exam_id)
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
        wrong = int(r["wrong_count"] or 0)

        answer_rate = (correct / attempts) if attempts > 0 else 0.0

        rows.append(
            {
                "question_id": int(r["question_id"]),
                "attempts": attempts,
                "correct_count": correct,
                "wrong_count": wrong,
                "answer_rate": round(float(answer_rate), 4),
                "avg_score": float(r["avg_score"] or 0.0),
                "max_score": float(r["max_score"] or 0.0),
            }
        )
    return rows


def get_top_wrong_questions(*, exam_id: int, limit: int = 5) -> List[Dict[str, Any]]:
    """
    오답이 많은 문항 TOP N (snapshot 기반)
    """
    stats = get_question_stats(exam_id=exam_id)
    stats.sort(key=lambda x: x["wrong_count"], reverse=True)
    return stats[: max(1, int(limit))]


def get_wrong_answer_distribution(
    *, exam_id: int, question_id: int, limit: int = 5
) -> Dict[str, Any]:
    """
    오답 분포 (Fact 기반: 누적 제출 히스토리)

    - is_correct=False 인 오답만 집계
    - 채점/정답비교 없음 (단순 통계)
    """
    qs = ResultFact.objects.filter(
        target_type="exam",
        target_id=exam_id,
        question_id=question_id,
        is_correct=False,
    ).exclude(answer="")

    counter = Counter(qs.values_list("answer", flat=True))
    total = sum(counter.values())

    top = []
    for ans, cnt in counter.most_common(limit):
        top.append(
            {
                "answer": ans,
                "count": int(cnt),
                "rate": round((cnt / total) * 100.0, 2) if total > 0 else 0.0,
            }
        )

    return {
        "question_id": int(question_id),
        "total": int(total),
        "top": top,
    }
