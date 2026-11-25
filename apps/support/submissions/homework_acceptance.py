"""Staff decisions for submitted homework files, separate from score and review history."""

from django.db import transaction
from django.db.models import Prefetch
from rest_framework.exceptions import APIException, NotFound, ValidationError

from apps.domains.progress.models import HomeworkFileAcceptance
from apps.domains.submissions.models import Submission, SubmissionMedia
from apps.domains.submissions.services.homework_media import (
    homework_media_set_fingerprint_for_submissions,
    serialize_legacy_homework_media,
)
from apps.support.homework.review_lock import lock_homework_review_target
from apps.support.submissions.dependencies import staff_homework_file_targets
from apps.support.submissions.homework_acceptance_queries import (
    homework_acceptance_rows as homework_acceptance_rows,
    homework_accepted_keys as homework_accepted_keys,
)


class HomeworkFileConflict(APIException):
    status_code = 409


def homework_acceptance_payload(decision):
    updated_at = decision.updated_at if decision else None
    return {
        "teacher_accepted": bool(decision and decision.fingerprint),
        "teacher_accepted_updated_at": updated_at.isoformat() if updated_at is not None else None,
    }


@transaction.atomic
def set_homework_file_acceptance(
    *, tenant, homework_id, enrollment_id, accepted, expected_updated_at, expected_media_set_fingerprint,
):
    from apps.domains.enrollment.models import Enrollment

    homework = staff_homework_file_targets(tenant=tenant).select_related("session").filter(pk=homework_id).first()
    if not homework or not Enrollment.objects.filter(
        id=enrollment_id, tenant=tenant, lecture_id=homework.session.lecture_id,
        student__tenant=tenant, student__deleted_at__isnull=True,
    ).exists():
        raise NotFound("해당 학생의 과제를 찾을 수 없습니다.")
    assignment = lock_homework_review_target(
        tenant=tenant, homework_id=homework_id, enrollment_id=enrollment_id, session_id=homework.session_id,
    )
    if not assignment:
        raise NotFound("해당 학생의 과제를 찾을 수 없습니다.")

    submissions = list(Submission.objects.filter(
        tenant=tenant, enrollment_id=enrollment_id, target_type=Submission.TargetType.HOMEWORK,
        target_id=homework_id, source__in=[Submission.Source.HOMEWORK_IMAGE, Submission.Source.HOMEWORK_VIDEO],
    ).exclude(status=Submission.Status.SUPERSEDED).prefetch_related(Prefetch(
        "media_files", queryset=SubmissionMedia.objects.filter(tenant=tenant).select_for_update().order_by("id"),
    )).order_by("id"))
    fingerprint = homework_media_set_fingerprint_for_submissions(submissions)
    if expected_media_set_fingerprint != fingerprint:
        raise HomeworkFileConflict({
            "code": "HOMEWORK_MEDIA_CHANGED", "detail": "제출 파일이 변경되었습니다. 최신 파일을 다시 확인해 주세요.",
        })

    target = dict(tenant=tenant, enrollment_id=enrollment_id, session_id=homework.session_id, homework_id=homework_id)
    decision = HomeworkFileAcceptance.objects.select_for_update().filter(**target).first()
    current_fingerprint = decision.fingerprint if decision else None
    actual_updated_at = decision.updated_at if decision else None
    if (accepted and current_fingerprint == fingerprint) or (not accepted and not current_fingerprint):
        return homework_acceptance_payload(decision)
    if expected_updated_at != actual_updated_at:
        raise HomeworkFileConflict({
            "code": "HOMEWORK_FILE_ACCEPTANCE_CONFLICT", "detail": "다른 화면에서 판정이 바뀌었습니다. 다시 확인해 주세요.",
        })
    if accepted:
        active_media = [
            media for submission in submissions if submission.status != Submission.Status.FAILED
            for media in submission.media_files.all() if media.removed_at is None
        ]
        if any(media.status == SubmissionMedia.Status.UPLOADING for media in active_media):
            raise HomeworkFileConflict({
                "code": "HOMEWORK_MEDIA_NOT_READY", "detail": "제출 파일을 저장 중입니다. 저장이 끝난 뒤 다시 확인해 주세요.",
            })
        has_uploaded = any(
            media.status == SubmissionMedia.Status.UPLOADED and media.object_key not in ("", "pending")
            for media in active_media
        )
        has_legacy = any(
            submission.file_key not in (None, "", "pending")
            and (item := serialize_legacy_homework_media(submission))["status"] == SubmissionMedia.Status.UPLOADED
            and not item["removed_at"]
            for submission in submissions
        )
        if not has_uploaded and not has_legacy:
            raise ValidationError({"accepted": "업로드를 마친 과제 파일이 필요합니다."})
    if decision is None:
        decision = HomeworkFileAcceptance(**target)
    decision.fingerprint = fingerprint if accepted else None
    decision.save()
    return homework_acceptance_payload(decision)
