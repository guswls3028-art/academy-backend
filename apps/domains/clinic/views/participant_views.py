# PATH: apps/domains/clinic/views/participant_views.py
import logging
import time

from django.db import IntegrityError, transaction
from django.utils import timezone
from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from rest_framework import serializers

from django_filters.rest_framework import DjangoFilterBackend
from rest_framework.filters import SearchFilter, OrderingFilter

from ..models import Session, SessionParticipant
from ..serializers import (
    ClinicSessionParticipantSerializer,
    ClinicSessionParticipantCreateSerializer,
)
from ..filters import ParticipantFilter

from apps.core.permissions import TenantResolvedAndMember, TenantResolvedAndStaff
from apps.domains.progress.models import ClinicLink

logger = logging.getLogger(__name__)


def _send_clinic_notification(tenant, student, trigger, context=None):
    """클리닉 알림 — 학생+학부모 동시 발송 (AUTO_DEFAULT 정책)."""
    try:
        from apps.support.messaging.services import send_event_notification
        for send_to in ("parent", "student"):
            send_event_notification(
                tenant=tenant,
                trigger=trigger,
                student=student,
                send_to=send_to,
                context=context,
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
        if self.action in ("update", "partial_update", "destroy"):
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
        if not tenant:
            raise serializers.ValidationError(
                {"tenant": "테넌트 컨텍스트가 필요합니다. (호스트 또는 X-Tenant-Code 확인)"}
            )

        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        session = serializer.validated_data.get("session")
        requested_date = serializer.validated_data.get("requested_date")
        requested_start_time = serializer.validated_data.get("requested_start_time")
        student = serializer.validated_data.get("student")
        enrollment_obj = serializer.validated_data.get("enrollment")
        enrollment_id = enrollment_obj.id if enrollment_obj else None
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

            # enrollment_id 자동 조회 (student만 있고 enrollment_id 없을 때)
            if not enrollment_id and student:
                from apps.domains.enrollment.models import Enrollment
                # Deterministic ordering: most recent enrollment first (prevents ambiguity with multiple active enrollments)
                enrollment = Enrollment.objects.filter(
                    student=student,
                    tenant=tenant,
                    status="ACTIVE"
                ).order_by("-enrolled_at", "-id").first()
                if enrollment:
                    enrollment_id = enrollment.id

            # Auto-determine clinic_reason from ClinicLink if not explicitly set
            clinic_reason = serializer.validated_data.get("clinic_reason")
            if not clinic_reason and enrollment_id:
                # tenant 격리: session__lecture__tenant 명시 필터
                links = ClinicLink.objects.filter(
                    enrollment_id=enrollment_id,
                    resolved_at__isnull=True,
                    session__lecture__tenant=tenant,
                )
                has_exam = links.filter(source_type="exam").exists()
                has_homework = links.filter(source_type="homework").exists()
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

            # ✅ V1.1.1 remediation 재정렬:
            # 예약(booking)은 운영 이벤트이며 해소 트리거가 아님.
            # ClinicLink 해소는 실제 시험/과제 통과 시에만 발생.
            # (ClinicResolutionService가 progress pipeline에서 자동 처리)

        # ── 클리닉 예약 완료 알림 (AUTO_DEFAULT, 학생+학부모) ──
        if obj.status in (SessionParticipant.Status.BOOKED, SessionParticipant.Status.PENDING):
            _t, _s = tenant, student
            _ctx = {
                "클리닉명": getattr(session, "title", "") if session else "",
                "장소": getattr(session, "location", "") if session else "",
                "날짜": str(session.date) if session and session.date else "",
                "시간": str(session.start_time)[:5] if session and session.start_time else "",
                # 예약 건별 멱등 키 — 미지정 시 occurrence_key가 날짜만 되어 같은 날 재예약 시 발송이 DB dedup으로 스킵될 수 있음
                "_domain_object_id": f"clinic_participant_{obj.pk}",
            }
            transaction.on_commit(lambda: _send_clinic_notification(_t, _s, "clinic_reservation_created", _ctx))

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
        # 학생: terminal state 변경 불가
        # 관리자: attended/no_show → booked 되돌리기 허용 (오입력 수정용)
        from apps.domains.student_app.permissions import get_request_student
        _is_student = get_request_student(request) is not None

        if _is_student:
            VALID_TRANSITIONS = {
                SessionParticipant.Status.PENDING: {
                    SessionParticipant.Status.CANCELLED,
                },
                SessionParticipant.Status.BOOKED: set(),
                SessionParticipant.Status.ATTENDED: set(),
                SessionParticipant.Status.NO_SHOW: set(),
                SessionParticipant.Status.REJECTED: set(),
                SessionParticipant.Status.CANCELLED: set(),
            }
        else:
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
                # 관리자: attended/no_show 간 전환 및 booked 되돌리기 허용 (오입력 수정용)
                SessionParticipant.Status.ATTENDED: {
                    SessionParticipant.Status.BOOKED,
                    SessionParticipant.Status.NO_SHOW,
                },
                SessionParticipant.Status.NO_SHOW: {
                    SessionParticipant.Status.BOOKED,
                    SessionParticipant.Status.ATTENDED,
                },
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

            # ✅ V1.1.1 remediation 재정렬:
            # 예약 상태 변경(no_show, cancelled, rejected)은 ClinicLink 해소에 영향 없음.

            # ── 클리닉 상태 변경 알림 (AUTO_DEFAULT, transaction.on_commit으로 안전 발송) ──
            _trigger_map = {
                SessionParticipant.Status.CANCELLED: "clinic_cancelled",
                SessionParticipant.Status.ATTENDED: "clinic_check_in",
                SessionParticipant.Status.NO_SHOW: "clinic_absent",
            }
            _trigger = _trigger_map.get(next_status)
            if _trigger:
                _t = getattr(request, "tenant", None)
                _s = obj.student
                _tr = _trigger
                _session = obj.session
                _loc = getattr(_session, "location", "") if _session else ""
                _date = str(_session.date) if _session and _session.date else ""
                _time = str(_session.start_time)[:5] if _session and getattr(_session, "start_time", None) else ""
                _is_cancel = (next_status == SessionParticipant.Status.CANCELLED)
                _is_absent = (next_status == SessionParticipant.Status.NO_SHOW)
                _ctx = {
                    "클리닉명": getattr(_session, "title", "") if _session else "",
                    "장소": f"[취소] {_loc}" if _is_cancel else f"[결석] {_loc}" if _is_absent else _loc,
                    "날짜": _date,
                    "시간": f"취소({_time})" if _is_cancel else f"결석({_time})" if _is_absent else _time,
                    "_domain_object_id": f"participant_{obj.pk}_{next_status}_{int(time.time())}",
                }
                transaction.on_commit(lambda: _send_clinic_notification(_t, _s, _tr, _ctx))

        out = ClinicSessionParticipantSerializer(
            obj, context={"request": request}
        ).data
        return Response(out)

    # complete() 허용 전이: 자율학습 완료 시 ATTENDED로 전환할 수 있는 상태
    COMPLETE_ALLOWED_TRANSITIONS = {
        SessionParticipant.Status.PENDING,
        SessionParticipant.Status.BOOKED,
    }

    @action(detail=True, methods=["post"])
    def complete(self, request, pk=None):
        """
        POST /clinic/participants/{id}/complete/
        자율학습 완료 처리 — 이력 기록 + 문자 트리거

        상태 전이: PENDING/BOOKED → ATTENDED (complete 전용 전이)
        이미 ATTENDED/NO_SHOW/CANCELLED/REJECTED인 경우 상태는 변경하지 않고
        completed_at만 기록한다.
        """
        with transaction.atomic():
            obj = SessionParticipant.objects.select_for_update().get(
                pk=self.get_object().pk
            )
            if obj.completed_at:
                return Response(
                    {"detail": "이미 완료 처리된 참가자입니다."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            # terminal 상태(CANCELLED, REJECTED)에서는 완료 불가
            if obj.status in (
                SessionParticipant.Status.CANCELLED,
                SessionParticipant.Status.REJECTED,
            ):
                return Response(
                    {"detail": f"'{obj.get_status_display()}' 상태의 참가자는 완료 처리할 수 없습니다."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            obj.completed_at = timezone.now()
            obj.completed_by = request.user
            # 출석 처리 (자율학습 완료 = 출석 확정)
            # PENDING/BOOKED → ATTENDED 전환만 허용 (명시적 전이 맵)
            if obj.status in self.COMPLETE_ALLOWED_TRANSITIONS:
                obj.status = SessionParticipant.Status.ATTENDED
                obj.status_changed_at = timezone.now()
                obj.status_changed_by = request.user
            obj.save(update_fields=[
                "completed_at", "completed_by",
                "status", "status_changed_at", "status_changed_by",
                "updated_at",
            ])

        # ── 클리닉 퇴실(완료) + 자율학습 완료 알림 ──
        _t = getattr(request, "tenant", None)
        _s = obj.student
        _session = obj.session
        _now = timezone.now()
        _ctx = {
            "클리닉명": getattr(_session, "title", "") if _session else "",
            "장소": getattr(_session, "location", "") if _session else "",
            "날짜": str(_session.date) if _session and _session.date else _now.strftime("%Y-%m-%d"),
            "시간": _now.strftime("%H:%M"),
            "_domain_object_id": str(obj.pk),
        }
        transaction.on_commit(lambda: _send_clinic_notification(_t, _s, "clinic_self_study_completed", _ctx))

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
        with transaction.atomic():
            obj = SessionParticipant.objects.select_for_update().get(
                pk=self.get_object().pk
            )
            if not obj.completed_at:
                return Response(
                    {"detail": "완료 처리되지 않은 참가자입니다."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            obj.completed_at = None
            obj.completed_by = None
            # complete 시 ATTENDED로 전환된 경우 BOOKED로 복원
            if obj.status == SessionParticipant.Status.ATTENDED:
                obj.status = SessionParticipant.Status.BOOKED
                obj.save(update_fields=["completed_at", "completed_by", "status", "updated_at"])
            else:
                obj.save(update_fields=["completed_at", "completed_by", "updated_at"])

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
                # Deterministic ordering: most recent enrollment first (prevents ambiguity with multiple active enrollments)
                enrollment = Enrollment.objects.filter(
                    student=request_student,
                    tenant=tenant,
                    status="ACTIVE"
                ).order_by("-enrolled_at", "-id").first()
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

            # ✅ V1.1.1 remediation 재정렬:
            # 예약 변경은 ClinicLink 해소에 영향 없음.

        # ── 클리닉 예약 변경 알림 (AUTO_DEFAULT, 학생+학부모) ──
        _t, _s = tenant, request_student
        _new_loc = getattr(new_session, "location", "") if new_session else ""
        _new_date = str(new_session.date) if new_session and new_session.date else ""
        _new_time = str(new_session.start_time)[:5] if new_session and getattr(new_session, "start_time", None) else ""
        _ctx = {
            "클리닉명": getattr(new_session, "title", "") if new_session else "",
            "장소": f"[변경] {_new_loc}" if _new_loc else "[변경]",
            "날짜": _new_date,
            "시간": _new_time,
            "_domain_object_id": f"booking_change_{new_booking.pk}",
        }
        transaction.on_commit(lambda: _send_clinic_notification(_t, _s, "clinic_reservation_changed", _ctx))

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
