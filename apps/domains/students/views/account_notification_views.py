# PATH: apps/domains/students/views/account_notification_views.py

from drf_spectacular.utils import extend_schema
from rest_framework import serializers
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.api.common.query_params import parse_query_int
from apps.api.common.throttles import StaffPasswordResetThrottle
from apps.core.permissions import TenantResolvedAndStaff
from apps.domains.students.models import Student
from apps.domains.students.services.account_recovery import (
    AccountRecoveryDeliveryError,
    AccountRecoveryValidationError,
    list_recent_account_notification_logs,
    resolve_staff_account_for_student,
    send_username_recovery,
)


class StudentAccountNotificationQuerySchema(serializers.Serializer):
    limit = serializers.IntegerField(min_value=1, max_value=20, required=False, default=5)


class StudentAccountNotificationItemSchema(serializers.Serializer):
    id = serializers.IntegerField()
    sent_at = serializers.DateTimeField(allow_null=True)
    success = serializers.BooleanField()
    status = serializers.CharField()
    notification_type = serializers.CharField()
    recipient_summary = serializers.CharField()
    provider_message_id = serializers.CharField()
    failure_reason = serializers.CharField()
    target_id = serializers.CharField()
    target_name = serializers.CharField()


class StudentAccountNotificationListSchema(serializers.Serializer):
    results = StudentAccountNotificationItemSchema(many=True)


class StudentAccountGuidanceRequestSchema(serializers.Serializer):
    target = serializers.ChoiceField(choices=("student", "parent"))


class StudentAccountGuidanceResponseSchema(serializers.Serializer):
    message = serializers.CharField()


class StudentAccountNotificationLogView(APIView):
    """Student-detail account Alimtalk history and username guidance."""

    permission_classes = [IsAuthenticated, TenantResolvedAndStaff]

    def get_throttles(self):
        if getattr(self, "request", None) and self.request.method == "POST":
            return [StaffPasswordResetThrottle()]
        return []

    @staticmethod
    def _student(request, student_id: int):
        return Student.objects.select_related("user", "tenant").filter(
            tenant=request.tenant,
            pk=student_id,
            deleted_at__isnull=True,
        ).first()

    @extend_schema(
        operation_id="students_account_notifications_list",
        parameters=[StudentAccountNotificationQuerySchema],
        responses={200: StudentAccountNotificationListSchema},
    )
    def get(self, request, student_id: int):
        student = self._student(request, student_id)
        if not student:
            return Response({"detail": "학생 정보를 찾을 수 없습니다."}, status=404)

        limit = parse_query_int(
            request.query_params,
            "limit",
            default=5,
            min_value=1,
        )

        return Response({
            "results": list_recent_account_notification_logs(student, limit=limit),
        })

    @extend_schema(
        operation_id="students_account_guidance_create",
        request=StudentAccountGuidanceRequestSchema,
        responses={200: StudentAccountGuidanceResponseSchema},
    )
    def post(self, request, student_id: int):
        """Send username guidance without changing the current password."""

        student = self._student(request, student_id)
        if not student:
            return Response({"detail": "학생 정보를 찾을 수 없습니다."}, status=404)

        payload = StudentAccountGuidanceRequestSchema(data=request.data)
        payload.is_valid(raise_exception=True)
        try:
            account = resolve_staff_account_for_student(
                student=student,
                target=payload.validated_data["target"],
            )
            send_username_recovery(account)
        except AccountRecoveryValidationError as exc:
            return Response({"detail": exc.detail}, status=400)
        except AccountRecoveryDeliveryError as exc:
            return Response({"detail": exc.detail}, status=503)

        target_label = "학생" if account.target == "student" else "학부모"
        return Response({
            "message": f"{target_label} 아이디 안내 알림톡을 발송했습니다. 비밀번호는 변경되지 않았습니다.",
        })
