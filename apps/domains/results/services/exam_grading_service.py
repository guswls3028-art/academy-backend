# PATH: apps/domains/results/services/exam_grading_service.py
from __future__ import annotations

from typing import Any, Dict, Tuple

from django.core.exceptions import ValidationError
from django.db import transaction
from django.shortcuts import get_object_or_404

from apps.domains.exams.models import Exam
from apps.domains.exams.models.sheet import Sheet
from apps.domains.exams.services.answer_key_service import AnswerKeyService
from apps.domains.results.models import ExamResult


class ExamGradingService:
    """
    Objective exam grading service (queue-less, sync).

    SSOT rules:
    - Submission은 exam FK를 직접 가지지 않고 target_type / target_id 를 사용
    - ExamResult는 (submission, exam) 기준 단 1개 (idempotent)
    - Sheet / AnswerKey의 단일 진실은 template_exam 이 소유
    """

    # ------------------------------------------------------------------
    # Internal loaders
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
            raise ValidationError(
                {"detail": f"submission target_type must be exam (got={submission.target_type})"}
            )

        return get_object_or_404(Exam, id=int(submission.target_id))

    def _get_template_sheet_and_key(self, exam: Exam) -> Tuple[Sheet, Any]:
        """
        REGULAR exam
          → template_exam
          → template_exam.sheet (SSOT)
          → AnswerKey
        """
        if exam.exam_type != Exam.ExamType.REGULAR:
            raise ValidationError({"detail": "only REGULAR exams are gradable"})

        if not exam.template_exam_id:
            raise ValidationError({"detail": "regular exam must have template_exam"})

        template_exam = exam.template_exam

        if not getattr(template_exam, "sheet", None):
            raise ValidationError({"detail": "template exam has no sheet"})

        sheet: Sheet = template_exam.sheet

        answer_key = AnswerKeyService.get_answer_key_for_sheet(sheet_id=int(sheet.id))
        if not answer_key:
            raise ValidationError({"detail": "answer key not found for template sheet"})

        return sheet, answer_key

    # ------------------------------------------------------------------
    # Scoring
    # ------------------------------------------------------------------
    def _compute_score(
        self,
        *,
        sheet: Sheet,
        answer_key,
        submission_answers,
    ) -> Tuple[int, int, Dict[str, Any]]:
        """
        Returns:
          (total_score, correct_count, detail)
        """

        key_map: Dict[int, str] = {}
        for item in getattr(answer_key, "items", []) or []:
            qid = int(getattr(item, "question_id", 0) or 0)
            ans = str(getattr(item, "answer", "") or "").strip()
            if qid > 0 and ans:
                key_map[qid] = ans

        answers_map: Dict[int, str] = {}
        for a in submission_answers:
            qid = int(getattr(a, "exam_question_id", 0) or 0)
            ans = str(getattr(a, "answer", "") or "").strip()
            if qid > 0:
                answers_map[qid] = ans

        questions = list(sheet.questions.all())
        total_q = len(questions)

        correct = 0
        breakdown: list[dict] = []

        for q in questions:
            qid = int(q.id)
            correct_ans = key_map.get(qid)
            submitted_ans = answers_map.get(qid)

            is_correct = bool(correct_ans and submitted_ans and submitted_ans == correct_ans)
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

        total_score = int(round((correct / total_q) * 100)) if total_q > 0 else 0

        detail = {
            "total_questions": total_q,
            "correct_count": correct,
            "breakdown": breakdown,
        }

        return total_score, correct, detail

    # ------------------------------------------------------------------
    # Public APIs
    # ------------------------------------------------------------------
    @transaction.atomic
    def auto_grade_objective(self, *, submission_id: int) -> ExamResult:
        SubmissionAnswer = __import__(
            "apps.domains.submissions.models",
            fromlist=["SubmissionAnswer"],
        ).SubmissionAnswer

        submission = self._load_submission(int(submission_id))
        exam = self._load_exam(submission)

        existing = (
            ExamResult.objects
            .select_for_update()
            .filter(submission=submission)
            .first()
        )

        if existing and existing.status == ExamResult.Status.FINAL:
            return existing

        sheet, answer_key = self._get_template_sheet_and_key(exam)

        submission_answers = list(
            SubmissionAnswer.objects.filter(submission=submission)
        )

        total_score, _, detail = self._compute_score(
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

        result.total_score = int(total_score)
        result.pass_score = int(getattr(exam, "pass_score", 0) or 0)
        result.is_passed = bool(result.total_score >= result.pass_score)
        result.detail = detail
        result.status = ExamResult.Status.DRAFT

        result.save(
            update_fields=[
                "total_score",
                "pass_score",
                "is_passed",
                "detail",
                "status",
                "updated_at",
            ]
        )

        # submission 상태는 부수효과 — 실패해도 무시
        try:
            submission.status = "graded"
            submission.save(update_fields=["status", "updated_at"])
        except Exception:
            pass

        return result

    @transaction.atomic
    def finalize(self, *, submission_id: int) -> ExamResult:
        submission = self._load_submission(int(submission_id))

        result = (
            ExamResult.objects
            .select_for_update()
            .filter(submission=submission)
            .first()
        )

        if not result:
            raise ValidationError({"detail": "auto-grade first; result not found"})

        if result.status == ExamResult.Status.FINAL:
            return result

        result.finalize()
        return result
