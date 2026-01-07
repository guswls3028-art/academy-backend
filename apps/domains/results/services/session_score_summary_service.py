# PATH: apps/domains/results/services/session_score_summary_service.py

from __future__ import annotations

from django.db.models import Avg, Min, Max, Count

from apps.domains.results.models import Result, ExamAttempt
from apps.domains.progress.models import SessionProgress
from apps.domains.lectures.models import Session


class SessionScoreSummaryService:
    """
    ✅ Session 단위 성적 통계 (results 기준 단일 진실)

    사용 근거:
    - 점수: Result (대표 attempt 스냅샷)
    - 통과/클리닉: SessionProgress
    - 재시험: ExamAttempt
    """

    @staticmethod
    def build(*, session_id: int) -> dict:
        session = Session.objects.filter(id=int(session_id)).first()
        if not session:
            return {
                "participant_count": 0,
                "avg_score": 0.0,
                "min_score": 0.0,
                "max_score": 0.0,
                "pass_rate": 0.0,
                "clinic_rate": 0.0,
                "attempt_stats": {
                    "avg_attempts": 0.0,
                    "retake_ratio": 0.0,
                },
            }

        # ---------------------------------------------
        # 1️⃣ Session ↔ Exam (다대일)
        # ---------------------------------------------
        exam_ids = list(
            session.exam_set.values_list("id", flat=True)
            if hasattr(session, "exam_set")
            else Session.objects.filter(id=session.id).values_list("exam__id", flat=True)
        )

        if not exam_ids:
            return {
                "participant_count": 0,
                "avg_score": 0.0,
                "min_score": 0.0,
                "max_score": 0.0,
                "pass_rate": 0.0,
                "clinic_rate": 0.0,
                "attempt_stats": {
                    "avg_attempts": 0.0,
                    "retake_ratio": 0.0,
                },
            }

        # ---------------------------------------------
        # 2️⃣ Result 기반 점수 통계 (대표 attempt)
        # ---------------------------------------------
        results = Result.objects.filter(
            target_type="exam",
            target_id__in=exam_ids,
        )

        agg = results.aggregate(
            participant_count=Count("id"),
            avg_score=Avg("total_score"),
            min_score=Min("total_score"),
            max_score=Max("total_score"),
        )

        participant_count = agg["participant_count"] or 0

        # ---------------------------------------------
        # 3️⃣ Progress 기반 pass / clinic
        # ---------------------------------------------
        progresses = SessionProgress.objects.filter(session=session)

        pass_count = progresses.filter(failed=False).count()
        clinic_count = progresses.filter(clinic_required=True).count()

        pass_rate = (
            pass_count / participant_count
            if participant_count else 0.0
        )
        clinic_rate = (
            clinic_count / participant_count
            if participant_count else 0.0
        )

        # ---------------------------------------------
        # 4️⃣ Attempt 통계 (재시험 비율)
        # ---------------------------------------------
        attempts = ExamAttempt.objects.filter(
            exam_id__in=exam_ids
        )

        per_enrollment = (
            attempts
            .values("enrollment_id")
            .annotate(cnt=Count("id"))
        )

        total_attempts = sum(r["cnt"] for r in per_enrollment)
        retake_users = sum(1 for r in per_enrollment if r["cnt"] > 1)

        avg_attempts = (
            total_attempts / participant_count
            if participant_count else 0.0
        )
        retake_ratio = (
            retake_users / participant_count
            if participant_count else 0.0
        )

        return {
            "participant_count": participant_count,
            "avg_score": float(agg["avg_score"] or 0.0),
            "min_score": float(agg["min_score"] or 0.0),
            "max_score": float(agg["max_score"] or 0.0),
            "pass_rate": round(float(pass_rate), 4),
            "clinic_rate": round(float(clinic_rate), 4),
            "attempt_stats": {
                "avg_attempts": round(float(avg_attempts), 2),
                "retake_ratio": round(float(retake_ratio), 4),
            },
        }
