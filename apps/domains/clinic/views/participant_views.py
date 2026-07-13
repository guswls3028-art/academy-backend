# PATH: apps/domains/clinic/views/participant_views.py
import logging

from django.db import transaction
from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from rest_framework import serializers

from django_filters.rest_framework import DjangoFilterBackend
from rest_framework.filters import SearchFilter, OrderingFilter

from ..models import SessionParticipant
from ..serializers import (
    ClinicSessionParticipantSerializer,
    ClinicSessionParticipantCreateSerializer,
)
from ..filters import ParticipantFilter
from ..services import (
    change_participant_booking,
    change_participant_status,
    complete_participant,
    create_participant,
    uncomplete_participant,
)

from apps.core.permissions import TenantResolvedAndMember, TenantResolvedAndStaff
from apps.core.services.tenant_access import STAFF_ROLES, get_active_membership_role
from apps.support.clinic.session_dependencies import (
    get_student_for_clinic_request,
    send_clinic_event_notification,
)

logger = logging.getLogger(__name__)


def _get_request_student_for_clinic(request):
    tenant = getattr(request, "tenant", None)
    user = getattr(request, "user", None)
    role = get_active_membership_role(user, tenant)
    if role in STAFF_ROLES:
        return None
    if role not in ("student", "parent"):
        raise PermissionDenied("클리닉 이용 권한을 확인할 수 없습니다.")
    student = get_student_for_clinic_request(request)
    if student is None:
        raise PermissionDenied("선택한 학생 정보를 확인할 수 없습니다.")
    return student


def _send_clinic_notification(tenant, student, trigger, context=None):
    """클리닉 알림 — 학생+학부모 동시 발송 (AUTO_DEFAULT 정책)."""
    try:
        event_context = dict(context or {})
        event_context.setdefault("_source_domain", "clinic")
        event_context.setdefault("_source_use_case", f"clinic.{trigger}")
        for send_to in ("parent", "student"):
            send_clinic_event_notification(
                tenant=tenant,
                trigger=trigger,
                student=student,
                send_to=send_to,
                context=event_context,
            )
    except Exception:
        logger.exception("clinic notification failed: trigger=%s student=%s", trigger, getattr(student, "id", "?"))


