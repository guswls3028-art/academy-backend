# apps/domains/student_app/sessions/views.py
import re
from datetime import date as dt_date, time as dt_time

from django.db.models import Count, Q
from django.utils import timezone

from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated

from apps.domains.student_app.permissions import IsStudentOrParent, get_request_student
from apps.domains.enrollment.models import SessionEnrollment
from apps.domains.lectures.models import Session as LectureSession
from apps.domains.clinic.models import SessionParticipant
from apps.domains.attendance.models import Attendance
from .serializers import StudentSessionSerializer


def _parse_lecture_start_time(lecture_time_str: str) -> dt_time | None:
    """lecture_time CharField (예: '토 12:00 ~ 13:00')에서 시작 시각 추출."""
    if not lecture_time_str:
        return None
    m = re.search(r"(\d{1,2}):(\d{2})", lecture_time_str)
    if m:
        return dt_time(int(m.group(1)), int(m.group(2)))
    return None


class StudentSessionListView(APIView):
    """
    GET /student/sessions/me/
    학생이 접근 가능한 차시 목록 + 클리닉 예약 (date 기준 정렬).
    """

    permission_classes = [IsAuthenticated, IsStudentOrParent]

    def get(self, request):
        student = get_request_student(request)
        if not student:
            return Response(StudentSessionSerializer([], many=True).data)
        tenant = getattr(request, "tenant", None)
        if not tenant:
            return Response(StudentSessionSerializer([], many=True).data)

        # 학생이 휴지통으로 비운 cutoff (포함, 이 날짜 이하는 숨김)
        hidden_before: dt_date | None = getattr(student, "schedule_hidden_before", None)
        # 학생이 스와이프로 개별 숨김한 id 집합 (양수=session id, 음수=clinic participant id*-1)
        raw_hidden_ids = getattr(student, "schedule_hidden_ids", None) or []
        hidden_session_ids = {int(x) for x in raw_hidden_ids if isinstance(x, int) and x > 0}
        hidden_clinic_participant_ids = {-int(x) for x in raw_hidden_ids if isinstance(x, int) and x < 0}

        # 1) 강의 차시
        session_ids = (
            SessionEnrollment.objects.filter(
                enrollment__student=student,
                enrollment__tenant=tenant,
                enrollment__status="ACTIVE",
            )
            .values_list("session_id", flat=True)
            .distinct()
        )
        sessions_qs = (
            LectureSession.objects.filter(id__in=session_ids, lecture__tenant=tenant)
            .select_related("lecture")
            .order_by("date", "order", "id")
        )
        if hidden_before is not None:
            sessions_qs = sessions_qs.exclude(date__lte=hidden_before)
        if hidden_session_ids:
            sessions_qs = sessions_qs.exclude(id__in=hidden_session_ids)
        sessions = sessions_qs
        data = [
            {
                "id": s.id,
                "title": getattr(s, "title", "") or f"{getattr(s.lecture, 'title', '')} {s.display_label}",
                "order": s.order,
                "session_type": s.session_type,
                "regular_order": s.regular_order,
                "display_label": s.display_label,
                "date": s.date.isoformat() if s.date else None,
                "status": None,
                "exam_ids": [],
                "type": "session",
                "start_time": _parse_lecture_start_time(
                    getattr(s.lecture, "lecture_time", "") or ""
                ),
            }
            for s in sessions
        ]

        # 2) 클리닉 예약 (PENDING/BOOKED만, session 있는 것만)
        clinic_participants = (
            SessionParticipant.objects
            .filter(
                student=student,
                tenant=tenant,
                status__in=[SessionParticipant.Status.PENDING, SessionParticipant.Status.BOOKED],
                session__isnull=False,
            )
            .select_related("session")
        )
        for cp in clinic_participants:
            sess = cp.session
            if hidden_before is not None and sess and sess.date and sess.date <= hidden_before:
                continue
            if cp.id in hidden_clinic_participant_ids:
                continue
            status_label = "대기 중" if cp.status == "pending" else "예약됨"
            data.append({
                "id": cp.id * -1,  # 음수 ID로 클리닉 구분
                "title": f"🏥 클리닉 {sess.title or sess.location}" if sess else "🏥 클리닉",
                "order": None,
                "session_type": None,
                "regular_order": None,
                "display_label": None,
                "date": sess.date.isoformat() if sess and sess.date else None,
                "status": status_label,
                "exam_ids": [],
                "type": "clinic",
                "start_time": sess.start_time if sess else None,
            })

        # 날짜 정렬
        data.sort(key=lambda x: x.get("date") or "9999-99-99")
        return Response(StudentSessionSerializer(data, many=True).data)


