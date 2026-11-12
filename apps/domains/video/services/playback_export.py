"""Admission and status of an on-demand playback copy. Raw originals are separate."""

from __future__ import annotations

import logging
import os
from datetime import timedelta
from uuid import uuid4

from django.conf import settings
from django.db import transaction
from django.utils import timezone
from rest_framework import serializers, status
from rest_framework.exceptions import APIException, NotFound, ValidationError
from rest_framework.response import Response

from academy.adapters.compute.aws_batch import submit_video_playback_export
from academy.adapters.storage.r2_objects import (
    VideoSourceStorageError,
    create_video_source_download_url,
    head_video_source_download,
)
from apps.core.models import Tenant
from apps.core.r2_paths import video_hls_master_path, video_playback_export_key
from apps.core.services.ops_audit import record_audit
from apps.domains.video.models import Video, VideoPlaybackExport
from apps.domains.video.services.source_download import video_download_filename


logger = logging.getLogger(__name__)
LEASE = timedelta(minutes=45)
RETRY_DELAY = timedelta(minutes=2)
JOB = VideoPlaybackExport.Status
RUNNING = (JOB.QUEUED, JOB.PROCESSING)


class VideoPlaybackExportSerializer(serializers.Serializer):
    state = serializers.ChoiceField(choices=["not_requested", "preparing", "ready", "failed"])
    download_url = serializers.URLField(required=False)
    filename = serializers.CharField(required=False)
    expires_in = serializers.IntegerField(required=False)


class ExportUnavailable(APIException):
    status_code = 503
    default_detail = "재생본을 준비하지 못했습니다. 잠시 후 다시 시도해 주세요."


class ExportBusy(APIException):
    status_code = 429
    default_detail = "이 학원에서 다른 재생본을 준비 중입니다. 잠시 후 다시 시도해 주세요."


def _response(state, http=status.HTTP_200_OK, **extra):
    response = Response({"state": state, **extra}, status=http)
    response["Cache-Control"] = "private, no-store"
    return response


def _state(export, now):
    if export is None:
        return "not_requested"
    if export.status in RUNNING:
        return "preparing" if export.updated_at >= now - LEASE else "failed"
    if export.status == JOB.READY and export.expires_at and export.expires_at > now + timedelta(seconds=10):
        return "ready"
    return "failed" if export.status == JOB.FAILED else "not_requested"


def _canonical(source):
    return (
        source.source_type == Video.SourceType.UPLOADED
        and source.status == Video.Status.READY
        and source.hls_path == video_hls_master_path(source.tenant_id, source.pk)
    )


def _batch_target():
    # The ordinary Video Batch in production owns this worker. Isolated APIs must not submit to it.
    runtime = os.environ.get("ACADEMY_RUNTIME_ENV", "").strip().lower()
    if settings.SETTINGS_MODULE != "apps.api.config.settings.prod" or runtime not in ("", "prod", "production"):
        raise ExportUnavailable()
    queue = getattr(settings, "VIDEO_BATCH_JOB_QUEUE", "")
    definition = getattr(settings, "VIDEO_BATCH_JOB_DEFINITION", "")
    if not queue or not definition:
        raise ExportUnavailable()
    region = getattr(settings, "AWS_REGION", "") or getattr(settings, "AWS_DEFAULT_REGION", "ap-northeast-2")
    return queue, definition, region


def _issue(request, video, source, export):
    try:
        key = video_playback_export_key(source.tenant_id, source.pk, export.attempt)
        if not head_video_source_download(key=key):
            return None
        if not Video.objects.filter(pk=video.pk, tenant=request.tenant).exists():
            raise NotFound("해당 영상을 찾을 수 없습니다.")
        remaining = int((export.expires_at - timezone.now()).total_seconds())
        if remaining < 10:
            return None
        filename = video_download_filename(video, "mp4")
        ttl = min(300, remaining)
        url = create_video_source_download_url(key=key, filename=filename, expires_in=ttl)
    except VideoSourceStorageError as exc:
        raise ExportUnavailable() from exc
    if record_audit(
        request,
        action="video.playback_export_download",
        summary="재생본 다운로드 주소 발급",
        target_tenant=request.tenant,
        payload={"video_id": video.pk, "source_video_id": source.pk},
    ) is None:
        raise ExportUnavailable()
    return _response("ready", download_url=url, filename=filename, expires_in=ttl)


def video_playback_export(request, video):
    source = video
    now = timezone.now()
    export = VideoPlaybackExport.objects.filter(video_id=source.pk).first()
    state = _state(export, now)
    if request.method == "GET":
        return _response(state)
    if not _canonical(source):
        raise ValidationError("학원에서 재생할 수 있는 영상만 MP4로 내려받을 수 있습니다.")
    if state == "preparing":
        return _response(state, status.HTTP_202_ACCEPTED)
    if state == "ready":
        ready = _issue(request, video, source, export)
        if ready is not None:
            return ready
    queue, definition, region = _batch_target()
    with transaction.atomic():
        # Serialize export admission for this academy; workers remain independent of request/DB locks.
        Tenant.objects.select_for_update().get(pk=request.tenant.pk)
        current_source = Video.objects.select_for_update().filter(pk=source.pk, tenant=request.tenant).first()
        if current_source is None or not _canonical(current_source):
            raise NotFound("재생본을 준비할 수 있는 영상이 없습니다.")
        current = VideoPlaybackExport.objects.select_for_update().filter(video=current_source).first()
        if _state(current, now) == "preparing":
            return _response("preparing", status.HTTP_202_ACCEPTED)
        if _state(current, now) == "ready" and (export is None or current.attempt != export.attempt):
            return _response("ready")
        if current and current.status == JOB.FAILED and current.updated_at >= now - RETRY_DELAY:
            return _response("failed")
        if VideoPlaybackExport.objects.filter(
            video__tenant=request.tenant, status__in=RUNNING, updated_at__gte=now - LEASE,
        ).exclude(video=current_source).exists():
            raise ExportBusy()
        if current is None:
            current = VideoPlaybackExport(video=current_source)
        current.attempt = uuid4()
        current.status = JOB.QUEUED
        current.expires_at = None
        current.save()
        if record_audit(
            request,
            action="video.playback_export_requested",
            summary="재생본 MP4 준비 요청",
            target_tenant=request.tenant,
            payload={"video_id": video.pk, "source_video_id": source.pk},
        ) is None:
            raise ExportUnavailable()
    try:
        job_id = submit_video_playback_export(
            export_id=current.pk, attempt=str(current.attempt), queue_name=queue, job_definition=definition, region=region,
        )
        if not job_id:
            raise RuntimeError("missing_batch_job")
    except Exception as exc:
        VideoPlaybackExport.objects.filter(pk=current.pk, attempt=current.attempt, status=JOB.QUEUED).update(
            status=JOB.FAILED, updated_at=timezone.now(),
        )
        logger.warning("VIDEO_PLAYBACK_EXPORT_SUBMIT_FAILED export_id=%s error_class=%s", current.pk, type(exc).__name__)
        raise ExportUnavailable() from exc
    return _response("preparing", status.HTTP_202_ACCEPTED)
