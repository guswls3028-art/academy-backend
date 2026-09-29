from __future__ import annotations

from django.db import transaction

from apps.domains.results.models import ExamResult
from apps.domains.results.services.exam_grading_service import ExamGradingService
from apps.domains.results.services.sync_result_from_submission import (
    sync_result_from_exam_submission,
)
from apps.domains.results.services.omr_subjective_completion import (
    finalize_omr_result_if_ready,
)
from apps.domains.results.services.submission_scope_guard import (
    validate_exam_submission_scope,
)
from apps.support.submissions.dependencies import complete_submission_after_auto_grade
from apps.support.results.grading_dependencies import (
    dispatch_progress_pipeline,
    get_submission_for_grading,
    get_exam_for_result_sync,
    lock_exam_submission_regrade_state,
    is_omr_manual_review_required,
    lock_score_edit_scope_before_submission_grading,
)


@transaction.atomic
def grade_submission(submission_id: int, *, force_regrade: bool = False) -> ExamResult:
    submission = get_submission_for_grading(submission_id=int(submission_id))
    lock_score_edit_scope_before_submission_grading(submission=submission)

    if submission is not None and str(submission.target_type) == "exam":
        submission, _canonical_result, not_submitted = lock_exam_submission_regrade_state(
            submission_id=int(submission.id),
            exam_id=int(submission.target_id),
            tenant_id=int(submission.tenant_id),
        )
        if not_submitted:
            exam = get_exam_for_result_sync(exam_id=int(submission.target_id))
            validate_exam_submission_scope(submission=submission, exam=exam)
            # Retain the existing compatibility snapshot without grading or
            # publishing the teacher-cancelled attempt again.
            retained = ExamResult.objects.get(submission=submission, exam=exam)
            if submission.status != submission.Status.DONE:
                complete_submission_after_auto_grade(
                    submission, actor="grader.absence_preserved",
                )
            return retained

    service = ExamGradingService()
    result = service.auto_grade_objective(
        submission_id=int(submission_id),
        force_regrade=force_regrade,
    )

    if is_omr_manual_review_required(submission):
        # 검토 필요 OMR은 관리자 DRAFT 점수만 계산하고, 학생 Result/진도/클리닉
        # 스냅샷에는 운영자가 확인 저장한 뒤 반영한다.
        return result

    # ✅ 모든 source(ONLINE, OMR_SCAN 등)에서 Result/ResultItem 동기화 (학생 결과 API용)
    canonical_result = None
    try:
        canonical_result = sync_result_from_exam_submission(submission_id)
    except Exception:
        import logging
        logging.getLogger(__name__).exception(
            "Result sync failed for submission %s", submission_id
        )
        raise

    # A fully recognized OMR submission has no remaining teacher-owned grading
    # step. Publish its compatibility snapshot before progress/analytics read it.
    # OMR rows explicitly marked for manual review remain DRAFT above.
    if submission.source == submission.Source.OMR_SCAN:
        if canonical_result is None:
            return result
        decision = finalize_omr_result_if_ready(result_id=int(canonical_result.id))
        if decision.projection_ready:
            result.refresh_from_db()

    # Final and pending transitions both invalidate the prior projection. In
    # particular, a newer mixed OMR scan must retract a previously completed
    # progress/clinic snapshot until its essay grading is complete.
    dispatch_progress_pipeline(submission_id=int(submission_id))

    return result
