"""Cross-domain reads for mixed OMR subjective completion."""

from __future__ import annotations

from typing import Iterable


def exams_by_id(exam_ids: Iterable[int]):
    ids = {int(exam_id) for exam_id in exam_ids}
    if not ids:
        return {}

    from apps.domains.exams.models import Exam

    return Exam.objects.filter(id__in=ids).in_bulk()


def math_exam_ids(exams):
    from apps.domains.exams.models import Exam
    from apps.support.exams.numeric_short_answer import is_math_subject

    found = {exam_id for exam_id, exam in exams.items() if is_math_subject(exam.subject)}
    remaining = set(exams) - found
    if remaining:
        found.update(
            exam_id for exam_id, subject in Exam.objects.filter(id__in=remaining)
            .values_list("id", "sessions__lecture__subject") if is_math_subject(subject)
        )
    return found


def enrollments_by_id(enrollment_ids: Iterable[int]):
    ids = {int(enrollment_id) for enrollment_id in enrollment_ids}
    if not ids:
        return {}

    from apps.domains.enrollment.models import Enrollment

    return Enrollment.objects.filter(id__in=ids).in_bulk()


def submissions_by_id(submission_ids: Iterable[int]):
    ids = {int(submission_id) for submission_id in submission_ids}
    if not ids:
        return {}

    from apps.domains.submissions.models import Submission

    return Submission.objects.filter(id__in=ids).in_bulk()
