"""Exam-domain DB read helpers for cross-domain callers."""

from __future__ import annotations


def regular_active_exam_for_tenant(exam_id: int, tenant):
    from apps.domains.exams.models import Exam

    return (
        Exam.objects.filter(
            id=int(exam_id),
            tenant=tenant,
            exam_type=Exam.ExamType.REGULAR,
            is_active=True,
        )
        .order_by("id")
        .first()
    )


def active_regular_exam_count(tenant) -> int:
    from apps.domains.exams.models import Exam

    return Exam.objects.filter(
        tenant=tenant,
        is_active=True,
        exam_type=Exam.ExamType.REGULAR,
    ).count()


def exam_enrollment_ids_for_tenant_exam(exam_id: int, tenant):
    from apps.domains.exams.models import ExamEnrollment

    return ExamEnrollment.objects.filter(
        exam_id=int(exam_id),
        enrollment__tenant=tenant,
    ).values_list("enrollment_id", flat=True)


def exam_question_number_map(question_ids, *, exam_id: int, tenant) -> dict[int, int]:
    from apps.domains.exams.models.question import ExamQuestion

    if not question_ids:
        return {}
    return dict(
        ExamQuestion.objects.filter(
            id__in=question_ids,
            sheet__exam_id=int(exam_id),
            sheet__exam__tenant=tenant,
        )
        .order_by("id")
        .values_list("id", "number")
    )


def answer_key_answers_for_exam(exam_id: int, *, tenant) -> dict:
    from apps.domains.exams.models import AnswerKey

    answer_key = (
        AnswerKey.objects.filter(
            exam_id=int(exam_id),
            exam__tenant=tenant,
        )
        .order_by("-updated_at", "-id")
        .first()
    )
    return answer_key.answers if answer_key and answer_key.answers else {}


def _session_location(session):
    return {
        "session_id": session.id,
        "session_title": session.title,
        "lecture_id": session.lecture_id,
        "lecture_title": session.lecture.title,
    }


