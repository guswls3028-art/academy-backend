from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Tuple

from django.db import transaction
from django.shortcuts import get_object_or_404
from rest_framework.exceptions import ValidationError

from apps.domains.exams.models import Exam, AnswerKey, ExamQuestion
from apps.domains.exams.services.template_resolver import resolve_template_exam
from apps.domains.results.models import ExamResult


@dataclass(frozen=True)
class AutoGradeOutput:
    result: ExamResult
    updated: bool


class ExamGradingService:
    """
    results SSOT grading

    규칙:
    - exam 구조는 exams(template)에서 resolve
    - 제출 원본은 submissions에서 가져온다
    - 결과/채점은 results에만 기록한다
    """

    def _get_submission_answers_number_based(self, submission) -> Dict[int, str]:
        """
        ✅ 전제: submissions가 객관식 답안을 "문항번호 기반"으로 보관
        - 예: {"1": "A", "2": "C"}
        - 어디에 저장되었는지는 프로젝트마다 달라서
          여기선 안전하게 attribute 후보를 몇 개 확인.
        """
        candidates = [
            getattr(submission, "objective_answers", None),
            getattr(submission, "answers", None),
            getattr(submission, "payload", None),
            getattr(submission, "data", None),
        ]
        raw = None
        for c in candidates:
            if isinstance(c, dict) and c:
                raw = c
                break
        if raw is None:
            return {}

        # payload 구조가 {"objective": {...}} 형태일 수 있어서 보정
        if "objective" in raw and isinstance(raw.get("objective"), dict):
            raw = raw["objective"]

        out: Dict[int, str] = {}
        for k, v in (raw or {}).items():
            try:
                n = int(k)
            except Exception:
                continue
            out[n] = str(v or "").upper().strip()
        return out

    def _build_number_to_question(self, template_exam: Exam) -> Dict[int, ExamQuestion]:
        sheet = getattr(template_exam, "sheet", None)
        if not sheet:
            return {}
        qs = list(
            ExamQuestion.objects.filter(sheet=sheet).order_by("number")
        )
        return {int(q.number): q for q in qs}

    def _load_answer_key_map(self, template_exam: Exam) -> Dict[int, str]:
        ak = AnswerKey.objects.filter(exam=template_exam).first()
        if not ak:
            return {}
        # answers: {"<ExamQuestion.id>": "A"} 라는 계약
        out: Dict[int, str] = {}
        for k, v in (ak.answers or {}).items():
            try:
                qid = int(k)
            except Exception:
                continue
            out[qid] = str(v or "").upper().strip()
        return out

    def _calc_max_score(self, number_to_q: Dict[int, ExamQuestion]) -> float:
        # exams_question.score (float) 합산
        return float(sum(float(q.score or 0.0) for q in number_to_q.values()))

    @transaction.atomic
    def auto_grade_objective(self, *, submission_id: int) -> AutoGradeOutput:
        Submission = __import__("apps.domains.submissions.models", fromlist=["Submission"]).Submission  # lazy import
        submission = get_object_or_404(Submission.objects.select_related("exam"), id=int(submission_id))

        regular_exam: Exam = submission.exam
        if regular_exam.exam_type != Exam.ExamType.REGULAR:
            raise ValidationError({"detail": "auto grading requires regular exam submission"})

        template_exam = resolve_template_exam(regular_exam)

        number_to_q = self._build_number_to_question(template_exam)
        answer_key_by_qid = self._load_answer_key_map(template_exam)
        submitted_by_num = self._get_submission_answers_number_based(submission)

        max_score = self._calc_max_score(number_to_q)

        breakdown: Dict[str, Any] = {}
        objective_score = 0.0

        for num, q in number_to_q.items():
            qid = int(q.id)
            correct = answer_key_by_qid.get(qid)  # "A".."E" or None
            submitted = submitted_by_num.get(int(num))

            earned = 0.0
            is_correct = False
            if correct and submitted:
                is_correct = (submitted == correct)
                if is_correct:
                    earned = float(q.score or 0.0)

            breakdown[str(num)] = {
                "question_id": qid,
                "submitted": submitted,
                "correct_answer": correct,
                "correct": bool(is_correct),
                "earned": float(earned),
                "max": float(q.score or 0.0),
            }
            objective_score += float(earned)

        # manual overrides는 기존 유지
        obj, created = ExamResult.objects.select_for_update().get_or_create(
            submission=submission,
            defaults={
                "exam": regular_exam,
                "max_score": max_score,
                "objective_score": objective_score,
                "subjective_score": 0.0,
                "total_score": objective_score,
                "breakdown": breakdown,
                "manual_overrides": {},
            },
        )
        updated = not created

        if updated:
            if obj.status == ExamResult.Status.FINAL:
                raise ValidationError({"detail": "result is finalized; cannot auto-grade"})

            obj.exam = regular_exam
            obj.max_score = max_score
            obj.objective_score = objective_score
            obj.breakdown = breakdown

            # subjective_score는 overrides 기반 재계산
            subj = 0.0
            for _, ov in (obj.manual_overrides or {}).items():
                try:
                    subj += float(ov.get("earned") or 0.0)
                except Exception:
                    continue
            obj.subjective_score = subj
            obj.total_score = float(obj.objective_score) + float(obj.subjective_score)

            # pass/fail
            pass_score = float(regular_exam.pass_score or 0.0)
            obj.is_passed = bool(obj.total_score >= pass_score)

            obj.save(update_fields=[
                "exam", "max_score", "objective_score", "subjective_score", "total_score",
                "breakdown", "is_passed", "updated_at"
            ])

        else:
            # created case에도 pass/fail 계산
            pass_score = float(regular_exam.pass_score or 0.0)
            obj.is_passed = bool(obj.total_score >= pass_score)
            obj.save(update_fields=["is_passed", "updated_at"])

        return AutoGradeOutput(result=obj, updated=updated)

    @transaction.atomic
    def apply_manual_overrides(self, *, submission_id: int, overrides: Dict[str, Any]) -> ExamResult:
        Submission = __import__("apps.domains.submissions.models", fromlist=["Submission"]).Submission  # lazy import
        submission = get_object_or_404(Submission.objects.select_related("exam"), id=int(submission_id))

        obj = ExamResult.objects.select_for_update().filter(submission=submission).first()
        if not obj:
            raise ValidationError({"detail": "auto-grade first; result not found"})

        if obj.status == ExamResult.Status.FINAL:
            raise ValidationError({"detail": "result is finalized; cannot edit"})

        # merge
        merged = dict(obj.manual_overrides or {})
        for k, v in (overrides or {}).items():
            merged[str(k)] = {
                "earned": float((v or {}).get("earned") or 0.0),
                "comment": str((v or {}).get("comment") or ""),
            }

        obj.manual_overrides = merged

        # recompute subjective_score
        subj = 0.0
        for _, ov in merged.items():
            subj += float(ov.get("earned") or 0.0)

        obj.subjective_score = subj
        obj.total_score = float(obj.objective_score) + float(obj.subjective_score)

        pass_score = float(submission.exam.pass_score or 0.0)
        obj.is_passed = bool(obj.total_score >= pass_score)

        obj.save(update_fields=[
            "manual_overrides", "subjective_score", "total_score", "is_passed", "updated_at"
        ])
        return obj

    @transaction.atomic
    def finalize(self, *, submission_id: int) -> ExamResult:
        Submission = __import__("apps.domains.submissions.models", fromlist=["Submission"]).Submission  # lazy import
        submission = get_object_or_404(Submission.objects.select_related("exam"), id=int(submission_id))

        obj = ExamResult.objects.select_for_update().filter(submission=submission).first()
        if not obj:
            raise ValidationError({"detail": "auto-grade first; result not found"})

        if obj.status == ExamResult.Status.FINAL:
            return obj

        obj.finalize()
        return obj
