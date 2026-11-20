"""Synthetic staff identities in the isolated Dawon review tenant."""
from apps.domains.staffs.models import Staff

PREFIX = "t719_dawon-demo-"
TEACHER = PREFIX + "teacher"
EMPLOYEE = PREFIX + "employee"
WORK_TYPE = "체험 수업 근무"
WORK_MARK = "dawon-staff-demo:v1"
LEGACY_FIXTURES = {
    TEACHER: ("다원 체험 선생님", "INSTRUCTOR"),
    EMPLOYEE: ("다원 체험 강사", "INSTRUCTOR"),
    PREFIX + "branch-director": ("체험 분원장", "DIRECTOR"),
    **{PREFIX + f"instructor-{i:02}": (f"체험 수학강사 {i:02}", "INSTRUCTOR") for i in range(1, 13)},
    **{PREFIX + f"assistant-{i:02}": (f"체험 수업조교 {i:02}", "ASSISTANT") for i in range(1, 3)},
}

FIXTURES = {
    TEACHER: ("김선생 1", "INSTRUCTOR"),
    EMPLOYEE: ("김선생 2", "INSTRUCTOR"),
    PREFIX + "branch-director": ("김원장 2", "DIRECTOR"),
    **{PREFIX + f"instructor-{i:02}": (f"김선생 {i+2}", "INSTRUCTOR") for i in range(1, 13)},
    **{PREFIX + f"assistant-{i:02}": (f"김조교 {i}", "ASSISTANT") for i in range(1, 3)},
}


def sample_staff(tenant):
    from django.db.models import Q
    from functools import reduce
    from operator import or_
    identity = reduce(or_, (Q(user__username=key, name__in=(name, LEGACY_FIXTURES[key][0])) for key, (name, _) in FIXTURES.items()))
    return Staff.objects.filter(
        identity, tenant=tenant, user__tenant=tenant, phone="", is_active=True,
        is_manager=False, user__is_staff=False, user__is_superuser=False, user__password__startswith="!",
    )
