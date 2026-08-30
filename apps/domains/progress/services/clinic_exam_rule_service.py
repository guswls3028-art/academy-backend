from __future__ import annotations

from apps.support.progress.clinic_exam_rule_dependencies import (
    exam_pass_score,
    exam_result_for_rule,
    low_confidence_fact_count,
    repeated_wrong_question_ids,
)
from apps.support.progress.exam_policy_dependencies import (
    effective_exam_pass_score,
    exam_by_id,
)


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
        lecture_id: int | None = None,
    ) -> dict:
        reasons: dict = {}

        # ----------------------
        # 1️⃣ 점수 미달
        # ----------------------
        result = exam_result_for_rule(
            enrollment_id=enrollment_id,
            exam_id=exam_id,
        )

        exam = exam_by_id(exam_id=int(exam_id))
        pass_score = (
            effective_exam_pass_score(
                exam=exam,
                lecture_id=lecture_id,
            )
            if exam is not None
            else exam_pass_score(exam_id=exam_id)
        )

        if result and result.total_score < pass_score:
            reasons["LOW_SCORE"] = {
                "score": result.total_score,
                "pass_score": pass_score,
            }

        # ----------------------
        # 2️⃣ OMR 신뢰도 낮음 (LOW_CONFIDENCE = conf<threshold,
        #     AMBIGUOUS_SINGLE = top-2 gap 작음 — 둘 다 AI 불확실 신호)
        # ----------------------
        low_conf = low_confidence_fact_count(
            enrollment_id=enrollment_id,
            exam_id=exam_id,
        )

        if low_conf >= cls.LOW_CONF_THRESHOLD:
            reasons["LOW_CONFIDENCE_OMR"] = {
                "count": low_conf,
                "threshold": cls.LOW_CONF_THRESHOLD,
            }

        # ----------------------
        # 3️⃣ 반복 오답
        # ----------------------
        repeated_question_ids = repeated_wrong_question_ids(
            enrollment_id=enrollment_id,
            exam_id=exam_id,
        )

        if repeated_question_ids:
            reasons["REPEATED_WRONG"] = {
                "question_ids": repeated_question_ids
            }

        return reasons
