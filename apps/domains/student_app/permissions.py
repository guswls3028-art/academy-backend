# apps/domains/student_app/permissions.py
from rest_framework.permissions import BasePermission


class IsStudent(BasePermission):
    """
    학생 로그인 전용
    """
    def has_permission(self, request, view):
        user = request.user
        return bool(
            user
            and user.is_authenticated
            and hasattr(user, "student_profile")
        )
