# PATH: apps/domains/results/views/internal_wrong_note_worker_views.py
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from django.conf import settings
from django.db import transaction

from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status as drf_status
from rest_framework.permissions import AllowAny
from rest_framework.exceptions import PermissionDenied, NotFound, ValidationError

from apps.domains.results.models import WrongNotePDF
from apps.domains.results.services.wrong_note_service import (
    WrongNoteQuery,
    list_wrong_notes_for_enrollment,
)

# NOTE:
# - Celery 전면 폐지
# - WrongNote PDF 생성은 "외부 HTTP Worker"가 수행한다.
# - Worker 인증은 Bearer token (settings.INTERNAL_WORKER_TOKEN or settings.WORKER_TOKEN)
# - R2 업로드는 presigned PUT URL 기반
#
# Contract:
#   GET  /api/v1/internal/wrong-note-worker/next/
#   GET  /api/v1/internal/wrong-note-worker/{job_id}/data/
#   POST /api/v1/internal/wrong-note-worker/{job_id}/prepare-upload/
#   POST /api/v1/internal/wrong-note-worker/{job_id}/complete/
#   POST /api/v1/internal/wrong-note-worker/{job_id}/fail/


def _get_worker_token() -> str:
    return (
        getattr(settings, "INTERNAL_WORKER_TOKEN", None)
        or getattr(settings, "WORKER_TOKEN", None)
        or ""
    )


def _assert_worker_auth(request) -> None:
    token = _get_worker_token()
    if not token:
        raise PermissionDenied("Internal worker token is not configured.")

    auth = request.headers.get("Authorization", "") or ""
    if not auth.lower().startswith("bearer "):
        raise PermissionDenied("Missing bearer token.")

    incoming = auth.split(" ", 1)[-1].strip()
    if incoming != token:
        raise PermissionDenied("Invalid worker token.")


def _safe_int(v: Any, default: Optional[int] = None) -> Optional[int]:
    try:
        return int(v)
    except Exception:
        return default


def _safe_str(v: Any) -> str:
    return str(v) if v is not None else ""


@dataclass(frozen=True)
class _NextPayload:
    job_id: int
    enrollment_id: int
    lecture_id: Optional[int]
    exam_id: Optional[int]
    from_session_order: int


class WrongNoteWorkerNextView(APIView):
    """
    Worker pulls the next queued job.

    ✅ 상태값 단일화(모델 enum):
    - PENDING -> RUNNING -> DONE/FAILED

    Response:
    - 200 {"has_job": false}
    - 200 {"has_job": true, "job": {...}}
    """

    permission_classes = [AllowAny]

    @transaction.atomic
    def get(self, request):
        _assert_worker_auth(request)

        # 1) pick oldest queued job
        qs = (
            WrongNotePDF.objects
            .select_for_update(skip_locked=True)
            .filter(status__in=[WrongNotePDF.Status.PENDING])
            .order_by("id")
        )

        job = qs.first()
        if not job:
            return Response({"has_job": False})

        # 2) mark processing (lock)
        job.status = WrongNotePDF.Status.RUNNING
        job.error_message = ""
        job.save(update_fields=["status", "error_message", "updated_at"])

        payload = _NextPayload(
            job_id=int(job.id),
            enrollment_id=int(job.enrollment_id),
            lecture_id=_safe_int(getattr(job, "lecture_id", None), None),
            exam_id=_safe_int(getattr(job, "exam_id", None), None),
            from_session_order=int(getattr(job, "from_session_order", 2) or 2),
        )

        return Response({
            "has_job": True,
            "job": {
                "job_id": payload.job_id,
                "enrollment_id": payload.enrollment_id,
                "lecture_id": payload.lecture_id,
                "exam_id": payload.exam_id,
                "from_session_order": payload.from_session_order,
            },
        })


