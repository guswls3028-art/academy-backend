"""Cross-domain integrity checks for results management commands."""

from __future__ import annotations

from django.db.models import Count, F


def exam_integrity_counts() -> dict[str, int]:
    from apps.domains.exams.models import Exam

    return {
        "bad_attempts": Exam.objects.filter(max_attempts=0).count(),
        "bad_pass": Exam.objects.filter(pass_score__gt=F("max_score")).count(),
        "bad_dates": Exam.objects.filter(
            open_at__isnull=False,
            close_at__isnull=False,
            open_at__gte=F("close_at"),
        ).count(),
    }


def homework_score_over_max_count() -> int:
    from apps.domains.homework_results.models import HomeworkScore

    return HomeworkScore.objects.filter(
        score__isnull=False,
        max_score__isnull=False,
        score__gt=F("max_score"),
        max_score__gt=0,
    ).count()


def legacy_clinic_link_unresolved_dupes() -> list[dict]:
    from apps.domains.progress.models import ClinicLink

    return list(
        ClinicLink.objects
        .filter(
            source_type__isnull=True,
            source_id__isnull=True,
            resolved_at__isnull=True,
        )
        .values("enrollment_id", "session_id")
        .annotate(cnt=Count("id"))
        .filter(cnt__gt=1)
    )
