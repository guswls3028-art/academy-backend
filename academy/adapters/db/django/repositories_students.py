"""
Students / User / Tag / Enrollment 등 DB 조회·저장 — .objects. 접근을 adapters 내부로 한정 (Gate 7).
"""
from __future__ import annotations


def tag_all():
    from apps.domains.students.models import Tag
    return Tag.objects.all()


def student_filter_tenant(tenant):
    from apps.domains.students.models import Student
    return Student.objects.filter(tenant=tenant)


def user_create_user(username, tenant=None, **kwargs):
    """테넌트별 격리: tenant 전달 시 내부 username t{id}_{입력} 으로 저장."""
    from django.contrib.auth import get_user_model
    from apps.core.models.user import user_internal_username
    if tenant is not None:
        kwargs["tenant"] = tenant
        username = user_internal_username(tenant, username)
    return get_user_model().objects.create_user(username=username, **kwargs)


def student_create(tenant, **kwargs):
    from apps.domains.students.models import Student
    return Student.objects.create(tenant=tenant, **kwargs)


def tag_get(pk):
    from apps.domains.students.models import Tag
    return Tag.objects.get(id=pk)


def student_tag_get_or_create(student, tag):
    from apps.domains.students.models import StudentTag
    return StudentTag.objects.get_or_create(student=student, tag=tag)


def student_tag_filter(student, tag):
    from apps.domains.students.models import StudentTag
    return StudentTag.objects.filter(student=student, tag=tag)


def student_filter_deleted(tenant):
    from apps.domains.students.models import Student
    return Student.objects.filter(tenant=tenant, deleted_at__isnull=False)


def user_filter_phone_active(phone):
    from django.contrib.auth import get_user_model
    return get_user_model().objects.filter(phone=phone, is_active=True)


def student_filter_tenant_ps_number(tenant, ps_number):
    from apps.domains.students.models import Student
    return Student.objects.filter(tenant=tenant, ps_number=ps_number, deleted_at__isnull=True)


def enrollment_filter_student_delete(student_id):
    from apps.domains.enrollment.models import Enrollment
    return Enrollment.objects.filter(student_id=student_id).delete()


def student_filter_tenant_pk(tenant, pk):
    from apps.domains.students.models import Student
    return Student.objects.filter(tenant=tenant, pk=pk).first()


def student_get(tenant, pk):
    from apps.domains.students.models import Student
    return Student.objects.get(tenant=tenant, pk=pk)


def enrollment_filter_student_delete_obj(student):
    from apps.domains.enrollment.models import Enrollment
    return Enrollment.objects.filter(student=student).delete()


def student_filter_tenant_deleted():
    from apps.domains.students.models import Student
    return Student.objects.filter(deleted_at__isnull=False)


def student_filter_tenant_deleted_only(tenant):
    from apps.domains.students.models import Student
    return Student.objects.filter(tenant=tenant, deleted_at__isnull=False)


def user_filter_username(username):
    from django.contrib.auth import get_user_model
    return get_user_model().objects.filter(username=username)


def user_filter_phone(phone):
    from django.contrib.auth import get_user_model
    return get_user_model().objects.filter(phone=phone)


def student_filter_tenant_ps(tenant, ps_number):
    from apps.domains.students.models import Student
    return Student.objects.filter(tenant=tenant, ps_number=ps_number)


def student_filter_tenant_phone_deleted(tenant, phone):
    from apps.domains.students.models import Student
    return Student.objects.filter(tenant=tenant, phone=phone, deleted_at__isnull=False)


def student_tag_filter_delete(student, tag_id):
    from apps.domains.students.models import StudentTag
    return StudentTag.objects.filter(student=student, tag_id=tag_id).delete()


def student_filter_tenant_id_deleted_first(tenant, student_id):
    from apps.domains.students.models import Student
    return Student.objects.filter(
        tenant=tenant, id=student_id, deleted_at__isnull=False
    ).select_related("user").first()


def student_filter_tenant_ids_active(tenant, ids):
    from apps.domains.students.models import Student
    return Student.objects.filter(
        tenant=tenant, id__in=ids, deleted_at__isnull=True
    ).select_related("user")


def student_filter_tenant_ids_deleted(tenant, ids):
    from apps.domains.students.models import Student
    return Student.objects.filter(
        tenant=tenant, id__in=ids, deleted_at__isnull=False
    ).select_related("user")


def student_filter_tenant_deleted_dup_groups(tenant):
    from apps.domains.students.models import Student
    from django.db.models import Count
    return (
        Student.objects.filter(tenant=tenant, deleted_at__isnull=False)
        .values("tenant_id", "name", "parent_phone")
        .annotate(cnt=Count("id"))
        .filter(cnt__gt=1)
    )


def student_filter_dup_keep_first(tenant_id, name, parent_phone):
    from apps.domains.students.models import Student
    return (
        Student.objects.filter(
            tenant_id=tenant_id,
            name=name,
            parent_phone=parent_phone,
            deleted_at__isnull=False,
        )
        .order_by("deleted_at")
        .first()
    )


def student_filter_dup_to_remove(tenant_id, name, parent_phone, exclude_id):
    from apps.domains.students.models import Student
    return Student.objects.filter(
        tenant_id=tenant_id,
        name=name,
        parent_phone=parent_phone,
        deleted_at__isnull=False,
    ).exclude(id=exclude_id)


def student_get_tenant_user(tenant, user):
    from apps.domains.students.models import Student
    return Student.objects.get(tenant=tenant, user=user)


def user_filter_username_exists(username):
    from django.contrib.auth import get_user_model
    return get_user_model().objects.filter(username=username).exists()


def user_filter_phone_exists(phone):
    from django.contrib.auth import get_user_model
    return get_user_model().objects.filter(phone=phone).exists()


def student_filter_tenant_ps_exclude_id(tenant, ps_number, exclude_id):
    from apps.domains.students.models import Student
    return Student.objects.filter(
        tenant=tenant, ps_number=ps_number
    ).exclude(id=exclude_id)


def student_filter_tenant_name_parent_phone_active(tenant, name, parent_phone):
    from apps.domains.students.models import Student
    return (
        Student.objects.filter(
            tenant=tenant,
            deleted_at__isnull=True,
            parent_phone=parent_phone,
        )
        .filter(name__iexact=name if name else "")
        .first()
    )


def student_filter_tenant_name_parent_phone_deleted(tenant, name, parent_phone):
    from apps.domains.students.models import Student
    return (
        Student.objects.filter(
            tenant=tenant,
            deleted_at__isnull=False,
            parent_phone=parent_phone,
        )
        .filter(name__iexact=name if name else "")
        .first()
    )


def student_filter_deleted_dup_groups():
    """삭제된 학생 중 (tenant_id, name, parent_phone) 별 중복 그룹 (cnt > 1)."""
    from django.db.models import Count, Min
    from apps.domains.students.models import Student
    return (
        Student.objects.filter(deleted_at__isnull=False)
        .values("tenant_id", "name", "parent_phone")
        .annotate(cnt=Count("id"), min_deleted_at=Min("deleted_at"))
        .filter(cnt__gt=1)
    )


def student_filter_deleted_before_cutoff(cutoff):
    from apps.domains.students.models import Student
    return Student.objects.filter(deleted_at__lt=cutoff).select_related("user")


def enrollment_filter_student_ids_bulk(student_ids):
    from apps.domains.enrollment.models import Enrollment
    return Enrollment.objects.filter(student_id__in=student_ids).delete()
