from __future__ import annotations

import hashlib
import logging
import uuid

from django.db import transaction
from django.utils import timezone
from drf_spectacular.utils import OpenApiParameter, OpenApiTypes, extend_schema
from rest_framework import status
from rest_framework.exceptions import APIException, ValidationError
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.models import OpsAuditLog
from apps.core.permissions import TenantResolvedAndStaff
from apps.domains.teacher_app.models import TeacherOpsExecution
from apps.support.teacher_app.ops_assistant_dependencies import NotificationLog, ScheduledNotification

from .extraction import inherit_previous_intent, ocr_teacher_ops_image, parse_teacher_ops_text
from .serializers import TeacherOpsAnalyzeSerializer, TeacherOpsConfirmSerializer
from .service import (
    build_preview_row,
    active_lecture_options,
    execute_proposal,
    load_proposal,
    make_proposal,
    proposal_digest,
)


logger = logging.getLogger(__name__)


class TeacherOpsAssistantUnavailable(APIException):
    status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    default_detail = "사진 분석을 시작하지 못했습니다. 잠시 후 다시 시도해 주세요."
    default_code = "teacher_ops_assistant_unavailable"


def _audit_context(request) -> dict:
    forwarded = str(request.META.get("HTTP_X_FORWARDED_FOR", "") or "")
    ip = forwarded.split(",", maxsplit=1)[0].strip() if forwarded else str(request.META.get("REMOTE_ADDR", "") or "")
    return {
        "actor_user": request.user,
        "actor_username": str(getattr(request.user, "username", "") or ""),
        "target_tenant": request.tenant,
        "ip": ip[:64],
        "user_agent": str(request.META.get("HTTP_USER_AGENT", "") or "")[:255],
    }


class TeacherOpsAnalyzeView(APIView):
    permission_classes = [IsAuthenticated, TenantResolvedAndStaff]
    parser_classes = [MultiPartParser, FormParser]

    @extend_schema(request=TeacherOpsAnalyzeSerializer, responses={200: OpenApiTypes.OBJECT})
    def post(self, request):
        serializer = TeacherOpsAnalyzeSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        uploads = serializer.validated_data["images"]
        image_payloads = [uploaded.read() for uploaded in uploads]
        image_sha256 = hashlib.sha256(b"".join(hashlib.sha256(value).digest() for value in image_payloads)).hexdigest()
        try:
            previous_row = None
            previous_token = serializer.validated_data.get("previous_proposal_token")
            if previous_token:
                previous_payload = load_proposal(
                    token=previous_token,
                    tenant=request.tenant,
                    actor=request.user,
                )
                if previous_payload.get("rows"):
                    previous_row = previous_payload["rows"][-1]
            source_rows = []
            for image_bytes in image_payloads:
                extracted = parse_teacher_ops_text(
                    ocr_text=ocr_teacher_ops_image(image_bytes),
                    message=serializer.validated_data["message"],
                )
                extracted = inherit_previous_intent(
                    row=extracted,
                    message=serializer.validated_data["message"],
                    previous_row=previous_row,
                )
                source_rows.append(extracted.as_dict())
                previous_row = source_rows[-1]
            token, _ = make_proposal(
                tenant=request.tenant,
                actor=request.user,
                image_sha256=image_sha256,
                source_rows=source_rows,
            )
            preview_rows = [build_preview_row(tenant=request.tenant, source_row=row) for row in source_rows]
            OpsAuditLog.objects.create(
                **_audit_context(request),
                action="teacher_ops_assistant.analyze",
                summary="교사 업무 사진 분석",
                payload={
                    "image_sha256_prefix": image_sha256[:12],
                    "row_count": len(preview_rows),
                    "actions": [row["actions"] for row in preview_rows],
                    "can_confirm": all(row["can_confirm"] for row in preview_rows),
                },
            )
            return Response(
                {
                    "proposal_token": token,
                    "expires_in_seconds": 1800,
                    "rows": preview_rows,
                    "lecture_options": active_lecture_options(tenant=request.tenant),
                    "privacy": "원본 사진은 저장하지 않았습니다.",
                }
            )
        except ValidationError:
            raise
        except Exception as exc:
            logger.exception("teacher ops assistant analysis failed")
            OpsAuditLog.objects.create(
                **_audit_context(request),
                action="teacher_ops_assistant.analyze",
                summary="교사 업무 사진 분석 실패",
                payload={"image_sha256_prefix": image_sha256[:12]},
                result=OpsAuditLog.Result.FAILED,
                error=exc.__class__.__name__[:255],
            )
            raise TeacherOpsAssistantUnavailable() from exc


