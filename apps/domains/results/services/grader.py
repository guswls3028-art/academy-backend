# apps/domains/results/services/grader.py
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from django.db import transaction

# 🔽 Submission은 submissions 도메인의 단일 진실
from apps.domains.submissions.models import Submission, SubmissionAnswer

# 🔽 Results 도메인 실 저장 Answer (채점 결과)
from apps.domains.results.models import SubmissionAnswer as ResultSubmissionAnswer

from apps.domains.results.services.applier import ResultApplier
from apps.domains.results.services.attempt_service import ExamAttemptService

from apps.domains.exams.models import ExamQuestion, AnswerKey

# ✅ Progress 파이프라인 Celery Task
from apps.domains.progress.tasks.progress_pipeline_task import (
    run_progress_pipeline_task,
)

# ============================================================
# OMR / 채점 정책 v1 (Results 도메인 책임)
# ============================================================

OMR_CONF_THRESHOLD_V1 = 0.70


def _norm(s: Optional[str]) -> str:
    return (s or "").strip().upper()


def _get_omr_meta(meta: Any) -> Dict[str, Any]:
    if not isinstance(meta, dict):
        return {}
    omr = meta.get("omr")
    return omr if isinstance(omr, dict) else {}


def _grade_choice_v1(
    *,
    detected: List[str],
    marking: str,
    confidence: Optional[float],
    status: str,
    correct_answer: str,
    max_score: float,
) -> Tuple[bool, float]:
    if (status or "").lower() != "ok":
        return False, 0.0

    if (marking or "").lower() in ("blank", "multi"):
        return False, 0.0

    conf = float(confidence) if confidence is not None else 0.0
    if conf < OMR_CONF_THRESHOLD_V1:
        return False, 0.0

    if not detected or len(detected) != 1:
        return False, 0.0

    ans = _norm(detected[0])
    cor = _norm(correct_answer)

    is_correct = ans != "" and cor != "" and ans == cor
    return is_correct, (float(max_score) if is_correct else 0.0)


def _grade_short_v1(
    *,
    answer_text: str,
    correct_answer: str,
    max_score: float,
) -> Tuple[bool, float]:
    ans = _norm(answer_text)
    cor = _norm(correct_answer)

    if ans == "":
        return False, 0.0

    is_correct = cor != "" and ans == cor
    return is_correct, (float(max_score) if is_correct else 0.0)


def _infer_answer_type(q: ExamQuestion) -> str:
    v = getattr(q, "answer_type", None)
    if isinstance(v, str) and v.strip():
        return v.strip().lower()
    return "choice"


def _get_correct_answer_map(exam_id: int) -> Dict[str, Any]:
    ak = AnswerKey.objects.filter(exam_id=exam_id).first()
    if not ak or not isinstance(ak.answers, dict):
        return {}
    return ak.answers


@transaction.atomic
def grade_submission_to_results(submission: Submission) -> None:
    """
    Submission → ExamAttempt → Result / ResultItem / ResultFact

    ✅ attempt 중심 설계:
    - ExamAttempt 생성
    - Result / ResultFact에 attempt_id 저장
    """

    # ---------------------------
    # 1️⃣ Submission 상태 변경
    # ---------------------------
    submission.status = Submission.Status.GRADING
    submission.save(update_fields=["status"])

    if submission.target_type != Submission.TargetType.EXAM:
        raise ValueError("Only exam grading is supported")

    # ---------------------------
    # 2️⃣ ExamAttempt 생성 + 상태 전이
    # ---------------------------
    attempt = ExamAttemptService.create_for_submission(
        exam_id=int(submission.target_id),
        enrollment_id=int(submission.enrollment_id),
        submission_id=int(submission.id),
    )

    attempt.status = "grading"
    attempt.save(update_fields=["status"])

    # ---------------------------
    # 3️⃣ 채점 대상 로딩
    # ---------------------------
    answers = list(
        SubmissionAnswer.objects.filter(submission=submission)
    )

    questions = (
        ExamQuestion.objects
        .filter(sheet__exam_id=submission.target_id)
        .in_bulk(field_name="id")
    )

    correct_map = _get_correct_answer_map(int(submission.target_id))

    items: List[dict] = []

    # ---------------------------
    # 4️⃣ 문항별 채점 + ResultSubmissionAnswer 저장
    # ---------------------------
    for sa in answers:
        q = questions.get(sa.question_id)
        if not q:
            continue

        max_score = float(getattr(q, "score", 0) or 0.0)
        correct_answer = str(
            correct_map.get(str(getattr(q, "number", ""))) or ""
        )

        answer_text = str(sa.answer or "").strip()

        omr = _get_omr_meta(sa.meta)
        detected = omr.get("detected") or []
        marking = str(omr.get("marking") or "")
        confidence = omr.get("confidence", None)
        status = str(omr.get("status") or "")
        omr_version = str(omr.get("version") or "")

        answer_type = _infer_answer_type(q)

        if answer_type in ("choice", "omr", "multiple_choice"):
            if omr_version.lower() == "v1":
                is_correct, score = _grade_choice_v1(
                    detected=[str(x) for x in detected],
                    marking=marking,
                    confidence=(float(confidence) if confidence is not None else None),
                    status=status,
                    correct_answer=correct_answer,
                    max_score=max_score,
                )
                final_answer = "".join([_norm(x) for x in detected]) if detected else ""
            else:
                is_correct, score = _grade_short_v1(
                    answer_text=answer_text,
                    correct_answer=correct_answer,
                    max_score=max_score,
                )
                final_answer = answer_text
        else:
            is_correct, score = _grade_short_v1(
                answer_text=answer_text,
                correct_answer=correct_answer,
                max_score=max_score,
            )
            final_answer = answer_text

        # 🔥 Results 도메인 Answer 실 저장 (불변)
        ResultSubmissionAnswer.objects.create(
            attempt=attempt,
            question_id=q.id,
            detected=detected,
            marking=marking,
            confidence=float(confidence or 0),
            status=status,
            is_correct=bool(is_correct),
            score_awarded=float(score),
            meta=sa.meta,
        )

        items.append({
            "question_id": q.id,
            "answer": final_answer,
            "is_correct": bool(is_correct),
            "score": float(score),
            "max_score": float(max_score),
            "source": submission.source,
            "meta": sa.meta,
        })

    # ---------------------------
    # 5️⃣ Result 반영 (attempt 기준)
    # ---------------------------
    ResultApplier.apply(
        target_type=submission.target_type,
        target_id=int(submission.target_id),
        enrollment_id=int(submission.enrollment_id),
        submission_id=int(submission.id),
        attempt_id=int(attempt.id),     # ✅ 핵심: attempt_id 저장
        items=items,
    )

    # ---------------------------
    # 6️⃣ 상태 마무리
    # ---------------------------
    attempt.status = "done"
    attempt.save(update_fields=["status"])

    submission.status = Submission.Status.DONE
    submission.save(update_fields=["status"])

    # ---------------------------
    # 7️⃣ Progress 파이프라인 (commit 후)
    # ---------------------------
    transaction.on_commit(
        lambda: run_progress_pipeline_task.delay(submission.id)
    )