def submission_exam_contexts(submissions, enrollment_map, *, tenant, supports_session_choices: bool) -> dict[int, dict]:
    """Resolve each staff inbox row from tenant-owned batch and roster evidence."""
    from collections import defaultdict

    from django.db.models import F, Prefetch

    from apps.domains.enrollment.models import SessionEnrollment
    from apps.domains.exams.models import Exam, ExamEnrollment
    from apps.domains.lectures.models import Session
    from apps.domains.submissions.models import Submission
    from apps.domains.submissions.models.omr_batch import OmrUploadBatchItem

    sessions = Session.objects.filter(lecture__tenant=tenant).select_related("lecture").order_by("id")
    exams = Exam.objects.filter(tenant=tenant, id__in={s.target_id for s in submissions})
    exams = exams.prefetch_related(Prefetch("sessions", queryset=sessions, to_attr="inbox_sessions"))
    exams_by_id = {exam.id: exam for exam in exams}
    if not supports_session_choices:
        # Earlier clients require a concrete navigation target for every linked exam.
        # Preserve their historical first-link ordering within the requesting tenant.
        legacy = {
            exam.id: {
                "target_title": exam.title,
                **(_session_location(exam.inbox_sessions[0]) if exam.inbox_sessions else {}),
            }
            for exam in exams_by_id.values()
        }
        return {s.id: legacy[s.target_id] for s in submissions if s.target_id in legacy}

    session_ids = {session.id for exam in exams_by_id.values() for session in exam.inbox_sessions}
    shared_exam_ids = {exam.id for exam in exams_by_id.values() if len({s.lecture_id for s in exam.inbox_sessions}) > 1}
    online = [s for s in submissions if s.source == Submission.Source.ONLINE and s.target_id in shared_exam_ids
              and s.enrollment_id in enrollment_map]
    assignments = defaultdict(list)
    enrollment_ids = set(enrollment_map)
    if online:
        # The online grading enrollment is the first eligible ExamEnrollment, not necessarily
        # the course the student opened. Batch-load the student's other assigned courses.
        for exam_id, student_id, eid, lecture_id, assigned_at in ExamEnrollment.objects.filter(
            exam_id__in={s.target_id for s in online}, enrollment__tenant=tenant,
            enrollment__student_id__in={enrollment_map[s.enrollment_id].student_id for s in online},
            enrollment__status="ACTIVE", enrollment__lecture__tenant=tenant, enrollment__lecture__is_active=True,
        ).values_list("exam_id", "enrollment__student_id", "enrollment_id", "enrollment__lecture_id", "created_at"):
            enrollment_ids.add(eid)
            assignments[exam_id, student_id].append((eid, lecture_id, assigned_at))

    roster = defaultdict(set)
    for enrollment_id, session_id in SessionEnrollment.objects.filter(
        tenant=tenant, enrollment_id__in=enrollment_ids, session_id__in=session_ids,
        enrollment__lecture_id=F("session__lecture_id"),
    ).values_list("enrollment_id", "session_id"):
        roster[enrollment_id].add(session_id)
    attended = defaultdict(set)
    absent = defaultdict(set)
    if assignments:
        from apps.domains.attendance.models import Attendance
        from apps.support.attendance.learning_todo_eligibility import attendance_status_is_learning_todo_eligible

        for eid, sid, status in Attendance.objects.filter(
            tenant=tenant, enrollment_id__in=enrollment_ids, session_id__in=session_ids,
            enrollment__lecture_id=F("session__lecture_id"),
        ).values_list("enrollment_id", "session_id", "status"):
            attended[eid].add(sid)
            if not attendance_status_is_learning_todo_eligible(status):
                absent[eid].add(sid)

    batches = {
        submission_id: (exam_id, session_id, lecture_id)
        for submission_id, exam_id, session_id, lecture_id in OmrUploadBatchItem.objects.filter(
            batch__tenant=tenant, submission_id__in=[s.id for s in submissions],
            batch__session_id__isnull=False,
        ).values_list("submission_id", "batch__exam_id", "batch__session_id", "batch__lecture_id")
    }
    contexts = {}
    for submission in submissions:
        exam = exams_by_id.get(submission.target_id)
        if exam is None:
            continue
        candidates = exam.inbox_sessions
        chosen = None
        recorded = False
        batch = batches.get(submission.id)
        enrollment = enrollment_map.get(submission.enrollment_id)
        entry_id = (submission.meta or {}).get("student_entry_session_id") if isinstance(submission.meta, dict) else None
        if batch:
            chosen = next((s for s in candidates if (exam.id, s.id, s.lecture_id) == batch), None)
            recorded = chosen is not None
        elif submission.source == Submission.Source.ONLINE and enrollment and type(entry_id) is int:
            chosen = next((s for s in candidates if s.id == entry_id), None)
            recorded = chosen is not None
        if not recorded and enrollment and len(candidates) > 1:
            lecture_sessions = [s for s in candidates if s.lecture_id == enrollment.lecture_id]
            enrolled = [s for s in lecture_sessions if s.id in roster[enrollment.id]]
            navigation_ids = {s.id for s in (enrolled or lecture_sessions)}
            if submission.source == Submission.Source.ONLINE:
                for eid, lecture_id, assigned_at in assignments[exam.id, enrollment.student_id]:
                    if eid == enrollment.id or assigned_at > submission.created_at:
                        continue
                    linked = {s.id for s in candidates if s.lecture_id == lecture_id}
                    eligible = linked & (roster[eid] | attended[eid])
                    navigation_ids.update((eligible or linked) - absent[eid])
            candidates = [s for s in candidates if s.id in navigation_ids] or candidates
            if len(candidates) == 1:
                chosen = candidates[0]
        elif not recorded and len(candidates) == 1:
            chosen = candidates[0]

        contexts[submission.id] = {
            "target_title": exam.title,
            "session_origin_recorded": recorded,
            "score_lecture_id": enrollment.lecture_id if enrollment else None,
            "session_choices": [_session_location(s) for s in candidates] if not chosen else [],
            **(_session_location(chosen) if chosen else {}),
        }
    return contexts
