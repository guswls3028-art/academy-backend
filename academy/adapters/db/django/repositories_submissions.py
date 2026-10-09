"""Submission-domain DB read helpers for cross-domain callers."""

from __future__ import annotations


def target_type_exam() -> str:
    from apps.domains.submissions.models import Submission

    return Submission.TargetType.EXAM


def target_type_homework() -> str:
    from apps.domains.submissions.models import Submission

    return Submission.TargetType.HOMEWORK


def status_done() -> str:
    from apps.domains.submissions.models import Submission

    return Submission.Status.DONE


def status_failed() -> str:
    from apps.domains.submissions.models import Submission

    return Submission.Status.FAILED


def pending_statuses() -> tuple[str, ...]:
    from apps.domains.submissions.models import Submission

    return (
        Submission.Status.SUBMITTED,
        Submission.Status.DISPATCHED,
        Submission.Status.EXTRACTING,
        Submission.Status.NEEDS_IDENTIFICATION,
        Submission.Status.ANSWERS_READY,
        Submission.Status.GRADING,
    )


def submission_filter_tenant(tenant):
    from apps.domains.submissions.models import Submission

    return Submission.objects.filter(tenant=tenant)


def annotate_submission_discarded(queryset):
    """Match the inbox's isinstance(meta['discarded'], dict) in the database."""
    from django.db.models import BooleanField, F, Func, Value
    from django.db.models.functions import Coalesce

    class DiscardedObject(Func):
        output_field = BooleanField()
        arity = 1
        template = "(jsonb_typeof(%(expressions)s -> 'discarded') = 'object')"

        def as_sqlite(self, compiler, connection, **extra_context):
            return self.as_sql(compiler, connection,
                               template="(json_type(%(expressions)s, '$.discarded') = 'object')",
                               **extra_context)

    return queryset.annotate(_inbox_discarded=Coalesce(DiscardedObject(F("meta")), Value(False)))


def get_submission_tenant_id(submission_id: int) -> int | None:
    from apps.domains.submissions.models import Submission

    return Submission.objects.filter(pk=submission_id).values_list("tenant_id", flat=True).first()


def list_stuck_dispatched_submission_ids(cutoff, *, limit: int = 100) -> list[int]:
    from apps.domains.submissions.models import Submission

    return list(
        Submission.objects.filter(
            status=Submission.Status.DISPATCHED,
            updated_at__lt=cutoff,
        ).values_list("id", flat=True)[:limit]
    )
