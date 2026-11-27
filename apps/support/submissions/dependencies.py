"""Cross-domain helpers for submission-facing orchestration.

Submission views/services should keep status transitions in the submissions
lifecycle while cross-domain lookups stay behind this support boundary.
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from django.db.models import Prefetch, Q

logger = logging.getLogger(__name__)


def homework_submission_revisions(
    *,
    tenant: Any,
    enrollment_ids: list[int] | set[int],
    homework_ids: list[int] | set[int],
) -> dict[tuple[int, int], str]:
    """Hash only accepted, still-active homework evidence per authorized target.

    File identity (rather than an update timestamp) survives duplicate retries
    and avoids treating failed/in-progress uploads as a completed submission.
    Missing keys have no submitted evidence.
    """
    if not enrollment_ids or not homework_ids:
        return {}

    from apps.domains.homework_results.models import Homework
    from apps.domains.submissions.models import Submission, SubmissionMedia

    tenant_homework_ids = Homework.objects.filter(
        tenant=tenant,
        id__in=homework_ids,
    ).values("id")

    evidence = {}
    parents = (
        Submission.objects.filter(
            tenant=tenant,
            enrollment_id__in=enrollment_ids,
            enrollment__tenant=tenant,
            target_type=Submission.TargetType.HOMEWORK,
            target_id__in=tenant_homework_ids,
        )
        .exclude(status__in=[Submission.Status.FAILED, Submission.Status.SUPERSEDED])
        .only("id", "enrollment_id", "target_id", "source", "meta", "file_key")
        .prefetch_related(Prefetch(
            "media_files",
            queryset=SubmissionMedia.objects.filter(
                tenant=tenant,
                status=SubmissionMedia.Status.UPLOADED,
                removed_at__isnull=True,
            ).exclude(object_key__in=["", "pending"]).only("id", "submission_id"),
            to_attr="uploaded_media_files",
        ))
    )
    for parent in parents:
        key = (int(parent.enrollment_id), int(parent.target_id))
        parts = evidence.setdefault(key, [])
        if parent.source not in {
            Submission.Source.HOMEWORK_IMAGE,
            Submission.Source.HOMEWORK_VIDEO,
        }:
            parts.append(("submission", int(parent.id)))
        meta = parent.meta if isinstance(parent.meta, dict) else {}
        if parent.file_key not in (None, "", "pending") and not meta.get("homework_media_legacy_removed_at"):
            parts.append(("legacy", int(parent.id)))
        parts.extend(
            ("media", int(media.id))
            for media in parent.uploaded_media_files
        )

    return {
        key: hashlib.sha256(
            json.dumps(sorted(parts), separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        for key, parts in evidence.items()
        if parts
    }


@dataclass(frozen=True)
class ExamEnrollmentCandidate:
    ok: bool
    detail: str = ""
    should_create: bool = False


@dataclass(frozen=True)
class ExamQuestionIdMap:
    question_number_to_pk: dict[int, int]

    @property
    def pk_set(self) -> set[int]:
        return set(self.question_number_to_pk.values())

    @property
    def question_number_set(self) -> set[int]:
        return set(self.question_number_to_pk.keys())


def request_is_parent(request: Any) -> bool:
    from apps.domains.student_app.permissions import is_request_parent

    return is_request_parent(request)


def request_student(request: Any):
    from apps.domains.student_app.permissions import get_request_student

    return get_request_student(request)


def grade_submission_objective(submission_id: int, *, force_regrade: bool = False):
    from apps.domains.results.services.grading_service import grade_submission

    return grade_submission(int(submission_id), force_regrade=force_regrade)


def lock_submission_score_edit_scope_before_write(*, submission) -> list[int]:
    """Lock an exam submission's Exam -> Session scope before mutable rows."""

    from apps.support.results.grading_dependencies import (
        lock_score_edit_scope_before_submission_grading,
    )

    return lock_score_edit_scope_before_submission_grading(submission=submission)


