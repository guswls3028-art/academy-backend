# apps/domains/results/services/question_stats_service.py
from __future__ import annotations

from typing import Dict, List, Optional
from django.db.models import (
    Avg,
    Count,
    ExpressionWrapper,
    F,
    FloatField,
    Max,
    Q,
    Subquery,
)

from apps.domains.results.models import ResultFact
from apps.support.results.question_stats_dependencies import (
    question_numbers_by_id_for_exam,
)


def _current_question_facts(
    *,
    exam_id: int,
    attempt_ids: Optional[List[int]] = None,
    legacy_enrollment_ids: Optional[List[int]] = None,
):
    """Return the latest non-absence fact for each attempt/question pair."""
    qs = (
        ResultFact.objects.filter(
            target_type="exam",
            target_id=int(exam_id),
        )
        .exclude(question_id=0)
        .filter(
            Q(attempt__meta__status__isnull=True)
            | ~Q(attempt__meta__status="NOT_SUBMITTED")
        )
    )
    if attempt_ids is not None or legacy_enrollment_ids is not None:
        scope = Q(pk__in=[])
        if attempt_ids:
            scope |= Q(attempt_id__in=attempt_ids)
        if legacy_enrollment_ids:
            scope |= Q(
                attempt_id__isnull=True,
                enrollment_id__in=legacy_enrollment_ids,
            )
        qs = qs.filter(scope)
    latest_fact_ids = (
        qs.order_by()
        .values("attempt_id", "enrollment_id", "question_id")
        .annotate(latest_id=Max("id"))
        .values("latest_id")
    )
    return qs.filter(id__in=Subquery(latest_fact_ids))


class QuestionStatsService:
    """
    시험 문항 통계 단일 진실 (정석)

    🔥 기준:
    - ResultFact (append-only)만 사용
    - Result / ResultItem / attempt 교체 여부와 무관
    - 운영/통계/분석 일관성 보장
    """

    # ======================================================
    # A) 문항별 기본 통계
    # ======================================================
    @staticmethod
    def per_question_stats(
        *,
        exam_id: int,
        attempt_ids: Optional[List[int]] = None,
        legacy_enrollment_ids: Optional[List[int]] = None,
    ) -> List[Dict]:
        """
        문항별 통계
        - 응시 수
        - 정답 수
        - 정답률
        - 평균 점수
        - 최대 점수
        """

        qs = _current_question_facts(
            exam_id=exam_id,
            attempt_ids=attempt_ids,
            legacy_enrollment_ids=legacy_enrollment_ids,
        )

        rows = list(
            qs.values("question_id")
            .annotate(
                attempts=Count("id"),
                correct=Count("id", filter=Q(is_correct=True)),
                avg_score=Avg("score"),
                max_score=Max("score"),
            )
            .annotate(
                accuracy=ExpressionWrapper(
                    F("correct") * 1.0 / F("attempts"),
                    output_field=FloatField(),
                )
            )
            .order_by("question_id")
        )

        question_numbers = question_numbers_by_id_for_exam(
            exam_id=int(exam_id),
            question_ids=[int(row["question_id"]) for row in rows],
        )

        return [
            {
                "question_id": row["question_id"],
                "question_number": question_numbers[int(row["question_id"])],
                "attempts": int(row["attempts"] or 0),
                "correct": int(row["correct"] or 0),
                "accuracy": round(float(row["accuracy"] or 0.0), 4),
                "avg_score": float(row["avg_score"] or 0.0),
                "max_score": float(row["max_score"] or 0.0),
            }
            for row in sorted(
                rows,
                key=lambda item: question_numbers.get(
                    int(item["question_id"]),
                    10**9,
                ),
            )
            if int(row["question_id"]) in question_numbers
        ]

    # ======================================================
    # B) 문항 단일 오답 분포 (선택지 기준)
    # ======================================================
    @staticmethod
    def wrong_choice_distribution(
        *,
        exam_id: int,
        question_id: int,
        attempt_ids: Optional[List[int]] = None,
        legacy_enrollment_ids: Optional[List[int]] = None,
    ) -> Dict[str, int]:
        """
        객관식 오답 분포
        - answer 값 기준
        """

        qs = _current_question_facts(
            exam_id=exam_id,
            attempt_ids=attempt_ids,
            legacy_enrollment_ids=legacy_enrollment_ids,
        ).filter(
            question_id=int(question_id),
            is_correct=False,
        )

        rows = qs.values("answer").annotate(cnt=Count("id"))

        dist: Dict[str, int] = {}
        for r in rows:
            key = str(r["answer"] or "")
            dist[key] = int(r["cnt"] or 0)

        return dist

    # ======================================================
    # C) 가장 많이 틀린 문항 TOP N
    # ======================================================
    @staticmethod
    def top_n_wrong_questions(
        *,
        exam_id: int,
        n: int = 5,
        attempt_ids: Optional[List[int]] = None,
        legacy_enrollment_ids: Optional[List[int]] = None,
    ) -> List[Dict]:
        """
        가장 많이 틀린 문항 TOP N
        """

        qs = _current_question_facts(
            exam_id=exam_id,
            attempt_ids=attempt_ids,
            legacy_enrollment_ids=legacy_enrollment_ids,
        ).filter(
            is_correct=False,
        )

        rows = list(
            qs.values("question_id")
            .annotate(wrong_count=Count("id"))
            .order_by("-wrong_count", "question_id")
        )

        question_numbers = question_numbers_by_id_for_exam(
            exam_id=int(exam_id),
            question_ids=[int(row["question_id"]) for row in rows],
        )
        data = [
            {
                "question_id": int(r["question_id"]),
                "question_number": question_numbers[int(r["question_id"])],
                "wrong_count": int(r["wrong_count"]),
            }
            for r in rows
            if int(r["question_id"]) in question_numbers
        ]
        data.sort(
            key=lambda item: (
                -item["wrong_count"],
                item["question_number"],
            )
        )
        return data[:max(int(n), 0)]