class StudentSessionClearPastView(APIView):
    """
    POST /student/sessions/clear-past/
    학생 본인(또는 학부모 대리 계정)이 "내 일정" 휴지통을 눌렀을 때 호출.
    오늘 이전(어제까지) 차시/클리닉 예약을 화면에서 모두 숨김.
    실제 차시/예약 데이터는 학원/선생 소유이므로 절대 삭제하지 않고,
    Student.schedule_hidden_before 컷오프만 갱신.
    """

    permission_classes = [IsAuthenticated, IsStudentOrParent]

    def post(self, request):
        from datetime import timedelta

        student = get_request_student(request)
        if not student:
            return Response({"detail": "Not found."}, status=404)
        tenant = getattr(request, "tenant", None)
        if not tenant or student.tenant_id != getattr(tenant, "id", None):
            return Response({"detail": "Not found."}, status=404)

        # 오늘은 "지난"이 아니므로 어제까지만 숨김 cutoff 로 잡음.
        cutoff = timezone.localdate() - timedelta(days=1)

        # 일괄 비우기 시 cutoff 이전 개별 숨김은 cutoff 로 흡수되므로 제거.
        # cutoff 이후(미래 일정)에 대한 개별 숨김은 유지 — 학생 의사 존중.
        raw_hidden_ids = list(getattr(student, "schedule_hidden_ids", None) or [])
        future_hidden_session_ids = {
            int(x) for x in raw_hidden_ids if isinstance(x, int) and x > 0
        }
        future_hidden_clinic_participant_ids = {
            -int(x) for x in raw_hidden_ids if isinstance(x, int) and x < 0
        }
        keep: list[int] = []
        if future_hidden_session_ids:
            future_sessions = LectureSession.objects.filter(
                id__in=future_hidden_session_ids,
                lecture__tenant=tenant,
            ).exclude(date__lte=cutoff).values_list("id", flat=True)
            keep.extend(int(i) for i in future_sessions)
        if future_hidden_clinic_participant_ids:
            future_clinic = (
                SessionParticipant.objects
                .filter(id__in=future_hidden_clinic_participant_ids)
                .exclude(session__date__lte=cutoff)
                .values_list("id", flat=True)
            )
            keep.extend(-int(i) for i in future_clinic)

        student.schedule_hidden_before = cutoff
        student.schedule_hidden_ids = keep
        student.save(update_fields=["schedule_hidden_before", "schedule_hidden_ids", "updated_at"])
        return Response({"hidden_before": cutoff.isoformat(), "hidden_ids": keep})


class StudentSessionHideView(APIView):
    """
    POST /student/sessions/hide/  body: {"id": <int>}
    학생이 일정 카드를 스와이프하여 개별 숨김. 양수=LectureSession.id, 음수=ClinicSessionParticipant.id*-1.
    실제 데이터는 그대로 두고 학생 본인 schedule_hidden_ids 에만 dedupe append.
    """

    permission_classes = [IsAuthenticated, IsStudentOrParent]

    def post(self, request):
        student = get_request_student(request)
        if not student:
            return Response({"detail": "Not found."}, status=404)
        tenant = getattr(request, "tenant", None)
        if not tenant or student.tenant_id != getattr(tenant, "id", None):
            return Response({"detail": "Not found."}, status=404)

        raw = request.data.get("id")
        try:
            target_id = int(raw)
        except (TypeError, ValueError):
            return Response({"detail": "id must be an integer."}, status=400)
        if target_id == 0:
            return Response({"detail": "id must be non-zero."}, status=400)

        # 본인 소유 일정만 숨길 수 있도록 검증
        if target_id > 0:
            owns = SessionEnrollment.objects.filter(
                enrollment__student=student,
                enrollment__tenant=tenant,
                session__lecture__tenant=tenant,
                session_id=target_id,
            ).exists()
        else:
            owns = SessionParticipant.objects.filter(
                tenant=tenant,
                student=student,
                id=-target_id,
            ).exists()
        if not owns:
            return Response({"detail": "Not found."}, status=404)

        current = list(getattr(student, "schedule_hidden_ids", None) or [])
        if target_id not in current:
            current.append(target_id)
            student.schedule_hidden_ids = current
            student.save(update_fields=["schedule_hidden_ids", "updated_at"])
        return Response({"hidden_ids": current})


