"""Cross-domain dependencies for the session scores BFF view."""

from __future__ import annotations

from apps.domains.attendance.models import Attendance
from apps.domains.clinic.models import SessionParticipant
from apps.domains.enrollment.models import Enrollment, SessionEnrollment
from apps.domains.exams.models import Exam, ExamEnrollment, ExamQuestion
from apps.domains.exams.models.sheet import Sheet
from apps.domains.exams.services.template_resolver import resolve_template_exam
from apps.domains.homework.models import HomeworkAssignment
from apps.domains.homework_results.models import Homework, HomeworkScore
from apps.domains.lectures.models import Session
from apps.domains.progress.models import AssessmentCorrection, ClinicLink, SessionProgress
from apps.domains.submissions.models import Submission
from apps.support.submissions.dependencies import homework_submission_revisions


def superseded_homework_missing_keys(*, tenant, scores, accepted_keys):
    """Resolve historical missing marks against the same evidence as student grades."""
    cleared = set()
    reviewed = {}
    for score in scores:
        if not isinstance(score.meta, dict) or score.meta.get("status") != HomeworkScore.MetaStatus.NOT_SUBMITTED:
            continue
        key = (score.enrollment_id, score.homework_id)
        if (score.homework_id, score.session_id, score.enrollment_id) in accepted_keys:
            cleared.add(key)
        else:
            reviewed[key] = score.reviewed_submission_revision

    current = homework_submission_revisions(
        tenant=tenant,
        enrollment_ids={enrollment_id for enrollment_id, _ in reviewed},
        homework_ids={homework_id for _, homework_id in reviewed},
    )
    cleared.update(key for key, revision in current.items() if key in reviewed and revision != reviewed[key])
    return cleared
