from __future__ import annotations
from django.db.models import Count

from apps.domains.results.models import Result, ResultFact
from apps.domains.exams.models import Exam


class ClinicExamRuleService:
    """
    시험/OMR/오답 기반 클리닉 판단 (판단만 함, 저장 ❌)
    """

    LOW_CONF_THRESHOLD = 2

    @classmethod
    def evaluate(
        cls,
        *,
        enrollment_id: int,
        exam_id: int,
    ) -> dict:
        reasons: dict = {}

        # ----------------------
        # 1️⃣ 점수 미달
        # ----------------------
        result = Result.objects.filter(
            enrollment_id=enrollment_id,
            target_type="exam",
            target_id=exam_id,
        ).first()

        exam = Exam.objects.filter(id=exam_id).first()
        pass_score = float(getattr(exam, "pass_score", 0) or 0)

        if result and result.total_score < pass_score:
            reasons["LOW_SCORE"] = {
                "score": result.total_score,
                "pass_score": pass_score,
            }

        # ----------------------
        # 2️⃣ OMR 신뢰도 낮음
        # ----------------------
        low_conf = ResultFact.objects.filter(
            enrollment_id=enrollment_id,
            target_type="exam",
            target_id=exam_id,
            meta__grading__invalid_reason="LOW_CONFIDENCE",
        ).count()

        if low_conf >= cls.LOW_CONF_THRESHOLD:
            reasons["LOW_CONFIDENCE_OMR"] = {
                "count": low_conf,
                "threshold": cls.LOW_CONF_THRESHOLD,
            }

        # ----------------------
        # 3️⃣ 반복 오답
        # ----------------------
        repeated = (
            ResultFact.objects
            .filter(
                enrollment_id=enrollment_id,
                target_type="exam",
                target_id=exam_id,
                is_correct=False,
            )
            .values("question_id")
            .annotate(cnt=Count("attempt_id", distinct=True))
            .filter(cnt__gte=2)
        )

        if repeated.exists():
            reasons["REPEATED_WRONG"] = {
                "question_ids": [r["question_id"] for r in repeated]
            }

        return reasons