class StudentSessionUnhideView(APIView):
    """
    POST /student/sessions/unhide/  body: {"id": <int>}
    숨김 토스트의 "되돌리기"가 호출. hidden_ids 에서 해당 id 제거.
    """

    permission_classes = [IsAuthenticated, IsStudentOrParent]

    def post(self, request):
        student = get_request_student(request)
        if not student:
            return Response({"detail": "Not found."}, status=404)
        tenant = getattr(request, "tenant", None)
        if not tenant or student.tenant_id != getattr(tenant, "id", None):
            return Response({"detail": "Not found."}, status=404)

        raw = request.data.get("id")
        try:
            target_id = int(raw)
        except (TypeError, ValueError):
            return Response({"detail": "id must be an integer."}, status=400)

        current = list(getattr(student, "schedule_hidden_ids", None) or [])
        if target_id in current:
            current = [x for x in current if x != target_id]
            student.schedule_hidden_ids = current
            student.save(update_fields=["schedule_hidden_ids", "updated_at"])
        return Response({"hidden_ids": current})


class StudentAttendanceSummaryView(APIView):
    """
    GET /student/attendance/summary/
    학생 본인의 출결 누적 요약 + 최근 차시별 상태.
    학부모는 자녀 단위로 받음 (?student_id 옵션).
    """

    permission_classes = [IsAuthenticated, IsStudentOrParent]

    def get(self, request):
        student = get_request_student(request)
        if not student:
            return Response({"detail": "Not found."}, status=404)
        tenant = getattr(request, "tenant", None)
        if not tenant:
            return Response({"detail": "Not found."}, status=404)

        qs = Attendance.objects.filter(
            tenant=tenant,
            enrollment__student=student,
        )

        # 누적 카운트
        agg = qs.aggregate(
            total=Count("id"),
            present=Count("id", filter=Q(status__in=["PRESENT", "ONLINE", "SUPPLEMENT"])),
            absent=Count("id", filter=Q(status="ABSENT")),
            late=Count("id", filter=Q(status="LATE")),
            early=Count("id", filter=Q(status="EARLY_LEAVE")),
            runaway=Count("id", filter=Q(status="RUNAWAY")),
        )

        # 최근 20개 차시 출결
        recent_qs = (
            qs.select_related("session", "session__lecture")
            .order_by("-session__date", "-id")[:20]
        )
        recent = []
        for att in recent_qs:
            sess = att.session
            recent.append({
                "session_id": sess.id,
                "lecture_title": getattr(sess.lecture, "title", "") if sess.lecture_id else "",
                "session_title": sess.title or sess.display_label,
                "date": sess.date.isoformat() if sess.date else None,
                "status": att.status,
            })

        return Response({
            "summary": {
                "total": agg["total"] or 0,
                "present": agg["present"] or 0,
                "absent": agg["absent"] or 0,
                "late": agg["late"] or 0,
                "early_leave": agg["early"] or 0,
                "runaway": agg["runaway"] or 0,
            },
            "recent": recent,
        })


class StudentSessionDetailView(APIView):
    """
    GET /student/sessions/{id}/
    """

    permission_classes = [IsAuthenticated, IsStudentOrParent]

    def get(self, request, pk):
        student = get_request_student(request)
        if not student:
            return Response({"detail": "Not found."}, status=404)
        tenant = getattr(request, "tenant", None)
        if not tenant:
            return Response({"detail": "Not found."}, status=404)
        has_access = SessionEnrollment.objects.filter(
            enrollment__student=student,
            enrollment__tenant=tenant,
            enrollment__status="ACTIVE",  # ✅ 퇴원 학생 제외
            session__lecture__tenant=tenant,
            session_id=pk,
        ).exists()
        if not has_access:
            return Response({"detail": "Not found."}, status=404)
        session = LectureSession.objects.filter(
            id=pk,
            lecture__tenant=tenant,
        ).select_related("lecture").first()
        if not session:
            return Response({"detail": "Not found."}, status=404)
        # 차시에 연결된 운영 시험(regular) 중 활성 — 학생 측 시험 진입 분기용.
        # 2026-05-13 학원장 결정: 시험 단위 status 폐기. status 기반 exclude 제거.
        # 학생별 Achievement SSOT 가 단일 진실 (응시/이수/판정은 attempt 차원).
        exam_ids = list(
            session.exams.filter(
                tenant=tenant,
                exam_type="regular",
                is_active=True,
            ).values_list("id", flat=True)
        )
        data = {
            "id": session.id,
            "title": getattr(session, "title", "") or f"{getattr(session.lecture, 'title', '')} {session.display_label}",
            "date": session.date.isoformat() if session.date else None,
            "status": None,
            "exam_ids": exam_ids,
        }
        return Response(StudentSessionSerializer(data).data)
