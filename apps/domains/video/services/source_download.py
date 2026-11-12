"""Staff download admission for the original file of an Academy upload."""

import logging
import re

from rest_framework import status
from rest_framework.exceptions import NotFound
from rest_framework.response import Response

from academy.adapters.storage.r2_objects import (
    VideoSourceStorageError,
    create_video_source_download_url,
    head_video_source_download,
)
from apps.core.r2_paths import video_raw_prefix
from apps.core.services.ops_audit import record_audit
from apps.domains.video.models import Video
from apps.domains.video.services.video_placements import resolve_media_source

logger = logging.getLogger(__name__)
DOWNLOAD_TTL_SECONDS = 300
_RAW_OBJECT = re.compile(r"[1-9][0-9]*/(?:[0-9a-f]{32}|[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12})\.([a-z0-9]{1,12})")
_UNSAFE_FILENAME = re.compile(r'[<>:"/\\|?*\x00-\x1f\x7f]')


def _error(code: str, message: str, http_status: int) -> Response:
    response = Response({"code": code, "detail": message}, status=http_status)
    response["Cache-Control"] = "private, no-store"
    return response


def _missing() -> Response:
    return _error("video_source_missing", "보관된 원본 파일을 찾을 수 없습니다.", status.HTTP_404_NOT_FOUND)


def _unavailable() -> Response:
    return _error("video_source_unavailable", "원본을 확인하지 못했습니다. 잠시 후 다시 시도해 주세요.",
                  status.HTTP_503_SERVICE_UNAVAILABLE)


def _filename(video: Video, extension: str) -> str:
    title = _UNSAFE_FILENAME.sub(" ", video.title).strip(" .")
    suffix = f".{extension}"
    if title.lower().endswith(suffix):
        title = title[:-len(suffix)].strip(" .")
    return f"{title[:110].rstrip(' .') or f'영상-{video.pk}'}{suffix}"


def create_source_download(request, video: Video) -> Response:
    if video.source_type != Video.SourceType.UPLOADED:
        return _error("video_source_external", "외부 링크 영상에는 학원에 보관된 원본이 없습니다.",
                      status.HTTP_409_CONFLICT)
    if video.status == Video.Status.PENDING:
        return _error("video_source_uploading", "파일 업로드가 완료된 뒤 내려받을 수 있습니다.",
                      status.HTTP_409_CONFLICT)

    prefix = video_raw_prefix(request.tenant.pk)
    try:
        source = resolve_media_source(video) if video.reused_from_id else video
    except NotFound:
        return _missing()
    key = source.file_key
    match = _RAW_OBJECT.fullmatch(key[len(prefix):]) if key.startswith(prefix) else None
    if match is None:
        return _missing()

    filename = _filename(video, match.group(1))
    try:
        if not head_video_source_download(key=key):
            return _missing()
        active = Video.objects.filter(
            pk=video.pk, tenant=request.tenant, source_type=Video.SourceType.UPLOADED,
        ).exclude(status=Video.Status.PENDING)
        if video.reused_from_id:
            active = active.filter(
                reused_from_id=source.pk, reused_from__tenant_id=request.tenant.pk,
                reused_from__reused_from__isnull=True, reused_from__file_key=key,
                reused_from__source_type=Video.SourceType.UPLOADED,
                reused_from__status=Video.Status.READY,
            )
        else:
            active = active.filter(reused_from__isnull=True, file_key=key)
        if not active.exists():
            return _missing()
        url = create_video_source_download_url(key=key, filename=filename, expires_in=DOWNLOAD_TTL_SECONDS)
    except VideoSourceStorageError:
        logger.warning("VIDEO_SOURCE_DOWNLOAD_STORAGE_FAILED video_id=%s", video.pk)
        return _unavailable()

    if record_audit(
        request,
        action="video.source_download",
        summary="영상 원본 다운로드 주소 발급",
        target_tenant=request.tenant,
        payload={"video_id": video.pk, **({"source_video_id": source.pk} if video.reused_from_id else {})},
    ) is None:
        return _unavailable()

    response = Response({"download_url": url, "filename": filename, "expires_in": DOWNLOAD_TTL_SECONDS})
    response["Cache-Control"] = "private, no-store"
    return response
