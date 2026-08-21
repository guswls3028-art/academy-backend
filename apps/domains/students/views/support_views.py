from __future__ import annotations

import uuid
from datetime import timedelta

from django.utils import timezone
from drf_spectacular.utils import extend_schema
from rest_framework import serializers
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.tokens import AccessToken

from apps.api.common.query_params import parse_query_bool, parse_query_int
from apps.core.models import OpsAuditLog
from apps.core.permissions import TenantResolvedAndStaff
from apps.core.services.ops_audit import record_audit
from apps.domains.enrollment.selectors import active_homework_assignment_for_student
from apps.domains.students.models import Student
from apps.domains.students.services.activity import (
    record_student_screen_view,
    record_student_target_open,
)


ACTIVITY_CATEGORIES = (
    "login",
    "home",
    "homework",
    "video",
    "exam",
    "result",
    "attendance",
    "clinic",
    "notice",
    "profile",
    "fee",
    "guide",
)


class StudentSupportSummarySchema(serializers.Serializer):
    id = serializers.IntegerField()
    name = serializers.CharField()


class StudentSupportSessionSchema(serializers.Serializer):
    access = serializers.CharField()
    expires_at = serializers.DateTimeField()
    session_id = serializers.UUIDField()
    student = StudentSupportSummarySchema()


class StudentActivityQuerySchema(serializers.Serializer):
    days = serializers.ChoiceField(choices=(7, 30, 90), required=False, default=30)
    limit = serializers.IntegerField(min_value=1, max_value=100, required=False, default=50)
    category = serializers.ChoiceField(choices=ACTIVITY_CATEGORIES, required=False)
    include_support = serializers.BooleanField(required=False, default=False)


class StudentActivityItemSchema(serializers.Serializer):
    id = serializers.IntegerField()
    occurred_at = serializers.DateTimeField()
    category = serializers.ChoiceField(choices=ACTIVITY_CATEGORIES)
    label = serializers.CharField()
    actor_mode = serializers.ChoiceField(choices=("student", "support"))
    device_class = serializers.ChoiceField(choices=("mobile", "tablet", "desktop"))
    screen_id = serializers.CharField()


class StudentActivityFeedSchema(serializers.Serializer):
    student = StudentSupportSummarySchema()
    results = StudentActivityItemSchema(many=True)
    count = serializers.IntegerField()
    days = serializers.ChoiceField(choices=(7, 30, 90))
    include_support = serializers.BooleanField()


class StudentActivityRecordSchema(serializers.Serializer):
    screen_id = serializers.CharField()
    device_class = serializers.ChoiceField(choices=("mobile", "tablet", "desktop"))


class StudentActivityAcceptedSchema(serializers.Serializer):
    accepted = serializers.BooleanField()


class StudentHomeworkOpenSchema(serializers.Serializer):
    homework_id = serializers.IntegerField(min_value=1)


def _student_for_staff(request, student_id: int) -> Student | None:
    return Student.objects.select_related("user").filter(
        tenant=request.tenant,
        pk=student_id,
        deleted_at__isnull=True,
    ).first()


class StudentSupportSessionView(APIView):
    """Issue a short-lived, access-only student token for staff support."""

    permission_classes = [IsAuthenticated, TenantResolvedAndStaff]
    lifetime = timedelta(minutes=15)

    @extend_schema(
        operation_id="students_support_session_create",
        request=None,
        responses={200: StudentSupportSessionSchema},
    )
    def post(self, request, student_id: int):
        student = _student_for_staff(request, student_id)
        if student is None:
            return Response({"detail": "학생 정보를 찾을 수 없습니다."}, status=404)
        if not student.user.is_active:
            return Response({"detail": "비활성 학생 계정은 화면을 열 수 없습니다."}, status=409)

        session_id = uuid.uuid4()
        expires_at = timezone.now() + self.lifetime
        token = AccessToken.for_user(student.user)
        token.set_exp(from_time=timezone.now(), lifetime=self.lifetime)
        token["tenant_id"] = request.tenant.id
        token["token_version"] = getattr(student.user, "token_version", 0) or 0
        token["mcp"] = False
        token["impersonated_by"] = request.user.id
        token["support_preview"] = True
        token["support_session_id"] = str(session_id)
        token["support_student_id"] = student.id

        record_audit(
            request,
            action="student_support_view.start",
            target_tenant=request.tenant,
            target_user=student.user,
            summary=f"학생 화면 대리보기 시작: {student.name}",
            payload={
                "student_id": student.id,
                "support_session_id": str(session_id),
                "expires_at": expires_at.isoformat(),
            },
        )

        return Response(
            {
                "access": str(token),
                "expires_at": expires_at.isoformat(),
                "session_id": str(session_id),
                "student": {"id": student.id, "name": student.name},
            }
        )


