# PATH: apps/domains/results/services/exam_grading_service.py
from __future__ import annotations

from typing import Dict, Tuple

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
    - 모델(SSOT)이 가진 필드만 사용
    - 결과 계산은 가능, 모델 계약 위반은 불가
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
            Submission.objects.select_related("user"),
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
    ) -> int:
        """
        Returns:
          total_score (0~100)
        """

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

        if total_q == 0:
            return 0

        correct = 0
        for q in questions:
            qid = int(q.id)
            if (
                qid in key_map
                and qid in answers_map
                and answers_map[qid] == key_map[qid]
            ):
                correct += 1

        return int(round((correct / total_q) * 100))

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

        submission_answers = list(
            SubmissionAnswer.objects.filter(submission=submission)
        )

        total_score = self._compute_score(
            sheet=sheet,
            answer_key=answer_key,
            submission_answers=submission_answers,
        )

        result = existing or ExamResult.objects.create(
            submission=submission,
            exam=exam,
            total_score=0,
            status=ExamResult.Status.DRAFT,
        )

        result.total_score = total_score
        result.status = ExamResult.Status.DRAFT
        result.save(update_fields=["total_score", "status", "updated_at"])

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
