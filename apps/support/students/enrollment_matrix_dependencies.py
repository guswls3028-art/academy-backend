"""Cross-domain dependencies for student enrollment matrix views."""

from __future__ import annotations


def build_student_enrollment_matrix(**kwargs):
    from apps.domains.enrollment.selectors import build_student_enrollment_matrix as _build

    return _build(**kwargs)


def toggle_student_learning_access(**kwargs):
    from apps.domains.enrollment.services.lifecycle import toggle_student_learning_access as _toggle

    return _toggle(**kwargs)

def create_class_group_lecture(*, tenant, title, start_date, end_date, student_ids):
    """Create the native lecture and use enrollment's normal account/visibility lifecycle."""
    from apps.domains.lectures.serializers import LectureSerializer
    from apps.domains.enrollment.services.lifecycle import bulk_create_enrollments

    serializer = LectureSerializer(data={
        "title": title, "name": title, "subject": "수학", "start_date": start_date, "end_date": end_date,
    })
    serializer.is_valid(raise_exception=True)
    lecture = serializer.save(tenant=tenant)
    if student_ids:
        bulk_create_enrollments(tenant=tenant, lecture_id=lecture.pk, student_ids=student_ids)
    return lecture