class StudentActivityView(APIView):
    """Tenant-scoped student activity timeline for staff support."""

    permission_classes = [IsAuthenticated, TenantResolvedAndStaff]

    @extend_schema(
        operation_id="students_activity_list",
        parameters=[StudentActivityQuerySchema],
        responses={200: StudentActivityFeedSchema},
    )
    def get(self, request, student_id: int):
        student = _student_for_staff(request, student_id)
        if student is None:
            return Response({"detail": "학생 정보를 찾을 수 없습니다."}, status=404)

        days = parse_query_int(
            request.query_params,
            "days",
            default=30,
            min_value=1,
        )
        if days not in (7, 30, 90):
            return Response(
                {"detail": "조회 기간은 7일, 30일, 90일 중 하나여야 합니다."},
                status=400,
            )
        limit = min(
            parse_query_int(
                request.query_params,
                "limit",
                default=50,
                min_value=1,
            ),
            100,
        )
        category = str(request.query_params.get("category") or "").strip()
        if category and category not in ACTIVITY_CATEGORIES:
            return Response({"detail": "활동 종류가 올바르지 않습니다."}, status=400)
        include_support = parse_query_bool(
            request.query_params,
            "include_support",
            default=False,
        )

        queryset = OpsAuditLog.objects.filter(
            target_tenant=request.tenant,
            target_user=student.user,
            action__in=(
                "student_activity.login",
                "student_activity.screen_view",
                "student_activity.target_open",
            ),
            created_at__gte=timezone.now() - timedelta(days=days),
        )
        if not include_support:
            queryset = queryset.exclude(payload__actor_mode="support")
        if category:
            queryset = queryset.filter(payload__category=category)

        rows = list(queryset.order_by("-created_at", "-id")[:limit])
        results = []
        for row in rows:
            payload = row.payload if isinstance(row.payload, dict) else {}
            results.append(
                {
                    "id": row.id,
                    "occurred_at": row.created_at.isoformat(),
                    "category": payload.get("category", "home"),
                    "label": row.summary,
                    "actor_mode": payload.get("actor_mode", "student"),
                    "device_class": payload.get("device_class", "desktop"),
                    "screen_id": payload.get("screen_id", ""),
                }
            )

        record_audit(
            request,
            action="student_activity.view",
            target_tenant=request.tenant,
            target_user=student.user,
            summary=f"학생 활동 조회: {student.name}",
            payload={
                "student_id": student.id,
                "days": days,
                "category": category,
                "include_support": include_support,
                "result_count": len(results),
            },
        )

        return Response(
            {
                "student": {"id": student.id, "name": student.name},
                "results": results,
                "count": len(results),
                "days": days,
                "include_support": include_support,
            }
        )


class StudentActivityRecordView(APIView):
    """Record a successful student-app screen open with server receipt time."""

    permission_classes = [IsAuthenticated]

    @extend_schema(
        operation_id="students_activity_record",
        request=StudentActivityRecordSchema,
        responses={202: StudentActivityAcceptedSchema},
    )
    def post(self, request):
        screen_id = str((request.data or {}).get("screen_id") or "").strip()
        device_class = str((request.data or {}).get("device_class") or "").strip()
        if device_class not in {"mobile", "tablet", "desktop"}:
            return Response({"detail": "기기 종류가 올바르지 않습니다."}, status=400)
        if not record_student_screen_view(
            request=request,
            screen_id=screen_id,
            device_class=device_class,
        ):
            return Response({"detail": "기록할 수 없는 학생 활동입니다."}, status=403)
        return Response({"accepted": True}, status=202)


class StudentHomeworkOpenActivityView(APIView):
    """Record an exact homework open after validating current student access."""

    permission_classes = [IsAuthenticated]

    @extend_schema(
        operation_id="students_homework_open_activity_record",
        request=StudentHomeworkOpenSchema,
        responses={202: StudentActivityAcceptedSchema},
    )
    def post(self, request):
        try:
            homework_id = int((request.data or {}).get("homework_id"))
        except (TypeError, ValueError):
            return Response({"detail": "과제를 다시 확인해 주세요."}, status=400)
        student = Student.objects.filter(
            tenant=request.tenant,
            user=request.user,
            deleted_at__isnull=True,
        ).first()
        if student is None:
            return Response({"detail": "기록할 수 없는 학생 활동입니다."}, status=403)
        assignment = active_homework_assignment_for_student(
            tenant=request.tenant,
            student=student,
            homework_id=homework_id,
        )
        if assignment is None:
            return Response({"detail": "열람할 수 없는 과제입니다."}, status=404)
        record_student_target_open(
            request=request,
            student=student,
            screen_id="student.assignment.submit",
            target_type="homework",
            target_id=assignment.homework_id,
            target_label=assignment.homework.title,
        )
        return Response({"accepted": True}, status=202)
