# PATH: apps/domains/attendance/views.py

import logging
from django.db import transaction
from rest_framework.viewsets import ModelViewSet
from rest_framework.decorators import action
from rest_framework.filters import SearchFilter
from rest_framework.pagination import PageNumberPagination
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework.response import Response
from rest_framework import status
from rest_framework.exceptions import NotFound

from academy.adapters.db.django import repositories_enrollment as enroll_repo
from .models import Attendance
from .serializers import (
    AttendanceSerializer,
    AttendanceMatrixStudentSerializer,
)
from .filters import AttendanceFilter

from rest_framework.permissions import IsAuthenticated
from apps.core.permissions import TenantResolvedAndStaff

from apps.domains.lectures.models import Lecture, Session
from apps.domains.enrollment.models import Enrollment, SessionEnrollment
from apps.domains.exams.models import ExamEnrollment
from apps.domains.homework.models import HomeworkAssignment
from apps.domains.ai.gateway import dispatch_job

logger = logging.getLogger(__name__)


def _send_attendance_notification(tenant, attendance, trigger):
    """
    출결 알림톡 발송 (best-effort, 실패해도 출결 처리는 유지).
    trigger: "check_in_complete" 또는 "absent_occurred"

    Time Guard: 세션 날짜가 오늘이 아니면 발송하지 않음.
    과거 날짜 출결 등록/수정은 행정 작업이지 실시간 이벤트가 아니므로
    학부모에게 "입실하였습니다" 알림을 보내면 안 됨.
    """
    try:
        from apps.support.messaging.services import send_event_notification
        from django.utils import timezone

        enrollment = attendance.enrollment
        student = enrollment.student
        session = attendance.session
        lecture = session.lecture
        now = timezone.localtime()

        # ── Time Guard: 오늘 세션만 알림 발송 ──
        session_date = session.date
        today = now.date()
        if session_date and session_date != today:
            logger.info(
                "attendance notification skipped (time guard): "
                "trigger=%s session_date=%s today=%s att_id=%s",
                trigger, session_date, today, attendance.id,
            )
            return

        context = {
            "강의명": lecture.title or "",
            "차시명": session.title or f"{session.order}차시",
            "날짜": str(session_date) if session_date else now.strftime("%Y-%m-%d"),
            "시간": now.strftime("%H:%M"),
        }

        send_event_notification(
            tenant=tenant,
            trigger=trigger,
            student=student,
            send_to="parent",
            context=context,
        )
    except Exception:
        logger.exception(
            "attendance notification failed: trigger=%s attendance_id=%s",
            trigger, attendance.id,
        )


class AttendanceListPagination(PageNumberPagination):
    """출결 목록 — 학생 도메인과 동일하게 page_size 쿼리 허용, 총계 표기용 count 반환."""
    page_size = 50
    page_size_query_param = "page_size"
    max_page_size = 500


