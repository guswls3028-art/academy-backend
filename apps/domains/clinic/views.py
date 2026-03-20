# PATH: apps/domains/clinic/views.py
import logging

from django.db import IntegrityError, transaction

logger = logging.getLogger(__name__)
from django.db.models import Count, Q, Exists, OuterRef
from django.utils import timezone
from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.permissions import IsAuthenticated
from apps.core.permissions import TenantResolvedAndStaff
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

from apps.core.permissions import TenantResolvedAndStaff
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

    permission_classes = [IsAuthenticated]
    serializer_class = ClinicSessionSerializer

    def get_permissions(self):
        if self.action in ("create", "update", "partial_update", "destroy"):
            return [IsAuthenticated(), TenantResolvedAndStaff()]
        return [IsAuthenticated()]
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
            .prefetch_related("target_lectures")
            .annotate(
                participant_count=Count("participants", distinct=True),
                booked_count=Count(
                    "participants",
                    filter=Q(
                        participants__status__in=[
                            SessionParticipant.Status.BOOKED,
                            SessionParticipant.Status.PENDING,
                        ]
                    ),
                    distinct=True,
                ),
                attended_count=Count(
                    "participants",
                    filter=Q(participants__status=SessionParticipant.Status.ATTENDED),
                    distinct=True,
                ),
                no_show_count=Count(
                    "participants",
                    filter=Q(participants__status=SessionParticipant.Status.NO_SHOW),
                    distinct=True,
                ),
                cancelled_count=Count(
                    "participants",
                    filter=Q(participants__status=SessionParticipant.Status.CANCELLED),
                    distinct=True,
                ),
                auto_count=Count(
                    "participants",
                    filter=Q(participants__source=SessionParticipant.Source.AUTO),
                    distinct=True,
                ),
                manual_count=Count(
                    "participants",
                    filter=Q(participants__source=SessionParticipant.Source.MANUAL),
                    distinct=True,
                ),
            )
        )

        # 학생 조회 시: 본인 조건에 맞는 세션만 노출
        from apps.domains.student_app.permissions import get_request_student
        student = get_request_student(self.request)
        if student:
            # 학년 필터: 학년 미설정 학생은 제한 없는 세션만
            if student.grade:
                qs = qs.filter(Q(target_grade__isnull=True) | Q(target_grade=student.grade))
            else:
                qs = qs.filter(target_grade__isnull=True)
            # 학교유형 필터: 미설정 시 제한 없는 세션만
            _no_school_restrict = Q(target_school_type__isnull=True) | Q(target_school_type="")
            if student.school_type:
                qs = qs.filter(_no_school_restrict | Q(target_school_type=student.school_type))
            else:
                qs = qs.filter(_no_school_restrict)
            # 강의 필터: 수강 중인 강의가 대상에 포함되거나 대상 강의가 비어있는 경우
            from apps.domains.enrollment.models import Enrollment
            enrolled_lecture_ids = list(
                Enrollment.objects.filter(
                    student=student, tenant=tenant, status="ACTIVE"
                ).values_list("lecture_id", flat=True)
            )
            if enrolled_lecture_ids:
                qs = qs.filter(
                    Q(target_lectures__isnull=True) | Q(target_lectures__id__in=enrolled_lecture_ids)
                ).distinct()
            else:
                qs = qs.filter(target_lectures__isnull=True)

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

    def perform_destroy(self, instance):
        """
        세션 삭제 전 참가자의 ClinicLink를 un-resolve 처리.
        CASCADE 삭제로 참가자가 사라지기 전에 enrollment_id 기반으로
        resolved된 ClinicLink를 되돌려 대상자 목록에 다시 나타나게 한다.
        """
        enrollment_ids = list(
            SessionParticipant.objects.filter(
                session=instance,
                enrollment_id__isnull=False,
                status__in=[
                    SessionParticipant.Status.BOOKED,
                    SessionParticipant.Status.PENDING,
                ],
            ).values_list("enrollment_id", flat=True)
        )
        with transaction.atomic():
            if enrollment_ids:
                ClinicLink.objects.filter(
                    enrollment_id__in=enrollment_ids,
                    is_auto=True,
                ).update(resolved_at=None)
            instance.delete()

    def retrieve(self, request, *args, **kwargs):
        """단일 세션 조회 시 직렬화 오류 방지 (annotate 필드 누락 등)."""
        try:
            return super().retrieve(request, *args, **kwargs)
        except Exception:
            logger.exception("ClinicSessionViewSet.retrieve failed")
            return Response(
                {"detail": "세션 조회 중 오류가 발생했습니다."},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    @action(detail=True, methods=["post"])
    def send_reminder(self, request, pk=None):
        """
        POST /clinic/sessions/{id}/send_reminder/
        - 세션 참가자 리마인더 발송
        """
        session = self.get_object()
        result = send_clinic_reminder_for_students(session_id=session.id)
        if result.get("status") == "not_implemented":
            return Response(result, status=status.HTTP_501_NOT_IMPLEMENTED)
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
                _has_target_lectures=Exists(
                    Session.target_lectures.through.objects.filter(session_id=OuterRef("pk"))
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
                "target_school_type": s.target_school_type,
                "has_target_lectures": s._has_target_lectures,
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

    @action(detail=False, methods=["post"], url_path="bulk-create")
    def bulk_create(self, request, *args, **kwargs):
        """
        POST /clinic/sessions/bulk-create/
        반복 클리닉 세션 일괄 생성 (최대 20일)
        - 과거 날짜는 건너뜀
        - IntegrityError(중복)는 건너뜀
        - tenant는 request.tenant에서 강제 설정
        """
        from .serializers import ClinicSessionBulkCreateSerializer

        tenant = getattr(request, "tenant", None)
        if not tenant:
            raise serializers.ValidationError(
                {"tenant": "테넌트 컨텍스트가 필요합니다."}
            )

        ser = ClinicSessionBulkCreateSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        data = ser.validated_data

        dates = data.pop("dates")
        target_lecture_ids = data.pop("target_lecture_ids")
        today = timezone.localdate()

        # Validate target_lecture_ids belong to this tenant
        if target_lecture_ids:
            from apps.domains.lectures.models import Lecture
            valid_count = Lecture.objects.filter(
                id__in=target_lecture_ids, tenant=tenant
            ).count()
            if valid_count != len(target_lecture_ids):
                return Response(
                    {"detail": "선택한 강의가 유효하지 않습니다."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
        created_by = request.user if request.user.is_authenticated else None

        created = []
        skipped = []

        for d in dates:
            # Skip past dates
            if d < today:
                skipped.append({"date": str(d), "reason": "past_date"})
                continue

            try:
                with transaction.atomic():
                    session = Session.objects.create(
                        tenant=tenant,
                        title=data.get("title", ""),
                        date=d,
                        start_time=data["start_time"],
                        duration_minutes=data["duration_minutes"],
                        location=data["location"],
                        max_participants=data["max_participants"],
                        target_grade=data.get("target_grade"),
                        target_school_type=data.get("target_school_type"),
                        created_by=created_by,
                    )
                    if target_lecture_ids:
                        session.target_lectures.set(target_lecture_ids)
                    created.append({"date": str(d), "id": session.id})
            except IntegrityError:
                skipped.append({"date": str(d), "reason": "duplicate"})

        return Response(
            {
                "created": created,
                "skipped": skipped,
                "created_count": len(created),
                "skipped_count": len(skipped),
            },
            status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
        )


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
        if self.action in ("update", "partial_update", "destroy"):
            return [TenantResolvedAndStaff()]
        return [IsAuthenticated()]

    def get_queryset(self):
        tenant = getattr(self.request, "tenant", None)
        qs = (
            SessionParticipant.objects
            .filter(tenant=tenant)
            .filter(student__deleted_at__isnull=True)  # 삭제된 학생 제외
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
        source = serializer.validated_data.get("source") or SessionParticipant.Source.MANUAL
        requested_status = serializer.validated_data.get("status")

        # ✅ 테넌트 교차 검증 (defense-in-depth: serializer queryset + view 명시 체크)
        if session and getattr(session, "tenant_id", None) != getattr(tenant, "id", None):
            return Response(
                {"detail": "해당 세션에 접근할 권한이 없습니다."},
                status=status.HTTP_403_FORBIDDEN,
            )

        # 지난 날짜 예약 차단
        if session and session.date < timezone.localdate():
            return Response(
                {"detail": "지난 날짜의 클리닉은 예약할 수 없습니다."},
                status=status.HTTP_400_BAD_REQUEST,
            )

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
            # 학년 제한 검증
            if session.target_grade:
                if not request_student.grade or session.target_grade != request_student.grade:
                    return Response(
                        {"detail": "해당 클리닉은 다른 학년 대상입니다. 본인 학년의 클리닉만 신청할 수 있습니다."},
                        status=status.HTTP_403_FORBIDDEN,
                    )
            # 학교유형 제한 검증 (빈 문자열 = 제한 없음)
            if session.target_school_type and session.target_school_type.strip():
                if not request_student.school_type or session.target_school_type != request_student.school_type:
                    return Response(
                        {"detail": "해당 클리닉은 다른 학교 유형 대상입니다."},
                        status=status.HTTP_403_FORBIDDEN,
                    )
            # 강의 제한 검증
            target_lec_ids = set(session.target_lectures.values_list("id", flat=True))
            if target_lec_ids:
                from apps.domains.enrollment.models import Enrollment
                enrolled_lec_ids = set(
                    Enrollment.objects.filter(
                        student=request_student, tenant=tenant, status="ACTIVE"
                    ).values_list("lecture_id", flat=True)
                )
                if not target_lec_ids & enrolled_lec_ids:
                    return Response(
                        {"detail": "해당 클리닉은 특정 강의 수강생 대상입니다."},
                        status=status.HTTP_403_FORBIDDEN,
                    )
            # 정원 체크는 아래 atomic 블록에서 select_for_update와 함께 수행
            # 학생 신청은 기본적으로 pending 상태
            if not requested_status or requested_status == SessionParticipant.Status.BOOKED:
                requested_status = SessionParticipant.Status.PENDING
            # 자동 승인 설정 시 바로 booked로 저장
            if getattr(tenant, "clinic_auto_approve_booking", False):
                requested_status = SessionParticipant.Status.BOOKED

        # ✅ 선생님 경로: enrollment_id로부터 student 자동 해석
        if not student and enrollment_id:
            from apps.domains.enrollment.models import Enrollment
            try:
                enrollment = Enrollment.objects.get(id=enrollment_id, tenant=tenant)
                student = enrollment.student
            except Enrollment.DoesNotExist:
                return Response(
                    {"detail": "해당 수강 등록 정보를 찾을 수 없습니다."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
        # ✅ enrollment_id ↔ student 교차검증: 불일치 시 데이터 정합성 위반 방지
        elif student and enrollment_id:
            from apps.domains.enrollment.models import Enrollment
            try:
                enrollment = Enrollment.objects.get(id=enrollment_id, tenant=tenant)
                if enrollment.student_id != student.id:
                    return Response(
                        {"detail": "enrollment_id와 student가 일치하지 않습니다."},
                        status=status.HTTP_400_BAD_REQUEST,
                    )
            except Enrollment.DoesNotExist:
                return Response(
                    {"detail": "해당 수강 등록 정보를 찾을 수 없습니다."},
                    status=status.HTTP_400_BAD_REQUEST,
                )

        if not student:
            return Response(
                {"detail": "student가 필요합니다."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # ✅ atomic block: capacity check lock + duplicate check + save를 직렬화
        with transaction.atomic():
            # 세션 행 잠금 (capacity 재검증 + 중복/저장 직렬화, 학생·선생 모두)
            if session:
                _locked = Session.objects.filter(tenant=tenant).select_for_update().get(pk=session.pk)
                # 정원 재검증 (lock 획득 후)
                if _locked.max_participants is not None:
                    current_booked = SessionParticipant.objects.filter(
                        session=_locked,
                        status__in=[
                            SessionParticipant.Status.BOOKED,
                            SessionParticipant.Status.PENDING,
                        ],
                    ).count()
                    if current_booked >= _locked.max_participants:
                        return Response(
                            {"detail": "해당 클리닉은 정원이 마감되었습니다."},
                            status=status.HTTP_409_CONFLICT,
                        )

            # 중복 체크: session이 있으면 session 기준, 없으면 requested_date/requested_start_time 기준
            if session:
                exists = SessionParticipant.objects.filter(
                    tenant=tenant,
                    session=session,
                    student=student,
                    status__in=[
                        SessionParticipant.Status.PENDING,
                        SessionParticipant.Status.BOOKED,
                    ],
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

            # Auto-determine clinic_reason from ClinicLink if not explicitly set
            clinic_reason = serializer.validated_data.get("clinic_reason")
            if not clinic_reason and enrollment_id:
                links = ClinicLink.objects.filter(
                    enrollment_id=enrollment_id,
                    resolved_at__isnull=True,
                )
                has_exam = links.filter(reason__in=["AUTO_FAILED", "AUTO_RISK"]).exists()
                has_homework = False  # TODO: check homework-specific links when available
                if has_exam and has_homework:
                    clinic_reason = "both"
                elif has_exam:
                    clinic_reason = "exam"
                elif has_homework:
                    clinic_reason = "homework"
                else:
                    clinic_reason = None

            # 기본 상태 결정: 선생 수동 배정(manual/auto)은 booked, 학생 신청은 pending (auto_approve 시 booked)
            if not requested_status:
                if source in (SessionParticipant.Source.MANUAL, SessionParticipant.Source.AUTO):
                    default_status = SessionParticipant.Status.BOOKED
                elif getattr(tenant, "clinic_auto_approve_booking", False):
                    default_status = SessionParticipant.Status.BOOKED
                else:
                    default_status = SessionParticipant.Status.PENDING
            else:
                default_status = requested_status

            save_kwargs = dict(
                tenant=tenant,
                student=student,
                source=source,
                status=default_status,
                enrollment_id=enrollment_id,
                participant_role=participant_role,
            )
            if clinic_reason:
                save_kwargs["clinic_reason"] = clinic_reason

            try:
                obj = serializer.save(**save_kwargs)
            except IntegrityError as e:
                logger.warning(
                    "clinic_participant IntegrityError: tenant=%s session=%s student=%s err=%s",
                    getattr(tenant, "id", None), getattr(session, "id", None),
                    getattr(student, "id", None), str(e)[:200],
                )
                return Response(
                    {"detail": "이미 해당 세션에 예약된 학생입니다."},
                    status=status.HTTP_409_CONFLICT,
                )

            # enrollment_id가 있으면 해당 수강의 미해소 ClinicLink를 resolved 처리
            if enrollment_id:
                ClinicLink.objects.filter(
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
        next_status = request.data.get("status")
        memo = request.data.get("memo")

        allowed = {c[0] for c in SessionParticipant.Status.choices}
        if next_status not in allowed:
            return Response(
                {"detail": f"Invalid status: {next_status}"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # 상태 전이 검증 (유효한 전이만 허용)
        VALID_TRANSITIONS = {
            SessionParticipant.Status.PENDING: {
                SessionParticipant.Status.BOOKED,
                SessionParticipant.Status.REJECTED,
                SessionParticipant.Status.CANCELLED,
            },
            SessionParticipant.Status.BOOKED: {
                SessionParticipant.Status.ATTENDED,
                SessionParticipant.Status.NO_SHOW,
                SessionParticipant.Status.CANCELLED,
            },
            # Terminal states — no further transitions allowed
            SessionParticipant.Status.ATTENDED: set(),
            SessionParticipant.Status.NO_SHOW: set(),
            SessionParticipant.Status.REJECTED: set(),
            SessionParticipant.Status.CANCELLED: set(),
        }

        with transaction.atomic():
            # Re-fetch with row lock to prevent concurrent status transitions
            obj = SessionParticipant.objects.select_for_update().get(
                pk=self.get_object().pk
            )

            valid_next = VALID_TRANSITIONS.get(obj.status, set())
            if next_status not in valid_next:
                return Response(
                    {"detail": f"'{obj.status}'에서 '{next_status}'(으)로 변경할 수 없습니다."},
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

            # ClinicLink un-resolve: 예약이 실질적으로 무효화된 경우
            # (no_show, cancelled, rejected) ClinicLink를 다시 미해소로 되돌림
            # 트랜잭션 안에서 실행하여 상태 변경과 원자적으로 처리
            if next_status in {
                SessionParticipant.Status.NO_SHOW,
                SessionParticipant.Status.CANCELLED,
                SessionParticipant.Status.REJECTED,
            } and obj.enrollment_id:
                ClinicLink.objects.filter(
                    enrollment_id=obj.enrollment_id,
                    is_auto=True,
                ).update(resolved_at=None)

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

        if not new_session_id:
            return Response(
                {"detail": "new_session_id가 필요합니다."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        tenant = getattr(request, "tenant", None)
        if not tenant:
            return Response(
                {"detail": "테넌트 컨텍스트가 필요합니다."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Only students can use this endpoint
        from apps.domains.student_app.permissions import get_request_student
        request_student = get_request_student(request)
        if not request_student:
            return Response(
                {"detail": "학생만 일정 변경을 신청할 수 있습니다."},
                status=status.HTTP_403_FORBIDDEN,
            )

        with transaction.atomic():
            # Lock and fetch old booking
            try:
                old_booking = (
                    SessionParticipant.objects
                    .select_for_update()
                    .get(pk=pk, tenant=tenant)
                )
            except SessionParticipant.DoesNotExist:
                return Response(
                    {"detail": "예약을 찾을 수 없습니다."},
                    status=status.HTTP_404_NOT_FOUND,
                )

            # Verify ownership
            if old_booking.student != request_student:
                return Response(
                    {"detail": "다른 학생의 예약을 변경할 수 없습니다."},
                    status=status.HTTP_403_FORBIDDEN,
                )

            # Only pending bookings can be changed by student
            if old_booking.status != SessionParticipant.Status.PENDING:
                return Response(
                    {"detail": "승인 대기 중인 예약만 변경할 수 있습니다."},
                    status=status.HTTP_403_FORBIDDEN,
                )

            # Prevent no-op change
            if old_booking.session_id == new_session_id:
                return Response(
                    {"detail": "같은 세션으로는 변경할 수 없습니다."},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            # Lock and validate new session
            try:
                new_session = (
                    Session.objects
                    .filter(tenant=tenant)
                    .select_for_update()
                    .get(pk=new_session_id)
                )
            except Session.DoesNotExist:
                return Response(
                    {"detail": "변경할 세션을 찾을 수 없습니다."},
                    status=status.HTTP_404_NOT_FOUND,
                )

            # Tenant cross-check (defense-in-depth)
            if new_session.tenant_id != tenant.id:
                return Response(
                    {"detail": "해당 세션에 접근할 권한이 없습니다."},
                    status=status.HTTP_403_FORBIDDEN,
                )

            # Past date check
            if new_session.date < timezone.localdate():
                return Response(
                    {"detail": "지난 날짜의 클리닉은 예약할 수 없습니다."},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            # Grade restriction
            if new_session.target_grade:
                if not request_student.grade or new_session.target_grade != request_student.grade:
                    return Response(
                        {"detail": "해당 클리닉은 다른 학년 대상입니다."},
                        status=status.HTTP_403_FORBIDDEN,
                    )

            # School type restriction (빈 문자열 = 제한 없음)
            if new_session.target_school_type and new_session.target_school_type.strip():
                if not request_student.school_type or new_session.target_school_type != request_student.school_type:
                    return Response(
                        {"detail": "해당 클리닉은 다른 학교 유형 대상입니다."},
                        status=status.HTTP_403_FORBIDDEN,
                    )

            # Lecture restriction
            target_lec_ids = set(new_session.target_lectures.values_list("id", flat=True))
            if target_lec_ids:
                from apps.domains.enrollment.models import Enrollment
                enrolled_lec_ids = set(
                    Enrollment.objects.filter(
                        student=request_student, tenant=tenant, status="ACTIVE"
                    ).values_list("lecture_id", flat=True)
                )
                if not target_lec_ids & enrolled_lec_ids:
                    return Response(
                        {"detail": "해당 클리닉은 특정 강의 수강생 대상입니다."},
                        status=status.HTTP_403_FORBIDDEN,
                    )

            # Capacity check (after lock)
            if new_session.max_participants is not None:
                current_booked = SessionParticipant.objects.filter(
                    session=new_session,
                    status__in=[
                        SessionParticipant.Status.BOOKED,
                        SessionParticipant.Status.PENDING,
                    ],
                ).count()
                if current_booked >= new_session.max_participants:
                    return Response(
                        {"detail": "해당 클리닉은 정원이 마감되었습니다."},
                        status=status.HTTP_409_CONFLICT,
                    )

            # Duplicate check on new session
            exists = SessionParticipant.objects.filter(
                tenant=tenant,
                session=new_session,
                student=request_student,
                status__in=[
                    SessionParticipant.Status.PENDING,
                    SessionParticipant.Status.BOOKED,
                ],
            ).exists()
            if exists:
                return Response(
                    {"detail": "이미 해당 세션에 예약된 학생입니다."},
                    status=status.HTTP_409_CONFLICT,
                )

            # Determine status for new booking
            new_status = SessionParticipant.Status.PENDING
            if getattr(tenant, "clinic_auto_approve_booking", False):
                new_status = SessionParticipant.Status.BOOKED

            # Resolve enrollment_id
            enrollment_id = old_booking.enrollment_id
            if not enrollment_id:
                from apps.domains.enrollment.models import Enrollment
                enrollment = Enrollment.objects.filter(
                    student=request_student,
                    tenant=tenant,
                    status="ACTIVE"
                ).first()
                if enrollment:
                    enrollment_id = enrollment.id

            # --- Atomic core: create new, then cancel old ---
            try:
                new_booking = SessionParticipant.objects.create(
                    tenant=tenant,
                    session=new_session,
                    student=request_student,
                    status=new_status,
                    source=SessionParticipant.Source.STUDENT_REQUEST,
                    enrollment_id=enrollment_id,
                    participant_role="manual",
                    memo=memo or "",
                )
            except IntegrityError:
                return Response(
                    {"detail": "이미 해당 세션에 예약된 학생입니다."},
                    status=status.HTTP_409_CONFLICT,
                )

            # Cancel old booking (only after new is secured)
            # Validate transition: terminal states (attended, no_show, rejected, cancelled) cannot be cancelled
            CANCEL_ALLOWED_FROM = {
                SessionParticipant.Status.PENDING,
                SessionParticipant.Status.BOOKED,
            }
            if old_booking.status not in CANCEL_ALLOWED_FROM:
                raise serializers.ValidationError(
                    f"'{old_booking.get_status_display()}' 상태의 예약은 변경할 수 없습니다."
                )
            old_booking.status = SessionParticipant.Status.CANCELLED
            old_booking.status_changed_at = timezone.now()
            old_booking.status_changed_by = request.user
            old_booking.save(
                update_fields=["status", "status_changed_at", "status_changed_by", "updated_at"]
            )

            # Update ClinicLink if applicable
            if old_booking.enrollment_id:
                ClinicLink.objects.filter(
                    enrollment_id=old_booking.enrollment_id,
                    is_auto=True,
                ).update(resolved_at=None)

            if enrollment_id:
                ClinicLink.objects.filter(
                    enrollment_id=enrollment_id,
                    is_auto=True,
                    resolved_at__isnull=True,
                ).update(resolved_at=timezone.now())

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


# ============================================================
# Test
# ============================================================
class TestViewSet(viewsets.ModelViewSet):
    permission_classes = [IsAuthenticated, TenantResolvedAndStaff]
    serializer_class = ClinicTestSerializer
    filter_backends = [DjangoFilterBackend, SearchFilter, OrderingFilter]
    search_fields = ["title"]
    ordering_fields = ["date", "created_at"]
    ordering = ["-date", "-created_at"]

    def get_queryset(self):
        tenant = getattr(self.request, "tenant", None)
        return Test.objects.filter(tenant=tenant).select_related("session")

    def perform_create(self, serializer):
        tenant = getattr(self.request, "tenant", None)
        if not tenant:
            raise serializers.ValidationError(
                {"tenant": "테넌트 컨텍스트가 필요합니다."}
            )
        serializer.save(tenant=tenant)


# ============================================================
# Clinic Settings (패스카드 색상 등)
# ============================================================
class ClinicSettingsView(APIView):
    """
    GET/PATCH /clinic/settings/
    클리닉 설정 (패스카드 배경 색상 등)
    """
    permission_classes = [IsAuthenticated, TenantResolvedAndStaff]

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
    permission_classes = [IsAuthenticated, TenantResolvedAndStaff]
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
