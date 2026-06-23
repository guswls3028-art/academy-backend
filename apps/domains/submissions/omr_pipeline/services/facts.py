"""
OMR fact persistence and readiness evaluation.

`Submission.status` remains the compatibility projection for existing screens.
The durable OMR state is recorded here as independent facts: recognition runs,
detected answers, and student matches. Grading code should ask readiness from
these facts instead of inferring completion from one status string.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from django.utils import timezone

from apps.domains.submissions.models import (
    OMRDetectedAnswer,
    OMRRecognitionRun,
    OMRStudentMatch,
    Submission,
    SubmissionAnswer,
)
from apps.support.omr.exam_structure import OmrExamStructure


@dataclass(frozen=True)
class OMRGradeReadiness:
    submission_id: int
    can_grade: bool
    missing: list[str] = field(default_factory=list)
    answer_count: int = 0
    enrollment_id: int | None = None
    manual_review_required: bool = False
    status: str = ""


def _worker_version(worker_result: dict[str, Any]) -> str:
    version = worker_result.get("version")
    if isinstance(version, str) and version:
        return version
    answers = worker_result.get("answers")
    if isinstance(answers, list) and answers:
        first = answers[0]
        if isinstance(first, dict) and first.get("version"):
            return str(first["version"])
    return ""


def _detected_values(answer_payload: dict[str, Any]) -> list[str]:
    raw = answer_payload.get("detected") or []
    if not isinstance(raw, list):
        return []
    return [str(v).strip() for v in raw if str(v).strip()]


def record_recognition_fact(
    *,
    submission: Submission,
    job_id: str | None,
    status: str,
    error: str | None,
    worker_result: dict[str, Any],
    exam_structure: OmrExamStructure,
) -> OMRRecognitionRun:
    """
    Persist one AI recognition fact and its question-level detected answers.

    The compatibility `SubmissionAnswer` rows are still written by
    answer_persister. This fact table keeps the raw recognition set stable even
    when manual edits later change the compatibility answer projection.
    """
    answers = worker_result.get("answers")
    if not isinstance(answers, list):
        answers = []

    status_counts = Counter(
        str(item.get("status") or "")
        for item in answers
        if isinstance(item, dict)
    )
    defaults = {
        "tenant": submission.tenant,
        "status": str(status or ""),
        "worker_version": _worker_version(worker_result),
        "answer_count": len([a for a in answers if isinstance(a, dict)]),
        "answer_status_counts": dict(status_counts),
        "aligned": worker_result.get("aligned")
        if isinstance(worker_result.get("aligned"), bool)
        else None,
        "alignment_method": str(worker_result.get("alignment_method") or ""),
        "contract_snapshot": exam_structure.contract_snapshot,
        "raw_result": worker_result if isinstance(worker_result, dict) else {},
        "error_message": str(error or ""),
        "received_at": timezone.now(),
    }

    clean_job_id = str(job_id or "")
    if clean_job_id:
        run, _created = OMRRecognitionRun.objects.update_or_create(
            submission=submission,
            job_id=clean_job_id,
            defaults=defaults,
        )
        OMRDetectedAnswer.objects.filter(recognition_run=run).delete()
    else:
        run = OMRRecognitionRun.objects.create(
            submission=submission,
            job_id="",
            **defaults,
        )

    qnum_to_pk = exam_structure.qnum_to_pk
    qnum_map_built = exam_structure.qnum_map_built
    for item in answers:
        if not isinstance(item, dict):
            continue
        raw_qnum = item.get("question_id") or item.get("exam_question_id")
        if not raw_qnum:
            continue
        try:
            question_number = int(raw_qnum)
        except (TypeError, ValueError):
            continue
        exam_question_id = None
        if qnum_map_built:
            exam_question_id = qnum_to_pk.get(question_number)

        detected = _detected_values(item)
        OMRDetectedAnswer.objects.create(
            tenant=submission.tenant,
            submission=submission,
            recognition_run=run,
            question_number=question_number,
            exam_question_id=exam_question_id,
            answer=",".join(detected),
            detected=detected,
            status=str(item.get("status") or ""),
            marking=str(item.get("marking") or ""),
            confidence=_float_or_none(item.get("confidence")),
            raw=item if isinstance(item, dict) else {},
        )

    return run


def record_student_match_fact(
    *,
    submission: Submission,
    enrollment_id: int | None,
    status: str,
    method: str,
    actor: str,
    identifier_status: str = "",
    identifier_payload: Any = None,
    confidence: float | None = None,
) -> OMRStudentMatch:
    """Record a current student-match fact while preserving match history."""
    OMRStudentMatch.objects.filter(
        submission=submission,
        is_current=True,
    ).update(is_current=False)
    return OMRStudentMatch.objects.create(
        tenant=submission.tenant,
        submission=submission,
        enrollment_id=int(enrollment_id) if enrollment_id else None,
        status=status,
        method=method,
        identifier_status=str(identifier_status or ""),
        identifier_payload=identifier_payload if isinstance(identifier_payload, dict) else {},
        confidence=confidence,
        actor=str(actor or ""),
        is_current=True,
        matched_at=timezone.now(),
    )


def get_current_student_match(submission: Submission) -> OMRStudentMatch | None:
    return (
        OMRStudentMatch.objects.filter(submission=submission, is_current=True)
        .order_by("-matched_at", "-id")
        .first()
    )


def evaluate_omr_grade_readiness(submission: Submission) -> OMRGradeReadiness:
    """Return whether an OMR submission has enough facts to run grading."""
    missing: list[str] = []
    if submission.source != Submission.Source.OMR_SCAN:
        missing.append("not_omr_scan")
    if submission.target_type != Submission.TargetType.EXAM:
        missing.append("not_exam")
    if submission.status in (
        Submission.Status.FAILED,
        Submission.Status.GRADING,
        Submission.Status.SUPERSEDED,
    ):
        missing.append(f"status:{submission.status}")

    current_match = get_current_student_match(submission)
    enrollment_id = int(submission.enrollment_id or 0) or None
    if current_match:
        if current_match.status == OMRStudentMatch.Status.CONFIRMED:
            enrollment_id = int(current_match.enrollment_id or 0) or enrollment_id
        else:
            enrollment_id = None
    if not enrollment_id:
        missing.append("student_match")

    answer_count = SubmissionAnswer.objects.filter(submission=submission).count()
    expected_answer_count = _latest_expected_answer_count(submission)
    if expected_answer_count is not None and expected_answer_count > 0:
        if answer_count != expected_answer_count:
            missing.append(f"answers:{answer_count}/{expected_answer_count}")
    elif answer_count <= 0:
        missing.append("answers")

    meta = submission.meta if isinstance(submission.meta, dict) else {}
    manual_review = meta.get("manual_review") if isinstance(meta, dict) else {}
    manual_review_required = (
        isinstance(manual_review, dict)
        and manual_review.get("required") is True
    )

    return OMRGradeReadiness(
        submission_id=int(submission.id),
        can_grade=not missing,
        missing=missing,
        answer_count=answer_count,
        enrollment_id=enrollment_id,
        manual_review_required=manual_review_required,
        status=str(submission.status),
    )


def write_readiness_meta(
    *,
    submission: Submission,
    readiness: OMRGradeReadiness,
    actor: str,
) -> None:
    meta = dict(submission.meta or {})
    meta["omr_readiness"] = {
        "can_grade": readiness.can_grade,
        "missing": list(readiness.missing),
        "answer_count": readiness.answer_count,
        "enrollment_id": readiness.enrollment_id,
        "manual_review_required": readiness.manual_review_required,
        "status": readiness.status,
        "updated_at": timezone.now().isoformat(),
        "actor": actor,
    }
    submission.meta = meta
    submission.save(update_fields=["meta", "updated_at"])


def _float_or_none(value: Any) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _latest_expected_answer_count(submission: Submission) -> int | None:
    run = (
        OMRRecognitionRun.objects.filter(submission=submission)
        .order_by("-received_at", "-id")
        .first()
    )
    if not run or not isinstance(run.contract_snapshot, dict):
        return None
    value = run.contract_snapshot.get("choice_count")
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
