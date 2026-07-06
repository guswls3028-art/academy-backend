"""Cross-domain dependencies for teacher app BFF views."""

from __future__ import annotations

from typing import Any

from django.db.models import Count


def notification_summary_counts(*, tenant: Any) -> dict[str, int]:
    from apps.domains.clinic.models import SessionParticipant
    from apps.domains.community.models import PostEntity
    from apps.domains.students.models import StudentRegistrationRequest

    qna_pending = (
        PostEntity.objects.filter(
            tenant=tenant,
            post_type="qna",
            status="published",
        )
        .exclude(author_role="staff")
        .annotate(reply_count=Count("replies"))
        .filter(reply_count=0)
        .count()
    )
    counsel_pending = (
        PostEntity.objects.filter(
            tenant=tenant,
            post_type="counsel",
            status="published",
        )
        .exclude(author_role="staff")
        .exclude(category_label="teacher_internal_memo")
        .annotate(reply_count=Count("replies"))
        .filter(reply_count=0)
        .count()
    )
    registration_pending = StudentRegistrationRequest.objects.filter(
        tenant=tenant,
        status="pending",
    ).count()
    clinic_pending = SessionParticipant.objects.filter(
        tenant=tenant,
        status="pending",
    ).count()

    return {
        "qna_pending": qna_pending,
        "counsel_pending": counsel_pending,
        "registration_pending": registration_pending,
        "clinic_pending": clinic_pending,
    }
