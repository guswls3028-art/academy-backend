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


def _homework_review_candidates(*, tenant):
    from django.db.models import Exists, F, OuterRef, Q, Subquery
    from apps.domains.homework.models import HomeworkAssignment
    from apps.domains.homework_results.models import Homework, HomeworkScore
    from apps.domains.progress.models import AssessmentCorrection, HomeworkFileAcceptance
    from apps.domains.submissions.models import Submission, SubmissionMedia

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
    return delivered.filter(Exists(assigned), ~Exists(accepted)).alias(
        passed=Subquery(latest_score.values("passed")[:1]),
    ).filter(
        Q(passed=False) | (Q(passed__isnull=True) & ~Exists(confirmed)),
    ).annotate(
        reviewed_revision=Subquery(latest_score.values("reviewed_submission_revision")[:1]),
    ).order_by()


def _unreviewed_rows(tenant, candidates):
    from apps.support.submissions.dependencies import homework_submission_revisions

    rows = list(candidates)
    previously_graded = [row for row in rows if row["reviewed_revision"]]
    current = {}
    if previously_graded:
        # A failing grade can be followed by a new upload. Compare the same
        # evidence revision that the grading write recorded, in one batch.
        current = homework_submission_revisions(
            tenant=tenant,
            enrollment_ids={row["enrollment_id"] for row in previously_graded},
            homework_ids={row["target_id"] for row in previously_graded},
        )
    for row in rows:
        key = row["enrollment_id"], row["target_id"]
        if not row["reviewed_revision"] or (key in current and current[key] != row["reviewed_revision"]):
            yield row


def pending_homework_file_counts(*, tenant: Any) -> dict[int, int]:
    from collections import Counter

    candidates = _homework_review_candidates(tenant=tenant).values(
        "enrollment_id", "target_id", "reviewed_revision",
    ).distinct()
    return dict(Counter(row["target_id"] for row in _unreviewed_rows(tenant, candidates)))


def pending_homework_submission_page(*, tenant, page_number, page_size):
    from django.core.paginator import Paginator
    from django.db.models import Count, Max, Q
    from django.db.models.functions import Coalesce, Greatest
    from apps.domains.submissions.models import SubmissionMedia
    from apps.support.submissions.dependencies import staff_homework_file_targets

    current_media = Q(media_files__tenant=tenant, media_files__status=SubmissionMedia.Status.UPLOADED,
                      media_files__removed_at__isnull=True)
    legacy = Q(file_key__isnull=False, meta__homework_media_legacy_removed_at__isnull=True) & ~Q(file_key="")
    candidates = _homework_review_candidates(tenant=tenant).values(
        "enrollment_id", "target_id", "reviewed_revision", "enrollment__student__name",
    ).annotate(
        file_count=Count("media_files", filter=current_media) + Count("pk", filter=legacy, distinct=True),
        submitted_at=Greatest(Max("created_at"), Coalesce(Max("media_files__uploaded_at", filter=current_media), Max("created_at"))),
    ).order_by("-submitted_at", "target_id", "enrollment_id")
    page = Paginator(list(_unreviewed_rows(tenant, candidates)), page_size).get_page(page_number)
    targets = staff_homework_file_targets(tenant=tenant).select_related("session__lecture").in_bulk(
        {row["target_id"] for row in page.object_list},
    )
    results = []
    for row in page.object_list:
        homework = targets.get(row["target_id"])
        if homework is None:  # Concurrent removal after the pending query.
            continue
        results.append({
            "homework_id": homework.pk, "enrollment_id": row["enrollment_id"],
            "student_name": row["enrollment__student__name"], "homework_title": homework.title,
            "lecture_title": homework.session.lecture.title, "session_title": homework.session.display_label,
            "submitted_at": row["submitted_at"], "file_count": row["file_count"],
        })
    return {"count": page.paginator.count, "page": page.number, "pages": page.paginator.num_pages, "results": results}


def failed_video_count_since(*, tenant: Any, cutoff: Any) -> int:
    from apps.domains.video.models import Video

    return Video.objects.filter(
        tenant=tenant,
        status=Video.Status.FAILED,
        deleted_at__isnull=True,
        updated_at__gte=cutoff,
    ).count()