class WrongNoteWorkerJobDataView(APIView):
    """
    Worker fetches the data to render PDF (server-side SSOT).
    """

    permission_classes = [AllowAny]

    def get(self, request, job_id: int):
        _assert_worker_auth(request)

        job = WrongNotePDF.objects.filter(id=int(job_id)).first()
        if not job:
            raise NotFound("job not found")

        # DONE이면 데이터 재전송 대신 안정 응답
        if str(job.status) == WrongNotePDF.Status.DONE:
            return Response({"job_id": int(job.id), "status": WrongNotePDF.Status.DONE, "count": 0, "items": []})

        # RUNNING/PENDING/FAILED 상태에서 재조회는 허용(워커 재시도/복구)
        q = WrongNoteQuery(
            exam_id=_safe_int(getattr(job, "exam_id", None), None),
            lecture_id=_safe_int(getattr(job, "lecture_id", None), None),
            from_session_order=int(getattr(job, "from_session_order", 2) or 2),
            offset=0,
            limit=200,
        )

        total, items = list_wrong_notes_for_enrollment(
            enrollment_id=int(job.enrollment_id),
            q=q,
        )

        return Response({
            "job_id": int(job.id),
            "status": str(job.status),
            "filters": {
                "enrollment_id": int(job.enrollment_id),
                "lecture_id": _safe_int(getattr(job, "lecture_id", None), None),
                "exam_id": _safe_int(getattr(job, "exam_id", None), None),
                "from_session_order": int(getattr(job, "from_session_order", 2) or 2),
            },
            "count": int(total),
            "items": items,
        })


class WrongNoteWorkerPrepareUploadView(APIView):
    """
    Worker asks server for a presigned PUT URL to upload PDF.
    """

    permission_classes = [AllowAny]

    def post(self, request, job_id: int):
        _assert_worker_auth(request)

        job = WrongNotePDF.objects.filter(id=int(job_id)).first()
        if not job:
            raise NotFound("job not found")

        if str(job.status) != WrongNotePDF.Status.RUNNING:
            raise ValidationError({"detail": "job is not running", "code": "INVALID_STATE"})

        file_key = f"results/wrong-notes/wrong-note-{int(job.id)}.pdf"

        try:
            from libs.s3_client.presign import create_presigned_put_url
        except Exception as e:
            return Response(
                {"detail": f"presign helper not available: {e}"},
                status=drf_status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        upload_url = create_presigned_put_url(
            key=file_key,
            content_type="application/pdf",
            expires_in=60 * 10,
        )

        return Response({
            "job_id": int(job.id),
            "file_key": file_key,
            "upload_url": upload_url,
            "content_type": "application/pdf",
        })


class WrongNoteWorkerCompleteView(APIView):
    """
    Worker reports success after upload.

    POST body:
      { "file_path": "...", "meta": {...} }
    """

    permission_classes = [AllowAny]

    @transaction.atomic
    def post(self, request, job_id: int):
        _assert_worker_auth(request)

        job = WrongNotePDF.objects.select_for_update().filter(id=int(job_id)).first()
        if not job:
            raise NotFound("job not found")

        file_path = request.data.get("file_path") or request.data.get("file_key")
        if not file_path:
            raise ValidationError({"detail": "file_path is required", "code": "INVALID"})

        if str(job.status) != WrongNotePDF.Status.RUNNING:
            raise ValidationError({"detail": "job is not running", "code": "INVALID_STATE"})

        job.status = WrongNotePDF.Status.DONE
        job.file_path = str(file_path)
        job.error_message = ""

        meta = request.data.get("meta")
        if hasattr(job, "meta") and meta is not None:
            job.meta = meta
            job.save(update_fields=["status", "file_path", "error_message", "meta", "updated_at"])
        else:
            job.save(update_fields=["status", "file_path", "error_message", "updated_at"])

        return Response({"ok": True, "job_id": int(job.id), "status": WrongNotePDF.Status.DONE, "file_path": str(job.file_path)})


class WrongNoteWorkerFailView(APIView):
    """
    Worker reports failure.

    POST body:
      { "error_message": "..." }
    """

    permission_classes = [AllowAny]

    @transaction.atomic
    def post(self, request, job_id: int):
        _assert_worker_auth(request)

        job = WrongNotePDF.objects.select_for_update().filter(id=int(job_id)).first()
        if not job:
            raise NotFound("job not found")

        msg = _safe_str(request.data.get("error_message") or request.data.get("detail") or "unknown error")[:5000]

        # DONE이면 멱등 처리
        if str(job.status) == WrongNotePDF.Status.DONE:
            return Response({"ok": True, "job_id": int(job.id), "status": WrongNotePDF.Status.DONE})

        job.status = WrongNotePDF.Status.FAILED
        job.error_message = msg
        job.save(update_fields=["status", "error_message", "updated_at"])

        return Response({"ok": True, "job_id": int(job.id), "status": WrongNotePDF.Status.FAILED})
