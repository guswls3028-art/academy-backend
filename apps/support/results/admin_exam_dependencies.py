"""Shared cross-domain dependencies for admin exam result endpoints."""

from __future__ import annotations

from typing import Any

from django.shortcuts import get_object_or_404


def get_regular_active_exam_for_tenant(*, exam_id: int, tenant: Any) -> Any:
    from apps.domains.exams.models import Exam

    return get_object_or_404(
        Exam,
        id=exam_id,
        tenant=tenant,
        exam_type=Exam.ExamType.REGULAR,
        is_active=True,
        sessions__lecture__tenant=tenant,
    )


def get_enrollment_for_tenant(*, enrollment_id: int, tenant: Any) -> Any | None:
    from apps.domains.enrollment.models import Enrollment

    return Enrollment.objects.filter(id=enrollment_id, tenant=tenant).first()