def rebind_representative_omr_submission(
    *,
    exam_id: int,
    enrollment_id: int,
    replacement_submission_id: int,
    allowed_previous_submission_ids: set[int],
    actor: str,
) -> bool:
    """Return True only when this transaction replaces the sitting's previous scan."""
    from django.core.exceptions import ValidationError
    from django.db import transaction
    from django.utils import timezone

    from apps.domains.results.models import ExamAttempt
    from apps.domains.submissions.models import Submission

    with transaction.atomic():
        # A teacher correcting an already graded paper's student is not
        # replacing the destination student's scan. Results owns the atomic
        # attribution correction and its grade/history/conflict policy.
        if ExamAttempt.objects.select_for_update().filter(
            exam_id=int(exam_id), submission_id=int(replacement_submission_id),
        ).exclude(enrollment_id=int(enrollment_id)).exists():
            return False
        attempt = (
            ExamAttempt.objects.select_for_update()
            .filter(
                exam_id=int(exam_id),
                enrollment_id=int(enrollment_id),
                is_representative=True,
            )
            .first()
        )
        if attempt is None or int(attempt.submission_id or 0) == int(replacement_submission_id):
            return False
        previous_submission_id = int(attempt.submission_id or 0)
        if previous_submission_id not in {int(value) for value in allowed_previous_submission_ids}:
            raise ValidationError("Representative attempt is not owned by the OMR replacement set.")

        submissions = {
            int(row.id): row
            for row in Submission.objects.select_for_update().filter(
                id__in=[previous_submission_id, int(replacement_submission_id)]
            )
        }
        previous = submissions.get(previous_submission_id)
        replacement = submissions.get(int(replacement_submission_id))
        if previous is None or replacement is None:
            raise ValidationError("OMR replacement submissions could not be resolved.")
        expected = (
            previous.tenant_id,
            previous.target_type,
            int(previous.target_id),
            int(previous.enrollment_id or 0),
            previous.source,
        )
        actual = (
            replacement.tenant_id,
            replacement.target_type,
            int(replacement.target_id),
            int(replacement.enrollment_id or 0),
            replacement.source,
        )
        if (
            expected != actual
            or actual[1] != Submission.TargetType.EXAM
            or actual[4] != Submission.Source.OMR_SCAN
        ):
            raise ValidationError("OMR replacement scope mismatch.")
        if actual[2] != int(exam_id) or actual[3] != int(enrollment_id):
            raise ValidationError("OMR replacement exam or enrollment mismatch.")
        if (
            ExamAttempt.objects.select_for_update()
            .filter(submission_id=int(replacement_submission_id))
            .exclude(id=attempt.id)
            .exists()
        ):
            raise ValidationError("Replacement submission already belongs to another attempt.")

        now = timezone.now().isoformat()
        meta = dict(attempt.meta or {}) if isinstance(attempt.meta, dict) else {}
        replacements = list(meta.get("omr_scan_replacements") or [])
        replacements.append(
            {
                "previous_submission_id": previous_submission_id,
                "replacement_submission_id": int(replacement_submission_id),
                "reason": "same_exam_rescan_not_retake",
                "actor": actor,
                "at": now,
            }
        )
        meta["omr_scan_replacements"] = replacements
        initial = meta.get("initial_snapshot")
        if (
            isinstance(initial, dict)
            and int(initial.get("submission_id") or 0) == previous_submission_id
        ):
            initial["submission_id"] = int(replacement_submission_id)
            initial["previous_submission_id"] = previous_submission_id
            initial["scan_rebound_at"] = now
            meta["initial_snapshot"] = initial
        attempt.submission_id = int(replacement_submission_id)
        attempt.status = "pending"
        attempt.meta = meta
        attempt.save(update_fields=["submission_id", "status", "meta", "updated_at"])
        return True


def dispatch_submission_ai_job(**kwargs: Any) -> Any:
    from apps.domains.ai.gateway import dispatch_job

    return dispatch_job(**kwargs)


def exam_belongs_to_tenant(*, exam_id: int, tenant: Any) -> bool:
    from apps.domains.exams.models import Exam

    return Exam.objects.filter(id=int(exam_id), tenant=tenant).exists()


def complete_submission_after_auto_grade(submission, *, actor: str) -> None:
    from django.core.exceptions import ValidationError

    from apps.domains.submissions.models import Submission
    from apps.domains.submissions.services.lifecycle import (
        can_mark_done,
        mark_done,
        mark_grading,
    )

    if submission.status == Submission.Status.ANSWERS_READY:
        mark_grading(submission, actor=actor)
        mark_done(submission, actor=actor)
        return

    if can_mark_done(submission.status):
        mark_done(submission, actor=actor)
        return

    logger.error(
        "Submission %s in status '%s' cannot transition to 'done'; "
        "aborting grading to preserve data consistency.",
        submission.id,
        submission.status,
    )
    raise ValidationError(
        f"Submission {submission.id} in status '{submission.status}' "
        f"cannot be graded - invalid state for transition to 'done'."
    )


