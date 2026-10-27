"""Private homework video compatibility jobs. Encoding runs only in Video Batch."""
from __future__ import annotations

import hashlib
import os
import re
from datetime import timedelta
import uuid

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import APIException, ValidationError

from academy.adapters.compute.aws_batch import submit_homework_preview_batch_job
from apps.core.r2_paths import ai_submission_key
from apps.domains.submissions.models import HomeworkMediaPreview, Submission, SubmissionMedia
from apps.domains.submissions.services.homework_media import legacy_homework_media_removed_at
from apps.domains.submissions.services.lifecycle import _submission_owned_object_key, schedule_unreferenced_ai_object_cleanup
from apps.infrastructure.storage.r2 import generate_presigned_get_url

JOB_LEASE = timedelta(minutes=30)
ACTIVE = (HomeworkMediaPreview.Status.QUEUED, HomeworkMediaPreview.Status.PROCESSING)


class PreviewUnavailable(APIException):
    status_code = 503
    default_detail = "영상 재생 준비를 시작하지 못했습니다. 잠시 후 다시 시도해 주세요."
    default_code = "HOMEWORK_PREVIEW_UNAVAILABLE"


def source_fingerprint(source_key: str) -> str:
    # Upload keys are immutable and include the validated client file identity.
    return hashlib.sha256(source_key.encode("utf-8")).hexdigest()


def source_is_current(preview: HomeworkMediaPreview) -> bool:
    parent = preview.submission
    if parent.tenant_id != preview.tenant_id or parent.target_type != Submission.TargetType.HOMEWORK:
        return False
    if not _submission_owned_object_key(tenant_id=preview.tenant_id, key=preview.source_key, submission_id=parent.pk):
        return False
    if (parent.meta or {}).get("discarded"):
        return False
    if preview.source_media_id == f"legacy-{parent.pk}":
        return (parent.file_key == preview.source_key
                and parent.source == Submission.Source.HOMEWORK_VIDEO
                and not legacy_homework_media_removed_at(parent))
    if not preview.source_media_id.isdecimal():
        return False
    return SubmissionMedia.objects.filter(
        pk=int(preview.source_media_id), tenant_id=preview.tenant_id,
        submission_id=parent.pk, object_key=preview.source_key,
        media_kind=SubmissionMedia.Kind.VIDEO, status=SubmissionMedia.Status.UPLOADED,
        removed_at__isnull=True,
    ).exists()


def find_preview(*, submission, media_id: str, source_key: str):
    return HomeworkMediaPreview.objects.filter(
        tenant_id=submission.tenant_id, submission=submission,
        source_media_id=media_id, source_fingerprint=source_fingerprint(source_key),
    ).first()


def preview_payload(preview: HomeworkMediaPreview | None) -> dict:
    if preview is None:
        return {"status": "not_requested"}
    state = preview.status
    if state in ACTIVE and preview.updated_at < timezone.now() - JOB_LEASE:
        state = HomeworkMediaPreview.Status.FAILED
    payload = {"status": state, "retry_after_seconds": 3 if state in ACTIVE else 0}
    if state == HomeworkMediaPreview.Status.READY and preview.object_key:
        payload.update(url=generate_presigned_get_url(key=preview.object_key, expires_in=600), mime_type="video/mp4", expires_in=600)
    if state == HomeworkMediaPreview.Status.FAILED:
        payload["detail"] = "영상을 준비하지 못했습니다. 원본은 보관되어 있습니다. 다시 시도해 주세요."
    return payload


def prepare_preview(*, submission, media_id: str, source_key: str, media_kind: str) -> HomeworkMediaPreview:
    if media_kind != "video":
        raise ValidationError({"detail": "동영상에만 재생 준비를 요청할 수 있습니다."})
    runtime = os.getenv("ACADEMY_RUNTIME_ENV", "").strip().lower()
    if runtime == "preprod":
        # A preprod database must never enqueue into the development database.
        raise PreviewUnavailable()
    if runtime == "development":
        queue = getattr(settings, "HOMEWORK_PREVIEW_BATCH_JOB_QUEUE", "")
        definition = getattr(settings, "HOMEWORK_PREVIEW_BATCH_JOB_DEFINITION", "")
        if queue != "academy-development-homework-video-queue" or not re.fullmatch(r"academy-development-homework-video-[0-9a-f]{12}-v[1-9][0-9]*:1", definition):
            raise PreviewUnavailable()
    else:
        queue = getattr(settings, "VIDEO_BATCH_JOB_QUEUE", "")
        definition = getattr(settings, "VIDEO_BATCH_JOB_DEFINITION", "")
    # Never synthesize production resource names for an isolated environment.
    if not queue or not definition:
        raise PreviewUnavailable() from ImproperlyConfigured("Video Batch resources are not configured")
    old_key = ""
    with transaction.atomic():
        parent = Submission.objects.select_for_update().get(pk=submission.pk, tenant_id=submission.tenant_id)
        preview, created = HomeworkMediaPreview.objects.select_for_update().get_or_create(
            tenant_id=parent.tenant_id, submission=parent, source_media_id=media_id,
            source_fingerprint=source_fingerprint(source_key),
            defaults={"source_key": source_key},
        )
        if not source_is_current(preview):
            raise ValidationError({"detail": "현재 제출된 동영상을 다시 선택해 주세요."})
        if not created:
            if preview.status == HomeworkMediaPreview.Status.READY:
                return preview
            if preview.status in ACTIVE and preview.updated_at >= timezone.now() - JOB_LEASE:
                return preview
            if preview.attempt_count >= 3 and preview.updated_at >= timezone.now() - timedelta(hours=1):
                raise ValidationError({"detail": "잠시 후 다시 시도해 주세요. 원본 영상은 보관되어 있습니다."})
            old_key = preview.object_key
            preview.attempt_token = uuid.uuid4()
            preview.attempt_count += 1
        preview.status = HomeworkMediaPreview.Status.QUEUED
        preview.batch_job_id = ""
        preview.error_code = ""
        preview.object_key = ai_submission_key(tenant_id=parent.tenant_id, submission_id=parent.pk,
            unique_id=f"preview-{preview.id.hex}-{preview.attempt_token.hex}", ext="mp4")
        preview.save()
    if old_key:
        schedule_unreferenced_ai_object_cleanup(tenant_id=parent.tenant_id, key=old_key)
    try:
        batch_id = submit_homework_preview_batch_job(
            preview_id=str(preview.pk), attempt_token=str(preview.attempt_token),
            queue_name=queue, job_definition=definition,
            region=getattr(settings, "AWS_REGION", "") or getattr(settings, "AWS_DEFAULT_REGION", "ap-northeast-2"),
        )
        if not batch_id:
            raise RuntimeError("Batch did not return a job identity")
        HomeworkMediaPreview.objects.filter(pk=preview.pk, attempt_token=preview.attempt_token).update(batch_job_id=batch_id)
        preview.batch_job_id = batch_id
    except Exception as exc:
        HomeworkMediaPreview.objects.filter(pk=preview.pk, attempt_token=preview.attempt_token, status=HomeworkMediaPreview.Status.QUEUED).update(
            status=HomeworkMediaPreview.Status.FAILED, error_code="enqueue_failed", updated_at=timezone.now())
        raise PreviewUnavailable() from exc
    return preview
