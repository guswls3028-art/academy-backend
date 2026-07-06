# apps/domains/student_app/permissions.py
from rest_framework.permissions import BasePermission

from apps.core.models import TenantMembership
from apps.support.student_app.permission_dependencies import (
    active_students_for_parent,
    student_for_tenant_user,
)


class IsStudent(BasePermission):
    """
    활성 학생 로그인 전용
    """
    def has_permission(self, request, view):
        user = request.user
        tenant = getattr(request, "tenant", None)
        if tenant:
            return bool(
                user
                and user.is_authenticated
                and student_for_tenant_user(tenant, user, deleted="active")
            )
        return bool(
            user
            and user.is_authenticated
            and hasattr(user, "student_profile")
            and getattr(user.student_profile, "deleted_at", None) is None
        )


class IsStudentOrParent(BasePermission):
    """
    학생 또는 학부모 로그인 전용
    - 학생: request.tenant 기준 active Student 존재
    - 학부모: TenantMembership role=parent
    """
    def has_permission(self, request, view):
        user = request.user
        if not user or not user.is_authenticated:
            return False
        tenant = getattr(request, "tenant", None)
        if not tenant:
            return False
        if student_for_tenant_user(tenant, user, deleted="active"):
            return True
        return TenantMembership.objects.filter(
            tenant=tenant, user=user, is_active=True, role="parent"
        ).exists()


def get_request_student(request):
    """
    요청자에 해당하는 Student 반환
    - 학생: request.tenant 기준 active Student
    - 학부모: X-Student-Id 헤더가 있으면 해당 자녀(삭제 제외 목록 내), 없으면 연결된 첫 번째 학생
    """
    user = request.user
    tenant = getattr(request, "tenant", None)
    if not tenant:
        return None
    student = student_for_tenant_user(tenant, user, deleted="active")
    if student:
        return student
    parent = getattr(user, "parent_profile", None)
    if not parent:
        return None
    active_students = active_students_for_parent(tenant, parent)
    header_id = request.META.get("HTTP_X_STUDENT_ID")
    if header_id:
        try:
            sid = int(header_id)
            return active_students.filter(id=sid).first()
        except (TypeError, ValueError):
            pass
    # Deterministic ordering: latest student ID first (prevents ambiguity when parent has multiple students)
    return active_students.order_by("-id").first()