def _refresh_omr_score_review_after_key(*, submission, answer_key, choice_question_ids):
    """Recheck scan-only ambiguity after a complete answer key becomes available."""
    from django.utils import timezone

    from apps.domains.results.services.answer_matching import (
        answer_matches,
        correct_answer_sets,
    )
    from apps.domains.submissions.models import Submission, SubmissionAnswer
    from apps.support.omr.answer_policy import ambiguous_answer_can_change_score

    if submission.source != Submission.Source.OMR_SCAN or not answer_key:
        return
    meta = dict(submission.meta or {})
    review = dict(meta.get("manual_review") or {})
    reasons = set(review.get("reasons") or [])
    if meta.get("manual_edits") or review.get("resolved_at"):
        return

    correct = answer_key.answers
    if not isinstance(correct, dict) or not choice_question_ids or any(
        not correct_answer_sets(correct.get(str(qid))) for qid in choice_question_ids
    ):
        return

    answers = list(SubmissionAnswer.objects.filter(
        tenant_id=submission.tenant_id,
        submission=submission,
        exam_question_id__in=choice_question_ids,
    ))
    if len(answers) != len(choice_question_ids) or any(
        not isinstance((answer.meta or {}).get("omr"), dict) for answer in answers
    ):
        return

    score_ambiguous = False
    low_confidence_ambiguous = False
    has_score_signal = False
    answer_meta_changed = False
    for answer in answers:
        omr_meta = dict(answer.meta["omr"])
        detected = [part.strip() for part in answer.answer.split(",") if part.strip()]
        expected = correct.get(str(answer.exam_question_id))
        expected_multi = len(detected) > 1 and answer_matches(detected, expected)
        status = str(omr_meta.get("status") or "").lower()
        marking = str(omr_meta.get("marking") or "").lower()
        score_signal = marking == "multi" or status not in ("ok", "blank")
        has_score_signal |= score_signal
        if status != "error" and not expected_multi and score_signal:
            affects_score = ambiguous_answer_can_change_score(
                detected_values=detected, correct_answer=expected,
            )
            score_ambiguous |= affects_score
            low_confidence_ambiguous |= status == "low_confidence" and affects_score
        if expected_multi != bool(omr_meta.get("expected_multi_answer")):
            if expected_multi:
                omr_meta["expected_multi_answer"] = True
            else:
                omr_meta.pop("expected_multi_answer", None)
            answer.meta = {**answer.meta, "omr": omr_meta}
            answer.save(update_fields=["meta", "updated_at"])
            answer_meta_changed = True

    if not has_score_signal and "ANSWER_SCORE_AMBIGUOUS" not in reasons:
        return

    reasons.discard("ANSWER_SCORE_AMBIGUOUS")
    reasons.discard("ANSWER_LOW_CONFIDENCE")
    if score_ambiguous:
        reasons.add("ANSWER_SCORE_AMBIGUOUS")
    if low_confidence_ambiguous:
        reasons.add("ANSWER_LOW_CONFIDENCE")
    if (
        not answer_meta_changed
        and review.get("reasons") == sorted(reasons)
        and review.get("required") == bool(reasons)
    ):
        return
    review["reasons"] = sorted(reasons)
    review["required"] = bool(reasons)
    review["updated_at"] = timezone.now().isoformat()
    meta["manual_review"] = review
    submission.meta = meta
    submission.save(update_fields=["meta", "updated_at"])