# ============================================================
# Participant
# ============================================================
class ParticipantViewSet(viewsets.ModelViewSet):
    """
    ✅ 클리닉 예약 / 출석 / 미이행 / 취소 관리
    - 운영 핵심 엔드포인트
    """

    permission_classes = [IsAuthenticated]
    filter_backends = [DjangoFilterBackend, SearchFilter, OrderingFilter]
    filterset_class = ParticipantFilter
    search_fields = ["student__name", "session__location"]
    ordering_fields = ["created_at", "updated_at", "session__date"]
    ordering = ["-created_at"]

    def get_permissions(self):
        if self.action in ("update", "partial_update", "destroy", "complete", "uncomplete"):
            return [TenantResolvedAndStaff()]
        return [IsAuthenticated(), TenantResolvedAndMember()]

    def get_queryset(self):
        tenant = getattr(self.request, "tenant", None)
        if not tenant:
            raise serializers.ValidationError(
                {"tenant": "테넌트 컨텍스트가 필요합니다. (호스트 또는 X-Tenant-Code 확인)"}
            )
        qs = (
            SessionParticipant.objects
            .filter(tenant=tenant)
            .filter(student__deleted_at__isnull=True)  # 삭제된 학생 제외
            .select_related("student", "session", "status_changed_by", "enrollment__lecture")
        )

        # 학생이 조회하는 경우: 자신의 예약 신청만 조회
        student = _get_request_student_for_clinic(self.request)
        if student:
            qs = qs.filter(student=student)

        return qs

    def get_serializer_class(self):
        if self.action == "create":
            return ClinicSessionParticipantCreateSerializer
        return ClinicSessionParticipantSerializer

    def create(self, request, *args, **kwargs):
        """
        ✅ 예약 생성
        - 선생: student, enrollment_id 직접 지정 가능
        - 학생: student 자동 설정, source="student_request", status="pending"
        - session 또는 (requested_date + requested_start_time) 중 하나 사용
        """
        tenant = getattr(request, "tenant", None)
        if not tenant:
            raise serializers.ValidationError(
                {"tenant": "테넌트 컨텍스트가 필요합니다. (호스트 또는 X-Tenant-Code 확인)"}
            )

        request_student = _get_request_student_for_clinic(request)
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        result = create_participant(
            tenant=tenant,
            validated_data=serializer.validated_data,
            request_student=request_student,
        )
        obj = result.participant
        if result.notification:
            _t = tenant
            _event = result.notification
            transaction.on_commit(
                lambda: _send_clinic_notification(
                    _t,
                    _event.student,
                    _event.trigger,
                    _event.context,
                )
            )

        out = ClinicSessionParticipantSerializer(
            obj, context={"request": request}
        ).data
        return Response(out, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["patch"])
    def set_status(self, request, pk=None):
        """
        PATCH /clinic/participants/{id}/set_status/
        - 상태 변경 + audit 기록
        - 학생: 자신의 예약 신청(status="pending")만 취소 가능
        - 선생: 모든 상태 변경 가능
        """
        next_status = request.data.get("status")
        memo = request.data.get("memo")

        request_student = _get_request_student_for_clinic(request)
        if request_student is None and not TenantResolvedAndStaff().has_permission(request, self):
            raise PermissionDenied("클리닉 상태 변경은 스태프만 가능합니다.")

        result = change_participant_status(
            tenant=getattr(request, "tenant", None),
            participant_id=self.get_object().pk,
            next_status=next_status,
            actor=request.user,
            request_student=request_student,
            memo=memo,
        )
        obj = result.participant
        if result.notification:
            _t = getattr(request, "tenant", None)
            _event = result.notification
            transaction.on_commit(
                lambda: _send_clinic_notification(
                    _t,
                    _event.student,
                    _event.trigger,
                    _event.context,
                )
            )

        out = ClinicSessionParticipantSerializer(
            obj, context={"request": request}
        ).data
        return Response(out)

    @action(detail=True, methods=["post"])
    def complete(self, request, pk=None):
        """
        POST /clinic/participants/{id}/complete/
        자율학습 완료 처리 — 이력 기록 + 문자 트리거

        상태 전이: PENDING/BOOKED → ATTENDED (complete 전용 전이)
        이미 ATTENDED/NO_SHOW/CANCELLED/REJECTED인 경우 상태는 변경하지 않고
        completed_at만 기록한다.
        """
        result = complete_participant(
            tenant=getattr(request, "tenant", None),
            participant_id=self.get_object().pk,
            actor=request.user,
        )
        obj = result.participant
        if result.notification:
            _t = getattr(request, "tenant", None)
            _event = result.notification
            transaction.on_commit(
                lambda: _send_clinic_notification(
                    _t,
                    _event.student,
                    _event.trigger,
                    _event.context,
                )
            )

        out = ClinicSessionParticipantSerializer(
            obj, context={"request": request}
        ).data
        return Response(out)

    @action(detail=True, methods=["post"])
    def uncomplete(self, request, pk=None):
        """
        POST /clinic/participants/{id}/uncomplete/
        완료 취소
        """
        result = uncomplete_participant(
            tenant=getattr(request, "tenant", None),
            participant_id=self.get_object().pk,
        )
        obj = result.participant

        out = ClinicSessionParticipantSerializer(
            obj, context={"request": request}
        ).data
        return Response(out)

    @action(detail=True, methods=["post"], url_path="change-booking")
    def change_booking(self, request, pk=None):
        """
        POST /clinic/participants/{id}/change-booking/
        Atomic booking change: secure new session first, then cancel old.
        If new booking fails, old booking is preserved (transaction rollback).

        Request body: { "new_session_id": int, "memo": str (optional) }
        """
        new_session_id = request.data.get("new_session_id")
        memo = request.data.get("memo")

        tenant = getattr(request, "tenant", None)
        if not tenant:
            return Response(
                {"detail": "테넌트 컨텍스트가 필요합니다."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        result = change_participant_booking(
            tenant=tenant,
            participant_id=pk,
            new_session_id=new_session_id,
            request_student=_get_request_student_for_clinic(request),
            actor=request.user,
            memo=memo,
        )
        new_booking = result.participant
        if result.notification:
            _t = tenant
            _event = result.notification
            transaction.on_commit(
                lambda: _send_clinic_notification(
                    _t,
                    _event.student,
                    _event.trigger,
                    _event.context,
                )
            )

        out = ClinicSessionParticipantSerializer(
            new_booking, context={"request": request}
        ).data
        return Response(out, status=status.HTTP_200_OK)

    @action(detail=False, methods=["get"])
    def by_session(self, request):
        """
        GET /clinic/participants/by_session/?session_id=12
        """
        session_id = request.query_params.get("session_id")
        if not session_id:
            return Response(
                {"detail": "session_id is required"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        qs = self.get_queryset().filter(session_id=session_id)
        data = ClinicSessionParticipantSerializer(
            qs, many=True, context={"request": request}
        ).data
        return Response(data)
