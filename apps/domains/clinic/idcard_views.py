# PATH: apps/domains/clinic/idcard_views.py
"""
학생 클리닉 인증(차시별 합불) 전용 API
GET /api/v1/clinic/idcard/
- 단일 진실: progress.ClinicLink(is_auto=True, resolved_at__isnull=True)
- 서버 기준 오늘 날짜 반환 (위조 방지)
"""
from django.utils import timezone
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated

from apps.domains.students.models import Student
from apps.domains.enrollment.models import Enrollment
from apps.domains.lectures.models import Session as LectureSession
from apps.domains.progress.models import ClinicLink


class StudentClinicIdcardView(APIView):
    """
    GET /clinic/idcard/
    학생 본인 차시별 합불 + 클리닉 대상 여부.
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        user = request.user
        student = getattr(user, "student_profile", None)
        if not student or not isinstance(student, Student):
            return Response({
                "student_name": "",
                "server_date": timezone.now().date().isoformat(),
                "server_datetime": timezone.now().isoformat(),
                "histories": [],
                "current_result": "SUCCESS",
            })

        enrollment = (
            Enrollment.objects
            .filter(student=student, status="ACTIVE")
            .select_related("lecture")
            .order_by("id")
            .first()
        )
        if not enrollment:
            return Response({
                "student_name": getattr(student, "name", "") or "",
                "server_date": timezone.now().date().isoformat(),
                "server_datetime": timezone.now().isoformat(),
                "histories": [],
                "current_result": "SUCCESS",
            })

        sessions = list(
            LectureSession.objects
            .filter(lecture=enrollment.lecture)
            .order_by("order")
        )
        enrollment_id = enrollment.id
        clinic_links = set(
            ClinicLink.objects.filter(
                enrollment_id=enrollment_id,
                is_auto=True,
                resolved_at__isnull=True,
            ).values_list("session_id", flat=True)
        )

        histories = []
        for sess in sessions:
            clinic_required = sess.id in clinic_links
            histories.append({
                "session_order": sess.order,
                "passed": not clinic_required,
                "clinic_required": clinic_required,
            })

        any_clinic = any(h["clinic_required"] for h in histories)
        return Response({
            "student_name": getattr(student, "name", "") or "",
            "server_date": timezone.now().date().isoformat(),
            "server_datetime": timezone.now().isoformat(),
            "histories": histories,
            "current_result": "FAIL" if any_clinic else "SUCCESS",
        })