def regrade_exam_submissions(*, tenant, exam_id: int, actor: str) -> dict[str, Any]:
    from django.db import transaction

    from apps.domains.results.models import ResultItem
    from apps.domains.results.services.manual_exam_answers import regrade_manual_exam_answers
    from apps.domains.submissions.models import Submission
    from apps.domains.exams.models import AnswerKey
    from apps.domains.submissions.services.lifecycle import reopen_for_regrade
    from apps.domains.exams.models import Exam
    from apps.support.omr.score_shape import get_exam_score_shape
    from apps.support.results.grading_dependencies import (
        lock_exam_and_score_edit_scope_for_grading,
        lock_exam_submission_regrade_state,
    )

    regradable_statuses = {
        Submission.Status.DONE,
        Submission.Status.ANSWERS_READY,
    }
    exam = Exam.objects.get(id=int(exam_id), tenant=tenant)
    score_shape = get_exam_score_shape(exam)
    choice_question_ids = {
        qid for qid, kind in score_shape.question_kind_by_id.items()
        if kind == "choice"
    }
    submissions = list(
        Submission.objects.filter(
            tenant=tenant,
            target_type=Submission.TargetType.EXAM,
            target_id=int(exam_id),
        )
        .exclude(status=Submission.Status.SUPERSEDED)
        .order_by("id")
        .values_list("id", "status")
    )

    graded = 0
    skipped = 0
    failed: list[dict[str, object]] = []
    needs_review: list[dict[str, object]] = []

    for submission_id, current_status in submissions:
        if current_status not in regradable_statuses:
            skipped += 1
            continue
        try:
            with transaction.atomic():
                lock_exam_and_score_edit_scope_for_grading(
                    exam_id=int(exam_id),
                    tenant_id=int(tenant.id),
                )
                submission, result, not_submitted = lock_exam_submission_regrade_state(
                    submission_id=int(submission_id),
                    exam_id=int(exam_id),
                    tenant_id=int(tenant.id),
                )
                if submission.status not in regradable_statuses:
                    skipped += 1
                    continue
                if submission.enrollment_id is None:
                    needs_review.append({
                        "submission_id": int(submission_id),
                        "detail": "학생이 식별되지 않아 정답 변경 후 재채점을 보류했습니다.",
                    })
                    skipped += 1
                    continue
                manual_question_ids = (
                    ResultItem.objects.filter(result=result, source="manual")
                    .values_list("question_id", flat=True)
                    if result is not None else ()
                )
                if any(score_shape.question_kind(int(qid)) == "choice" for qid in manual_question_ids):
                    # A teacher-owned item may differ from the scan; keep it intact.
                    needs_review.append({
                        "submission_id": int(submission_id),
                        "detail": "수기 보정 문항이 있어 자동 재채점에서 제외했습니다.",
                    })
                    skipped += 1
                    continue
                if not_submitted:
                    skipped += 1
                    continue
                if submission.source == Submission.Source.OMR_SCAN:
                    _refresh_omr_score_review_after_key(
                        submission=submission,
                        answer_key=AnswerKey.objects.filter(
                            exam_id=score_shape.template_exam_id,
                        ).first(),
                        choice_question_ids=choice_question_ids,
                    )
                if submission.status != Submission.Status.ANSWERS_READY:
                    reopen_for_regrade(submission, actor=actor)
                grade_submission_objective(int(submission_id), force_regrade=True)
            graded += 1
        except Exception as exc:
            failed.append(
                {
                    "submission_id": int(submission_id),
                    "status": str(current_status),
                    "detail": str(exc) or exc.__class__.__name__,
                }
            )

    manual = regrade_manual_exam_answers(exam=exam, tenant=tenant)
    needs_review.extend(manual["needs_review"])
    failed.extend({
        "submission_id": 0, "enrollment_id": item["enrollment_id"],
        "status": "manual_entry", "detail": item["detail"],
    } for item in manual["failed"])
    return {
        "exam_id": int(exam_id),
        "total": len(submissions),
        "graded": graded,
        "skipped": skipped,
        "failed": failed,
        "manual_total": manual["total"],
        "manual_graded": manual["graded"],
        "needs_review": needs_review,
    }


def target_belongs_to_tenant(target_type, target_id, tenant) -> bool:
    from apps.domains.submissions.models import Submission

    try:
        target_id_i = int(target_id)
        if target_type == Submission.TargetType.EXAM:
            from apps.domains.exams.models import Exam

            return Exam.objects.filter(
                id=target_id_i,
                tenant=tenant,
                sessions__lecture__tenant=tenant,
            ).exists()
        if target_type == Submission.TargetType.HOMEWORK:
            from apps.domains.homework_results.models import Homework

            return (
                Homework.objects.filter(
                    id=target_id_i,
                    session__lecture__tenant=tenant,
                )
                .exclude(meta__removed_from_session_at__isnull=False)
                .exists()
            )
    except Exception:
        return False
    return False


def staff_submission_student_exists(*, tenant, student_id: int) -> bool:
    from apps.domains.students.models import Student

    return Student.objects.filter(tenant=tenant, id=student_id, deleted_at__isnull=True).exists()


def staff_homework_file_targets(*, tenant):
    from apps.domains.homework_results.models import Homework

    return Homework.objects.filter(
        tenant=tenant,
        homework_type=Homework.HomeworkType.REGULAR,
        session__lecture__tenant=tenant,
    ).exclude(meta__removed_from_session_at__isnull=False)


def homework_submission_target_exists(*, homework_id: int, tenant) -> bool:
    from apps.domains.homework_results.models import Homework

    return Homework.objects.filter(
        id=int(homework_id),
        session__lecture__tenant=tenant,
    ).exists()


def clinic_highlight_map_for_enrollments(*, tenant, enrollment_ids: set[int]) -> dict[int, bool]:
    if not enrollment_ids:
        return {}

    from apps.domains.results.utils.clinic_highlight import compute_clinic_highlight_map

    return compute_clinic_highlight_map(
        tenant=tenant,
        enrollment_ids=enrollment_ids,
    )


