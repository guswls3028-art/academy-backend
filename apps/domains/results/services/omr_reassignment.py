"""Audited correction of the student attribution of one already graded paper.

This is not a retake, a scan replacement, or an edit of historical grading facts.
The attempt remains the same physical sitting; its current student attribution
changes only after the teacher confirms the match. Every prior fact is retained.
"""
from __future__ import annotations

from copy import deepcopy

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from apps.domains.results.models import ExamAttempt, Result, ResultFact, ResultItem
from apps.domains.results.services.attempt_service import ExamAttemptService
from apps.domains.results.services.attempt_snapshot import (
    apply_result_snapshot,
    resolve_result_snapshot_from_attempt,
)
from apps.support.results.grading_dependencies import (
    dispatch_progress_pipeline,
    has_confirmed_current_omr_identity,
    lock_omr_reassignment_enrollments,
)

_ITEM_FIELDS = (
    "question_id", "answer", "is_correct", "include_in_wrong_note",
    "score", "max_score", "source",
)


def _is_clinic(attempt):
    return bool(attempt.clinic_link_id or (attempt.meta or {}).get("source") == "clinic_remediation")


def _snapshot(result):
    return {
        "total_score": result.total_score,
        "objective_score": result.objective_score,
        "max_score": result.max_score,
        "submitted_at": result.submitted_at.isoformat() if result.submitted_at else None,
    }


def _archive_result(result, *, at):
    if result is None or not result.attempt_id:
        return
    previous = ExamAttempt.objects.select_for_update().filter(
        id=result.attempt_id, exam_id=result.target_id, enrollment_id=result.enrollment_id,
    ).first()
    if previous is None:
        raise ValidationError("Existing result attribution must be resolved first.", code="retake_not_allowed")
    meta = deepcopy(previous.meta or {})
    meta["final_result_snapshot"] = {**_snapshot(result), "archived_at": at}
    initial = meta.get("initial_snapshot")
    if previous.is_initial_grade and (
        not isinstance(initial, dict) or initial.get("total_score") is None
    ):
        meta["initial_snapshot"] = {
            **_snapshot(result), "source": "omr_attribution_legacy_first_snapshot",
        }
    previous.meta = meta
    previous.save(update_fields=["meta", "updated_at"])


