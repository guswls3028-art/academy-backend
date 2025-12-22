from rest_framework.permissions import BasePermission
from apps.domains.enrollment.models import Enrollment


class HasEnrollmentAccess(BasePermission):
    """
    학생이 해당 Enrollment(수강 정보)에 접근 가능한지 검증
    """
    message = "You do not have access to this enrollment."

    def has_permission(self, request, view):
        user = request.user

        if not user or not user.is_authenticated:
            return False

        # IsStudent에서 이미 student_profile 보장됨
        student = getattr(user, "student_profile", None)
        if not student:
            return False

        enrollment_id = (
            request.data.get("enrollment_id")
            or request.query_params.get("enrollment_id")
        )
        if not enrollment_id:
            return False

        return Enrollment.objects.filter(
            id=enrollment_id,
            student=student,
            status="ACTIVE",  # ✅ 여기만 핵심 수정
        ).exists()
