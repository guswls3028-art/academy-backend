# apps/domains/student_app/dashboard/views.py
import re
from datetime import date, time as dt_time
from django.utils import timezone
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated

from apps.domains.student_app.permissions import IsStudentOrParent, get_request_student
from apps.domains.community.selectors import get_notice_posts_for_tenant
from apps.domains.enrollment.models import SessionEnrollment
from apps.domains.lectures.models import Session as LectureSession
from apps.domains.clinic.models import SessionParticipant
from .serializers import StudentDashboardSerializer


def _parse_lecture_start_time(lecture_time_str: str) -> dt_time | None:
    """lecture_time CharField (예: '토 12:00 ~ 13:00')에서 시작 시각 추출."""
    if not lecture_time_str:
        return None
    m = re.search(r"(\d{1,2}):(\d{2})", lecture_time_str)
    if m:
        return dt_time(int(m.group(1)), int(m.group(2)))
    return None


def _get_tenant_from_request(request):
    """request.tenant 반환. 테넌트 미해석 시 None (폴백 없음 — §B 절대 격리)."""
    return getattr(request, "tenant", None)


class StudentDashboardView(APIView):
    """
    GET /student/dashboard/
    - notices: 최신 공지 최대 5건 (Community block_type=notice)
    - today_sessions: 오늘 날짜의 수업 일정 (학생이 SessionEnrollment로 접근 가능한 차시)
    - badges, tenant_info
    """

    permission_classes = [IsAuthenticated, IsStudentOrParent]

    def get(self, request):
        tenant = _get_tenant_from_request(request)
        data = {
            "notices": [],
            "today_sessions": [],
            "badges": {},
            "tenant_info": None,
        }
        if tenant:
            hq_phone = (getattr(tenant, "headquarters_phone", None) or "").strip()
            owner_phone = (getattr(tenant, "phone", None) or "").strip()
            fallback_phone = hq_phone or owner_phone
            academies = getattr(tenant, "academies", None) or []
            if not academies:
                academies = [{
                    "name": (getattr(tenant, "name", None) or "").strip(),
                    "phone": fallback_phone,
                }]
            else:
                # academies JSON에 phone이 비어 있으면 본부/대표 번호로 보완
                academies = [
                    {
                        "name": (a.get("name") or "").strip(),
                        "phone": (a.get("phone") or "").strip() or fallback_phone,
                    }
                    for a in academies
                ]
            data["tenant_info"] = {
                "name": (getattr(tenant, "name", None) or "").strip(),
                "phone": owner_phone,
                "headquarters_phone": hq_phone,
                "academies": academies,
            }
            # 공지: Community 공지 최대 5건
            notice_qs = get_notice_posts_for_tenant(tenant)[:5]
            data["notices"] = [
                {"id": p.id, "title": getattr(p, "title", "") or "", "created_at": getattr(p, "created_at", None)}
                for p in notice_qs
            ]
            # 오늘 일정: 학생이 수강 중인 차시 중 date=오늘
            student = get_request_student(request)
            if student:
                today = timezone.localdate()
                sessions = (
                    LectureSession.objects.filter(
                        sessionenrollment__enrollment__student=student,
                        sessionenrollment__enrollment__tenant=tenant,
                        sessionenrollment__enrollment__status="ACTIVE",  # ✅ 퇴원 학생 제외
                        date=today,
                    )
                    .select_related("lecture")
                    .distinct()
                    .order_by("order", "id")
                )
                data["today_sessions"] = [
                    {
                        "id": s.id,
                        "title": getattr(s, "title", "") or f"{getattr(s.lecture, 'title', '')} {s.order}차시",
                        "date": s.date.isoformat() if s.date else None,
                        "status": None,
                        "type": "session",
                        "start_time": _parse_lecture_start_time(
                            getattr(s.lecture, "lecture_time", "") or ""
                        ),
                    }
                    for s in sessions
                ]
                # 오늘 클리닉 예약 (PENDING/BOOKED)
                clinic_today = (
                    SessionParticipant.objects.filter(
                        student=student,
                        tenant=tenant,
                        status__in=[SessionParticipant.Status.PENDING, SessionParticipant.Status.BOOKED],
                        session__isnull=False,
                        session__date=today,
                    )
                    .select_related("session")
                )
                for cp in clinic_today:
                    sess = cp.session
                    data["today_sessions"].append({
                        "id": cp.id * -1,
                        "title": f"클리닉 {sess.title or sess.location}" if sess else "클리닉",
                        "date": today.isoformat(),
                        "status": "대기 중" if cp.status == "pending" else "예약됨",
                        "type": "clinic",
                        "start_time": sess.start_time if sess else None,
                    })
        return Response(StudentDashboardSerializer(data).data)
