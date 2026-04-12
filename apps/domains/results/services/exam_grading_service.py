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
    # Helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _get_question_max_score(exam: Exam, question_id: int) -> float:
        """문항 DB에서 원본 만점 조회. 찾지 못하면 0 반환."""
        try:
            from apps.domains.exams.models import ExamQuestion
            tid = exam.effective_template_exam_id
            q = ExamQuestion.objects.filter(
                sheet__exam_id=tid, id=int(question_id),
            ).only("score").first()
            return float(q.score) if q else 0.0
        except Exception:
            return 0.0

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
    ) -> Tuple[float, float, Dict]:
        """
        Returns:
          (total_score, max_score, breakdown)
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

        if not questions:
            return 0, 0.0, {}

        total_score = 0
        max_score = 0.0
        breakdown: Dict[str, dict] = {}

        for q in questions:
            qid = int(q.id)
            q_score = float(getattr(q, "score", 0) or 0)
            max_score += q_score
            correct_answer = key_map.get(qid)
            student_answer = answers_map.get(qid, "")
            # 정규화: 대소문자·공백 무시 (grader.py _norm()과 일관성 유지)
            _norm = lambda s: str(s).strip().upper() if s else ""
            is_correct = (
                correct_answer is not None
                and _norm(student_answer) == _norm(correct_answer)
            )
            earned = q_score if is_correct else 0

            if is_correct:
                total_score += q_score

            breakdown[str(q.number)] = {
                "question_id": qid,
                "correct": is_correct,
                "earned": earned,
                "answer": student_answer,
                "correct_answer": correct_answer or "",
            }

        return round(float(total_score), 2), max_score, breakdown

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

        # FINAL 결과는 불변 — 재채점으로 덮어쓰기 금지
        if existing and existing.status == ExamResult.Status.FINAL:
            return existing

        submission_answers = list(
            SubmissionAnswer.objects.filter(submission=submission)
        )

        total_score, max_score, breakdown = self._compute_score(
            sheet=sheet,
            answer_key=answer_key,
            submission_answers=submission_answers,
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

        from apps.domains.submissions.services.transition import (
            can_transit, transit_save,
        )

        # answers_ready → grading → done (STATUS_FLOW SSOT 준수)
        # 다른 상태에서는 grading/done에 도달할 수 없으므로 채점 자체를 중단한다.
        # @transaction.atomic가 롤백하여 ExamResult 저장도 취소됨.
        if submission.status == "answers_ready":
            transit_save(submission, "grading", actor="ExamGradingService.auto_grade")
            transit_save(submission, "done", actor="ExamGradingService.auto_grade")
        elif can_transit(submission.status, "done"):
            transit_save(submission, "done", actor="ExamGradingService.auto_grade")
        else:
            import logging
            logging.getLogger(__name__).error(
                "Submission %s in status '%s' cannot transition to 'done'; "
                "aborting grading to preserve data consistency.",
                submission.id, submission.status,
            )
            raise ValidationError(
                f"Submission {submission.id} in status '{submission.status}' "
                f"cannot be graded — invalid state for transition to 'done'."
            )

        return result

    @transaction.atomic
    def apply_manual_overrides(
        self, *, submission_id: int, overrides: dict
    ) -> ExamResult:
        """
        수동 채점 오버라이드: 교사가 개별 문항 점수를 수동 조정.
        overrides 형태: {"grades": [...], "answers": [...], "note": "..."} 등
        """
        submission = self._load_submission(submission_id)
        exam = self._load_exam(submission)

        existing = (
            ExamResult.objects
            .select_for_update()
            .filter(submission=submission)
            .first()
        )

        if existing and existing.status == ExamResult.Status.FINAL:
            return existing

        result = existing or ExamResult.objects.create(
            submission=submission,
            exam=exam,
            total_score=0,
            status=ExamResult.Status.DRAFT,
        )

        # overrides에서 점수 정보 추출
        grades = overrides.get("grades") or overrides.get("overrides") or []

        manual_total = 0.0
        manual_max = 0.0
        manual_breakdown = {}

        # P0-4: max_score 왜곡 방지
        # max_score는 반드시 원본 문항 만점을 사용.
        # item에 max_score가 없으면 기존 breakdown에서 원본 만점을 복원.
        existing_breakdown = result.breakdown or {}

        # breakdown 키는 question number이고 값에 question_id 포함.
        # question_id → breakdown entry 매핑 생성
        qid_to_breakdown = {}
        for _num, entry in existing_breakdown.items():
            if isinstance(entry, dict):
                entry_qid = entry.get("question_id")
                if entry_qid is not None:
                    qid_to_breakdown[int(entry_qid)] = entry

        for item in grades:
            if isinstance(item, dict):
                qid = item.get("exam_question_id", 0)
                score = float(item.get("score", 0) or 0)
                # 음수 방어: 음수면 0으로 클램핑
                if score < 0:
                    score = 0.0

                # max_score 결정: item 명시 > 기존 breakdown의 earned > exam 문항 DB
                raw_max = item.get("max_score")
                if raw_max is not None and float(raw_max) > 0:
                    max_score_item = float(raw_max)
                else:
                    # 기존 자동채점 breakdown에서 원본 만점 복원 (question_id 기준)
                    orig = qid_to_breakdown.get(int(qid), {})
                    orig_earned = orig.get("earned")
                    # breakdown의 earned는 정답 시 문항 만점이므로 참조
                    if orig_earned is not None and float(orig_earned) > 0:
                        max_score_item = float(orig_earned)
                    else:
                        # 마지막 수단: 문항 DB에서 조회
                        max_score_item = self._get_question_max_score(exam, qid)

                # 만점 초과 방어: max_score 초과면 max_score로 클램핑
                if score > max_score_item:
                    score = max_score_item

                manual_total += score
                manual_max += max_score_item
                manual_breakdown[str(qid)] = {
                    "question_id": qid,
                    "score": score,
                    "max_score": max_score_item,  # 원본 만점 보존
                    "is_correct": item.get("is_correct", score >= max_score_item),
                    "source": "manual",
                    "note": item.get("note", ""),
                }

        if grades:
            result.total_score = manual_total
            result.max_score = manual_max
            result.subjective_score = manual_total
            result.manual_overrides = manual_breakdown

        # is_passed 재계산 (수동 점수 반영)
        pass_score = float(getattr(exam, "pass_score", 0) or 0)
        result.is_passed = result.total_score >= pass_score if pass_score > 0 else True

        result.status = ExamResult.Status.DRAFT

        update_fields = [
            "total_score", "max_score", "subjective_score",
            "manual_overrides", "is_passed", "status", "updated_at",
        ]
        result.save(update_fields=update_fields)

        # 수동 오버라이드 후 Result(학생 화면)에 수동 점수를 직접 반영
        # sync_result_from_exam_submission은 자동채점을 다시 수행하므로 사용하면 안 됨
        if grades:
            try:
                from apps.domains.results.models import Result
                enrollment_id = getattr(submission, "enrollment_id", None)
                if enrollment_id:
                    r, _ = Result.objects.get_or_create(
                        target_type="exam",
                        target_id=int(exam.id),
                        enrollment_id=int(enrollment_id),
                        defaults={"total_score": 0, "max_score": 0},
                    )
                    r.total_score = manual_total
                    r.max_score = manual_max
                    r.objective_score = result.objective_score
                    r.save(update_fields=["total_score", "max_score", "objective_score", "updated_at"])
            except Exception:
                import logging
                logging.getLogger(__name__).exception(
                    "Manual override: failed to sync Result for submission %s",
                    submission.id,
                )

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