def enrollment_map_for_submission_list(*, tenant, enrollment_ids: set[int]) -> dict[int, Any]:
    if not enrollment_ids:
        return {}

    from apps.domains.enrollment.models import Enrollment

    return {
        enrollment.id: enrollment
        for enrollment in (
            Enrollment.objects.select_related("student", "lecture")
            .filter(id__in=enrollment_ids, tenant=tenant, student__tenant=tenant, lecture__tenant=tenant)
        )
    }


def exam_submission_list_allowed(*, tenant, exam_id: int) -> bool:
    from apps.domains.exams.models import Exam
    from apps.domains.submissions.models import Submission

    exam_id_i = int(exam_id)
    exam_qs = Exam.objects.filter(id=exam_id_i)
    if exam_qs.filter(sessions__lecture__tenant=tenant).exists():
        return True
    if hasattr(Exam, "tenant") and exam_qs.filter(tenant=tenant).exists():
        return True
    return Submission.objects.filter(
        tenant=tenant,
        target_type=Submission.TargetType.EXAM,
        target_id=exam_id_i,
    ).exists()


def score_map_for_exam_submission_list(
    *, tenant, exam_id: int, submission_enrollments: dict[int, int], focus_id: int | None = None,
) -> dict[int, float]:
    if not submission_enrollments:
        return {}

    from django.db.models import F
    from apps.domains.results.models import ExamAttempt, Result
    from apps.domains.results.utils.initial_exam_score import stored_attempt_total

    rows = Result.objects.filter(
        target_type="exam", target_id=exam_id, enrollment__tenant=tenant,
        attempt__exam_id=exam_id, attempt__exam__tenant=tenant, attempt__enrollment_id=F("enrollment_id"),
        attempt__submission_id__in=submission_enrollments,
    ).values_list("attempt__submission_id", "enrollment_id", "total_score")
    scores = {sid: float(score) for sid, eid, score in rows
              if submission_enrollments.get(sid) == eid and score is not None}
    if focus_id in submission_enrollments and focus_id not in scores:
        # A retake may now own the representative Result. Read only the requested sitting's saved score.
        attempt = ExamAttempt.objects.regular().with_initial_grade().filter(
            exam_id=exam_id, exam__tenant=tenant, enrollment__tenant=tenant,
            enrollment_id=submission_enrollments[focus_id], submission_id=focus_id, status="done",
        ).first()
        if attempt and isinstance(attempt.meta, dict) and attempt.meta.get("status") != "NOT_SUBMITTED":
            stored = stored_attempt_total(attempt.meta, initial=attempt.is_initial_grade)
            if stored is not None:
                scores[focus_id] = stored
    return scores


def enrollment_belongs_to_tenant(*, enrollment_id, tenant) -> bool:
    from apps.domains.enrollment.models import Enrollment

    return Enrollment.objects.filter(id=enrollment_id, tenant=tenant).exists()


def student_owns_enrollment(*, enrollment_id, student, tenant) -> bool:
    from apps.domains.enrollment.models import Enrollment

    return Enrollment.objects.filter(
        id=enrollment_id,
        student=student,
        tenant=tenant,
    ).exists()


def target_enrollment_assignment_exists(
    target_type,
    target_id,
    enrollment_id,
    tenant,
    *,
    ensure_exam_enrollment: bool = False,
) -> bool:
    from apps.domains.submissions.models import Submission

    try:
        target_id_i = int(target_id)
        enrollment_id_i = int(enrollment_id)
    except (TypeError, ValueError):
        return False

    from apps.domains.enrollment.models import Enrollment, SessionEnrollment

    enrollment = (
        Enrollment.objects.filter(
            id=enrollment_id_i,
            tenant=tenant,
            status="ACTIVE",
            lecture__is_active=True,
            student__deleted_at__isnull=True,
        )
        .select_related("lecture")
        .first()
    )
    if not enrollment:
        return False

    if target_type == Submission.TargetType.EXAM:
        from apps.domains.exams.models import ExamEnrollment

        in_exam = ExamEnrollment.objects.filter(
            exam_id=target_id_i,
            exam__tenant=tenant,
            enrollment_id=enrollment_id_i,
            enrollment__tenant=tenant,
        ).exists()
        if in_exam:
            return True

        if ExamEnrollment.objects.filter(
            exam_id=target_id_i,
            exam__tenant=tenant,
        ).exists():
            return False

        in_session = SessionEnrollment.objects.filter(
            tenant=tenant,
            session__exams__id=target_id_i,
            session__exams__tenant=tenant,
            enrollment_id=enrollment_id_i,
            enrollment__status="ACTIVE",
            enrollment__student__deleted_at__isnull=True,
        ).exists()
        if in_session and ensure_exam_enrollment:
            from apps.support.exams.enrollment_candidates import ensure_legacy_exam_roster_candidate

            return ensure_legacy_exam_roster_candidate(
                tenant=tenant, exam_id=target_id_i, enrollment_id=enrollment_id_i,
            )
        return in_session

    if target_type == Submission.TargetType.HOMEWORK:
        from apps.domains.homework_results.models import Homework

        return (
            Homework.objects.filter(
                id=target_id_i,
                session__lecture_id=enrollment.lecture_id,
                session__lecture__tenant=tenant,
            )
            .exclude(meta__removed_from_session_at__isnull=False)
            .filter(
                assignments__tenant=tenant,
                assignments__enrollment_id=enrollment_id_i,
            )
            .exists()
        )

    return False


