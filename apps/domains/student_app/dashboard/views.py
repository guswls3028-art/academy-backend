# apps/domains/student_app/dashboard/views.py
from datetime import date
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated

from apps.domains.student_app.permissions import IsStudentOrParent, get_request_student
from apps.domains.community.selectors import get_notice_posts_for_tenant
from apps.domains.enrollment.models import SessionEnrollment
from apps.domains.lectures.models import Session as LectureSession
from .serializers import StudentDashboardSerializer


def _get_tenant_from_request(request):
    tenant = getattr(request, "tenant", None)
    if not tenant:
        student = get_request_student(request)
        if student and getattr(student, "tenant", None):
            tenant = student.tenant
    return tenant


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
            academies = getattr(tenant, "academies", None) or []
            if not academies:
                academies = [{
                    "name": (getattr(tenant, "name", None) or "").strip(),
                    "phone": (getattr(tenant, "headquarters_phone", None) or "").strip(),
                }]
            data["tenant_info"] = {
                "name": (getattr(tenant, "name", None) or "").strip(),
                "phone": (getattr(tenant, "phone", None) or "").strip(),
                "headquarters_phone": (getattr(tenant, "headquarters_phone", None) or "").strip(),
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
                today = date.today()
                session_ids = (
                    SessionEnrollment.objects.filter(
                        enrollment__student=student,
                        enrollment__tenant=tenant,
                        session__date=today,
                    )
                    .values_list("session_id", flat=True)
                    .distinct()
                )
                sessions = (
                    LectureSession.objects.filter(id__in=session_ids)
                    .select_related("lecture")
                    .order_by("order", "id")
                )
                data["today_sessions"] = [
                    {
                        "id": s.id,
                        "title": getattr(s, "title", "") or f"{getattr(s.lecture, 'title', '')} {s.order}차시",
                        "date": s.date.isoformat() if s.date else None,
                        "status": None,
                    }
                    for s in sessions
                ]
        return Response(StudentDashboardSerializer(data).data)
