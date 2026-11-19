from django.conf import settings


def is_dawon_reference_tenant(tenant):
    return bool(getattr(settings, "DAWON_REVIEW_CLASS_GROUPS", False) and tenant and
                (tenant.pk, tenant.code) == (719, "dawon"))


def reference_percent(tenant, lecture_id):
    if not lecture_id or not is_dawon_reference_tenant(tenant):
        return None
    from apps.domains.students.models import ClassGroupLecture

    return ClassGroupLecture.objects.filter(group__tenant=tenant, lecture__tenant=tenant, lecture_id=lecture_id
                                            ).values_list("top_reference_percent", flat=True).first()