def _latest_homework_scores(*, tenant, enrollment_ids, homework_ids):
    from django.db.models import F, Window
    from django.db.models.functions import RowNumber
    from apps.domains.homework_results.models import HomeworkScore

    return (
        HomeworkScore.objects.filter(
            enrollment_id__in=enrollment_ids,
            enrollment__tenant=tenant,
            homework_id__in=homework_ids,
            homework__tenant=tenant,
        )
        .order_by()
        .annotate(
            attempt_rank=Window(
                expression=RowNumber(),
                partition_by=[F("enrollment_id"), F("homework_id")],
                order_by=[F("attempt_index").desc(), F("updated_at").desc(), F("id").desc()],
            ),
        )
        .filter(attempt_rank=1)
    )


def _homework_review_source(score_passed: bool | None, manual_completed: bool) -> str | None:
    if score_passed is not None:
        return "score" if score_passed else None
    return "manual" if manual_completed else None


def homework_submission_media_lock_keys(*, tenant, enrollment_ids, homework_ids) -> set[tuple[int, int]]:
    """Current upload locks for the write API and learner read models."""
    enrollment_ids = set(enrollment_ids)
    homework_ids = set(homework_ids)
    if not enrollment_ids or not homework_ids:
        return set()

    latest_scores = {
        (enrollment_id, homework_id): passed
        for enrollment_id, homework_id, passed in _latest_homework_scores(
            tenant=tenant, enrollment_ids=enrollment_ids, homework_ids=homework_ids,
        ).values_list("enrollment_id", "homework_id", "passed")
    }
    manual_completed = set()
    if len(latest_scores) < len(enrollment_ids) * len(homework_ids):
        from apps.domains.progress.models import AssessmentCorrection

        manual_completed = set(
            AssessmentCorrection.objects.filter(
                tenant=tenant,
                enrollment_id__in=enrollment_ids,
                source_type=AssessmentCorrection.SourceType.HOMEWORK,
                source_id__in=homework_ids,
                completed=True,
            ).values_list("enrollment_id", "source_id")
        )
    from apps.support.submissions.homework_acceptance import homework_acceptance_rows

    accepted = set(homework_acceptance_rows(
        tenant=tenant, enrollment_ids=enrollment_ids, homework_ids=homework_ids,
    ).filter(fingerprint__isnull=False).values_list("enrollment_id", "homework_id"))
    return accepted | {
        key for key in latest_scores.keys() | manual_completed
        if _homework_review_source(latest_scores.get(key), key in manual_completed)
    }


def homework_submission_is_teacher_reviewed(
    *,
    tenant,
    enrollment_id: int,
    homework_id: int,
) -> bool:
    """Return whether the current passing/completed result locks media changes."""
    return (enrollment_id, homework_id) in homework_submission_media_lock_keys(
        tenant=tenant, enrollment_ids=[enrollment_id], homework_ids=[homework_id],
    )


