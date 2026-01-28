# PATH: apps/domains/clinic/views.py

from django.db.models import Count, Q
from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response

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


class SessionViewSet(viewsets.ModelViewSet):
    """
    ✅ 클리닉 세션 CRUD
    - 예약페이지(세션 리스트)에서 주로 사용
    - participant_count를 annotate하여 잔여좌석 계산에 활용 가능
    """

    serializer_class = ClinicSessionSerializer
    filter_backends = [DjangoFilterBackend, SearchFilter, OrderingFilter]
    filterset_class = SessionFilter
    search_fields = ["location"]
    ordering_fields = ["date", "start_time", "created_at"]
    ordering = ["-date", "-start_time"]

    def get_queryset(self):
        return (
            Session.objects.all()
            .annotate(participant_count=Count("participants"))
        )

    @action(detail=True, methods=["post"])
    def send_reminder(self, request, pk=None):
        """
        POST /clinic/sessions/{id}/send_reminder/
        - 세션 참가자들에게 리마인더 발송 (운영 기능)
        """
        session = self.get_object()
        send_clinic_reminder_for_students(session_id=session.id)
        return Response({"ok": True})

    # ==================================================
    # ✅ [추가] 운영 페이지 좌측 트리 전용 API
    # GET /clinic/sessions/tree/?year=YYYY&month=MM
    # ==================================================
    @action(detail=False, methods=["get"])
    def tree(self, request):
        year = request.query_params.get("year")
        month = request.query_params.get("month")

        if not year or not month:
            return Response(
                {"detail": "year and month are required"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        qs = (
            Session.objects
            .filter(date__year=year, date__month=month)
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


class ParticipantViewSet(viewsets.ModelViewSet):
    """
    ✅ 예약/출석/미이행/취소 운영의 핵심
    - 리스트: /clinic/participants/?session=...&status=...
    - 생성: /clinic/participants/  (예약 생성)
    - 상태 변경: /clinic/participants/{id}/set_status/
    """

    filter_backends = [DjangoFilterBackend, SearchFilter, OrderingFilter]
    filterset_class = ParticipantFilter
    search_fields = ["student__name", "session__location"]
    ordering_fields = ["created_at", "updated_at", "session__date"]
    ordering = ["-created_at"]

    def get_queryset(self):
        return (
            SessionParticipant.objects.select_related("student", "session").all()
        )

    def get_serializer_class(self):
        if self.action in ["create"]:
            return ClinicSessionParticipantCreateSerializer
        return ClinicSessionParticipantSerializer

    def create(self, request, *args, **kwargs):
        """
        ✅ 예약 등록
        payload 예:
        {
          "session": 12,
          "student": 345,
          "source": "auto",
          "enrollment_id": 1234,
          "clinic_reason": "both",
          "memo": "자동 클리닉 대상자(시험+과제)"
        }
        """
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        # unique_together 충돌을 친절히 처리
        session_id = serializer.validated_data["session"].id
        student_id = serializer.validated_data["student"].id

        exists = SessionParticipant.objects.filter(
            session_id=session_id,
            student_id=student_id,
        ).exists()
        if exists:
            return Response(
                {"detail": "이미 해당 세션에 예약된 학생입니다."},
                status=status.HTTP_409_CONFLICT,
            )

        obj = serializer.save()
        out = ClinicSessionParticipantSerializer(obj, context={"request": request}).data
        return Response(out, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["patch"])
    def set_status(self, request, pk=None):
        """
        PATCH /clinic/participants/{id}/set_status/
        {
          "status": "attended" | "no_show" | "cancelled" | "booked",
          "memo": "선택"
        }
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

        obj.status = next_status
        if memo is not None:
            obj.memo = memo

        obj.save(update_fields=["status", "memo", "updated_at"])
        out = ClinicSessionParticipantSerializer(obj, context={"request": request}).data
        return Response(out)

    @action(detail=False, methods=["get"])
    def by_session(self, request):
        """
        GET /clinic/participants/by_session/?session_id=12
        - 운영 페이지에서 세션별 참가자 빠르게 로드할 때 편함
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


class TestViewSet(viewsets.ModelViewSet):
    serializer_class = ClinicTestSerializer
    filter_backends = [DjangoFilterBackend, SearchFilter, OrderingFilter]
    search_fields = ["title"]
    ordering_fields = ["date", "created_at"]
    ordering = ["-date", "-created_at"]

    def get_queryset(self):
        return Test.objects.select_related("session").all()


class SubmissionViewSet(viewsets.ModelViewSet):
    serializer_class = ClinicSubmissionSerializer
    filter_backends = [DjangoFilterBackend, SearchFilter, OrderingFilter]
    filterset_class = SubmissionFilter
    search_fields = ["student__name", "test__title"]
    ordering_fields = ["created_at"]
    ordering = ["-created_at"]

    def get_queryset(self):
        return Submission.objects.select_related("student", "test", "test__session").all()