class TeacherOpsConfirmView(APIView):
    permission_classes = [IsAuthenticated, TenantResolvedAndStaff]

    @extend_schema(request=TeacherOpsConfirmSerializer, responses={200: OpenApiTypes.OBJECT})
    def post(self, request):
        serializer = TeacherOpsConfirmSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        payload = load_proposal(
            token=serializer.validated_data["proposal_token"],
            tenant=request.tenant,
            actor=request.user,
        )
        digest = proposal_digest(payload)
        try:
            nonce = uuid.UUID(str(payload["nonce"]))
        except (ValueError, TypeError, KeyError) as exc:
            raise ValidationError({"proposal_token": "확인 정보가 올바르지 않습니다."}) from exc

        with transaction.atomic():
            receipt, created = TeacherOpsExecution.objects.select_for_update().get_or_create(
                id=nonce,
                defaults={
                    "tenant": request.tenant,
                    "actor_user": request.user,
                    "proposal_digest": digest,
                    "status": TeacherOpsExecution.Status.PROCESSING,
                },
            )
            if not created:
                if (
                    receipt.tenant_id != request.tenant.id
                    or receipt.actor_user_id != request.user.id
                    or receipt.proposal_digest != digest
                ):
                    raise ValidationError("이미 사용된 확인 정보입니다.")
                if receipt.status == TeacherOpsExecution.Status.SUCCEEDED:
                    return Response({**receipt.result, "idempotent_replay": True})
                if receipt.status == TeacherOpsExecution.Status.PROCESSING:
                    return Response(
                        {
                            "execution_id": str(receipt.id),
                            "status": "processing",
                            "idempotent_replay": True,
                        },
                        status=status.HTTP_202_ACCEPTED,
                    )
                receipt.status = TeacherOpsExecution.Status.PROCESSING
                receipt.error_code = ""
                receipt.result = {}
                receipt.completed_at = None
                receipt.save(update_fields=["status", "error_code", "result", "completed_at", "updated_at"])

        try:
            result = execute_proposal(
                tenant=request.tenant,
                actor=request.user,
                payload=payload,
                overrides=serializer.validated_data["rows"],
            )
        except Exception as exc:
            TeacherOpsExecution.objects.filter(pk=nonce).update(
                status=TeacherOpsExecution.Status.FAILED,
                error_code=exc.__class__.__name__[:64],
                completed_at=timezone.now(),
            )
            OpsAuditLog.objects.create(
                **_audit_context(request),
                action="teacher_ops_assistant.execute",
                summary="교사 업무 요청 실행 실패",
                payload={"execution_id": str(nonce)},
                result=OpsAuditLog.Result.FAILED,
                error=exc.__class__.__name__[:255],
            )
            raise

        TeacherOpsExecution.objects.filter(pk=nonce).update(
            status=TeacherOpsExecution.Status.SUCCEEDED,
            result=result.payload,
            completed_at=timezone.now(),
        )
        OpsAuditLog.objects.create(
            **_audit_context(request),
            action="teacher_ops_assistant.execute",
            summary="교사 업무 요청 실행",
            payload={
                "execution_id": str(nonce),
                "student_ids": list(result.student_ids),
                "lecture_ids": list(result.lecture_ids),
                "video_ids": list(result.video_ids),
            },
        )
        return Response(result.payload)


class TeacherOpsExecutionStatusView(APIView):
    permission_classes = [IsAuthenticated, TenantResolvedAndStaff]

    @extend_schema(
        parameters=[
            OpenApiParameter(
                "execution_id",
                OpenApiTypes.UUID,
                OpenApiParameter.PATH,
            )
        ],
        responses={200: OpenApiTypes.OBJECT},
    )
    def get(self, request, execution_id):
        receipt = TeacherOpsExecution.objects.filter(
            id=execution_id,
            tenant=request.tenant,
            actor_user=request.user,
        ).first()
        if receipt is None:
            return Response({"detail": "실행 결과를 찾지 못했습니다."}, status=status.HTTP_404_NOT_FOUND)
        result = dict(receipt.result or {})
        rows = []
        for source in result.get("rows", []):
            row = dict(source)
            notice = dict(row.get("account_notice") or {})
            origin_id = str(notice.get("origin_id") or "")
            expected = int(notice.get("expected_recipients") or 0)
            if origin_id and expected:
                logs = list(
                    NotificationLog.objects.filter(
                        tenant=request.tenant,
                        origin_id=origin_id,
                        notification_type__in=[
                            "registration_approved_student",
                            "registration_approved_parent",
                        ],
                    ).order_by("id")
                )
                outboxes = ScheduledNotification.objects.filter(
                    tenant=request.tenant,
                    origin_id=origin_id,
                    trigger__in=[
                        "registration_approved_student",
                        "registration_approved_parent",
                    ],
                )
                failed = any(log.status in {"failed", "ambiguous"} or log.failure_reason for log in logs)
                accepted = len(logs) >= expected and all(
                    log.status == "sent"
                    and log.success
                    and log.message_mode == "alimtalk"
                    and bool(log.provider_message_id)
                    and not log.failure_reason
                    for log in logs[:expected]
                )
                if accepted:
                    notice["state"] = "provider_received"
                elif failed or outboxes.filter(status=ScheduledNotification.Status.FAILED).exists():
                    notice["state"] = "failed"
                elif outboxes.exists() or logs:
                    notice["state"] = "processing"
                else:
                    notice["state"] = "queued"
                notice["provider_evidence"] = {
                    "accepted_count": sum(
                        1
                        for log in logs
                        if log.status == "sent"
                        and log.success
                        and log.message_mode == "alimtalk"
                        and bool(log.provider_message_id)
                        and not log.failure_reason
                    ),
                    "expected_count": expected,
                    "mode": "alimtalk",
                    "kakao_read_confirmed": False,
                }
            row["account_notice"] = notice
            rows.append(row)
        result["rows"] = rows
        result["status"] = receipt.status
        return Response(result)
