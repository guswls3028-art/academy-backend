# PATH: apps/domains/results/views/wrong_note_pdf_view.py
from __future__ import annotations

import logging
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
from apps.domains.results.services.wrong_note_pdf_service import (
    WrongNotePDFEmptyError,
    WrongNotePDFLimitError,
    delete_wrong_note_pdf_object,
    generate_and_store_wrong_note_pdf,
)
from apps.domains.results.throttles import WrongNotePDFCreateThrottle
from apps.support.results.wrong_note_pdf_dependencies import (
    exam_exists_for_tenant,
    exam_is_attached_to_lecture,
    get_wrong_note_pdf_enrollment,
    lecture_exists_for_tenant,
)

logger = logging.getLogger(__name__)


class WrongNotePDFCreateView(APIView):
    """
    오답노트 PDF 생성 요청.

    과거에는 PENDING job만 만들고 처리 주체가 없어 완료되지 않았다.
    현재는 요청 안에서 PDF를 생성해 R2에 저장하고 DONE/FAILED를 확정한다.

    응답:
    {
      "job_id": 1,
      "status": "PENDING",
      "status_url": "https://.../results/wrong-notes/pdf/1/"
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
        if not enrollment_id:
            return Response({"detail": "enrollment_id required"}, status=400)

        try:
            enrollment_id_i = int(enrollment_id)
            from_order = int(request.data.get("from_session_order", 2) or 2)
            if from_order < 1:
                raise ValueError
        except (TypeError, ValueError):
            raise ValidationError({"detail": "enrollment_id/from_session_order must be valid integers."})

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
            job = WrongNotePDF.objects.create(
                enrollment_id=enrollment_id_i,
                lecture_id=lecture_id_i or int(enrollment.lecture_id),
                exam_id=exam_id_i,
                from_session_order=from_order,
                status=WrongNotePDF.Status.PENDING,
            )

        status_path = reverse("wrong-note-pdf-status", kwargs={"job_id": job.id})
        status_url = request.build_absolute_uri(status_path)

        job.status = WrongNotePDF.Status.RUNNING
        job.save(update_fields=["status", "updated_at"])
        file_key = ""
        try:
            job.file_path = generate_and_store_wrong_note_pdf(
                job=job,
                enrollment=enrollment,
                tenant=request.tenant,
            )
            file_key = str(job.file_path)
            job.status = WrongNotePDF.Status.DONE
            job.error_message = ""
            job.save(
                update_fields=["status", "file_path", "error_message", "updated_at"]
            )
        except (WrongNotePDFEmptyError, WrongNotePDFLimitError) as exc:
            job.status = WrongNotePDF.Status.FAILED
            job.error_message = str(exc)
            job.save(update_fields=["status", "error_message", "updated_at"])
            return Response(
                {
                    "job_id": int(job.id),
                    "status": str(job.status),
                    "status_url": status_url,
                    "detail": str(exc),
                },
                status=status.HTTP_400_BAD_REQUEST,
            )
        except Exception:
            cleanup_succeeded = True
            if file_key:
                cleanup_succeeded = delete_wrong_note_pdf_object(file_key)
            logger.exception(
                "wrong-note PDF generation failed",
                extra={
                    "job_id": int(job.id),
                    "tenant_id": int(request.tenant.id),
                    "enrollment_id": enrollment_id_i,
                },
            )
            job.status = WrongNotePDF.Status.FAILED
            job.error_message = "PDF를 만들지 못했습니다. 잠시 후 다시 시도해 주세요."
            job.file_path = "" if cleanup_succeeded else file_key
            job.save(
                update_fields=[
                    "status",
                    "file_path",
                    "error_message",
                    "updated_at",
                ]
            )
            return Response(
                {
                    "job_id": int(job.id),
                    "status": str(job.status),
                    "status_url": status_url,
                    "detail": job.error_message,
                },
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        return Response({
            "job_id": int(job.id),
            "status": str(job.status),
            "status_url": status_url,
        }, status=status.HTTP_201_CREATED)
