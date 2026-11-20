from django.shortcuts import get_object_or_404
from rest_framework.exceptions import NotFound, PermissionDenied

from apps.domains.results.utils.session_exam import get_exams_for_session
from apps.support.results.parent_comment_dependencies import (
    Enrollment, Session, Homework, HomeworkAssignment, learning_history_enrollments_for_student,
    session_score_enrollment_ids, get_exam_target_enrollment_pairs_for_session,
)


def assessment_scope(tenant, session_id, kind, assessment_id, *, student=None):
    if tenant is None:
        raise PermissionDenied("Tenant required.")
    session = get_object_or_404(Session, id=session_id, lecture__tenant=tenant)
    if kind == "exam":
        get_object_or_404(get_exams_for_session(session), id=assessment_id, tenant=tenant)
    elif kind == "homework":
        get_object_or_404(Homework.objects.exclude(meta__removed_from_session_at__isnull=False),
                          id=assessment_id, tenant=tenant, session=session, homework_type="regular")
    else:
        raise NotFound()
    enrollments = Enrollment.objects.filter(
        tenant=tenant, lecture_id=session.lecture_id, status="ACTIVE",
        student__tenant=tenant, student__deleted_at__isnull=True,
        id__in=session_score_enrollment_ids(tenant=tenant, session=session),
    )
    if student is not None:
        enrollments = enrollments.filter(id__in=learning_history_enrollments_for_student(tenant=tenant, student=student).values("id"))
    if kind == "exam":
        pairs = get_exam_target_enrollment_pairs_for_session(session=session, enrollment_ids=list(enrollments.values_list("id", flat=True)))
        enrollments = enrollments.filter(id__in=[eid for eid, exam_id in pairs if exam_id == assessment_id])
    else:
        enrollments = enrollments.filter(id__in=HomeworkAssignment.objects.filter(
            tenant=tenant, session=session, homework_id=assessment_id).values("enrollment_id"))
    return session, enrollments
