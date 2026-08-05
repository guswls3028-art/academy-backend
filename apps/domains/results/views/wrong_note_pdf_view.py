# PATH: apps/domains/results/views/wrong_note_pdf_view.py
from __future__ import annotations

import logging
import re
from datetime import timedelta
from typing import Any

from django.db import transaction
from django.urls import reverse
from django.utils import timezone

from rest_framework import status
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from rest_framework.exceptions import PermissionDenied, ValidationError

from apps.core.models import Tenant
from apps.core.permissions import TenantResolvedAndStaff
from apps.domains.results.models.wrong_note_pdf import WrongNotePDF
from apps.domains.results.services.wrong_note_service import (
    WrongNoteQuery,
    build_wrong_note_source_fingerprint,
    list_wrong_notes_for_enrollment,
)
from apps.domains.results.services.selected_wrong_note_service import (
    WrongNoteSourceSelectionError,
    list_wrong_notes_for_selection,
    normalize_wrong_note_source_selection,
)
from apps.domains.results.throttles import WrongNotePDFCreateThrottle
from apps.support.results.wrong_note_pdf_dependencies import (
    exam_exists_for_tenant,
    exam_is_attached_to_lecture,
    create_wrong_note_pdf_ai_job,
    get_wrong_note_pdf_enrollment,
    lecture_exists_for_tenant,
    mark_wrong_note_pdf_ai_job_failed,
    publish_wrong_note_pdf_ai_job,
)

logger = logging.getLogger(__name__)


