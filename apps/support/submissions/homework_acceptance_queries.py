"""Read teacher homework acceptance from API and worker processes."""

from django.db.models import F

from apps.domains.progress.models import HomeworkFileAcceptance


def homework_acceptance_rows(*, tenant, enrollment_ids, homework_ids):
    return HomeworkFileAcceptance.objects.filter(
        tenant=tenant,
        enrollment_id__in=enrollment_ids,
        homework_id__in=homework_ids,
        session_id=F("homework__session_id"),
    )


def homework_accepted_keys(*, tenant, enrollment_ids, homework_ids):
    return set(homework_acceptance_rows(
        tenant=tenant, enrollment_ids=enrollment_ids, homework_ids=homework_ids,
    ).filter(fingerprint__isnull=False).values_list("homework_id", "session_id", "enrollment_id"))
