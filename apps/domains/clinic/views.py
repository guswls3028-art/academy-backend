# PATH: apps/domains/clinic/views.py

from django.db.models import Count, Q
from django.utils import timezone
from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.permissions import IsAuthenticated

from django_filters.rest_framework import DjangoFilterBackend
from rest_framework.filters import SearchFilter, OrderingFilter

from .models import Session, SessionParticipant, Test, Submission
from .serializers import (
    ClinicSessionSerializer,
    ClinicSessionParticipantSerializer,
    ClinicSessionParticipantCreateSerializer,
    ClinicTestSerializer,
    ClinicSubmissionSerializer,
)
from .filters import SessionFilter, SubmissionFilter, ParticipantFilter

from apps.support.messaging.services import send_clinic_reminder_for_students
from apps.domains.progress.models import ClinicLink


# ============================================================
# Session
# ============================================================
class SessionViewSet(viewsets.ModelViewSet):
    """
    ✅ 클리닉 세션 CRUD
    - 예약 페이지 / 운영 페이지 공용
    - 모든 participant 통계는 BACKEND 단일진실
    """

    serializer_class = ClinicSessionSerializer
    filter_backends = [DjangoFilterBackend, SearchFilter, OrderingFilter]
    filterset_class = SessionFilter
    search_fields = ["location"]
    ordering_fields = ["date", "start_time", "created_at"]
    ordering = ["-date", "-start_time"]

    def get_queryset(self):
        tenant = getattr(self.request, "tenant", None)
        return (
            Session.objects
            .filter(tenant=tenant)
            .annotate(
                participant_count=Count("participants"),
                booked_count=Count(
                    "participants",
                    filter=Q(participants__status=SessionParticipant.Status.BOOKED),
                ),
                attended_count=Count(
                    "participants",
                    filter=Q(participants__status=SessionParticipant.Status.ATTENDED),
                ),
                no_show_count=Count(
                    "participants",
                    filter=Q(participants__status=SessionParticipant.Status.NO_SHOW),
                ),
                cancelled_count=Count(
                    "participants",
                    filter=Q(participants__status=SessionParticipant.Status.CANCELLED),
                ),
                auto_count=Count(
                    "participants",
                    filter=Q(participants__source=SessionParticipant.Source.AUTO),
                ),
                manual_count=Count(
                    "participants",
                    filter=Q(participants__source=SessionParticipant.Source.MANUAL),
                ),
            )
        )

    def perform_create(self, serializer):
        """
        ✅ created_by 자동 기록 (운영/감사 기준)
        """
        serializer.save(
            tenant=getattr(self.request, "tenant", None),
            created_by=self.request.user,
        )

    @action(detail=True, methods=["post"])
    def send_reminder(self, request, pk=None):
        """
        POST /clinic/sessions/{id}/send_reminder/
        - 세션 참가자 리마인더 발송
        """
        session = self.get_object()
        send_clinic_reminder_for_students(session_id=session.id)
        return Response({"ok": True})

    # ------------------------------------------------------------
    # 운영 페이지 좌측 트리 전용 API
    # ------------------------------------------------------------
    @action(detail=False, methods=["get"])
    def tree(self, request):
        """
        GET /clinic/sessions/tree/?year=YYYY&month=MM
        - 운영 페이지 좌측 트리 전용
        - serializer 우회 (UI 최적화 목적)
        """
        tenant = getattr(request, "tenant", None)

        year = request.query_params.get("year")
        month = request.query_params.get("month")

        if not year or not month:
            return Response(
                {"detail": "year and month are required"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        qs = (
            Session.objects
            .filter(
                tenant=tenant,
                date__year=year,
                date__month=month,
            )
            .annotate(
                participant_count=Count("participants"),
                booked_count=Count(
                    "participants",
                    filter=Q(participants__status=SessionParticipant.Status.BOOKED),
                ),
                no_show_count=Count(
                    "participants",
                    filter=Q(participants__status=SessionParticipant.Status.NO_SHOW),
                ),
            )
            .order_by("date", "start_time")
        )

        data = [
            {
                "id": s.id,
                "date": s.date,
                "start_time": s.start_time,
                "location": s.location,
                "participant_count": s.participant_count,
                "booked_count": s.booked_count,
                "no_show_count": s.no_show_count,
            }
            for s in qs
        ]

        return Response(data)


# ============================================================
# Participant
# ============================================================
class ParticipantViewSet(viewsets.ModelViewSet):
    """
    ✅ 클리닉 예약 / 출석 / 미이행 / 취소 관리
    - 운영 핵심 엔드포인트
    """

    filter_backends = [DjangoFilterBackend, SearchFilter, OrderingFilter]
    filterset_class = ParticipantFilter
    search_fields = ["student__name", "session__location"]
    ordering_fields = ["created_at", "updated_at", "session__date"]
    ordering = ["-created_at"]

    def get_queryset(self):
        tenant = getattr(self.request, "tenant", None)
        qs = (
            SessionParticipant.objects
            .filter(tenant=tenant)
            .select_related("student", "session", "status_changed_by")
        )
        
        # 학생이 조회하는 경우: 자신의 예약 신청만 조회
        from apps.domains.student_app.permissions import get_request_student
        student = get_request_student(self.request)
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
        """
        tenant = getattr(request, "tenant", None)

        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        session = serializer.validated_data["session"]
        student = serializer.validated_data["student"]
        enrollment_id = serializer.validated_data.get("enrollment_id")
        source = serializer.validated_data.get("source")

        exists = SessionParticipant.objects.filter(
            tenant=tenant,
            session=session,
            student=student,
        ).exists()
        if exists:
            return Response(
                {"detail": "이미 해당 세션에 예약된 학생입니다."},
                status=status.HTTP_409_CONFLICT,
            )

        participant_role = (
            "manual"
            if source == SessionParticipant.Source.MANUAL
            else "target"
        )

        obj = serializer.save(
            tenant=tenant,
            participant_role=participant_role,
        )

        if enrollment_id:
            ClinicLink.objects.filter(
                session=session,
                enrollment_id=enrollment_id,
                is_auto=True,
                resolved_at__isnull=True,
            ).update(resolved_at=timezone.now())

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
        obj = self.get_object()

        next_status = request.data.get("status")
        memo = request.data.get("memo")

        allowed = {c[0] for c in SessionParticipant.Status.choices}
        if next_status not in allowed:
            return Response(
                {"detail": f"Invalid status: {next_status}"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # 학생 권한 체크: 자신의 예약 신청만 취소 가능
        from apps.domains.student_app.permissions import get_request_student
        request_student = get_request_student(request)
        if request_student:
            if obj.student != request_student:
                return Response(
                    {"detail": "다른 학생의 예약을 수정할 수 없습니다."},
                    status=status.HTTP_403_FORBIDDEN,
                )
            # 학생은 pending 상태만 cancelled로 변경 가능
            if obj.status != SessionParticipant.Status.PENDING:
                return Response(
                    {"detail": "승인 대기 중인 예약만 취소할 수 있습니다."},
                    status=status.HTTP_403_FORBIDDEN,
                )
            if next_status != SessionParticipant.Status.CANCELLED:
                return Response(
                    {"detail": "학생은 예약 취소만 가능합니다."},
                    status=status.HTTP_403_FORBIDDEN,
                )

        obj.status = next_status
        obj.status_changed_at = timezone.now()
        obj.status_changed_by = request.user

        if memo is not None:
            obj.memo = memo

        obj.save(
            update_fields=[
                "status",
                "memo",
                "status_changed_at",
                "status_changed_by",
                "updated_at",
            ]
        )

        if next_status in {
            SessionParticipant.Status.NO_SHOW,
            SessionParticipant.Status.CANCELLED,
        } and obj.enrollment_id:
            ClinicLink.objects.filter(
                session=obj.session,
                enrollment_id=obj.enrollment_id,
                is_auto=True,
            ).update(resolved_at=None)

        out = ClinicSessionParticipantSerializer(
            obj, context={"request": request}
        ).data
        return Response(out)

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
        data = ClinicSessionParticipantSerializer(qs, many=True).data
        return Response(data)


# ============================================================
# Test
# ============================================================
class TestViewSet(viewsets.ModelViewSet):
    serializer_class = ClinicTestSerializer
    filter_backends = [DjangoFilterBackend, SearchFilter, OrderingFilter]
    search_fields = ["title"]
    ordering_fields = ["date", "created_at"]
    ordering = ["-date", "-created_at"]

    def get_queryset(self):
        tenant = getattr(self.request, "tenant", None)
        return Test.objects.filter(tenant=tenant).select_related("session")

    def perform_create(self, serializer):
        serializer.save(tenant=getattr(self.request, "tenant", None))


# ============================================================
# Clinic Settings (패스카드 색상 등)
# ============================================================
class ClinicSettingsView(APIView):
    """
    GET/PATCH /clinic/settings/
    클리닉 설정 (패스카드 배경 색상 등)
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        tenant = getattr(request, "tenant", None)
        if not tenant:
            return Response({"detail": "tenant가 필요합니다."}, status=status.HTTP_400_BAD_REQUEST)
        
        colors = getattr(tenant, "clinic_idcard_colors", None)
        if not colors or not isinstance(colors, list) or len(colors) < 3:
            # 기본값: 빨강, 파랑, 초록
            colors = ["#ef4444", "#3b82f6", "#22c55e"]
        
        return Response({
            "colors": colors[:3],  # 최대 3개만
        })

    def patch(self, request):
        tenant = getattr(request, "tenant", None)
        if not tenant:
            return Response({"detail": "tenant가 필요합니다."}, status=status.HTTP_400_BAD_REQUEST)
        
        colors = request.data.get("colors")
        if not isinstance(colors, list) or len(colors) != 3:
            return Response(
                {"detail": "colors는 3개의 색상 코드 배열이어야 합니다. (예: [\"#ef4444\", \"#3b82f6\", \"#22c55e\"])"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        
        # 색상 코드 검증 (간단한 hex 검증)
        import re
        hex_pattern = re.compile(r"^#[0-9A-Fa-f]{6}$")
        for c in colors:
            if not isinstance(c, str) or not hex_pattern.match(c):
                return Response(
                    {"detail": f"잘못된 색상 코드: {c}. #RRGGBB 형식이어야 합니다."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
        
        tenant.clinic_idcard_colors = colors[:3]
        tenant.save(update_fields=["clinic_idcard_colors"])
        
        return Response({
            "colors": tenant.clinic_idcard_colors,
        })


# ============================================================
# Submission
# ============================================================
class SubmissionViewSet(viewsets.ModelViewSet):
    serializer_class = ClinicSubmissionSerializer
    filter_backends = [DjangoFilterBackend, SearchFilter, OrderingFilter]
    filterset_class = SubmissionFilter
    search_fields = ["student__name", "test__title"]
    ordering_fields = ["created_at"]
    ordering = ["-created_at"]

    def get_queryset(self):
        tenant = getattr(self.request, "tenant", None)
        return (
            Submission.objects
            .filter(tenant=tenant)
            .select_related("student", "test", "test__session")
        )

    def perform_create(self, serializer):
        serializer.save(tenant=getattr(self.request, "tenant", None))
