"""Tenant-scoped source material for a teacher's lecture-range print run."""

from collections import defaultdict

from django.db.models import F, Min

from apps.domains.attendance.models import Attendance
from apps.domains.enrollment.models import Enrollment, SessionEnrollment
from apps.domains.exams.models import AnswerKey, Exam, ExamEnrollment, ExamQuestion
from apps.domains.homework.models import HomeworkAssignment
from apps.domains.homework_results.models import Homework
from apps.domains.lectures.models import Session


def range_source_records(*, tenant_id, lecture_id, first, last, enrollment_id=None, selection=None):
    roster = Enrollment.objects.filter(
        tenant_id=tenant_id, lecture_id=lecture_id, lecture__tenant_id=tenant_id,
        status="ACTIVE", student__tenant_id=tenant_id, student__deleted_at__isnull=True,
    )
    if enrollment_id is not None:
        roster = roster.filter(pk=enrollment_id)
    enrollments = list(roster.select_related("student", "lecture").order_by("student__name", "id"))
    enrolled = {row.pk for row in enrollments}
    sessions = Session.objects.filter(
        lecture_id=lecture_id, lecture__tenant_id=tenant_id,
        session_type=Session.SessionType.REGULAR, regular_order__range=(first, last),
    )
    exam_query = Exam.objects.filter(
        tenant_id=tenant_id, exam_type=Exam.ExamType.REGULAR, is_active=True,
        sessions__in=sessions, source_homework__isnull=True,
    )
    homework_query = Homework.objects.filter(
        tenant_id=tenant_id, session__in=sessions, homework_type=Homework.HomeworkType.REGULAR,
    )
    if selection is not None:
        exam_query = exam_query.filter(pk__in=[s["id"] for s in selection if s["type"] == "exam"])
        homework_query = homework_query.filter(pk__in=[s["id"] for s in selection if s["type"] == "homework"])
    exams = list(exam_query.annotate(print_order=Min("sessions__regular_order")).select_related("sheet"))
    exam_ids = [exam.pk for exam in exams]
    explicit = defaultdict(set)
    for exam_id, enrollment_id in ExamEnrollment.objects.filter(exam_id__in=exam_ids).values_list("exam_id", "enrollment_id"):
        explicit[exam_id].add(enrollment_id)
    implicit = defaultdict(set)
    legacy_ids = set(exam_ids) - explicit.keys()
    if legacy_ids:
        for roster in (SessionEnrollment, Attendance):
            pairs = roster.objects.filter(
                tenant_id=tenant_id, session__in=sessions, session__exams__in=legacy_ids,
                enrollment_id__in=enrolled,
            ).values_list("session__exams", "enrollment_id")
            for exam_id, enrollment_id in pairs:
                implicit[exam_id].add(enrollment_id)
    records = []
    for exam in exams:
        allowed = explicit.get(exam.pk, implicit[exam.pk]) & enrolled
        records.append(("exam", exam.pk, exam.title, exam.print_order, exam, allowed))

    homework_rows = list(homework_query.select_related("session", "source_exam__sheet"))
    assignments = defaultdict(set)
    for homework_id, enrollment_id in HomeworkAssignment.objects.filter(
        tenant_id=tenant_id, homework_id__in=[hw.pk for hw in homework_rows],
        session_id=F("homework__session_id"), enrollment_id__in=enrolled,
    ).values_list("homework_id", "enrollment_id"):
        assignments[homework_id].add(enrollment_id)
    for hw in homework_rows:
        source = hw.source_exam
        if source and source.tenant_id != tenant_id:
            source = None
        records.append(("homework", hw.pk, hw.title, hw.session.regular_order, source, assignments[hw.pk]))
    return enrollments, records


def range_question_records(*, tenant_id, exams):
    owner_ids = {exam.effective_structure_exam_id for exam in exams}
    questions = defaultdict(list)
    for question in ExamQuestion.objects.filter(
        sheet__exam_id__in=owner_ids, sheet__exam__tenant_id=tenant_id,
    ).select_related("sheet", "explanation").order_by("number"):
        questions[question.sheet.exam_id].append(question)
    answers = dict(AnswerKey.objects.filter(
        exam_id__in=owner_ids, exam__tenant_id=tenant_id,
    ).values_list("exam_id", "answers"))
    return questions, answers
