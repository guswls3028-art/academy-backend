# PATH: apps/domains/results/permissions.py
from __future__ import annotations

from rest_framework.permissions import BasePermission

from apps.core.permissions import is_effective_staff


def _role(u) -> str:
    """
    프로젝트마다 user.role / user.user_type / groups 등 다를 수 있어서 방어적으로.
    - 있으면 쓰고
    - 없으면 is_staff/is_superuser로 판단
    """
    v = getattr(u, "role", None) or getattr(u, "user_type", None) or ""
    return str(v).upper()


def is_admin_user(u) -> bool:
    return bool(getattr(u, "is_superuser", False) or getattr(u, "is_staff", False) or _role(u) in ("ADMIN", "STAFF"))


def is_teacher_user(u) -> bool:
    # 프로젝트에 따라 "TEACHER" 문자열이 다를 수 있음 → 필요시 여기만 수정
    return bool(is_admin_user(u) or _role(u) in ("TEACHER",))


def is_student_user(u) -> bool:
    # 명시적으로 teacher/admin 아니면 student로 취급(일반적인 정책)
    return bool(not is_teacher_user(u))


class IsStudent(BasePermission):
    def has_permission(self, request, view):
        u = getattr(request, "user", None)
        return bool(u and u.is_authenticated and is_student_user(u))


class IsTeacherOrAdmin(BasePermission):
    """
    테넌트 내 슈퍼유저급: is_effective_staff(오너 포함) 또는 레거시 role 판단.
    원장(owner) = 프로그램 내 풀 권한, 충돌 방지용 core.is_effective_staff 사용.
    """

    def has_permission(self, request, view):
        u = getattr(request, "user", None)
        if not u or not u.is_authenticated:
            return False
        tenant = getattr(request, "tenant", None)
        if is_effective_staff(u, tenant):
            return True
        return is_teacher_user(u)
