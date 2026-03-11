# PATH: apps/domains/clinic/views.py

from django.db import IntegrityError
from django.db.models import Count, Q
from django.utils import timezone
from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.permissions import IsAuthenticated
from rest_framework import serializers

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
from .color_utils import get_effective_clinic_colors, get_daily_random_colors

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
        qs = (
            Session.objects
            .filter(tenant=tenant)
            .annotate(
                participant_count=Count("participants"),
                booked_count=Count(
                    "participants",
                    filter=Q(
                        participants__status__in=[
                            SessionParticipant.Status.BOOKED,
                            SessionParticipant.Status.PENDING,
                        ]
                    ),
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

        # ✅ 학생 조회 시: 본인 학년에 해당하는 세션만 노출 (시스템 레벨 통제)
        from apps.domains.student_app.permissions import get_request_student
        student = get_request_student(self.request)
        if student and student.grade:
            qs = qs.filter(Q(target_grade__isnull=True) | Q(target_grade=student.grade))

        return qs

    def perform_create(self, serializer):
        """
        ✅ created_by 자동 기록 (운영/감사 기준). 미인증 시 None
        ✅ 멀티테넌트: tenant 없으면 400 (RDS→Aurora 격리 준비)
        ✅ 동일 날짜/시간/장소 중복 생성 시 400 (UniqueConstraint)
        """
        tenant = getattr(self.request, "tenant", None)
        if not tenant:
            raise serializers.ValidationError(
                {"tenant": "테넌트 컨텍스트가 필요합니다. (호스트 또는 X-Tenant-Code 확인)"}
            )
        created_by = self.request.user if self.request.user.is_authenticated else None
        try:
            serializer.save(
                tenant=tenant,
                created_by=created_by,
            )
        except IntegrityError as e:
            err_str = str(e)
            if "uniq_clinic_session_per_tenant_time_loc" in err_str:
                raise serializers.ValidationError(
                    {"non_field_errors": "같은 날짜·시간·장소·학년의 클리닉이 이미 있습니다. 다른 시간, 장소, 또는 학년을 선택해주세요."}
                )
            raise

    def retrieve(self, request, *args, **kwargs):
        """단일 세션 조회 시 직렬화 오류 방지 (annotate 필드 누락 등)."""
        try:
            return super().retrieve(request, *args, **kwargs)
        except Exception as e:
            return Response(
                {"detail": str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
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
                    filter=Q(
                        participants__status__in=[
                            SessionParticipant.Status.BOOKED,
                            SessionParticipant.Status.PENDING,
                        ]
                    ),
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
                "title": s.title or "",
                "date": s.date,
                "start_time": s.start_time,
                "location": s.location,
                "target_grade": s.target_grade,
                "participant_count": s.participant_count,
                "booked_count": s.booked_count,
                "no_show_count": s.no_show_count,
                "max_participants": getattr(s, "max_participants", None),
            }
            for s in qs
        ]

        return Response(data)

    @action(detail=False, methods=["get"])
    def locations(self, request):
        """
        GET /clinic/sessions/locations/
        - 클리닉 생성 시 장소 불러오기용: 사용된 장소(룸) 목록
        """
        tenant = getattr(request, "tenant", None)
        if not tenant:
            return Response([])
        qs = (
            Session.objects
            .filter(tenant=tenant)
            .values_list("location", flat=True)
            .distinct()
            .order_by("location")
        )
        return Response([x for x in qs if x])


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
        - 선생: student, enrollment_id 직접 지정 가능, session 필수
        - 학생: student 자동 설정, source="student_request", status="pending"
        - 학생 신청 시: session 또는 (requested_date + requested_start_time) 사용 가능
        """
        tenant = getattr(request, "tenant", None)

        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        session = serializer.validated_data.get("session")
        requested_date = serializer.validated_data.get("requested_date")
        requested_start_time = serializer.validated_data.get("requested_start_time")
        student = serializer.validated_data.get("student")
        enrollment_id = serializer.validated_data.get("enrollment_id")
        source = serializer.validated_data.get("source")
        requested_status = serializer.validated_data.get("status")

        # 학생이 직접 신청하는 경우: student 자동 설정 + 반드시 기존 세션만 허용
        from apps.domains.student_app.permissions import get_request_student
        request_student = get_request_student(request)
        if request_student:
            # 학생이 신청하는 경우
            if student and student != request_student:
                return Response(
                    {"detail": "다른 학생의 예약을 신청할 수 없습니다."},
                    status=status.HTTP_403_FORBIDDEN,
                )
            student = request_student
            source = SessionParticipant.Source.STUDENT_REQUEST
            # 학생 신청은 기존에 강사가 만든 클리닉(세션)만 선택 가능
            if not session:
                return Response(
                    {"detail": "등록 가능한 클리닉을 선택해주세요. 해당 날짜에 열린 클리닉만 신청할 수 있습니다."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            # ✅ 학년 제한 검증: 학생 학년과 세션 대상 학년이 불일치하면 거부
            if session.target_grade and request_student.grade and session.target_grade != request_student.grade:
                return Response(
                    {"detail": "해당 클리닉은 다른 학년 대상입니다. 본인 학년의 클리닉만 신청할 수 있습니다."},
                    status=status.HTTP_403_FORBIDDEN,
                )
            # 정원 체크: booked + pending 인원이 max_participants 이상이면 신청 불가
            from django.db.models import Count
            from django.db.models import Q
            session_with_count = Session.objects.filter(pk=session.pk).annotate(
                booked_total=Count(
                    "participants",
                    filter=Q(
                        participants__status__in=[
                            SessionParticipant.Status.BOOKED,
                            SessionParticipant.Status.PENDING,
                        ]
                    ),
                ),
            ).first()
            if session_with_count and session.max_participants is not None:
                if session_with_count.booked_total >= session.max_participants:
                    return Response(
                        {"detail": "해당 클리닉은 정원이 마감되었습니다."},
                        status=status.HTTP_409_CONFLICT,
                    )
            # 학생 신청은 기본적으로 pending 상태
            if not requested_status or requested_status == SessionParticipant.Status.BOOKED:
                requested_status = SessionParticipant.Status.PENDING
            # 자동 승인 설정 시 바로 booked로 저장
            if getattr(tenant, "clinic_auto_approve_booking", False):
                requested_status = SessionParticipant.Status.BOOKED

        if not student:
            return Response(
                {"detail": "student가 필요합니다."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # 중복 체크: session이 있으면 session 기준, 없으면 requested_date/requested_start_time 기준
        if session:
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
        elif requested_date and requested_start_time:
            exists = SessionParticipant.objects.filter(
                tenant=tenant,
                session__isnull=True,
                requested_date=requested_date,
                requested_start_time=requested_start_time,
                student=student,
                status__in=[SessionParticipant.Status.PENDING, SessionParticipant.Status.BOOKED],
            ).exists()
            if exists:
                return Response(
                    {"detail": "이미 해당 시간에 예약 신청이 있습니다."},
                    status=status.HTTP_409_CONFLICT,
                )

        # participant_role 결정
        if source == SessionParticipant.Source.MANUAL:
            participant_role = "manual"
        elif source == SessionParticipant.Source.STUDENT_REQUEST:
            participant_role = "manual"  # 학생 신청도 manual로 분류
        else:
            participant_role = "target"

        # enrollment_id 자동 조회 (학생 신청 시)
        if not enrollment_id and request_student:
            from apps.domains.enrollment.models import Enrollment
            enrollment = Enrollment.objects.filter(
                student=request_student,
                tenant=tenant,
                status="ACTIVE"
            ).first()
            if enrollment:
                enrollment_id = enrollment.id

        obj = serializer.save(
            tenant=tenant,
            student=student,
            source=source,
            status=requested_status or SessionParticipant.Status.PENDING,
            enrollment_id=enrollment_id,
            participant_role=participant_role,
        )

        # enrollment_id가 있고 session이 있으면 ClinicLink 업데이트
        if enrollment_id and session:
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

        use_daily_random = getattr(tenant, "clinic_use_daily_random", False)
        auto_approve_booking = getattr(tenant, "clinic_auto_approve_booking", False)
        saved = getattr(tenant, "clinic_idcard_colors", None)
        if not saved or not isinstance(saved, list) or len(saved) < 3:
            saved = ["#ef4444", "#3b82f6", "#22c55e"]

        colors = get_effective_clinic_colors(tenant)

        return Response({
            "colors": colors[:3],
            "use_daily_random": use_daily_random,
            "auto_approve_booking": auto_approve_booking,
            "saved_colors": saved[:3],
        })

    def patch(self, request):
        tenant = getattr(request, "tenant", None)
        if not tenant:
            return Response({"detail": "tenant가 필요합니다."}, status=status.HTTP_400_BAD_REQUEST)

        # use_daily_random 업데이트
        if "use_daily_random" in request.data:
            tenant.clinic_use_daily_random = bool(request.data["use_daily_random"])
            tenant.save(update_fields=["clinic_use_daily_random"])

        if "auto_approve_booking" in request.data:
            tenant.clinic_auto_approve_booking = bool(request.data["auto_approve_booking"])
            tenant.save(update_fields=["clinic_auto_approve_booking"])

        colors = request.data.get("colors")
        if colors is not None:
            if not isinstance(colors, list) or len(colors) != 3:
                return Response(
                    {"detail": "colors는 3개의 색상 코드 배열이어야 합니다. (예: [\"#ef4444\", \"#3b82f6\", \"#22c55e\"])"},
                    status=status.HTTP_400_BAD_REQUEST,
                )
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

        use_daily_random = getattr(tenant, "clinic_use_daily_random", False)
        auto_approve_booking = getattr(tenant, "clinic_auto_approve_booking", False)
        saved = getattr(tenant, "clinic_idcard_colors", None) or ["#ef4444", "#3b82f6", "#22c55e"]
        return Response({
            "colors": get_effective_clinic_colors(tenant),
            "use_daily_random": use_daily_random,
            "auto_approve_booking": auto_approve_booking,
            "saved_colors": saved[:3],
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
