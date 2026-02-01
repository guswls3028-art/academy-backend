# apps/domains/results/services/exam_grading_service.py
from __future__ import annotations

from typing import Any, Dict, Optional, Tuple

from django.core.exceptions import ValidationError
from django.db import transaction
from django.shortcuts import get_object_or_404

from apps.domains.exams.models import Exam, ExamSheet
from apps.domains.exams.services.answer_key_service import AnswerKeyService
from apps.domains.results.models import ExamResult


class ExamGradingService:
    """
    Objective exam grading service (queue-less).

    Contract:
    - Submission은 exam FK가 아니라 target_type/target_id로 시험을 가리킨다.
    - 따라서 Exam 조회는 submission.target_id 기반으로 수행한다.
    - ExamResult는 (submission, exam) 기준으로 1개가 SSOT이며, idempotent하게 upsert한다.
    """

    # -----------------------------
    # Internal helpers
    # -----------------------------
    def _load_submission(self, submission_id: int):
        Submission = __import__("apps.domains.submissions.models", fromlist=["Submission"]).Submission  # lazy import
        # Submission에는 exam FK가 없으므로 select_related("exam") 금지
        return get_object_or_404(
            Submission.objects.select_related("user", "exam_result"),
            id=int(submission_id),
        )

    def _load_exam(self, submission) -> Exam:
        # Submission.TargetType.EXAM 인 경우만 채점
        if str(submission.target_type) != "exam":
            raise ValidationError({"detail": f"submission target_type must be exam (got={submission.target_type})"})

        return get_object_or_404(Exam, id=int(submission.target_id))

    def _get_template_sheet_and_key(self, exam: Exam) -> Tuple[Any, Any]:
        """
        REGULAR exam -> template_exam -> template.sheet + answer_key
        """
        if exam.exam_type != Exam.ExamType.REGULAR:
            raise ValidationError({"detail": "only REGULAR exams are gradable via this service"})

        if not getattr(exam, "template_exam_id", None):
            raise ValidationError({"detail": "regular exam must have template_exam"})

        template_exam = exam.template_exam
        if not getattr(template_exam, "sheet", None):
            raise ValidationError({"detail": "template exam has no sheet initialized"})

        sheet = template_exam.sheet
        answer_key = AnswerKeyService.get_answer_key_for_sheet(sheet_id=int(sheet.id))
        if not answer_key:
            raise ValidationError({"detail": "answer key not found for template sheet"})

        return sheet, answer_key

    def _compute_score(
        self,
        *,
        sheet,
        answer_key,
        submission_answers,
    ) -> Tuple[int, int, Dict[str, Any]]:
        """
        returns:
          (total_score, correct_count, detail)
        """

        # answer_key: question_id -> correct_answer(str)
        key_map: Dict[int, str] = {}
        # AnswerKeyService 반환 구조를 신뢰하되, 방어적으로 처리
        for item in getattr(answer_key, "items", []) or []:
            qid = int(getattr(item, "question_id", 0) or 0)
            ans = str(getattr(item, "answer", "") or "").strip()
            if qid > 0 and ans:
                key_map[qid] = ans

        # submission_answers: list[SubmissionAnswer]
        answers_map: Dict[int, str] = {}
        for a in submission_answers:
            qid = int(getattr(a, "exam_question_id", 0) or 0)
            ans = str(getattr(a, "answer", "") or "").strip()
            if qid > 0:
                answers_map[qid] = ans

        # template sheet questions 기준
        questions = list(sheet.questions.all())
        total_q = len(questions)
        correct = 0
        per_question: list[dict] = []

        for q in questions:
            qid = int(q.id)
            correct_ans = key_map.get(qid)
            submitted_ans = answers_map.get(qid)

            is_correct = bool(correct_ans and submitted_ans and submitted_ans == correct_ans)
            if is_correct:
                correct += 1

            per_question.append(
                {
                    "exam_question_id": qid,
                    "correct_answer": correct_ans,
                    "submitted_answer": submitted_ans,
                    "is_correct": is_correct,
                }
            )

        # 점수 정책(운영 기본): 100점 만점 비율
        total_score = 0
        if total_q > 0:
            total_score = int(round((correct / total_q) * 100))

        detail = {
            "total_questions": total_q,
            "correct_count": correct,
            "breakdown": per_question,
        }
        return total_score, correct, detail

    # -----------------------------
    # Public API
    # -----------------------------
    @transaction.atomic
    def auto_grade_objective(self, *, submission_id: int) -> ExamResult:
        SubmissionAnswer = __import__("apps.domains.submissions.models", fromlist=["SubmissionAnswer"]).SubmissionAnswer  # lazy import

        submission = self._load_submission(int(submission_id))
        exam = self._load_exam(submission)

        # 이미 FINAL이면 그대로 반환 (idempotent)
        existing = ExamResult.objects.select_for_update().filter(submission=submission).first()
        if existing and existing.status == ExamResult.Status.FINAL:
            return existing

        # template sheet + answer key
        sheet, answer_key = self._get_template_sheet_and_key(exam)

        # 제출 답안
        submission_answers = list(SubmissionAnswer.objects.filter(submission=submission))

        # 점수 계산
        total_score, correct_count, detail = self._compute_score(
            sheet=sheet,
            answer_key=answer_key,
            submission_answers=submission_answers,
        )

        # 결과 upsert (SSOT)
        obj = existing or ExamResult.objects.create(
            submission=submission,
            exam=exam,
            status=ExamResult.Status.DRAFT,
            total_score=0,
            pass_score=int(getattr(exam, "pass_score", 0) or 0),
            is_passed=False,
            detail={},
        )

        obj.total_score = int(total_score)
        obj.pass_score = int(getattr(exam, "pass_score", 0) or 0)
        obj.is_passed = bool(obj.total_score >= obj.pass_score)
        obj.detail = detail
        obj.status = ExamResult.Status.DRAFT
        obj.save(update_fields=["total_score", "pass_score", "is_passed", "detail", "status", "updated_at"])

        # submission 상태도 동기화(운영 기본)
        # (Submission.Status 값은 너 프로젝트에 맞춰야 함. 여기서는 존재하는 값만 사용)
        try:
            submission.status = "graded"
            submission.save(update_fields=["status", "updated_at"])
        except Exception:
            # status 정책이 다르면 submission 상태 업데이트는 무시(채점 결과 SSOT는 ExamResult)
            pass

        return obj

    @transaction.atomic
    def apply_manual_overrides(self, *, submission_id: int, overrides: Dict[str, Any]) -> ExamResult:
        """
        사람 채점/수정 점수 반영.
        overrides 예:
          {
            "total_score": 80,
            "detail": {...}
          }
        """
        submission = self._load_submission(int(submission_id))
        exam = self._load_exam(submission)

        obj = ExamResult.objects.select_for_update().filter(submission=submission).first()
        if not obj:
            obj = ExamResult.objects.create(
                submission=submission,
                exam=exam,
                status=ExamResult.Status.DRAFT,
                total_score=0,
                pass_score=int(getattr(exam, "pass_score", 0) or 0),
                is_passed=False,
                detail={},
            )

        if "total_score" in overrides:
            obj.total_score = int(overrides["total_score"] or 0)
        if "detail" in overrides:
            obj.detail = overrides["detail"]

        obj.pass_score = int(getattr(exam, "pass_score", 0) or 0)
        obj.is_passed = bool(obj.total_score >= obj.pass_score)
        obj.status = ExamResult.Status.DRAFT
        obj.save(update_fields=["total_score", "pass_score", "is_passed", "detail", "status", "updated_at"])

        return obj

    @transaction.atomic
    def finalize(self, *, submission_id: int) -> ExamResult:
        submission = self._load_submission(int(submission_id))

        obj = ExamResult.objects.select_for_update().filter(submission=submission).first()
        if not obj:
            raise ValidationError({"detail": "auto-grade first; result not found"})

        if obj.status == ExamResult.Status.FINAL:
            return obj

        obj.finalize()
        return obj
