"""Cross-domain dependencies for student enrollment matrix views."""

from __future__ import annotations


def build_student_enrollment_matrix(**kwargs):
    from apps.domains.enrollment.selectors import build_student_enrollment_matrix as _build

    return _build(**kwargs)


def toggle_student_learning_access(**kwargs):
    from apps.domains.enrollment.services.lifecycle import toggle_student_learning_access as _toggle

    return _toggle(**kwargs)