def homework_submission_review_map(
    *,
    tenant,
    enrollment_ids: set[int],
    homework_id: int,
) -> dict[int, dict[str, Any]]:
    """Return the teacher-owned review projection for homework submissions."""
    if not enrollment_ids:
        return {}

    normalized_ids = {int(enrollment_id) for enrollment_id in enrollment_ids}
    from apps.domains.progress.models import AssessmentCorrection

    correction_map = {
        int(correction.enrollment_id): correction
        for correction in AssessmentCorrection.objects.filter(
            tenant=tenant,
            enrollment_id__in=normalized_ids,
            source_type=AssessmentCorrection.SourceType.HOMEWORK,
            source_id=int(homework_id),
        )
    }
    score_map = {
        int(score.enrollment_id): score
        for score in _latest_homework_scores(
            tenant=tenant, enrollment_ids=normalized_ids, homework_ids=[int(homework_id)],
        ).only("enrollment_id", "updated_at", "passed")
    }

    from apps.support.submissions.homework_acceptance import homework_acceptance_payload, homework_acceptance_rows

    file_decisions = {
        row.enrollment_id: row
        for row in homework_acceptance_rows(tenant=tenant, enrollment_ids=normalized_ids, homework_ids=[homework_id])
    }
    review_map: dict[int, dict[str, Any]] = {}
    for enrollment_id in normalized_ids:
        score = score_map.get(enrollment_id)
        correction = correction_map.get(enrollment_id)
        review_source = _homework_review_source(
            bool(score.passed) if score is not None else None,
            bool(correction and correction.completed),
        )
        reviewed_at = (
            score.updated_at
            if review_source == "score"
            else correction.completed_at
            if review_source == "manual"
            else None
        )
        review_map[enrollment_id] = {
            **homework_acceptance_payload(file_decisions.get(enrollment_id)),
            "teacher_reviewed": review_source is not None,
            "teacher_review_source": review_source,
            "teacher_review_note": (
                correction.note if review_source == "manual" else ""
            ),
            "teacher_reviewed_at": reviewed_at.isoformat() if reviewed_at else None,
            "teacher_review_updated_at": (
                correction.updated_at.isoformat() if correction else None
            ),
        }
    return review_map


def validate_exam_enrollment_candidate(
    *,
    tenant,
    exam_id: int,
    enrollment_id: int,
    session_id: int | None = None,
) -> ExamEnrollmentCandidate:
    from apps.domains.exams.models import ExamEnrollment
    from apps.support.submissions.candidate_dependencies import (
        active_exam_review_enrollments,
        exam_review_roster_filter,
        exam_review_session_is_linked,
    )

    if session_id is not None and (
        not exam_id
        or not exam_review_session_is_linked(tenant=tenant, exam_id=exam_id, session_id=session_id)
    ):
        return ExamEnrollmentCandidate(ok=False, detail="해당 시험에 연결된 차시가 아닙니다.")

    enrollments = active_exam_review_enrollments(tenant=tenant).filter(id=enrollment_id)
    if not enrollments.exists():
        return ExamEnrollmentCandidate(
            ok=False,
            detail=f"enrollment_id={enrollment_id}는 현재 학원의 학생이 아닙니다.",
        )

    if not exam_id:
        return ExamEnrollmentCandidate(ok=True)

    targets = ExamEnrollment.objects.filter(exam_id=exam_id, exam__tenant=tenant)
    has_exam_targets = targets.exists()
    if has_exam_targets and not targets.filter(enrollment_id=enrollment_id).exists():
        return ExamEnrollmentCandidate(ok=False, detail="해당 시험의 대상 학생이 아닙니다.")

    if (not has_exam_targets or session_id is not None) and not enrollments.filter(
        exam_review_roster_filter(
            tenant=tenant, exam_id=exam_id, session_id=session_id, include_attendance=has_exam_targets,
        ),
    ).exists():
        return ExamEnrollmentCandidate(
            ok=False,
            detail=("해당 차시에 등록되지 않은 학생입니다." if session_id is not None
                    else "해당 시험에 등록되지 않은 학생입니다."),
        )
    return ExamEnrollmentCandidate(ok=True, should_create=not has_exam_targets)


def create_exam_enrollment_assignment(*, tenant, exam_id: int, enrollment_id: int) -> bool:
    from django.core.exceptions import ValidationError
    from apps.domains.exams.models import ExamEnrollment
    from apps.support.exams.enrollment_candidates import ensure_legacy_exam_roster_candidate

    already_assigned = ExamEnrollment.objects.filter(
        exam_id=int(exam_id), exam__tenant=tenant, enrollment_id=int(enrollment_id),
    ).exists()
    if not ensure_legacy_exam_roster_candidate(
        tenant=tenant, exam_id=exam_id, enrollment_id=enrollment_id,
    ):
        raise ValidationError("해당 시험의 대상 학생이 아닙니다.")
    return not already_assigned


def exam_question_number_by_id(*, tenant, question_ids: list[int]) -> dict[int, int]:
    if not question_ids:
        return {}

    from apps.domains.exams.models import ExamQuestion

    return {
        int(qid): int(number)
        for qid, number in ExamQuestion.objects.filter(
            id__in=question_ids,
            sheet__exam__tenant=tenant,
        ).values_list("id", "number")
    }


