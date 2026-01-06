# apps/domains/results/services/grader.py
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from django.db import transaction

# ======================================================
# 🔽 submissions 도메인 (raw input)
# ======================================================
from apps.domains.submissions.models import Submission, SubmissionAnswer

# ======================================================
# 🔽 results 도메인 (apply / attempt)
# ======================================================
from apps.domains.results.services.applier import ResultApplier
from apps.domains.results.services.attempt_service import ExamAttemptService

# ======================================================
# 🔽 exams 도메인 (정답 / 문제 정의)
# ======================================================
from apps.domains.exams.models import ExamQuestion, AnswerKey

# ======================================================
# 🔽 progress pipeline (side-effect)
# ======================================================
from apps.domains.progress.tasks.progress_pipeline_task import (
    run_progress_pipeline_task,
)

# ======================================================
# Constants
# ======================================================
OMR_CONF_THRESHOLD_V1 = 0.70


# ======================================================
# Utils
# ======================================================
def _norm(s: Optional[str]) -> str:
    """
    문자열 정규화:
    - None 방어
    - 공백 제거
    - 대문자 통일
    """
    return (s or "").strip().upper()


def _get_omr_meta(meta: Any) -> Dict[str, Any]:
    """
    submissions.SubmissionAnswer.meta 에서
    omr dict 만 안전하게 추출
    """
    if not isinstance(meta, dict):
        return {}
    omr = meta.get("omr")
    return omr if isinstance(omr, dict) else {}


# ======================================================
# Grading helpers
# ======================================================
def _grade_choice_v1(
    *,
    detected: List[str],
    marking: str,
    confidence: Optional[float],
    status: str,
    correct_answer: str,
    max_score: float,
) -> Tuple[bool, float]:
    """
    OMR 객관식 채점 v1

    정책:
    - status != ok → 무조건 오답
    - blank / multi → 오답
    - confidence < threshold → 오답
    - detected 1개 아니면 → 오답
    """
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
    """
    주관식 / fallback 채점

    정책:
    - 빈 답안 → 오답
    - 완전 일치만 정답
    """
    ans = _norm(answer_text)
    cor = _norm(correct_answer)

    if ans == "":
        return False, 0.0

    is_correct = cor != "" and ans == cor
    return is_correct, (float(max_score) if is_correct else 0.0)


def _infer_answer_type(q: ExamQuestion) -> str:
    """
    ExamQuestion.answer_type 추론
    """
    v = getattr(q, "answer_type", None)
    if isinstance(v, str) and v.strip():
        return v.strip().lower()
    return "choice"


def _get_correct_answer_map_v2(exam_id: int) -> Dict[str, Any]:
    """
    ✅ AnswerKey v2 고정

    answers = {
        "123": "B",
        "124": "D"
    }

    key == ExamQuestion.id (string)
    """
    ak = AnswerKey.objects.filter(exam_id=int(exam_id)).first()
    if not ak or not isinstance(ak.answers, dict):
        return {}
    return ak.answers


