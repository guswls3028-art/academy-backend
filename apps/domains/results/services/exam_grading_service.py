# PATH: apps/domains/results/services/exam_grading_service.py
from __future__ import annotations

from typing import Any, Dict, Tuple

from django.core.exceptions import ValidationError
from django.db import transaction
from django.shortcuts import get_object_or_404

from apps.domains.exams.models import Exam
from apps.domains.results.models import ExamResult
from apps.domains.results.guards.grading_contract import GradingContractGuard


class ExamGradingService:
    """
    Objective exam grading service (queue-less, sync).

    운영 원칙:
    - 채점 전 계약(SSOT) 검증 필수
    - 계산 실패는 허용, 데이터 오염은 불허
    """

    # ------------------------------------------------------------------
    # Loaders
    # ------------------------------------------------------------------
    def _load_submission(self, submission_id: int):
        Submission = __import__(
            "apps.domains.submissions.models",
            fromlist=["Submission"],
        ).Submission

        return get_object_or_404(
            Submission.objects.select_related("user", "exam_result"),
            id=int(submission_id),
        )

    def _load_exam(self, submission) -> Exam:
        if str(submission.target_type) != "exam":
            raise ValidationError("submission target_type must be exam")

        return get_object_or_404(Exam, id=int(submission.target_id))

    # ------------------------------------------------------------------
    # Scoring
    # ------------------------------------------------------------------
    def _compute_score(
        self,
        *,
        sheet,
        answer_key,
        submission_answers,
    ) -> Tuple[int, Dict[str, Any]]:
        """
        Returns:
          (total_score, detail)
        """

        # AnswerKey.answers = { "question_id": "correct_answer" }
        key_map: Dict[int, str] = {
            int(k): str(v).strip()
            for k, v in answer_key.answers.items()
            if str(k).isdigit()
        }

        answers_map: Dict[int, str] = {}
        for a in submission_answers:
            qid = int(getattr(a, "exam_question_id", 0) or 0)
            ans = str(getattr(a, "answer", "") or "").strip()
            if qid > 0:
                answers_map[qid] = ans

        questions = list(sheet.questions.all())
        total_q = len(questions)

        correct = 0
        breakdown = []

        for q in questions:
            qid = int(q.id)
            correct_ans = key_map.get(qid)
            submitted_ans = answers_map.get(qid)

            is_correct = (
                correct_ans is not None
                and submitted_ans is not None
                and submitted_ans == correct_ans
            )

            if is_correct:
                correct += 1

            breakdown.append(
                {
                    "exam_question_id": qid,
                    "correct_answer": correct_ans,
                    "submitted_answer": submitted_ans,
                    "is_correct": is_correct,
                }
            )

        score = int(round((correct / total_q) * 100)) if total_q > 0 else 0

        return score, {
            "total_questions": total_q,
            "correct_count": correct,
            "breakdown": breakdown,
        }

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    @transaction.atomic
    def auto_grade_objective(self, *, submission_id: int) -> ExamResult:
        SubmissionAnswer = __import__(
            "apps.domains.submissions.models",
            fromlist=["SubmissionAnswer"],
        ).SubmissionAnswer

        submission = self._load_submission(submission_id)
        exam = self._load_exam(submission)

        # ✅ 계약 검증 (대기업 운영 핵심)
        sheet, answer_key = GradingContractGuard.validate_exam_for_grading(exam)

        existing = (
            ExamResult.objects
            .select_for_update()
            .filter(submission=submission)
            .first()
        )

        if existing and existing.status == ExamResult.Status.FINAL:
            return existing

        submission_answers = list(
            SubmissionAnswer.objects.filter(submission=submission)
        )

        total_score, detail = self._compute_score(
            sheet=sheet,
            answer_key=answer_key,
            submission_answers=submission_answers,
        )

        result = existing or ExamResult.objects.create(
            submission=submission,
            exam=exam,
            status=ExamResult.Status.DRAFT,
            total_score=0,
            pass_score=int(getattr(exam, "pass_score", 0) or 0),
            is_passed=False,
            detail={},
        )

        result.total_score = total_score
        result.is_passed = total_score >= result.pass_score
        result.detail = detail
        result.status = ExamResult.Status.DRAFT

        result.save(
            update_fields=[
                "total_score",
                "is_passed",
                "detail",
                "status",
                "updated_at",
            ]
        )

        try:
            submission.status = "graded"
            submission.save(update_fields=["status", "updated_at"])
        except Exception:
            pass

        return result

    @transaction.atomic
    def finalize(self, *, submission_id: int) -> ExamResult:
        submission = self._load_submission(submission_id)

        result = (
            ExamResult.objects
            .select_for_update()
            .filter(submission=submission)
            .first()
        )

        if not result:
            raise ValidationError("auto-grade first; result not found")

        if result.status == ExamResult.Status.FINAL:
            return result

        result.finalize()
        return result