class WrongNotePDFCreateView(APIView):
    """
    오답노트 PDF/HWPX 생성 요청.

    API 요청에서는 job을 내구성 있게 기록하고 tools worker 큐에 발행한다.
    실제 문서 생성과 R2 저장은 ALB 요청 제한 밖에서 비동기로 처리한다.

    응답:
    {
      "job_id": 1,
      "status": "PENDING",
      "status_url": "https://.../results/wrong-notes/documents/1/",
      "output_format": "pdf"
    }
    """

    permission_classes = [IsAuthenticated, TenantResolvedAndStaff]
    throttle_classes = [WrongNotePDFCreateThrottle]

    def _get_allowed_enrollment(self, request, enrollment_id: int) -> Any:
        enrollment = get_wrong_note_pdf_enrollment(
            enrollment_id=int(enrollment_id),
            tenant=request.tenant,
        )
        if not enrollment:
            raise PermissionDenied("You cannot create PDF for this enrollment_id.")
        return enrollment

    def _validate_scope_ids(self, request, enrollment: Any) -> tuple[int | None, int | None]:
        lecture_id = request.data.get("lecture_id")
        exam_id = request.data.get("exam_id")

        lecture_id_i = int(lecture_id) if lecture_id else None
        if lecture_id_i is not None:
            if lecture_id_i != enrollment.lecture_id:
                raise ValidationError({"lecture_id": "수강 등록의 강의와 일치하지 않습니다."})
            if not lecture_exists_for_tenant(lecture_id=lecture_id_i, tenant=request.tenant):
                raise ValidationError({"lecture_id": "해당 강의를 찾을 수 없습니다."})

        exam_id_i = int(exam_id) if exam_id else None
        if exam_id_i is not None:
            if not exam_exists_for_tenant(
                exam_id=exam_id_i,
                tenant=request.tenant,
            ):
                raise ValidationError({"exam_id": "해당 시험을 찾을 수 없습니다."})
            if not exam_is_attached_to_lecture(
                exam_id=exam_id_i,
                lecture_id=enrollment.lecture_id,
            ):
                raise ValidationError({"exam_id": "수강 등록의 강의에 연결된 시험만 선택할 수 있습니다."})

        return lecture_id_i, exam_id_i

    def post(self, request):
        enrollment_id = request.data.get("enrollment_id")
        raw_source_selection = request.data.get("source_selection")
        selected_mode = raw_source_selection is not None
        if not selected_mode and not enrollment_id:
            return Response({"detail": "enrollment_id required"}, status=400)
        output_format = str(request.data.get("output_format") or "pdf").lower()
        if output_format not in WrongNotePDF.OutputFormat.values:
            raise ValidationError(
                {"output_format": "PDF 또는 HWPX만 선택할 수 있습니다."}
            )
        requested_fingerprint = str(
            request.data.get("source_fingerprint") or ""
        ).strip().lower()
        if requested_fingerprint and not re.fullmatch(
            r"[0-9a-f]{64}", requested_fingerprint
        ):
            raise ValidationError(
                {"source_fingerprint": "오답 목록을 새로 불러온 뒤 다시 시도해 주세요."}
            )

        normalized_selection: list[dict[str, int | str]] = []
        selected_student_id: int | None = None
        if selected_mode:
            try:
                selected_student_id = int(request.data.get("student_id"))
                if selected_student_id < 1:
                    raise ValueError
                normalized_selection = normalize_wrong_note_source_selection(
                    tenant_id=int(request.tenant.id),
                    student_id=selected_student_id,
                    raw_sources=raw_source_selection,
                )
            except (TypeError, ValueError, WrongNoteSourceSelectionError) as exc:
                detail = str(exc) if isinstance(exc, WrongNoteSourceSelectionError) else "학생과 자료를 다시 선택해 주세요."
                raise ValidationError({"source_selection": detail}) from exc
            enrollment_id_i = int(normalized_selection[0]["enrollment_id"])
            from_order = 1
            to_order = None
            lecture_id_i = None
            exam_id_i = None
            enrollment = self._get_allowed_enrollment(request, enrollment_id_i)
        else:
            try:
                enrollment_id_i = int(enrollment_id)
                from_order = int(request.data.get("from_session_order", 2) or 2)
                requested_to_order = request.data.get("to_session_order")
                to_order = (
                    int(requested_to_order)
                    if requested_to_order not in (None, "")
                    else None
                )
                if from_order < 1 or (to_order is not None and to_order < from_order):
                    raise ValueError
            except (TypeError, ValueError):
                raise ValidationError(
                    {"detail": "시작 회차와 종료 회차를 다시 확인해 주세요."}
                )

            enrollment = self._get_allowed_enrollment(request, enrollment_id_i)
            try:
                lecture_id_i, exam_id_i = self._validate_scope_ids(request, enrollment)
            except ValueError:
                raise ValidationError({"detail": "lecture_id/exam_id must be valid integers."})

        with transaction.atomic():
            Tenant.objects.select_for_update().get(pk=request.tenant.pk)
            locked_enrollment = get_wrong_note_pdf_enrollment(
                enrollment_id=enrollment_id_i,
                tenant=request.tenant,
                for_update=True,
            )
            if locked_enrollment is None:
                raise PermissionDenied(
                    "You cannot create PDF for this enrollment_id."
                )
            stale_before = timezone.now() - timedelta(minutes=5)
            WrongNotePDF.objects.filter(
                enrollment__tenant=request.tenant,
                status__in=[
                    WrongNotePDF.Status.PENDING,
                    WrongNotePDF.Status.RUNNING,
                ],
                updated_at__lt=stale_before,
            ).update(
                status=WrongNotePDF.Status.FAILED,
                error_message="생성이 중단되어 다시 시도할 수 있습니다.",
                updated_at=timezone.now(),
            )
            if WrongNotePDF.objects.filter(
                enrollment__tenant=request.tenant,
                status__in=[
                    WrongNotePDF.Status.PENDING,
                    WrongNotePDF.Status.RUNNING,
                ],
            ).exists():
                return Response(
                    {"detail": "학원에서 다른 오답노트를 만들고 있습니다. 잠시 후 다시 시도해 주세요."},
                    status=status.HTTP_409_CONFLICT,
                )
            if selected_mode:
                try:
                    total, items, normalized_selection = list_wrong_notes_for_selection(
                        tenant_id=int(request.tenant.id),
                        student_id=int(selected_student_id),
                        source_selection=normalized_selection,
                    )
                except WrongNoteSourceSelectionError as exc:
                    raise ValidationError({"source_selection": str(exc)}) from exc
            else:
                total, items = list_wrong_notes_for_enrollment(
                    enrollment_id=enrollment_id_i,
                    q=WrongNoteQuery(
                        exam_id=exam_id_i,
                        lecture_id=lecture_id_i or int(enrollment.lecture_id),
                        from_session_order=from_order,
                        to_session_order=to_order,
                        offset=0,
                        limit=200,
                    ),
                )
            source_fingerprint = build_wrong_note_source_fingerprint(
                total=total,
                items=items,
            )
            if (
                requested_fingerprint
                and requested_fingerprint != source_fingerprint
            ):
                return Response(
                    {
                        "detail": "채점 또는 문항이 변경되었습니다. 최신 오답을 다시 불러와 주세요.",
                        "source_fingerprint": source_fingerprint,
                    },
                    status=status.HTTP_409_CONFLICT,
                )
            job = WrongNotePDF.objects.create(
                enrollment_id=enrollment_id_i,
                lecture_id=lecture_id_i or int(enrollment.lecture_id),
                exam_id=exam_id_i,
                from_session_order=from_order,
                to_session_order=to_order,
                status=WrongNotePDF.Status.PENDING,
                output_format=output_format,
                source_fingerprint=source_fingerprint,
                source_selection=normalized_selection,
            )
            ai_job = create_wrong_note_pdf_ai_job(
                pdf_job_id=int(job.id),
                tenant_id=int(request.tenant.id),
                source_fingerprint=source_fingerprint,
            )

        status_path = reverse("wrong-note-document-status", kwargs={"job_id": job.id})
        status_url = request.build_absolute_uri(status_path)

        try:
            enqueued = publish_wrong_note_pdf_ai_job(ai_job)
        except Exception:
            logger.exception(
                "wrong-note PDF queue publish failed",
                extra={
                    "job_id": int(job.id),
                    "tenant_id": int(request.tenant.id),
                    "enrollment_id": enrollment_id_i,
                },
            )
            enqueued = False

        if not enqueued:
            error_message = "오답노트 생성 작업을 등록하지 못했습니다. 잠시 후 다시 시도해 주세요."
            with transaction.atomic():
                WrongNotePDF.objects.filter(id=job.id).update(
                    status=WrongNotePDF.Status.FAILED,
                    error_message=error_message,
                    updated_at=timezone.now(),
                )
                mark_wrong_note_pdf_ai_job_failed(
                    ai_job_id=int(ai_job.id),
                    error_message=error_message,
                )
            return Response(
                {
                    "job_id": int(job.id),
                    "status": WrongNotePDF.Status.FAILED,
                    "status_url": status_url,
                    "output_format": output_format,
                    "detail": error_message,
                },
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        return Response(
            {
                "job_id": int(job.id),
                "status": WrongNotePDF.Status.PENDING,
                "status_url": status_url,
                "output_format": output_format,
                "source_fingerprint": source_fingerprint,
            },
            status=status.HTTP_202_ACCEPTED,
        )