def allowed_manual_exam_question_ids(*, tenant, exam_id: int) -> set[int] | None:
    from apps.domains.exams.models import Exam, ExamQuestion

    exam = (
        Exam.objects.filter(id=int(exam_id or 0), tenant=tenant)
        .select_related("template_exam")
        .first()
    )
    if not exam:
        return None

    sheet_exam_ids = [exam.id]
    if exam.template_exam_id:
        sheet_exam_ids.append(exam.template_exam_id)

    return set(
        ExamQuestion.objects.filter(
            sheet__exam_id__in=sheet_exam_ids,
            sheet__exam__tenant=tenant,
        ).values_list("id", flat=True)
    )


def question_id_map_for_exam(*, exam_id: int) -> ExamQuestionIdMap | None:
    from apps.domains.exams.models import Exam, ExamQuestion, Sheet
    from apps.domains.exams.services.template_resolver import resolve_template_exam

    exam = Exam.objects.filter(id=int(exam_id)).first()
    if not exam:
        return None

    template_exam = resolve_template_exam(exam)
    sheet = Sheet.objects.filter(exam=template_exam).first()
    if not sheet:
        return None

    questions = list(ExamQuestion.objects.filter(sheet=sheet).only("id", "number"))
    if not questions:
        return None

    return ExamQuestionIdMap(
        question_number_to_pk={int(question.number): int(question.id) for question in questions},
    )


def latest_ai_job_for_submission(*, submission_id: int, tenant_id: int) -> Any | None:
    from apps.domains.ai.models import AIJobModel

    return (
        AIJobModel.objects
        .filter(
            tenant_id=str(tenant_id),
            source_domain="submissions",
            source_id=str(submission_id),
        )
        .order_by("-created_at", "-id")
        .first()
    )


def ai_result_payload_for_job(ai_job: Any) -> dict:
    from apps.domains.ai.models import AIResultModel

    ai_result = AIResultModel.objects.filter(job=ai_job).first()
    payload = ai_result.payload if ai_result else {}
    return payload if isinstance(payload, dict) else {}


def dispatch_ai_result_to_submissions_domain(
    *,
    job_id: str,
    status: str,
    result_payload: dict,
    error: str | None,
    source_id: str,
    tier: str,
) -> None:
    from apps.domains.ai.callbacks import dispatch_ai_result_to_domain

    dispatch_ai_result_to_domain(
        job_id=job_id,
        status=status,
        result_payload=result_payload,
        error=error,
        source_domain="submissions",
        source_id=source_id,
        tier=tier,
    )


def get_synced_exam_score(
    *,
    tenant,
    target_id: int,
    enrollment_id: int,
) -> tuple[int | None, float | None, float | None]:
    try:
        from apps.domains.results.models import Result

        result = (
            Result.objects.filter(
                target_type="exam",
                target_id=int(target_id),
                enrollment_id=int(enrollment_id),
                enrollment__tenant=tenant,
            )
            .only("id", "total_score", "max_score")
            .order_by("-id")
            .first()
        )
        if result:
            return (
                int(result.id),
                float(result.total_score or 0.0),
                float(result.max_score or 0.0),
            )
    except Exception:
        return None, None, None
    return None, None, None


def absence_blocks_confirmed_scan(*, submission) -> bool:
    """Whether a teacher-confirmed scan is held back only by an absence."""

    from apps.domains.results.services.attempt_placeholders import (
        absence_blocks_confirmed_scan as _blocks,
    )

    return _blocks(submission=submission)


def record_absence_resume_for_confirmed_scan(*, submission, user_id):
    """Record the explicit OMR-review decision to grade a scan despite absence."""

    from apps.domains.results.services.attempt_placeholders import (
        record_absence_resume_for_confirmed_scan as _record,
    )

    return _record(submission=submission, user_id=user_id)


def finalize_omr_result_projection(*, result_id: int):
    """Finalize an OMR result through the results-domain service boundary."""

    from apps.domains.results.services.omr_subjective_completion import (
        finalize_omr_result_if_ready,
    )

    return finalize_omr_result_if_ready(result_id=int(result_id))


def active_submission_ai_job_exists(
    *, submission_id: int, tenant_id: int, now: datetime, grace: timedelta,
) -> bool:
    """Check a submission's live worker lease within its exact tenant scope."""
    from apps.domains.ai.models import AIJobModel

    recent = now - grace
    return AIJobModel.objects.filter(
        tenant_id=str(tenant_id),
        source_domain="submissions",
        source_id=str(submission_id),
    ).filter(
        Q(status="RUNNING")
        & (Q(lease_expires_at__gt=now) | Q(started_at__gte=recent))
        | Q(status__in=("PENDING", "VALIDATING", "RETRYING"), updated_at__gte=recent)
    ).exists()