class AttendanceViewSet(ModelViewSet):
    """
    lectures/attendance
    """

    serializer_class = AttendanceSerializer
    permission_classes = [IsAuthenticated, TenantResolvedAndStaff]
    pagination_class = AttendanceListPagination

    filter_backends = [DjangoFilterBackend, SearchFilter]
    filterset_class = AttendanceFilter
    search_fields = ["enrollment__student__name"]

    def get_queryset(self):
        tenant = getattr(self.request, "tenant", None)
        return (
            Attendance.objects
            .filter(tenant=tenant)
            .filter(enrollment__student__deleted_at__isnull=True)
            .select_related(
                "session",
                "session__lecture",
                "enrollment",
                "enrollment__student",
            )
        )

    # =========================================================
    # 0️⃣ 퇴원 처리 (SECESSION → 수강등록 비활성화 + 시험/과제 대상 제외)
    # =========================================================
    @transaction.atomic
    def partial_update(self, request, *args, **kwargs):
        instance = self.get_object()
        new_status = request.data.get("status")

        if new_status == "SECESSION" and instance.status != "SECESSION":
            if not request.data.get("confirm_secession"):
                return Response(
                    {"detail": "퇴원 처리는 confirm_secession: true를 포함해야 합니다."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            tenant = getattr(request, "tenant", None)
            enrollment = instance.enrollment

            # 수강등록 비활성화
            Enrollment.objects.filter(
                id=enrollment.id, tenant=tenant
            ).update(status="INACTIVE")

            # 해당 수강등록의 모든 출결을 SECESSION으로 변경
            Attendance.objects.filter(
                tenant=tenant, enrollment=enrollment
            ).update(status="SECESSION")

            # 시험 응시 대상에서 제거
            ExamEnrollment.objects.filter(
                enrollment=enrollment
            ).delete()

            # 과제 대상에서 제거
            HomeworkAssignment.objects.filter(
                tenant=tenant, enrollment=enrollment
            ).delete()

            logger.info(
                "SECESSION enrollment_id=%s student_id=%s tenant_id=%s — "
                "enrollment INACTIVE, exam/homework enrollments removed",
                enrollment.id,
                enrollment.student_id,
                tenant.id,
            )

            instance.refresh_from_db()
            return Response(AttendanceSerializer(instance).data)

        # 일반 출결 상태 변경 (PRESENT, ABSENT 등)
        response = super().partial_update(request, *args, **kwargs)
        instance.refresh_from_db()

        # 일반 강의 출결 변경 시 알림톡 발송하지 않음.
        # 입실/퇴실/결석 알림은 클리닉 전용 기능. 일반 강의 출결은 행정 작업.

        return response

    # =========================================================
    # 0-1️⃣ 전체 현장 출석 (세션 내 모든 출결을 PRESENT로 일괄 변경)
    # =========================================================
    @transaction.atomic
    @action(detail=False, methods=["post"], url_path="bulk_set_present")
    def bulk_set_present(self, request):
        tenant = getattr(request, "tenant", None)
        session_id = request.data.get("session")
        if not session_id:
            return Response(
                {"detail": "session은 필수입니다"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        session = Session.objects.select_related("lecture").filter(id=session_id).first()
        if not session:
            raise NotFound("세션을 찾을 수 없습니다.")
        if session.lecture.tenant_id != tenant.id:
            raise NotFound("세션을 찾을 수 없습니다.")

        # 변경 대상 ID를 먼저 수집 (알림톡 발송용)
        target_qs = Attendance.objects.filter(
            tenant=tenant, session=session,
        ).exclude(
            status="PRESENT",
        ).exclude(
            status="SECESSION",
        ).exclude(
            enrollment__status="INACTIVE",
        )
        target_ids = list(target_qs.values_list("id", flat=True))

        updated = target_qs.update(status="PRESENT")

        # 일반 강의 전체 출석은 행정 작업 — 알림톡 발송하지 않음.
        # 입실/결석 알림은 클리닉 전용 기능.

        return Response(
            {"updated": updated, "session": session_id},
            status=status.HTTP_200_OK,
        )

    # =========================================================
    # 1️⃣ 세션 기준 학생 등록
    # =========================================================
    @transaction.atomic
    @action(detail=False, methods=["post"])
    def bulk_create(self, request):
        tenant = getattr(request, "tenant", None)

        session_id = request.data.get("session")
        student_ids = request.data.get("students", [])

        if not session_id or not isinstance(student_ids, list):
            return Response(
                {"detail": "session, students(list)는 필수입니다"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        session = Session.objects.select_related("lecture").filter(id=session_id).first()
        if not session:
            raise NotFound("세션을 찾을 수 없습니다.")
        # 🔐 tenant isolation: verify session belongs to request tenant
        if session.lecture.tenant_id != tenant.id:
            raise NotFound("세션을 찾을 수 없습니다.")
        created = []

        for sid in student_ids:
            enrollment, created_new = enroll_repo.enrollment_get_or_create(
                tenant=tenant,
                lecture=session.lecture,
                student_id=sid,
                defaults={"status": "ACTIVE"},
            )
            # 퇴원(INACTIVE) 수강생 재등록 시 활성화 복원
            if not created_new and enrollment.status != "ACTIVE":
                enrollment.status = "ACTIVE"
                enrollment.save(update_fields=["status"])

            enroll_repo.session_enrollment_get_or_create_tenant(
                tenant=tenant,
                session=session,
                enrollment=enrollment,
            )

            attendance, _ = enroll_repo.attendance_get_or_create_tenant(
                tenant=tenant,
                enrollment=enrollment,
                session=session,
                defaults={"status": "PRESENT"},
            )

            created.append(attendance)

        # 차시 학생 등록(bulk_create)은 행정 작업 — 입실(check_in_complete) 알림톡 발송 안 함.
        # 실제 입실 알림은 partial_update(개별 출결 변경) 또는 bulk_set_present(전체 현장 출석)에서만 발송.

        return Response(
            AttendanceSerializer(created, many=True).data,
            status=status.HTTP_201_CREATED,
        )

    # =========================================================
    # 2️⃣ 강의 × 차시 출결 매트릭스
    # =========================================================
    @action(detail=False, methods=["get"], url_path="matrix")
    def matrix(self, request):
        tenant = getattr(request, "tenant", None)

        lecture_id = request.query_params.get("lecture")
        if not lecture_id:
            return Response(
                {"detail": "lecture 파라미터는 필수입니다"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        lecture = enroll_repo.get_lecture_by_id_tenant_raw(lecture_id, tenant)
        if not lecture:
            raise NotFound("강의를 찾을 수 없습니다.")

        sessions = enroll_repo.get_sessions_for_lecture_ordered(lecture)

        enrollment_ids = list(enroll_repo.get_session_enrollment_enrollment_ids(tenant, lecture))
        enrollments = enroll_repo.get_enrollments_by_ids_all(enrollment_ids, tenant)

        attendances = enroll_repo.get_attendances_for_lecture(tenant, lecture, enrollments)

        attendance_map = {
            (a.enrollment_id, a.session_id): a
            for a in attendances
        }

        # ✅ 클리닉 하이라이트 일괄 계산
        from apps.domains.results.utils.clinic_highlight import compute_clinic_highlight_map
        highlight_map = compute_clinic_highlight_map(
            tenant=tenant,
            enrollment_ids=set(en.id for en in enrollments),
        )

        students_payload = []

        for en in enrollments:
            # profile_photo_url
            profile_photo_url = None
            r2_key = getattr(en.student, "profile_photo_r2_key", None) or ""
            if r2_key:
                try:
                    from django.conf import settings as _settings
                    from libs.r2_client.presign import create_presigned_get_url
                    profile_photo_url = create_presigned_get_url(r2_key, expires_in=3600, bucket=_settings.R2_STORAGE_BUCKET)
                except Exception:
                    pass

            row = {
                "student_id": en.student.id,
                "name": en.student.name,
                "phone": en.student.phone,
                "parent_phone": en.student.parent_phone,
                "profile_photo_url": profile_photo_url,
                "name_highlight_clinic_target": highlight_map.get(en.id, False),
                "attendance": {},
            }

            for s in sessions:
                att = attendance_map.get((en.id, s.id))
                if att:
                    row["attendance"][str(s.id)] = {
                        "attendance_id": att.id,
                        "status": att.status,
                    }

            students_payload.append(row)

        return Response(
            {
                "lecture": {
                    "id": lecture.id,
                    "title": lecture.title,
                    "color": (lecture.color or "#3b82f6"),
                },
                "sessions": [
                    {
                        "id": s.id,
                        "order": s.order,
                        "date": s.date,
                    }
                    for s in sessions
                ],
                "students": AttendanceMatrixStudentSerializer(
                    students_payload, many=True
                ).data,
            }
        )

    # =========================================================
    # 3️⃣ 엑셀 내보내기 (워커 비동기)
    # POST /api/v1/lectures/attendance/excel/ body: { "lecture_id": int }
    # 응답: { "job_id", "status": "PENDING" } → 클라이언트는 GET /api/v1/jobs/<job_id>/ 폴링 후 result.download_url 로 다운로드
    # =========================================================
    @action(detail=False, methods=["post"], url_path="excel")
    def excel(self, request):
        tenant = getattr(request, "tenant", None)
        if not tenant:
            return Response(
                {"detail": "tenant가 필요합니다."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        lecture_id = request.data.get("lecture_id") or request.query_params.get("lecture")
        if not lecture_id:
            return Response(
                {"detail": "lecture_id(또는 lecture)는 필수입니다."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        lecture = enroll_repo.get_lecture_by_id_tenant_raw(lecture_id, tenant)
        if not lecture:
            raise NotFound("강의를 찾을 수 없습니다.")

        out = dispatch_job(
            job_type="attendance_excel_export",
            payload={
                "tenant_id": str(tenant.id),
                "lecture_id": int(lecture.id),
            },
            tenant_id=str(tenant.id),
            source_domain="attendance",
            source_id=str(lecture.id),
            tier="basic",
            idempotency_key=f"attendance_export:{tenant.id}:{lecture.id}",
        )
        if not out.get("ok"):
            return Response(
                {"detail": out.get("error", "job 등록 실패")},
                status=status.HTTP_400_BAD_REQUEST,
            )
        logger.info(
            "ATTENDANCE_EXCEL_EXPORT dispatch job_id=%s tenant_id=%s lecture_id=%s",
            out["job_id"],
            tenant.id,
            lecture.id,
        )
        return Response(
            {"job_id": out["job_id"], "status": "PENDING"},
            status=status.HTTP_202_ACCEPTED,
        )
