# PATH: apps/domains/clinic/views/session_views.py
import logging

from django.db import IntegrityError, transaction
from django.db.models import Count, Q, Exists, OuterRef
from django.utils import timezone
from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from rest_framework import serializers

from django_filters.rest_framework import DjangoFilterBackend
from rest_framework.filters import SearchFilter, OrderingFilter

from ..models import Session, SessionParticipant
from ..serializers import ClinicSessionSerializer
from ..filters import SessionFilter

from apps.core.permissions import TenantResolvedAndMember, TenantResolvedAndStaff
from apps.domains.messaging.services import send_clinic_reminder_for_students
from apps.domains.progress.models import ClinicLink
from apps.domains.progress.services.clinic_resolution_service import ClinicResolutionService

logger = logging.getLogger(__name__)


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
        return [IsAuthenticated(), TenantResolvedAndMember()]
    filter_backends = [DjangoFilterBackend, SearchFilter, OrderingFilter]
    filterset_class = SessionFilter
    search_fields = ["location"]
    ordering_fields = ["date", "start_time", "created_at"]
    ordering = ["-date", "-start_time"]

    def get_queryset(self):
        tenant = getattr(self.request, "tenant", None)
        if not tenant:
            raise serializers.ValidationError(
                {"tenant": "테넌트 컨텍스트가 필요합니다. (호스트 또는 X-Tenant-Code 확인)"}
            )
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
        세션 삭제. ClinicLink 해소는 시험/과제 통과에 의해 결정되므로
        세션 삭제 시 해소 상태를 건드리지 않음.
        단, BOOKING_LEGACY 해소만 되돌림 (레거시 호환).
        """
        tenant = getattr(self.request, "tenant", None)
        if not tenant:
            raise serializers.ValidationError(
                {"tenant": "테넌트 컨텍스트가 필요합니다. (호스트 또는 X-Tenant-Code 확인)"}
            )
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
                # 레거시 예약 기반 해소만 되돌림. 실제 pass 기반 해소는 유지.
                # SSOT: ClinicResolutionService.unresolve() 경유
                # 범위 제한: 이 clinic session의 target_lectures에 해당하는 ClinicLink만
                target_lecture_ids = list(instance.target_lectures.values_list("id", flat=True))
                link_filter = Q(
                    enrollment_id__in=enrollment_ids,
                    is_auto=True,
                    resolution_type="BOOKING_LEGACY",
                    resolved_at__isnull=False,
                    session__lecture__tenant=tenant,
                )
                if target_lecture_ids:
                    link_filter &= Q(session__lecture_id__in=target_lecture_ids)
                qs = ClinicLink.objects.filter(link_filter)
                for link in qs:
                    ClinicResolutionService.unresolve(clinic_link_id=link.id)
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
        if not tenant:
            raise serializers.ValidationError(
                {"tenant": "테넌트 컨텍스트가 필요합니다. (호스트 또는 X-Tenant-Code 확인)"}
            )

        year = request.query_params.get("year")
        month = request.query_params.get("month")
        section_id = request.query_params.get("section")

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
            .select_related("section")
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

        if section_id:
            if section_id == "unassigned":
                qs = qs.filter(section__isnull=True)
            else:
                try:
                    qs = qs.filter(section_id=int(section_id))
                except (TypeError, ValueError):
                    pass

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
                "duration_minutes": s.duration_minutes,
                "participant_count": s.participant_count,
                "booked_count": s.booked_count,
                "no_show_count": s.no_show_count,
                "max_participants": getattr(s, "max_participants", None),
                "section": s.section_id,
                "section_label": s.section.label if s.section_id else None,
                "section_type": s.section.section_type if s.section_id else None,
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
        from ..serializers import ClinicSessionBulkCreateSerializer

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
        section_id = data.pop("section_id", None)
        today = timezone.localdate()

        # Validate section belongs to this tenant
        section_obj = None
        if section_id:
            from apps.domains.lectures.models import Section
            section_obj = Section.objects.filter(id=section_id, tenant=tenant).first()
            if not section_obj:
                return Response(
                    {"detail": "선택한 반이 유효하지 않습니다."},
                    status=status.HTTP_400_BAD_REQUEST,
                )

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
                        section=section_obj,
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
