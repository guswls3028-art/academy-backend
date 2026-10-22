"""Cross-domain dependencies for explicit parent assessment feedback."""
from apps.domains.enrollment.models import Enrollment, SessionEnrollment
from apps.domains.exams.models import ExamEnrollment
from apps.domains.homework.models import HomeworkAssignment
from apps.domains.homework_results.models import Homework
from apps.domains.lectures.models import Session
from apps.domains.student_app.permissions import get_request_student, is_request_parent

__all__ = ["Enrollment", "SessionEnrollment", "ExamEnrollment", "HomeworkAssignment", "Homework", "Session", "get_request_student", "is_request_parent"]

from apps.domains.enrollment.selectors import learning_history_enrollments_for_student
from apps.support.results.progress_read_dependencies import session_score_enrollment_ids
from apps.support.progress.session_calculator_dependencies import get_exam_target_enrollment_pairs_for_session


def parent_comment_titles(*, tenant, student, comments):
    """Request-local bulk equivalent of assessment_scope for parent publication.

    The attendance-first roster applies per session, not per selected child.
    Explicit exam targets remain authoritative even when none is this child.
    Only IDs/titles are read; attendance notes and revision history are not loaded.
    """
    from django.db.models import F
    from apps.domains.attendance.models import Attendance
    from apps.domains.exams.models import Exam
    from apps.support.results.progress_read_dependencies import live_regular_exam_filter

    if not comments:
        return {}
    session_ids = {c.session_id for c in comments}
    enrollments = dict(learning_history_enrollments_for_student(
        tenant=tenant, student=student,
    ).filter(student__tenant=tenant).values_list("id", "lecture_id"))
    roster_filter = {
        "tenant": tenant, "session_id__in": session_ids,
        "session__lecture__tenant": tenant,
        "enrollment__lecture_id": F("session__lecture_id"),
        "enrollment__status": "ACTIVE",
        "enrollment__student__deleted_at__isnull": True,
    }
    attendance = set(Attendance.objects.filter(**roster_filter).values_list("session_id", "enrollment_id"))
    attendance_sessions = {sid for sid, _ in attendance}
    session_roster = set(SessionEnrollment.objects.filter(**roster_filter).values_list("session_id", "enrollment_id"))
    roster = attendance | {(sid, eid) for sid, eid in session_roster if sid not in attendance_sessions}

    exam_ids = {c.assessment_id for c in comments if c.assessment_kind == "exam"}
    exams = {(sid, eid): title for sid, eid, title in Exam.objects.filter(
        tenant=tenant, id__in=exam_ids, sessions__id__in=session_ids,
        **live_regular_exam_filter(),
    ).values_list("sessions__id", "id", "title")}
    exam_targets = set(ExamEnrollment.objects.filter(exam_id__in=exam_ids).values_list("exam_id", "enrollment_id"))
    explicit_exams = {eid for eid, _ in exam_targets}
    homework_ids = {c.assessment_id for c in comments if c.assessment_kind == "homework"}
    homeworks = {(sid, hid): title for sid, hid, title in Homework.objects.filter(
        tenant=tenant, id__in=homework_ids, session_id__in=session_ids, homework_type="regular",
    ).exclude(meta__removed_from_session_at__isnull=False).values_list("session_id", "id", "title")}
    homework_targets = set(HomeworkAssignment.objects.filter(
        tenant=tenant, session_id__in=session_ids, homework_id__in=homework_ids,
        enrollment_id__in=enrollments,
    ).values_list("session_id", "homework_id", "enrollment_id"))
    titles = {}
    for comment in comments:
        if (comment.session.lecture.tenant_id != tenant.pk
                or enrollments.get(comment.enrollment_id) != comment.session.lecture_id
                or (comment.session_id, comment.enrollment_id) not in roster):
            continue
        key = (comment.session_id, comment.assessment_id)
        if comment.assessment_kind == "exam":
            if key in exams and (comment.assessment_id not in explicit_exams
                                 or (comment.assessment_id, comment.enrollment_id) in exam_targets):
                titles[comment.pk] = exams[key]
        elif comment.assessment_kind == "homework":
            if key in homeworks and (*key, comment.enrollment_id) in homework_targets:
                titles[comment.pk] = homeworks[key]
    return titles
