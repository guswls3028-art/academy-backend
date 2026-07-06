"""Cross-domain dependencies for attendance serializers."""

from __future__ import annotations


def session_queryset_for_attendance_serializer(tenant=None):
    from apps.domains.lectures.models import Session

    queryset = Session.objects.select_related("lecture")
    if tenant:
        queryset = queryset.filter(lecture__tenant=tenant)
    return queryset


def enrollment_queryset_for_attendance_serializer(tenant=None):
    if tenant:
        from apps.domains.enrollment.selectors import enrollments_for_tenant

        return enrollments_for_tenant(tenant)

    from apps.domains.enrollment.models import Enrollment

    return Enrollment.objects.select_related("lecture", "student")


def clinic_highlight_map_for_attendance(*, tenant, enrollment_ids: set[int]) -> dict[int, bool]:
    if not enrollment_ids:
        return {}

    from apps.domains.results.utils.clinic_highlight import compute_clinic_highlight_map

    return compute_clinic_highlight_map(tenant=tenant, enrollment_ids=enrollment_ids)
