# apps/domains/student_app/permissions.py
from rest_framework.permissions import BasePermission

from apps.core.models import TenantMembership


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


class IsStudentOrParent(BasePermission):
    """
    학생 또는 학부모 로그인 전용
    - 학생: user.student_profile 존재
    - 학부모: TenantMembership role=parent
    """
    def has_permission(self, request, view):
        user = request.user
        if not user or not user.is_authenticated:
            return False
        if hasattr(user, "student_profile"):
            return True
        tenant = getattr(request, "tenant", None)
        if not tenant:
            return False
        return TenantMembership.objects.filter(
            tenant=tenant, user=user, is_active=True, role="parent"
        ).exists()


def get_request_student(request):
    """
    요청자에 해당하는 Student 반환
    - 학생: user.student_profile
    - 학부모: X-Student-Id 헤더가 있으면 해당 자녀(삭제 제외 목록 내), 없으면 연결된 첫 번째 학생
    """
    user = request.user
    if hasattr(user, "student_profile") and user.student_profile:
        return user.student_profile
    from apps.domains.parents.models import Parent
    parent = getattr(user, "parent_profile", None)
    if not parent:
        return None
    active_students = parent.students.filter(deleted_at__isnull=True)
    header_id = request.META.get("HTTP_X_STUDENT_ID")
    if header_id:
        try:
            sid = int(header_id)
            return active_students.filter(id=sid).first()
        except (TypeError, ValueError):
            pass
    return active_students.first()