@transaction.atomic
def correct_scored_omr_attribution(*, submission, exam):
    """Return the former enrollment after a correction, otherwise None.

    The caller owns the Exam -> Session -> Submission grading locks. Both
    enrollment/result scopes are locked before mutation; any later grading or
    projection failure rolls the entire manual-edit transaction back.
    """
    attempt = ExamAttempt.objects.select_for_update().filter(
        submission_id=submission.id,
    ).first()
    if attempt is None:
        return None
    if int(attempt.exam_id) != int(exam.id):
        raise ValidationError("Submission attempt belongs to a different exam.")
    target_id = int(submission.enrollment_id)
    if attempt.enrollment_id == target_id:
        return None
    if not settings.OMR_SCORED_REASSIGNMENT_ENABLED:
        raise ValidationError("Graded OMR correction is awaiting compatible reader rollout.", code="retake_not_allowed")

    meta = submission.meta if isinstance(submission.meta, dict) else {}
    edits = meta.get("manual_edits") or []
    edit = edits[-1] if edits and isinstance(edits[-1], dict) else {}
    if (
        submission.source != "omr_scan"
        or meta.get("identifier_match_kind") != "manual"
        or edit.get("resolved_enrollment_id") != target_id
        or not edit.get("by_user_id")
        or not has_confirmed_current_omr_identity(
            submission_id=int(submission.id), exam_id=int(exam.id),
            enrollment_id=target_id, tenant_id=int(exam.tenant_id),
        )
    ):
        raise ValidationError("A teacher-confirmed OMR match is required to correct a graded paper.")

    former_id = int(attempt.enrollment_id)
    enrollments = lock_omr_reassignment_enrollments(
        enrollment_ids=[former_id, target_id], tenant_id=int(exam.tenant_id),
    )
    if set(enrollments) != {former_id, target_id}:
        raise ValidationError("OMR reassignment enrollment tenant mismatch.")
    results = {
        int(row.enrollment_id): row
        for row in Result.objects.select_for_update().filter(
            target_type="exam", target_id=exam.id,
            enrollment_id__in=sorted(enrollments),
        ).order_by("enrollment_id")
    }
    attempts = list(ExamAttempt.objects.select_for_update().filter(
        exam=exam, enrollment_id__in=sorted(enrollments),
    ).order_by("enrollment_id", "attempt_index"))
    target_attempts = [row for row in attempts if row.enrollment_id == target_id]
    target_grade_attempts = [row for row in target_attempts if not _is_clinic(row)]
    next_index = ExamAttemptService.next_regular_attempt_index(
        ExamAttempt.objects.filter(exam=exam, enrollment_id=target_id),
    )
    if target_grade_attempts and not exam.allow_retake:
        raise ValidationError("The selected student already has a graded attempt.", code="retake_not_allowed")
    if len(target_grade_attempts) + 1 > int(exam.max_attempts or 1):
        raise ValidationError("The selected student's attempt limit is reached.", code="max_attempts_exceeded")
    target_result = results.get(target_id)
    if target_result and target_result.attempt_id not in {row.id for row in target_attempts}:
        raise ValidationError("Resolve the selected student's existing result attribution first.", code="retake_not_allowed")

    former_result = results.get(former_id)
    owns_former_result = bool(former_result and former_result.attempt_id == attempt.id)
    if owns_former_result:
        paper_result = former_result
        paper_items = list(ResultItem.objects.filter(result=paper_result).values(*_ITEM_FIELDS))
    else:
        paper_result = Result(target_type="exam", target_id=exam.id, enrollment_id=former_id)
        facts, total, objective, submitted = resolve_result_snapshot_from_attempt(
            result=paper_result, attempt=attempt, exam=exam,
        )
        paper_result.total_score = total
        paper_result.objective_score = objective
        paper_result.max_score = float(exam.max_score or 100)
        paper_result.submitted_at = submitted
        paper_items = [{
            "question_id": fact.question_id, "answer": fact.answer,
            "is_correct": fact.is_correct, "score": fact.score,
            "max_score": fact.max_score, "source": fact.source,
            "include_in_wrong_note": bool((fact.meta or {}).get("include_in_wrong_note")),
        } for fact in facts]

    old_facts = list(ResultFact.objects.current().filter(
        target_type="exam", target_id=exam.id, attempt=attempt,
        enrollment_id=former_id,
    ).order_by("id"))
    paper_values = {
        key: getattr(paper_result, key)
        for key in ("total_score", "objective_score", "max_score", "submitted_at")
    }
    at = timezone.now().isoformat()
    before_meta = deepcopy(attempt.meta or {})
    old_index = attempt.attempt_index
    remaining = [row for row in attempts if row.enrollment_id == former_id and row.id != attempt.id]
    first_remaining = next((row for row in remaining if not _is_clinic(row)), None)
    was_initial = ExamAttempt.objects.initial().filter(id=attempt.id).exists()
    first_score_restoration = None
    if was_initial and first_remaining:
        first_score_restoration = {
            "attempt_id": first_remaining.id, "attempt_index": first_remaining.attempt_index,
            "previous_meta": deepcopy(first_remaining.meta or {}),
        }
    audit = ResultFact.objects.create(
        target_type="exam", target_id=exam.id, enrollment_id=former_id,
        submission_id=submission.id, attempt=attempt, question_id=0,
        source="omr_reassignment", score=paper_result.total_score,
        max_score=paper_result.max_score,
        meta={
            "from_enrollment_id": former_id, "to_enrollment_id": target_id,
            "from_attempt_index": old_index, "to_attempt_index": next_index,
            "was_representative": attempt.is_representative,
            "actor_user_id": edit["by_user_id"], "at": at,
            "previous_fact_after_id": attempt.current_fact_after_id,
            "attempt_meta": before_meta, "result_snapshot": _snapshot(paper_result),
            "result_items": paper_items, "first_score_restoration": first_score_restoration,
            "target_previous_attempt_id": target_result.attempt_id if target_result else None,
        },
    )
    _archive_result(target_result, at=at)
    ExamAttempt.objects.filter(exam=exam, enrollment_id=target_id).update(is_representative=False)
    attempt.enrollment_id = target_id
    attempt.attempt_index = next_index
    attempt.is_retake = bool(target_grade_attempts)
    attempt.is_representative = True
    attempt.current_fact_after_id = audit.id
    attempt_meta = deepcopy(before_meta)
    attempt_meta["last_attribution_correction_fact_id"] = audit.id
    attempt_meta["final_result_snapshot"] = {**_snapshot(paper_result), "archived_at": at}
    attempt.meta = attempt_meta
    attempt.save(update_fields=[
        "enrollment_id", "attempt_index", "is_retake", "is_representative",
        "current_fact_after_id", "meta", "updated_at",
    ])
    # Restore a first ordinary score from its preserved evidence. Ordinals and
    # clinic history stay unchanged; initial() selects the first ordinary sitting.
    if first_score_restoration:
        row = first_remaining
        first_meta = deepcopy(row.meta or {})
        initial = first_meta.get("initial_snapshot")
        if not isinstance(initial, dict) or initial.get("total_score") is None:
            if former_result and former_result.attempt_id == row.id:
                first_meta["initial_snapshot"] = _snapshot(former_result)
            else:
                probe = Result(target_type="exam", target_id=exam.id, enrollment_id=former_id)
                _, total, objective, submitted = resolve_result_snapshot_from_attempt(
                    result=probe, attempt=row, exam=exam,
                )
                first_meta["initial_snapshot"] = {
                    "total_score": total, "objective_score": objective,
                    "max_score": float(exam.max_score or 100),
                    "submitted_at": submitted.isoformat() if submitted else None,
                }
            first_meta["initial_snapshot"].update({
                "source": "omr_attribution_first_attempt_restored",
                "correction_fact_id": audit.id,
            })
        row.meta = first_meta
        row.save(update_fields=["meta", "updated_at"])

    # Copy the effective paper facts in event order. Original actor/time/source
    # remain inspectable both in the unchanged original and this provenance.
    for fact in old_facts:
        fact_meta = deepcopy(fact.meta or {})
        fact_meta["attribution_origin"] = {
            "fact_id": fact.id, "created_at": fact.created_at.isoformat(),
            "original_created_at": (fact_meta.get("attribution_origin") or {}).get(
                "original_created_at", fact.created_at.isoformat(),
            ),
            "enrollment_id": former_id, "correction_fact_id": audit.id,
        }
        ResultFact.objects.create(
            target_type=fact.target_type, target_id=fact.target_id,
            enrollment_id=target_id, submission_id=fact.submission_id,
            attempt=attempt, question_id=fact.question_id, answer=fact.answer,
            is_correct=fact.is_correct, score=fact.score, max_score=fact.max_score,
            source=fact.source, meta=fact_meta,
        )

    if owns_former_result:
        previous = next((row for row in reversed(remaining) if row.status == "done" and not _is_clinic(row)), None)
        if previous is None:
            former_result.delete()  # Mutable projection only; all facts survive.
        else:
            ExamAttempt.objects.filter(exam=exam, enrollment_id=former_id).update(is_representative=False)
            previous.is_representative = True
            previous.save(update_fields=["is_representative", "updated_at"])
            facts, total, objective, submitted = resolve_result_snapshot_from_attempt(
                result=former_result, attempt=previous, exam=exam,
            )
            apply_result_snapshot(
                result=former_result, attempt_id=previous.id, question_facts=facts,
                total_score=total, objective_score=objective,
                current_max_score=float(exam.max_score or 100), submitted_at=submitted,
            )

    target_result, _ = Result.objects.get_or_create(
        target_type="exam", target_id=exam.id, enrollment_id=target_id,
    )
    target_result.attempt = attempt
    for key, value in paper_values.items():
        setattr(target_result, key, value)
    target_result.save()
    target_result.items.all().delete()
    ResultItem.objects.bulk_create([ResultItem(result=target_result, **item) for item in paper_items])
    return enrollments[former_id]


def refresh_former_omr_student(*, exam, enrollment):
    """Refresh even an enrollment that no longer has a Result for this exam."""
    for session_id in exam.sessions.filter(lecture_id=enrollment.lecture_id).values_list("id", flat=True):
        dispatch_progress_pipeline(enrollment_id=enrollment.id, session_id=session_id)
