"""Canonical tenant-scoped enrollment read helpers."""

from __future__ import annotations

from rest_framework.exceptions import NotFound, ValidationError

from apps.domains.enrollment.models import Enrollment, SessionEnrollment


def require_tenant(tenant):
    if tenant is None:
        raise ValidationError({"detail": "tenant가 필요합니다."})
    return tenant


def enrollments_for_tenant(tenant):
    tenant = require_tenant(tenant)
    return (
        Enrollment.objects
        .filter(tenant=tenant)
        .filter(student__deleted_at__isnull=True)
        .select_related("student", "lecture")
    )


def session_enrollments_for_tenant(tenant):
    tenant = require_tenant(tenant)
    return (
        SessionEnrollment.objects
        .filter(tenant=tenant)
        .filter(enrollment__student__deleted_at__isnull=True)
        .select_related("session", "enrollment", "enrollment__student")
    )


def active_session_enrollments_for_session(*, tenant, session_id: int):
    tenant = require_tenant(tenant)
    return (
        SessionEnrollment.objects
        .filter(
            tenant=tenant,
            session_id=session_id,
            session__lecture__tenant=tenant,
            enrollment__tenant=tenant,
            enrollment__status="ACTIVE",
            enrollment__student__deleted_at__isnull=True,
        )
        .select_related("enrollment", "enrollment__student", "enrollment__lecture")
        .order_by("id")
    )


def active_enrollment_ids_for_session(*, tenant, session_id: int) -> set[int]:
    return set(
        active_session_enrollments_for_session(
            tenant=tenant,
            session_id=session_id,
        ).values_list("enrollment_id", flat=True)
    )


def get_student_for_tenant_or_404(*, tenant, student_id: int):
    from apps.domains.students.models import Student

    tenant = require_tenant(tenant)
    try:
        return Student.objects.get(id=student_id, tenant=tenant, deleted_at__isnull=True)
    except Student.DoesNotExist as exc:
        raise NotFound("학생을 찾을 수 없습니다") from exc


def get_lecture_for_tenant_or_404(*, tenant, lecture_id: int):
    from apps.domains.lectures.models import Lecture

    tenant = require_tenant(tenant)
    try:
        return Lecture.objects.get(id=lecture_id, tenant=tenant)
    except Lecture.DoesNotExist as exc:
        raise NotFound("강의를 찾을 수 없습니다") from exc


def get_session_for_lecture_or_404(*, lecture, session_id: int):
    from apps.domains.lectures.models import Session

    try:
        return Session.objects.get(id=session_id, lecture=lecture)
    except Session.DoesNotExist as exc:
        raise NotFound("차시를 찾을 수 없습니다") from exc


def get_active_enrollment_for_student_lecture(*, tenant, student, lecture):
    tenant = require_tenant(tenant)
    return Enrollment.objects.filter(
        tenant=tenant,
        student=student,
        lecture=lecture,
        status="ACTIVE",
    ).first()


def build_student_enrollment_matrix(*, tenant, student_id: int, lecture_id: int) -> dict:
    from apps.domains.exams.models import Exam, ExamEnrollment
    from apps.domains.homework.models import HomeworkAssignment
    from apps.domains.homework_results.models import Homework
    from apps.domains.lectures.models import Session

    student = get_student_for_tenant_or_404(tenant=tenant, student_id=student_id)
    lecture = get_lecture_for_tenant_or_404(tenant=tenant, lecture_id=lecture_id)
    enrollment = get_active_enrollment_for_student_lecture(
        tenant=tenant,
        student=student,
        lecture=lecture,
    )
    if not enrollment:
        return {
            "enrollment_id": None,
            "lecture": {"id": lecture.id, "title": lecture.title},
            "sessions": [],
            "detail": "이 학생은 해당 강의에 등록되어 있지 않습니다.",
        }

    sessions = list(
        Session.objects.filter(lecture=lecture).order_by("order", "id")
        .values("id", "title", "order")
    )
    session_ids = [s["id"] for s in sessions]

    enrolled_session_ids = set(
        SessionEnrollment.objects.filter(
            tenant=tenant,
            enrollment=enrollment,
            session_id__in=session_ids,
        ).values_list("session_id", flat=True)
    )

    exams_by_session: dict[int, list[dict]] = {}
    exams = (
        Exam.objects
        .filter(tenant=tenant, sessions__id__in=session_ids, sessions__lecture=lecture)
        .distinct()
        .prefetch_related("sessions")
    )
    for exam in exams:
        for sid in exam.sessions.filter(id__in=session_ids).values_list("id", flat=True):
            exams_by_session.setdefault(sid, []).append({"id": exam.id, "title": exam.title})

    enrolled_exam_ids = set(
        ExamEnrollment.objects.filter(
            exam__tenant=tenant,
            exam__sessions__id__in=session_ids,
            enrollment=enrollment,
        ).values_list("exam_id", flat=True)
    )

    homeworks_by_session: dict[int, list] = {}
    for hw in Homework.objects.filter(
        tenant=tenant,
        session_id__in=session_ids,
    ).values("id", "title", "session_id"):
        homeworks_by_session.setdefault(hw["session_id"], []).append(hw)

    enrolled_hw_ids = set(
        HomeworkAssignment.objects.filter(
            tenant=tenant,
            enrollment=enrollment,
            session_id__in=session_ids,
        ).values_list("homework_id", flat=True)
    )

    result_sessions = []
    for s in sessions:
        sid = s["id"]
        result_sessions.append({
            "id": sid,
            "title": s["title"] or f"세션 {s['order']}",
            "order": s["order"],
            "session_enrolled": sid in enrolled_session_ids,
            "exams": [
                {"id": e["id"], "title": e["title"], "enrolled": e["id"] in enrolled_exam_ids}
                for e in exams_by_session.get(sid, [])
            ],
            "homeworks": [
                {"id": h["id"], "title": h["title"], "enrolled": h["id"] in enrolled_hw_ids}
                for h in homeworks_by_session.get(sid, [])
            ],
        })

    return {
        "enrollment_id": enrollment.id,
        "lecture": {"id": lecture.id, "title": lecture.title},
        "student": {"id": student.id, "name": getattr(student, "name", "") or ""},
        "sessions": result_sessions,
    }
