from __future__ import annotations

from datetime import timedelta

from django.db.models import Q
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
from apps.core.models.user import user_display_username
from apps.domains.students.models import Student, StudentSupportSession
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
    q = serializers.CharField(required=False, allow_blank=True, max_length=80)


class StudentActivityItemSchema(serializers.Serializer):
    id = serializers.IntegerField()
    occurred_at = serializers.DateTimeField()
    category = serializers.ChoiceField(choices=ACTIVITY_CATEGORIES)
    label = serializers.CharField()
    actor_mode = serializers.ChoiceField(choices=("student", "support"))
    device_class = serializers.ChoiceField(choices=("mobile", "tablet", "desktop"))
    screen_id = serializers.CharField()
    actor_label = serializers.CharField()
    target_label = serializers.CharField()
    evidence_id = serializers.CharField()


class StudentActivityFeedSchema(serializers.Serializer):
    student = StudentSupportSummarySchema()
    results = StudentActivityItemSchema(many=True)
    count = serializers.IntegerField()
    total_count = serializers.IntegerField()
    has_more = serializers.BooleanField()
    days = serializers.ChoiceField(choices=(7, 30, 90))
    include_support = serializers.BooleanField()
    query = serializers.CharField()


class StudentActivityRecordSchema(serializers.Serializer):
    screen_id = serializers.CharField()
    device_class = serializers.ChoiceField(choices=("mobile", "tablet", "desktop"))


class StudentActivityAcceptedSchema(serializers.Serializer):
    accepted = serializers.BooleanField()


class StudentSupportSessionEndedSchema(serializers.Serializer):
    ended = serializers.BooleanField()


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

        expires_at = timezone.now() + self.lifetime
        support_session = StudentSupportSession.objects.create(
            tenant=request.tenant,
            student=student,
            operator=request.user,
            expires_at=expires_at,
        )
        token = AccessToken.for_user(student.user)
        token.set_exp(from_time=timezone.now(), lifetime=self.lifetime)
        token["tenant_id"] = request.tenant.id
        token["token_version"] = getattr(student.user, "token_version", 0) or 0
        token["mcp"] = False
        token["impersonated_by"] = request.user.id
        token["support_preview"] = True
        token["support_session_id"] = str(support_session.id)
        token["support_student_id"] = student.id

        record_audit(
            request,
            action="student_support_view.start",
            target_tenant=request.tenant,
            target_user=student.user,
            summary=f"학생 화면 대리보기 시작: {student.name}",
            payload={
                "student_id": student.id,
                "support_session_id": str(support_session.id),
                "expires_at": expires_at.isoformat(),
            },
        )

        return Response(
            {
                "access": str(token),
                "expires_at": expires_at.isoformat(),
                "session_id": str(support_session.id),
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
        query = str(request.query_params.get("q") or "").strip()
        if len(query) > 80:
            return Response({"detail": "검색어는 80자 이하로 입력해 주세요."}, status=400)

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
        if query:
            queryset = queryset.filter(
                Q(summary__icontains=query)
                | Q(payload__target_label__icontains=query)
                | Q(actor_user__name__icontains=query)
                | Q(actor_username__icontains=query)
            )

        total_count = queryset.count()
        rows = list(
            queryset.select_related("actor_user").order_by("-created_at", "-id")[:limit]
        )
        results = []
        for row in rows:
            payload = row.payload if isinstance(row.payload, dict) else {}
            actor_mode = "support" if payload.get("actor_mode") == "support" else "student"
            category_value = payload.get("category", "home")
            if category_value not in ACTIVITY_CATEGORIES:
                category_value = "home"
            device_class = payload.get("device_class", "desktop")
            if device_class not in {"mobile", "tablet", "desktop"}:
                device_class = "desktop"
            actor_label = "학생 본인"
            if actor_mode == "support":
                actor = row.actor_user
                actor_label = (
                    str(getattr(actor, "name", "") or "").strip()
                    or user_display_username(actor)
                    or "교직원"
                )
            results.append(
                {
                    "id": row.id,
                    "occurred_at": row.created_at.isoformat(),
                    "category": category_value,
                    "label": row.summary,
                    "actor_mode": actor_mode,
                    "device_class": device_class,
                    "screen_id": payload.get("screen_id", ""),
                    "actor_label": actor_label,
                    "target_label": str(payload.get("target_label") or ""),
                    "evidence_id": f"ACT-{row.id}",
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
                "total_count": total_count,
                "query": query,
            },
        )

        return Response(
            {
                "student": {"id": student.id, "name": student.name},
                "results": results,
                "count": len(results),
                "total_count": total_count,
                "has_more": total_count > len(results),
                "days": days,
                "include_support": include_support,
                "query": query,
            }
        )


def _end_support_session(*, request, support_session: StudentSupportSession, reason: str) -> bool:
    ended_at = timezone.now()
    updated = StudentSupportSession.objects.filter(
        pk=support_session.pk,
        ended_at__isnull=True,
    ).update(
        ended_at=ended_at,
        end_reason=reason,
        updated_at=ended_at,
    )
    if not updated:
        return False
    record_audit(
        request,
        actor_user=support_session.operator,
        action="student_support_view.end",
        target_tenant=support_session.tenant,
        target_user=support_session.student.user,
        summary=f"학생 화면 대리보기 종료: {support_session.student.name}",
        payload={
            "student_id": support_session.student_id,
            "support_session_id": str(support_session.id),
            "end_reason": reason,
        },
    )
    return True


class StudentSupportSessionEndView(APIView):
    """End the current support token immediately from the student popup."""

    permission_classes = [IsAuthenticated]

    @extend_schema(
        operation_id="students_support_session_end_current",
        request=None,
        responses={200: StudentSupportSessionEndedSchema},
    )
    def post(self, request):
        support_session = getattr(request, "student_support_session", None)
        if support_session is None:
            return Response({"detail": "학생 지원 세션이 아닙니다."}, status=403)
        ended = _end_support_session(
            request=request,
            support_session=support_session,
            reason=StudentSupportSession.EndReason.MANUAL,
        )
        return Response({"ended": ended or support_session.ended_at is not None})


class StudentSupportSessionRevokeView(APIView):
    """End an issued support session when its popup closes."""

    permission_classes = [IsAuthenticated, TenantResolvedAndStaff]

    @extend_schema(
        operation_id="students_support_session_end_by_staff",
        request=None,
        responses={200: StudentSupportSessionEndedSchema},
    )
    def post(self, request, student_id: int, session_id):
        support_session = (
            StudentSupportSession.objects.select_related("student__user", "tenant", "operator")
            .filter(
                pk=session_id,
                tenant=request.tenant,
                student_id=student_id,
                operator=request.user,
            )
            .first()
        )
        if support_session is None:
            return Response({"detail": "학생 지원 세션을 찾을 수 없습니다."}, status=404)
        ended = _end_support_session(
            request=request,
            support_session=support_session,
            reason=StudentSupportSession.EndReason.WINDOW_CLOSED,
        )
        return Response({"ended": ended or support_session.ended_at is not None})


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
