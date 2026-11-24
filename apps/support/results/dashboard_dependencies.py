"""Cross-domain dashboard dependencies for result endpoints."""

from __future__ import annotations

from typing import Any


def pending_work_counts(*, tenant: Any) -> dict:
    from academy.adapters.db.django import repositories_submissions as submissions_repo
    from apps.domains.community.selectors.post_selector import get_pending_post_counts

    counts = get_pending_post_counts(tenant)
    counts["submission_pending"] = submissions_repo.exam_inbox_submissions(tenant).filter(
        status__in=submissions_repo.pending_statuses(),
    ).count()
    by_homework = pending_homework_file_counts(tenant=tenant)
    return {
        **counts,
        "homework_review_pending": sum(by_homework.values()),
        "homework_review_by_homework": by_homework,
    }


def pending_homework_file_counts(*, tenant: Any) -> dict[int, int]:
    """Count current student assignments with delivered files awaiting staff review."""
    from collections import Counter

    from django.db.models import Exists, F, OuterRef, Q, Subquery
    from apps.domains.homework.models import HomeworkAssignment
    from apps.domains.homework_results.models import Homework, HomeworkScore
    from apps.domains.progress.models import AssessmentCorrection, HomeworkFileAcceptance
    from apps.domains.submissions.models import Submission, SubmissionMedia
    from apps.support.submissions.dependencies import homework_submission_revisions

    uploaded = SubmissionMedia.objects.filter(
        tenant=tenant, submission_id=OuterRef("pk"),
        status=SubmissionMedia.Status.UPLOADED, removed_at__isnull=True,
    )
    legacy_file = Q(file_key__isnull=False, meta__homework_media_legacy_removed_at__isnull=True) & ~Q(file_key="")
    delivered = Submission.objects.filter(
        tenant=tenant, target_type=Submission.TargetType.HOMEWORK,
    ).exclude(status__in=[Submission.Status.FAILED, Submission.Status.SUPERSEDED]).filter(
        Exists(uploaded) | legacy_file,
    )
    assigned = HomeworkAssignment.objects.filter(
        tenant=tenant, homework__tenant=tenant,
        homework_id=OuterRef("target_id"), enrollment_id=OuterRef("enrollment_id"),
        homework__homework_type=Homework.HomeworkType.REGULAR,
        homework__session_id=F("session_id"), session__lecture__tenant=tenant,
        enrollment__tenant=tenant, enrollment__lecture_id=F("session__lecture_id"),
        enrollment__student__tenant=tenant, enrollment__student__deleted_at__isnull=True,
        enrollment__status="ACTIVE",
    ).exclude(homework__meta__removed_from_session_at__isnull=False)
    latest_score = HomeworkScore.objects.filter(
        homework_id=OuterRef("target_id"), enrollment_id=OuterRef("enrollment_id"),
        session_id=F("homework__session_id"),
    ).order_by("-attempt_index", "-updated_at", "-id")
    confirmed = AssessmentCorrection.objects.filter(
        tenant=tenant, source_type=AssessmentCorrection.SourceType.HOMEWORK,
        completed=True, source_id=OuterRef("target_id"), enrollment_id=OuterRef("enrollment_id"),
        session__homeworks__id=OuterRef("target_id"),
    )
    accepted = HomeworkFileAcceptance.objects.filter(
        tenant=tenant, homework_id=OuterRef("target_id"), enrollment_id=OuterRef("enrollment_id"),
        session_id=F("homework__session_id"), fingerprint__isnull=False,
    )
    candidates = delivered.filter(Exists(assigned), ~Exists(accepted)).alias(
        passed=Subquery(latest_score.values("passed")[:1]),
    ).filter(
        Q(passed=False) | (Q(passed__isnull=True) & ~Exists(confirmed)),
    ).annotate(
        reviewed_revision=Subquery(latest_score.values("reviewed_submission_revision")[:1]),
    ).order_by().values_list("enrollment_id", "target_id", "reviewed_revision").distinct()

    pending: Counter[int] = Counter()
    previously_graded = {}
    for enrollment_id, homework_id, reviewed_revision in candidates.iterator():
        if reviewed_revision:
            previously_graded[(enrollment_id, homework_id)] = reviewed_revision
        else:
            pending[homework_id] += 1
    if previously_graded:
        # A failing grade can be followed by a new upload. Compare the same
        # evidence revision that the grading write recorded, in one batch.
        current = homework_submission_revisions(
            tenant=tenant,
            enrollment_ids={key[0] for key in previously_graded},
            homework_ids={key[1] for key in previously_graded},
        )
        for key, reviewed_revision in previously_graded.items():
            if key in current and current[key] != reviewed_revision:
                pending[key[1]] += 1
    return dict(pending)


def failed_video_count_since(*, tenant: Any, cutoff: Any) -> int:
    from apps.domains.video.models import Video

    return Video.objects.filter(
        tenant=tenant,
        status=Video.Status.FAILED,
        deleted_at__isnull=True,
        updated_at__gte=cutoff,
    ).count()

