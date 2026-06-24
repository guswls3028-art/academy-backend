# PATH: apps/domains/results/services/exam_grading_service.py
from __future__ import annotations

from typing import Any, Dict, Tuple

from django.core.exceptions import ValidationError
from django.db import transaction
from django.shortcuts import get_object_or_404

from apps.domains.results.models import ExamResult
from apps.domains.results.guards.grading_contract import GradingContractGuard
from apps.domains.results.services.answer_matching import (
    answer_matches,
    format_answer_for_display,
)
from apps.domains.results.services.submission_answer_map import (
    build_submission_answers_map,
    require_complete_omr_answers,
)
from apps.domains.results.services.submission_scope_guard import validate_exam_submission_scope
from apps.support.omr.score_shape import get_exam_score_shape
from apps.support.omr.score_adjustment import get_score_adjustment_from_answers
from apps.support.submissions.dependencies import complete_submission_after_auto_grade


class ExamGradingService:
    """
    Objective exam grading service for the legacy ExamResult compatibility
    snapshot.

    Canonical user-visible results are synchronized by
    sync_result_from_exam_submission(). This service must not invent scoring
    rules; it mirrors the OMR score-shape SSOT while preserving the historical
    ExamResult(submission OneToOne) contract.
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

    def _load_exam(self, submission):
        if str(submission.target_type) != "exam":
            raise ValidationError("submission target_type must be exam")

        Exam = __import__(
            "apps.domains.exams.models",
            fromlist=["Exam"],
        ).Exam

        return get_object_or_404(Exam, id=int(submission.target_id))

    # ------------------------------------------------------------------
    # Scoring
    # ------------------------------------------------------------------
    def _compute_score(
        self,
        *,
        exam,
        sheet,
        answer_key,
        answers_map: dict[int, str],
    ) -> Tuple[float, float, Dict]:
        """
        Returns:
          (total_score, max_score, breakdown)
        """

        key_map: Dict[int, Any] = {
            int(k): v
            for k, v in answer_key.answers.items()
            if str(k).isdigit()
        }

        score_shape = get_exam_score_shape(exam)
        questions = [
            q
            for q in sheet.questions.all().order_by("number")
            if score_shape.question_kind(int(q.id)) != "essay"
        ]

        if not questions:
            return 0, 0.0, {}

        total_score = 0
        max_score = 0.0
        breakdown: Dict[str, dict] = {}

        for q in questions:
            qid = int(q.id)
            q_score = score_shape.question_max_score(
                qid,
                getattr(q, "score", 0),
            )
            max_score += q_score
            correct_answer = key_map.get(qid)
            student_answer = answers_map.get(qid, "")
            is_correct = answer_matches(student_answer, correct_answer)
            earned = q_score if is_correct else 0

            if is_correct:
                total_score += q_score

            breakdown[str(q.number)] = {
                "question_id": qid,
                "correct": is_correct,
                "earned": earned,
                "answer": student_answer,
                "correct_answer": format_answer_for_display(correct_answer),
            }

        objective_adjustment = get_score_adjustment_from_answers(
            answer_key.answers,
        ).objective
        if objective_adjustment > 0:
            total_score += objective_adjustment
            max_score += objective_adjustment

        return round(float(total_score), 2), round(max_score, 2), breakdown

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    @transaction.atomic
    def auto_grade_objective(self, *, submission_id: int) -> ExamResult:
        submission = self._load_submission(submission_id)
        exam = self._load_exam(submission)
        validate_exam_submission_scope(submission=submission, exam=exam)

        sheet, answer_key = GradingContractGuard.validate_exam_for_grading(exam)

        existing = (
            ExamResult.objects
            .select_for_update()
            .filter(submission=submission)
            .first()
        )

        if existing and existing.status == ExamResult.Status.FINAL:
            return existing

        questions = list(sheet.questions.all().only("id", "number"))
        score_shape = get_exam_score_shape(exam)
        auto_score_question_ids = {
            int(q.id)
            for q in questions
            if score_shape.question_kind(int(q.id)) != "essay"
        }
        answers_map = build_submission_answers_map(
            submission=submission,
            question_number_to_id={int(q.number): int(q.id) for q in questions},
        )
        require_complete_omr_answers(
            submission=submission,
            answers_map=answers_map,
            expected_question_ids=auto_score_question_ids,
            context="ExamGradingService.auto_grade_objective",
            protect_existing_score=existing is not None,
        )

        total_score, max_score, breakdown = self._compute_score(
            exam=exam,
            sheet=sheet,
            answer_key=answer_key,
            answers_map=answers_map,
        )

        pass_score = float(getattr(exam, "pass_score", 0) or 0)
        is_passed = total_score >= pass_score if pass_score > 0 else True

        result = existing or ExamResult.objects.create(
            submission=submission,
            exam=exam,
            total_score=0,
            status=ExamResult.Status.DRAFT,
        )

        result.total_score = total_score
        result.max_score = max_score
        result.objective_score = total_score
        result.breakdown = breakdown
        result.is_passed = is_passed
        result.status = ExamResult.Status.DRAFT
        result.save(update_fields=[
            "total_score", "max_score", "objective_score",
            "breakdown", "is_passed", "status", "updated_at",
        ])

        complete_submission_after_auto_grade(
            submission,
            actor="ExamGradingService.auto_grade",
        )

        return result
