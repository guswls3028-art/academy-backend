# PATH: apps/domains/enrollment/permissions.py

from rest_framework.permissions import BasePermission
from apps.domains.enrollment.models import Enrollment


class HasEnrollmentAccess(BasePermission):
    """
    학생이 해당 Enrollment(수강 정보)에 접근 가능한지 검증
    (tenant 기준 강제)
    """
    message = "You do not have access to this enrollment."

    def has_permission(self, request, view):
        user = request.user

        if not user or not user.is_authenticated:
            return False

        student = getattr(user, "student_profile", None)
        if not student:
            return False

        enrollment_id = (
            request.data.get("enrollment_id")
            or request.query_params.get("enrollment_id")
        )
        if not enrollment_id:
            return False

        tenant = getattr(request, "tenant", None)

        return Enrollment.objects.filter(
            id=enrollment_id,
            student=student,
            tenant=tenant,          # ✅ tenant 안전장치
            status="ACTIVE",
        ).exists()