# ======================================================
# Main grading pipeline
# ======================================================
@transaction.atomic
def grade_submission_to_results(submission: Submission) -> None:
    """
    Submission → ExamAttempt → Result / ResultItem / ResultFact

    🔥 v2 핵심 계약:
    - SubmissionAnswer.exam_question_id 만 사용
    - number / fallback 완전 제거
    - AnswerKey v2 고정
    """

    # --------------------------------------------------
    # 0️⃣ Submission 상태 전이
    # --------------------------------------------------
    submission.status = Submission.Status.GRADING
    if hasattr(submission, "error_message"):
        submission.error_message = ""
        submission.save(update_fields=["status", "error_message"])
    else:
        submission.save(update_fields=["status"])

    if submission.target_type != Submission.TargetType.EXAM:
        raise ValueError("Only exam grading is supported")

    attempt = None

    try:
        # --------------------------------------------------
        # 1️⃣ ExamAttempt 생성
        # --------------------------------------------------
        attempt = ExamAttemptService.create_for_submission(
            exam_id=int(submission.target_id),
            enrollment_id=int(submission.enrollment_id),
            submission_id=int(submission.id),
        )
        attempt.status = "grading"
        attempt.save(update_fields=["status"])

        # --------------------------------------------------
        # 2️⃣ Raw answers (submissions 도메인)
        # --------------------------------------------------
        answers = list(
            SubmissionAnswer.objects.filter(submission=submission)
        )

        # --------------------------------------------------
        # 3️⃣ ExamQuestion 로딩 (id 기준)
        # --------------------------------------------------
        questions_by_id = (
            ExamQuestion.objects
            .filter(sheet__exam_id=submission.target_id)
            .in_bulk(field_name="id")
        )

        correct_map = _get_correct_answer_map_v2(int(submission.target_id))

        items: List[dict] = []

        # --------------------------------------------------
        # 4️⃣ 문항별 채점
        # --------------------------------------------------
        for sa in answers:
            eqid = getattr(sa, "exam_question_id", None)
            if not eqid:
                # 전환기 legacy 데이터는 skip
                continue

            # 🔧 운영 안정성 최소 방어
            try:
                q = questions_by_id.get(int(eqid))
            except (TypeError, ValueError):
                continue

            if not q:
                continue

            max_score = float(getattr(q, "score", 0) or 0.0)
            correct_answer = str(correct_map.get(str(int(q.id))) or "")

            answer_text = str(getattr(sa, "answer", "") or "").strip()

            omr = _get_omr_meta(getattr(sa, "meta", None))
            detected = omr.get("detected") or []
            marking = str(omr.get("marking") or "")
            confidence = omr.get("confidence", None)
            status = str(omr.get("status") or "")
            omr_version = str(omr.get("version") or "")

            answer_type = _infer_answer_type(q)

            if answer_type in ("choice", "omr", "multiple_choice"):
                if omr_version.lower() in ("v1", "v2"):
                    is_correct, score = _grade_choice_v1(
                        detected=[str(x) for x in detected],
                        marking=marking,
                        confidence=confidence,
                        status=status,
                        correct_answer=correct_answer,
                        max_score=max_score,
                    )
                    final_answer = (
                        "".join([_norm(x) for x in detected])
                        if detected else ""
                    )
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

            # --------------------------------------------------
            # 5️⃣ ResultApplier 입력용 item 구성
            # --------------------------------------------------
            items.append({
                "question_id": int(q.id),
                "answer": final_answer,
                "is_correct": bool(is_correct),
                "score": float(score),
                "max_score": float(max_score),
                "source": submission.source,
                "meta": getattr(sa, "meta", None),
            })

        # --------------------------------------------------
        # 6️⃣ Result / ResultItem / ResultFact 반영
        # --------------------------------------------------
        ResultApplier.apply(
            target_type=submission.target_type,
            target_id=int(submission.target_id),
            enrollment_id=int(submission.enrollment_id),
            submission_id=int(submission.id),
            attempt_id=int(attempt.id),
            items=items,
        )

        # --------------------------------------------------
        # 7️⃣ 상태 마무리
        # --------------------------------------------------
        attempt.status = "done"
        attempt.save(update_fields=["status"])

        submission.status = Submission.Status.DONE
        submission.save(update_fields=["status"])

        transaction.on_commit(
            lambda: run_progress_pipeline_task.delay(submission.id)
        )

    except Exception as e:
        # --------------------------------------------------
        # 실패 처리 (멱등 / 재시도 안전)
        # --------------------------------------------------
        if attempt:
            attempt.status = "failed"
            attempt.save(update_fields=["status"])

        submission.status = Submission.Status.FAILED
        if hasattr(submission, "error_message"):
            submission.error_message = str(e)[:2000]
            submission.save(update_fields=["status", "error_message"])
        else:
            submission.save(update_fields=["status"])
        raise
