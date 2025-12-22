# apps/domains/results/services/grader.py
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from django.db import transaction

from apps.domains.submissions.models import Submission, SubmissionAnswer
from apps.domains.results.services.applier import ResultApplier
from apps.domains.exams.models import ExamQuestion, AnswerKey


# ============================================================
# OMR/채점 정책 v1 (Results 도메인 책임)
# - Worker는 "답안 사실"만 보내고, 점수 계산은 여기서 한다.
# - v1 고정 정책:
#   - multi 마킹 = 0점
#   - confidence < 0.70 = 0점
#   - status != ok = 0점
#   - 부분점수 없음
# ============================================================

OMR_CONF_THRESHOLD_V1 = 0.70


def _norm(s: Optional[str]) -> str:
    return (s or "").strip().upper()


def _get_omr_meta(meta: Any) -> Dict[str, Any]:
    """
    SubmissionAnswer.meta에서 OMR v1 payload 추출.
    기대 위치: meta["omr"]
    """
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
    """
    객관식(선택형) 채점 v1
    return: (is_correct, score)
    """
    if (status or "").lower() != "ok":
        return (False, 0.0)

    m = (marking or "").lower()
    if m in ("blank", "multi"):
        return (False, 0.0)

    conf = float(confidence) if confidence is not None else 0.0
    if conf < OMR_CONF_THRESHOLD_V1:
        return (False, 0.0)

    if not detected or len(detected) != 1:
        return (False, 0.0)

    ans = _norm(detected[0])
    cor = _norm(correct_answer)

    is_correct = (ans != "") and (cor != "") and (ans == cor)
    score = float(max_score) if is_correct else 0.0
    return (is_correct, score)


def _grade_short_v1(
    *,
    answer_text: str,
    correct_answer: str,
    max_score: float,
) -> Tuple[bool, float]:
    """
    주관식(텍스트) 채점 v1 (exact match only)
    """
    ans = _norm(answer_text)
    cor = _norm(correct_answer)

    if ans == "":
        return (False, 0.0)

    is_correct = (cor != "") and (ans == cor)
    score = float(max_score) if is_correct else 0.0
    return (is_correct, score)


def _infer_answer_type(q: ExamQuestion) -> str:
    """
    answer_type가 모델에 없을 수도 있으니 방어적으로 추론.
    """
    v = getattr(q, "answer_type", None)
    if isinstance(v, str) and v.strip():
        return v.strip().lower()
    return "choice"


def _get_correct_answer_map(exam_id: int) -> Dict[str, Any]:
    """
    AnswerKey.answers: { "1": "B", "2": "3", ... } (question number 기반)
    """
    ak = AnswerKey.objects.filter(exam_id=exam_id).first()
    if not ak or not isinstance(ak.answers, dict):
        return {}
    return ak.answers


@transaction.atomic
def grade_submission_to_results(submission: Submission) -> None:
    """
    Submission + SubmissionAnswer(+meta) -> Result/ResultItem/ResultFact 반영
    - 정책/점수 계산은 Results 도메인 책임
    """
    submission.status = Submission.Status.GRADING
    submission.save(update_fields=["status"])

    answers = list(SubmissionAnswer.objects.filter(submission=submission))

    if submission.target_type != Submission.TargetType.EXAM:
        raise ValueError("Only exam grading is supported")

    # ✅ ExamQuestion은 sheet->exam 구조
    questions = (
        ExamQuestion.objects
        .filter(sheet__exam_id=submission.target_id)
        .in_bulk(field_name="id")
    )

    # ✅ 정답은 AnswerKey.answers에서 (question.number 기준)
    correct_map = _get_correct_answer_map(int(submission.target_id))

    items: List[dict] = []

    for sa in answers:
        q = questions.get(sa.question_id)
        if not q:
            continue

        max_score = float(getattr(q, "score", 0) or 0.0)

        # question.number 기반 정답
        correct_answer = str(correct_map.get(str(getattr(q, "number", ""))) or "")

        answer_text = str(sa.answer or "").strip()

        omr = _get_omr_meta(sa.meta)
        omr_version = str(omr.get("version") or "")
        detected = omr.get("detected") or []
        marking = str(omr.get("marking") or "")
        confidence = omr.get("confidence", None)
        status = str(omr.get("status") or "")

        answer_type = _infer_answer_type(q)

        if answer_type in ("choice", "omr", "multiple_choice"):
            if omr_version.lower() == "v1" and isinstance(detected, list):
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

        items.append(
            {
                "question_id": q.id,
                "answer": final_answer,
                "is_correct": bool(is_correct),
                "score": float(score),
                "max_score": float(max_score),
                "source": submission.source,
                "meta": sa.meta,
            }
        )

    ResultApplier.apply(
        target_type=submission.target_type,
        target_id=int(submission.target_id),
        enrollment_id=int(submission.enrollment_id or 0),
        submission_id=int(submission.id),
        items=items,
    )

    submission.status = Submission.Status.DONE
    submission.save(update_fields=["status"])
