"""
Teachers 도메인 DB 조회 — .objects. 접근을 adapters 내부로 한정 (Gate 7).
"""
from __future__ import annotations


def teacher_filter_tenant_active(tenant):
    from apps.domains.teachers.models import Teacher
    return Teacher.objects.filter(tenant=tenant, is_active=True).order_by("name")


def teacher_filter_tenant(tenant):
    """ViewSet get_queryset용."""
    from apps.domains.teachers.models import Teacher
    return Teacher.objects.filter(tenant=tenant)


def teacher_exists_tenant_name_phone(tenant, name, phone) -> bool:
    from apps.domains.teachers.models import Teacher
    return Teacher.objects.filter(tenant=tenant, name=name, phone=phone or "").exists()


def teacher_update_is_active_by_name_phone(tenant, name, phone, is_active: bool):
    from apps.domains.teachers.models import Teacher
    return Teacher.objects.filter(tenant=tenant, name=name, phone=phone).update(is_active=is_active)


def teacher_delete_by_name_phone(tenant, name, phone):
    from apps.domains.teachers.models import Teacher
    return Teacher.objects.filter(tenant=tenant, name=name, phone=phone).delete()


def teacher_create(tenant, name, phone, is_active: bool = True):
    from apps.domains.teachers.models import Teacher
    return Teacher.objects.create(
        tenant=tenant,
        name=name,
        phone=phone or "",
        is_active=is_active,
    )


def teacher_update_name_phone(tenant, old_name, old_phone, new_name, new_phone):
    """Staff 이름/전화 변경 시 대응하는 Teacher 레코드도 동기화."""
    from apps.domains.teachers.models import Teacher
    return Teacher.objects.filter(
        tenant=tenant, name=old_name, phone=old_phone or "",
    ).update(name=new_name, phone=new_phone or "")
